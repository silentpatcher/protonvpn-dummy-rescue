# Proton VPN Linux — NetworkManager Kill Switch Async Fix (`v4.2`)

[![CI](https://github.com/silentpatcher/protonvpn-dummy-rescue/actions/workflows/ci.yml/badge.svg)](https://github.com/silentpatcher/protonvpn-dummy-rescue/actions/workflows/ci.yml)

A surgical, idempotent patch for Proton VPN's Linux NetworkManager backend (`nmclient.py`) that eliminates connection/disconnection hangs, `TimeoutError` exceptions, and PolicyKit deletion race conditions caused by dummy Kill Switch interfaces (`pvpnksintrf0`, `pvpnksintrf1`, `ipv6leakintrf0`).

---

## Symptoms & Log Errors

This patch fixes hangs and crashes on Linux when connecting, disconnecting, or toggling the Kill Switch (`Standard` / `Advanced`) in Proton VPN. Common symptoms and journalctl / Proton VPN log errors include:

- **Proton VPN hangs indefinitely on "Connecting..." or "Disconnecting..."**
- **Kill Switch dummy interfaces stuck in `UNMANAGED` state:** `pvpnksintrf0`, `pvpnksintrf1`, `ipv6leakintrf0`
- `TimeoutError` / `asyncio.exceptions.TimeoutError` inside `_wrap_future` during `add_connection_async` or `remove_connection_async`
- `GLib.GError: nm-settings-error-quark: connection was deleted`
- `RuntimeError: Error adding kill switch connection` or `Error removing kill switch connection`

## Quick Start

**1. Completely quit Proton VPN** (including the system tray icon), then run:

```bash
curl -fsSL https://raw.githubusercontent.com/silentpatcher/protonvpn-dummy-rescue/main/patch_proton_nm.py -o patch_proton_nm.py
python3 patch_proton_nm.py --dry-run
sudo python3 patch_proton_nm.py
```

## Compatibility & Tested Environments

- **Directly tested and verified on:** **Linux Mint 22.2** (Ubuntu 24.04 LTS base) with `proton-vpn-gtk-app` **4.19.0** (`proton-vpn-api-core` 6.1.1, `proton-vpn-network-manager` 0.13.5, `proton-vpn-killswitch-network-manager` 0.6.1, `proton-vpn-killswitch-network-manager-wireguard` 0.2.1).
- **Other Linux distributions:** Because the underlying race conditions live inside Proton's upstream Python NetworkManager wrapper (`nmclient.py`), this fix **should also work out-of-the-box on any other distribution** running official Proton VPN Linux packages (`4.13.x` – `4.19.x`+), including **Ubuntu 24.04+**, **Debian 12+**, **Pop!_OS**, **Zorin OS**, and **Fedora** (the script automatically scans both `/usr/lib/python3/dist-packages` and `/usr/lib/python3.*/site-packages`).

---

## Root Cause Analysis

Proton VPN implements its Linux Kill Switch by dynamically creating and removing dummy NetworkManager connections (`pvpn-killswitch`, `pvpn-killswitch-perm`, `pvpn-killswitch-ipv6`). In `nmclient.py`, three asynchronous GObject/D-Bus issues occur on modern kernels (`6.8+` / `6.14+`) and NetworkManager builds:

1. **Disconnection hang (`device-removed` never emitted)**
   `remove_connection_async()` waits for NetworkManager's `device-removed` GObject signal before resolving `future_interface_removed`. For `dummy` interfaces (especially `UNMANAGED` ones), the kernel/NetworkManager often removes the connection profile without emitting `device-removed`, causing `disconnect()` to stall until the 5-second `_wrap_future` timeout expires.
2. **Connection hang (`device-added` / `state-changed` missed or skipped)**
   `add_connection_async()` waits for `device-added` followed by `NM.DeviceState.ACTIVATED`. If the dummy device already exists or transitions immediately to `UNMANAGED`/`ACTIVATED` before the signal handler attaches, `future_conn_activated` never resolves and connection setup times out.
3. **PolicyKit deletion race (`"connection was deleted"`)**
   When switching Kill Switch modes (Standard $\leftrightarrow$ Advanced $\leftrightarrow$ Off) or cleaning up profiles, `connection.delete_finish(result)` can raise `GLib.GError: ... connection was deleted` if NetworkManager already finalized the deletion asynchronously. Treating this benign race condition as a fatal error aborts the state machine.

### The Fix

- Resolve `future_conn_activated` deterministically inside `_on_connection_added` once `nm_client.add_connection_finish(res)` succeeds.
- Resolve `future_interface_removed` deterministically inside `_on_connection_removed` once `connection.delete_finish(result)` succeeds.
- Treat `"connection was deleted"` inside `_on_connection_removed` as a successful idempotent removal rather than an unhandled exception.
- Automatically discover all `nmclient.py` targets across both `4.19+` (`proton/vpn/backend/networkmanager/killswitch/...`) and `4.13–4.18` (`proton/vpn/backend/linux/networkmanager/killswitch/...`, `proton/vpn/killswitch/backend/linux/...`) directory layouts on both Debian-based and RPM-based systems.

---

## Known Limitation: OpenVPN + Advanced (Permanent) Kill Switch

While **WireGuard** works reliably with Kill Switch **Off**, **Standard**, and **Advanced (Permanent)**, **OpenVPN (UDP and TCP)** currently fails with `Connection timed out` (`VPN server NOT reachable`) when **Advanced (Permanent) Kill Switch** is enabled.

This is a separate upstream routing bug in `proton/vpn/backend/networkmanager/core/networkmanager.py` (around line 81), outside `nmclient.py`:
- When Advanced Kill Switch (`pvpn-killswitch-perm`) is active, all outbound traffic is routed into the dummy sink interface (`pvpnksintrf1`).
- Before starting OpenVPN, `NetworkManagerConnection.start()` runs `tcpcheck.is_any_port_reachable(server_ip, openvpn_ports.tcp)` **before** punching a `/32` host route (`add_routed_killswitch_connection`) for the VPN server IP.
- Because `pvpnksintrf1` drops the TCP probe packets, `is_any_port_reachable()` returns `False` (`VPN server NOT reachable`) and aborts with `events.Timeout`.
- **Workaround:** Use **WireGuard** when **Advanced Kill Switch** is enabled, or use **Standard Kill Switch** with OpenVPN.

---

## Usage

### 1. Preview changes (Dry Run)
```bash
python3 patch_proton_nm.py --dry-run
```

### 2. Apply the patch
**Completely quit Proton VPN** (including the system tray icon) before running:
```bash
sudo python3 patch_proton_nm.py
```
The script automatically creates `.py.bak` backups of every modified file and recompiles Python bytecode (`py_compile`).

### 3. Restore original files
**Completely quit Proton VPN**, then run:
```bash
sudo python3 patch_proton_nm.py --restore
```

### Note on Package Updates (`apt` / `dnf` upgrade)
Because this patch modifies system files under `/usr/lib/python3/.../proton/vpn/`, any package manager upgrade of Proton VPN packages will overwrite them with upstream files. Re-run `sudo python3 patch_proton_nm.py` after updating until Proton merges an official fix.

On **Debian / Ubuntu / Linux Mint**, you can temporarily hold Proton VPN packages to prevent automatic updates from overwriting the patch:

```bash
sudo apt-mark hold proton-vpn-gtk-app python3-proton-vpn-api-core python3-proton-vpn-network-manager python3-proton-vpn-killswitch-network-manager python3-proton-vpn-killswitch-network-manager-wireguard
```

To re-enable updates later:

```bash
sudo apt-mark unhold proton-vpn-gtk-app python3-proton-vpn-api-core python3-proton-vpn-network-manager python3-proton-vpn-killswitch-network-manager python3-proton-vpn-killswitch-network-manager-wireguard
```

## License

GPL-3.0-or-later (matching upstream Proton VPN Linux packages).

