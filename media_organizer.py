import os
import re
import shutil
import requests
from pathlib import Path
from config import (
    DOWNLOAD_DIR_INCOMPLETE,
    MEDIA_DIR_MOVIES,
    MEDIA_DIR_TV,
    DOWNLOAD_DIR_OTHER,
    logger,
    load_config
)


class MediaOrganizer:
    """Handles automatic categorization and organization of downloaded media files."""

    # Common TV show patterns - checked against filename AND torrent name
    TV_PATTERNS = [
        r'[Ss]\d{1,2}[Ee]\d{1,2}',      # S01E05, s01e05
        r'\d{1,2}x\d{1,2}',             # 1x05
        r'\b[Ss]\d{1,2}\b',             # S01, S1 (season pack, no episode number)
        r'Season[\s._]*\d+',            # Season 1, Season.2, Season_3
        r'Episode[\s._]*\d+',           # Episode 5, Episode.05
        r'\bE\d{2,3}\b',               # E01, E001 (standalone, no S prefix)
        r'^\[.+?\]',                    # [SubGroup] prefix → fansub anime TV
        r'[\s._-](?!720|480|360|240|576)\d{3}[\s._\[]',  # .359. .107. (3-digit episode, not a resolution)
    ]

    # Common movie patterns (year in filename)
    MOVIE_PATTERNS = [
        r'\b(19|20)\d{2}\b',  # Year 1900-2099
    ]

    # Quality/source tags that should be stripped when cleaning names
    _STRIP_TAGS = re.compile(
        r'[.\s]?(2160p|1080p|720p|480p|4K|UHD|BluRay|BDRip|BRRip|WEB-?DL|WEBRip|'
        r'HDTV|REMUX|HDR|SDR|x264|x265|HEVC|AAC|AC3|DTS|FLAC|Complete|Batch|'
        r'Vol\.?\s*\d+|Dual\.?Audio|Multi\.?Subs?|REPACK|PROPER|EXTENDED|'
        r'UNRATED|THEATRICAL|DIRECTORS\.?CUT)',
        re.IGNORECASE,
    )

    # Video extensions
    VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv',
                       '.webm', '.mpg', '.mpeg', '.m4v', '.ts'}

    # Subtitle extensions - these ride alongside whichever video they
    # belong to (see move_alongside / hydra_daemon.py's _post_download)
    # rather than going through movie/TV classification themselves.
    SUBTITLE_EXTENSIONS = {'.srt', '.ass', '.ssa', '.vtt', '.sub'}

    def __init__(self):
        self.config = load_config()

    def is_video_file(self, filename):
        """Check if file is a video file."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in self.VIDEO_EXTENSIONS

    def is_subtitle_file(self, filename):
        """Check if file is a subtitle file."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in self.SUBTITLE_EXTENSIONS

    def detect_media_type(self, filename, torrent_name=None):
        """
        Detect if a file is a movie or TV show.
        Checks filename first, then torrent_name as fallback context.
        Returns: 'tv' or 'movie'
        """
        for candidate in filter(None, [filename, torrent_name]):
            for pattern in self.TV_PATTERNS:
                if re.search(pattern, candidate, re.IGNORECASE):
                    logger.info(f"Detected TV show: {filename} (matched in '{candidate}')")
                    return 'tv'

        # Year in filename is a strong movie indicator
        for candidate in filter(None, [filename, torrent_name]):
            if re.search(r'\b(19|20)\d{2}\b', candidate):
                logger.info(f"Detected movie: {filename}")
                return 'movie'

        logger.warning(f"Could not categorize '{filename}', defaulting to movie")
        return 'movie'

    def extract_tv_info(self, filename, torrent_name=None):
        """
        Extract show name and season number from filename (or torrent_name fallback).
        Returns: (show_name, season_number)  - season defaults to 1 for anime.
        """
        for candidate in filter(None, [filename, torrent_name]):
            result = self._parse_tv_info(candidate)
            if result[0]:
                return result

        # Last resort: clean up torrent_name if provided
        if torrent_name:
            name = self._strip_quality_tags(torrent_name)
            name = re.sub(r'\W+', ' ', name).strip()
            if name:
                return name, 1

        return None, 1

    def _parse_tv_info(self, text):
        """Try all known TV naming patterns on a single string."""
        # S01E05 / s01e05
        m = re.search(r'(.+?)[.\s_]S(\d{1,2})E\d{1,2}', text, re.IGNORECASE)
        if m:
            return m.group(1).replace('.', ' ').strip(), int(m.group(2))

        # 1x05
        m = re.search(r'(.+?)[.\s_](\d{1,2})x\d{1,2}', text, re.IGNORECASE)
        if m:
            return m.group(1).replace('.', ' ').strip(), int(m.group(2))

        # Season N  (e.g. "Show.Name.Season.2")
        m = re.search(r'(.+?)[.\s_]Season[.\s_]?(\d{1,2})', text, re.IGNORECASE)
        if m:
            return m.group(1).replace('.', ' ').strip(), int(m.group(2))

        # Bare season pack, no episode number (e.g. "Show.Name.S01.COMPLETE")
        # Negative lookahead excludes S01E05 - that's handled above already,
        # but stays defensive in case this branch is ever reordered.
        m = re.search(r'(.+?)[.\s_][Ss](\d{1,2})(?![Ee]\d)(?:[.\s_-]|$)', text)
        if m:
            show_name = self._strip_quality_tags(m.group(1).replace('.', ' ')).strip()
            if show_name:
                return show_name, int(m.group(2))

        # Anime fansub: [Group] Show Name - 001 [quality]
        # Strip the [Group] tag first, then parse "Show - 001"
        stripped = re.sub(r'^\[.+?\]\s*', '', text).strip()
        m = re.search(r'^(.+?)\s+-\s+\d{1,4}[\s\[({]', stripped)
        if m:
            show_name = self._strip_quality_tags(m.group(1)).strip()
            show_name = re.sub(r'\W+$', '', show_name).strip()
            if show_name:
                return show_name, 1

        # Show.Name.E01 / Show Name E001
        m = re.search(r'(.+?)[\s._]E(\d{2,3})\b', text, re.IGNORECASE)
        if m:
            show_name = m.group(1).replace('.', ' ').strip()
            show_name = self._strip_quality_tags(show_name).strip()
            if show_name:
                return show_name, 1

        return None, 1

    def _strip_quality_tags(self, text):
        """Remove resolution/source/codec tags from a string."""
        return self._STRIP_TAGS.sub('', text).strip(' .-_')

    def organize_tv_show(self, filename, source_path, torrent_name=None):
        """
        Organize TV show into proper directory structure.
        Format: TV/Show Name/Season 01/filename.mkv
        Returns: destination path
        """
        show_name, season = self.extract_tv_info(filename, torrent_name)

        if not show_name:
            show_name = os.path.splitext(filename)[0]
            season = 1

        # Final clean-up: remove leftover punctuation, normalise spaces
        show_name = re.sub(r'[._]+', ' ', show_name)
        show_name = re.sub(r'\s+', ' ', show_name).strip()
        # Strip trailing/leading junk characters
        show_name = re.sub(r'^[\W_]+|[\W_]+$', '', show_name).strip()

        season_dir = os.path.join(MEDIA_DIR_TV, show_name, f"Season {season:02d}")
        os.makedirs(season_dir, exist_ok=True)
        return os.path.join(season_dir, filename)

    def organize_movie(self, filename, source_path):
        """
        Organize movie into proper directory structure.
        Format: Movies/Movie Name (Year)/filename.mkv
        Returns: destination path
        """
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        year = year_match.group(0) if year_match else None

        # Everything before the year or first quality tag is the movie name
        name_match = re.match(
            r'(.+?)(?:[.\s_])(19|20)\d{2}|(.+?)(?:[.\s_])(2160p|1080p|720p|480p|BluRay|WEB-?DL|WEBRip|HDTV)',
            filename, re.IGNORECASE,
        )
        if name_match:
            raw = name_match.group(1) or name_match.group(3)
        else:
            raw = os.path.splitext(filename)[0]

        movie_name = re.sub(r'[._]+', ' ', raw)
        movie_name = re.sub(r'\s+', ' ', movie_name).strip()
        movie_name = re.sub(r'^[\W_]+|[\W_]+$', '', movie_name).strip()

        folder = f"{movie_name} ({year})" if year else movie_name
        movie_dir = os.path.join(MEDIA_DIR_MOVIES, folder)
        os.makedirs(movie_dir, exist_ok=True)
        return os.path.join(movie_dir, filename)

    def move_to_plex(self, filename, source_path=None, torrent_name=None):
        """
        Automatically categorize and move a completed download to Plex media folder.

        Args:
            filename:     Basename of the file to move.
            source_path:  Full source path (defaults to DOWNLOAD_DIR_INCOMPLETE/filename;
                          torrents are downloaded and stay there until moved out).
            torrent_name: Name of the parent torrent - used as context when the
                          individual filename is ambiguous (e.g. "01.mkv").

        Returns:
            (success: bool, dest_path: str, error: str)
        """
        try:
            if not source_path:
                source_path = os.path.join(DOWNLOAD_DIR_INCOMPLETE, filename)

            if not os.path.exists(source_path):
                return False, None, f"Source file not found: {source_path}"

            if not self.is_video_file(filename):
                return self._move_to_other_or_skip(filename, source_path, "Not a video file")

            media_type = self.detect_media_type(filename, torrent_name)
            library_dir = MEDIA_DIR_TV if media_type == 'tv' else MEDIA_DIR_MOVIES

            # No configured library path for this type - either Plex/Jellyfin
            # was never set up, or just Movies/TV was left blank. Rather than
            # leaving a Plex-classified file stranded, fall back to the same
            # general downloads path non-video files use below (this is what
            # makes DOWNLOAD_DIR_OTHER work as a real "normal torrent client,
            # no media server" download folder, not just a dumping ground for
            # non-video files).
            if not library_dir:
                return self._move_to_other_or_skip(filename, source_path, "Library path not configured")

            # A real Plex/Jellyfin library is an *existing* library - Hydra
            # should never be the thing that first creates it. Requiring the
            # configured root to already exist catches a whole class of
            # misconfiguration that would otherwise "succeed" silently:
            # a Linux NFS-style path typed into the Windows build (confirmed
            # live - Windows treats a leading '/' as "root of the current
            # drive" and happily fabricates a brand-new C:\mnt\... tree,
            # reports "Moved OK", and even triggers a Plex scan that finds
            # nothing, since Plex's real library was never touched), a typo,
            # or a network share that's just temporarily unmounted. Any of
            # these used to end up silently inventing a new folder tree in
            # a location nobody would think to check, rather than surfacing
            # as the configuration problem it actually is.
            if not os.path.isdir(library_dir):
                return self._move_to_other_or_skip(
                    filename, source_path,
                    f"Library path does not exist: {library_dir} - check media_dir_movies/media_dir_tv in Settings",
                )

            if media_type == 'tv':
                dest_path = self.organize_tv_show(filename, source_path, torrent_name)
            else:
                dest_path = self.organize_movie(filename, source_path)

            logger.info(f"Moving '{filename}' → {dest_path}")

            if os.path.exists(dest_path):
                base, ext = os.path.splitext(dest_path)
                counter = 1
                while os.path.exists(f"{base} ({counter}){ext}"):
                    counter += 1
                dest_path = f"{base} ({counter}){ext}"

            shutil.move(source_path, dest_path)
            logger.info(f"Moved OK: {dest_path}")
            self.notify_plex()
            self.notify_jellyfin()
            return True, dest_path, None

        except Exception as e:
            error_msg = f"Failed to move {filename}: {e}"
            logger.error(error_msg)
            return False, None, error_msg

    def move_alongside(self, filename, source_path, dest_dir):
        """
        Move a companion file (currently: subtitles) into a directory a
        sibling file already claimed. No classification, no library
        lookup - the caller already knows exactly where this belongs,
        because it matched it to a video that was just moved there.

        Returns: (success: bool, dest_path: str, error: str)
        """
        try:
            if not os.path.exists(source_path):
                return False, None, f"Source file not found: {source_path}"

            dest_path = os.path.join(dest_dir, filename)
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(dest_path)
                counter = 1
                while os.path.exists(f"{base} ({counter}){ext}"):
                    counter += 1
                dest_path = f"{base} ({counter}){ext}"

            shutil.move(source_path, dest_path)
            logger.info(f"Moved alongside video: {dest_path}")
            return True, dest_path, None
        except Exception as e:
            error_msg = f"Failed to move {filename}: {e}"
            logger.error(error_msg)
            return False, None, error_msg

    def _move_to_other_or_skip(self, filename, source_path, skip_reason):
        """
        General downloads fallback: used for non-video files, and for video
        files Plex/Jellyfin has no configured library path for. Flat move,
        no movie/TV folder structure, no library scan (nothing to notify).
        Returns (success, dest_path, error) same as move_to_plex.
        """
        if not DOWNLOAD_DIR_OTHER:
            logger.info(f"Skipping {filename}: {skip_reason}")
            return False, None, skip_reason

        os.makedirs(DOWNLOAD_DIR_OTHER, exist_ok=True)
        dest_path = os.path.join(DOWNLOAD_DIR_OTHER, filename)
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(dest_path)
            counter = 1
            while os.path.exists(f"{base} ({counter}){ext}"):
                counter += 1
            dest_path = f"{base} ({counter}){ext}"

        shutil.move(source_path, dest_path)
        logger.info(f"Moved to general downloads: {dest_path}")
        return True, dest_path, None

    def notify_plex(self):
        """
        Trigger Plex library scan (optional).
        Requires Plex URL and token to be configured.
        """
        try:
            plex_url = self.config.get('plex_url', '')
            plex_token = self.config.get('plex_token', '')

            if not plex_url or not plex_token:
                logger.warning("Plex auto-scan disabled: URL/token not configured in settings")
                return

            # Trigger library scan
            scan_url = f"{plex_url}/library/sections/all/refresh?X-Plex-Token={plex_token}"
            logger.info(f"Triggering Plex scan at: {plex_url}")

            response = requests.get(scan_url, timeout=10)

            if response.status_code == 200:
                logger.info("✓ Plex library scan triggered successfully")
            elif response.status_code == 401:
                logger.error("✗ Plex scan failed: Invalid token (check plex_token in settings)")
            else:
                logger.warning(f"✗ Plex scan returned status {response.status_code}")

        except requests.exceptions.Timeout:
            logger.error(f"✗ Plex scan failed: Connection timeout to {plex_url}")
        except requests.exceptions.ConnectionError:
            logger.error(f"✗ Plex scan failed: Cannot connect to {plex_url} (is Plex running?)")
        except Exception as e:
            logger.error(f"✗ Plex scan failed: {str(e)}")

    def notify_jellyfin(self):
        """
        Trigger a Jellyfin library scan (optional).
        Requires Jellyfin URL and API key to be configured.
        """
        jellyfin_url = self.config.get('jellyfin_url', '')
        try:
            jellyfin_key = self.config.get('jellyfin_api_key', '')

            if not jellyfin_url or not jellyfin_key:
                logger.warning("Jellyfin auto-scan disabled: URL/API key not configured in settings")
                return

            scan_url = f"{jellyfin_url.rstrip('/')}/Library/Refresh"
            logger.info(f"Triggering Jellyfin scan at: {jellyfin_url}")

            response = requests.post(scan_url, headers={"X-Emby-Token": jellyfin_key}, timeout=10)

            if response.status_code in (200, 204):
                logger.info("✓ Jellyfin library scan triggered successfully")
            elif response.status_code == 401:
                logger.error("✗ Jellyfin scan failed: Invalid API key (check jellyfin_api_key in settings)")
            else:
                logger.warning(f"✗ Jellyfin scan returned status {response.status_code}")

        except requests.exceptions.Timeout:
            logger.error(f"✗ Jellyfin scan failed: Connection timeout to {jellyfin_url}")
        except requests.exceptions.ConnectionError:
            logger.error(f"✗ Jellyfin scan failed: Cannot connect to {jellyfin_url} (is Jellyfin running?)")
        except Exception as e:
            logger.error(f"✗ Jellyfin scan failed: {str(e)}")


# Convenience function
def auto_move_completed_download(filename, source_path=None, torrent_name=None):
    """
    Convenience function to auto-move a completed download.
    torrent_name is the libtorrent torrent name - used as context when
    individual filenames are too bare to classify (e.g. "01.mkv").
    Returns: (success: bool, dest_path: str, error: str)
    """
    organizer = MediaOrganizer()
    return organizer.move_to_plex(filename, source_path, torrent_name=torrent_name)


# Plain module-level classification - no self.config/load_config() needed
# for an extension check, so callers (hydra_daemon.py's _post_download,
# deciding per-file which pass a file belongs in) don't have to construct
# a whole MediaOrganizer just to ask "is this a video/subtitle file".
def is_video_file(filename):
    return os.path.splitext(filename)[1].lower() in MediaOrganizer.VIDEO_EXTENSIONS


def is_subtitle_file(filename):
    return os.path.splitext(filename)[1].lower() in MediaOrganizer.SUBTITLE_EXTENSIONS


def move_subtitle_alongside_video(filename, source_path, dest_dir):
    """Convenience wrapper for MediaOrganizer.move_alongside - same shape
    as auto_move_completed_download above."""
    organizer = MediaOrganizer()
    return organizer.move_alongside(filename, source_path, dest_dir)
