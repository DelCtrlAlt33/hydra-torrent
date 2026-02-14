#!/usr/bin/env python3
"""
Plex Library Cleanup Tool
Scans your Plex library and reorganizes files to follow Plex naming conventions.
"""

import os
import re
import json
import shutil
import requests
from pathlib import Path
from difflib import SequenceMatcher

# TMDB API (free, no key required for search)
TMDB_SEARCH_MOVIE = "https://api.themoviedb.org/3/search/movie"
TMDB_SEARCH_TV = "https://api.themoviedb.org/3/search/tv"
TMDB_API_KEY = "3fd2be6f0c70a2a598f084ddfb75487c"  # Public demo key

# Plex library paths (from TrueNAS)
MOVIES_PATH = r'\\192.168.20.4\Plex\movies'
TV_PATH = r'\\192.168.20.4\Plex\tv'


class PlexCleanup:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.changes = []

    def clean_filename(self, filename):
        """Remove quality indicators and clean up filename."""
        # Remove common quality/release tags
        patterns = [
            r'\d{3,4}p',  # 1080p, 720p, 2160p
            r'BluRay', r'BDRip', r'WEB-?DL', r'WEBRip', r'HDTV', r'HDRip',
            r'x26[45]', r'[Hh]\.?26[45]', r'HEVC', r'XviD',
            r'AAC', r'AC3', r'DDP\d\.\d', r'DD\+?',
            r'10bit', r'5\.1', r'6CH',
            r'\[.*?\]', r'\(.*?BluRay.*?\)', r'\(.*?WEB.*?\)',
            r'-[A-Z]{2,}$',  # Release groups at end
            r'COMPLETE', r'SEASON', r'Season',
            r'YTS\.?[A-Z]{2}', r'YIFY', r'RARBG', r'eztv\.re',
        ]

        clean = filename
        for pattern in patterns:
            clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)

        # Clean up separators
        clean = re.sub(r'[\.\-_]+', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        return clean

    def extract_year(self, text):
        """Extract year from filename."""
        match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
        return match.group(1) if match else None

    def extract_tv_info(self, filename):
        """Extract show name, season, episode from filename."""
        # Try S01E01 format
        match = re.search(r'(.+?)[.\s-]+S(\d{1,2})E(\d{1,2})', filename, re.IGNORECASE)
        if match:
            show = match.group(1)
            season = int(match.group(2))
            episode = int(match.group(3))
            return show, season, episode

        # Try 1x01 format
        match = re.search(r'(.+?)[.\s-]+(\d{1,2})x(\d{1,2})', filename, re.IGNORECASE)
        if match:
            show = match.group(1)
            season = int(match.group(2))
            episode = int(match.group(3))
            return show, season, episode

        return None, None, None

    def search_movie(self, title, year=None):
        """Search TMDB for movie."""
        params = {
            'api_key': TMDB_API_KEY,
            'query': title,
        }
        if year:
            params['year'] = year

        try:
            resp = requests.get(TMDB_SEARCH_MOVIE, params=params, timeout=5)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    movie = results[0]
                    return {
                        'title': movie['title'],
                        'year': movie.get('release_date', '')[:4],
                        'id': movie['id']
                    }
        except Exception as e:
            print(f"  WARNING TMDB search failed for '{title}': {e}")

        return None

    def search_tv(self, show_name):
        """Search TMDB for TV show."""
        params = {
            'api_key': TMDB_API_KEY,
            'query': show_name,
        }

        try:
            resp = requests.get(TMDB_SEARCH_TV, params=params, timeout=5)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    show = results[0]
                    return {
                        'title': show['name'],
                        'year': show.get('first_air_date', '')[:4],
                        'id': show['id']
                    }
        except Exception as e:
            print(f"  WARNING TMDB search failed for '{show_name}': {e}")

        return None

    def scan_movies(self):
        """Scan movies directory and suggest cleanup."""
        print(f"\n{'='*60}")
        print("SCANNING MOVIES")
        print(f"{'='*60}\n")

        if not os.path.exists(MOVIES_PATH):
            print(f"ERROR Movies path not found: {MOVIES_PATH}")
            return

        items = os.listdir(MOVIES_PATH)
        print(f"Found {len(items)} items\n")

        for item in sorted(items):
            item_path = os.path.join(MOVIES_PATH, item)

            # Determine if it's a file or folder
            is_file = os.path.isfile(item_path)

            if is_file:
                # File directly in movies folder (should be in a folder)
                name_without_ext = os.path.splitext(item)[0]
                ext = os.path.splitext(item)[1]
            else:
                # Folder - check if it contains video files
                name_without_ext = item
                ext = None

            # Clean the name
            clean_name = self.clean_filename(name_without_ext)
            year = self.extract_year(name_without_ext)

            # Search TMDB
            print(f"[MOVIE] {item}")
            metadata = self.search_movie(clean_name, year)

            if metadata:
                correct_name = metadata['title']
                correct_year = metadata['year']

                # Plex format: Movie Name (Year)/Movie Name (Year).ext
                new_folder_name = f"{correct_name} ({correct_year})"

                if is_file:
                    # File needs to be moved into a folder
                    new_path = os.path.join(MOVIES_PATH, new_folder_name, f"{correct_name} ({correct_year}){ext}")
                else:
                    # Folder needs to be renamed
                    new_path = os.path.join(MOVIES_PATH, new_folder_name)

                if item != new_folder_name:
                    print(f"  OK Found: {correct_name} ({correct_year})")
                    print(f"  -> New: {new_folder_name}")
                    self.changes.append({
                        'type': 'movie',
                        'old': item_path,
                        'new': new_path,
                        'is_file': is_file,
                        'display_old': item,
                        'display_new': new_folder_name
                    })
                else:
                    print(f"  OK Already correct!")
            else:
                print(f"  WARNING Could not find metadata for: {clean_name}")

            print()

    def scan_tv(self):
        """Scan TV directory and suggest cleanup."""
        print(f"\n{'='*60}")
        print("SCANNING TV SHOWS")
        print(f"{'='*60}\n")

        if not os.path.exists(TV_PATH):
            print(f"ERROR TV path not found: {TV_PATH}")
            return

        items = os.listdir(TV_PATH)
        print(f"Found {len(items)} items\n")

        # Group by show name
        shows = {}

        for item in sorted(items):
            item_path = os.path.join(TV_PATH, item)

            # Check if it's an episode file in root
            show_name, season, episode = self.extract_tv_info(item)

            if show_name:
                # Individual episode file
                clean_show = self.clean_filename(show_name)
                if clean_show not in shows:
                    shows[clean_show] = {'episodes': [], 'folders': []}
                shows[clean_show]['episodes'].append({
                    'file': item,
                    'path': item_path,
                    'season': season,
                    'episode': episode
                })
            else:
                # Folder (might be show or season)
                clean_name = self.clean_filename(item)
                if clean_name not in shows:
                    shows[clean_name] = {'episodes': [], 'folders': []}
                shows[clean_name]['folders'].append({
                    'name': item,
                    'path': item_path
                })

        # Process each show
        for show_name, data in shows.items():
            print(f"[TV] {show_name}")

            metadata = self.search_tv(show_name)
            if metadata:
                correct_name = metadata['title']
                print(f"  OK Found: {correct_name}")

                # Process loose episode files
                for ep in data['episodes']:
                    season_folder = os.path.join(TV_PATH, correct_name, f"Season {ep['season']:02d}")
                    new_filename = f"{correct_name} - S{ep['season']:02d}E{ep['episode']:02d}{os.path.splitext(ep['file'])[1]}"
                    new_path = os.path.join(season_folder, new_filename)

                    print(f"  -> Move episode: {ep['file']}")
                    print(f"     To: {correct_name}/Season {ep['season']:02d}/{new_filename}")

                    self.changes.append({
                        'type': 'tv_episode',
                        'old': ep['path'],
                        'new': new_path,
                        'show': correct_name,
                        'season': ep['season']
                    })

                # Process folders
                for folder in data['folders']:
                    if folder['name'] != correct_name:
                        new_path = os.path.join(TV_PATH, correct_name)
                        print(f"  -> Rename folder: {folder['name']} -> {correct_name}")

                        self.changes.append({
                            'type': 'tv_folder',
                            'old': folder['path'],
                            'new': new_path,
                            'show': correct_name
                        })
            else:
                print(f"  WARNING Could not find metadata")

            print()

    def show_summary(self):
        """Show summary of proposed changes."""
        print(f"\n{'='*60}")
        print("SUMMARY OF CHANGES")
        print(f"{'='*60}\n")

        if not self.changes:
            print("SUCCESS No changes needed! Library looks good.")
            return

        print(f"Total changes: {len(self.changes)}\n")

        movies = [c for c in self.changes if c['type'] == 'movie']
        tv_episodes = [c for c in self.changes if c['type'] == 'tv_episode']
        tv_folders = [c for c in self.changes if c['type'] == 'tv_folder']

        if movies:
            print(f"[FOLDER] Movies to rename/reorganize: {len(movies)}")
        if tv_episodes:
            print(f"[TV] TV episodes to organize: {len(tv_episodes)}")
        if tv_folders:
            print(f"[TV] TV folders to rename: {len(tv_folders)}")

    def apply_changes(self):
        """Apply the changes."""
        if self.dry_run:
            print("\nWARNING DRY RUN MODE - No changes will be made")
            print("Run with dry_run=False to apply changes")
            return

        print(f"\n{'='*60}")
        print("APPLYING CHANGES")
        print(f"{'='*60}\n")

        for i, change in enumerate(self.changes, 1):
            print(f"[{i}/{len(self.changes)}] Processing...")

            try:
                old_path = change['old']
                new_path = change['new']

                # Create destination directory if needed
                new_dir = os.path.dirname(new_path)
                os.makedirs(new_dir, exist_ok=True)

                # Move/rename
                if os.path.exists(old_path):
                    shutil.move(old_path, new_path)
                    print(f"  OK Moved: {os.path.basename(old_path)}")
                    print(f"     To: {new_path}")
                else:
                    print(f"  WARNING Source not found: {old_path}")

            except Exception as e:
                print(f"  ERROR Error: {e}")

            print()

        print("SUCCESS Done!")

    def run(self):
        """Run the cleanup process."""
        self.scan_movies()
        self.scan_tv()
        self.show_summary()

        if self.changes and not self.dry_run:
            response = input("\nApply these changes? (yes/no): ")
            if response.lower() in ['yes', 'y']:
                self.apply_changes()
            else:
                print("Cancelled.")


if __name__ == '__main__':
    print("=" * 60)
    print("PLEX LIBRARY CLEANUP TOOL")
    print("=" * 60)
    print("\nThis will scan your Plex library and suggest improvements.")
    print("Running in DRY RUN mode - no changes will be made yet.\n")

    cleanup = PlexCleanup(dry_run=True)
    cleanup.run()

    if cleanup.changes:
        print("\n" + "=" * 60)
        print("To apply these changes, edit the script and set:")
        print("  cleanup = PlexCleanup(dry_run=False)")
        print("=" * 60)
