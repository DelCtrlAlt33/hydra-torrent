# Recommended setup (VPN-protected Hydra and Jackett)

The full picture: Hydra, search, and VPN protection together, with no gaps.

[Back to the README](../README.md)

The complete picture, for anyone who wants everything - Hydra, search, and VPN protection - running together, VPN-protected end to end with no gaps.

**The goal:** not just "downloads go through a VPN" (that part's automatic - see [VPN Kill Switch](vpn.md#vpn-kill-switch)), but that Jackett's *searches* are protected too. Jackett makes real outbound requests to indexer sites for every search - if Jackett itself isn't behind the VPN, those requests (and the domains they reveal) go out over your normal connection even while your downloads are fully protected. Easy to miss, since everything still works either way.

The reliable way to guarantee this - not just for Jackett, but for anything added later - is a Docker network mode where a container has *no other path to the internet at all*, rather than configuring each app individually to remember to use the VPN:

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      - VPN_SERVICE_PROVIDER=mullvad   # or protonvpn, nordvpn, surfshark, private internet access, etc.
      - VPN_TYPE=wireguard
      - WIREGUARD_PRIVATE_KEY=your_key_here
      - WIREGUARD_ADDRESSES=10.x.x.x/32
    ports:
      - "9117:9117"   # Jackett's web UI, exposed through gluetun
    restart: unless-stopped

  jackett:
    image: lscr.io/linuxserver/jackett:latest
    network_mode: "service:gluetun"   # no network of its own - everything routes through gluetun
    depends_on:
      - gluetun
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - ./jackett-config:/config
    restart: unless-stopped

  hydra:
    build: .
    volumes:
      - ./data:/data
      - /path/to/movies:/movies
      - /path/to/tv:/tv
    ports:
      - "8765:8765"
    restart: unless-stopped
```

In Hydra's Settings, point the Jackett URL at `http://<docker-host-ip>:9117` (Jackett's UI is exposed through gluetun's port mapping, since it has no ports of its own to publish).

Full list of [providers gluetun supports natively](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers) - most just need an API key or WireGuard config from your provider's dashboard, no extra steps.

**PIA (Private Internet Access) is the one common exception** - gluetun doesn't support it natively in WireGuard mode, only OpenVPN. If OpenVPN mode doesn't authenticate cleanly with your PIA credentials (the account login you use for PIA's app/website is *not* the same credential as VPN protocol auth, and PIA's dashboard doesn't expose the protocol-specific one directly), the reliable fallback is generating a WireGuard config yourself with PIA's own official tool:

```bash
git clone https://github.com/pia-foss/manual-connections.git
cd manual-connections
sudo PIA_USER=p1234567 PIA_PASS=your_password ./get_token.sh
# note the PIA_TOKEN it prints, then pick a region:
PIA_TOKEN=<token from above> PREFERRED_REGION=<region id> ./get_region.sh
# use the printed WireGuard IP/hostname for that region:
PIA_TOKEN=<token> WG_SERVER_IP=<ip> WG_HOSTNAME=<hostname> PIA_CONNECT=false ./connect_to_wireguard_with_token.sh
```
`PIA_CONNECT=false` is important - it writes a config file instead of bringing up a live interface on your host, which is what you actually want when the destination is gluetun's `WIREGUARD_PRIVATE_KEY`/`WIREGUARD_ADDRESSES`/`WIREGUARD_PUBLIC_KEY`/`VPN_ENDPOINT_IP`/`VPN_ENDPOINT_PORT` env vars (`VPN_SERVICE_PROVIDER=custom` for this mode), not a second tunnel running directly on the machine.

**Running this inside a Proxmox LXC?** See [PROXMOX.md](../PROXMOX.md) for the extra steps (`/dev/net/tun` passthrough, AppArmor, egress firewall ports) needed to get gluetun/Docker networking working inside an LXC specifically.
