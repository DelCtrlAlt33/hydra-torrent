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

## 2026-08-22 - SSRF guard blocked legitimate Jackett download links on any private-LAN Jackett

- **Symptom**: found by accident while capturing real footage for a demo
  GIF, not a report - a well-seeded (40 real seeders), completely legal
  TorrentGalaxy result failed to add with "Could not fetch metadata:
  Refusing to fetch '...': resolves to 192.168.20.40, an internal/private
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
  (TorrentGalaxy, confirmed live) was silently unusable for anyone whose
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
  hardcoded `\\192.168.20.4\Plex\...` UNC path (this machine's own home NAS)
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

## 2026-08-20 - Removed hardcoded 1337x/Nyaa/The Pirate Bay scraping (the "Public" search mode)

- **Symptom**: not a technical bug - a legal-risk-by-design one. `search.py`
  had a `search_online_public()` function that scraped three named public
  indexers (1337x, with four mirror domains hardcoded; Nyaa.si; The Pirate
  Bay via apibay.org) directly, rotating fake browser user-agents to get
  past basic bot detection, and returning ready-to-download magnet links.
  It was the default: `hydra_app.py`'s self-install config set
  `"search_mode": "online"`, and the Search tab's mode dropdown listed
  "Public" first with no other option marked selected - a brand-new
  install, zero configuration, searched The Pirate Bay by default.
- **Root cause**: under *MGM v. Grokster* (2005), a tool that's otherwise
  protected by the Betamax "substantial non-infringing use" doctrine can
  still lose that protection if it's shown to be intentionally designed to
  facilitate infringement - and hardcoding specific named piracy indexers
  into the app's own source, as the default search path, is a real example
  of exactly that factor. It's not about marketing copy (README/PITCH.md
  never mentioned any of these sites) - the code itself was the problem.
  This is also the *architectural* reason Sonarr/Radarr/Jackett have
  operated safely for years: none of them contain built-in knowledge of
  any specific indexer. Every source is something the user configures
  themselves, at arm's length, through Jackett. Hydra's own `search_jackett`
  and `search_index_server` paths already worked exactly that way -
  `search_online_public` was the one place that didn't.
- **Fix**: removed `search_online_public()` entirely, the "Public" option
  in the Search tab dropdown, and the `/search` endpoint's silent
  fallthrough to it. `mode` now only accepts `"jackett"` or `"local"`
  (both already user-configured, arm's-length sources) - anything else
  gets a clean `400`, not a silent default. `beautifulsoup4` (only ever
  needed for the HTML scraping) dropped from every requirements file and
  the PyInstaller build.
- **How it was caught**: Matth asked directly whether this was legally
  sound, given the Plex/Jellyfin integration work made Hydra look
  increasingly like a real product rather than a hobby project. A direct
  code + docs audit (grepped every `.py`/`.md`/`.html` file in the repo
  for known piracy-site domain names) confirmed `search_online_public()`
  was the only place this pattern existed - `rss_poller.py`'s Auto-DL only
  ever routes through Jackett, and `index_server.py`'s "local" mode is
  user-operated infrastructure, not a Hydra-run index.

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
