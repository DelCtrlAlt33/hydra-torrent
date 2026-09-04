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
# Seeding progress/speed freeze (see CHANGELOG.md 2026-08-23 "a completed,
# actively-seeding download still showed as barely started")
# ---------------------------------------------------------------------------

class _FakeSeedingStatus:
    error = None
    paused = False
    state = 5  # 'Seeding' per _state_map
    total_upload = 500_000
    upload_rate = 1234.0
    num_peers = 3
    num_seeds = 3


class _FakeSeedingHandle:
    def status(self):
        return _FakeSeedingStatus()


def test_monitor_seeding_reports_complete_bytes_done_not_stale_value(engine):
    """The actual bug: _monitor_seeding()'s own update() calls never
    included bytes_done/speed_down at all - once a torrent started
    seeding, those two fields froze forever at whatever they happened to
    read on the exact final download-loop iteration before handoff,
    which could be well below the real total size if libtorrent's
    s.total_done genuinely lagged s.progress/is_seeding by a beat right
    at completion. Confirmed live: a real, actively-seeding, 100%-
    complete download kept reporting bytes_done far below its real file
    size, with speed_down still showing an old nonzero rate, forever.
    A torrent that's seeding is complete by definition - this proves
    _monitor_seeding() now asserts that itself every iteration, instead
    of trusting a stale value handed to it from before.
    """
    name = "SeedingStaleBytesTorrent"
    handle = _FakeSeedingHandle()
    total_size = 2_556_693_199  # a real torrent's actual size
    engine.store.add(name, "magnet:?xt=urn:btih:" + "1" * 40, total_size, handle)
    # Simulate exactly the stale state found live: frozen at a low
    # bytes_done/nonzero speed_down from the last download-loop tick
    # before the (untested here) handoff to seeding.
    engine.store.update(name, status="Seeding", bytes_done=41_943_040, speed_down=6_464_736.9)

    th = threading.Thread(target=engine._monitor_seeding, args=(name, handle), daemon=True)
    th.start()
    time.sleep(0.3)  # let one real loop iteration run

    t = engine.store.get(name)
    assert t.get("bytes_done") == total_size, "a seeding torrent must report its full size as downloaded"
    assert t.get("speed_down") == 0.0, "a seeding torrent isn't downloading - speed_down must not show a stale rate"


class _FakeRecheckingStatus:
    """A torrent _monitor_seeding was handed already knowing it's complete
    (real completion handoff, or a restart-resume that found status
    already 'Seeding'), but whose libtorrent handle is mid-recheck -
    exactly what a bare magnet re-add produces (the path moved_to_plex
    forces, since saved resume data points at the pre-move location)."""
    error = None
    paused = False
    state = 3  # 'Downloading' per the old _state_map - mid-recheck, not a real download
    total_upload = 3249152
    upload_rate = 1635.66
    num_peers = 9
    num_seeds = 8


class _FakeRecheckingHandle:
    def status(self):
        return _FakeRecheckingStatus()


def test_monitor_seeding_reports_seeding_even_mid_recheck(engine):
    """The actual bug: _monitor_seeding() used to set status to whatever
    libtorrent's raw internal state currently read
    (_state_map.get(s.state, 'Seeding')) instead of asserting 'Seeding'
    unconditionally the way it already does for bytes_done/speed_down a
    few lines above. This thread only ever runs for a torrent already
    known complete, so a mid-recheck raw state of 'Checking' or
    'Downloading' is never a real download in progress - just libtorrent
    re-verifying an already-complete file against a new location.
    Confirmed live: right after a daemon restart, a real, already-moved-
    to-Plex download displayed 'Downloading' again while its handle was
    mid-recheck, alarming enough to be reported as a regression.
    """
    name = "AlreadyMovedRecheckingTorrent"
    handle = _FakeRecheckingHandle()
    total_size = 2_556_693_199
    engine.store.add(name, "magnet:?xt=urn:btih:" + "2" * 40, total_size, handle)
    engine.store.update(name, status="Seeding", moved_to_plex=True)

    th = threading.Thread(target=engine._monitor_seeding, args=(name, handle), daemon=True)
    th.start()
    time.sleep(0.3)

    t = engine.store.get(name)
    assert t.get("status") == "Seeding", (
        f"a torrent this thread already knows is complete must never report a mid-recheck "
        f"raw libtorrent state as its status, got {t.get('status')!r}"
    )


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
# Instant fake-complete on zero selected files (see CHANGELOG.md 2026-08-23
# "the toast came up as downloaded, immediately")
# ---------------------------------------------------------------------------

def test_monitor_download_fails_instead_of_faking_completion_when_nothing_selected(tmp_path, engine):
    """The actual bug: set_file_priorities() sets file_priorities_set=True
    unconditionally, even when the resulting priorities are still all
    zero. commit_preview()'s only safety net (default to downloading
    everything) only fires when file_priorities_set is False, so it never
    catches this. libtorrent then correctly reports is_finished/is_seeding
    - by its own definition, "nothing left that's wanted" - and the
    completion branch used to trust that blindly and force bytes_done to
    the full size. Confirmed live: a real torrent with real peers
    "completed" in milliseconds with total_done at 0 and every entry in
    s.pieces false, because every file was still sitting at priority 0.

    This proves the fix: _monitor_download must verify at least one file
    is genuinely wanted before accepting is_finished/is_seeding as a real
    completion, and fail loudly instead of lying about a download that
    never happened.
    """
    PIECE = 16 * 1024
    data_dir = tmp_path / "nothing_selected"
    data_dir.mkdir()
    (data_dir / "a.bin").write_bytes(os.urandom(PIECE))
    (data_dir / "b.bin").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(data_dir))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    ti = lt.torrent_info(lt.bencode(t.generate()))
    magnet = lt.make_magnet_uri(ti)
    assert ti.num_files() == 2, "test setup issue: unexpected pad files in the torrent"

    name = "ZeroPriorityInstantCompleteTorrent"
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(tmp_path / "download_dest")
    params.flags = (lt.torrent_flags.default_flags
                     | lt.torrent_flags.default_dont_download) & ~lt.torrent_flags.paused
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, magnet, ti.total_size(), handle)
    engine.store.update(name, status="Metadata ready", hidden=True)

    # Simulate the real trigger: set_file_priorities() got called (so the
    # flag is True) but every file ended up at 0 anyway.
    handle.prioritize_files([0, 0])
    time.sleep(0.2)
    engine.store.update(name, file_priorities_set=True, hidden=False, status="Downloading")

    th = threading.Thread(
        target=engine._monitor_download, args=(name, handle, ti.total_size()), daemon=True,
    )
    th.start()
    th.join(timeout=5)

    result = engine.store.get(name)
    assert result.get("status") == "Failed", (
        f"a torrent with zero files selected must not report success, got {result.get('status')!r}"
    )
    assert "no files" in (result.get("error") or "").lower()
    assert result.get("bytes_done") != ti.total_size(), (
        "bytes_done must not be force-set to the full size when nothing was actually downloaded"
    )
    assert not th.is_alive(), "monitor thread should exit, not hand off to seeding"


class _FakeBlipStatus:
    """A single fake libtorrent status snapshot with everything
    _monitor_download reads from it."""
    def __init__(self, state, is_finished, is_seeding, progress, total_done):
        self.error = None
        self.paused = False
        self.state = state
        self.is_finished = is_finished
        self.is_seeding = is_seeding
        self.progress = progress
        self.total_done = total_done
        self.upload_rate = 0.0
        self.num_peers = 5
        self.num_seeds = 3
        self.pieces = []
        self.num_pieces = 0


class _FakeBlipHandle:
    """Returns a transient 'finished' reading on the very first status()
    call (is_finished True, total_done still 0 - libtorrent's raw state
    right as a torrent starts, before real data has landed), then settles
    into a normal, genuinely-downloading state on every call after."""
    def __init__(self):
        self._calls = 0

    def status(self):
        self._calls += 1
        if self._calls == 1:
            return _FakeBlipStatus(state=4, is_finished=True, is_seeding=False, progress=1.0, total_done=0)
        return _FakeBlipStatus(state=3, is_finished=False, is_seeding=False, progress=0.05, total_done=500_000)


def test_monitor_download_never_reports_seeding_from_a_transient_first_pass_blip(engine):
    """The actual bug: the plain per-loop status write
    (status = 'Seeding' if s.is_seeding or s.is_finished) had no
    first_pass/total_done guard at all - only the completion-handling
    branch right below it did. A transient is_finished=True reading on
    the very first poll (total_done still 0, real data not landed yet)
    got written straight to the store - and pushed over the websocket -
    as status='Seeding', even though the completion branch correctly
    declined to treat it as a real finish and the loop kept running
    normally afterward. Confirmed live: a real, healthily-downloading
    torrent (3m50s real duration) showed a false "✓ finished" activity-
    log line and fired the completion toast within seconds of being
    added, then continued downloading normally for real - because the
    UI has no way to know a status it was told was true wasn't.
    """
    name = "TransientBlipTorrent"
    handle = _FakeBlipHandle()
    total_size = 2_000_000
    engine.store.add(name, "magnet:?xt=urn:btih:" + "5" * 40, total_size, handle)
    engine.store.update(name, status="Downloading")

    th = threading.Thread(
        target=engine._monitor_download, args=(name, handle, total_size), daemon=True,
    )
    th.start()
    time.sleep(0.2)  # let the first (blip) iteration run
    first_status = engine.store.get(name).get("status")
    assert first_status != "Seeding", (
        f"a transient is_finished blip on the first pass must never be reported as 'Seeding', "
        f"got {first_status!r}"
    )
    assert th.is_alive(), "the loop must not have exited - the blip isn't a real completion"

    time.sleep(2.2)  # let a second, real iteration run past the 2s poll interval
    second_status = engine.store.get(name).get("status")
    assert second_status != "Seeding", (
        f"a genuinely still-downloading torrent must not show 'Seeding' either, got {second_status!r}"
    )
    assert th.is_alive(), "a torrent that's still genuinely downloading must not have completed"


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


def test_post_download_reports_moved_to_plex_even_when_extras_fall_back_after_it(tmp_path, engine, monkeypatch):
    """The actual bug: _post_download() calls store.update(moved_to_plex=...)
    once per successfully-moved file, unconditionally - so whichever file
    is processed *last* decides the whole torrent's reported outcome. A
    torrent's main video file often lands in the real Plex library while
    its extras (subtitle, cover art) correctly fall back to the general
    downloads folder afterward - both individually correct - but that
    made the overall summary flip back to "moved to Downloads" the moment
    a fallback extra was processed after the real Plex move. Confirmed
    live: a real movie genuinely organized into Plex, immediately
    followed by its .srt/.jpg landing in the local fallback, reported
    moved_to_plex=False and plex_path=the local folder for the whole
    torrent.
    """
    PIECE = 16 * 1024
    save_dir = tmp_path / "incomplete"
    save_dir.mkdir()
    (save_dir / "movie.mp4").write_bytes(os.urandom(PIECE))
    (save_dir / "extra.jpg").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(save_dir))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    ti = lt.torrent_info(lt.bencode(t.generate()))
    assert ti.num_files() == 2, "test setup issue: unexpected pad files in the torrent"

    name = "PlexLabelClobberTorrent"
    params = lt.add_torrent_params()
    params.ti = ti
    # file_info.path already includes save_dir's own basename (add_files
    # roots relative to it) - save_path must be its *parent*, matching
    # the pattern used elsewhere in this file, or _post_download's
    # os.path.join(actual_save_path, file_info.path) double-nests it.
    params.save_path = str(tmp_path)
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, "magnet:?xt=urn:btih:" + "3" * 40, ti.total_size(), handle)
    engine.store.update(name, status="Seeding", moved_to_plex=False, plex_path=None)

    plex_movies = tmp_path / "media" / "movies" / "PlexLabelClobberTorrent"
    local_fallback = tmp_path / "Downloads" / "HydraTorrent"
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(tmp_path / "media" / "movies"))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", str(tmp_path / "media" / "tv"))

    call_order = []

    def fake_mover(basename, file_path, torrent_name=None):
        # First call (the video) really lands in Plex; every call after
        # (the extras) correctly falls back to the local downloads folder -
        # matching the real order this bug was found in.
        call_order.append(basename)
        if len(call_order) == 1:
            dest = plex_movies / basename
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = local_fallback / basename
            dest.parent.mkdir(parents=True, exist_ok=True)
        return True, str(dest), None

    monkeypatch.setattr(hd, "auto_move_completed_download", fake_mover)

    engine._post_download(name, handle)

    result = engine.store.get(name)
    assert result.get("moved_to_plex") is True, (
        "a torrent whose main file genuinely landed in Plex must not have that overwritten "
        "back to False just because an extra file afterward correctly fell back elsewhere"
    )
    assert str(plex_movies) in (result.get("plex_path") or ""), (
        f"plex_path must still point at the real Plex destination, got {result.get('plex_path')!r}"
    )


@pytest.mark.parametrize("subtitle_stem,video_stem,expected", [
    ("movie", "movie", True),
    ("movie.en", "movie", True),
    ("movie.eng", "movie", True),
    ("Show.S01E01", "Show.S01E01", True),
    ("Show.S01E01.eng", "Show.S01E01", True),
    ("Show.S01E10", "Show.S01E1", False),  # the exact false-positive this guards against
    ("movie2", "movie", False),
    ("other", "movie", False),
])
def test_subtitle_matches_video(subtitle_stem, video_stem, expected):
    assert hd._subtitle_matches_video(subtitle_stem, video_stem) is expected


def test_post_download_moves_subtitle_alongside_video_and_leaves_everything_else(tmp_path, engine, monkeypatch):
    """End-to-end for the actual feature request: subtitles should follow
    their video into Plex; unrelated extras (cover art, promo images)
    should stay in the incomplete folder rather than being scattered into
    a local downloads fallback nobody asked for. Uses the real
    auto_move_completed_download/move_subtitle_alongside_video path, not
    a mock, so this also exercises the real movie-folder naming logic.
    """
    PIECE = 16 * 1024
    save_dir = tmp_path / "incomplete"
    save_dir.mkdir()
    (save_dir / "Some.Movie.2024.1080p.mkv").write_bytes(os.urandom(PIECE))
    (save_dir / "Some.Movie.2024.1080p.srt").write_bytes(os.urandom(PIECE))
    (save_dir / "poster.jpg").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(save_dir))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    ti = lt.torrent_info(lt.bencode(t.generate()))
    assert ti.num_files() == 3, "test setup issue: unexpected pad files in the torrent"

    name = "SubtitlePairingTorrent"
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(tmp_path)  # parent of save_dir - see the note on this pattern above
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, "magnet:?xt=urn:btih:" + "4" * 40, ti.total_size(), handle)
    engine.store.update(name, status="Seeding")

    # _is_under_library_root (hydra_daemon.py) and move_to_plex
    # (media_organizer.py) each imported their own copy of these
    # constants from config.py - patching one name doesn't affect the
    # other, both need it.
    movies_root = tmp_path / "media" / "movies"
    movies_root.mkdir(parents=True)
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(movies_root))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", str(tmp_path / "media" / "tv"))
    monkeypatch.setattr("media_organizer.MEDIA_DIR_MOVIES", str(movies_root))
    monkeypatch.setattr("media_organizer.MEDIA_DIR_TV", str(tmp_path / "media" / "tv"))

    engine._post_download(name, handle)

    result = engine.store.get(name)
    assert result.get("moved_to_plex") is True

    moved_files = result.get("moved_files") or []
    video_dest = next((p for p in moved_files if p.endswith(".mkv")), None)
    srt_dest = next((p for p in moved_files if p.endswith(".srt")), None)
    assert video_dest is not None, "the video must have been moved into the Plex library"
    assert srt_dest is not None, "the subtitle must have been moved too, not left behind"
    assert os.path.dirname(srt_dest) == os.path.dirname(video_dest), (
        "the subtitle must land in the exact same folder as its video, "
        f"got video={video_dest!r} srt={srt_dest!r}"
    )

    assert not any(p.endswith("poster.jpg") for p in moved_files), (
        "an unrelated extra (cover art) must never be moved - it belongs to neither "
        "the video pass nor the subtitle pass"
    )
    assert os.path.exists(save_dir / "poster.jpg"), (
        "poster.jpg must still be sitting in the incomplete folder, untouched"
    )


def test_post_download_per_transfer_override_skips_auto_organize(tmp_path, engine, monkeypatch):
    """The actual bug reported live: a user opens the add-torrent dialog,
    changes SAVE AT to a folder of their own choosing, downloads it - and
    the video ends up in the Plex library anyway, ignoring what the
    dialog said. commit_preview()'s auto_organize=False (set from the
    dialog's "move to Plex automatically" checkbox) must actually stop
    the move, not just get silently ignored in favor of the global
    auto_move_to_plex setting.
    """
    PIECE = 16 * 1024
    save_dir = tmp_path / "incomplete"
    save_dir.mkdir()
    (save_dir / "movie.mp4").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(save_dir))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    ti = lt.torrent_info(lt.bencode(t.generate()))

    name = "CustomFolderTorrent"
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(tmp_path)
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, "magnet:?xt=urn:btih:" + "5" * 40, ti.total_size(), handle)
    # Mirrors what commit_preview() now does when the dialog's checkbox
    # was unchecked - real code path is exercised in test_daemon_models.py
    # / API-level tests, this isolates _post_download's own behavior.
    engine.store.update(name, status="Seeding", auto_organize=False)

    called = []
    monkeypatch.setattr(
        hd, "auto_move_completed_download",
        lambda *a, **k: (called.append(1), (True, "/should/not/be/used", None))[1],
    )

    engine._post_download(name, handle)

    assert called == [], "auto_organize=False on this transfer must skip the Plex move entirely"
    result = engine.store.get(name)
    assert result.get("moved_to_plex") is not True
    assert (save_dir / "movie.mp4").exists(), (
        "the video must stay exactly where the user's own SAVE AT path put it"
    )


def test_post_download_per_transfer_override_true_beats_global_off(tmp_path, engine, monkeypatch):
    """The other direction: auto_organize=True on a specific transfer
    still organizes it even if the user has the global 'auto_move_to_plex'
    setting turned off - symmetric with the False case above.
    """
    PIECE = 16 * 1024
    save_dir = tmp_path / "incomplete"
    save_dir.mkdir()
    (save_dir / "movie.mp4").write_bytes(os.urandom(PIECE))
    fs = lt.file_storage()
    lt.add_files(fs, str(save_dir))
    t = lt.create_torrent(fs, piece_size=PIECE)
    lt.set_piece_hashes(t, str(tmp_path))
    ti = lt.torrent_info(lt.bencode(t.generate()))

    name = "ForceOrganizeTorrent"
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(tmp_path)
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, "magnet:?xt=urn:btih:" + "6" * 40, ti.total_size(), handle)
    engine.store.update(name, status="Seeding", auto_organize=True)

    movies_root = tmp_path / "media" / "movies"
    movies_root.mkdir(parents=True)
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(movies_root))
    monkeypatch.setattr(hd, "load_config", lambda: {"auto_move_to_plex": False})
    monkeypatch.setattr(
        hd, "auto_move_completed_download",
        lambda basename, file_path, torrent_name=None: (True, str(movies_root / name / basename), None),
    )

    engine._post_download(name, handle)

    result = engine.store.get(name)
    assert result.get("moved_to_plex") is True, (
        "an explicit per-transfer auto_organize=True must still organize the file "
        "even though the global setting is off"
    )


# ---------------------------------------------------------------------------
# Default SAVE AT path (see CHANGELOG.md 2026-08-23 "SAVE AT defaulted to
# local downloads even with a real, working Plex library configured",
# "SAVE AT still used the Plex-adjacent folder even after Plex itself
# became unreachable", and "a PATCH /config change wasn't reflected until
# the whole app restarted")
# ---------------------------------------------------------------------------
# _default_save_path() reads everything - media_dir_movies/tv,
# download_dir_incomplete, plex/jellyfin url+token - straight from
# load_config() each call, deliberately not from the MEDIA_DIR_MOVIES/
# MEDIA_DIR_TV/DOWNLOAD_DIR_INCOMPLETE module-level constants (those are
# frozen once at daemon startup - confirmed live, a PATCH /config change
# to any of them wasn't reflected here until the whole app restarted).
# Every test below mocks the complete load_config() dict instead of the
# module constants, matching that.

def _cfg(**overrides) -> dict:
    """Full load_config()-shaped dict for these tests, with sane blank
    defaults for every key _default_save_path() reads - override just
    the ones a given test cares about."""
    base = {
        "plex_url": "", "plex_token": "",
        "jellyfin_url": "", "jellyfin_api_key": "",
        "media_dir_movies": "", "media_dir_tv": "",
        "download_dir_incomplete": "",
    }
    base.update(overrides)
    return base


def test_default_save_path_uses_plex_adjacent_incomplete_when_reachable(tmp_path, monkeypatch):
    """The actual fix: a properly configured AND reachable media library
    should make its sibling "incomplete" folder the real default, not the
    plain local download folder - confirmed live, the local folder was
    being used unconditionally even with Plex fully set up and working.
    """
    library_root = tmp_path / "Plex" / "movies"
    library_root.mkdir(parents=True)
    incomplete = tmp_path / "Plex" / "incomplete"
    incomplete.mkdir()

    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        media_dir_movies=str(library_root),
        download_dir_incomplete=str(tmp_path / "local_fallback"),
    ))

    assert hd._default_save_path() == str(incomplete)


def test_default_save_path_falls_back_when_sibling_incomplete_missing(tmp_path, monkeypatch):
    """A configured, reachable Movies library with no sibling "incomplete"
    folder (never created, or a differently-laid-out share) must fall
    back to the local download directory rather than pointing at a
    folder that doesn't exist.
    """
    library_root = tmp_path / "Plex" / "movies"
    library_root.mkdir(parents=True)
    # deliberately no tmp_path/Plex/incomplete

    local_fallback = tmp_path / "local_fallback"
    local_fallback.mkdir()
    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        media_dir_movies=str(library_root),
        download_dir_incomplete=str(local_fallback),
    ))

    assert hd._default_save_path() == str(local_fallback)


def test_default_save_path_falls_back_when_media_dir_unreachable(tmp_path, monkeypatch):
    """The exact scenario from tonight's earlier bug: a configured but
    genuinely unreachable media path (network share down, wrong
    platform's path syntax, etc.) must not be used as the default -
    falls back to local downloads instead of pointing somewhere Hydra
    itself can't confirm exists.
    """
    local_fallback = tmp_path / "local_fallback"
    local_fallback.mkdir()
    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        media_dir_movies=str(tmp_path / "does_not_exist" / "movies"),
        download_dir_incomplete=str(local_fallback),
    ))

    assert hd._default_save_path() == str(local_fallback)


def test_default_save_path_falls_back_when_nothing_configured(tmp_path, monkeypatch):
    local_fallback = tmp_path / "local_fallback"
    local_fallback.mkdir()
    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        download_dir_incomplete=str(local_fallback),
    ))

    assert hd._default_save_path() == str(local_fallback)


def test_default_save_path_uses_tv_when_only_tv_configured(tmp_path, monkeypatch):
    library_root = tmp_path / "Plex" / "tv"
    library_root.mkdir(parents=True)
    incomplete = tmp_path / "Plex" / "incomplete"
    incomplete.mkdir()

    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        media_dir_tv=str(library_root),
        download_dir_incomplete=str(tmp_path / "local_fallback"),
    ))

    assert hd._default_save_path() == str(incomplete)


def test_default_save_path_falls_back_when_plex_configured_but_unreachable(tmp_path, monkeypatch):
    """The exact bug just reported live: typed a wrong Plex IP (Plex URL
    now unreachable), but the underlying SMB/NFS share at the media path
    was completely unaffected by that and still genuinely existed on
    disk - the old filesystem-only check kept defaulting to the
    Plex-adjacent folder anyway, which isn't what a user expects once
    they know Plex itself is down. A wrong URL doesn't take the
    underlying file share down with it, but the save-location default
    should still reflect "the media server I configured isn't working
    right now," not just "the folder happens to still exist."
    """
    library_root = tmp_path / "Plex" / "movies"
    library_root.mkdir(parents=True)
    incomplete = tmp_path / "Plex" / "incomplete"
    incomplete.mkdir()  # the share itself is fine - only Plex's URL is wrong

    local_fallback = tmp_path / "local_fallback"
    local_fallback.mkdir()
    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        plex_url="http://192.168.20.99:32400",  # the wrong IP that was typed
        plex_token="sometoken",
        media_dir_movies=str(library_root),
        download_dir_incomplete=str(local_fallback),
    ))
    monkeypatch.setattr(hd, "check_plex", lambda url, token, timeout=5.0: {
        "configured": True, "reachable": False, "error": "connection refused",
    })

    assert hd._default_save_path() == str(local_fallback)


def test_default_save_path_uses_plex_adjacent_when_plex_configured_and_reachable(tmp_path, monkeypatch):
    """The positive case for the same fix: Plex genuinely configured AND
    reachable must still use the Plex-adjacent folder as before - the
    reachability gate shouldn't make things worse for the working case.
    """
    library_root = tmp_path / "Plex" / "movies"
    library_root.mkdir(parents=True)
    incomplete = tmp_path / "Plex" / "incomplete"
    incomplete.mkdir()

    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        plex_url="http://192.168.20.33:32400",
        plex_token="sometoken",
        media_dir_movies=str(library_root),
        download_dir_incomplete=str(tmp_path / "local_fallback"),
    ))
    monkeypatch.setattr(hd, "check_plex", lambda url, token, timeout=5.0: {
        "configured": True, "reachable": True, "error": None,
    })

    assert hd._default_save_path() == str(incomplete)


def test_default_save_path_uses_jellyfin_when_plex_not_configured(tmp_path, monkeypatch):
    """Jellyfin-only setups (no Plex at all) must not get stuck falling
    back to local downloads just because there's no Plex to check -
    whichever media server is actually configured gates this, not Plex
    specifically.
    """
    library_root = tmp_path / "Media" / "movies"
    library_root.mkdir(parents=True)
    incomplete = tmp_path / "Media" / "incomplete"
    incomplete.mkdir()

    monkeypatch.setattr(hd, "load_config", lambda: _cfg(
        jellyfin_url="http://192.168.20.50:8096",
        jellyfin_api_key="somekey",
        media_dir_movies=str(library_root),
        download_dir_incomplete=str(tmp_path / "local_fallback"),
    ))
    monkeypatch.setattr(hd, "check_jellyfin", lambda url, key, timeout=5.0: {
        "configured": True, "reachable": True, "error": None,
    })

    assert hd._default_save_path() == str(incomplete)


def test_default_save_path_uses_fresh_config_not_stale_module_constants(tmp_path, monkeypatch):
    """The exact staleness bug just found live: PATCH /config updates the
    file on disk, but MEDIA_DIR_MOVIES/DOWNLOAD_DIR_INCOMPLETE are frozen
    at daemon startup and never refresh - so a value change wouldn't be
    reflected here until the whole app restarted. Deliberately sets the
    module constants to something that would produce the WRONG answer,
    to prove _default_save_path() ignores them and reads load_config()
    fresh instead.
    """
    stale_root = tmp_path / "stale_from_startup" / "movies"
    stale_root.mkdir(parents=True)
    (tmp_path / "stale_from_startup" / "incomplete").mkdir()
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(stale_root))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")
    monkeypatch.setattr(hd, "DOWNLOAD_DIR_INCOMPLETE", str(tmp_path / "stale_local_fallback"))

    fresh_root = tmp_path / "fresh_from_config" / "movies"
    fresh_root.mkdir(parents=True)
    fresh_incomplete = tmp_path / "fresh_from_config" / "incomplete"
    fresh_incomplete.mkdir()
    monkeypatch.setattr(hd, "load_config", lambda: _cfg(media_dir_movies=str(fresh_root)))

    assert hd._default_save_path() == str(fresh_incomplete)
