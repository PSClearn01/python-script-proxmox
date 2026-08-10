#!/usr/bin/env python3
"""
Proxmox VM & LXC Manager
=========================
Interactive script to manage LXC containers and QEMU virtual machines
on a Proxmox node.

Features:
  • Create LXC containers from templates (with optional template upload)
  • Clone existing LXC containers (full or linked clones)
  • Create QEMU VMs from ISO images (with optional ISO upload)
  • Clone QEMU VMs from VM templates (full or linked clones)
  • Delete LXC containers and QEMU VMs

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

# Supported ISO file extensions
ISO_EXTENSIONS = (".iso", ".img")


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
# Task helpers
# ──────────────────────────────────────────────────────────────

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


def next_vmid(proxmox, used_vmids=None):
    """Ask the cluster for the next available VMID, skipping any in used_vmids."""
    if used_vmids is None:
        used_vmids = set()
    try:
        vmid = int(proxmox.cluster.nextid.get())
    except Exception:
        vmid = 100
    while vmid in used_vmids:
        vmid += 1
    return vmid


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


# ──────────────────────────────────────────────────────────────
# Template discovery (LXC)
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
# ISO discovery (VMs)
# ──────────────────────────────────────────────────────────────

def list_isos(proxmox, node):
    """
    Return a list of available ISO images across all storage pools
    on the given node.

    Each item is a dict with keys: volid, storage, filename, size.
    """
    isos = []
    storages = proxmox.nodes(node).storage.get()

    for store in storages:
        storage_name = store["storage"]
        try:
            content = proxmox.nodes(node).storage(storage_name).content.get()
            for item in content:
                if item.get("content") == "iso":
                    isos.append({
                        "volid": item["volid"],
                        "storage": storage_name,
                        "filename": item["volid"].split("/")[-1],
                        "size": item.get("size", 0),
                    })
        except Exception:
            continue

    return isos


def list_iso_storages(proxmox, node):
    """
    Return a list of storage pools that accept 'iso' content.

    These are the storages where ISO files can be uploaded.
    """
    eligible = []
    storages = proxmox.nodes(node).storage.get()

    for store in storages:
        content_types = store.get("content", "")
        if "iso" in content_types:
            eligible.append(store)

    return eligible


def pick_iso_storage(proxmox, node):
    """
    Let the user pick a storage pool to upload the ISO to.

    Only storages that accept iso content are listed.
    """
    eligible = list_iso_storages(proxmox, node)

    if not eligible:
        print("\n  ✗ No storage pools accept ISO uploads.")
        print("    Configure a storage pool with 'iso' content type.\n")
        return None

    if len(eligible) == 1:
        selected = eligible[0]["storage"]
        print(f"\n  ✓ Upload target: {selected} (only eligible storage)\n")
        return selected

    print("\n┌─────────────────────────────────────────────┐")
    print("│        Storage Pools for ISO Upload         │")
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


def validate_iso_path(filepath):
    """Validate that the file exists and has a supported ISO extension."""
    if not os.path.isfile(filepath):
        return False, f"File not found: {filepath}"

    if not any(filepath.lower().endswith(ext) for ext in ISO_EXTENSIONS):
        return False, (
            f"Unsupported file type. "
            f"Expected one of: {', '.join(ISO_EXTENSIONS)}"
        )

    return True, None


def upload_iso(proxmox, node, host, token_id, token_secret, filepath,
               storage):
    """
    Upload a local ISO file to a Proxmox storage pool.

    Uses the Proxmox REST API multipart upload endpoint:
        POST /nodes/{node}/storage/{storage}/upload

    Returns an ISO dict compatible with pick_iso output.
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
                "content": "iso",
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
        return None

    result = response.json()
    task_id = result.get("data")
    if task_id:
        print(f"  ⟳ Upload task started: {task_id}")
        try:
            wait_for_task(proxmox, node, task_id, timeout=600)
        except Exception as exc:
            print(f"\n  ✗ Upload task failed: {exc}\n")
            return None

    print(f"  ✓ ISO uploaded successfully!\n")

    volid = f"{storage}:iso/{filename}"
    return {
        "volid": volid,
        "storage": storage,
        "filename": filename,
        "size": file_size,
    }


def pick_iso(proxmox, node, host, token_id, token_secret, isos):
    """
    Display a numbered list of ISOs and let the user choose one,
    or upload a custom ISO from a local file.
    """
    print("\n┌─────────────────────────────────────────────┐")
    print("│           Available ISO Images              │")
    print("└─────────────────────────────────────────────┘")

    if isos:
        for idx, iso in enumerate(isos, start=1):
            size_mb = iso["size"] / (1024 * 1024) if iso["size"] else 0
            print(f"  [{idx:>2}]  {iso['filename']}")
            print(f"        storage: {iso['storage']}  |  size: {size_mb:.1f} MB")
    else:
        print("\n  (no existing ISOs found on this node)")

    upload_idx = len(isos) + 1
    print(f"\n  [{upload_idx:>2}]  ⬆  Upload an ISO from local file")

    while True:
        choice = input("\n  Select an ISO number: ").strip()
        if not choice.isdigit():
            print("  ✗ Invalid selection, try again.")
            continue

        idx = int(choice)
        if 1 <= idx <= len(isos):
            selected = isos[idx - 1]
            print(f"\n  ✓ Selected: {selected['filename']}\n")
            return selected
        elif idx == upload_idx:
            return _handle_iso_upload(
                proxmox, node, host, token_id, token_secret
            )
        else:
            print("  ✗ Invalid selection, try again.")


def _handle_iso_upload(proxmox, node, host, token_id, token_secret):
    """Prompt for a local ISO file path and upload it."""
    print("\n  ── Upload ISO Image ──")
    print(f"  Supported formats: {', '.join(ISO_EXTENSIONS)}\n")

    while True:
        filepath = input("  Path to ISO file: ").strip()
        filepath = filepath.strip('"').strip("'")
        filepath = os.path.expanduser(filepath)
        filepath = os.path.abspath(filepath)

        valid, err = validate_iso_path(filepath)
        if valid:
            break
        print(f"  ✗ {err}")

    storage = pick_iso_storage(proxmox, node)
    if storage is None:
        print("  ✗ Cannot upload ISO — no eligible storage pools.\n")
        return None

    iso = upload_iso(
        proxmox, node, host, token_id, token_secret, filepath, storage
    )
    return iso


# ──────────────────────────────────────────────────────────────
# Storage discovery
# ──────────────────────────────────────────────────────────────

def pick_storage(proxmox, node, content_filter=None):
    """
    List storage pools and let the user pick one.

    Args:
        proxmox: ProxmoxAPI handle.
        node: Proxmox node name.
        content_filter: Optional content type to filter by
                        (e.g. 'rootdir', 'images'). If None, filters
                        by 'rootdir' or 'images' with a fallback to all.
    """
    storages = proxmox.nodes(node).storage.get()

    # Filter storages by content type
    eligible = []
    for store in storages:
        content_types = store.get("content", "")
        if content_filter:
            if content_filter in content_types:
                eligible.append(store)
        else:
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
# LXC container creation (from template)
# ──────────────────────────────────────────────────────────────

def configure_container(proxmox, node, used_vmids=None):
    """Interactively collect all settings for a new container."""
    suggested_id = next_vmid(proxmox, used_vmids)

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


def confirm_and_create(proxmox, node, template, storage, configs):
    """Show a summary and create container(s) upon confirmation."""
    if not isinstance(configs, list):
        configs = [configs]

    count = len(configs)
    header = f"Container Summary ({count} container{'s' if count > 1 else ''})"
    print(f"\n┌─────────────────────────────────────────────┐")
    print(f"│  {header:<43}│")
    print(f"└─────────────────────────────────────────────┘")

    for idx, config in enumerate(configs, start=1):
        if count > 1:
            print(f"\n  ── Container #{idx} ──")
        print(f"  VMID:           {config['vmid']}")
        print(f"  Hostname:       {config['hostname']}")
        print(f"  Template:       {template['filename']}")
        print(f"  Storage:        {storage}")
        print(f"  CPU cores:      {config['cores']}")
        print(f"  Memory:         {config['memory']} MB")
        print(f"  Swap:           {config['swap']} MB")
        print(f"  Root disk:      {config['disk_size']} GB")
        print(f"  Network:        {config['net0']}")
        print(f"  IP config:      {config['ip_config']}")
        print(f"  On boot:        {'Yes' if config['onboot'] else 'No'}")
        print(f"  Unprivileged:   {'Yes' if config['unprivileged'] else 'No'}")
        print(f"  Start after:    {'Yes' if config['start_after'] else 'No'}")
        print(f"  Node:           {node}")

    confirm = input(f"\n  Proceed with creation of {count} container(s)? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("\n  ✗ Aborted.\n")
        return

    for idx, config in enumerate(configs, start=1):
        if count > 1:
            print(f"\n  ━━ Creating container {idx}/{count}: {config['hostname']} (VMID {config['vmid']}) ━━")

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
            "net0": f"{config['net0']},{config['ip_config']}",
            "nameserver": "",
            "onboot": config["onboot"],
            "unprivileged": config["unprivileged"],
        }

        print(f"\n  ⟳ Creating container {config['vmid']} …")

        try:
            task_id = proxmox.nodes(node).lxc.create(**payload)
            print(f"  ✓ Task started: {task_id}")
        except Exception as exc:
            print(f"\n  ✗ Failed to create container {config['vmid']}: {exc}\n")
            continue

        print("  ⟳ Waiting for task to complete …")
        try:
            wait_for_task(proxmox, node, task_id)
        except Exception as exc:
            print(f"\n  ⚠ Could not verify task completion for CT {config['vmid']}: {exc}")
            print("    Check the Proxmox UI for task status.\n")
            continue

        print(f"  ✓ Container {config['vmid']} created successfully!\n")

        if config["start_after"]:
            print(f"  ⟳ Starting container {config['vmid']} …")
            try:
                proxmox.nodes(node).lxc(config["vmid"]).status.start.post()
                print(f"  ✓ Container {config['vmid']} is now running.\n")
            except Exception as exc:
                print(f"  ⚠ Failed to start container {config['vmid']}: {exc}\n")


# ──────────────────────────────────────────────────────────────
# LXC container cloning
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


def pick_clone_source(proxmox, node):
    """
    Display all LXC containers and let the user pick one to clone.

    Returns the selected container dict, or None if cancelled.
    """
    containers = list_containers(proxmox, node)

    print("\n┌─────────────────────────────────────────────┐")
    print("│       LXC Containers Available to Clone     │")
    print("└─────────────────────────────────────────────┘")

    if not containers:
        print("\n  (no containers found on this node)\n")
        return None

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

    while True:
        choice = input("\n  Select a container to clone (or 'q' to cancel): ").strip()
        if choice.lower() in ("q", "quit", "cancel"):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(containers):
            selected = containers[int(choice) - 1]
            vmid = selected.get("vmid", "?")
            name = selected.get("name", "(unnamed)")
            print(f"\n  ✓ Clone source: CT {vmid} — {name}\n")
            return selected
        print("  ✗ Invalid selection, try again.")


def configure_clone(proxmox, node, source, used_vmids=None):
    """
    Interactively collect settings for cloning an LXC container.

    Args:
        proxmox: ProxmoxAPI handle.
        node: Proxmox node name.
        source: Source container dict (must have 'vmid' and 'name').
        used_vmids: Optional set of VMIDs already allocated in batch.

    Returns a config dict for the clone operation.
    """
    suggested_id = next_vmid(proxmox, used_vmids)
    source_vmid = source.get("vmid", "?")
    source_name = source.get("name", "(unnamed)")

    print("┌─────────────────────────────────────────────┐")
    print("│          Clone Configuration                │")
    print("└─────────────────────────────────────────────┘")
    print(f"\n  Source: CT {source_vmid} — {source_name}\n")

    new_vmid = prompt_value("New Container ID (VMID)", default=suggested_id, cast=int)
    hostname = prompt_value("New hostname", default=f"{source_name}-clone")

    # Full or linked clone
    print("\n  ── Clone Type ──")
    print("  Full clone:   Independent copy (uses more disk space)")
    print("  Linked clone: Shares base image (faster, less disk)\n")
    clone_type = prompt_value("Clone type (full/linked)", default="full")
    full_clone = 1 if clone_type.lower() in ("full", "f") else 0

    # Target storage (only relevant for full clones)
    target_storage = None
    if full_clone:
        print("\n  ── Target Storage for Clone ──")
        use_custom = prompt_value(
            "Use a different storage for the clone? (y/n)", default="n"
        )
        if use_custom.lower() in ("y", "yes"):
            target_storage = pick_storage(proxmox, node)

    # Description
    description = prompt_value(
        "Description (optional)",
        default=f"Clone of CT {source_vmid} ({source_name})"
    )

    # Start after clone?
    start_input = prompt_value("Start container after cloning? (y/n)", default="n")
    start_after = start_input.lower() in ("y", "yes")

    config = {
        "new_vmid": new_vmid,
        "hostname": hostname,
        "full_clone": full_clone,
        "target_storage": target_storage,
        "description": description,
        "start_after": start_after,
    }
    return config


def confirm_and_clone(proxmox, node, source, configs):
    """Show a summary and execute LXC clone(s) upon confirmation."""
    if not isinstance(configs, list):
        configs = [configs]

    count = len(configs)
    source_vmid = source.get("vmid", "?")
    source_name = source.get("name", "(unnamed)")

    header = f"Clone Summary ({count} clone{'s' if count > 1 else ''})"
    print(f"\n┌─────────────────────────────────────────────┐")
    print(f"│  {header:<43}│")
    print(f"└─────────────────────────────────────────────┘")
    print(f"  Source: CT {source_vmid} — {source_name}")

    for idx, config in enumerate(configs, start=1):
        clone_type_label = "Full" if config["full_clone"] else "Linked"
        storage_label = config["target_storage"] or "(same as source)"
        if count > 1:
            print(f"\n  ── Clone #{idx} ──")
        print(f"  New VMID:       {config['new_vmid']}")
        print(f"  New hostname:   {config['hostname']}")
        print(f"  Clone type:     {clone_type_label}")
        print(f"  Target storage: {storage_label}")
        print(f"  Description:    {config['description']}")
        print(f"  Start after:    {'Yes' if config['start_after'] else 'No'}")
        print(f"  Node:           {node}")

    confirm = input(f"\n  Proceed with cloning {count} container(s)? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("\n  ✗ Aborted.\n")
        return

    for idx, config in enumerate(configs, start=1):
        if count > 1:
            print(f"\n  ━━ Cloning container {idx}/{count}: {config['hostname']} (VMID {config['new_vmid']}) ━━")

        payload = {
            "newid": config["new_vmid"],
            "hostname": config["hostname"],
            "full": config["full_clone"],
            "description": config["description"],
        }
        if config["target_storage"]:
            payload["storage"] = config["target_storage"]

        print(f"\n  ⟳ Cloning container {config['new_vmid']} …")

        try:
            task_id = proxmox.nodes(node).lxc(source_vmid).clone.post(**payload)
            print(f"  ✓ Task started: {task_id}")
        except Exception as exc:
            print(f"\n  ✗ Failed to clone container {config['new_vmid']}: {exc}\n")
            continue

        print("  ⟳ Waiting for clone to complete …")
        try:
            wait_for_task(proxmox, node, task_id, timeout=300)
        except Exception as exc:
            print(f"\n  ⚠ Could not verify task completion for CT {config['new_vmid']}: {exc}")
            print("    Check the Proxmox UI for task status.\n")
            continue

        print(f"  ✓ Container {config['new_vmid']} cloned successfully!\n")

        if config["start_after"]:
            print(f"  ⟳ Starting cloned container {config['new_vmid']} …")
            try:
                proxmox.nodes(node).lxc(config["new_vmid"]).status.start.post()
                print(f"  ✓ Container {config['new_vmid']} is now running.\n")
            except Exception as exc:
                print(f"  ⚠ Failed to start container {config['new_vmid']}: {exc}\n")


# ──────────────────────────────────────────────────────────────
# VM creation (from ISO)
# ──────────────────────────────────────────────────────────────

# Common OS types for the Proxmox 'ostype' parameter
OS_TYPES = [
    ("l26",      "Linux 2.6 – 6.x kernel"),
    ("l24",      "Linux 2.4 kernel"),
    ("win11",    "Windows 11 / Server 2025"),
    ("win10",    "Windows 10 / Server 2016–2022"),
    ("win8",     "Windows 8 / Server 2012"),
    ("win7",     "Windows 7 / Server 2008 R2"),
    ("wxp",      "Windows XP / Server 2003"),
    ("solaris",  "Solaris / OpenSolaris"),
    ("other",    "Other / Unspecified"),
]


def configure_vm(proxmox, node, used_vmids=None):
    """
    Interactively collect all settings for a new virtual machine.

    Returns a config dict with all VM parameters.
    """
    suggested_id = next_vmid(proxmox, used_vmids)

    print("┌─────────────────────────────────────────────┐")
    print("│       New Virtual Machine Configuration     │")
    print("└─────────────────────────────────────────────┘\n")

    vmid = prompt_value("VM ID (VMID)", default=suggested_id, cast=int)
    name = prompt_value("VM name", required=True)

    # OS type
    print("\n  ── OS Type ──")
    for idx, (key, label) in enumerate(OS_TYPES, start=1):
        print(f"  [{idx:>2}]  {label}  ({key})")

    while True:
        os_choice = input("\n  Select OS type [1]: ").strip() or "1"
        if os_choice.isdigit() and 1 <= int(os_choice) <= len(OS_TYPES):
            ostype = OS_TYPES[int(os_choice) - 1][0]
            print(f"  ✓ OS type: {ostype}\n")
            break
        print("  ✗ Invalid selection, try again.")

    # BIOS type
    print("  ── BIOS Type ──")
    print("  [1]  SeaBIOS (legacy BIOS — most compatible)")
    print("  [2]  OVMF (UEFI — required for some modern OSes)\n")
    bios_choice = prompt_value("BIOS type", default="1")
    bios = "ovmf" if bios_choice in ("2", "ovmf", "uefi") else "seabios"
    print(f"  ✓ BIOS: {bios}\n")

    # CPU & Memory
    sockets = prompt_value("CPU sockets", default=1, cast=int)
    cores = prompt_value("CPU cores per socket", default=2, cast=int)
    cpu_type = prompt_value("CPU type", default="host")
    memory = prompt_value("Memory (MB)", default=2048, cast=int)
    balloon = prompt_value("Balloon memory minimum (MB, 0 to disable)",
                           default=0, cast=int)

    # Disk
    disk_size = prompt_value("Disk size (GB)", default=32, cast=int)

    # SCSI controller type
    print("\n  ── SCSI Controller ──")
    print("  [1]  VirtIO SCSI Single (recommended)")
    print("  [2]  VirtIO SCSI")
    print("  [3]  LSI 53C895A (legacy)\n")
    scsi_choice = prompt_value("SCSI controller", default="1")
    if scsi_choice in ("2", "virtio-scsi-pci"):
        scsihw = "virtio-scsi-pci"
    elif scsi_choice in ("3", "lsi"):
        scsihw = "lsi"
    else:
        scsihw = "virtio-scsi-single"
    print(f"  ✓ SCSI controller: {scsihw}\n")

    # Networking
    print("  ── Network Configuration ──\n")
    net_model = prompt_value("Network adapter model", default="virtio")
    bridge = prompt_value("Bridge interface", default="vmbr0")
    firewall = prompt_value("Enable firewall? (y/n)", default="n")
    fw_flag = 1 if firewall.lower() in ("y", "yes") else 0

    # VGA
    vga = prompt_value("Display type (std/virtio/vmware/qxl/none)",
                       default="std")

    # Boot order
    print("\n  ── Boot Order ──")
    print("  The VM will try to boot from the ISO (CD-ROM) first,")
    print("  then fall back to the primary disk.\n")

    # Start on boot?
    onboot_input = prompt_value("Start on boot? (y/n)", default="n")
    onboot = 1 if onboot_input.lower() in ("y", "yes") else 0

    # Start after creation?
    start_input = prompt_value("Start VM after creation? (y/n)", default="y")
    start_after = start_input.lower() in ("y", "yes")

    # QEMU agent
    agent_input = prompt_value("Enable QEMU Guest Agent? (y/n)", default="y")
    agent = 1 if agent_input.lower() in ("y", "yes") else 0

    config = {
        "vmid": vmid,
        "name": name,
        "ostype": ostype,
        "bios": bios,
        "sockets": sockets,
        "cores": cores,
        "cpu_type": cpu_type,
        "memory": memory,
        "balloon": balloon,
        "disk_size": disk_size,
        "scsihw": scsihw,
        "net_model": net_model,
        "bridge": bridge,
        "firewall": fw_flag,
        "vga": vga,
        "onboot": onboot,
        "start_after": start_after,
        "agent": agent,
    }
    return config


def confirm_and_create_vm(proxmox, node, iso, storage, configs):
    """Show a summary and create VM(s) upon confirmation."""
    if not isinstance(configs, list):
        configs = [configs]

    count = len(configs)
    header = f"Virtual Machine Summary ({count} VM{'s' if count > 1 else ''})"
    print(f"\n┌─────────────────────────────────────────────┐")
    print(f"│  {header:<43}│")
    print(f"└─────────────────────────────────────────────┘")

    for idx, config in enumerate(configs, start=1):
        if count > 1:
            print(f"\n  ── VM #{idx} ──")
        print(f"  VMID:           {config['vmid']}")
        print(f"  Name:           {config['name']}")
        print(f"  OS type:        {config['ostype']}")
        print(f"  BIOS:           {config['bios']}")
        print(f"  ISO:            {iso['filename']}")
        print(f"  Storage:        {storage}")
        print(f"  CPU:            {config['sockets']} socket(s) × {config['cores']} core(s)  [{config['cpu_type']}]")
        print(f"  Memory:         {config['memory']} MB")
        print(f"  Balloon:        {config['balloon']} MB")
        print(f"  Disk:           {config['disk_size']} GB")
        print(f"  SCSI:           {config['scsihw']}")
        print(f"  Network:        {config['net_model']}, bridge={config['bridge']}")
        print(f"  Firewall:       {'Yes' if config['firewall'] else 'No'}")
        print(f"  Display:        {config['vga']}")
        print(f"  QEMU agent:     {'Yes' if config['agent'] else 'No'}")
        print(f"  On boot:        {'Yes' if config['onboot'] else 'No'}")
        print(f"  Start after:    {'Yes' if config['start_after'] else 'No'}")
        print(f"  Node:           {node}")

    confirm = input(f"\n  Proceed with creation of {count} VM(s)? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("\n  ✗ Aborted.\n")
        return

    for idx, config in enumerate(configs, start=1):
        if count > 1:
            print(f"\n  ━━ Creating VM {idx}/{count}: {config['name']} (VMID {config['vmid']}) ━━")

        net0 = f"{config['net_model']},bridge={config['bridge']}"
        if config["firewall"]:
            net0 += ",firewall=1"

        payload = {
            "vmid": config["vmid"],
            "name": config["name"],
            "ostype": config["ostype"],
            "bios": config["bios"],
            "sockets": config["sockets"],
            "cores": config["cores"],
            "cpu": config["cpu_type"],
            "memory": config["memory"],
            "balloon": config["balloon"],
            "scsihw": config["scsihw"],
            "scsi0": f"{storage}:{config['disk_size']}",
            "ide2": f"{iso['volid']},media=cdrom",
            "net0": net0,
            "vga": config["vga"],
            "onboot": config["onboot"],
            "agent": config["agent"],
            "boot": "order=ide2;scsi0",
        }

        if config["bios"] == "ovmf":
            payload["efidisk0"] = f"{storage}:1"

        print(f"\n  ⟳ Creating virtual machine {config['vmid']} …")

        try:
            task_id = proxmox.nodes(node).qemu.create(**payload)
            print(f"  ✓ Task started: {task_id}")
        except Exception as exc:
            print(f"\n  ✗ Failed to create VM {config['vmid']}: {exc}\n")
            continue

        print("  ⟳ Waiting for task to complete …")
        try:
            wait_for_task(proxmox, node, task_id)
        except Exception as exc:
            print(f"\n  ⚠ Could not verify task completion for VM {config['vmid']}: {exc}")
            print("    Check the Proxmox UI for task status.\n")
            continue

        print(f"  ✓ VM {config['vmid']} created successfully!\n")

        if config["start_after"]:
            print(f"  ⟳ Starting virtual machine {config['vmid']} …")
            try:
                proxmox.nodes(node).qemu(config["vmid"]).status.start.post()
                print(f"  ✓ VM {config['vmid']} is now running.\n")
            except Exception as exc:
                print(f"  ⚠ Failed to start VM {config['vmid']}: {exc}\n")


# ──────────────────────────────────────────────────────────────
# VM cloning (from VM template)
# ──────────────────────────────────────────────────────────────

def list_vm_templates(proxmox, node):
    """
    Return a list of QEMU VMs that are marked as templates on the given node.

    Each item is a dict with keys from the Proxmox API, including:
    vmid, name, status, maxmem, maxdisk, cpus, etc.
    """
    vms = list_vms(proxmox, node)
    templates = []
    for vm in vms:
        if vm.get("template"):
            templates.append(vm)
    return sorted(templates, key=lambda v: int(v.get("vmid", 0)))


def pick_vm_template(proxmox, node):
    """
    Display all QEMU VM templates and let the user pick one to clone.

    Returns the selected VM template dict, or None if cancelled.
    """
    templates = list_vm_templates(proxmox, node)

    print("\n┌─────────────────────────────────────────────┐")
    print("│       VM Templates Available to Clone       │")
    print("└─────────────────────────────────────────────┘")

    if not templates:
        print("\n  (no VM templates found on this node)")
        print("  Tip: Convert a VM to a template in the Proxmox UI")
        print("       (right-click → Convert to Template).\n")
        return None

    for idx, vm in enumerate(templates, start=1):
        vmid = vm.get("vmid", "?")
        name = vm.get("name", "(unnamed)")
        cpus = vm.get("cpus", "?")
        mem_mb = int(vm.get("maxmem", 0)) / (1024 * 1024)
        disk_gb = int(vm.get("maxdisk", 0)) / (1024 ** 3)
        print(f"  [{idx:>2}]  📋  VM {vmid} — {name}")
        print(f"        cpus: {cpus}  |  memory: {mem_mb:.0f} MB  |  "
              f"disk: {disk_gb:.1f} GB")

    while True:
        choice = input(
            "\n  Select a VM template to clone (or 'q' to cancel): "
        ).strip()
        if choice.lower() in ("q", "quit", "cancel"):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(templates):
            selected = templates[int(choice) - 1]
            vmid = selected.get("vmid", "?")
            name = selected.get("name", "(unnamed)")
            print(f"\n  ✓ Clone source: VM {vmid} — {name}\n")
            return selected
        print("  ✗ Invalid selection, try again.")


def configure_vm_clone(proxmox, node, source, used_vmids=None):
    """
    Interactively collect settings for cloning a VM from a template.

    Args:
        proxmox: ProxmoxAPI handle.
        node: Proxmox node name.
        source: Source VM template dict (must have 'vmid' and 'name').
        used_vmids: Optional set of VMIDs already allocated in batch.

    Returns a config dict for the clone operation.
    """
    suggested_id = next_vmid(proxmox, used_vmids)
    source_vmid = source.get("vmid", "?")
    source_name = source.get("name", "(unnamed)")

    print("┌─────────────────────────────────────────────┐")
    print("│        VM Clone Configuration               │")
    print("└─────────────────────────────────────────────┘")
    print(f"\n  Source template: VM {source_vmid} — {source_name}\n")

    new_vmid = prompt_value("New VM ID (VMID)", default=suggested_id, cast=int)
    name = prompt_value("New VM name", default=f"{source_name}-clone")

    # Full or linked clone
    print("\n  ── Clone Type ──")
    print("  Full clone:   Independent copy (uses more disk space, slower)")
    print("  Linked clone: Shares base image with template (faster, less disk)")
    print("                Note: Template cannot be deleted while linked")
    print("                clones exist.\n")
    clone_type = prompt_value("Clone type (full/linked)", default="full")
    full_clone = 1 if clone_type.lower() in ("full", "f") else 0

    # Target storage (only relevant for full clones)
    target_storage = None
    if full_clone:
        print("\n  ── Target Storage for Clone ──")
        use_custom = prompt_value(
            "Use a different storage for the clone? (y/n)", default="n"
        )
        if use_custom.lower() in ("y", "yes"):
            target_storage = pick_storage(
                proxmox, node, content_filter="images"
            )

    # Format (disk format for full clones)
    disk_format = None
    if full_clone:
        print("\n  ── Disk Format ──")
        print("  [1]  qcow2 (QEMU copy-on-write, supports snapshots)")
        print("  [2]  raw   (raw disk image, best performance)")
        print("  [3]  vmdk  (VMware compatible)")
        print("  [4]  (same as source)\n")
        fmt_choice = prompt_value("Disk format", default="4")
        format_map = {"1": "qcow2", "2": "raw", "3": "vmdk"}
        disk_format = format_map.get(fmt_choice)

    # Description
    description = prompt_value(
        "Description (optional)",
        default=f"Clone of VM {source_vmid} ({source_name})"
    )

    # Start after clone?
    start_input = prompt_value(
        "Start VM after cloning? (y/n)", default="n"
    )
    start_after = start_input.lower() in ("y", "yes")

    config = {
        "new_vmid": new_vmid,
        "name": name,
        "full_clone": full_clone,
        "target_storage": target_storage,
        "disk_format": disk_format,
        "description": description,
        "start_after": start_after,
    }
    return config


def confirm_and_clone_vm(proxmox, node, source, configs):
    """Show a summary and execute VM template clone(s) upon confirmation."""
    if not isinstance(configs, list):
        configs = [configs]

    count = len(configs)
    source_vmid = source.get("vmid", "?")
    source_name = source.get("name", "(unnamed)")

    header = f"VM Clone Summary ({count} clone{'s' if count > 1 else ''})"
    print(f"\n┌─────────────────────────────────────────────┐")
    print(f"│  {header:<43}│")
    print(f"└─────────────────────────────────────────────┘")
    print(f"  Source: VM {source_vmid} — {source_name}")

    for idx, config in enumerate(configs, start=1):
        clone_type_label = "Full" if config["full_clone"] else "Linked"
        storage_label = config["target_storage"] or "(same as source)"
        format_label = config["disk_format"] or "(same as source)"
        if count > 1:
            print(f"\n  ── VM Clone #{idx} ──")
        print(f"  New VMID:       {config['new_vmid']}")
        print(f"  New name:       {config['name']}")
        print(f"  Clone type:     {clone_type_label}")
        print(f"  Target storage: {storage_label}")
        print(f"  Disk format:    {format_label}")
        print(f"  Description:    {config['description']}")
        print(f"  Start after:    {'Yes' if config['start_after'] else 'No'}")
        print(f"  Node:           {node}")

    confirm = input(f"\n  Proceed with VM clone of {count} VM(s)? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("\n  ✗ Aborted.\n")
        return

    for idx, config in enumerate(configs, start=1):
        if count > 1:
            print(f"\n  ━━ Cloning VM {idx}/{count}: {config['name']} (VMID {config['new_vmid']}) ━━")

        payload = {
            "newid": config["new_vmid"],
            "name": config["name"],
            "full": config["full_clone"],
            "description": config["description"],
        }
        if config["target_storage"]:
            payload["storage"] = config["target_storage"]
        if config["disk_format"]:
            payload["format"] = config["disk_format"]

        print(f"\n  ⟳ Cloning virtual machine {config['new_vmid']} …")

        try:
            task_id = proxmox.nodes(node).qemu(source_vmid).clone.post(**payload)
            print(f"  ✓ Task started: {task_id}")
        except Exception as exc:
            print(f"\n  ✗ Failed to clone VM {config['new_vmid']}: {exc}\n")
            continue

        print("  ⟳ Waiting for clone to complete …")
        try:
            wait_for_task(proxmox, node, task_id, timeout=600)
        except Exception as exc:
            print(f"\n  ⚠ Could not verify task completion for VM {config['new_vmid']}: {exc}")
            print("    Check the Proxmox UI for task status.\n")
            continue

        print(f"  ✓ VM {config['new_vmid']} cloned successfully!\n")

        if config["start_after"]:
            print(f"  ⟳ Starting cloned virtual machine {config['new_vmid']} …")
            try:
                proxmox.nodes(node).qemu(config["new_vmid"]).status.start.post()
                print(f"  ✓ VM {config['new_vmid']} is now running.\n")
            except Exception as exc:
                print(f"  ⚠ Failed to start VM {config['new_vmid']}: {exc}\n")


# ──────────────────────────────────────────────────────────────
# Container listing & deletion
# ──────────────────────────────────────────────────────────────

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
# VM listing & deletion
# ──────────────────────────────────────────────────────────────

def list_vms(proxmox, node):
    """
    Return a list of all QEMU VMs on the given node.

    Each item is a dict with keys from the Proxmox API, including:
    vmid, name, status, mem, maxmem, disk, maxdisk, cpus, etc.
    """
    try:
        return proxmox.nodes(node).qemu.get()
    except Exception as exc:
        print(f"\n  ✗ Failed to list VMs: {exc}\n")
        return []


def pick_vms_to_delete(vms):
    """
    Display all QEMU VMs and let the user pick one or more to delete.

    Returns a list of selected VM dicts.
    """
    print("\n┌─────────────────────────────────────────────┐")
    print("│          QEMU VMs on This Node              │")
    print("└─────────────────────────────────────────────┘")

    if not vms:
        print("\n  (no VMs found on this node)\n")
        return []

    # Sort by VMID for consistent ordering
    vms = sorted(vms, key=lambda v: int(v.get("vmid", 0)))

    for idx, vm in enumerate(vms, start=1):
        vmid = vm.get("vmid", "?")
        name = vm.get("name", "(unnamed)")
        status = vm.get("status", "unknown")
        cpus = vm.get("cpus", "?")
        mem_mb = int(vm.get("maxmem", 0)) / (1024 * 1024)
        status_icon = "🟢" if status == "running" else "⚫"
        print(f"  [{idx:>2}]  {status_icon}  VM {vmid} — {name}")
        print(f"        status: {status}  |  cpus: {cpus}  |  "
              f"memory: {mem_mb:.0f} MB")

    print(f"\n  Enter VM numbers to delete (comma-separated),")
    print(f"  or 'all' to select all, or 'q' to cancel.")

    while True:
        choice = input("\n  Selection: ").strip().lower()

        if choice in ("q", "quit", "cancel"):
            return []

        if choice == "all":
            return list(vms)

        # Parse comma-separated numbers
        parts = [p.strip() for p in choice.split(",")]
        selected = []
        valid = True
        for part in parts:
            if not part.isdigit():
                valid = False
                break
            idx = int(part)
            if 1 <= idx <= len(vms):
                selected.append(vms[idx - 1])
            else:
                valid = False
                break

        if valid and selected:
            return selected

        print("  ✗ Invalid selection, try again.")


def delete_vms(proxmox, node):
    """
    Interactive flow to list, select, and delete QEMU VMs.

    Running VMs are stopped before deletion.
    """
    vms = list_vms(proxmox, node)
    if not vms:
        return

    selected = pick_vms_to_delete(vms)
    if not selected:
        print("\n  ✗ No VMs selected. Returning to menu.\n")
        return

    # Confirmation summary
    print("\n┌─────────────────────────────────────────────┐")
    print("│            VMs Marked for Deletion           │")
    print("└─────────────────────────────────────────────┘")

    for vm in selected:
        vmid = vm.get("vmid", "?")
        name = vm.get("name", "(unnamed)")
        status = vm.get("status", "unknown")
        print(f"  • VM {vmid} — {name}  ({status})")

    running = [vm for vm in selected if vm.get("status") == "running"]
    if running:
        print(f"\n  ⚠  {len(running)} VM(s) are currently running "
              f"and will be stopped first.")

    confirm = input("\n  ⚠  This action is IRREVERSIBLE. "
                    "Proceed with deletion? (yes/no): ").strip().lower()
    if confirm not in ("yes",):
        print("\n  ✗ Aborted.\n")
        return

    # Process each VM
    for vm in selected:
        vmid = vm.get("vmid")
        name = vm.get("name", "(unnamed)")
        status = vm.get("status", "unknown")

        print(f"\n  ── VM {vmid} ({name}) ──")

        # Stop the VM if it's running
        if status == "running":
            print(f"  ⟳ Stopping VM {vmid} …")
            try:
                task_id = proxmox.nodes(node).qemu(vmid).status.stop.post()
                wait_for_task(proxmox, node, task_id)
                print(f"  ✓ VM {vmid} stopped.")
            except Exception as exc:
                print(f"  ✗ Failed to stop VM {vmid}: {exc}")
                print(f"    Skipping deletion of {vmid}.")
                continue

        # Delete the VM
        print(f"  ⟳ Deleting VM {vmid} …")
        try:
            task_id = proxmox.nodes(node).qemu(vmid).delete()
            wait_for_task(proxmox, node, task_id)
            print(f"  ✓ VM {vmid} deleted successfully!")
        except Exception as exc:
            print(f"  ✗ Failed to delete VM {vmid}: {exc}")

    print()


# ──────────────────────────────────────────────────────────────
# Workflow orchestrators
# ──────────────────────────────────────────────────────────────

def create_lxc_flow(proxmox, node, host, token_id, token_secret):
    """Run the interactive LXC container-creation workflow."""
    print("┌─────────────────────────────────────────────┐")
    print("│         Create LXC Container(s)             │")
    print("└─────────────────────────────────────────────┘\n")

    count = prompt_value("Number of LXC containers to create", default=1, cast=int)
    if count < 1:
        print("  ✗ Count must be at least 1.\n")
        return

    # Pick a template (existing or upload a custom one)
    templates = list_templates(proxmox, node)
    template = pick_template(
        proxmox, node, host, token_id, token_secret, templates
    )

    # Pick storage
    storage = pick_storage(proxmox, node)

    # Configure each container
    used_vmids = set()
    configs = []
    for i in range(1, count + 1):
        if count > 1:
            print(f"\n── Container {i} of {count} Configuration ──")
        config = configure_container(proxmox, node, used_vmids=used_vmids)
        used_vmids.add(config["vmid"])
        configs.append(config)

    # Confirm and create
    confirm_and_create(proxmox, node, template, storage, configs)


def clone_lxc_flow(proxmox, node):
    """Run the interactive LXC clone workflow."""
    # Pick a source container
    source = pick_clone_source(proxmox, node)
    if source is None:
        print("  ✗ No clone source selected. Returning to menu.\n")
        return

    count = prompt_value("Number of LXC clones to create", default=1, cast=int)
    if count < 1:
        print("  ✗ Count must be at least 1.\n")
        return

    # Configure each clone
    used_vmids = set()
    configs = []
    for i in range(1, count + 1):
        if count > 1:
            print(f"\n── Clone {i} of {count} Configuration ──")
        config = configure_clone(proxmox, node, source, used_vmids=used_vmids)
        used_vmids.add(config["new_vmid"])
        configs.append(config)

    # Confirm and clone
    confirm_and_clone(proxmox, node, source, configs)


def create_vm_flow(proxmox, node, host, token_id, token_secret):
    """Run the interactive VM-creation workflow."""
    print("┌─────────────────────────────────────────────┐")
    print("│         Create Virtual Machine(s)           │")
    print("└─────────────────────────────────────────────┘\n")

    count = prompt_value("Number of VMs to create", default=1, cast=int)
    if count < 1:
        print("  ✗ Count must be at least 1.\n")
        return

    # Pick an ISO (existing or upload one)
    isos = list_isos(proxmox, node)
    iso = pick_iso(proxmox, node, host, token_id, token_secret, isos)

    if iso is None:
        print("  ✗ No ISO selected. Returning to menu.\n")
        return

    # Pick storage for the VM disk
    print("  ── VM Disk Storage ──\n")
    storage = pick_storage(proxmox, node, content_filter="images")

    # Configure each VM
    used_vmids = set()
    configs = []
    for i in range(1, count + 1):
        if count > 1:
            print(f"\n── VM {i} of {count} Configuration ──")
        config = configure_vm(proxmox, node, used_vmids=used_vmids)
        used_vmids.add(config["vmid"])
        configs.append(config)

    # Confirm and create
    confirm_and_create_vm(proxmox, node, iso, storage, configs)


def clone_vm_flow(proxmox, node):
    """Run the interactive VM-from-template clone workflow."""
    # Pick a VM template
    source = pick_vm_template(proxmox, node)
    if source is None:
        print("  ✗ No VM template selected. Returning to menu.\n")
        return

    count = prompt_value("Number of VM clones to create", default=1, cast=int)
    if count < 1:
        print("  ✗ Count must be at least 1.\n")
        return

    # Configure each clone
    used_vmids = set()
    configs = []
    for i in range(1, count + 1):
        if count > 1:
            print(f"\n── VM Clone {i} of {count} Configuration ──")
        config = configure_vm_clone(proxmox, node, source, used_vmids=used_vmids)
        used_vmids.add(config["new_vmid"])
        configs.append(config)

    # Confirm and clone
    confirm_and_clone_vm(proxmox, node, source, configs)



# ──────────────────────────────────────────────────────────────
# Main menu & entry point
# ──────────────────────────────────────────────────────────────

def main_menu():
    """Display the main menu and return the user's choice."""
    print("┌─────────────────────────────────────────────┐")
    print("│               Main Menu                     │")
    print("├─────────────────────────────────────────────┤")
    print("│  LXC Containers                             │")
    print("│  [1]  Create LXC from template              │")
    print("│  [2]  Clone an existing LXC container       │")
    print("│  [3]  Delete LXC container(s)               │")
    print("├─────────────────────────────────────────────┤")
    print("│  Virtual Machines                           │")
    print("│  [4]  Create VM from ISO                    │")
    print("│  [5]  Clone VM from template                │")
    print("│  [6]  Delete VM(s)                          │")
    print("├─────────────────────────────────────────────┤")
    print("│  [7]  Exit                                  │")
    print("└─────────────────────────────────────────────┘")

    while True:
        choice = input("\n  Select an option: ").strip()
        if choice in ("1", "2", "3", "4", "5", "6", "7"):
            return choice
        print("  ✗ Invalid selection, try again.")


def main():
    print("\n" + "═" * 50)
    print("  Proxmox VM & LXC Manager — Target node: pmx-4")
    print("═" * 50)

    # Load config & connect
    host, token_id, token_secret, node = load_config()
    print(f"\n  ⟳ Connecting to Proxmox ({host}) …")
    proxmox = connect(host, token_id, token_secret)
    print(f"  ✓ Connected to node: {node}\n")

    while True:
        choice = main_menu()

        if choice == "1":
            create_lxc_flow(proxmox, node, host, token_id, token_secret)
        elif choice == "2":
            clone_lxc_flow(proxmox, node)
        elif choice == "3":
            delete_containers(proxmox, node)
        elif choice == "4":
            create_vm_flow(proxmox, node, host, token_id, token_secret)
        elif choice == "5":
            clone_vm_flow(proxmox, node)
        elif choice == "6":
            delete_vms(proxmox, node)
        elif choice == "7":
            print("\n  Goodbye!\n")
            break

        print()  # breathing room between menu cycles


if __name__ == "__main__":
    main()
