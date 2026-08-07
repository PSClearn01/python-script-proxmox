#!/usr/bin/env python3
"""
Proxmox LXC Creator
===================
Interactive script to create new LXC containers on a Proxmox node (pmx-4)
from available container templates.

Authenticates via API token loaded from a .env file.

Usage:
    1. Copy .env.example to .env and fill in your Proxmox credentials.
    2. pip install -r requirements.txt
    3. python create_lxc.py
"""

import os
import sys
import getpass
import textwrap

from dotenv import load_dotenv
from proxmoxer import ProxmoxAPI


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


def pick_template(templates):
    """Display a numbered list of templates and let the user choose one."""
    if not templates:
        print("\n✗ No LXC templates found on this node.")
        print("  Upload a template via the Proxmox UI or `pveam`.\n")
        sys.exit(1)

    print("\n┌─────────────────────────────────────────────┐")
    print("│         Available LXC Templates             │")
    print("└─────────────────────────────────────────────┘")

    for idx, tpl in enumerate(templates, start=1):
        size_mb = tpl["size"] / (1024 * 1024) if tpl["size"] else 0
        print(f"  [{idx:>2}]  {tpl['filename']}")
        print(f"        storage: {tpl['storage']}  |  size: {size_mb:.1f} MB")

    while True:
        choice = input("\n  Select a template number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(templates):
            selected = templates[int(choice) - 1]
            print(f"\n  ✓ Selected: {selected['filename']}\n")
            return selected
        print("  ✗ Invalid selection, try again.")


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
    import time

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
# Main
# ──────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 50)
    print("  Proxmox LXC Creator — Target node: pmx-4")
    print("═" * 50)

    # 1. Load config & connect
    host, token_id, token_secret, node = load_config()
    print(f"\n  ⟳ Connecting to Proxmox ({host}) …")
    proxmox = connect(host, token_id, token_secret)
    print(f"  ✓ Connected to node: {node}\n")

    # 2. Pick a template
    templates = list_templates(proxmox, node)
    template = pick_template(templates)

    # 3. Pick storage
    storage = pick_storage(proxmox, node)

    # 4. Configure the new container
    config = configure_container(proxmox, node)

    # 5. Confirm and create
    confirm_and_create(proxmox, node, template, storage, config)


if __name__ == "__main__":
    main()
