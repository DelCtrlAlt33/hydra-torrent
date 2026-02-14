import os
import re
import shutil
import requests
from pathlib import Path
from config import (
    DOWNLOAD_DIR_COMPLETE,
    MEDIA_DIR_MOVIES,
    MEDIA_DIR_TV,
    logger,
    load_config
)


class MediaOrganizer:
    """Handles automatic categorization and organization of downloaded media files."""

    # Common TV show patterns
    TV_PATTERNS = [
        r'[Ss]\d{1,2}[Ee]\d{1,2}',  # S01E05, s01e05
        r'\d{1,2}x\d{1,2}',          # 1x05
        r'Season\s*\d+',             # Season 1
        r'Episode\s*\d+',            # Episode 5
    ]

    # Common movie patterns (year in filename)
    MOVIE_PATTERNS = [
        r'\b(19|20)\d{2}\b',  # Year 1900-2099
    ]

    # Video extensions
    VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv',
                       '.webm', '.mpg', '.mpeg', '.m4v', '.ts'}

    def __init__(self):
        self.config = load_config()

    def is_video_file(self, filename):
        """Check if file is a video file."""
        ext = os.path.splitext(filename)[1].lower()
        return ext in self.VIDEO_EXTENSIONS

    def detect_media_type(self, filename):
        """
        Detect if a file is a movie or TV show.
        Returns: 'tv', 'movie', or 'unknown'
        """
        # Check TV patterns first (more specific)
        for pattern in self.TV_PATTERNS:
            if re.search(pattern, filename, re.IGNORECASE):
                logger.info(f"Detected TV show: {filename}")
                return 'tv'

        # Check for year pattern (common in movies)
        for pattern in self.MOVIE_PATTERNS:
            if re.search(pattern, filename):
                logger.info(f"Detected movie: {filename}")
                return 'movie'

        # Default to movie if we can't determine
        logger.warning(f"Could not definitively categorize: {filename}, defaulting to movie")
        return 'movie'

    def extract_tv_info(self, filename):
        """
        Extract TV show name and season from filename.
        Returns: (show_name, season_number) or (None, None)
        """
        # Try to match S01E05 pattern
        match = re.search(r'(.+?)[.\s]S(\d{1,2})E\d{1,2}', filename, re.IGNORECASE)
        if match:
            show_name = match.group(1).replace('.', ' ').strip()
            season = int(match.group(2))
            return show_name, season

        # Try to match 1x05 pattern
        match = re.search(r'(.+?)[.\s](\d{1,2})x\d{1,2}', filename, re.IGNORECASE)
        if match:
            show_name = match.group(1).replace('.', ' ').strip()
            season = int(match.group(2))
            return show_name, season

        return None, None

    def organize_tv_show(self, filename, source_path):
        """
        Organize TV show into proper directory structure.
        Format: TV/Show Name/Season 01/filename.mkv
        Returns: destination path
        """
        show_name, season = self.extract_tv_info(filename)

        if not show_name:
            # Fallback: just use the filename prefix
            show_name = filename.split('.')[0]
            season = 1

        # Clean up show name
        show_name = re.sub(r'\W+', ' ', show_name).strip()

        # Create directory structure
        show_dir = os.path.join(MEDIA_DIR_TV, show_name)
        season_dir = os.path.join(show_dir, f"Season {season:02d}")

        os.makedirs(season_dir, exist_ok=True)

        dest_path = os.path.join(season_dir, filename)
        return dest_path

    def organize_movie(self, filename, source_path):
        """
        Organize movie into proper directory structure.
        Format: Movies/Movie Name (Year)/filename.mkv
        Returns: destination path
        """
        # Try to extract year
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        year = year_match.group(0) if year_match else None

        # Clean up movie name (everything before year or quality indicators)
        name_match = re.match(r'(.+?)(?:\.|_|\s)(19|20)\d{2}|(.+?)(?:\.|_|\s)(720p|1080p|2160p|BluRay|WEBRip|HDTV)',
                             filename, re.IGNORECASE)

        if name_match:
            movie_name = (name_match.group(1) or name_match.group(3)).replace('.', ' ').strip()
        else:
            # Fallback: use filename without extension
            movie_name = os.path.splitext(filename)[0].replace('.', ' ').strip()

        # Clean up movie name
        movie_name = re.sub(r'\W+', ' ', movie_name).strip()

        # Create directory
        if year:
            movie_dir = os.path.join(MEDIA_DIR_MOVIES, f"{movie_name} ({year})")
        else:
            movie_dir = os.path.join(MEDIA_DIR_MOVIES, movie_name)

        os.makedirs(movie_dir, exist_ok=True)

        dest_path = os.path.join(movie_dir, filename)
        return dest_path

    def move_to_plex(self, filename, source_path=None):
        """
        Automatically categorize and move a completed download to Plex media folder.

        Args:
            filename: Name of the file
            source_path: Full path to source file (optional, will use DOWNLOAD_DIR_COMPLETE if not provided)

        Returns:
            (success: bool, dest_path: str, error: str)
        """
        try:
            # Determine source path
            if not source_path:
                source_path = os.path.join(DOWNLOAD_DIR_COMPLETE, filename)

            # Check if file exists
            if not os.path.exists(source_path):
                return False, None, f"Source file not found: {source_path}"

            # Only process video files
            if not self.is_video_file(filename):
                logger.info(f"Skipping non-video file: {filename}")
                return False, None, "Not a video file"

            # Detect media type
            media_type = self.detect_media_type(filename)

            # Determine destination
            if media_type == 'tv':
                dest_path = self.organize_tv_show(filename, source_path)
            else:  # movie or unknown
                dest_path = self.organize_movie(filename, source_path)

            # Move the file
            logger.info(f"Moving {filename} to {dest_path}")

            # If destination exists, add a number suffix
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(dest_path)
                counter = 1
                while os.path.exists(f"{base} ({counter}){ext}"):
                    counter += 1
                dest_path = f"{base} ({counter}){ext}"

            shutil.move(source_path, dest_path)
            logger.info(f"Successfully moved to: {dest_path}")

            # Notify Plex if configured
            self.notify_plex()

            return True, dest_path, None

        except Exception as e:
            error_msg = f"Failed to move {filename}: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg

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


# Convenience function
def auto_move_completed_download(filename, source_path=None):
    """
    Convenience function to auto-move a completed download.
    Returns: (success: bool, dest_path: str, error: str)
    """
    organizer = MediaOrganizer()
    return organizer.move_to_plex(filename, source_path)
