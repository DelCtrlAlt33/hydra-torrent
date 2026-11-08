# AGENTS.md: installing and verifying Hydra Torrent

Instructions for AI coding agents setting up Hydra Torrent for a user. Humans: see [README.md](README.md).

## Ground rules

- Do not invent secrets, tokens, API keys or paths. If you need one, ask the user.
- Do not report success because a process started. Run the checklist below and report each item as **pass**, **fail** or **could not test**, with the command output as evidence.
- Do not change the user's VPN, firewall, NAS or media server configuration without asking first.
- Do not turn off "Require VPN to download" to make something work.

## 1. Choose an install method

| Situation | Method |
|-----------|--------|
| Windows desktop | Download `HydraTorrent.exe` from the [latest release](https://github.com/DelCtrlAlt33/hydra-torrent/releases/latest) and run it. It installs itself. When updating, fully quit the running instance first (tray icon, Exit). |
| Docker host | `git clone https://github.com/DelCtrlAlt33/hydra-torrent.git`, then `docker compose up -d` in the repo. No edits needed. |
| Ubuntu / Debian / LXC / VM | `git clone` as above, then `sudo bash install.sh`. It installs to `/opt/hydra` and creates a systemd service named `hydra-torrent`. |
| Anything else with Python 3.10 to 3.13 | `pip install -r requirements.txt`, then `python hydra_daemon.py` |

Facts that trip agents up:

- Hydra serves **HTTPS only**, on port **8765**, with a **self-signed certificate**. The UI is at `https://<host>:8765/ui`. Plain `http://` does not work. Use `curl -k` for checks. Do not "fix" the certificate warning.
- Every API call needs the header `X-API-Key: <key>`. The key is the `daemon_api_key` field in `hydra_config.json` (in `/opt/hydra/` for `install.sh`, in `./data/` for Docker). That file holds the API key plus any Plex/Jackett/Jellyfin tokens, and Hydra keeps it owner-only (mode 0600, owned by `hydra`; by root in Docker), so read it with `sudo`, for example `K=$(sudo python3 -c "import json;print(json.load(open('/opt/hydra/hydra_config.json'))['daemon_api_key'])")`. Never print the key back to the user's chat or logs.
- **Hydra has no login.** Anyone who can reach port 8765 can load `/ui`, which hands out the API key, and so control Hydra. Do not publish the port to the internet, open it in a firewall to the world, or add a router port-forward. If the user wants remote access, suggest a VPN (WireGuard, Tailscale) or an authenticating reverse proxy, and let them decide.
- Check the port before installing: `ss -ltn | grep 8765`. If something is already listening, stop and tell the user.
- Download locations: `/opt/hydra/downloads` for `install.sh` installs, `./data/downloads` for Docker. They are owned by the `hydra` user or by root, so use `sudo` to look inside.
- Linux service commands: `sudo systemctl status|restart hydra-torrent` and `sudo journalctl -u hydra-torrent -f`.
- Keep the Docker `./data` volume across rebuilds. It holds config, certificates, transfers and downloads (`./data/downloads`). The container runs as root, so those files are root-owned and need `sudo` to edit or delete from the host.
- `docker` may need `sudo` on a fresh machine (the user isn't in the `docker` group until they log in again). Use `sudo docker compose up -d`.
- A torrent that shows the same `progress` and `peers` on two reads a few seconds apart is not downloading, whatever its `status` says. Check 4 uses this to prove downloads survive a restart.

## 2. Known install problems

- `pip install libtorrent` fails: check Python is 3.10 to 3.13. On Linux try `sudo apt install python3-libtorrent`.
- `install.sh` fails with `\r: command not found`: the file got Windows line endings. Fix with `sed -i 's/\r$//' install.sh`, or re-clone on Linux.
- Proxmox LXC: read [PROXMOX.md](PROXMOX.md) first.
- Updating an `install.sh` install: `cd hydra-torrent && git pull && sudo bash install.sh`. Re-running the installer is the update (it copies the code to `/opt/hydra`, refreshes dependencies and restarts the service). `git pull` plus `systemctl restart` alone does nothing, because the service runs from `/opt/hydra`. Verify with `grep VERSION /opt/hydra/config.py`.
- `install.sh` exits 100 right after a VM's first boot: apt was probably locked by first-boot upgrades. Wait a minute and re-run it.

## 3. Configuration

Everything is optional and is set in the Settings tab, or over the API: `GET /config` returns the effective settings and `PATCH /config` changes them (JSON body, with the API key header). Ask the user for these, and configure only what they want:

- **Plex**: URL and token. **Jellyfin**: URL and API key.
- **Jackett** (Search and Auto-DL): URL and API key.
- **Save downloads to** (config key `download_dir_incomplete`): where downloads are saved. With no Plex/Jellyfin this is the only folder needed. With Plex/Jellyfin it is also the working folder: files stay here while downloading so the media server never sees half-finished files, and it should be on the same drive or share as the library so the finishing move is an instant rename, not a copy.
- **Movies folder / TV folder** (config keys `media_dir_movies`, `media_dir_tv`): only shown in Settings once Plex/Jellyfin is set up (or a folder already has a value). They must be the same folders the media server's libraries use, as the machine running Hydra can open them. Hydra fills them in itself when it can open the folders Plex/Jellyfin reports; otherwise set them by hand (Settings, Browse, or `PATCH /config`).
- There is no "other downloads" box: anything that isn't a movie or show for Plex stays where it downloaded. (`download_dir_other` in the config still works for anyone who wants a separate finished folder.)
- **Require VPN to download**: the kill switch. Hydra turns it on by itself the first time it sees a VPN, and leaves it off when there is none. Do not switch it on by hand if no VPN is running on the same machine: every download would pause.

Guides: [docs/plex-jellyfin.md](docs/plex-jellyfin.md), [docs/search-jackett.md](docs/search-jackett.md), [docs/vpn.md](docs/vpn.md), [docs/nas-storage.md](docs/nas-storage.md).

**The most common mistake.** If Plex or Jellyfin runs in Docker, the library path it reports is the path *inside its container*. Hydra, if it runs natively, needs the *host* path. Do not accept a Plex-reported path without checking that this machine can open it. If the Movies or TV box shows a warning, run `docker inspect <container> --format '{{json .Mounts}}'` and use the `Source` field. Details: [docs/plex-jellyfin.md](docs/plex-jellyfin.md).

**NAS permissions.** If the library is reached over more than one protocol (SMB and NFS), both must map to the same NAS user. Test by creating a file from each side and deleting it from the other. Details: [docs/nas-storage.md](docs/nas-storage.md).

## 4. Verification checklist

Run every item. Report pass / fail / could not test, with evidence.

1. **Service up.** `curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:8765/ui` prints `200`.
2. **No crash loop.** After 60 seconds, `sudo journalctl -u hydra-torrent --no-pager | grep -ci traceback` prints `0` and `systemctl show hydra-torrent -p NRestarts` prints `NRestarts=0`. For Docker, use `sudo docker logs hydra-torrent 2>&1 | grep -ci traceback`.
3. **Integrations.** `GET /health` (with the API key) returns JSON like this on a fresh install:
   ```json
   {"vpn": {"connected": false, "iface": null, "vpn_ip": null, "public_ip": null, "country_code": null},
    "jackett": {"configured": false, "reachable": false, "error": null},
    "plex": {"configured": false, "reachable": false, "error": null},
    "jellyfin": {"configured": false, "reachable": false, "error": null}}
   ```
   For each integration the user asked for, `configured` and `reachable` must both be `true`. An unconfigured one shows `configured: false`, which is correct if the user didn't ask for it. If the user asked for none, report "n/a, none requested" and confirm only that the call returned JSON.
4. **A real download works, and survives a restart.**
   1. Add a small, legal test torrent: `POST /transfers/url` with body `{"url": "https://releases.ubuntu.com/24.04/ubuntu-24.04.5-live-server-amd64.iso.torrent"}` (HTTP 202).
   2. After about 20 seconds, `GET /transfers` returns a list. Each entry also carries a `pieces` array of thousands of booleans (about 90 KB for a 4 GB torrent), so do not print the raw response: pipe it through `python3` and print only the fields you need. Beyond what is shown below, an entry also has `magnet`, `speed_up`, `num_pieces` (the count of completed pieces), `vpn_paused`, `moved_to_plex`, `plex_path`, `start_time`, `ratio` and `total_uploaded`. A trimmed entry looks like this:
      ```json
      {"name": "ubuntu-24.04.5-live-server-amd64.iso", "size": 4080486400, "bytes_done": 125779968,
       "progress": 3.08, "speed_down": 12380237.0, "eta": "5m 19s", "peers": 73, "seeds": 68,
       "status": "Downloading", "paused": false, "error": null}
      ```
      `progress` is a percentage (0 to 100) and `speed_down` is bytes per second. `peers` and `speed_down` must both be above 0. A torrent with 0 peers after a minute is a **fail**, not "still starting".
   3. Restart Hydra mid-download (`sudo systemctl restart hydra-torrent`, or `sudo docker restart hydra-torrent`). Wait 20 seconds, then read `GET /transfers` twice, 8 seconds apart. `progress` must increase between the reads and `peers` must be above 0. Frozen numbers are a **fail**.
   4. Clean up, **including the files**: `DELETE /transfers/<name>?delete_files=true`. A plain `DELETE /transfers/<name>` leaves the data on disk. Afterwards the downloads folder should be empty (`sudo ls -A /opt/hydra/downloads`).
5. **Library paths are writable.** For each Movies / TV / Other path that is set, create and delete a temp file as the user Hydra runs as, and confirm the media server sees the same folder. The `hydra` user has a `nologin` shell, so use `sudo -u hydra bash -c 'touch /path/.probe && unlink /path/.probe'`. In Docker, Hydra runs as root, so a plain `touch` inside the container is enough. If none is set, confirm that the default downloads folder (see "Download locations" above) exists and is writable by Hydra, and report that no library path was configured.
6. **No folder warning** under the Movies or TV box when Plex or Jellyfin is configured (a warning means this machine can't open that folder).
7. **Search works.** If Jackett is configured, run one search and confirm results come back.
8. **VPN detected.** If the user has a VPN, `GET /health` shows `vpn.connected: true` and `GET /config` shows `vpn_required: true` (Hydra sets it itself the first time it sees the VPN).
9. **Kill switch works.** Only with the user's permission: bring the VPN down, confirm active torrents pause, bring it back, confirm they resume. If you can't or shouldn't, report **could not test**. Never report pass without doing it.
10. **Autostart.** Linux: `systemctl is-enabled hydra-torrent` prints `enabled`. Docker: `restart: unless-stopped` is set.

## 5. Report format

Finish with: the install method used, the URL, a table of the ten checks with results, anything you changed, and anything the user still has to do (tokens, VPN setup, path fixes).
