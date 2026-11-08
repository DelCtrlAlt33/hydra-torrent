# Updating

[Back to the README](../README.md)

**Windows:** the app checks for a newer release on launch and shows an update badge (top-right in the browser/web UI, top-left of the title bar in the desktop app) when a newer version is out. Click it, download the new `HydraTorrent.exe`, and run it - but fully quit the running instance first (right-click the tray icon and choose Exit, not just the window's close button, which only hides it to the tray). Otherwise the still-running old version just refocuses itself and the update never applies.

**Docker:**
```bash
cd hydra-torrent
git pull
docker compose up -d --build
```
`./data` stays mounted as a volume, so config/certs/transfers survive the rebuild untouched.

**Linux:**
```bash
cd hydra-torrent
git pull
sudo bash install.sh
```
Re-running `install.sh` **is** the update step. It copies the new code into `/opt/hydra` (the folder the service actually runs from), refreshes the dependencies, and restarts the service. Your settings, transfers and downloads are kept.

A plain `git pull` plus `systemctl restart` does **not** update an `install.sh` install: the service runs from `/opt/hydra`, not from the folder you cloned into, so it would keep running the old code. To check which version is running, look at the version shown in the app, or run `grep VERSION /opt/hydra/config.py`.

**Manual:**
```bash
cd hydra-torrent
git pull
pip install -r requirements.txt
python hydra_daemon.py
```
