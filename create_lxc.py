#!/usr/bin/env python3
"""
Proxmox LXC Manager
===================
Interactive script to create and delete LXC containers on a Proxmox node
(pmx-4) from available container templates.

Authenticates via API token loaded from a .env file.

Usage:
    1. Copy .env.example to .env and fill in your Proxmox credentials.
    2. pip install -r requirements.txt
    3. python create_lxc.py
"""

import os
import sys
import getpass
import time
import textwrap
import mimetypes

import requests
import urllib3
from dotenv import load_dotenv
from proxmoxer import ProxmoxAPI

# Suppress InsecureRequestWarning for self-signed Proxmox certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Supported template file extensions
TEMPLATE_EXTENSIONS = (".tar.gz", ".tar.xz", ".tar.zst")


# ──────────────────────────────────────────────────────────────
# Configuration helpers
# ──────────────────────────────────────────────────────────────

def load_config():
    """Load Proxmox connection settings from .env file."""
    load_dotenv()

    host = os.getenv("PROXMOX_HOST")
    token_id = os.getenv("PROXMOX_TOKEN_ID")
    token_secret = os.getenv("PROXMOX_TOKEN_SECRET")
    node = os.getenv("PROXMOX_NODE", "pmx-4")

    missing = []
    if not host:
        missing.append("PROXMOX_HOST")
    if not token_id:
        missing.append("PROXMOX_TOKEN_ID")
    if not token_secret:
        missing.append("PROXMOX_TOKEN_SECRET")

    if missing:
        print(f"\n✗ Missing required .env variables: {', '.join(missing)}")
        print("  Copy .env.example → .env and fill in your values.\n")
        sys.exit(1)

    # Strip protocol for proxmoxer (it adds https:// itself)
    host = host.replace("https://", "").replace("http://", "")
    # Strip trailing port if present — proxmoxer accepts host:port
    return host, token_id, token_secret, node


def connect(host, token_id, token_secret):
    """Return an authenticated ProxmoxAPI handle."""
    # token_id format: user@realm!tokenname
    user, token_name = token_id.rsplit("!", 1)
    try:
        proxmox = ProxmoxAPI(
            host,
            user=user,
            token_name=token_name,
            token_value=token_secret,
            verify_ssl=False,
        )
        # Quick connectivity check
        proxmox.version.get()
        return proxmox
    except Exception as exc:
        print(f"\n✗ Failed to connect to Proxmox at {host}: {exc}\n")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────
# Template discovery
# ──────────────────────────────────────────────────────────────

def list_templates(proxmox, node):
    """
    Return a list of available LXC templates across all storage pools
    on the given node.

    Each item is a dict with keys: volid, storage, filename, size.
    """
    templates = []
    storages = proxmox.nodes(node).storage.get()

    for store in storages:
        storage_name = store["storage"]
        try:
            content = proxmox.nodes(node).storage(storage_name).content.get()
            for item in content:
                if item.get("content") == "vztmpl":
                    templates.append({
                        "volid": item["volid"],
                        "storage": storage_name,
                        "filename": item["volid"].split("/")[-1],
                        "size": item.get("size", 0),
                    })
        except Exception:
            # Some storages may not support content listing
            continue

    return templates


def list_template_storages(proxmox, node):
    """
    Return a list of storage pools that accept 'vztmpl' content.

    These are the storages where template files can be uploaded.
    """
    eligible = []
    storages = proxmox.nodes(node).storage.get()

    for store in storages:
        content_types = store.get("content", "")
        if "vztmpl" in content_types:
            eligible.append(store)

    return eligible


def pick_template_storage(proxmox, node):
    """
    Let the user pick a storage pool to upload the template to.

    Only storages that accept vztmpl content are listed.
    """
    eligible = list_template_storages(proxmox, node)

    if not eligible:
        print("\n  ✗ No storage pools accept template uploads (vztmpl).")
        print("    Configure a storage pool with 'vztmpl' content type.\n")
        sys.exit(1)

    if len(eligible) == 1:
        selected = eligible[0]["storage"]
        print(f"\n  ✓ Upload target: {selected} (only eligible storage)\n")
        return selected

    print("\n┌─────────────────────────────────────────────┐")
    print("│       Storage Pools for Template Upload     │")
    print("└─────────────────────────────────────────────┘")

    for idx, store in enumerate(eligible, start=1):
        total_gb = store.get("total", 0) / (1024 ** 3)
        avail_gb = store.get("avail", 0) / (1024 ** 3)
        print(f"  [{idx:>2}]  {store['storage']}")
        print(f"        type: {store.get('type', '?')}  |  "
              f"total: {total_gb:.1f} GB  |  free: {avail_gb:.1f} GB")

    while True:
        choice = input("\n  Select a storage pool for upload: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(eligible):
            selected = eligible[int(choice) - 1]["storage"]
            print(f"\n  ✓ Upload target: {selected}\n")
            return selected
        print("  ✗ Invalid selection, try again.")


def validate_template_path(filepath):
    """Validate that the file exists and has a supported template extension."""
    if not os.path.isfile(filepath):
        return False, f"File not found: {filepath}"

    if not any(filepath.endswith(ext) for ext in TEMPLATE_EXTENSIONS):
        return False, (
            f"Unsupported file type. "
            f"Expected one of: {', '.join(TEMPLATE_EXTENSIONS)}"
        )

    return True, None


def upload_template(proxmox, node, host, token_id, token_secret, filepath,
                    storage):
    """
    Upload a local template file to a Proxmox storage pool.

    Uses the Proxmox REST API multipart upload endpoint:
        POST /nodes/{node}/storage/{storage}/upload

    Args:
        proxmox: ProxmoxAPI handle (used only for task polling).
        node: Target Proxmox node name.
        host: Proxmox host (e.g. '192.168.1.50:8006').
        token_id: Full API token ID (e.g. 'root@pam!mytoken').
        token_secret: API token secret.
        filepath: Absolute path to the local template file.
        storage: Target storage pool name.

    Returns a template dict compatible with pick_template output.
    """
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    size_mb = file_size / (1024 * 1024)

    print(f"  ⟳ Uploading {filename} ({size_mb:.1f} MB) to '{storage}' …")

    upload_url = (
        f"https://{host}/api2/json/nodes/{node}"
        f"/storage/{storage}/upload"
    )
    headers = {
        "Authorization": f"PVEAPIToken={token_id}={token_secret}",
    }

    content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"

    try:
        with open(filepath, "rb") as fh:
            files = {
                "filename": (filename, fh, content_type),
            }
            data = {
                "content": "vztmpl",
            }
            response = requests.post(
                upload_url,
                headers=headers,
                files=files,
                data=data,
                verify=False,
            )
            response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"\n  ✗ Upload failed: {exc}\n")
        sys.exit(1)

    result = response.json()
    task_id = result.get("data")
    if task_id:
        print(f"  ⟳ Upload task started: {task_id}")
        try:
            wait_for_task(proxmox, node, task_id)
        except Exception as exc:
            print(f"\n  ✗ Upload task failed: {exc}\n")
            sys.exit(1)

    print(f"  ✓ Template uploaded successfully!\n")

    volid = f"{storage}:vztmpl/{filename}"
    return {
        "volid": volid,
        "storage": storage,
        "filename": filename,
        "size": file_size,
    }


def pick_template(proxmox, node, host, token_id, token_secret, templates):
    """
    Display a numbered list of templates and let the user choose one,
    or upload a custom template from a local file.
    """
    print("\n┌─────────────────────────────────────────────┐")
    print("│         Available LXC Templates             │")
    print("└─────────────────────────────────────────────┘")

    if templates:
        for idx, tpl in enumerate(templates, start=1):
            size_mb = tpl["size"] / (1024 * 1024) if tpl["size"] else 0
            print(f"  [{idx:>2}]  {tpl['filename']}")
            print(f"        storage: {tpl['storage']}  |  size: {size_mb:.1f} MB")
    else:
        print("\n  (no existing templates found on this node)")

    upload_idx = len(templates) + 1
    print(f"\n  [{upload_idx:>2}]  ⬆  Upload a custom template from local file")

    while True:
        choice = input("\n  Select a template number: ").strip()
        if not choice.isdigit():
            print("  ✗ Invalid selection, try again.")
            continue

        idx = int(choice)
        if 1 <= idx <= len(templates):
            selected = templates[idx - 1]
            print(f"\n  ✓ Selected: {selected['filename']}\n")
            return selected
        elif idx == upload_idx:
            return _handle_template_upload(
                proxmox, node, host, token_id, token_secret
            )
        else:
            print("  ✗ Invalid selection, try again.")


def _handle_template_upload(proxmox, node, host, token_id, token_secret):
    """Prompt for a local template file path and upload it."""
    print("\n  ── Upload Custom Template ──")
    print(f"  Supported formats: {', '.join(TEMPLATE_EXTENSIONS)}\n")

    while True:
        filepath = input("  Path to template file: ").strip()
        # Handle quoted paths and ~ expansion
        filepath = filepath.strip('"').strip("'")
        filepath = os.path.expanduser(filepath)
        filepath = os.path.abspath(filepath)

        valid, err = validate_template_path(filepath)
        if valid:
            break
        print(f"  ✗ {err}")

    storage = pick_template_storage(proxmox, node)
    template = upload_template(
        proxmox, node, host, token_id, token_secret, filepath, storage
    )
    return template


# ──────────────────────────────────────────────────────────────
# Storage discovery
# ──────────────────────────────────────────────────────────────

def pick_storage(proxmox, node):
    """List storage pools that accept rootdir/images and let the user pick one."""
    storages = proxmox.nodes(node).storage.get()

    # Filter storages that can hold rootdir or container images
    eligible = []
    for store in storages:
        content_types = store.get("content", "")
        if "rootdir" in content_types or "images" in content_types:
            eligible.append(store)

    if not eligible:
        # Fallback: show all storages and let the user decide
        eligible = storages

    print("┌─────────────────────────────────────────────┐")
    print("│           Available Storage Pools            │")
    print("└─────────────────────────────────────────────┘")

    for idx, store in enumerate(eligible, start=1):
        total_gb = store.get("total", 0) / (1024 ** 3)
        avail_gb = store.get("avail", 0) / (1024 ** 3)
        print(f"  [{idx:>2}]  {store['storage']}")
        print(f"        type: {store.get('type', '?')}  |  "
              f"total: {total_gb:.1f} GB  |  free: {avail_gb:.1f} GB")

    while True:
        choice = input("\n  Select a storage pool number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(eligible):
            selected = eligible[int(choice) - 1]["storage"]
            print(f"\n  ✓ Selected storage: {selected}\n")
            return selected
        print("  ✗ Invalid selection, try again.")


# ──────────────────────────────────────────────────────────────
# Next available VMID
# ──────────────────────────────────────────────────────────────

def next_vmid(proxmox):
    """Ask the cluster for the next available VMID."""
    try:
        return proxmox.cluster.nextid.get()
    except Exception:
        return 100  # fallback


# ──────────────────────────────────────────────────────────────
# Interactive container configuration
# ──────────────────────────────────────────────────────────────

def prompt_value(label, default=None, required=False, cast=None):
    """Prompt the user for a value with an optional default."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if not value and default is not None:
            value = str(default)
        if required and not value:
            print("  ✗ This field is required.")
            continue
        if cast:
            try:
                return cast(value)
            except (ValueError, TypeError):
                print(f"  ✗ Invalid value, expected {cast.__name__}.")
                continue
        return value


def configure_container(proxmox, node):
    """Interactively collect all settings for the new container."""
    suggested_id = next_vmid(proxmox)

    print("┌─────────────────────────────────────────────┐")
    print("│         New Container Configuration         │")
    print("└─────────────────────────────────────────────┘\n")

    vmid = prompt_value("Container ID (VMID)", default=suggested_id, cast=int)
    hostname = prompt_value("Hostname", required=True)
    password = getpass.getpass("  Root password: ")
    while not password:
        print("  ✗ Password cannot be empty.")
        password = getpass.getpass("  Root password: ")

    cores = prompt_value("CPU cores", default=1, cast=int)
    memory = prompt_value("Memory (MB)", default=512, cast=int)
    swap = prompt_value("Swap (MB)", default=512, cast=int)
    disk_size = prompt_value("Root disk size (GB)", default=8, cast=int)

    # Networking
    print("\n  ── Network Configuration ──")
    print("  Enter 'dhcp' for automatic IP assignment, or a static")
    print("  address in CIDR notation (e.g. 192.168.1.100/24).\n")

    ip_input = prompt_value("IP address (or 'dhcp')", default="dhcp")
    bridge = prompt_value("Bridge interface", default="vmbr0")
    gateway = None

    if ip_input.lower() == "dhcp":
        net_ip = "dhcp"
    else:
        net_ip = ip_input
        gateway = prompt_value("Gateway", default="")

    # Assemble network string
    net0 = f"name=eth0,bridge={bridge}"
    if net_ip == "dhcp":
        ip_config = "ip=dhcp"
    else:
        ip_config = f"ip={net_ip}"
        if gateway:
            ip_config += f",gw={gateway}"

    # Start on boot?
    onboot_input = prompt_value("Start on boot? (y/n)", default="y")
    onboot = 1 if onboot_input.lower() in ("y", "yes") else 0

    # Start after creation?
    start_input = prompt_value("Start container after creation? (y/n)", default="y")
    start_after = start_input.lower() in ("y", "yes")

    # Unprivileged?
    unpriv_input = prompt_value("Unprivileged container? (y/n)", default="y")
    unprivileged = 1 if unpriv_input.lower() in ("y", "yes") else 0

    config = {
        "vmid": vmid,
        "hostname": hostname,
        "password": password,
        "cores": cores,
        "memory": memory,
        "swap": swap,
        "disk_size": disk_size,
        "net0": net0,
        "ip_config": ip_config,
        "onboot": onboot,
        "start_after": start_after,
        "unprivileged": unprivileged,
    }
    return config


# ──────────────────────────────────────────────────────────────
# Container creation
# ──────────────────────────────────────────────────────────────

def confirm_and_create(proxmox, node, template, storage, config):
    """Show a summary and create the container upon confirmation."""

    summary = textwrap.dedent(f"""
    ┌─────────────────────────────────────────────┐
    │             Container Summary                │
    └─────────────────────────────────────────────┘

      VMID:           {config['vmid']}
      Hostname:       {config['hostname']}
      Template:       {template['filename']}
      Storage:        {storage}
      CPU cores:      {config['cores']}
      Memory:         {config['memory']} MB
      Swap:           {config['swap']} MB
      Root disk:      {config['disk_size']} GB
      Network:        {config['net0']}
      IP config:      {config['ip_config']}
      On boot:        {'Yes' if config['onboot'] else 'No'}
      Unprivileged:   {'Yes' if config['unprivileged'] else 'No'}
      Start after:    {'Yes' if config['start_after'] else 'No'}
      Node:           {node}
    """)
    print(summary)

    confirm = input("  Proceed with creation? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("\n  ✗ Aborted.\n")
        sys.exit(0)

    # Build the API payload
    payload = {
        "vmid": config["vmid"],
        "ostemplate": template["volid"],
        "hostname": config["hostname"],
        "password": config["password"],
        "storage": storage,
        "rootfs": f"{storage}:{config['disk_size']}",
        "cores": config["cores"],
        "memory": config["memory"],
        "swap": config["swap"],
        "net0": config["net0"],
        "nameserver": "",
        "onboot": config["onboot"],
        "unprivileged": config["unprivileged"],
    }

    # Attach IP config (ipconfig is a separate param in newer PVE)
    # For net0, Proxmox expects: name=eth0,bridge=vmbr0,ip=dhcp
    # We append the ip portion to net0
    payload["net0"] = f"{config['net0']},{config['ip_config']}"

    print("\n  ⟳ Creating container …")

    try:
        task_id = proxmox.nodes(node).lxc.create(**payload)
        print(f"  ✓ Task started: {task_id}")
    except Exception as exc:
        print(f"\n  ✗ Failed to create container: {exc}\n")
        sys.exit(1)

    # Wait for the task to finish
    print("  ⟳ Waiting for task to complete …")
    try:
        wait_for_task(proxmox, node, task_id)
    except Exception as exc:
        print(f"\n  ⚠ Could not verify task completion: {exc}")
        print("    Check the Proxmox UI for task status.\n")
        return

    print(f"  ✓ Container {config['vmid']} created successfully!\n")

    # Optionally start the container
    if config["start_after"]:
        print("  ⟳ Starting container …")
        try:
            proxmox.nodes(node).lxc(config["vmid"]).status.start.post()
            print(f"  ✓ Container {config['vmid']} is now running.\n")
        except Exception as exc:
            print(f"  ⚠ Failed to start container: {exc}\n")


def wait_for_task(proxmox, node, task_id, timeout=120, interval=2):
    """Poll a Proxmox task until it completes or times out."""
    elapsed = 0
    while elapsed < timeout:
        status = proxmox.nodes(node).tasks(task_id).status.get()
        if status.get("status") == "stopped":
            if status.get("exitstatus") == "OK":
                return
            else:
                raise RuntimeError(
                    f"Task exited with status: {status.get('exitstatus')}"
                )
        time.sleep(interval)
        elapsed += interval

    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


# ──────────────────────────────────────────────────────────────
# Container listing & deletion
# ──────────────────────────────────────────────────────────────

def list_containers(proxmox, node):
    """
    Return a list of all LXC containers on the given node.

    Each item is a dict with keys from the Proxmox API, including:
    vmid, name, status, mem, maxmem, disk, maxdisk, cpus, etc.
    """
    try:
        return proxmox.nodes(node).lxc.get()
    except Exception as exc:
        print(f"\n  ✗ Failed to list containers: {exc}\n")
        return []


def pick_containers_to_delete(containers):
    """
    Display all LXC containers and let the user pick one or more to delete.

    Returns a list of selected container dicts.
    """
    print("\n┌─────────────────────────────────────────────┐")
    print("│         LXC Containers on This Node         │")
    print("└─────────────────────────────────────────────┘")

    if not containers:
        print("\n  (no containers found on this node)\n")
        return []

    # Sort by VMID for consistent ordering
    containers = sorted(containers, key=lambda c: int(c.get("vmid", 0)))

    for idx, ct in enumerate(containers, start=1):
        vmid = ct.get("vmid", "?")
        name = ct.get("name", "(unnamed)")
        status = ct.get("status", "unknown")
        cpus = ct.get("cpus", "?")
        mem_mb = int(ct.get("maxmem", 0)) / (1024 * 1024)
        status_icon = "🟢" if status == "running" else "⚫"
        print(f"  [{idx:>2}]  {status_icon}  CT {vmid} — {name}")
        print(f"        status: {status}  |  cpus: {cpus}  |  "
              f"memory: {mem_mb:.0f} MB")

    print(f"\n  Enter container numbers to delete (comma-separated),")
    print(f"  or 'all' to select all, or 'q' to cancel.")

    while True:
        choice = input("\n  Selection: ").strip().lower()

        if choice in ("q", "quit", "cancel"):
            return []

        if choice == "all":
            return list(containers)

        # Parse comma-separated numbers
        parts = [p.strip() for p in choice.split(",")]
        selected = []
        valid = True
        for part in parts:
            if not part.isdigit():
                valid = False
                break
            idx = int(part)
            if 1 <= idx <= len(containers):
                selected.append(containers[idx - 1])
            else:
                valid = False
                break

        if valid and selected:
            return selected

        print("  ✗ Invalid selection, try again.")


def delete_containers(proxmox, node):
    """
    Interactive flow to list, select, and delete LXC containers.

    Running containers are stopped before deletion.
    """
    containers = list_containers(proxmox, node)
    if not containers:
        return

    selected = pick_containers_to_delete(containers)
    if not selected:
        print("\n  ✗ No containers selected. Returning to menu.\n")
        return

    # Confirmation summary
    print("\n┌─────────────────────────────────────────────┐")
    print("│          Containers Marked for Deletion      │")
    print("└─────────────────────────────────────────────┘")

    for ct in selected:
        vmid = ct.get("vmid", "?")
        name = ct.get("name", "(unnamed)")
        status = ct.get("status", "unknown")
        print(f"  • CT {vmid} — {name}  ({status})")

    running = [ct for ct in selected if ct.get("status") == "running"]
    if running:
        print(f"\n  ⚠  {len(running)} container(s) are currently running "
              f"and will be stopped first.")

    confirm = input("\n  ⚠  This action is IRREVERSIBLE. "
                    "Proceed with deletion? (yes/no): ").strip().lower()
    if confirm not in ("yes",):
        print("\n  ✗ Aborted.\n")
        return

    # Process each container
    for ct in selected:
        vmid = ct.get("vmid")
        name = ct.get("name", "(unnamed)")
        status = ct.get("status", "unknown")

        print(f"\n  ── CT {vmid} ({name}) ──")

        # Stop the container if it's running
        if status == "running":
            print(f"  ⟳ Stopping container {vmid} …")
            try:
                task_id = proxmox.nodes(node).lxc(vmid).status.stop.post()
                wait_for_task(proxmox, node, task_id)
                print(f"  ✓ Container {vmid} stopped.")
            except Exception as exc:
                print(f"  ✗ Failed to stop container {vmid}: {exc}")
                print(f"    Skipping deletion of {vmid}.")
                continue

        # Delete the container
        print(f"  ⟳ Deleting container {vmid} …")
        try:
            task_id = proxmox.nodes(node).lxc(vmid).delete()
            wait_for_task(proxmox, node, task_id)
            print(f"  ✓ Container {vmid} deleted successfully!")
        except Exception as exc:
            print(f"  ✗ Failed to delete container {vmid}: {exc}")

    print()


# ──────────────────────────────────────────────────────────────
# Main menu & entry point
# ──────────────────────────────────────────────────────────────

def main_menu():
    """Display the main menu and return the user's choice."""
    print("┌─────────────────────────────────────────────┐")
    print("│               Main Menu                     │")
    print("└─────────────────────────────────────────────┘")
    print("  [1]  Create a new LXC container")
    print("  [2]  Delete existing LXC container(s)")
    print("  [3]  Exit")

    while True:
        choice = input("\n  Select an option: ").strip()
        if choice in ("1", "2", "3"):
            return choice
        print("  ✗ Invalid selection, try again.")


def create_flow(proxmox, node, host, token_id, token_secret):
    """Run the interactive container-creation workflow."""
    # Pick a template (existing or upload a custom one)
    templates = list_templates(proxmox, node)
    template = pick_template(
        proxmox, node, host, token_id, token_secret, templates
    )

    # Pick storage
    storage = pick_storage(proxmox, node)

    # Configure the new container
    config = configure_container(proxmox, node)

    # Confirm and create
    confirm_and_create(proxmox, node, template, storage, config)


def main():
    print("\n" + "═" * 50)
    print("  Proxmox LXC Manager — Target node: pmx-4")
    print("═" * 50)

    # Load config & connect
    host, token_id, token_secret, node = load_config()
    print(f"\n  ⟳ Connecting to Proxmox ({host}) …")
    proxmox = connect(host, token_id, token_secret)
    print(f"  ✓ Connected to node: {node}\n")

    while True:
        choice = main_menu()

        if choice == "1":
            create_flow(proxmox, node, host, token_id, token_secret)
        elif choice == "2":
            delete_containers(proxmox, node)
        elif choice == "3":
            print("\n  Goodbye!\n")
            break

        print()  # breathing room between menu cycles


if __name__ == "__main__":
    main()
