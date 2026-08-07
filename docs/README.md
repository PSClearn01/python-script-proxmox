# Proxmox LXC Creator

An interactive Python script that creates new LXC containers on a Proxmox VE node from available container templates. The script connects to the Proxmox API, discovers templates on the target node (`pmx-4`), and walks you through configuring and deploying a new container.

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

- **Template discovery** — automatically scans all storage pools on the target node for available LXC templates (`vztmpl`)
- **Interactive menus** — numbered selection lists for templates and storage pools
- **Full container configuration** — prompts for VMID, hostname, root password, CPU, memory, swap, disk size, networking, and more
- **DHCP or static IP** — supports both automatic and manual network configuration with bridge and gateway options
- **Confirmation summary** — displays all settings for review before creating anything
- **Auto-start** — optionally starts the container immediately after creation
- **Secure credentials** — API token and host stored in a `.env` file, kept out of version control
- **Smart defaults** — auto-suggests the next available VMID, sensible resource defaults

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
| `PROXMOX_NODE`         | ❌       | `pmx-4`  | Target Proxmox node name where containers will be created                   |

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
├── create_lxc.py          # Main script
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
