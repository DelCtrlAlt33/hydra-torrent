"""
Focused unit tests for small, pure hydra_daemon.py helpers that don't
need a live daemon/FastAPI app or a real torrent - see test_torrent_engine.py
for the real-libtorrent integration tier.
"""
import os

import hydra_daemon as hd


# ---------------------------------------------------------------------------
# _default_save_path (see CHANGELOG.md 2026-08-23 "SAVE AT defaulted to
# local downloads even when Plex was properly configured")
# ---------------------------------------------------------------------------

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

    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(library_root))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")
    monkeypatch.setattr(hd, "DOWNLOAD_DIR_INCOMPLETE", str(tmp_path / "local_fallback"))

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
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(library_root))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")
    monkeypatch.setattr(hd, "DOWNLOAD_DIR_INCOMPLETE", str(local_fallback))

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
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", str(tmp_path / "does_not_exist" / "movies"))
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")
    monkeypatch.setattr(hd, "DOWNLOAD_DIR_INCOMPLETE", str(local_fallback))

    assert hd._default_save_path() == str(local_fallback)


def test_default_save_path_falls_back_when_nothing_configured(tmp_path, monkeypatch):
    local_fallback = tmp_path / "local_fallback"
    local_fallback.mkdir()
    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", "")
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", "")
    monkeypatch.setattr(hd, "DOWNLOAD_DIR_INCOMPLETE", str(local_fallback))

    assert hd._default_save_path() == str(local_fallback)


def test_default_save_path_uses_tv_when_only_tv_configured(tmp_path, monkeypatch):
    library_root = tmp_path / "Plex" / "tv"
    library_root.mkdir(parents=True)
    incomplete = tmp_path / "Plex" / "incomplete"
    incomplete.mkdir()

    monkeypatch.setattr(hd, "MEDIA_DIR_MOVIES", "")
    monkeypatch.setattr(hd, "MEDIA_DIR_TV", str(library_root))
    monkeypatch.setattr(hd, "DOWNLOAD_DIR_INCOMPLETE", str(tmp_path / "local_fallback"))

    assert hd._default_save_path() == str(incomplete)
