# VPN setup and kill switch

Optional. Hydra works without a VPN, but if you want your real IP protected, this is how.

[Back to the README](../README.md)

## How the kill switch works

The kill switch turns itself on the first time Hydra sees a VPN. You can see and change it in Settings, under "Require VPN to download". If you switch it off, it stays off.

With it on, Hydra binds all torrent traffic directly to your VPN interface, so your real IP is not used to download, upload, or connect to a peer or tracker. If the VPN drops, Hydra moves off the real network at once (tested against a real VPN drop: within a second it had no sockets on any real interface), every download pauses, and everything resumes automatically on the new VPN address when the VPN comes back.

With it off, a VPN drop makes Hydra fall back to your normal connection and keep downloading on your real IP. That is why it is on by default once a VPN has been seen.

With no VPN at all the switch stays off, so Hydra still works. The header shows a red "EXPOSED (No VPN)" warning until you connect one.
## Setting up your VPN

Install your VPN on the same machine Hydra runs on. That's it - Hydra finds it automatically.

It works with WireGuard, OpenVPN, NordVPN, Mullvad, ProtonVPN, ExpressVPN, PIA, Cisco AnyConnect, or anything that creates a `tun`/`tap`/`vpn` network adapter. All torrent traffic binds to the VPN interface. If the VPN drops, every torrent pauses instantly. When it reconnects, they resume.

No configuration needed in Hydra. Just get your VPN running and Hydra handles the rest.

### If Hydra picks the wrong adapter

Hydra recognises a VPN by its adapter name, so a tunnel that only reaches your home network (for example a WireGuard link to your LAN) can be mistaken for your internet VPN. Torrents would then bind to it and find no peers. Tell Hydra to ignore it by adding the adapter name (or part of it) to `hydra_config.json`:

```json
"vpn_ignore_interfaces": ["home", "lan-vpn"]
```

Restart Hydra afterwards. **Tailscale** is not treated as a VPN, because without an exit node it has no route to the internet. If you do use a Tailscale exit node for torrents, bind Hydra through a VPN that does route to the internet instead.

Most VPN providers give you either a WireGuard or OpenVPN config file. Set it up on the machine where Hydra runs and Hydra handles the rest.

### PIA (Private Internet Access)

PIA's WireGuard tokens expire every 24 hours, so you need their official script to auto-reconnect:

```bash
apt install wireguard
git clone https://github.com/pia-foss/manual-connections.git
cd manual-connections
sudo PIA_USER=p1234567 PIA_PASS=your_password \
     VPN_PROTOCOL=wireguard AUTOCONNECT=true \
     PIA_PF=false PIA_DNS=true DISABLE_IPV6=yes \
     DIP_TOKEN=no ./run_setup.sh
```

To keep it running permanently, set up [pia-autoconnect-wireguard](https://github.com/j00w33/pia-autoconnect-wireguard) - it refreshes the token daily via a systemd service + cron job.

### Mullvad / ProtonVPN / NordVPN / Surfshark

These providers give you a static WireGuard config file that doesn't expire. Download it from your provider's website and:

```bash
apt install wireguard
# Save your provider's config as /etc/wireguard/wg0.conf
wg-quick up wg0
systemctl enable wg-quick@wg0   # auto-start on boot
```

### OpenVPN (works with any provider)

```bash
apt install openvpn
# Download your .ovpn file from your VPN provider
cp your-config.ovpn /etc/openvpn/client.conf
systemctl start openvpn@client
systemctl enable openvpn@client   # auto-start on boot
```

Hydra detects the VPN within seconds - check the badge in the top right of the UI.
