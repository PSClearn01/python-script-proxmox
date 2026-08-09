# Proxmox VM & LXC Manager

An interactive Python script that creates, clones, and deletes LXC containers and QEMU virtual machines on a Proxmox VE node. The script connects to the Proxmox API, discovers templates, ISO images, existing containers, and virtual machines on the target node (`pmx-4`), and walks you through configuring, deploying, cloning, or deleting resources.

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Usage Guide](usage.md)
- [API Token Setup](api-token-setup.md)
- [Troubleshooting](troubleshooting.md)

---

## Features

- **LXC template discovery & upload** — automatically scans storage pools for available LXC templates (`vztmpl`) with support for uploading local template files (`.tar.gz`, `.tar.xz`, `.tar.zst`)
- **LXC container cloning** — clone existing LXC containers with support for full (independent copy) or linked (shared base) clones, custom hostnames, target storage selection, and descriptions
- **VM creation from ISO** — create QEMU virtual machines from ISO images stored on Proxmox storage pools
- **ISO upload support** — upload local ISO images (`.iso`, `.img`) directly to eligible Proxmox storage pools via multipart REST API
- **OS type selection** — target OS profiles including Linux (2.6+, 2.4), Windows variants (Win 11 / Server 2025, Win 10 / 2016–2022, Win 8, Win 7, Win XP), Solaris, and Other
- **BIOS selection** — choose between SeaBIOS (legacy BIOS) and OVMF (UEFI with automatic EFI disk provisioning)
- **SCSI controller selection** — configure disk controllers with support for VirtIO SCSI Single, VirtIO SCSI, or LSI 53C895A
- **QEMU Guest Agent support** — toggle QEMU Guest Agent enablement during VM provisioning
- **Full container & VM configuration** — interactively set VMID, hostname/name, CPU (cores/sockets), memory, swap/ballooning, disk size, display adapter, and networking
- **DHCP or static IP for LXCs** — supports automatic and manual network configuration with bridge and gateway options
- **Confirmation summary** — displays all settings for review before creating, cloning, or deleting resources
- **Auto-start** — optionally start containers or VMs immediately after creation or cloning
- **Container & VM deletion** — list LXC containers and QEMU VMs with multi-select deletion (individual numbers, `all`, or `q` to cancel)
- **Safe deletion workflow** — automatically stops running containers and VMs before deletion, guarded by a mandatory `yes` confirmation prompt
- **Expanded main menu** — interactive main menu with 6 structured options to manage containers and VMs in a single session without restarting the script:
  1. **Create LXC from template**
  2. **Clone an existing LXC container**
  3. **Delete LXC container(s)**
  4. **Create VM from ISO**
  5. **Delete VM(s)**
  6. **Exit**
- **Secure credentials** — API token and host stored in a `.env` file, kept out of version control
- **Smart defaults** — auto-suggests next available VMID and sensible resource defaults

---

## Prerequisites

| Requirement            | Minimum Version |
|------------------------|-----------------|
| Python                 | 3.8+            |
| Proxmox VE             | 6.0+            |
| Proxmox API Token      | —               |
| Network access to PVE  | Port 8006       |

The following Python packages are required (installed via `requirements.txt`):

| Package          | Purpose                             |
|------------------|-------------------------------------|
| `proxmoxer`      | Proxmox API client library          |
| `requests`       | HTTP backend for proxmoxer          |
| `python-dotenv`  | Loads `.env` file into environment   |

---

## Quick Start

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd python-script-proxmox

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
nano .env   # fill in your Proxmox host, API token ID, and secret

# 4. Run the script
python create_lxc.py
```

---

## Configuration

All configuration is done via the `.env` file in the project root. Copy the provided example and fill in your values:

```bash
cp .env.example .env
```

### Environment Variables

| Variable               | Required | Default  | Description                                                                 |
|------------------------|----------|----------|-----------------------------------------------------------------------------|
| `PROXMOX_HOST`         | ✅       | —        | Proxmox host URL (e.g. `https://192.168.1.100:8006`)                        |
| `PROXMOX_TOKEN_ID`     | ✅       | —        | API Token ID in `user@realm!tokenname` format                               |
| `PROXMOX_TOKEN_SECRET` | ✅       | —        | API Token secret value                                                      |
| `PROXMOX_NODE`         | ❌       | `pmx-4`  | Target Proxmox node name where containers and VMs will be managed           |

### Example `.env`

```dotenv
PROXMOX_HOST=https://192.168.1.50:8006
PROXMOX_TOKEN_ID=root@pam!lxc-creator
PROXMOX_TOKEN_SECRET=aabbccdd-1122-3344-5566-778899aabbcc
PROXMOX_NODE=pmx-4
```

> **Security note:** The `.gitignore` is configured to exclude `.env` from version control. Never commit your API credentials.

---

## Project Structure

```
python-script-proxmox/
├── create_lxc.py          # Main script — create, clone, & delete LXC containers and QEMU VMs
├── requirements.txt       # Python dependencies
├── .env.example           # Credential template (safe to commit)
├── .env                   # Your actual credentials (git-ignored)
├── .gitignore             # Excludes .env and Python artifacts
└── docs/
    ├── README.md           # This file
    ├── usage.md            # Step-by-step usage walkthrough
    ├── api-token-setup.md  # How to create a Proxmox API token
    └── troubleshooting.md  # Common issues and solutions
```

---

## License

Internal use. Modify and distribute as needed within your organisation.
