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
