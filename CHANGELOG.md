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
  \\\\192.168.20.4\\Plex\\incomplete path with real free-space numbers
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
