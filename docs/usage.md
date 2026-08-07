# Usage Guide

This guide walks through a complete run of the LXC creator script, from launch to a running container.

---

## Running the Script

```bash
python create_lxc.py
```

The script progresses through five interactive stages:

1. **Connect** to Proxmox
2. **Select a template**
3. **Select a storage pool**
4. **Configure the container**
5. **Review and create**

---

## Stage 1 — Connection

The script loads credentials from `.env` and connects to the Proxmox API.

```
══════════════════════════════════════════════════
  Proxmox LXC Creator — Target node: pmx-4
══════════════════════════════════════════════════

  ⟳ Connecting to Proxmox (192.168.1.50:8006) …
  ✓ Connected to node: pmx-4
```

If credentials are missing or the connection fails, the script will print a clear error and exit. See [Troubleshooting](troubleshooting.md) for common issues.

---

## Stage 2 — Template Selection

The script scans all storage pools on the node for LXC templates (`vztmpl` content type) and presents a numbered list. The last option always allows you to **upload a custom template** from a local file:

```
┌─────────────────────────────────────────────┐
│         Available LXC Templates             │
└─────────────────────────────────────────────┘
  [ 1]  ubuntu-24.04-standard_24.04-2_amd64.tar.zst
        storage: local  |  size: 128.3 MB
  [ 2]  debian-12-standard_12.7-1_amd64.tar.zst
        storage: local  |  size: 104.5 MB
  [ 3]  alpine-3.20-default_20240501_amd64.tar.xz
        storage: local  |  size: 3.2 MB

  [ 4]  ⬆  Upload a custom template from local file

  Select a template number: 1

  ✓ Selected: ubuntu-24.04-standard_24.04-2_amd64.tar.zst
```

> **Tip:** If no templates appear, you can still upload your own — just select the upload option.

### Uploading a Custom Template

Select the upload option to use your own container template (`.tar.gz`, `.tar.xz`, or `.tar.zst`). The script will:

1. Prompt for the **path to your local template file**
2. Let you choose a **storage pool** that accepts template uploads (`vztmpl` content type)
3. **Upload the file** to the selected storage via the Proxmox API
4. Automatically use the uploaded template for container creation

```
  ── Upload Custom Template ──
  Supported formats: .tar.gz, .tar.xz, .tar.zst

  Path to template file: ~/templates/my-custom-ubuntu.tar.gz

  ✓ Upload target: local (only eligible storage)

  ⟳ Uploading my-custom-ubuntu.tar.gz (95.2 MB) to 'local' …
  ⟳ Upload task started: UPID:pmx-4:00001B3C:...
  ✓ Template uploaded successfully!
```

> **Note:** The template file must exist on the machine running the script. Paths with `~` and quotes are handled automatically.

---

## Stage 3 — Storage Selection

The script lists storage pools available for container root filesystems (those accepting `rootdir` or `images` content types):

```
┌─────────────────────────────────────────────┐
│           Available Storage Pools            │
└─────────────────────────────────────────────┘
  [ 1]  local-lvm
        type: lvmthin  |  total: 200.0 GB  |  free: 150.3 GB
  [ 2]  local-zfs
        type: zfspool   |  total: 500.0 GB  |  free: 420.7 GB

  Select a storage pool number: 1

  ✓ Selected storage: local-lvm
```

---

## Stage 4 — Container Configuration

You'll be prompted for each setting. Defaults are shown in brackets — press Enter to accept them.

```
┌─────────────────────────────────────────────┐
│         New Container Configuration         │
└─────────────────────────────────────────────┘

  Container ID (VMID) [105]: 
  Hostname: web-server-01
  Root password: ********
  CPU cores [1]: 2
  Memory (MB) [512]: 1024
  Swap (MB) [512]: 
  Root disk size (GB) [8]: 16

  ── Network Configuration ──
  Enter 'dhcp' for automatic IP assignment, or a static
  address in CIDR notation (e.g. 192.168.1.100/24).

  IP address (or 'dhcp') [dhcp]: 192.168.1.100/24
  Bridge interface [vmbr0]: 
  Gateway: 192.168.1.1
  Start on boot? (y/n) [y]: 
  Start container after creation? (y/n) [y]: 
  Unprivileged container? (y/n) [y]: 
```

### Configuration Options Reference

| Setting                  | Default   | Description                                              |
|--------------------------|-----------|----------------------------------------------------------|
| **Container ID (VMID)**  | Auto      | Unique numeric ID; auto-suggested as the next available  |
| **Hostname**             | —         | Required. DNS-friendly name for the container            |
| **Root password**        | —         | Required. Hidden input. Sets the root user password      |
| **CPU cores**            | `1`       | Number of CPU cores allocated                            |
| **Memory (MB)**          | `512`     | RAM allocation in megabytes                              |
| **Swap (MB)**            | `512`     | Swap space in megabytes                                  |
| **Root disk size (GB)**  | `8`       | Root filesystem disk size in gigabytes                   |
| **IP address**           | `dhcp`    | `dhcp` or static IP in CIDR notation (e.g. `10.0.0.5/24`) |
| **Bridge interface**     | `vmbr0`   | Linux bridge to attach the container NIC to              |
| **Gateway**              | —         | Only prompted for static IPs. IPv4 gateway address       |
| **Start on boot**        | `yes`     | Whether the container starts when the host boots         |
| **Start after creation** | `yes`     | Whether to start the container immediately               |
| **Unprivileged**         | `yes`     | Unprivileged containers are more secure (recommended)    |

---

## Stage 5 — Review and Create

A full summary is displayed before anything is created:

```
┌─────────────────────────────────────────────┐
│             Container Summary                │
└─────────────────────────────────────────────┘

  VMID:           105
  Hostname:       web-server-01
  Template:       ubuntu-24.04-standard_24.04-2_amd64.tar.zst
  Storage:        local-lvm
  CPU cores:      2
  Memory:         1024 MB
  Swap:           512 MB
  Root disk:      16 GB
  Network:        name=eth0,bridge=vmbr0
  IP config:      ip=192.168.1.100/24,gw=192.168.1.1
  On boot:        Yes
  Unprivileged:   Yes
  Start after:    Yes
  Node:           pmx-4

  Proceed with creation? (y/n): y

  ⟳ Creating container …
  ✓ Task started: UPID:pmx-4:00001A2B:...
  ⟳ Waiting for task to complete …
  ✓ Container 105 created successfully!

  ⟳ Starting container …
  ✓ Container 105 is now running.
```

Answering `n` aborts without making any changes.

---

## Downloading Templates

If the script reports no templates, you need to download them first. You can do this via:

### Option A — Proxmox Web UI

1. Navigate to **pmx-4** → **local** storage → **CT Templates**
2. Click **Templates** and select the desired distribution
3. Click **Download**

### Option B — Command line on the Proxmox host

```bash
# Update the template list
pveam update

# List available templates
pveam available --section system

# Download a template
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
```

### Option C — Manual upload

Upload a `.tar.gz`, `.tar.xz`, or `.tar.zst` template file to:

```
/var/lib/vz/template/cache/
```

---

## Creating Multiple Containers

Run the script multiple times to create additional containers. Each run will auto-suggest the next available VMID so there's no risk of ID conflicts.

```bash
# Create first container
python create_lxc.py

# Create another
python create_lxc.py
```
