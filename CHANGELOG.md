# Drift & Bug Log

Not a feature changelog (that's `git log` / the GitHub Releases page). This is
a running record of real bugs found after the fact - drift, edge cases,
things that worked "on my machine" but not somewhere else - with the actual
root cause, not just the symptom. The point is a scannable list of what's
already gone wrong once, so a future fix (by Matth, by a contributor, or by
an AI session with no memory of this one) starts from "here's what already
happened" instead of from zero.

Newest first. Add an entry whenever a fix is more than a typo - anything
where knowing *why* it broke matters as much as knowing that it's fixed now.

---

## 2026-11-10 - Audit fixes: moved torrents re-downloading after a restart, duplicate adds, VPN detection, Auto-DL (v1.4.33; found by reading the whole codebase after a red CI run, not from a bug report)

- **A finished multi-file torrent downloaded itself again into the library after every restart.** Reproduced with two real libtorrent sessions before fixing.
  - **Cause:** `_resume_persisted()` re-added a moved torrent with `plex_path` (the library folder its video was moved into) as the save path. That only lines up for a single-file torrent. For a torrent with its own folder, libtorrent looked for `<library folder>/<torrent folder>/<file>`, found nothing, and downloaded everything again, into the library, while `_monitor_seeding` kept the row on "Seeding".
  - **Seen live:** `/mnt/plex/tv/Game of Thrones/Season 05/` on LXC 108 holds a second full copy of the season inside a folder named after the torrent, and Season 02 has the same pattern. Consistent with this bug; the files date from March/April so the cause cannot be proven from them.
  - **Fix:** the torrent is re-added at its original save path, with each moved file mapped to where it really is (`_moved_layout`, passed as `renamed_files`). The exact map is recorded at move time (`moved_map` on the transfer record); older records are matched back by file name from `moved_files`, or from `plex_path` alone.
  - **Safety net:** any torrent recorded as finished is restored in libtorrent's upload mode with auto-management off (`_seed_only_flags`), so it cannot request a single piece whatever it does or does not find on disk.
  - **No saved metadata (a crash):** the magnet is re-added in upload mode and the file map is applied once metadata arrives (`_apply_moved_layout_when_ready`).
- **A file moved into the library silently stopped seeding.** The changelog entry of 2026-10-18 noted "the effect on seeding was not verified". It is now: with the file moved by `shutil.move()` and libtorrent not told, a connecting peer got 0% while the torrent reported "Seeding" with no error. `_post_download()` now calls `handle.rename_file(index, absolute destination)` after each move (`_remap_moved_file`), which only updates libtorrent's idea of the path because the old path is already gone. A peer then gets 100%.
- **Opening the add dialog for a torrent already in Transfers, then closing it, killed the running torrent.**
  - **Cause:** libtorrent answers a second add of a torrent it already has with the SAME handle. The preview stored that handle under a new hidden name, and closing the dialog (`DELETE /transfers/{name}`) removed it from the session. The original row stayed, with a dead handle.
  - **Also:** a second plain add (`add_magnet`, used by Auto-DL and pasted magnets) overwrote the existing record, wiping `moved_files` (so delete-with-files could no longer find the library copy) and the upload total, and started a second monitor thread.
  - **Fix:** every add path checks the session first (`_claim`, comparing v1 and v2 info-hashes separately so a v1-only magnet still matches a hybrid torrent). A preview of a real transfer is refused with HTTP 409 "Already in your transfers: <name>"; a plain add returns the existing name and changes nothing; a leftover preview of the same torrent is discarded and replaced.
- **A fresh Linux install could never see a WireGuard VPN.** `wg show` needs root, so `_wg_handshake_ok()` falls back to `sudo`. LXC 108 only worked because of a hand-made `/etc/sudoers.d/hydra-wg` that nothing in the repo creates. Without it sudo refuses, and that was reported as "tunnel is dead", so with the kill switch on nothing ever downloaded. Now: `sudo -n` (never prompts), a refusal from sudo itself means "cannot verify" and the interface is trusted as up (logged once), and `install.sh` writes the one-command sudoers rule (validated with `visudo`, removed if invalid). Documented in `docs/vpn.md`.
- **A VPN that reconnected on a new address between two polls left Hydra bound to the old one.** `VPNGuard` compared only connected/disconnected, and Linux polls every 30s. The PIA renew script on LXC 108 worked around this by restarting Hydra. The guard now also fires when the address changes while connected.
- **A magnet that started being monitored before its metadata existed kept a placeholder name and size 0 for good.** Happens for a magnet added while the kill switch is holding everything (Auto-DL during a VPN outage) and for one restored after a restart that interrupted its metadata fetch. It finished with `bytes_done` 0 and a seed ratio of 0, so the seed-ratio limit never removed it. `_monitor_download()` now fills in the real name and size as soon as metadata is there.
- **Turning the kill switch off with no VPN connected did nothing until a restart.** Only "on" was applied live; the session stayed on loopback and every torrent stayed on "Waiting for VPN". `TorrentEngine.apply_kill_switch()` now handles both directions. Pressing Resume on a held torrent while the switch is on no longer clears its "held for VPN" flag.
- **A changed Movies/TV/"other downloads" folder was saved but not used until a restart.** `config.py` reads them once and `media_organizer.py` copies them at import, so finished downloads kept being filed with the old values (on a first-time setup: not filed at all). `PATCH /config` now updates the live values (`_apply_live_folder_settings`).
- **Auto-DL ignored every release that had no magnet link.** Indexers that only return a .torrent link (most private trackers) could never match a rule. `RssPoller` now takes an `add_url_fn` and the daemon passes `TorrentEngine.add_torrent_url()` (the body of the `/transfers/url` endpoint, moved onto the engine so both share it).
- **One malformed Jackett result failed the whole search.** A missing `pubDate` or a non-numeric seeder count raised out of `search_jackett()`. Items are now parsed one at a time and a bad one is skipped.
- **Desktop app with `daemon_host` set to `0.0.0.0` opened on an error page.** `main()` pointed the window at `http://0.0.0.0:8765`, which nothing can connect to on Windows. It now connects to loopback, as the tray and the single-instance check already did.
- **Docker: no incoming peers.** `docker-compose.yml` published 8765 but not the BitTorrent port. 6001 TCP and UDP are now published.
- **Smaller:** a restart without usable resume data now re-adds a torrent at the save path it was really given (recorded as `save_path`), not the default folder; a discarded preview no longer leaves its metadata-wait thread dying with "invalid torrent handle"; unused imports removed.
- **CI:** the red runs that started this were not a Hydra bug. `tests/test_kill_switch_default.py` imports `hydra_daemon` (and so libtorrent), which the main `test` job does not install, so pytest died at collection and ran nothing. It now runs in the `engine-tests` job, as does the new `tests/test_engine_moves_and_duplicates.py`.
- **Tests:** `tests/test_engine_moves_and_duplicates.py` (16 tests; a second libtorrent session plays the rest of the swarm, so the tests see whether a moved file is really served and whether a restart really downloads anything), plus new cases in `test_vpn_guard.py`, `test_rss_poller.py` and `test_search.py`.
- **Not changed:** the web UI still has no "delete files too" option on Remove (the API supports it). The duplicate folders already in the library on LXC 108 were left alone.

---

## 2026-11-08 - Project moved back to GitHub; update check and bug reporter repointed (v1.4.32)

- **Context:** the GitHub account was restricted from about 2026-08-24 to 2026-11-08, so releases, the update check and the in-app bug reporter all pointed at Codeberg. With the account reinstated, GitHub is the home again.
- **Change:** `updater.py` now queries the GitHub releases API (`api.github.com`), the "Report a bug" button opens a pre-filled GitHub issue, and the README badges, download link and clone URLs point at GitHub. Test: `test_checks_github_releases_api`.
- **Upgrade note:** copies of v1.4.31 and earlier keep checking Codeberg for updates, so releases are published on both for now.
- **README media:** the full set of screenshots and GIFs (previously only on the Codeberg `easy-install` branch) is now on `main`.

---

## 2026-10-18 - Search results showed a literal `&amp;` in titles (v1.4.31; found while retaking screenshots)

- **Symptom:** a Search result such as "Linux Command Line (Hands-On) Ubuntu Linux &amp; Linux Scripting" displayed the `&amp;` as typed.
- **Cause:** some Jackett indexers send titles that are already escaped inside the torznab XML (`&amp;amp;`). The XML parser decodes that once, leaving a literal `&amp;` in the name, and the UI (correctly) escapes everything it renders, so the entity was shown as text.
- **Fix:** `search_jackett` now runs titles through `html.unescape`. The UI still escapes on output, so a title containing markup is displayed as text, not interpreted. Test: `test_search_jackett_unescapes_double_escaped_titles`.
- **Not fixed (indexer data, not Hydra):** some titles contain literal backslash-escaped quotes (`\"Debian\"`) as sent by the indexer.

---

## 2026-10-18 - Audit fixes: window controls, firewall port, tray, RSS, peer states, pinned dependencies (v1.4.30; found by reading the code and testing on real machines, not from a bug report)

- **Live VPN-drop test (real PIA tunnel dropped with `piactl disconnect`, run
  twice) and one fix it found.** With the kill switch on and a download
  running: every Hydra socket bound to the VPN address was gone within about 1s
  of the drop, no Hydra socket was ever seen on the real Ethernet/Wi-Fi
  addresses or a wildcard address (sampled every 0.25s, 0 leaks in both runs),
  the transfer was marked `vpn_paused` within 1-3s, and speed fell to 0. On
  reconnect Hydra rebound to the new VPN address (it changed each time) in
  about 4s and was downloading again in 7-10s. What the test found: about 5s
  into the drop libtorrent's auto-manager un-paused the transfer
  (`paused=False` while `vpn_paused=True`); `_monitor_download` and
  `_monitor_seeding` only ever resumed, never re-paused. Nothing could leave the
  machine (the session was bound to loopback), but a transfer that says
  "paused" should be, so the monitors now pause it again (it flaps for a moment
  between the auto-manager and the monitor's 2s tick). Not covered by this test:
  DNS and other traffic from the same machine, which the kill switch doesn't
  claim to cover, and packets as opposed to socket bindings.

- **Dependencies were unpinned, and pywebview wasn't listed at all.** A fresh
  install or a rebuild took whatever was newest that day; pywebview (which
  drives the desktop window) wasn't in `requirements.txt` so a rebuild picked
  its own. All `requirements*.txt` are now exact `==` pins, verified together:
  a clean environment built only from them passes the full suite. New
  `requirements-build.txt` pins everything the Windows exe is built from
  (pywebview, PyInstaller and their helpers). Dependabot's weekly PRs now bump
  pins deliberately. `tests/test_requirements_pinned.py` fails on any loose
  requirement and on CI's inline pins drifting from `requirements.txt`.
  Found while doing it: Pillow and `pystray` are used only by the Windows tray,
  yet were installed everywhere, including Linux where newer Pillow has no
  prebuilt wheel (a source build would fail on a minimal system); they are now
  Windows-only. `miniupnpc` and `aiofiles` were only used by the removed
  `network.py` and are gone from the requirements and the exe. Pillow and
  websockets have a second pin for Python 3.10, whose newest releases dropped
  it (resolved for 3.10/3.11/3.13 on Linux x86_64 and Windows, and for
  Linux ARM64 and the Docker image; Python 3.10 itself was not run).
- **Tray "Pause All" / "Resume All" gave up at the first error.** The whole loop
  was one try/except, so a timeout on one transfer silently skipped every
  transfer after it. Each transfer is now handled on its own and failures are
  reported. `tests/test_tray_actions.py` also covers Exit saving state first.
- **The second-instance fallback could restore the wrong window.** If the
  running copy didn't answer `/window/show`, the fallback found "its" window by
  title alone and could restore and focus another program's. New
  `window_lookup.py` (shared with `webview_api.py`) always filters by owner:
  process id for our own window, executable name for another copy of Hydra, and
  refuses a title-only lookup.

- **Minimize, maximize and resize stopped working when another program had a
  window titled "Hydra Torrent".** `webview_api._find_hwnd` (and the code that
  gives the window its resize frame) used `FindWindowW(None, title)`, which
  returns the first window anywhere with that title. A File Explorer tab or
  window open on a folder called "Hydra Torrent" has a hidden shell window
  (`Windows.Internal.Shell.TabProxyWindow`) with exactly that title, so every
  window operation, including applying `WS_THICKFRAME`, acted on Explorer's
  window and none on Hydra's. Reproduced live: the real window had no resize
  frame and ignored `minimize()`/`maximize()` even when called directly. The
  lookup now matches the owning process id. Fixed in the same change: the frame
  styling retries for a few seconds if the window isn't up yet. (The
  second-instance fallback in `hydra_app.py` still uses the title, but it only
  ever tries to restore a window and is guarded by the daemon's own
  `/window/show` call.) `tests/test_window_lookup.py` guards it.

- **Most peers were labelled "Unknown", and half-open peers were counted as
  connected.** libtorrent lists a peer as soon as it starts connecting, with an
  empty client string and an all-zero peer id until the handshake finishes.
  Measured live: 88 of 141 peers in one sample (and 98 of 103 earlier) were in
  that state, and Hydra called them "Unknown", counted them in "peers
  connected", and drew map arcs to them. `get_peer_info` now sets `connecting`
  for those; the UI shows them dimmed as "connecting…", leaves them off the map
  and out of the connected count (the header says "N connecting"). Handshaken
  peers that send no version string are identified from their peer-id
  fingerprint (libtorrent's `identify_client`); after the change no established
  peer was left "Unknown" in the live sample.
- **A broad RSS rule could queue hundreds of torrents at once.** A rule such as
  "debian" at "Any" quality matched 106 releases in one live check and started
  adding all of them. A check now adds at most `MAX_ADDS_PER_CHECK` (10), the
  ones with the most seeders; the rest are not recorded as matched, so later
  checks pick them up in turn. A warning in the log says when the cap applies.
- **RSS rules showed "never checked" until every add finished.** The check was
  recorded only after all adds completed, and an add waits up to 10 minutes for
  metadata. `last_checked` is now recorded as soon as the search and matching
  are done, `pending_adds` (new, shown as "N adding") reports what is still
  being added, and results are recorded as each add finishes. Overlapping
  checks of one rule (check-now during adds, or the periodic poll) are skipped,
  since titles aren't recorded as matched until their add succeeds and would
  otherwise be added twice.

- **Windows firewall rule opened the wrong port.** The installer allowed
  6002/TCP+UDP and 6881/UDP; libtorrent listens on `PEER_PORT` (6001) for peers
  and DHT. Inbound peers depended on the Windows prompt. Now opens 6001 and
  removes the two stale rules. `tests/test_firewall_port.py` keeps the literal
  in step with `config.PEER_PORT`.
- **Tray Exit threw away fast-resume data.** `os._exit(0)` skips the daemon's
  shutdown, the only place resume data is saved, so every tray Exit meant a
  full re-hash on the next start. The tray now calls `engine.shutdown()` first.
- **"Disable startup" undid itself.** The tray re-registered itself for startup
  on every launch. Now only the first time (`tray_startup_initialized`). A user
  who had already disabled it gets it re-enabled once on upgrade.
- **Tailscale / LAN-only tunnels were picked as "the VPN".** Detection is by
  interface name, and Tailscale without an exit node has no internet route, so
  torrents bound to it found no peers. Tailscale is no longer matched, and
  `vpn_ignore_interfaces` in `hydra_config.json` skips any other adapter.
- **A metadata timeout looked like a successful add.** `add_magnet` returned
  normally after 10 minutes with no metadata, leaving an unmonitored torrent in
  the session, and the RSS poller recorded the title as matched so it was never
  retried. It now removes the torrent and raises `TimeoutError` (as its
  docstring said). RSS adds run concurrently and only matched titles are
  recorded on success.
- **`transfers.json` was rewritten on every state update.** Routine progress
  saves are throttled to every 2s; structural and persisted-state changes still
  save immediately.
- **Desktop app ignored `daemon_use_ssl`.** It looked for `hydra.crt/.key`,
  which nothing writes, and silently fell back to HTTP. It now uses the same
  certificate files as the standalone daemon.
- **Peer flags came from a third-party host.** The peer list's flag images were
  fetched by the browser from flagcdn.com, a request made outside the VPN-bound
  torrent session. The flags are now bundled (flag-icons, MIT, 257 country SVGs
  in `static/flags`) and served by Hydra itself at `/flags/xx.svg`, so the page
  makes no third-party request at all. Only a two-letter lower-case code is
  accepted, the path is re-checked to stay inside `static/flags`, and the
  response carries a sandboxing CSP so an SVG opened directly cannot run script.
  A bare `flags/` rule in `.gitignore` had to be narrowed so the folder is
  tracked.
- Removed unused `network.py` (old peer system) from the Windows build.
- Tests added: VPN drop/reconnect behaviour (`_on_vpn_change`), store save
  throttling, metadata timeout, RSS retry, ignore list, firewall port.

---

## 2026-10-07 - Peer map was blank without a VPN, and a VPN drop did not stop downloads by default (v1.4.29)

Found while retaking the README screenshots on a clean demo install, then
testing the kill switch against a real VPN (PIA over WireGuard) dropping.

- **The peer map drew nothing for anyone without a VPN.** The map only learns
  where "home" is from `/status`, and `/status` only reported a country
  when the VPN was connected, so `HydraMap` returned early with no home
  point. The header said "196 peers" over an empty map. `/status` now
  returns the country of the public IP either way (the IP itself is still
  withheld when unprotected) and the UI sets home in both states. The map is
  country level on purpose: the bundled DB-IP database is country only, so
  every peer in a country shares one dot.
- **With the default settings a VPN drop did not stop anything.**
  `vpn_required` defaults to false (so a user with no VPN is not left with
  every torrent paused), and with it off `_on_vpn_change` rebinds to the real
  network and keeps going. Measured on a real drop: within 3 seconds Hydra had
  sockets on the real LAN address and kept downloading and seeding for the
  whole outage, while the README promised "every torrent pauses instantly".
  With the switch on it behaved correctly (no sockets on any real interface
  within a second, progress frozen, resumed about 3 seconds after reconnect).
  Hydra now turns the kill switch on once, the first time it sees a VPN
  (`vpn_kill_switch_auto_armed` marks that it happened, so turning it off
  in Settings sticks). No VPN, no change. Anyone upgrading with a VPN already
  running will find it on after the next start. Tests in
  `tests/test_kill_switch_default.py` fail on the old code.
- Not verified: DNS or other non-torrent traffic from the same machine, which
  the kill switch does not cover, and how long data already in flight keeps
  moving in the first second or two after a drop.

---
## 2026-10-04 - Fresh installs could not download, .torrent transfers died on restart, secrets were world-readable (v1.4.28)

Found by installing from scratch on a clean Ubuntu 24.04 VM (native and
Docker) and then having a fresh AI agent do the same from the README alone.
All three were invisible on the dev machine because it always had a VPN up
and a long-lived daemon.

- **No VPN meant 0 peers, forever.** `_create_session()` passed
  `outgoing_interfaces: '0.0.0.0'` to libtorrent when no VPN was detected.
  `0.0.0.0` is a wildcard address, not an interface, so every outbound
  connection failed: torrents sat at 0 peers with a normal-looking
  "Downloading" status. The VPN-drop rebind path already used `''`
  ("no restriction"), which works; the startup path now does the same.
  Confirmed in isolation with a bare libtorrent session (0 peers vs ~60
  seeds at ~18 MB/s, same VM, same torrent).
- **Transfers added from a .torrent file or URL were never re-attached after
  a restart.** `_resume_persisted()` skipped any entry with an empty
  `magnet`, and Jackett results / uploaded files have none. After any
  restart, update or reboot they stayed in the list frozen at their last
  progress and peer count (identical numbers on two reads is the tell), and
  "remove + delete files" could not reach their data because there was no
  handle. They now restore from the metadata `_save_resume_data()` already
  stored. Regression test fails on the old code.
- **`hydra_config.json` and `certs/privkey.pem` were mode 0644.** The config
  holds the API key and the Plex/Jackett/Jellyfin tokens, so any other local
  user could read them. New files are now created 0600, existing ones are
  tightened at startup, and every config write re-applies it (no-op on
  Windows). Note this does not make the UI private: `/ui` still serves the
  API key to anyone who can reach the port, by design (no login), so the
  README now says plainly not to expose port 8765.
- **The documented Linux update never updated anything.** The Updating docs
  said `git pull` + `systemctl restart`, but `install.sh` copies the code to
  `/opt/hydra` and the service runs from there, so the pull changed a folder
  nothing was reading and the restart reloaded the same old code. Found by
  installing the previous release, following the docs, and checking
  `/opt/hydra/config.py`: still the old version. Anyone following the docs
  stayed on the old (0-peers) build indefinitely. The update step is now
  `git pull && sudo bash install.sh`, and `install.sh` does `restart` instead
  of `start` (a no-op on an already-running service).
- **Test Connection and Detect ignored what you had just typed.** Found by
  the first real test of the Windows exe. Both read only the SAVED settings
  (`/health`, `/plex/libraries`), so a Plex/Jackett URL and token typed but not
  yet saved showed "Not configured". The badges only turned green after
  flipping an unrelated switch (the VPN toggle), because that happens to call
  `saveSettings()` for every field and then refresh health. Both buttons now
  save first. The same test showed the Downloads section was hard to read:
  four folder boxes with no plain-words explanation, and Detect filling in
  Plex's in-container paths (`/data/movies`) that a Windows PC cannot open,
  with a warning that said what was wrong but not what to enter. The warning
  now says to use the path this computer uses (network share or mapped
  drive), Movies/TV got Browse buttons, "Other downloads" is collapsed unless
  already set, and `docs/plex-jellyfin.md` opens with a table of what each
  folder is plus a worked Windows-PC-and-Plex-server example.
- **Clearing the Plex or Jackett box left the health check saying "Connected".**
  Found by the same user, after the Downloads section had been tidied once and
  was still judged confusing. `saveSettings()` deliberately skipped blank
  values so it could never overwrite a saved setting by accident, which meant
  a *cleared* Plex/Jackett/Jellyfin box was never saved: the old URL and key
  stayed in the config and the health check kept connecting with them. Now the
  connection and library boxes are saved as cleared (only once the real
  settings have loaded into the boxes, otherwise "empty" would just mean "not
  loaded yet"), they save and re-check as soon as you leave the box, and
  Connection Health re-checks every 20s while Settings is open. Separately,
  Jackett's health check was meaningless for the key: it called `t=caps`,
  which Jackett answers with HTTP 200 for ANY key, even none. It now calls
  `t=indexers` (about a second), whose XML body says `<error code="100">
  Invalid API Key` for a bad or missing key, and a blank key reads "Not
  configured".
- **The Downloads section was four differently-shaped boxes plus walls of
  text.** The settings block is a two-column grid, so some boxes spanned both
  columns, Movies/TV sat side by side, and "Other downloads" was a collapsible
  with its own paragraph; each box had been added for one edge case and never
  redesigned together. It is now one column: Download folder, Movies folder,
  TV folder, one short line under each. The "Other downloads" box is gone
  (anything that is not a movie or show for Plex just stays in the download
  folder, which is what a blank value already did; an existing value is kept),
  and the two "Detect" buttons are gone: Hydra now fills Movies/TV in by
  itself once Plex/Jellyfin is connected, but only an EMPTY box, and only with
  a folder this computer can actually open, so it never shows a warning about
  a path the user did not type.
- **A finished file was "moved" up out of its own folder.** Found by asking
  what actually happens to a finished download on the laptop. With "other
  downloads" set to the same folder as the download folder (as it was there)
  and the Plex folders unreachable, `_move_to_other_or_skip()` moved every
  finished video from `<download>/<torrent>/file.mkv` to `<download>/file.mkv`:
  a pointless relocation that left the `.nfo` behind and pulls the file out
  from under a torrent still seeding it (the effect on seeding was not
  verified). It now treats an "other" folder that IS the download folder, or
  that the file is already directly inside, as "not set" and leaves the file
  alone. A genuinely separate finished folder, including the download folder's
  parent, still works. Three regression tests fail on the old code.
- **Downloads settings, third pass: one box unless Plex is in use.** Even
  three boxes showed Plex concepts to people who don't use Plex. Now the
  section is "Save downloads to" and nothing else; the Movies/TV folders (and
  the one-line reason the download folder matters for Plex: unfinished files
  stay out of the library, and the same drive makes the finishing move an
  instant rename instead of a copy) appear once Plex/Jellyfin is set up, once
  either folder has a value, or when the user clicks "Use Plex or Jellyfin?".
  Once shown they stay shown for the session. The same-drive suggestion
  (an `incomplete` folder beside the library) that disappeared with the Detect
  buttons is back, in the auto-fill, and only fills an empty box. The
  "separate finished folder" is not a box any more; `download_dir_other` in
  the config still works.
- **A saved Movies/TV folder this PC can't open showed no warning.** Found by
  screenshotting the real window after the third pass. The "this computer
  can't open that folder" check used to run only as a side effect of the Detect
  button (and while typing); with Detect gone, a stale `/data/movies` sat in
  the box with no message even though finished movies could never reach Plex.
  The saved folders are now checked as soon as the settings load, and the
  empty space reserved for that message is no longer shown until there is one.
- **The maximize button never showed the "restore" icon.** The title-bar
  button had a fixed square icon and a fixed "Maximize" tooltip, so it looked
  the same whether or not the window was maximized. The page now asks the
  window for its real state (Win32 `IsZoomed`, exposed as `is_maximized`) every
  half second and swaps to the two-overlapping-squares icon and a "Restore"
  tooltip, so it also stays right when the window is maximized by a title-bar
  double-click or Win+Up.
- **The Windows window could vanish and its min/max buttons seemed dead.**
  Found by the same laptop test (a 1536x864 screen, i.e. 1920x1080 at 125%
  scaling). The window was found parked at x=26214, and once "maximized" at
  (-26214, 26214): exactly +/-32768 divided by the display scale, the
  "hidden window" placeholder coordinate used as if it were a real position.
  Windows still reported it visible, so it just looked like Hydra had
  disappeared. Two causes found by reproducing it: (1) the invisible
  edge-resize strips ran across the whole top edge and right edge, so a click
  near the top of the minimize/maximize/close buttons started a resize drag
  instead of pressing the button, and (2) that drag, like the buttons
  themselves, went through pywebview's `x`/`y`/`move`/`maximize`, which
  mishandle scaled displays; Windows' own `ShowWindow`/`SetWindowPos` were
  verified to behave correctly on the same machine. Fixed: the strips stay
  clear of the buttons, minimize/maximize/resize use Win32 directly (and a
  window at its minimum size no longer slides when you keep dragging an
  edge), and a watchdog puts a window that ends up on no screen, or taller
  than the usable screen, back. The placement maths is in
  `window_placement.py` with tests using the real observed numbers.
- Also: bare `/` returned 404 (now redirects to `/ui`); `docker-compose.yml`
  shipped `/path/to/movies` placeholders that broke a plain `up -d`; no
  `.gitattributes`, so a Windows clone got CRLF `install.sh`; the update
  check and Report-a-Bug button pointed at GitHub after the project moved to
  Codeberg.

---

## 2026-09-05 - The "move to Plex automatically" checkbox now follows SAVE AT instead of needing a separate click

- **Why a checkbox instead of just inferring intent from the SAVE AT
  path**: asked live, and worth recording the actual reasoning, not just
  the mechanism. A real, common workflow is downloading to a fast local
  drive for I/O reasons (random-write torrent activity performs badly on
  a NAS share) while still wanting the *finished* video organized into
  the real Plex library afterward - a custom SAVE AT there does not mean
  "keep this out of Plex." Silently inferring that from the path alone
  would break that case with no way to override it. A separate, visible
  control keeps the two questions ("where does this download live" vs
  "should it also end up in Plex when done") legible and independently
  answerable, matching the whole point of the original bug this session
  (SAVE AT quietly not being the whole story) - the fix for one silent
  side effect shouldn't introduce a different one.
- **The actual gap, now fixed**: the checkbox itself didn't need to
  exist as a *second, separate click* for the common case it was built
  for. It now starts matching the real global `auto_move_to_plex`
  setting, same as before, but also auto-unchecks itself the moment
  SAVE AT is changed away from the pre-filled default - covering "I
  moved this to Downloads because I don't want it in Plex" with zero
  extra clicks, exactly what was reported as missing. Clicking the
  checkbox directly at any point locks in that explicit choice from then
  on, so retyping the path afterward won't fight it - covering the
  fast-scratch-drive case with one click instead of none.

- **Symptom**: reported live, with a screenshot to prove it - several
  transfers sitting at "vpn paused" with real download/upload speed and
  real peer counts shown right next to that label, while a torrent added
  fresh in the same session showed correctly with no such label.
- **Root cause**: `_resume_persisted()` (runs on every daemon startup,
  restoring whatever was in `transfers.json` from the last shutdown)
  correctly re-evaluates the real libtorrent handle against the *current*
  VPN state (`handle.resume()` when the kill switch isn't actually
  active) - but it never touched the store's own `vpn_paused`/`paused`
  display fields, which were simply carried over as-is from whatever they
  were the last time the app quit. A torrent that got genuinely
  kill-switch-paused in an earlier session, then resumed on a later
  restart where VPN just happened to already be connected, kept showing
  the stale "vpn paused" label indefinitely - even though the real
  transfer was working correctly the whole time. `_on_vpn_change()`
  already does this exact reconciliation for a *live* VPN-reconnect event
  (see its own `vpn_paused=False` update); `_resume_persisted()` had no
  equivalent for "VPN was already up when the daemon started."
- **Fix**: `_resume_persisted()` now sets `vpn_paused`/`paused` from the
  same kill-switch-active check that already decides real pause/resume,
  instead of leaving them untouched from the last save.

## 2026-09-05 - "Retrieving metadata..." could sit for a long time on some search results

- **Symptom**: reported live - the add-torrent dialog's file list can take
  a genuinely long time to appear for some magnets, sometimes close to
  the metadata-fetch timeout, while others resolve almost instantly.
- **Root cause**: `_TRACKERS` (Hydra's own list of 11 solid, well-known
  public trackers, meant as a reliability net) was only ever added to a
  torrent *after* its metadata had already arrived - in `commit_preview()`
  and, separately, in `add_magnet()`'s own post-metadata block. The one
  part of the whole add flow where a supplemental tracker list would
  actually matter - the initial "Retrieving metadata..." wait, which
  needs to find and connect to a real peer for the ut_metadata handshake
  (BEP 9) - ran on nothing but whatever trackers happened to be embedded
  in that specific magnet (varies a lot by indexer) plus DHT alone,
  getting none of the benefit `_TRACKERS` exists for. `.torrent`-file
  previews were never affected - they carry their own real trackers
  already, with no metadata fetch needed at all.
- **Fix**: `preview_magnet()` now sets `_TRACKERS` on `params.trackers`
  (deduped against the magnet's own, order preserved) before calling
  `add_torrent()`, and `add_magnet()` now calls `handle.add_tracker()`
  for each one right after `add_magnet_uri()`, before its metadata-wait
  loop starts - both moved from after the slow part to before it.
- **Separately noted, not changed this round**: libtorrent's DHT routing
  table is never persisted across restarts (no `save_state`/`load_state`
  or `write_session_params_buf`/`read_session_params` anywhere), so every
  daemon launch starts DHT bootstrap cold from just the 3 router nodes.
  Real secondary contributor to metadata-fetch slowness, especially right
  after opening the app - touches core session init/shutdown though, so
  left as a known, deliberate follow-up rather than bundled in here.

## 2026-09-05 - A custom SAVE AT folder was silently ignored; the video got auto-organized into Plex anyway

- **Symptom**: reported live - picking a search result, changing SAVE AT
  in the add-torrent dialog to a folder of your own choosing (e.g.
  `C:\Users\Matth\Downloads`), downloading it, and the finished video
  file ends up moved into the Plex library instead, with only the
  non-video extras (subtitles, cover art) actually landing where SAVE AT
  said. The dialog told you where it was going; the daemon did something
  else.
- **Root cause**: not actually a bug in what the code did - it did
  exactly what it was written to do, which turned out not to match what
  the SAVE AT field implied. `_post_download()`'s decision to move a
  completed video into Plex/Jellyfin was gated purely on the single
  global `auto_move_to_plex` setting, with zero awareness of whether the
  user had just hand-picked a different destination for this specific
  download two clicks earlier. SAVE AT only ever controlled where
  libtorrent wrote the download; auto-organize ran as a fully separate,
  unconditional post-processing step afterward whenever the global
  toggle was on - which is most installs, since it defaults on.
- **Fix**: added a real per-transfer override. `commit_preview()` (and
  the `POST /transfers/{name}/commit` request body, `CommitPreviewRequest.
  auto_organize`) now accepts an explicit True/False that's stored on the
  transfer and checked first in `_post_download()`, before falling back
  to the global setting when unset. The add-torrent dialog got a new
  "Move the video into Plex/Jellyfin automatically when done" checkbox
  next to SAVE AT, defaulted from the real global setting so it never
  surprises anyone who already has it off, and unchecking it for one
  download now actually keeps that video exactly where SAVE AT put it.

## 2026-08-24 - A real, healthily-downloading torrent fired the completion toast within seconds of being added

- **Symptom**: reported live, with the Activity Log screenshot to prove
  it - adding a real torrent logged "+ name" immediately followed by
  "✓ name" at the same timestamp, and the Windows toast fired
  "Download finished" right away, but the media then kept downloading
  normally afterward for real. The daemon's own log for this exact
  download showed a genuine 3 minute 50 second gap between "Committed
  preview → downloading" and the real "Download complete" - the false
  completion signal had nothing to do with the actual download timeline
  at all.
- **Root cause**: the 2026-08-23 "instant fake-complete" fix (further
  up this log) guarded the *completion-handling* branch in
  `_monitor_download()` against a transient/untrustworthy
  is_finished/is_seeding reading on the first poll - but the ordinary,
  unconditional per-loop status write two lines above it
  (`status = 'Seeding' if s.is_seeding or s.is_finished else ...`) was
  never given the same guard. Every `store.update()` call pushes a
  fresh snapshot to every connected browser over the websocket, so the
  moment libtorrent's raw flags blipped true on an early poll - the
  same kind of transient reading the earlier fix already knew to
  distrust - the *displayed* status flipped to 'Seeding' and got pushed
  out immediately, even though the completion branch right below it
  correctly declined to treat it as a real finish and let the loop
  continue normally. The frontend has no way to know a status it was
  told was true wasn't; it logged "✓" and fired the toast on the very
  next honest signal, then never repeated either the moment the torrent
  actually, genuinely finished minutes later (its own de-duplication
  correctly assumed it already had).
- **Fix**: both decisions - the status field written every iteration,
  and whether to treat the torrent as complete - now come from one
  `genuinely_done` boolean computed once per iteration (not first pass,
  and `total_done > 0`), instead of two separately-maintained
  conditions that could drift apart.
- **Verified**: a new test uses a fake handle that returns a transient
  is_finished=True/total_done=0 reading on its first call and a normal,
  genuinely-downloading reading on every call after - confirms status
  never reads 'Seeding' on either poll, and the monitor loop keeps
  running rather than treating the blip as a real completion. Fails
  against the pre-fix code when checked directly (228 tests total, all
  passing).

## 2026-08-23 - Subtitles now follow their video into Plex; unrelated extras stay in the incomplete folder

- **Request**: after the previous fix made it clear that non-video
  extras (subtitles, cover art, NFOs, promo images) were all being
  swept into the local Downloads fallback regardless of Plex being
  configured, asked for two specific changes: subtitles should follow
  their video into the Plex library (so Plex can actually use them as
  an external subtitle track), and everything else that isn't real
  media should just stay in the incomplete folder instead of being
  scattered into Downloads.
- **Change**: `_post_download()` now runs in two passes instead of one
  blanket loop over every file. Pass 1 moves video files exactly as
  before (real Plex library, or the general-downloads fallback if no
  library is configured/reachable) and records where each one landed.
  Pass 2 looks at subtitle files (`.srt`/`.ass`/`.ssa`/`.vtt`/`.sub`)
  and pairs each one to its video by filename - exact stem match, or
  the video's stem extended with a `.`-separated suffix (e.g.
  `movie.en.srt` → `movie.mp4`, boundary-checked so `Show.S01E1` can't
  wrongly claim `Show.S01E10`'s subtitle in a season pack) - then moves
  it into that same destination directory with a plain filesystem move
  (`MediaOrganizer.move_alongside`, new). Anything that's neither a
  video nor a matched subtitle (cover art, NFOs, samples, release-group
  promo images) is no longer touched at all - it stays in the
  incomplete folder, where libtorrent's own delete-on-remove already
  cleans it up correctly without Hydra needing to track a second copy
  of its location.
- **Verified**: new tests cover `is_subtitle_file`, `move_alongside`
  (including the collision-avoidance and missing-source paths), the
  stem-matching boundary case that guards against the season-pack
  false positive, and a full end-to-end `_post_download()` run against
  a real three-file torrent (video + matching subtitle + unrelated
  cover art) confirming the subtitle lands in the exact same folder as
  its video and the cover art is left untouched in the incomplete
  folder (227 tests total, all passing).

## 2026-08-23 - A movie genuinely moved to Plex was reported as "moved to Downloads"

- **Symptom**: reported live - a real download whose movie file had
  genuinely landed in the Plex library folder (confirmed by checking
  the actual file on disk) was displayed as having moved to the local
  Downloads folder instead, even with Plex configured and reachable.
- **Root cause**: `_post_download()` calls `store.update(moved_to_plex=
  ..., plex_path=...)` once per successfully-moved file, unconditionally
  - so whichever file is processed *last* decides the whole torrent's
  reported outcome. A torrent's main video file often lands in the real
  Plex library while its extras (subtitle, cover art, sample images)
  correctly fall back to the general downloads folder afterward - both
  individually correct - but the second, unrelated result silently
  overwrote the first. Confirmed live via the daemon's own stored state
  on a real completed download: `moved_files` listed the `.mp4` under
  the real `\\...\Plex\movies\...` path *and* the `.srt`/`.jpg` under
  the local Downloads folder, yet the torrent's final `moved_to_plex`
  read `false` and `plex_path` pointed at the local folder - the last
  file processed, not the one that actually mattered.
- **Fix**: `moved_to_plex` is now sticky once true - a later file
  falling back to the general downloads folder can no longer un-set it
  - and `plex_path` only ever updates to a new value on a genuine Plex
  placement, not on a subsequent fallback move.
- **Verified**: a new test builds a real two-file torrent (a "movie" and
  an "extra"), fakes the mover so the first call lands under a real
  configured Plex library path and the second falls back to a separate
  local folder - confirms the stored `moved_to_plex`/`plex_path` still
  correctly reflect the real Plex placement afterward, and fails against
  the pre-fix code when checked directly (207 tests total, all passing).

## 2026-08-23 - An already-downloaded, already-moved-to-Plex file showed "Downloading" again after a restart

- **Symptom**: reported live, right after the previous fix - a torrent
  that had genuinely finished downloading and been moved to Plex showed
  "Downloading" again the moment the desktop app was restarted, even
  though `transfers.json` still correctly had `moved_to_plex: true` and
  the real destination in `moved_files`.
- **Root cause**: `_monitor_seeding()` set `status` to whatever
  libtorrent's *raw* internal state currently read
  (`_state_map.get(s.state, 'Seeding')`), instead of asserting `'Seeding'`
  unconditionally the way it already does for `bytes_done`/`speed_down`
  a few lines above in the same function (see the 2026-08-23 seeding-
  stats-freeze entry). This thread only ever runs for a torrent already
  known complete - either the real completion handoff from
  `_monitor_download`, or a restart-resume that found `status` already
  `'Seeding'` in `transfers.json`. But a torrent that's already been
  moved to Plex deliberately can't reuse its saved resume data on resume
  (that data still points at the pre-move path - see `_load_transfers`'s
  own docstring), so it gets re-added via a bare magnet instead, and
  libtorrent has to re-verify the whole file from scratch against its
  new post-move location. While that recheck ran, its raw state
  genuinely read `Checking`/`Downloading`, and the code faithfully - but
  misleadingly - echoed that straight through, even though nothing was
  actually being re-downloaded.
- **Fix**: `_monitor_seeding()` now asserts `status='Seeding'`
  unconditionally in its normal (non-paused) branch, matching the same
  "true by definition, every iteration" principle already applied to
  `bytes_done`/`speed_down` in this exact function - closing a gap in
  that same earlier fix rather than a new, unrelated bug. The now-unused
  raw-state lookup table was removed from this function.
- **Verified**: a new test hands `_monitor_seeding()` a fake handle
  reporting raw state 3 (`'Downloading'` in the old map) on a torrent
  already marked `moved_to_plex` and `status='Seeding'` - confirms
  status now stays `'Seeding'` regardless of the underlying recheck
  state (206 tests total, all passing).

## 2026-08-23 - Windows toast said "Download finished" while the progress bar was still healthily moving

- **Symptom**: reported live, immediately after the previous fix - a
  real download with real peers was genuinely progressing normally (the
  on-screen progress bar moving, status correctly showing
  "Downloading"), but the Windows tray fired a "Download finished" toast
  right away anyway.
- **Root cause**: `hydra_tray.py`'s `_check_completions()` fired on
  `status in ('Seeding', 'Finished') or progress >= 100` - an extra,
  uncoordinated fallback on top of `status`. `progress` is written from
  libtorrent's raw `s.progress` on every single poll in
  `_monitor_download()`, independent of whether the daemon's own
  (already-fixed, race-protected) completion check has accepted the
  torrent as genuinely done - so it can transiently read 100 while
  `status` correctly still says `Downloading`. The daemon-side fix above
  made `status`/`bytes_done` the trustworthy signal; this tray check
  never got the same treatment and kept trusting the untrustworthy one.
- **Fix**: `_check_completions()` now fires only on `status in
  ('Seeding', 'Finished')` - the same signal `_monitor_download()`
  itself now guarantees is honest - both for the toast trigger and for
  the "don't re-notify" baseline built when the tray first starts.
- **Verified**: two new tests construct a bare stand-in for the tray's
  notification state (no real pystray icon needed) and drive
  `_check_completions()` directly - one proves a `Downloading` torrent
  with `progress: 100` never fires, the other proves a genuine
  `status: 'Seeding'` transition still fires exactly once (205 tests
  total, all passing).

## 2026-08-23 - A torrent "completed" in milliseconds with nothing actually downloaded

- **Symptom**: reported live, twice, on real torrents with real peers -
  a fresh download showed as fully complete (100% progress, a "Seeding"
  toast) within milliseconds of being added, before any real data could
  possibly have transferred. First traced to genuine leftover files on
  a shared network folder (fixed separately - see the TrueNAS NFS
  mapping note below), but recurred on a completely different torrent
  right after that share was emptied, proving it wasn't only a stale-
  files problem.
- **Root cause**: `set_file_priorities()` (the `/transfers/{name}/files`
  endpoint) sets `file_priorities_set = True` unconditionally after
  calling `handle.prioritize_files()`, regardless of what the resulting
  priorities actually ended up being. `commit_preview()`'s only safety
  net - "if file selection was never explicitly touched, default to
  downloading everything" - only fires when that flag is still `False`,
  so it never catches the case where the flag is `True` but every file
  is still sitting at priority 0. libtorrent's `is_finished`/`is_seeding`
  are true whenever nothing is left that's *wanted* - which includes
  "nothing was ever selected" as well as "everything genuinely
  downloaded" - and the completion branch trusted either as a real
  finish, then force-wrote `bytes_done = total_size` (see the seeding-
  stats fix above), turning "zero bytes downloaded" into a confident
  lie. Confirmed live via the daemon's own stored state: `bytes_done`
  equal to the full file size and `progress: 100.0`, while every entry
  in the real per-piece `pieces` array read `false` and `num_pieces`
  read `0` - the real, synchronous signal directly contradicting the
  reported outcome.
- **Fix**: `_monitor_download()` no longer trusts a completion signal
  on its very first poll at all (success or failure) - `commit_preview`'s
  own fallback correction is itself async into libtorrent and may not
  have landed internally yet a moment later, and real pre-existing data
  can also legitimately read as not-yet-verified on that same first
  pass. From the second poll onward, a reported completion is only
  accepted if `status().total_done > 0` (checked from the same
  synchronous snapshot already used for every other decision in the
  loop, not a second racy `file_priorities()` read-back) - a torrent
  that finished with nothing actually selected now fails loudly
  ("No files were selected for download") instead of reporting fake
  success.
- **Also fixed in the same pass**: `_post_download()`'s Plex-move step
  joined file paths against the frozen module-level
  `DOWNLOAD_DIR_INCOMPLETE` constant instead of the torrent's real,
  possibly-per-torrent `save_path` - the same class of staleness bug as
  the `_default_save_path()` fix a few entries below, just never
  applied to this function. Any download saved to a network share (the
  current default when Plex is configured and reachable) would silently
  never get auto-moved, since the existence check was always run
  against the wrong path. Now reads the live path from
  `handle.status().save_path`.
- **Also fixed, separately (infrastructure, not code)**: the TrueNAS
  NFS export for `/mnt/MainPool/Plex` was mapping all NFS writes to
  `nobody:nogroup` while Windows connects to the same share over SMB as
  `mediauser` - so files created via NFS (e.g. from LXC-side tooling)
  could never be deleted from the Windows side. 47 folders had silently
  accumulated in the shared `incomplete` staging folder since February
  as a result, undeletable by anything on the Windows/SMB side. Fixed
  the export's `mapall_user`/`mapall_group` to `mediauser` (matching the
  already-correct `/mnt/MainPool/media` export) and cleared the
  orphaned folders. Real, but a contributing/confounding factor for the
  first occurrence of this symptom, not the actual root cause -
  the code bug above is what actually mattered, since it reproduced
  again immediately after the share was emptied.
- **Verified**: a new test builds a real two-file torrent, forces both
  files to priority 0 exactly as the real trigger does, and drives it
  through the real `_monitor_download()` loop - confirms it now reports
  `Failed` / "No files were selected for download" with `bytes_done`
  left honest, instead of a fake `Seeding` at 100% (203 tests total,
  all passing, both the libtorrent-integration tier and the
  libtorrent-free main tier).

## 2026-08-23 - A completed, actively-seeding download still showed as barely started

- **Symptom**: reported live - a real download showed "100%" progress
  while simultaneously still reading "Downloading", and once it settled
  into "Seeding" the bytes-downloaded figure stayed frozen at a small
  fraction of the real file size, with a stale nonzero download speed
  still showing next to it.
- **Root cause**: `_monitor_seeding()`'s own `store.update()` calls
  never included `bytes_done` or `speed_down` at all - only upload-side
  stats. Once a torrent finished downloading and handed off from
  `_monitor_download()` to `_monitor_seeding()`, those two fields froze
  forever at whatever they happened to read on the exact final
  iteration before the handoff - which could be well below the real
  total size, since libtorrent's own `total_done` counter can genuinely
  lag its `progress`/`is_seeding` flags by a beat right at the moment
  of completion. Nothing ever corrected it afterward, for the rest of
  that torrent's entire seeding lifetime.
- **Fix**: a seeding torrent is complete by definition, so `bytes_done`
  is set to the real total size and `speed_down` to 0 both at the
  one-time handoff moment and on every subsequent `_monitor_seeding()`
  iteration - not trusting whatever the last snapshot happened to say.
- **Verified**: a new test using a fake handle that simulates exactly
  the broken state found live (frozen low bytes_done, stale nonzero
  speed_down) - confirms `_monitor_seeding()` now corrects both within
  one real loop iteration (202 tests total, all passing).

## 2026-08-23 - Connection Health stayed stale after editing Plex/Jackett/Jellyfin settings, and SAVE AT ignored Plex's actual reachability

- **Symptom 1**: reported live - typed a wrong IP into the Plex URL
  field and saved. Connection Health kept showing "connected" (the
  result from before the edit) until manually clicking Refresh.
- **Root cause 1**: `saveSettings()` never re-triggered a health check
  after a successful save - Connection Health only ever ran on Settings
  tab open or the manual Refresh button, so any edit that changed
  Plex/Jackett/Jellyfin connectivity left the old badge showing
  indefinitely until someone thought to hit Refresh themselves.
- **Fix 1**: `saveSettings()` now calls `loadHealth()` right after a
  successful save.

- **Symptom 2**: with the wrong Plex IP still in effect, opening the
  add-torrent dialog still showed SAVE AT defaulting to the
  Plex-adjacent `\\...\Plex\incomplete` path - even though Plex itself
  was now known to be unreachable, it should have fallen back to the
  plain local downloads folder.
- **Investigation**: `_default_save_path()` (added in the previous
  entry) only checked whether the *filesystem path* was reachable, not
  whether the *media server itself* was - a wrong Plex URL doesn't take
  the underlying SMB/NFS share down with it, so the folder genuinely
  still existed and the old logic correctly (by its own narrower
  definition) kept using it. Added a live `check_plex()`/
  `check_jellyfin()` gate (whichever server is actually configured) on
  top of the filesystem check, with a short 2s timeout so a down server
  doesn't stall opening the add-torrent dialog.
- **A second, more fundamental bug found while verifying the above
  live**: even after fixing the gate, a `PATCH /config` change still
  didn't affect the running daemon's behavior at all until the whole
  app restarted. Root cause: `MEDIA_DIR_MOVIES`/`MEDIA_DIR_TV`/
  `DOWNLOAD_DIR_INCOMPLETE` are read once from `config.py`'s
  module-level constants at daemon startup and never refresh - a
  config change writes correctly to disk, but the already-running
  process keeps computing from its stale in-memory value indefinitely.
  `_default_save_path()` now reads all of this straight from
  `load_config()` on every call instead, exactly like `plex_url`/
  `plex_token` already did - proven live by changing Plex's URL and
  the download path back and forth on the *same* running process with
  no restart in between, both times reflected correctly and
  immediately.
- **Verified**: 4 new/rewritten unit tests (201 total passing,
  including a dedicated staleness-regression test that deliberately
  sets the module constants to a wrong answer to prove the function
  ignores them) plus multiple rounds of live testing against a real
  running daemon and the real TrueNAS share, flipping Plex between a
  working and a broken URL on the same process without restarting.

## 2026-08-23 - SAVE AT defaulted to local downloads even with a real, working Plex library configured

- **Symptom**: reported live, after finally getting a real, verified,
  reachable Plex library path configured on Windows (a whole separate
  SMB-access saga - see the entries below). The add-torrent dialog's
  "SAVE AT" field still defaulted to the plain local downloads folder
  every time, completely ignoring that a real Plex path was now
  properly set up and confirmed reachable.
- **Root cause**: `GET /disk-space` (which supplies the SAVE AT
  default) always defaulted to `DOWNLOAD_DIR_INCOMPLETE`
  unconditionally - there was no path from "Plex is configured and
  reachable" to "use its sibling incomplete folder as the real
  default." `DOWNLOAD_DIR_INCOMPLETE` was acting as the default every
  time, when it's meant to be the fallback specifically for "Plex
  isn't set up, or its path isn't reachable from this machine."
- **Fix**: added `_default_save_path()` - checks whether
  MEDIA_DIR_MOVIES/MEDIA_DIR_TV are configured AND have a genuinely
  reachable sibling "incomplete" folder (same mount = an instant
  rename instead of a slow cross-filesystem copy once the download
  completes), using that as the real default. Falls back to
  DOWNLOAD_DIR_INCOMPLETE exactly when it should: library not
  configured, or its path/sibling folder isn't actually reachable from
  this machine.
- **Verified**: 5 new unit tests (197 total passing) plus a live,
  unmocked run of the real production function against this desktop's
  actual live config and a real API round-trip against the running
  daemon - confirmed it correctly returns the real
  \\\\192.168.1.4\\Plex\\incomplete path with real free-space numbers
  from the real share.

## 2026-08-23 - File checkboxes stayed live-editable after a torrent was already downloading, and the Windows toast repeated the false "Plex" claim

- **Symptom**: reported live, right after the file-selection race fix
  above shipped. Two things: (1) opening the FILES panel on an
  already-downloading transfer still showed clickable checkboxes for
  every file, including ones excluded before commit - implying you
  could still change your mind about them there, when in fact ticking
  one back on did nothing meaningful for a file that was never part of
  the active download; (2) the Windows toast notification for a
  finished download said "Moved to Plex Movies" even though the file
  had genuinely fallen back to the general downloads folder - the
  exact same false claim as the on-screen "→ Plex:" label fixed
  earlier tonight, just in a second, separate place.
- **Root cause (1)**: `_filesHtml()` is one shared renderer used both
  by the pre-commit add-dialog (where ticking/unticking is a real,
  meaningful choice) and the post-commit Transfers-tab file panel
  (where the download has already started and priorities are fixed) -
  with no distinction between the two contexts.
- **Root cause (2)**: `hydra_tray.py`'s `_fire_notification()` decided
  the toast wording from `plex_path` alone - which, same as the
  on-screen label bug, is set for both a genuine organized move and
  the safe fallback. It should have checked `moved_to_plex` (already
  fixed on the backend earlier tonight to correctly distinguish the
  two) instead.
- **Fix**: `_filesHtml()` now takes an `editable` flag - `true` for the
  add-dialog (unchanged), `false` for the post-commit view, which
  drops the checkboxes entirely and only lists files that were
  actually included, exactly matching what's really downloading. The
  tray notification now checks `moved_to_plex`, matching the frontend
  fix.
- **Verified**: extracted and ran the real, updated `_filesHtml()`
  function against the exact real 3-file scenario from tonight's bug
  report - confirmed the read-only view shows only the real movie
  file, no checkboxes, and the excluded poster/NFO files aren't
  present at all.

## 2026-08-23 - Unchecked files in the add-torrent dialog downloaded anyway

- **Symptom**: reported live - unticked the poster/NFO files in the
  file-selection dialog before adding a torrent, only the video file
  was meant to download, but all the files downloaded anyway.
- **Root cause**: a genuine race condition, not a UI bug. The frontend
  correctly sends the user's real file selection to
  `POST /transfers/{name}/files` before committing, but `handle.
  prioritize_files()` is a fire-and-forget async call into libtorrent's
  own internal engine - nothing guarantees the change has actually
  landed by the time anything reads it back. `commit_preview()` had a
  safety net for "the frontend never explicitly touched file
  selection" that worked by re-reading `handle.file_priorities()` and
  resetting everything to download if it read back as all-zero. When
  the async update genuinely hadn't landed yet, that looked
  indistinguishable from "never touched" and silently overrode the
  user's real exclusions back to "download everything."
- **Fix**: track our own explicit `file_priorities_set` flag (set
  synchronously, in Hydra's own application state, the moment
  `POST /files` is called) instead of re-querying libtorrent's
  possibly-stale internal state. `commit_preview()`'s "default to
  everything" fallback now only fires when that flag was never set at
  all, not based on what libtorrent's engine happens to report at an
  arbitrary moment.
- **Verified**: two new real libtorrent integration tests - one proving
  an explicit all-zero selection is respected even when read back as
  all-zero (the exact condition the old code misread), one proving the
  "never touched, default to everything" fallback still works when the
  flag was genuinely never set.

## 2026-08-23 - "Moved to Plex" was shown even when the file never touched Plex

- **Symptom**: reported live, same session as the media-path fix below
  - a download correctly fell back to the general downloads folder
  (Plex library path not configured), but the UI still displayed "→
  Plex: C:\...\Downloads\HydraTorrent" - a plain false claim, since
  Plex was never actually involved, no scan was triggered, nothing was
  organized.
- **Root cause**: `auto_move_completed_download()` reports success
  (`True, dest_path, None`) for both a genuine Plex/Jellyfin-organized
  move and the safe general-downloads fallback - the caller had no way
  to distinguish them from the return value alone, so the UI
  unconditionally labeled every successful move as a Plex move.
- **Fix**: the two destinations are structurally guaranteed to be
  different roots (`MEDIA_DIR_MOVIES`/`MEDIA_DIR_TV` for a real
  organized move, `DOWNLOAD_DIR_OTHER` for the fallback) - added
  `_is_under_library_root()` to check which root the real destination
  actually landed under, and only report `moved_to_plex=True` when
  it's genuine. The frontend now shows an honest "→ Moved:" label
  (instead of "→ Plex:") for a fallback move, so the destination is
  still visible, just not falsely attributed to Plex.
- **Verified**: three new unit tests covering the real-library,
  fallback, and nothing-configured cases.

## 2026-08-23 - A misconfigured media path silently fabricated a bogus folder instead of erroring

- **Symptom**: reported live - a completed download reported "Moved to
  Plex" successfully, but the actual video file wasn't anywhere Plex's
  real library could see it.
- **Root cause**: `media_dir_movies`/`media_dir_tv` was set to a
  Linux-style NFS path (`/mnt/plex/movies` - correct on the Linux/LXC
  deployment, meaningless on Windows). Windows silently reinterpreted
  the leading `/` as "root of the current drive" and
  `organize_movie()`'s `os.makedirs(..., exist_ok=True)` happily
  fabricated a brand-new `C:\mnt\plex\movies\...` folder that never
  existed before, moved the real file there, logged "Moved OK", and
  even triggered a Plex library scan that predictably found nothing.
  This isn't just a Windows/Linux path-syntax bug - the same silent
  fabrication would happen for any misconfigured, unreachable, or
  temporarily-unmounted media path, on any platform.
- **Fix**: a real Plex/Jellyfin library already exists - Hydra should
  never be the thing that first creates it. `move_to_plex()` now
  requires the configured library root to already exist
  (`os.path.isdir`) before organizing into it; if it doesn't, the file
  falls back to the same safe general-downloads path used for an
  unconfigured library, instead of silently inventing a new folder
  tree in a location nobody would think to check.
- **Verified two ways**: two new unit tests covering the exact
  scenario (183 tests total, all passing), plus a live, unmocked,
  in-process run of the real production `move_to_plex()` function
  against a genuinely fresh, never-existed path and a real file on
  disk - confirmed it correctly falls back and does not fabricate the
  bogus path. Also surfaced a real, worth-noting limitation live: a
  machine already "poisoned" by the old bug (a bogus folder the old
  code already created) needs manual cleanup for this protection to
  be fully restored there, since the fix can't distinguish an
  old-bug-fabricated folder from a genuine library after the fact.

## 2026-08-23 - Add-torrent file list opened with every checkbox unchecked

- **Symptom**: reported live - opening the file-selection dialog for a
  well-seeded real result showed every file's checkbox unchecked, no
  matter how many files the torrent had.
- **Root cause**: `_loadModalFiles()` correctly computed a "default to
  checked" correction into `_fileData` (backend preview torrents
  genuinely sit at real priority 0 for every file - that's what stops
  real data downloading before commit, not a user choice), but then
  rendered the dialog from the original *uncorrected* API response
  instead of the corrected data it had just computed - the correction
  was calculated and then never used.
- **Fix**: one-line swap to render from the corrected `_fileData`.
  Verified against a real search result and a real live preview
  fetched from the running daemon (not synthetic data) - extracted the
  actual production `_filesHtml` function and ran it against the real
  API response to confirm it renders `checked`.

## 2026-08-23 - Internal Python exceptions leaked raw into the add-torrent dialog

- **Symptom**: reported live - adding a torrent failed with "Could not
  fetch metadata: 'charmap' codec can't encode character '→' in
  position 97: character maps to &lt;undefined&gt;" shown directly in the
  UI, from a torrent whose real metadata had actually already been
  fetched successfully.
- **Investigation**: traced every direct print/encode/file-write path
  in the actual preview/search code and ruled all of them out - none
  touch the Windows console codepage (cp1252, the "charmap" codec)
  that this specific error implicates, and Python's logging module
  swallows encoding failures inside a log call by default rather than
  propagating them (confirmed by testing both directly). Could not
  pin the exact single call site without the original torrent that
  triggered it.
- **Real fix, addressing the actual design flaw**: both
  `/transfers/preview` and `/search` had `except Exception as e: raise
  HTTPException(..., detail=str(e))` - meaning *any* unexpected
  internal exception, whatever it happened to be, got shown raw to the
  user. That's the real bug, independent of this one trigger. Both now
  log the full exception server-side and show a clean message instead.
  Also hardened the console log handler in config.py
  (`stream.reconfigure(errors='replace')`) as defense-in-depth, since
  cp1252 is the most likely actual origin given the error's shape.

## 2026-08-23 - Startup banner still advertised /docs after it was disabled

- **Symptom**: found doing a full re-verification fresh-install pass on
  v1.4.10 across all 3 platforms before calling it done - the Docker
  container's startup log printed `API docs: https://.../docs`, but
  that URL correctly returns 404 (disabled in the earlier application-
  security hardening pass). Not a functional bug, just misleading -
  telling a user a URL exists that doesn't.
- **Fix**: removed the stale print line.
- **How it was caught**: another deliberate fresh-install verification
  pass (Docker + native Linux + Windows, all real installs from a
  clean state) run specifically because the favicon revert bumped the
  version - same discipline as the v1.4.5 pass, not a one-off.

## 2026-08-23 - Reverted the favicon contrast/crop fix

- The 2026-08-23 favicon fix below (crop + contrast boost + unsharp
  mask) technically improved legibility at true tab-icon size, but on
  review the tradeoff wasn't worth it - Matth preferred the original
  icon's look over the sharpened one. Reverted `static/index.html`
  back to the original embedded favicon. The original's low legibility
  at 16-32px (documented below) is still real, just an accepted
  tradeoff rather than something being actively fixed right now.

## 2026-08-23 - Browser tab favicon was a muddy blob next to every other tab

- **Symptom**: reported by Matth comparing his browser's tab strip -
  every other site's icon (YouTube, GitHub, etc.) read cleanly at tab
  size; Hydra's was a near-uniform dark green smear with no visible
  shape.
- **Root cause**: not a broken image (the PNG was valid, correctly
  embedded, correctly served) - the source `logo.png` is a detailed
  illustration (individual eye dots, spike edges, scale ridges) at
  fairly low contrast against its own background circle. Confirmed by
  extracting and viewing the actual shipped 32x32/16x16 PNGs at true
  size: the fine detail that makes the full logo look good simply has
  nowhere to go at 16-32 real pixels and collapses into mud. This is
  a fundamentally different problem than the earlier 2026-08-21
  favicon fix, which was about transparency/corruption, not legibility.
- **Fix**: regenerated both sizes from the source logo with a modest
  crop toward the head (the original canvas wastes real pixels on
  background around the circle), a contrast/saturation boost applied
  *before* downsizing (so surviving detail has more separation once
  shrunk), LANCZOS resampling, and a post-resize unsharp mask to fight
  the softening LANCZOS itself introduces - the 16px version needed a
  noticeably stronger pass than 32px since it has a quarter of the
  pixels to represent the same shape. Verified by extracting and
  viewing the new PNGs at true size against the old ones side by side -
  visibly higher contrast and a legible head shape at both sizes.

## 2026-08-22 - Windows: reopening a hidden window via the desktop shortcut silently did nothing

- **Symptom**: reported live by Matth testing on his own machine - open
  Hydra, close it with the titlebar X (hides to tray, doesn't quit),
  double-click the desktop shortcut again expecting it to reopen -
  nothing visibly happened.
- **Investigation note**: the first diagnosis (DWM cloaking, confirmed
  via `DwmGetWindowAttribute`/`DwmSetWindowAttribute` returning
  E_ACCESSDENIED cross-process) turned out to be checking the *wrong*
  window - `FindWindowW(None, "Hydra Torrent")` had matched an
  unrelated `explorer.exe` window that happened to share the title.
  Re-tested properly by enumerating all windows and confirming PID
  ownership, then faithfully reproducing the actual close by calling
  the real `pywebview.api.close()` via a CDP connection to the
  WebView2 instance (not a synthetic WM_CLOSE, which doesn't go
  through the same path a frameless window's custom close button
  does). The real mechanism is a plain visibility toggle, not
  cloaking - and the original raw `ShowWindow`/`SetForegroundWindow`
  restore *did* successfully restore visibility in that corrected
  test. What it couldn't be shown to do reliably is bring the window
  to the foreground/on top of whatever else was on screen -
  `SetForegroundWindow` called by an unrelated external process is a
  well-known unreliable API on Windows, succeeding at the call level
  while the OS still declines to actually hand over focus. That
  matches "reappeared invisibly behind other windows" far better than
  "stayed hidden."
- **Fix**: instead of a second, short-lived process trying to
  manipulate the first process's window handle from outside (fragile
  regardless of the exact mechanism), the second process now asks the
  *first* process to show its own window from the inside - a new
  authenticated `POST /window/show` endpoint on the daemon calls the
  same `_open_callback` the tray icon's "Open" menu item already uses
  reliably. Verified end-to-end: hidden the real window via the real
  close call, hit the new endpoint, watched it become visible again.
- **Also caught in the same testing pass**: a debug flag set during
  this investigation (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-
  debugging-port=...`) had overwritten rather than added to the
  existing `--force-color-profile=srgb` fix for WebView2's duller
  colors vs. a real browser tab, on the one leftover test process -
  not a real regression, just testing residue, resolved by killing
  that process and relaunching clean.

## 2026-08-22 - Application-security pass: disabled auto API docs, hardened torrent file-move paths

- **Not a report** - a deliberate application-security review, run after
  the git-history/leaked-secrets audit earlier the same day was closed
  out, specifically checking whether the *running* daemon (not just the
  repo) could be abused by someone who found the public source.
- **Found**: FastAPI's default `/docs` and `/openapi.json` were live and
  unauthenticated - anyone who could reach the port got the full
  endpoint/schema map for free, no API key needed. Separately, the
  per-file path used when moving a completed torrent's files came
  straight from the torrent's own declared file list - attacker-
  controlled via a malicious .torrent/magnet - with no containment
  check at this layer, relying entirely on libtorrent's own path
  sanitization rather than anything Hydra itself verified.
- **Fix**: disabled `/docs`/`/openapi.json`/`/redoc`
  (`FastAPI(docs_url=None, ...)`). Applied the same realpath + "still
  under the download dir" containment check `/static/{filename}`
  already used elsewhere in this file to the torrent-file-path join, as
  defense-in-depth independent of what libtorrent guarantees.
- **Also reviewed, found clean**: auth coverage on every other route,
  the WebSocket's own auth handshake, injection surface, SSRF guard
  coverage across every attacker-reachable fetch, CORS, log redaction
  completeness, frontend XSS escaping, the update-checker's trust model,
  and cert pinning.
- **Noted but left as-is (a design tradeoff, not a bug)**: `/ui` embeds
  the live API key into the page it serves, unauthenticated, and
  `/download/tray` hands back a config zip containing it - both by
  design, so the browser UI doesn't need a manual key-entry step. The
  real security boundary for this daemon is network reach (LAN/VPN
  only, never port-forward it to the WAN), not the API key itself.

## 2026-08-22 - v1.4.5 Windows .exe couldn't start the daemon at all

- **Symptom**: found running the actual published v1.4.5 release on real
  Windows hardware, as part of the same fresh-install testing pass that
  caught the install.sh bug below. The app opened to a blank
  "127.0.0.1 refused to connect" page every time - no error, no dialog,
  nothing in the log beyond a clean-looking startup.
- **Root cause**: `ssrf_guard.py` (added earlier this session for the
  Jackett SSRF-guard fix, see the entry below) was never added to
  `HydraTorrent.spec`'s `datas` list, so it was missing from the built
  .exe entirely. `hydra_daemon.py` is loaded by `hydra_app.py` via a
  string import (`uvicorn.run("hydra_daemon:app", ...)` on a background
  thread) specifically so it can be swapped/reloaded - PyInstaller can't
  trace that kind of dynamic import via static analysis, so every module
  `hydra_daemon.py` pulls in has to be hand-listed in `datas` or it's
  silently left out of the build. `ModuleNotFoundError: No module named
  'ssrf_guard'` killed the daemon thread instantly, with no handler to
  catch or log it - the main thread waited out its 15s timeout for a
  daemon that was never coming, then opened the window anyway.
- **Fix**: added `ssrf_guard.py` to the `datas` list, cross-checked every
  other local import in `hydra_daemon.py` against that same list (nothing
  else missing), rebuilt, and confirmed the rebuilt .exe actually starts
  end-to-end on real hardware. Shipped as v1.4.6 rather than silently
  replacing the v1.4.5 asset, since real users may already have it.
- **How it was caught**: same deliberate fresh-install pass as install.sh
  below - confirmed on the real machine that hit it, by running the
  actual installed .exe with stdout/stderr properly captured instead of
  hidden, which surfaced the real traceback.

## 2026-08-22 - install.sh was completely broken on Ubuntu 24.04

- **Symptom**: found doing a genuine fresh-install test on a clean VM
  before considering the project done, not a report. `sudo bash
  install.sh`, following the README exactly, died silently partway
  through a wall of pip output with no systemd service ever created.
- **Root cause**: the pip install line already tried
  `--break-system-packages` first (for PEP 668, "externally managed
  environment"), falling back to a plain `pip install` if that failed -
  but the first attempt was failing for an unrelated reason: Ubuntu
  ships its own apt-packaged `typing_extensions` with no pip RECORD
  file, so pip can't safely upgrade it even with the flag ("Cannot
  uninstall typing_extensions 4.10.0, RECORD file not found: The
  package was installed by debian"). The fallback then hit PEP 668
  itself, since dropping the flag makes pip refuse outright on 24.04.
  Both paths failed, for different reasons - `set -e` aborted the
  whole script before it ever reached systemd service creation.
- **Fix**: install dependencies into a dedicated venv instead of
  fighting system Python at all - the fix pip's own error message
  recommends, and it sidesteps this whole class of conflict rather
  than working around one specific package. Also had to explicitly
  check for `python3-venv`: it's a separate apt package Debian/Ubuntu
  doesn't install by default, and the script's only existing install
  of it lived inside the "python3 not found" branch - which never
  runs on a completely normal system that already has python3.
  Confirmed live this would have been a second, immediate failure
  right after "fixing" the first one, before shipping the fix.
- **How it was caught**: a deliberate fresh-install test across
  Windows/Linux/Docker on a genuinely clean VM, done before calling
  the project finished - not triggered by a user hitting it.

## 2026-08-22 - SSRF guard blocked legitimate Jackett download links on any private-LAN Jackett

- **Symptom**: found by accident while capturing real footage for a demo
  GIF, not a report - a well-seeded (40 real seeders), completely legal
  a public tracker result failed to add with "Could not fetch metadata:
  Refusing to fetch '...': resolves to 192.168.1.40, an internal/private
  address."
- **Root cause**: the SSRF guard added earlier this session (see the
  2026-08-20 entry two below) was applied uniformly to both
  `/transfers/preview` and `/transfers/url`, as if either could receive a
  genuinely arbitrary URL. Traced the real call path: `/transfers/preview`
  (the Search tab's "double-click for details" flow) only ever receives a
  URL Jackett itself returned, for the server the user configured in
  Settings - the same already-trusted-input category `search.py`'s
  `search_jackett()` already gets a pass on, per the guard's own docstring.
  It just wasn't actually applied here. Net effect: any indexer that
  returns a `.torrent` proxy download link instead of a direct magnet URI
  (a public tracker, confirmed live) was silently unusable for anyone whose
  Jackett runs on a private LAN IP - which is nearly every self-hosted
  deployment, including the one this was found on.
- **Fix**: `ensure_safe_fetch_url()` takes an optional `trusted_hosts` set
  (exact host:port matches only), built from the user's own configured
  `indexing_server` via a new `trusted_host_from_url()` helper. A
  different private address is still rejected - the exemption doesn't
  blanket-trust every private IP, only the one the user actually
  configured as their indexer.
- **How it was caught**: not a bug report - making an honest demo GIF of
  the real search-to-download flow hit it live on the first attempt with a
  result that didn't provide a magnet link. Re-verified the exact same
  result after the fix: metadata fetch succeeded, a real 5.3GB download
  completed end-to-end in under 3 minutes.

## 2026-08-22 - Torrent errors while seeding were never surfaced, only while downloading

- **Symptom**: audit item #2 from the same proactive pass that found the
  resume-data gap. The audit's own framing turned out to overstate it -
  worth recording precisely because it shows why "verify against the code"
  matters even when the source list looks credible.
- **What the audit claimed**: "nothing pauses a torrent or surfaces a clear
  error when libtorrent hits a write failure mid-download."
- **What was actually true**: `_monitor_download()` already checked
  `status().error` every 2-second poll and correctly marked the torrent
  Failed with the real error message - this part of the claim was wrong.
  `_monitor_seeding()`, however, never checked it at all - a disk error
  *after* completion (files deleted out from under a seeding torrent, the
  disk goes read-only, etc.) just kept reporting upload stats forever with
  nothing surfaced anywhere. Real gap, just a narrower and different one
  than described.
- **Fix**: mirrored `_monitor_download()`'s existing error-check block into
  `_monitor_seeding()` - log, mark Failed with the real message, detach the
  handle, stop the loop.
- **How it was caught**: verified directly against the real
  `_monitor_seeding()` method with a stub handle reporting a genuine error
  signal, confirming the store transitions to Failed and the monitor
  thread exits. First tried reproducing an actual disk failure by deleting
  a seeding torrent's files on disk - libtorrent just re-downloads into the
  recreated directory instead of erroring, so that path doesn't reproduce
  this at all; noted here so it's not attempted again next time.

## 2026-08-22 - Every daemon restart re-hashed every in-progress torrent from scratch

- **Symptom**: found via a proactive audit (cross-referencing high-impact
  bug reports from qBittorrent/Transmission against Hydra's own code,
  rather than waiting for a user to hit it first), not a bug report -
  confirmed directly in code before doing anything about it: zero
  references to `save_resume_data`/`read_resume_data` anywhere in
  `hydra_daemon.py`. `_resume_persisted()` re-added every torrent with just
  `url`/`save_path`/`storage_mode`, no resume state, and `shutdown()` never
  asked libtorrent to save any either.
- **Root cause**: libtorrent has no way to know which pieces are already
  verified after a restart unless it's told - the same failure class
  behind qBittorrent's worst startup-time complaints, and it hit Hydra
  specifically hard since the daemon gets restarted routinely for every
  deploy.
- **Two gotchas found building the fix, both confirmed against the real
  installed libtorrent (2.1.1.0) rather than assumed**:
  1. `storage_notification` isn't in libtorrent's default `alert_mask` -
     without explicitly requesting it, `save_resume_data_alert` is
     silently dropped and the whole thing looks like it works while saving
     nothing.
  2. `save_resume_data()`'s output never includes torrent metadata (file
     list/piece hashes), only piece-verification state. Every torrent here
     is magnet-added, so restoring from resume data alone left it stuck in
     `downloading_metadata` forever - reproduced this directly in a live
     test before fixing it. Fix required separately caching each handle's
     metadata alongside its resume data, and re-merging trackers from the
     stored magnet URI on restore (resume data doesn't restore those for a
     magnet-only add either).
- **Fix**: session now requests `storage_notification | error_notification`
  in its alert mask; `_save_resume_data()` saves resume state + metadata
  for every live torrent on shutdown; `_resume_persisted()` reconstructs
  both and merges trackers back in. A torrent already auto-moved to
  Plex/Jellyfin is excluded (its libtorrent-side resume state still points
  at the pre-move path, since media_organizer does a plain filesystem
  move, not `handle.move_storage()` - re-verifying at the new location was
  already correct there). A corrupt/incompatible resume blob falls back to
  the original plain magnet re-add instead of failing the torrent.
- **How it was caught**: not a user report - a proactive audit prompted by
  the "should Hydra have a bug-report button" conversation, which led to
  researching what actually breaks in other torrent clients and checking
  Hydra's own code against the same failure classes before anyone hit them.
  Verified end-to-end against the real `DaemonStore`/`TorrentEngine`
  classes with a synthetic local torrent (save -> simulated restart ->
  restore -> immediately seeding). Not added to the pytest suite - CI
  deliberately doesn't install libtorrent, so an import-level test would
  turn CI red for infrastructure reasons, not code ones; that's a separate
  decision, not bundled into this fix.

## 2026-08-21 - Browser tab showed a generic default icon instead of Hydra's logo

- **Symptom**: raised as "why is my cursor... wait, why does the tab just
  show a plain default icon instead of an app icon, like Frigate/YouTube/
  Claude have?" - a real, present-since-the-icon-was-added bug, not a missing
  feature.
- **Root cause**: the `<link rel="icon">` data URI's base64 PNG never
  actually decoded - confirmed with PIL: `OSError: broken data stream when
  reading image file`. Every browser silently fell back to its own generic
  page icon; nothing in the app surfaced the failure, so it just looked
  unbranded and nobody noticed until someone put Hydra's tab next to a few
  others and compared.
- **Fix**: replaced it with two properly generated PNGs (32x32, 16x16)
  downscaled from the real `static/logo.png` - the same hydra head already
  used for the desktop titlebar icon, so the browser tab and the desktop app
  now show the same mark. Shipped both sizes explicitly rather than one,
  since the full logo's fine detail (multiple eyes, scale texture) turns to
  an indistinct blur at true 16px - confirmed by rendering the source logo
  down to 16/32/48px before picking sizes, not by guesswork.
- **How it was caught**: a user comparing Hydra's browser tab against other
  open tabs (Frigate, YouTube, Claude) and noticing it had no real icon.

## 2026-08-21 - Movies/TV auto-move defaulted to a hardcoded personal NAS path; no path existed for non-video downloads at all

- **Symptom**: raised as a product question ("shouldn't non-Plex users have a
  normal download path selector? what happens if a Plex/Jellyfin user
  downloads an ISO or MP3?") rather than a bug report, but pulling the thread
  found two real issues in the same code path.
- **Root cause #1**: `MEDIA_DIR_MOVIES`/`MEDIA_DIR_TV` defaulted to a
  hardcoded `\\192.168.1.4\Plex\...` UNC path (this machine's own home NAS)
  whenever `media_dir_movies`/`media_dir_tv` weren't set in
  `hydra_config.json`. Auto-move defaults to on, so every user who installed
  Hydra without ever configuring Plex/Jellyfin still had every completed
  video attempt an `os.makedirs()` against that unreachable path - caught,
  not crashing, but a real network-timeout stall on every single download
  completion. Already flagged once before in `tests/conftest.py`'s comments
  (routed around for CI, never fixed at the source).
- **Root cause #2**: `download_dir_other` (the destination for non-video
  completions like ISOs/music/archives) existed in `media_organizer.py` and
  `config.py` but was never added to `_EDITABLE_CONFIG_KEYS`, `ConfigResponse`,
  or `ConfigUpdate` - dead, unreachable config with no way to ever set it.
  Non-video files just stayed in the download/incomplete folder forever,
  silently, for every user regardless of Plex/Jellyfin setup.
- **Fix**: `MEDIA_DIR_MOVIES`/`MEDIA_DIR_TV` now default to `""`. Wired
  `download_dir_other` all the way through (editable config key, both
  Pydantic models, `/config` GET/PATCH) and added a real "Other downloads
  path" field to the Downloads settings section, with the destination folder
  now auto-created via `os.makedirs(..., exist_ok=True)` instead of requiring
  it to pre-exist. `move_to_plex()` now falls back to this general folder
  (flat move, no library scan) for anything it can't file into Plex/Jellyfin -
  a non-video file, or a video with no configured library path for its type -
  instead of leaving it stranded in the download folder. That fallback is
  what makes the field work as a real "normal torrent client, no media
  server" download folder too, not just a dumping ground for non-video files.
- **How it was caught**: a user question about the gap between "people with
  Plex/Jellyfin wired up" and "people who just run Hydra as a normal torrent
  client" - not a report of the NAS-path issue directly, but the same code
  path turned out to hide both problems.

## 2026-08-20 - Status strip showed "0 transfers / No active transfers" during real downloads

- **Symptom**: a fresh page load could show a genuinely active, 67%-complete
  download with 79 real peers in the Transfers list, while the header
  directly above it read "0 transfers - 0 peers connected" and the map panel
  said "No active transfers" - found live while capturing screenshots for
  the README, not from a bug report.
- **Root cause**: the transfer list itself (`renderTransfers()`) updates
  immediately from every WebSocket push, but the status-strip count and the
  "No active transfers"/"Idle" label only updated on `refreshPulseMap()`'s
  own independent 5-second timer - a real, contradictory gap between what
  the list showed and what the header claimed, not just cosmetic staleness.
- **Fix**: split the free part (count + label, derived from `_lastTransfers`
  with no network call) into `updateStatusStripCounts()`, called
  synchronously every time `renderTransfers()` runs. The expensive part
  (fetching real per-transfer peer lists for the map, a genuine network call
  per active transfer) stays on the slower timer - only the misleading
  count/label needed to move.
- **How it was caught**: capturing a real screenshot of an active download
  for the README, then noticing the header didn't match the list right next
  to it in the same frame.

## 2026-08-20 - Settings' own API Key field wasn't masked, unlike its neighbors

- **Symptom**: the Settings page's "API Key" field - the credential that
  gates every single endpoint on the daemon - rendered as plain `type="text"`
  with only a Copy button, while Plex Token and Jackett API Key right next
  to it were both password-masked behind a Show toggle.
- **Root cause**: simple inconsistency between fields added at different
  times; nobody had compared the API Key field against its neighbors.
- **Fix**: changed to `type="password"` with the same Show/Hide toggle as
  the other two secret fields.
- **How it was caught**: taking a real Settings screenshot for the README
  leaked the actual live API key in plain text into the captured image.
  Rotated the key on LXC 108 immediately, fixed the masking, retook clean.

## 2026-08-20 - Search is Jackett-only

- Removed the built-in public search mode. Hydra now searches only through your own Jackett instance (and the optional index server), so every source is one you configure yourself.
- The "Public" option is gone from the Search tab, and the HTML-parsing dependencies it needed were dropped from the requirements files.

## 2026-08-20 - CI silently red for 4 days: `subprocess.CREATE_NO_WINDOW` on Linux

- **Symptom**: GitHub Actions `Tests` workflow showed `INTERNALERROR` /
  `NotImplementedError: cannot instantiate 'WindowsPath' on your system` -
  looked like a pytest-internals bug, gave no hint of the real cause.
- **Root cause**: `tests/test_vpn_guard.py` monkeypatches `os.name = "nt"` to
  exercise the Windows-only code path in `vpn_guard.py`. That path referenced
  `subprocess.CREATE_NO_WINDOW` directly - a constant that only exists in the
  real `subprocess` module on actual Windows. On the Ubuntu CI runner this
  raised `AttributeError` mid-test, while `os.name` was still patched to
  `'nt'` (monkeypatch reverts at teardown, which never got reached). pytest's
  own failure-reporting code then tried to build a `Path(os.getcwd())` to
  format the traceback - and with `os.name` still `'nt'` at that instant,
  that resolved to `WindowsPath`, which refuses to instantiate on a
  non-Windows OS. That secondary crash is what actually surfaced in the logs.
- **Fix**: `getattr(subprocess, 'CREATE_NO_WINDOW', 0)` in `vpn_guard.py`
  instead of a direct attribute access; test now injects the constant via
  `monkeypatch.setattr(..., raising=False)` instead of relying on it existing
  on the host OS.
- **How it was caught**: not by the tripwire itself - by Matth asking whether
  a tripwire existed at all, which led to checking `gh run list` and finding
  every run since 2026-08-16 (v1.4.1 through v1.4.3, three releases) had been
  failing with nobody notified. This incident is *why* the Discord CI-failure
  webhook (same day) exists - a caught failure that nobody sees is barely
  better than an uncaught one.

## 2026-08-20 - Rate limit "not applying" was actually a display bug

- **Symptom**: reported as "download rate limit doesn't apply live."
- **Root cause**: the rate limit genuinely was being applied correctly the
  whole time (`apply_settings()` on the live libtorrent session, confirmed
  via journalctl and three isolated raw-libtorrent reproductions, plus a real
  2-minute sustained download matching the configured cap almost exactly).
  The actual bug was cosmetic: `speed_down`/`speed_up` in `_monitor_download`
  / `_monitor_seeding` computed `delta_bytes / delta_time` with no floor on
  `delta_time`, so an occasional near-zero interval between stat polls
  produced spurious 100+ MB/s readings that looked like the limit wasn't
  holding.
- **Fix**: minimum 0.5s interval guard in both monitor loops; falls back to
  the last-known speed instead of a fresh (and possibly spurious) calc.
- **How it was caught**: live sustained-download test, not just code review -
  the code path that was actually broken (`hydra_daemon.py`'s stat-delta
  math) was nowhere near the code path that was originally suspected
  (`apply_rate_limits()`).

## 2026-08-20 - Delete-with-files left the real file on disk

- **Symptom**: removing a torrent with "delete files" checked reported
  success, but the media file was still sitting on disk afterward.
- **Root cause**: `media_organizer.py` moves completed files with a plain
  `shutil.move()`, never calling libtorrent's own `handle.move_storage()`.
  libtorrent's internal bookkeeping still pointed at the original
  (now-empty) download path, so `remove_torrent(handle, delete_files)`
  deleted nothing real and reported success anyway.
- **Fix**: track the exact destination of every moved file (`moved_files` on
  the transfer record) and delete those real paths directly in
  `TorrentEngine.remove()`; only remove the containing folder if it's
  actually empty afterward (never risks a TV show's shared season folder).
- **How it was caught**: a real download -> move -> delete-with-files cycle
  against live infrastructure, confirmed by reading the raw `transfers.json`
  on disk over SSH (the `/transfers/{name}` API response filters the
  `moved_files` field out, so the API alone made the fix look broken when it
  wasn't).

## 2026-08-16 - `install.sh` failed on a fresh box if cloned into `/root`

- **Symptom**: fresh clean-room install (disposable Proxmox LXC) failed at
  systemd startup with `Changing to the requested working directory failed:
  Permission denied` (exit code 200/CHDIR) - no indication of the real cause.
- **Root cause**: the installer ran fully in place, wherever it was cloned.
  `/root` is `700` by default, which blocks the restricted `hydra` service
  user from even traversing into it - regardless of the cloned folder's own
  permissions.
- **Fix**: always install to a fixed `/opt/hydra`, copying from wherever the
  repo was actually cloned.
- **How it was caught**: a genuine disposable clean-room test (spin up a
  throwaway LXC, follow only the public README, no manual fixes) - not
  something a code review of `install.sh` in isolation would have surfaced,
  since it only breaks depending on *where* someone happens to clone it.

## 2026-08-16 - Plex/Jellyfin path mismatch when the media server runs in Docker

- **Symptom**: auto-detected library paths from a Dockerized Plex (e.g.
  `/data/movies`) don't match the real host path Hydra needs to write to
  (e.g. `/mnt/plex/movies`) - not a Hydra bug exactly, but a real, common
  environment gotcha with no warning if left unhandled.
- **Root cause**: a containerized app reports paths as seen *inside its own
  container filesystem namespace*. That only happens to match the host path
  if the querying app (Hydra) is dockerized too, with identical bind mounts.
- **Fix**: `GET /check-path` verifies a detected/typed path actually resolves
  on the host before trusting it, with a clear inline warning instead of
  silent failure. Documented per-scenario fixes in the README (native,
  NAS/remote, mixed-Docker, fully-Dockerized-with-matching-mounts).
- **How it was caught**: testing the new Plex-autodetect feature against
  LXC 108's real Plex - which turned out to be Docker-in-LXC, not native,
  surfacing the exact mismatch the feature needed to handle.

---

Earlier history (pre-2026-08-16) lives in `git log` - this file starts from
the point real root-cause detail was captured at fix time, rather than
reconstructed after the fact.
