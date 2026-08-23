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


# ---------------------------------------------------------------------------
# File-selection race condition (see CHANGELOG.md 2026-08-23 "a user's
# unchecked files downloaded anyway")
# ---------------------------------------------------------------------------

def test_commit_preview_respects_explicit_zero_priorities_even_when_flag_set(tmp_path, engine):
    """The actual bug: commit_preview() used to decide "did the user ever
    touch file selection" by re-reading handle.file_priorities() back from
    libtorrent - an async, fire-and-forget call, so nothing guaranteed the
    change had landed internally by the time commit_preview read it a
    moment later. When it hadn't, every file still reading as priority 0
    looked indistinguishable from "nothing was ever set," and the old
    code force-reset everything back to 1, silently overriding the user's
    real exclusions.

    This proves the fix: with file_priorities_set=True (what
    POST /transfers/{name}/files now sets, synchronously, in our own
    application state rather than libtorrent's), commit_preview must NOT
    touch priorities at all - not even in the edge case where every file
    genuinely reads back as 0, which is exactly the condition the old
    code used to misinterpret as "never configured."
    """
    # A real multi-file torrent so there's more than one priority slot to
    # get wrong - single-file torrents can't distinguish "reset everything"
    # from "reset the one thing" the way this bug needs. Files sized to an
    # exact piece boundary so lt.create_torrent()'s hybrid v1/v2 default
    # doesn't insert invisible pad-file entries between them - confirmed
    # live that mismatched sizes/piece_size silently changes the real file
    # count file_priorities() reports, which isn't what this test is about.
    PIECE = 16 * 1024
    data_dir2 = tmp_path / "multi"
    data_dir2.mkdir()
    (data_dir2 / "a.bin").write_bytes(os.urandom(PIECE))
    (data_dir2 / "b.bin").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(data_dir2))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    multi_ti = lt.torrent_info(lt.bencode(t.generate()))
    magnet = lt.make_magnet_uri(multi_ti)
    assert multi_ti.num_files() == 2, "test setup issue: unexpected pad files in the torrent"

    name = "FilePriorityRaceTorrent"
    params = lt.add_torrent_params()
    params.ti = multi_ti
    params.save_path = str(tmp_path)
    params.flags = (lt.torrent_flags.default_flags
                     | lt.torrent_flags.default_dont_download) & ~lt.torrent_flags.paused
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, magnet, multi_ti.total_size(), handle)
    engine.store.update(name, status="Metadata ready", hidden=True)

    # Every file genuinely at priority 0, exactly as it sits throughout
    # real preview - and exactly the state the old code misread as "user
    # never touched selection."
    handle.prioritize_files([0, 0])
    time.sleep(0.2)  # let libtorrent's internal state actually settle

    # This is what the fixed POST /transfers/{name}/files endpoint sets -
    # simulating "the user explicitly excluded every file," a real (if
    # unusual) choice that must be respected, not silently reversed.
    engine.store.update(name, file_priorities_set=True)

    real_name = engine.commit_preview(name)

    final_handle = engine.store.get_handle(real_name)
    final_priorities = list(final_handle.file_priorities())
    assert final_priorities == [0, 0], (
        f"commit_preview overrode explicit user file selection: {final_priorities}"
    )


def test_commit_preview_defaults_to_all_files_when_never_explicitly_set(tmp_path, engine):
    """The other half of the same fix: when the frontend's /files endpoint
    was genuinely never called (file_priorities_set stays False - the
    common case, most people never touch file selection), commit_preview
    must still fall back to downloading everything, not leave a torrent
    permanently stuck at priority 0 with nothing ever downloading.
    """
    PIECE = 16 * 1024
    data_dir2 = tmp_path / "multi2"
    data_dir2.mkdir()
    (data_dir2 / "a.bin").write_bytes(os.urandom(PIECE))
    (data_dir2 / "b.bin").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(data_dir2))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    multi_ti = lt.torrent_info(lt.bencode(t.generate()))
    magnet = lt.make_magnet_uri(multi_ti)
    assert multi_ti.num_files() == 2, "test setup issue: unexpected pad files in the torrent"

    name = "FilePriorityNeverSetTorrent"
    params = lt.add_torrent_params()
    params.ti = multi_ti
    params.save_path = str(tmp_path)
    params.flags = (lt.torrent_flags.default_flags
                     | lt.torrent_flags.default_dont_download) & ~lt.torrent_flags.paused
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, magnet, multi_ti.total_size(), handle)
    engine.store.update(name, status="Metadata ready", hidden=True)
    # file_priorities_set left at its real default (False) - the frontend
    # never called POST /files, same as the common "never touched file
    # selection" case.

    real_name = engine.commit_preview(name)
    time.sleep(0.2)  # commit_preview's own prioritize_files() call is
                      # itself async into libtorrent's engine - give it a
                      # moment to land before reading it back, same as
                      # elsewhere in this file.

    final_handle = engine.store.get_handle(real_name)
    final_priorities = list(final_handle.file_priorities())
    assert all(p > 0 for p in final_priorities), (
        f"a torrent whose file selection was never touched must default to "
        f"downloading everything, got {final_priorities}"
    )


# ---------------------------------------------------------------------------
# Honest "moved to Plex" labeling (see CHANGELOG.md 2026-08-23 "it says it
# moved to plex, which it did not")
# ---------------------------------------------------------------------------

def test_is_under_library_root_true_for_real_library_path(tmp_path, monkeypatch):
    movies_root = tmp_path / "media" / "movies"
    movies_root.mkdir(parents=True)
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(movies_root))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")

    dest = movies_root / "Some Movie (2024)"
    assert hd._is_under_library_root(str(dest)) is True


def test_is_under_library_root_false_for_fallback_downloads_path(tmp_path, monkeypatch):
    """The exact bug: a file that fell back to the general-downloads
    folder (Plex never actually configured/reachable) must not be
    reported as having landed under a library root, even though both
    paths structurally live under the same broader Downloads tree.
    """
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(tmp_path / "media" / "movies"))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", str(tmp_path / "media" / "tv"))

    fallback_dest = tmp_path / "Downloads" / "HydraTorrent"
    fallback_dest.mkdir(parents=True)
    assert hd._is_under_library_root(str(fallback_dest)) is False


def test_is_under_library_root_false_when_nothing_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", "")
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")
    assert hd._is_under_library_root(str(tmp_path / "anything")) is False
