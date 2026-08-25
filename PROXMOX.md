# Running Hydra in a Proxmox LXC

Everything here is specific to Proxmox LXC containers and won't show up on a normal Linux box, a VM, or bare metal. If you're not running Proxmox, this file doesn't apply to you.

## Docker inside an LXC needs `nesting=1` enabled first

Proxmox web UI → the LXC → Options → Features (or `pct set <vmid> --features nesting=1`). Without it, Docker either won't start or fails in confusing ways. Only matters if Hydra or Plex/Jellyfin runs via Docker inside the LXC - skip this if everything's installed natively.

## NAS/NFS storage gets bind-mounted through Proxmox itself, not `/etc/fstab` inside the container

Add it as a Mount Point on the LXC's Resources tab (or `pct set <vmid> --mp0 /host/path,mp=/container/path`) rather than mounting NFS from inside the LXC directly - unprivileged LXCs generally can't mount NFS themselves even if you try.

## `/dev/net/tun` usually isn't exposed inside an LXC by default

Needed if you're running gluetun (or anything else that creates its own WireGuard/OpenVPN tunnel) inside a Docker container in the LXC. Add to the container's config (`/etc/pve/lxc/<vmid>.conf`) and reboot the LXC:

```
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

## Docker inside an LXC with AppArmor enabled on the host can fail to start *new* containers

Error looks like `docker-default profile... Permission denied`, even though Docker itself runs fine and existing containers keep working. Add `security_opt: [apparmor:unconfined]` to each service in `docker-compose.yml` - same fix already needed for GPU-passthrough LXCs running Docker (e.g. Frigate, Plex hardware transcoding).

## Per-container egress firewalls

If your Proxmox host runs its own per-container egress firewall (common in a locked-down homelab setup), make sure it allows outbound to whatever port your VPN provider's WireGuard/OpenVPN endpoint actually uses. It's easy to only have the port your *existing* VPN client uses allowed, and silently block a second one on a different port when you add something new (e.g. a second tunnel for Jackett alongside Hydra's own VPN).
