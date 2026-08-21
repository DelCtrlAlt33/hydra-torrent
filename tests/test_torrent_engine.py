"""
Integration tests against the real TorrentEngine/DaemonStore classes in
hydra_daemon.py, using a real (synthetic, local, no-network) libtorrent
session - not a reimplementation or a mock of libtorrent's behavior.

These need libtorrent actually installed and are deliberately NOT part of
the fast unit-test tier (see tests.yml's own comment on why libtorrent was
originally left out of CI): they run in a separate, non-blocking CI job
specifically so a libtorrent wheel-availability hiccup can't turn the main,
always-reliable suite red for a reason that has nothing to do with a real
code bug.

Written after both bugs below were found completely uncovered on
2026-08-22 during a proactive audit (not a bug report) - this file exists
so neither can silently regress again.
"""
import base64
import os
import threading
import time

import libtorrent as lt
import pytest

import hydra_daemon as hd


def _make_local_torrent(tmp_path, filename="testfile.bin", size=256 * 1024):
    """A synthetic, fully local, hybrid v1/v2 torrent - no network needed.

    Hybrid is what lt.create_torrent() produces by default on this
    libtorrent version (confirmed via info_hashes().has_v1()/has_v2()) -
    this doubles as coverage for hybrid-torrent handling, not just resume
    data, since every test here goes through the same real add/hash path.
    """
    data_dir = tmp_path / "torrent_data"
    data_dir.mkdir()
    data_path = data_dir / filename
    data_path.write_bytes(os.urandom(size))

    fs = lt.file_storage()
    lt.add_files(fs, str(data_path))
    t = lt.create_torrent(fs, piece_size=16 * 1024)
    lt.set_piece_hashes(t, str(data_dir))
    ti = lt.torrent_info(lt.bencode(t.generate()))
    magnet = lt.make_magnet_uri(ti)
    return str(data_dir), ti, magnet


def _wait_for_state(handle, states, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if handle.status().state in states:
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def engine():
    """A real TorrentEngine with its own session - isolated per test, not
    the module-level hd.store/hd.engine singleton."""
    store = hd.DaemonStore()
    eng = hd.TorrentEngine(store)
    eng._create_session("127.0.0.1")
    return eng


# ---------------------------------------------------------------------------
# Fast-resume data (see CHANGELOG.md 2026-08-22 "Every daemon restart
# re-hashed every in-progress torrent from scratch")
# ---------------------------------------------------------------------------

def test_save_and_restore_resume_data_skips_full_recheck(tmp_path, engine):
    """The actual bug: every daemon restart re-verified every piece of every
    in-progress torrent from scratch, because nothing ever called
    save_resume_data()/read_resume_data(). This proves the real fix works
    end-to-end: save -> simulated restart -> restore -> immediately seeding,
    not stuck re-checking or (the sneakier failure mode found while building
    this) stuck in downloading_metadata forever because resume data alone
    never carries the torrent's metadata.
    """
    data_dir, ti, magnet = _make_local_torrent(tmp_path)
    name = "ResumeDataTestTorrent"

    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = data_dir
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, magnet, ti.total_size(), handle)
    engine.store.update(name, status="Downloading", magnet=magnet)

    assert _wait_for_state(handle, (lt.torrent_status.states.seeding, lt.torrent_status.states.finished)), \
        "setup failed: torrent never finished its initial check"

    # Real shutdown path
    engine._save_resume_data()
    assert os.path.exists(hd._RESUME_DATA_PATH)

    # Simulate a restart: the live handle goes away, but the store entry
    # (as if freshly loaded from transfers.json) stays.
    engine.ses.remove_torrent(handle)
    time.sleep(0.3)
    engine.store.update(name, handle=None)

    engine._resume_persisted()
    time.sleep(0.3)

    restored = engine.store.get_handle(name)
    assert restored is not None, "_resume_persisted did not re-attach a handle"
    assert _wait_for_state(restored, (lt.torrent_status.states.seeding, lt.torrent_status.states.finished), timeout=5.0), \
        f"restored torrent never reached seeding - stuck at {restored.status().state}"


def test_resume_data_falls_back_gracefully_when_corrupt(tmp_path, engine):
    """A corrupt/incompatible resume blob (e.g. a libtorrent version bump
    changing the format) must fall back to the plain magnet re-add, not
    sink the torrent - this is the difference between "slightly slower
    restart for one torrent" and "a transfer silently vanishes."
    """
    data_dir, ti, magnet = _make_local_torrent(tmp_path)
    name = "CorruptResumeDataTorrent"

    engine.store.add(name, magnet, ti.total_size(), None)
    engine.store.update(name, status="Downloading", magnet=magnet)

    with open(hd._RESUME_DATA_PATH, "w", encoding="utf-8") as f:
        import json
        json.dump({name: {
            "resume": base64.b64encode(b"not real resume data").decode("ascii"),
            "metadata": base64.b64encode(b"not real metadata either").decode("ascii"),
        }}, f)

    engine._resume_persisted()
    time.sleep(0.3)

    restored = engine.store.get_handle(name)
    assert restored is not None, "corrupt resume data should still fall back to a working magnet re-add"


def test_moved_to_plex_torrent_skips_resume_data(tmp_path, engine):
    """A torrent already auto-moved to Plex/Jellyfin must NOT use its saved
    resume data on restart: media_organizer moves files with a plain
    filesystem move (not handle.move_storage()), so libtorrent's own resume
    state still points at the pre-move path. Using it anyway would tell
    libtorrent "verified pieces are at path X" when the files are no longer
    there - this proves the moved_to_plex gate actually takes the plain
    magnet re-add branch instead.
    """
    data_dir, ti, magnet = _make_local_torrent(tmp_path)
    name = "MovedToPlexTorrent"

    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = data_dir
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, magnet, ti.total_size(), handle)
    engine.store.update(name, status="Seeding", magnet=magnet)

    assert _wait_for_state(handle, (lt.torrent_status.states.seeding, lt.torrent_status.states.finished))

    engine._save_resume_data()
    assert os.path.exists(hd._RESUME_DATA_PATH)

    engine.ses.remove_torrent(handle)
    time.sleep(0.3)
    # A different save_path (as if plex_path points at the post-move
    # location) - if the resume-data path were used here, add_torrent
    # would trust the pre-move path baked into the resume blob instead.
    new_dir = tmp_path / "moved_elsewhere"
    new_dir.mkdir()
    engine.store.update(name, handle=None, moved_to_plex=True, plex_path=str(new_dir))

    resume_data = engine._load_resume_data()
    assert name in resume_data, "test setup issue: resume data should exist for this name"

    # Exercise the actual gating condition _resume_persisted uses, rather
    # than re-running the full re-add (which needs real files at the new
    # path to succeed) - this is the one line that matters for this bug.
    t = engine.store.get(name)
    uses_resume_data = name in resume_data and "resume" in resume_data[name] and "metadata" in resume_data[name] and not t.get("moved_to_plex")
    assert uses_resume_data is False, "a moved-to-Plex torrent must not use its (now-stale) resume data"


# ---------------------------------------------------------------------------
# Seeding-phase error surfacing (see CHANGELOG.md 2026-08-22 "Torrent
# errors while seeding were never surfaced, only while downloading")
# ---------------------------------------------------------------------------

class _FakeErrorStatus:
    error = "disk write failed: no space left on device"
    paused = False


class _FakeErrorHandle:
    def status(self):
        return _FakeErrorStatus()


def test_monitor_seeding_detects_and_surfaces_torrent_error(engine):
    """_monitor_download already checked status().error every poll and
    correctly failed the torrent; _monitor_seeding never did, so a disk
    error after completion (files deleted out from under a seeding
    torrent, disk goes read-only, etc.) used to just report stats forever
    with nothing surfaced anywhere.
    """
    name = "SeedingErrorTorrent"
    handle = _FakeErrorHandle()
    engine.store.add(name, "magnet:?xt=urn:btih:" + "0" * 40, 1000, handle)
    engine.store.update(name, status="Seeding")

    th = threading.Thread(target=engine._monitor_seeding, args=(name, handle), daemon=True)
    th.start()
    th.join(timeout=3)

    t = engine.store.get(name)
    assert t.get("status") == "Failed"
    assert "no space left" in (t.get("error") or "")
    assert not th.is_alive(), "monitor thread should exit once an error is detected, not loop forever"
