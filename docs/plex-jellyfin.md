# Plex and Jellyfin

Optional. Auto-files finished downloads into your library and triggers a scan.

[Back to the README](../README.md)

If you use Plex or Jellyfin, Hydra can move finished movies and TV shows into your library and tell Plex/Jellyfin to scan.

## The folders

In **Settings, Downloads** there is one box everyone uses:

- **Save downloads to**: where downloads are saved. With no Plex or Jellyfin that is all you need: everything downloads there and stays there.

If you use Plex or Jellyfin, a **Movies folder** and a **TV folder** appear once you connect Plex/Jellyfin (or click **Use Plex or Jellyfin? Set up library folders**). Finished movies and shows are moved into them and Plex/Jellyfin is told to scan. Music, ISOs and anything else that isn't a movie or show stays where it downloaded.

**Why "Save downloads to" matters for Plex.** While a download is still running its files stay there, so Plex never sees half-finished files. When it finishes, Hydra moves the movie or show into the right library folder (`Movies/Movie Name (2024)/`, `TV/Show/Season 01/`). Put "Save downloads to" on the **same drive or network share** as your Movies and TV folders and that move is instant. On a different drive it becomes a full copy, which is slow for big files.

**Movies and TV must be folders this computer can open.** That is not always the path Plex shows you, because Plex may be in Docker or on another machine.

- **Hydra and Plex on the same machine:** Hydra fills both boxes in for you as soon as Plex or Jellyfin is connected, as long as it can open the folders Plex reports. If "Save downloads to" is empty it also suggests an `incomplete` folder beside your library.
- **Hydra on Windows, Plex on a server or NAS:** press **Browse** and pick the same folders through a network share (`\\server\media\movies`) or a mapped drive (`Z:\movies`). If Plex reports something like `/data/movies`, that is its own path inside Docker and your PC can't open it, so Hydra leaves the box empty. Use Browse.
- **Hydra and Plex in Docker on the same host:** see [Docker path mapping](#docker-path-mapping-read-this-if-plexjellyfin-runs-in-docker) below.

**Advanced: a separate "finished" folder.** If you want unfinished downloads in one place and everything that isn't a movie or show moved to another, set `download_dir_other` in `hydra_config.json` (or `PATCH /config`). It has no box in Settings on purpose, because most people don't need it.
## Connecting Plex or Jellyfin

In **Settings, Connections**, enter the address and the token or key. Hydra saves them and re-checks the connection as soon as you leave the box, and the badge in Connection Health shows the result. Clear a box and it goes back to "Not configured".

- **Plex:** the **Plex URL** (e.g. `http://localhost:32400`) and **Plex token**. To find the token, open any media in Plex web, click "Get Info", click "View XML", and look for `X-Plex-Token=` in the URL ([Plex's guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)).
- **Jellyfin:** the **Jellyfin URL** and an **API key** (Dashboard → API Keys).

## What Hydra does when a download finishes

Hydra looks at the filename and works out what it is:

- Has `S01E05`, `Season 2`, `1x05`, or episode numbers → **TV show** → moves to `TV folder/Show Name/Season 01/`
- Has a year like `(2024)` → **Movie** → moves to `Movies folder/Movie Name (2024)/`
- Anime with fansub tags like `[SubGroup]` → detected as TV automatically

A library scan triggers after every move, so new content shows up within seconds. Anything that isn't a movie or show (a non-video file, or a video when no Movies/TV folder is set) stays in the download folder.

### Docker path mapping (read this if Plex/Jellyfin runs in Docker)

This is the single most common setup mistake, so it gets its own section. The short version: **auto-move only works if Hydra and your media server agree on what a folder is called.** If Plex is in Docker, Plex reports its library path as seen *inside its own container*, which is often not the real path on disk - Hydra only fills the boxes in by itself when this computer can open the folder Plex reports, and warns if a folder you typed can't be opened, rather than silently pointing somewhere wrong.

Which setup you're in determines the fix:

- **Plex/Jellyfin and Hydra both installed directly on the same machine (no Docker at all).** Nothing to do - the paths already match. This is the common case for Windows users running the desktop app.

- **Plex/Jellyfin on a NAS or separate machine, Hydra elsewhere (e.g. the Windows desktop app).** Use whatever network path both can actually reach - a mapped drive letter or a UNC path like `\\nas\media\movies`. It just has to be the same share both sides are pointed at.

- **Plex/Jellyfin runs in Docker, Hydra runs natively on the same host** (installed via `install.sh`, or the desktop app on the same box) - this is the case the folder warning is built for. Docker remaps a host folder to a different path inside the container (e.g. `/mnt/plex/movies` on the host becomes `/data/movies` inside Plex's container). Hydra needs the **host** path, not what Plex reports. Find it with:
  ```bash
  docker inspect <plex-container-name> --format '{{json .Mounts}}'
  ```
  The `"Source"` field is the real host path - use that in Hydra's Movies and TV folders.

- **Hydra itself also runs in Docker, alongside Plex/Jellyfin.** Mount the same host folder to the *same* internal container path in both services' `docker-compose.yml`. If Plex has `- /mnt/plex:/data`, give Hydra the identical line - `- /mnt/plex:/data` - not a different internal path. With matching mounts, Hydra can fill the folders in by itself, container or not. This is the same "consistent path mapping" principle [TRaSH-Guides](https://trash-guides.info/) recommends for Sonarr/Radarr/qBittorrent, applied to Hydra.

Rule of thumb either way: if the box under Movies or TV says nothing (or shows a green check), you're done. If it warns that this computer can't open the folder, you're in one of the mixed setups above - follow the matching fix, not a guess.
