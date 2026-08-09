# Proxmox API Token Setup

This guide explains how to create an API token in Proxmox VE for use with the LXC manager script.

---

## Why API Tokens?

API tokens are the recommended way to authenticate with the Proxmox API because:

- **No session management** — tokens don't expire like ticket-based auth
- **Granular permissions** — tokens can be scoped to specific privileges
- **Revocable** — individual tokens can be revoked without changing user passwords
- **Auditable** — token usage is logged separately from interactive logins

---

## Creating an API Token

### Step 1 — Open the Proxmox Web UI

Navigate to your Proxmox host in a browser:

```
https://<your-proxmox-host>:8006
```

### Step 2 — Navigate to API Tokens

1. In the left sidebar, expand **Datacenter**
2. Click **Permissions** → **API Tokens**
3. Click **Add**

### Step 3 — Configure the Token

| Field            | Value                    | Notes                                                            |
|------------------|--------------------------|------------------------------------------------------------------|
| **User**         | `root@pam`               | Or any user with sufficient privileges                           |
| **Token ID**     | `lxc-manager`            | A descriptive name for this token                                |
| **Privilege Separation** | ☐ Unchecked      | When unchecked, the token inherits all of the user's privileges  |
| **Expire**       | *(leave blank)*          | Optional — set an expiry date if desired                         |
| **Comment**      | `LXC manager script`     | Optional                                                         |

> **Important:** If you check "Privilege Separation", you'll need to explicitly assign permissions to the token. For simplicity, leave it unchecked to inherit the user's permissions.

### Step 4 — Save the Token Secret

After clicking **Add**, Proxmox displays the token secret **once**. Copy it immediately.

```
Token ID:     root@pam!lxc-manager
Token Secret: aabbccdd-1122-3344-5566-778899aabbcc
```

> ⚠️ **You cannot retrieve the secret later.** If you lose it, you must delete and recreate the token.

### Step 5 — Add to `.env`

Open your `.env` file and fill in the values:

```dotenv
PROXMOX_HOST=https://192.168.1.50:8006
PROXMOX_TOKEN_ID=root@pam!lxc-manager
PROXMOX_TOKEN_SECRET=aabbccdd-1122-3344-5566-778899aabbcc
PROXMOX_NODE=pmx-4
```

---

## Alternative: CLI Token Creation

You can also create tokens from the Proxmox host command line:

```bash
# Create the token (returns the secret)
pveum user token add root@pam lxc-manager --privsep 0

# Verify it was created
pveum user token list root@pam
```

The output will contain the token secret — copy it to your `.env`.

---

## Required Permissions

If using privilege separation (or a non-root user), the token needs the following permissions on the target node:

| Permission             | Path                     | Purpose                           |
|------------------------|--------------------------|-----------------------------------|
| `VM.Allocate`          | `/vms`                   | Create and delete containers      |
| `VM.Config.Disk`       | `/vms`                   | Configure container storage       |
| `VM.Config.CPU`        | `/vms`                   | Set CPU allocation                |
| `VM.Config.Memory`     | `/vms`                   | Set memory allocation             |
| `VM.Config.Network`    | `/vms`                   | Configure networking              |
| `VM.Config.Options`    | `/vms`                   | Set general options (onboot etc.) |
| `VM.PowerMgmt`         | `/vms`                   | Start/stop containers             |
| `Datastore.AllocateSpace` | `/storage/<name>`     | Allocate disk on storage          |
| `Datastore.Audit`      | `/storage`               | List storage and templates        |
| `Sys.Audit`            | `/nodes/<node>`          | Query node and task status        |

### Applying Permissions via CLI

```bash
# Example: grant all needed permissions to a token with privilege separation
pveum aclmod /vms -token 'root@pam!lxc-manager' -role PVEVMAdmin
pveum aclmod /storage -token 'root@pam!lxc-manager' -role PVEDatastoreUser
pveum aclmod /nodes/pmx-4 -token 'root@pam!lxc-manager' -role PVEAuditor
```

---

## Verifying the Token

Test the token from the command line to verify it works:

```bash
curl -s -k \
  -H "Authorization: PVEAPIToken=root@pam!lxc-manager=aabbccdd-1122-3344-5566-778899aabbcc" \
  https://192.168.1.50:8006/api2/json/version | python3 -m json.tool
```

Expected output:

```json
{
    "data": {
        "version": "8.2",
        "release": "2",
        ...
    }
}
```

---

## Security Best Practices

1. **Use a dedicated user** — avoid using `root@pam` in production; create a dedicated user with only the required permissions
2. **Enable privilege separation** — scope the token to only the permissions it needs
3. **Set an expiry** — rotate tokens periodically
4. **Never commit `.env`** — the `.gitignore` already excludes it, but double-check before pushing
5. **Use file permissions** — restrict `.env` to the script user only:
   ```bash
   chmod 600 .env
   ```
