# Troubleshooting

Common issues and their solutions when using the Proxmox VM & LXC Manager script.

---

## Connection Issues

### ✗ Missing required .env variables

```
✗ Missing required .env variables: PROXMOX_HOST, PROXMOX_TOKEN_ID, PROXMOX_TOKEN_SECRET
  Copy .env.example → .env and fill in your values.
```

**Cause:** The `.env` file is missing or incomplete.

**Solution:**
```bash
cp .env.example .env
nano .env   # fill in all values
```

---

### ✗ Failed to connect to Proxmox

```
✗ Failed to connect to Proxmox at 192.168.1.50:8006: <error details>
```

**Possible causes and solutions:**

| Cause | Solution |
|-------|----------|
| Host unreachable | Verify the IP/hostname and that port 8006 is open: `curl -sk https://192.168.1.50:8006/api2/json/version` |
| DNS resolution failure | Use an IP address instead of a hostname in `PROXMOX_HOST` |
| Firewall blocking | Check firewall rules on both the client and Proxmox host |
| Wrong port | Ensure you're using port `8006` (default PVE API port) |
| Invalid API token | Verify `PROXMOX_TOKEN_ID` format is `user@realm!tokenname` |
| Expired token | Check if the token has an expiry date in the Proxmox UI |
| Wrong token secret | Recreate the token if the secret was lost (it's only shown once) |

---

### SSL Certificate Errors

The script disables SSL verification (`verify_ssl=False`) by default to work with self-signed certificates. If you see SSL-related errors despite this:

1. Ensure your `proxmoxer` version is up to date: `pip install --upgrade proxmoxer`
2. Check that `requests` is installed (it's the HTTP backend): `pip install requests`

---

## Template Issues

### ✗ No LXC templates found on this node

```
✗ No LXC templates found on this node.
  Upload a template via the Proxmox UI or `pveam`.
```

**Cause:** There are no container templates on any storage pool of the target node.

**Solution:** Download templates using one of these methods:

```bash
# On the Proxmox host:
pveam update
pveam available --section system
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
```

Or use the Proxmox UI: navigate to **Node** → **local** → **CT Templates** → **Templates** → **Download**.

See the [Usage Guide — Downloading Templates](usage.md#downloading-templates) for more details.

---

### Template is on a different node

The script only lists templates on the configured `PROXMOX_NODE` (default: `pmx-4`). Templates on other nodes won't appear.

**Solution:** Either:
- Download the template on `pmx-4`
- Change `PROXMOX_NODE` in `.env` to the node where your templates exist

---

## Container Creation Issues

### ✗ Failed to create container: 500 ...

**Common causes:**

| Error message contains | Cause | Solution |
|------------------------|-------|----------|
| `VMID already in use` | The selected VMID is taken | Choose a different VMID |
| `storage ... does not exist` | Invalid storage pool | Select a valid storage from the list |
| `permission denied` | Insufficient token permissions | See [API Token Setup — Required Permissions](api-token-setup.md#required-permissions) |
| `unable to create CT` | General creation failure | Check the Proxmox task log for details |

---

### Task exited with non-OK status

```
✗ Task exited with status: <status>
```

**Solution:** Check the full task log in the Proxmox UI:
1. Navigate to **pmx-4** → **Task Log** (or **Cluster** → **Tasks**)
2. Find the failed task and click on it to view the full output

---

### ⚠ Could not verify task completion

```
⚠ Could not verify task completion: Task UPID:... did not complete within 120s
```

**Cause:** The container creation task took longer than the 120-second timeout.

**Solution:** This is usually fine — the container is likely still being created. Check the Proxmox UI task log for status. The timeout does not cancel the task.

---

### ⚠ Failed to start container

```
⚠ Failed to start container: <error>
```

**Possible causes:**

| Cause | Solution |
|-------|----------|
| IP conflict | Ensure the static IP isn't already in use on the network |
| Missing bridge | Verify the bridge interface (e.g. `vmbr0`) exists on the node |
| Resource limits | Check that the node has enough free memory and CPU |
| Lock file | Another operation may be in progress; wait and try again |

You can start the container manually:
```bash
# From the Proxmox host
pct start <VMID>
```

---

## Container Deletion Issues

### ✗ Failed to stop container

```
✗ Failed to stop container <VMID>: <error>
  Skipping deletion of <VMID>.
```

**Possible causes:**

| Cause | Solution |
|-------|----------|
| Container is locked | Another operation (backup, snapshot) may be running. Wait for it to finish or remove the lock: `pct unlock <VMID>` |
| Permission denied | Ensure the API token has `VM.PowerMgmt` permission. See [API Token Setup](api-token-setup.md#required-permissions) |
| Container in an error state | Check the Proxmox UI for details. You may need to force-stop: `pct stop <VMID> --force` |

---

### ✗ Failed to delete container

```
✗ Failed to delete container <VMID>: <error>
```

**Possible causes:**

| Cause | Solution |
|-------|----------|
| Container still running | The script should auto-stop before deleting. If it failed to stop, stop it manually first: `pct stop <VMID>` |
| Container is locked | Remove the lock: `pct unlock <VMID>`, then retry |
| Permission denied | Ensure the API token has `VM.Allocate` permission for deletion |
| Snapshots exist | Some storage backends require removing snapshots first. Delete snapshots via the Proxmox UI or `pct delsnapshot <VMID> <snapname>` |

---

### No containers found on this node

```
(no containers found on this node)
```

**Cause:** There are no LXC containers on the configured `PROXMOX_NODE`.

**Solution:** Verify you're targeting the correct node in your `.env` file. Containers on other nodes won't appear.

---

## Network Configuration Issues

### Container has no network connectivity

If the container was created but can't reach the network:

1. **Check the bridge exists** on the Proxmox host:
   ```bash
   ip link show vmbr0
   ```

2. **Verify IP configuration** inside the container:
   ```bash
   pct enter <VMID>
   ip addr show eth0
   ip route
   ```

3. **For DHCP:** Ensure a DHCP server is available on the bridge's network segment

4. **For static IPs:** Verify the IP and gateway are on the same subnet:
   ```
   ✓  IP: 192.168.1.100/24  Gateway: 192.168.1.1     (same /24 subnet)
   ✗  IP: 192.168.1.100/24  Gateway: 10.0.0.1         (different subnet)
   ```

---

## Python / Dependency Issues

### ModuleNotFoundError: No module named 'proxmoxer'

```bash
pip install -r requirements.txt
```

If using a virtual environment, ensure it's activated:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

---

### proxmoxer version compatibility

If you encounter unexpected API errors, ensure you're using a recent version:

```bash
pip install --upgrade proxmoxer requests
```

The script has been tested with `proxmoxer >= 2.0`.

---

## VM Cloning Issues

### No VM templates found

```
(no VM templates found on this node)
```

**Cause:** No QEMU VMs have been converted to templates on the configured `PROXMOX_NODE`.

**Solution:**
1. Create and configure a VM with your desired OS and settings
2. Right-click the VM in the Proxmox UI → **Convert to Template**
3. The VM will now appear in the template list when using option `[5]`

---

### ✗ Failed to clone VM: 403 Forbidden

**Cause:** Insufficient API token permissions.

**Solution:** The token needs these permissions:

| Permission | Required for |
|-----------|-------------|
| `VM.Clone` | Cloning the VM template |
| `VM.Allocate` | Creating a new VM from the clone |
| `Datastore.AllocateSpace` | Allocating disk space (also needed on target storage if different) |

See [API Token Setup](api-token-setup.md#required-permissions) for details.

---

### Linked clone fails

**Cause:** The source template's storage backend doesn't support the snapshot mechanism required for linked clones.

**Solution:** Use a full clone instead, or ensure the template is stored on a backend that supports snapshots:
- ZFS
- LVM-thin
- Ceph (RBD)
- qcow2 on directory/NFS storage

---

### Clone takes a very long time or times out

```
⚠ Could not verify task completion: Task UPID:... did not complete within 600s
```

**Cause:** Full clones copy the entire disk image, which can take significant time for large VMs.

**Solution:** The clone task is likely still running on the Proxmox server — the timeout only affects the script's polling, not the server-side task. Check progress in the Proxmox UI (Node → Tasks). If speed is important, consider using linked clones instead.

---

### Template cannot be deleted

**Cause:** Linked clones depend on the template's base image. Proxmox prevents deletion of templates that have active linked clones.

**Solution:** Either:
- Delete all linked clones first, then delete the template
- Use full clones instead, which are independent of the template

---

## Getting Help

1. **Proxmox task logs:** Most creation issues are detailed in the task log (Proxmox UI → Tasks)
2. **Proxmox API docs:** https://pve.proxmox.com/pve-docs/api-viewer/
3. **proxmoxer docs:** https://github.com/proxmoxer/proxmoxer
