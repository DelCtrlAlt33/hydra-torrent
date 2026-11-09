"""Regression tests for the 2026-11-10 audit (see CHANGELOG.md).

Two bugs here were reproduced against real libtorrent sessions before being
fixed, and these tests reproduce them the same way: a second, plain libtorrent
session plays "the rest of the swarm", so a test can see whether Hydra really
serves a moved file, and whether a restart really downloads anything.

  * A finished multi-file torrent whose video had been moved into the library
    was re-added after a restart pointing at the library folder, did not find
    its files there, and downloaded the whole torrent again into the library.
  * Previewing a torrent that was already a transfer handed back the live
    torrent's own handle, so closing the dialog removed the real download.

Needs libtorrent, so it runs in the engine-tests CI job (see tests.yml).
"""
import os
import shutil
import threading
import time

import libtorrent as lt
import pytest

import hydra_daemon as hd

VIDEO = "Movie.2020.1080p.mkv"
FOLDER = "Movie.2020.1080p"


def _make_folder_torrent(tmp_path, tag="t"):
    """A torrent with its own folder holding a video and an .nfo, the shape
    nearly every real release has. Returns (save_path, torrent_info, magnet)."""
    save_path = tmp_path / f"{tag}_downloads"
    folder = save_path / FOLDER
    folder.mkdir(parents=True)
    (folder / VIDEO).write_bytes(os.urandom(600 * 1024))
    (folder / "info.nfo").write_bytes(b"release notes")
    fs = lt.file_storage()
    lt.add_files(fs, str(folder))
    t = lt.create_torrent(fs, piece_size=16 * 1024)
    lt.set_piece_hashes(t, str(save_path))
    ti = lt.torrent_info(lt.bencode(t.generate()))
    return str(save_path), ti, lt.make_magnet_uri(ti)


def _video_index(ti):
    fs = ti.files()
    return next(i for i in range(fs.num_files()) if fs.file_path(i).endswith(VIDEO))


def _plain_session():
    return lt.session({
        'listen_interfaces': '127.0.0.1:0',
        'enable_dht': False, 'enable_lsd': False, 'enable_upnp': False, 'enable_natpmp': False,
        'allow_multiple_connections_per_ip': True,
    })


def _listen_port(ses, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = ses.listen_port()
        if port:
            return port
        time.sleep(0.1)
    raise AssertionError("helper session never started listening")


def _swarm_seeder(ti, save_path):
    """Another client that has the whole torrent. Returns (session, port)."""
    ses = _plain_session()
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = save_path
    handle = ses.add_torrent(params)
    assert _wait(handle, lambda s: s.is_seeding), "helper seeder never finished checking"
    return ses, _listen_port(ses)


def _wait(handle, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(handle.status()):
            return True
        time.sleep(0.1)
    return False


_CHECKING = (lt.torrent_status.states.checking_files, lt.torrent_status.states.checking_resume_data)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(hd, "load_config", lambda: {"auto_move_to_plex": True})
    store = hd.DaemonStore()
    eng = hd.TorrentEngine(store)
    eng._create_session("127.0.0.1")
    return eng


@pytest.fixture
def library(tmp_path, monkeypatch):
    movies = tmp_path / "library" / "movies"
    movies.mkdir(parents=True)
    for module in ("hydra_daemon", "media_organizer"):
        monkeypatch.setattr(f"{module}.MEDIA_DIR_MOVIES", str(movies))
        monkeypatch.setattr(f"{module}.MEDIA_DIR_TV", "")
    monkeypatch.setattr("media_organizer.DOWNLOAD_DIR_OTHER", "")
    return movies


def _seed_in_engine(engine, name, ti, magnet, save_path):
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = save_path
    handle = engine.ses.add_torrent(params)
    engine.store.add(name, magnet, ti.total_size(), handle)
    engine.store.update(name, status="Seeding", save_path=save_path)
    assert _wait(handle, lambda s: s.is_seeding), "setup failed: torrent never finished its initial check"
    return handle


# ---------------------------------------------------------------------------
# A moved file keeps seeding
# ---------------------------------------------------------------------------

def test_file_moved_into_the_library_is_still_served_to_peers(tmp_path, engine, library):
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    name = "MovedStillSeeds"
    handle = _seed_in_engine(engine, name, ti, magnet, save_path)

    engine._post_download(name, handle)

    dest = library / "Movie (2020)" / VIDEO
    assert dest.is_file(), "setup failed: the video was not moved into the library"
    record = engine.store.get(name)
    assert record["moved_map"] == {str(_video_index(ti)): str(dest)}
    assert record["moved_to_plex"] is True

    # A fresh client asks for the whole torrent. Before the fix it got 0%:
    # libtorrent was still looking for the video at its old path.
    leecher = _plain_session()
    params = lt.add_torrent_params()
    params.ti = ti
    params.save_path = str(tmp_path / "leecher")
    leech_handle = leecher.add_torrent(params)
    handle.connect_peer(("127.0.0.1", _listen_port(leecher)))
    assert _wait(leech_handle, lambda s: s.is_seeding), \
        f"a peer could not download the moved file (got {leech_handle.status().progress * 100:.0f}%)"
    assert not handle.status().error


# ---------------------------------------------------------------------------
# Restart of a moved torrent
# ---------------------------------------------------------------------------

def _restart_moved(tmp_path, engine, library, record_fields, with_saved_metadata=True):
    """Seed a folder torrent, move its video into the library the way
    _post_download does, 'restart', and let the restored torrent talk to a peer
    that has everything. Returns (restored handle, library dir of the movie)."""
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    swarm_copy = tmp_path / "swarm"
    shutil.copytree(save_path, swarm_copy)
    name = f"RestartMoved-{'-'.join(sorted(record_fields))}-{with_saved_metadata}"

    handle = _seed_in_engine(engine, name, ti, magnet, save_path)
    if with_saved_metadata:
        engine._save_resume_data()
    engine.ses.remove_torrent(handle)
    time.sleep(0.3)

    movie_dir = library / "Movie (2020)"
    movie_dir.mkdir()
    dest = movie_dir / VIDEO
    shutil.move(os.path.join(save_path, FOLDER, VIDEO), dest)

    fields = {"handle": None, "status": "Seeding", "moved_to_plex": True, "plex_path": str(movie_dir)}
    if "moved_map" in record_fields:
        fields["moved_map"] = {str(_video_index(ti)): str(dest)}
    if "moved_files" in record_fields:
        fields["moved_files"] = [str(dest)]
    engine.store.update(name, **fields)
    if not with_saved_metadata:
        # No resume_data.json entry at all for this torrent (e.g. a crash).
        engine._load_resume_data = lambda: {}

    swarm, swarm_port = _swarm_seeder(ti, str(swarm_copy))
    engine._resume_persisted()
    restored = engine.store.get_handle(name)
    assert restored is not None, "_resume_persisted did not re-attach a handle"
    restored.connect_peer(("127.0.0.1", swarm_port))
    restored._keep_alive = swarm          # keep the helper session alive for the caller
    return restored, movie_dir


@pytest.mark.parametrize("record_fields", [
    ("moved_map", "moved_files"),   # a record written by the fixed code
    ("moved_files",),               # an older record: destinations only
    (),                             # the oldest: nothing but plex_path
])
def test_restart_finds_the_moved_files_and_downloads_nothing(tmp_path, engine, library, record_fields):
    restored, movie_dir = _restart_moved(tmp_path, engine, library, record_fields)

    assert _wait(restored, lambda s: s.state not in _CHECKING and s.progress >= 1.0), \
        f"restored torrent did not find its files (progress {restored.status().progress * 100:.0f}%)"
    time.sleep(1.5)
    assert restored.status().total_payload_download == 0, "a finished torrent downloaded data again after a restart"
    assert sorted(os.listdir(movie_dir)) == [VIDEO], \
        f"the torrent was re-downloaded into the library: {sorted(os.listdir(movie_dir))}"


def test_restart_with_only_a_magnet_waits_for_metadata_then_finds_the_moved_files(tmp_path, engine, library):
    restored, movie_dir = _restart_moved(tmp_path, engine, library, ("moved_map", "moved_files"),
                                         with_saved_metadata=False)

    assert _wait(restored, lambda s: s.has_metadata and s.state not in _CHECKING and s.progress >= 1.0, timeout=30.0), \
        f"restored torrent did not find its files (progress {restored.status().progress * 100:.0f}%)"
    assert restored.status().total_payload_download == 0
    assert sorted(os.listdir(movie_dir)) == [VIDEO]


def test_finished_torrent_whose_files_are_gone_is_never_downloaded_again(tmp_path, engine):
    """The safety net behind the two tests above: whatever a restart does or
    does not find on disk, a torrent Hydra recorded as finished must not start
    downloading again."""
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    swarm_copy = tmp_path / "swarm"
    shutil.copytree(save_path, swarm_copy)
    name = "FinishedButFilesGone"

    handle = _seed_in_engine(engine, name, ti, magnet, save_path)
    engine._save_resume_data()
    engine.ses.remove_torrent(handle)
    time.sleep(0.3)
    shutil.rmtree(save_path)
    os.makedirs(save_path)
    # moved_to_plex makes the restart re-hash instead of trusting resume data,
    # so it really notices the files are missing.
    engine.store.update(name, handle=None, status="Seeding", moved_to_plex=True)

    swarm, swarm_port = _swarm_seeder(ti, str(swarm_copy))
    engine._resume_persisted()
    restored = engine.store.get_handle(name)
    restored.connect_peer(("127.0.0.1", swarm_port))
    time.sleep(5)

    assert restored.status().total_payload_download == 0
    assert os.listdir(save_path) == [], f"files were written again: {os.listdir(save_path)}"


def test_moved_layout_matches_a_destination_that_got_a_numbered_suffix(tmp_path):
    _, ti, _ = _make_folder_torrent(tmp_path)
    stem, ext = os.path.splitext(VIDEO)
    dest = os.path.join(str(tmp_path), "lib", f"{stem} (1){ext}")
    layout = hd._moved_layout(ti, {"moved_files": [dest]})
    assert layout == {_video_index(ti): dest}


# ---------------------------------------------------------------------------
# Adding something that is already there
# ---------------------------------------------------------------------------

def test_previewing_an_active_torrent_is_refused_and_leaves_it_running(tmp_path, engine):
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    name = ti.name()
    handle = _seed_in_engine(engine, name, ti, magnet, save_path)

    with pytest.raises(hd.DuplicateTorrentError) as excinfo:
        engine.preview_magnet(magnet, str(tmp_path / "elsewhere"))

    assert name in str(excinfo.value)
    assert handle.is_valid(), "the live torrent was removed from the session"
    assert [t["name"] for t in engine.store.snapshot()] == [name]
    assert engine.store.all_names() == [name], "a stray preview entry was left behind"


def test_adding_the_same_magnet_again_keeps_the_existing_record(tmp_path, engine):
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    name = ti.name()
    _seed_in_engine(engine, name, ti, magnet, save_path)
    engine.store.update(name, moved_files=["/library/x.mkv"], total_uploaded=12345)
    threads_before = {t.name for t in threading.enumerate()}

    assert engine.add_magnet(magnet, save_path) == name

    record = engine.store.get(name)
    assert record["moved_files"] == ["/library/x.mkv"]
    assert record["total_uploaded"] == 12345
    new_threads = {t.name for t in threading.enumerate()} - threads_before
    assert not [t for t in new_threads if t.startswith("dl-")], "a second monitor thread was started"


def test_adding_the_same_torrent_file_again_returns_the_existing_transfer(tmp_path, engine):
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    name = ti.name()
    _seed_in_engine(engine, name, ti, magnet, save_path)
    engine.store.update(name, total_uploaded=777)

    data = lt.bencode({b"info": lt.bdecode(bytes(ti.info_section()))})
    assert engine.add_torrent_file(data, "again.torrent") == name
    assert engine.store.get(name)["total_uploaded"] == 777
    assert engine.store.all_names() == [name]


def test_reopening_a_preview_replaces_the_stale_one(tmp_path, engine):
    """Close the add dialog and open it again on the same result: the first
    preview's delete may not have happened yet, and that must not be reported
    as "already in your transfers"."""
    _, _, magnet = _make_folder_torrent(tmp_path)
    first = engine.preview_magnet(magnet, str(tmp_path / "dl"))
    second = engine.preview_magnet(magnet, str(tmp_path / "dl"))

    assert engine.store.all_names() == [second]
    assert engine.store.get(second)["hidden"] is True
    assert engine.store.get_handle(second).is_valid()
    assert first == second or engine.store.get(first) is None


# ---------------------------------------------------------------------------
# A magnet that starts being monitored before its metadata exists
# ---------------------------------------------------------------------------

def test_monitor_fills_in_real_name_and_size_once_metadata_arrives(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(hd, "load_config", lambda: {"auto_move_to_plex": False})
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    swarm, swarm_port = _swarm_seeder(ti, save_path)

    params = lt.parse_magnet_uri(magnet)
    params.save_path = str(tmp_path / "dl")
    handle = engine.ses.add_torrent(params)
    placeholder = "0123456789abcdef"
    engine.store.add(placeholder, magnet, 0, handle)
    threading.Thread(target=engine._monitor_download, args=(placeholder, handle, 0), daemon=True).start()
    handle.connect_peer(("127.0.0.1", swarm_port))

    deadline = time.time() + 30
    while time.time() < deadline and engine.store.get(ti.name()) is None:
        time.sleep(0.2)
    record = engine.store.get(ti.name())
    assert record is not None, "the row kept its placeholder name after metadata arrived"
    assert record["size"] == ti.total_size()
    assert engine.store.get(placeholder) is None


# ---------------------------------------------------------------------------
# Kill switch changes from Settings, with no VPN connected
# ---------------------------------------------------------------------------

def test_turning_the_kill_switch_off_without_a_vpn_releases_held_torrents(tmp_path, engine):
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    name = ti.name()
    handle = _seed_in_engine(engine, name, ti, magnet, save_path)
    engine.apply_kill_switch(True)
    assert engine.store.get(name)["vpn_paused"] is True
    assert engine.ses.get_settings()["outgoing_interfaces"] == "127.0.0.1"

    engine.apply_kill_switch(False)

    assert engine.store.get(name)["vpn_paused"] is False
    assert engine.store.get(name)["paused"] is False
    assert engine.ses.get_settings()["outgoing_interfaces"] == ""
    assert engine.ses.get_settings()["listen_interfaces"].startswith("0.0.0.0:")
    assert _wait(handle, lambda s: not s.paused, timeout=5.0)


def test_kill_switch_change_is_left_alone_while_a_vpn_is_connected(engine):
    engine.vpn_ip = "10.8.0.2"
    before = engine.ses.get_settings()["listen_interfaces"]
    engine.apply_kill_switch(False)
    engine.apply_kill_switch(True)
    assert engine.ses.get_settings()["listen_interfaces"] == before


def test_resume_while_the_kill_switch_holds_everything_stays_held(tmp_path, engine, monkeypatch):
    monkeypatch.setattr(hd, "load_config", lambda: {"vpn_required": True})
    save_path, ti, magnet = _make_folder_torrent(tmp_path)
    name = ti.name()
    _seed_in_engine(engine, name, ti, magnet, save_path)
    engine.apply_kill_switch(True)
    engine.pause(name)

    engine.resume(name)

    record = engine.store.get(name)
    assert record["intended_pause"] is False
    assert record["vpn_paused"] is True, "resume cleared the kill-switch hold with no VPN connected"


# ---------------------------------------------------------------------------
# Folder settings apply without a restart
# ---------------------------------------------------------------------------

def test_changed_library_folders_reach_the_code_that_files_downloads(monkeypatch, tmp_path):
    import config
    import media_organizer
    for module in (config, media_organizer, hd):
        for const in ("MEDIA_DIR_MOVIES", "MEDIA_DIR_TV"):
            monkeypatch.setattr(module, const, getattr(module, const))
    for module in (config, media_organizer):
        monkeypatch.setattr(module, "DOWNLOAD_DIR_OTHER", module.DOWNLOAD_DIR_OTHER)

    movies, other = str(tmp_path / "m"), str(tmp_path / "o")
    hd._apply_live_folder_settings({"media_dir_movies": movies, "download_dir_other": other, "seed_ratio": 2})

    assert media_organizer.MEDIA_DIR_MOVIES == movies
    assert hd.MEDIA_DIR_MOVIES == movies
    assert config.MEDIA_DIR_MOVIES == movies
    assert media_organizer.DOWNLOAD_DIR_OTHER == other
    assert media_organizer.MEDIA_DIR_TV == config.MEDIA_DIR_TV   # not in the update: untouched
