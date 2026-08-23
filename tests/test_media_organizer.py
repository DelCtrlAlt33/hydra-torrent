"""
Tests for media_organizer.py - Hydra's filename classification and
Plex-folder-structure logic. This is the single most important module to
keep covered: it's pure regex/string logic unique to Hydra (not something
Jackett or any upstream project maintains for us), and it's the exact kind
of "own glue code" that rotted, unnoticed, in Bobarr (the one prior project
that attempted this same all-in-one thesis) until it needed a full rewrite.

These tests are pure-function tests - no network, no real filesystem writes
except where explicitly noted (organize_* creates directories under the
isolated HYDRA_BASE set up in conftest.py).
"""
import os
import pytest

from media_organizer import MediaOrganizer
from config import MEDIA_DIR_TV, MEDIA_DIR_MOVIES


@pytest.fixture
def organizer():
    return MediaOrganizer()


# ---------------------------------------------------------------------------
# is_video_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("Movie.2024.1080p.mkv", True),
    ("Movie.2024.1080p.mp4", True),
    ("Show.S01E01.avi", True),
    ("readme.txt", False),
    ("subtitles.srt", False),
    ("archive.zip", False),
    ("cover.jpg", False),
    ("NO_EXTENSION", False),
])
def test_is_video_file(organizer, filename, expected):
    assert organizer.is_video_file(filename) is expected


# ---------------------------------------------------------------------------
# is_subtitle_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("Movie.2024.1080p.srt", True),
    ("Movie.2024.1080p.en.srt", True),
    ("Show.S01E01.ass", True),
    ("Show.S01E01.vtt", True),
    ("Movie.2024.1080p.mkv", False),
    ("cover.jpg", False),
    ("readme.txt", False),
    ("NO_EXTENSION", False),
])
def test_is_subtitle_file(organizer, filename, expected):
    assert organizer.is_subtitle_file(filename) is expected


# ---------------------------------------------------------------------------
# move_alongside (see CHANGELOG.md 2026-08-23 "subtitles should follow
# their video into Plex; everything else stays in the incomplete folder")
# ---------------------------------------------------------------------------

def test_move_alongside_moves_into_given_destination(organizer, tmp_path):
    dest_dir = tmp_path / "Plex" / "movies" / "Some Movie (2024)"
    dest_dir.mkdir(parents=True)
    src = tmp_path / "incoming" / "Some.Movie.2024.srt"
    src.parent.mkdir()
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")

    ok, dest, error = organizer.move_alongside("Some.Movie.2024.srt", str(src), str(dest_dir))

    assert ok is True
    assert error is None
    assert dest == str(dest_dir / "Some.Movie.2024.srt")
    assert os.path.exists(dest)
    assert not src.exists()


def test_move_alongside_avoids_collision(organizer, tmp_path):
    dest_dir = tmp_path / "Plex" / "movies" / "Some Movie (2024)"
    dest_dir.mkdir(parents=True)
    (dest_dir / "movie.srt").write_text("existing")
    src = tmp_path / "incoming" / "movie.srt"
    src.parent.mkdir()
    src.write_text("new")

    ok, dest, error = organizer.move_alongside("movie.srt", str(src), str(dest_dir))

    assert ok is True
    assert dest == str(dest_dir / "movie (1).srt")
    assert (dest_dir / "movie.srt").read_text() == "existing"


def test_move_alongside_reports_missing_source(organizer, tmp_path):
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    ok, dest, error = organizer.move_alongside(
        "gone.srt", str(tmp_path / "nonexistent.srt"), str(dest_dir)
    )
    assert ok is False
    assert dest is None
    assert "not found" in error.lower()


# ---------------------------------------------------------------------------
# detect_media_type - every documented pattern from the README, plus the
# ambiguous/ordering cases that make this function worth testing at all.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", [
    "Show.Name.S01E05.1080p.WEB-DL.mkv",           # S01E05
    "show.name.s1e5.mkv",                           # lowercase, single digit
    "Show.Name.1x05.720p.mkv",                      # 1x05
    "Show.Name.Season.2.Complete.mkv",              # Season N
    "Show Name Season 3.mkv",                       # Season N, spaced
    "Show.Name.Episode.12.mkv",                     # Episode N
    "[SubGroup] Show Name - 05 [1080p].mkv",        # fansub prefix
    "Show.Name.E07.mkv",                            # standalone E07
    "Show.Name.E123.mkv",                           # 3-digit episode number
])
def test_detect_media_type_tv_patterns(organizer, filename):
    assert organizer.detect_media_type(filename) == "tv"


@pytest.mark.parametrize("filename", [
    "Movie.Name.2024.1080p.BluRay.x264.mkv",
    "Another Movie (2019) 720p.mp4",
    "Old.Movie.1998.DVDRip.avi",
])
def test_detect_media_type_movie_patterns(organizer, filename):
    assert organizer.detect_media_type(filename) == "movie"


def test_detect_media_type_tv_wins_over_year_when_both_present(organizer):
    # A TV episode from a show whose title/season contains a year-like
    # number must still classify as TV - TV patterns are checked first.
    assert organizer.detect_media_type("Show.Name.2024.S01E01.mkv") == "tv"


def test_detect_media_type_falls_back_to_movie_with_no_match(organizer):
    # No TV pattern, no year - must not raise, must default to 'movie'
    # (this is the documented, deliberate fallback behavior).
    assert organizer.detect_media_type("random_download_file.mkv") == "movie"


def test_detect_media_type_uses_torrent_name_as_fallback_context(organizer):
    # A bare filename like "01.mkv" carries no info on its own - Hydra is
    # supposed to fall back to the parent torrent name for context.
    assert organizer.detect_media_type(
        "01.mkv", torrent_name="Show.Name.S01E01.1080p.WEB-DL"
    ) == "tv"


def test_detect_media_type_season_pack_without_episode_number(organizer):
    # Season packs (a whole season released as one torrent, no per-episode
    # number in the name) are a common release format and previously fell
    # through every TV_PATTERNS entry - all of which required season+episode
    # together, "Season N", or "Episode N" - silently misclassifying as a
    # movie and breaking Plex auto-sort for anyone grabbing a full season.
    # Fixed via the bare "S01" pattern; pinned here as a regression guard.
    assert organizer.detect_media_type(
        "01.mkv", torrent_name="Show.Name.S01.COMPLETE.1080p.WEB-DL"
    ) == "tv"


def test_extract_tv_info_season_pack_without_episode_number(organizer):
    show, season = organizer.extract_tv_info(
        "Show.Name.S02.COMPLETE.1080p.WEB-DL.mkv"
    )
    assert show == "Show Name"
    assert season == 2


def test_organize_tv_show_season_pack_lands_in_correct_season_folder(organizer):
    dest = organizer.organize_tv_show(
        "Show.Name.S03.COMPLETE.1080p.mkv", source_path="/tmp/src"
    )
    expected = os.path.join(MEDIA_DIR_TV, "Show Name", "Season 03", "Show.Name.S03.COMPLETE.1080p.mkv")
    assert dest == expected


def test_three_digit_number_does_not_false_positive_as_resolution(organizer):
    # Regression guard for the deliberately negative-lookahead pattern:
    # a 3-digit episode number must be detected as TV, but 720p/1080p
    # resolution tags must NOT be misread as an episode number.
    assert organizer.detect_media_type("[Group] Show Name - 359 [1080p].mkv") == "tv"


# ---------------------------------------------------------------------------
# extract_tv_info - show name + season extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected_show,expected_season", [
    ("Show.Name.S01E05.1080p.mkv", "Show Name", 1),
    ("Show.Name.S02E10.720p.mkv", "Show Name", 2),
    ("Show.Name.1x05.mkv", "Show Name", 1),
    ("Show.Name.Season.3.mkv", "Show Name", 3),
])
def test_extract_tv_info_show_and_season(organizer, filename, expected_show, expected_season):
    show, season = organizer.extract_tv_info(filename)
    assert show == expected_show
    assert season == expected_season


def test_extract_tv_info_fansub_anime_defaults_season_1(organizer):
    show, season = organizer.extract_tv_info("[SubGroup] Naruto - 105 [1080p].mkv")
    assert show == "Naruto"
    assert season == 1


def test_extract_tv_info_strips_quality_tags_from_show_name(organizer):
    # Standalone-E pattern path runs the extracted name through
    # _strip_quality_tags - a resolution/codec tag stuck to the show name
    # must not leak into the folder name.
    show, season = organizer.extract_tv_info("Show.Name.1080p.E07.mkv")
    assert "1080p" not in show
    assert season == 1


def test_extract_tv_info_no_match_returns_none(organizer):
    show, season = organizer.extract_tv_info("Movie.Name.2024.1080p.mkv")
    assert show is None
    assert season == 1


def test_extract_tv_info_falls_back_to_cleaned_torrent_name(organizer):
    # When the filename itself carries no parseable info (e.g. "01.mkv"),
    # extract_tv_info should still recover a usable show name from the
    # torrent name, even if the torrent name itself doesn't match a
    # season/episode pattern.
    show, season = organizer.extract_tv_info(
        "01.mkv", torrent_name="Show.Name.Complete.1080p.WEB-DL"
    )
    assert show  # non-empty - some reasonable name was recovered
    assert "1080p" not in show


# ---------------------------------------------------------------------------
# organize_tv_show / organize_movie - destination path construction
# ---------------------------------------------------------------------------

def test_organize_tv_show_builds_expected_path(organizer):
    dest = organizer.organize_tv_show("Show.Name.S01E05.1080p.mkv", source_path="/tmp/src")
    expected = os.path.join(MEDIA_DIR_TV, "Show Name", "Season 01", "Show.Name.S01E05.1080p.mkv")
    assert dest == expected
    assert os.path.isdir(os.path.join(MEDIA_DIR_TV, "Show Name", "Season 01"))


def test_organize_tv_show_pads_season_number(organizer):
    dest = organizer.organize_tv_show("Show.Name.S01E05.1080p.mkv", source_path="/tmp/src")
    assert "Season 01" in dest  # not "Season 1"


def test_organize_tv_show_double_digit_season_no_extra_padding(organizer):
    dest = organizer.organize_tv_show("Show.Name.S12E05.1080p.mkv", source_path="/tmp/src")
    assert "Season 12" in dest


def test_organize_tv_show_falls_back_to_filename_stem_when_unparseable(organizer):
    # A completely unparseable filename must not raise - it should fall
    # back to using the filename itself (minus extension) as the show
    # folder, so a download never silently fails to land somewhere.
    dest = organizer.organize_tv_show("weird_file_no_pattern.mkv", source_path="/tmp/src")
    assert dest  # produced *some* destination rather than raising


def test_organize_movie_builds_expected_path_with_year(organizer):
    dest = organizer.organize_movie("Movie.Name.2024.1080p.BluRay.mkv", source_path="/tmp/src")
    expected = os.path.join(MEDIA_DIR_MOVIES, "Movie Name (2024)", "Movie.Name.2024.1080p.BluRay.mkv")
    assert dest == expected


def test_organize_movie_without_year_omits_parens(organizer):
    dest = organizer.organize_movie("Movie.Name.BluRay.1080p.mkv", source_path="/tmp/src")
    folder = os.path.basename(os.path.dirname(dest))
    assert "(" not in folder  # no year → no "(Year)" suffix
    assert "BluRay" not in folder  # quality tag should still be stripped from the name


# ---------------------------------------------------------------------------
# move_to_plex - the actual filesystem side effect, isolated to tmp_path
# ---------------------------------------------------------------------------

def test_move_to_plex_missing_source_returns_error(organizer, tmp_path):
    ok, dest, error = organizer.move_to_plex(
        "does_not_exist.mkv", source_path=str(tmp_path / "does_not_exist.mkv")
    )
    assert ok is False
    assert dest is None
    assert "not found" in error.lower()


def test_move_to_plex_non_video_file_reports_not_a_video(organizer, tmp_path, monkeypatch):
    # With DOWNLOAD_DIR_OTHER unset (the default in an isolated test env),
    # a non-video file should be skipped, not silently misfiled as media.
    monkeypatch.setattr("media_organizer.DOWNLOAD_DIR_OTHER", "")
    src = tmp_path / "readme.txt"
    src.write_text("hello")
    ok, dest, error = organizer.move_to_plex("readme.txt", source_path=str(src))
    assert ok is False
    assert error == "Not a video file"
    assert src.exists()  # untouched


def test_move_to_plex_moves_tv_episode_into_season_folder(organizer, tmp_path, monkeypatch):
    monkeypatch.setattr("media_organizer.notify_plex", lambda self: None, raising=False)
    src = tmp_path / "Show.Name.S01E05.1080p.mkv"
    src.write_bytes(b"fake video data")

    ok, dest, error = organizer.move_to_plex(
        "Show.Name.S01E05.1080p.mkv", source_path=str(src)
    )

    assert ok is True
    assert error is None
    assert os.path.exists(dest)
    assert not src.exists()  # moved, not copied
    assert "Season 01" in dest


def test_move_to_plex_skips_video_when_library_path_not_configured(organizer, tmp_path, monkeypatch):
    # A blank media_dir_movies/tv (the default until a user actually sets
    # one) must skip the move quietly instead of attempting os.makedirs()
    # against an unconfigured/unreachable path - this used to default to a
    # hardcoded NAS UNC path and fail (slowly) for every user who never set
    # up Plex/Jellyfin at all.
    monkeypatch.setattr("media_organizer.MEDIA_DIR_MOVIES", "")
    src = tmp_path / "Movie.Name.2024.1080p.mkv"
    src.write_bytes(b"fake video data")

    ok, dest, error = organizer.move_to_plex(
        "Movie.Name.2024.1080p.mkv", source_path=str(src)
    )

    assert ok is False
    assert dest is None
    assert error == "Library path not configured"
    assert src.exists()  # untouched, not lost


def test_move_to_plex_video_falls_back_to_other_dir_when_library_not_configured(organizer, tmp_path, monkeypatch):
    # This is what makes DOWNLOAD_DIR_OTHER work as a real "normal torrent
    # client, no media server" download folder: a video file Hydra can't
    # file into Plex/Jellyfin (no library path configured) should land in
    # the general downloads folder flat, not get stranded, and not error.
    monkeypatch.setattr("media_organizer.MEDIA_DIR_MOVIES", "")
    other_dir = tmp_path / "other"
    monkeypatch.setattr("media_organizer.DOWNLOAD_DIR_OTHER", str(other_dir))
    src = tmp_path / "Movie.Name.2024.1080p.mkv"
    src.write_bytes(b"fake video data")

    ok, dest, error = organizer.move_to_plex(
        "Movie.Name.2024.1080p.mkv", source_path=str(src)
    )

    assert ok is True
    assert error is None
    assert dest == str(other_dir / "Movie.Name.2024.1080p.mkv")
    assert os.path.exists(dest)
    assert not src.exists()


def test_move_to_plex_skips_when_library_path_configured_but_nonexistent(organizer, tmp_path, monkeypatch):
    # A configured-but-nonexistent library root is a different, more
    # dangerous case than the blank-path test above: os.makedirs(...,
    # exist_ok=True) would happily fabricate a brand-new folder tree at
    # whatever the path resolves to and report success - confirmed live
    # against a real Windows install with media_dir_movies set to a
    # Linux-style path ('/mnt/plex/movies'), which Windows silently
    # reinterpreted as "root of the current drive" and created a bogus
    # C:\mnt\plex\movies tree, logged "Moved OK", and even triggered a
    # Plex scan that predictably found nothing. A real Plex/Jellyfin
    # library already exists - Hydra should never be the thing that
    # first creates it, so a missing root must be treated as a
    # misconfiguration, same as an unset path, not silently "fixed" by
    # creating it.
    monkeypatch.setattr("media_organizer.MEDIA_DIR_MOVIES", str(tmp_path / "does_not_exist"))
    src = tmp_path / "Movie.Name.2024.1080p.mkv"
    src.write_bytes(b"fake video data")

    ok, dest, error = organizer.move_to_plex(
        "Movie.Name.2024.1080p.mkv", source_path=str(src)
    )

    assert ok is False
    assert dest is None
    assert "does not exist" in error
    assert src.exists()  # untouched, not lost
    assert not (tmp_path / "does_not_exist").exists()  # not silently fabricated


def test_move_to_plex_falls_back_to_other_dir_when_library_path_nonexistent(organizer, tmp_path, monkeypatch):
    # Same misconfiguration as above, but with DOWNLOAD_DIR_OTHER
    # configured - should land there flat, exactly like the blank-path
    # case, not get stranded or silently mis-filed.
    monkeypatch.setattr("media_organizer.MEDIA_DIR_MOVIES", str(tmp_path / "does_not_exist"))
    other_dir = tmp_path / "other"
    monkeypatch.setattr("media_organizer.DOWNLOAD_DIR_OTHER", str(other_dir))
    src = tmp_path / "Movie.Name.2024.1080p.mkv"
    src.write_bytes(b"fake video data")

    ok, dest, error = organizer.move_to_plex(
        "Movie.Name.2024.1080p.mkv", source_path=str(src)
    )

    assert ok is True
    assert error is None
    assert dest == str(other_dir / "Movie.Name.2024.1080p.mkv")
    assert os.path.exists(dest)
    assert not src.exists()
    assert not (tmp_path / "does_not_exist").exists()


def test_move_to_plex_non_video_file_moves_to_configured_other_dir(organizer, tmp_path, monkeypatch):
    other_dir = tmp_path / "other"
    monkeypatch.setattr("media_organizer.DOWNLOAD_DIR_OTHER", str(other_dir))
    src = tmp_path / "ubuntu.iso"
    src.write_bytes(b"fake iso data")

    ok, dest, error = organizer.move_to_plex("ubuntu.iso", source_path=str(src))

    assert ok is True
    assert error is None
    assert dest == str(other_dir / "ubuntu.iso")
    assert os.path.exists(dest)
    assert not src.exists()  # moved, not copied


def test_move_to_plex_avoids_overwriting_existing_file_at_destination(organizer, tmp_path):
    # If a file with the same name already exists at the computed
    # destination, Hydra must not silently clobber it.
    src = tmp_path / "Movie.Name.2024.1080p.mkv"
    src.write_bytes(b"new copy")

    dest_dir = os.path.join(MEDIA_DIR_MOVIES, "Movie Name (2024)")
    os.makedirs(dest_dir, exist_ok=True)
    existing = os.path.join(dest_dir, "Movie.Name.2024.1080p.mkv")
    with open(existing, "wb") as f:
        f.write(b"already there")

    ok, dest, error = organizer.move_to_plex(
        "Movie.Name.2024.1080p.mkv", source_path=str(src)
    )

    assert ok is True
    assert dest != existing  # got a "(1)" suffix instead of overwriting
    assert os.path.exists(existing)
    with open(existing, "rb") as f:
        assert f.read() == b"already there"  # original untouched
