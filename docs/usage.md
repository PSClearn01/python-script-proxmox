# Usage Guide

This guide walks through a complete run of the Proxmox VM & LXC manager script, covering LXC container creation, cloning, deletion, QEMU virtual machine creation, VM template cloning, and VM deletion.

---

## Running the Script

```bash
python create_lxc.py
```

After connecting to Proxmox, the script presents a **main menu**:

```
┌─────────────────────────────────────────────┐
│               Main Menu                     │
├─────────────────────────────────────────────┤
│  LXC Containers                             │
│  [1]  Create LXC from template              │
│  [2]  Clone an existing LXC container       │
│  [3]  Delete LXC container(s)               │
├─────────────────────────────────────────────┤
│  Virtual Machines                           │
│  [4]  Create VM from ISO                    │
│  [5]  Clone VM from template                │
│  [6]  Delete VM(s)                          │
├─────────────────────────────────────────────┤
│  [7]  Exit                                  │
└─────────────────────────────────────────────┘

  Select an option:
```

The menu loops after each operation, so you can perform multiple management tasks in a single session.

---

## Stage 1 — Connection

The script loads credentials from `.env` and connects to the Proxmox API.

```
══════════════════════════════════════════════════
  Proxmox VM & LXC Manager — Target node: pmx-4
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

## Clone LXC Container

Select option **[2]** from the main menu to enter the LXC container cloning workflow.

### Step 1 — Select Source Container

The script scans the node and displays all existing LXC containers with status indicators (`🟢` running, `⚫` stopped):

```
┌─────────────────────────────────────────────┐
│       LXC Containers Available to Clone     │
└─────────────────────────────────────────────┘
  [ 1]  🟢  CT 100 — web-server-01
        status: running  |  cpus: 2  |  memory: 1024 MB
  [ 2]  🟢  CT 101 — db-server
        status: running  |  cpus: 4  |  memory: 2048 MB
  [ 3]  ⚫  CT 102 — test-box
        status: stopped  |  cpus: 1  |  memory: 512 MB

  Select a container to clone (or 'q' to cancel): 1

  ✓ Clone source: CT 100 — web-server-01
```

### Step 2 — Configure Clone Settings

Configure the clone parameters. Defaults are shown in brackets — press Enter to accept them:

```
┌─────────────────────────────────────────────┐
│          Clone Configuration                │
└─────────────────────────────────────────────┘

  Source: CT 100 — web-server-01

  New Container ID (VMID) [106]: 
  New hostname [web-server-01-clone]: 

  ── Clone Type ──
  Full clone:   Independent copy (uses more disk space)
  Linked clone: Shares base image (faster, less disk)

  Clone type (full/linked) [full]: 

  ── Target Storage for Clone ──
  Use a different storage for the clone? (y/n) [n]: y

┌─────────────────────────────────────────────┐
│           Available Storage Pools            │
└─────────────────────────────────────────────┘
  [ 1]  local-lvm
        type: lvmthin  |  total: 200.0 GB  |  free: 150.3 GB
  [ 2]  local-zfs
        type: zfspool   |  total: 500.0 GB  |  free: 420.7 GB

  Select a storage pool number: 2

  ✓ Selected storage: local-zfs

  Description (optional) [Clone of CT 100 (web-server-01)]: 
  Start container after cloning? (y/n) [n]: y
```

#### Clone Configuration Options Reference

| Setting                  | Default                      | Description                                                  |
|--------------------------|------------------------------|--------------------------------------------------------------|
| **New Container ID (VMID)** | Auto                      | Unique numeric ID for the cloned container                   |
| **New hostname**         | `<source_name>-clone`        | Hostname assigned to the clone                               |
| **Clone type**           | `full`                       | `full` (independent copy) or `linked` (shares base image)    |
| **Target storage**       | Source storage               | Target storage pool (optional, for full clones only)         |
| **Description**          | `Clone of CT <vmid> (<name>)` | Optional description/notes for the cloned container          |
| **Start after cloning**  | `no`                         | Whether to start the container immediately after cloning     |

### Step 3 — Review and Execute Clone

A summary is displayed before executing the clone operation:

```
┌─────────────────────────────────────────────┐
│              Clone Summary                   │
└─────────────────────────────────────────────┘

  Source:         CT 100 — web-server-01
  New VMID:       106
  New hostname:   web-server-01-clone
  Clone type:     Full
  Target storage: local-zfs
  Description:    Clone of CT 100 (web-server-01)
  Start after:    Yes
  Node:           pmx-4

  Proceed with clone? (y/n): y

  ⟳ Cloning container …
  ✓ Task started: UPID:pmx-4:00001C3D:...
  ⟳ Waiting for clone to complete …
  ✓ Container 106 cloned successfully!

  ⟳ Starting cloned container …
  ✓ Container 106 is now running.
```

---

## Deleting Containers

Select option **[3]** from the main menu to enter the LXC container deletion workflow.

### Step 1 — Container List

All LXC containers on the node are listed with their status:

```
┌─────────────────────────────────────────────┐
│         LXC Containers on This Node         │
└─────────────────────────────────────────────┘
  [ 1]  🟢  CT 100 — web-server-01
        status: running  |  cpus: 2  |  memory: 1024 MB
  [ 2]  🟢  CT 101 — db-server
        status: running  |  cpus: 4  |  memory: 2048 MB
  [ 3]  ⚫  CT 102 — test-box
        status: stopped  |  cpus: 1  |  memory: 512 MB

  Enter container numbers to delete (comma-separated),
  or 'all' to select all, or 'q' to cancel.

  Selection:
```

### Step 2 — Selection

You can select containers in several ways:

| Input       | Effect                              |
|-------------|-------------------------------------|
| `2`         | Select container #2 only            |
| `1,3`       | Select containers #1 and #3         |
| `all`       | Select all listed containers        |
| `q`         | Cancel and return to the menu       |

### Step 3 — Confirmation

A summary of selected containers is shown. Running containers are noted:

```
┌─────────────────────────────────────────────┐
│          Containers Marked for Deletion      │
└─────────────────────────────────────────────┘
  • CT 100 — web-server-01  (running)
  • CT 102 — test-box  (stopped)

  ⚠  1 container(s) are currently running and will be stopped first.

  ⚠  This action is IRREVERSIBLE. Proceed with deletion? (yes/no):
```

> **Safety:** You must type the full word `yes` to proceed — `y` alone will abort. This prevents accidental deletion.

### Step 4 — Deletion

The script stops running containers, then deletes each one:

```
  ── CT 100 (web-server-01) ──
  ⟳ Stopping container 100 …
  ✓ Container 100 stopped.
  ⟳ Deleting container 100 …
  ✓ Container 100 deleted successfully!

  ── CT 102 (test-box) ──
  ⟳ Deleting container 102 …
  ✓ Container 102 deleted successfully!
```

If a container fails to stop, it is skipped and an error is shown.

---

## Create VM from ISO

Select option **[4]** from the main menu to enter the QEMU VM creation workflow.

### Step 1 — ISO Selection

The script scans all storage pools on the node for available ISO images (`iso` content type) and presents a numbered list. The last option allows you to upload a custom ISO image from a local file (`.iso` and `.img` extensions supported):

```
┌─────────────────────────────────────────────┐
│           Available ISO Images              │
└─────────────────────────────────────────────┘
  [ 1]  ubuntu-24.04-live-server-amd64.iso
        storage: local  |  size: 2621.4 MB
  [ 2]  debian-12.5.0-amd64-netinst.iso
        storage: local  |  size: 650.0 MB

  [ 3]  ⬆  Upload an ISO from local file

  Select an ISO number: 1

  ✓ Selected: ubuntu-24.04-live-server-amd64.iso
```

> **Tip:** If no ISO images appear, select the upload option to upload one directly from your local system.

### Uploading a Custom ISO Image

Select the upload option to use your own ISO image (`.iso` or `.img`). The script will:

1. Prompt for the path to your local ISO file (supports `~` expansion and quotes)
2. Let you choose a storage pool that accepts ISO uploads (`iso` content type)
3. Upload the file to the selected storage via the Proxmox REST API
4. Automatically use the uploaded ISO for VM creation

```
  ── Upload ISO Image ──
  Supported formats: .iso, .img

  Path to ISO file: ~/iso/ubuntu-24.04-live-server-amd64.iso

┌─────────────────────────────────────────────┐
│        Storage Pools for ISO Upload         │
└─────────────────────────────────────────────┘
  [ 1]  local
        type: dir  |  total: 100.0 GB  |  free: 45.2 GB

  Select a storage pool for upload: 1

  ✓ Upload target: local

  ⟳ Uploading ubuntu-24.04-live-server-amd64.iso (2621.4 MB) to 'local' …
  ⟳ Upload task started: UPID:pmx-4:00002A1B:...
  ✓ ISO uploaded successfully!
```

### Step 2 — Storage Selection for VM Disk

The script prompts for a storage pool for the VM primary disk, filtering storage pools by the `images` content type:

```
  ── VM Disk Storage ──

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

### Step 3 — VM Configuration

You will be prompted for each virtual machine setting. Defaults are shown in brackets — press Enter to accept them:

```
┌─────────────────────────────────────────────┐
│       New Virtual Machine Configuration     │
└─────────────────────────────────────────────┘

  VM ID (VMID) [107]: 
  VM name: ubuntu-vm-01

  ── OS Type ──
  [ 1]  Linux 2.6 – 6.x kernel  (l26)
  [ 2]  Linux 2.4 kernel  (l24)
  [ 3]  Windows 11 / Server 2025  (win11)
  [ 4]  Windows 10 / Server 2016–2022  (win10)
  [ 5]  Windows 8 / Server 2012  (win8)
  [ 6]  Windows 7 / Server 2008 R2  (win7)
  [ 7]  Windows XP / Server 2003  (wxp)
  [ 8]  Solaris / OpenSolaris  (solaris)
  [ 9]  Other / Unspecified  (other)

  Select OS type [1]: 1
  ✓ OS type: l26

  ── BIOS Type ──
  [1]  SeaBIOS (legacy BIOS — most compatible)
  [2]  OVMF (UEFI — required for some modern OSes)

  BIOS type [1]: 1
  ✓ BIOS: seabios

  CPU sockets [1]: 1
  CPU cores per socket [2]: 4
  CPU type [host]: host
  Memory (MB) [2048]: 4096
  Balloon memory minimum (MB, 0 to disable) [0]: 2048
  Disk size (GB) [32]: 50

  ── SCSI Controller ──
  [1]  VirtIO SCSI Single (recommended)
  [2]  VirtIO SCSI
  [3]  LSI 53C895A (legacy)

  SCSI controller [1]: 1
  ✓ SCSI controller: virtio-scsi-single

  ── Network Configuration ──

  Network adapter model [virtio]: virtio
  Bridge interface [vmbr0]: vmbr0
  Enable firewall? (y/n) [n]: n
  Display type (std/virtio/vmware/qxl/none) [std]: std

  ── Boot Order ──
  The VM will try to boot from the ISO (CD-ROM) first,
  then fall back to the primary disk.

  Start on boot? (y/n) [n]: y
  Start VM after creation? (y/n) [y]: y
  Enable QEMU Guest Agent? (y/n) [y]: y
```

### Configuration Options Reference (VM)

| Setting                  | Default               | Description                                                                 |
|--------------------------|-----------------------|-----------------------------------------------------------------------------|
| **VM ID (VMID)**         | Auto                  | Unique numeric ID; auto-suggested as the next available                     |
| **VM name**              | —                     | Required. DNS-friendly name for the virtual machine                         |
| **OS type**              | `l26`                 | Proxmox OS type (`l26`, `win11`, `win10`, `other`, etc.)                    |
| **BIOS type**            | `seabios`             | `seabios` (legacy BIOS) or `ovmf` (UEFI; automatically provisions EFI disk) |
| **CPU sockets**          | `1`                   | Number of CPU sockets allocated                                             |
| **CPU cores per socket** | `2`                   | Number of CPU cores per socket                                              |
| **CPU type**             | `host`                | CPU model pass-through or emulation mode                                    |
| **Memory (MB)**          | `2048`                | Dedicated RAM allocation in megabytes                                       |
| **Balloon minimum (MB)** | `0`                   | Minimum memory target for dynamic memory ballooning (`0` to disable)        |
| **Disk size (GB)**       | `32`                  | Storage disk allocation size in gigabytes                                   |
| **SCSI controller**      | `virtio-scsi-single`  | Hardware SCSI controller type (`virtio-scsi-single`, `virtio-scsi-pci`, `lsi`)|
| **Network model**        | `virtio`              | Network adapter device model (`virtio`, `e1000`, etc.)                      |
| **Bridge interface**     | `vmbr0`               | Linux bridge interface to attach network adapter to                        |
| **Firewall**             | `no`                  | Enable or disable Proxmox firewall on virtual network adapter               |
| **Display type**         | `std`                 | VGA graphics display device (`std`, `virtio`, `qxl`, `vmware`, `none`)      |
| **QEMU agent**           | `yes`                 | Enable QEMU Guest Agent support                                             |
| **Boot order**           | `order=ide2;scsi0`    | Configured automatically to boot from CD-ROM (ISO) first, then disk          |
| **Start on boot**        | `no`                  | Whether the VM automatically starts when host boots                         |
| **Start after creation** | `yes`                 | Whether to start the VM immediately after creation completes                |

### Step 4 — Review and Create VM

A full summary is displayed before creating the VM via the QEMU API:

```
┌─────────────────────────────────────────────┐
│            Virtual Machine Summary           │
└─────────────────────────────────────────────┘

  VMID:           107
  Name:           ubuntu-vm-01
  OS type:        l26
  BIOS:           seabios
  ISO:            ubuntu-24.04-live-server-amd64.iso
  Storage:        local-lvm
  CPU:            1 socket(s) × 4 core(s)  [host]
  Memory:         4096 MB
  Balloon:        2048 MB
  Disk:           50 GB
  SCSI:           virtio-scsi-single
  Network:        virtio, bridge=vmbr0
  Firewall:       No
  Display:        std
  QEMU agent:     Yes
  On boot:        Yes
  Start after:    Yes
  Node:           pmx-4

  Proceed with VM creation? (y/n): y

  ⟳ Creating virtual machine …
  ✓ Task started: UPID:pmx-4:00002B3C:...
  ⟳ Waiting for task to complete …
  ✓ VM 107 created successfully!

  ⟳ Starting virtual machine …
  ✓ VM 107 is now running.
```

---

## Clone VM from Template

Select option **[5]** from the main menu to clone a new VM from an existing VM template.

### Step 1 — Select a VM Template

The script lists all QEMU VMs that have been converted to templates on the node:

```
┌─────────────────────────────────────────────┐
│       VM Templates Available to Clone       │
└─────────────────────────────────────────────┘
  [ 1]  📋  VM 9000 — ubuntu-22.04-template
        cpus: 2  |  memory: 2048 MB  |  disk: 32.0 GB
  [ 2]  📋  VM 9001 — debian-12-template
        cpus: 1  |  memory: 1024 MB  |  disk: 16.0 GB

  Select a VM template to clone (or 'q' to cancel): 1

  ✓ Clone source: VM 9000 — ubuntu-22.04-template
```

> **Tip:** To create a VM template, first set up a VM with your desired configuration and OS, then right-click it in the Proxmox UI and select **Convert to Template**.

### Step 2 — Configure Clone Settings

Configure the clone parameters. Defaults are shown in brackets — press Enter to accept them:

```
┌─────────────────────────────────────────────┐
│        VM Clone Configuration               │
└─────────────────────────────────────────────┘

  Source template: VM 9000 — ubuntu-22.04-template

  New VM ID (VMID) [200]:
  New VM name [ubuntu-22.04-template-clone]: ubuntu-clone-01

  ── Clone Type ──
  Full clone:   Independent copy (uses more disk space, slower)
  Linked clone: Shares base image with template (faster, less disk)
                Note: Template cannot be deleted while linked
                clones exist.

  Clone type (full/linked) [full]:

  ── Target Storage for Clone ──
  Use a different storage for the clone? (y/n) [n]:

  ── Disk Format ──
  [1]  qcow2 (QEMU copy-on-write, supports snapshots)
  [2]  raw   (raw disk image, best performance)
  [3]  vmdk  (VMware compatible)
  [4]  (same as source)

  Disk format [4]: 1

  Description (optional) [Clone of VM 9000 (ubuntu-22.04-template)]:
  Start VM after cloning? (y/n) [n]: y
```

#### Clone Configuration Options Reference (VM)

| Setting                  | Default                      | Description                                                  |
|--------------------------|------------------------------|--------------------------------------------------------------|
| **New VM ID (VMID)**     | Auto                         | Unique numeric ID for the cloned VM                          |
| **New VM name**          | `<source_name>-clone`        | Name assigned to the clone                                   |
| **Clone type**           | `full`                       | `full` (independent copy) or `linked` (shares base image)    |
| **Target storage**       | Source storage               | Target storage pool (optional, for full clones only)         |
| **Disk format**          | Same as source               | `qcow2`, `raw`, or `vmdk` (full clones only)                 |
| **Description**          | `Clone of VM <vmid> (<name>)` | Optional description/notes for the cloned VM                |
| **Start after cloning**  | `no`                         | Whether to start the VM immediately after cloning            |

### Step 3 — Review and Execute Clone

A summary is displayed before executing the clone operation:

```
┌─────────────────────────────────────────────┐
│           VM Clone Summary                   │
└─────────────────────────────────────────────┘

  Source:         VM 9000 — ubuntu-22.04-template
  New VMID:       200
  New name:       ubuntu-clone-01
  Clone type:     Full
  Target storage: (same as source)
  Disk format:    qcow2
  Description:    Clone of VM 9000 (ubuntu-22.04-template)
  Start after:    Yes
  Node:           pmx-4

  Proceed with VM clone? (y/n): y

  ⟳ Cloning virtual machine …
  ✓ Task started: UPID:pmx-4:00003C4D:...
  ⟳ Waiting for clone to complete …
  ✓ VM 200 cloned successfully!

  ⟳ Starting cloned virtual machine …
  ✓ VM 200 is now running.
```

---

## Delete VMs

Select option **[6]** from the main menu to enter the QEMU VM deletion workflow.

### Step 1 — VM List

All QEMU virtual machines on the node are listed with their status:

```
┌─────────────────────────────────────────────┐
│          QEMU VMs on This Node              │
└─────────────────────────────────────────────┘
  [ 1]  🟢  VM 107 — ubuntu-vm-01
        status: running  |  cpus: 4  |  memory: 4096 MB
  [ 2]  ⚫  VM 108 — win11-test
        status: stopped  |  cpus: 2  |  memory: 8192 MB

  Enter VM numbers to delete (comma-separated),
  or 'all' to select all, or 'q' to cancel.

  Selection: 1
```

### Step 2 — Selection

You can select VMs in several ways:

| Input       | Effect                              |
|-------------|-------------------------------------|
| `1`         | Select VM #1 only                   |
| `1,2`       | Select VMs #1 and #2                |
| `all`       | Select all listed VMs               |
| `q`         | Cancel and return to the menu       |

### Step 3 — Confirmation

A summary of selected VMs is shown. Running VMs are noted:

```
┌─────────────────────────────────────────────┐
│            VMs Marked for Deletion           │
└─────────────────────────────────────────────┘
  • VM 107 — ubuntu-vm-01  (running)

  ⚠  1 VM(s) are currently running and will be stopped first.

  ⚠  This action is IRREVERSIBLE. Proceed with deletion? (yes/no): yes
```

> **Safety:** You must type the full word `yes` to proceed — `y` alone will abort. This prevents accidental deletion.

### Step 4 — Deletion

The script stops running VMs, then deletes each one via the QEMU API:

```
  ── VM 107 (ubuntu-vm-01) ──
  ⟳ Stopping VM 107 …
  ✓ VM 107 stopped.
  ⟳ Deleting VM 107 …
  ✓ VM 107 deleted successfully!
```

If a VM fails to stop, it is skipped and an error is shown.

---

## Creating and Deleting in One Session

The script returns to the main menu after each operation, so you can perform multiple container and VM actions without restarting:

```bash
python create_lxc.py
# → Select [1] to create an LXC container from a template
# → Select [2] to clone an existing LXC container
# → Select [3] to delete LXC container(s)
# → Select [4] to create a QEMU VM from an ISO
# → Select [5] to clone a VM from a VM template
# → Select [6] to delete VM(s)
# → Select [7] to exit
```
