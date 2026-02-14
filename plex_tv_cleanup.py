#!/usr/bin/env python3
"""
Smart Plex TV Show Cleanup
Organizes TV shows into proper Plex structure
"""

import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

TV_PATH = r'\\192.168.20.4\Plex\tv'
DRY_RUN = False  # Set to False to apply changes

# Quality/release patterns to remove
QUALITY_PATTERNS = [
    r'\d{3,4}p',
    r'BluRay|BDRip|BRRip|WEB-?DL|WEBRip|HDTV|AMZN',
    r'x26[45]|[Hh]\.?26[45]|HEVC|XviD',
    r'AAC|AC3|DDP\d\.\d|DD\+?',
    r'10bit|5\.1|Dual Audio',
    r'COMPLETE',
    r'\[?(YTS|YIFY|RARBG|eztv\.re|TGx|EZTVx\.to)\]?',
    r'-?(BONE|EMBER|PSA|BiOMA|Kitsune|LAMA|PublicHD|BS|NVEE|Lootera|ReEnc|DeeJayAhmed|Sujaidr)',
    r'ENG\.?ITA',
]

# Manual show name fixes
SHOW_NAME_FIXES = {
    'Frieren Beyond Journey s End': 'Frieren: Beyond Journey\'s End',
    'Frieren Beyond Journeys End': 'Frieren: Beyond Journey\'s End',
}


class TVCleanup:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.changes = []
        self.stats = {'shows_organized': 0, 'episodes_moved': 0, 'folders_merged': 0}

    def clean_name(self, name):
        """Remove quality tags from name."""
        clean = name
        for pattern in QUALITY_PATTERNS:
            clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)

        clean = re.sub(r'[\.\-_]+', ' ', clean)
        clean = re.sub(r'\[.*?\]', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        return clean

    def extract_show_and_season(self, name):
        """Extract show name and season from filename/folder."""
        # Try S01 format
        match = re.search(r'(.+?)[.\s-]+S(\d{1,2})', name, re.IGNORECASE)
        if match:
            show = match.group(1)
            season = int(match.group(2))
            return self.clean_name(show), season

        # Try "Season X" format
        match = re.search(r'(.+?)[.\s-]+Season[.\s-]+(\d{1,2})', name, re.IGNORECASE)
        if match:
            show = match.group(1)
            season = int(match.group(2))
            return self.clean_name(show), season

        # Try just show name (assume Season 1)
        clean = self.clean_name(name)
        return clean, None

    def extract_episode_info(self, filename):
        """Extract show, season, episode from filename."""
        # Try S01E01 format
        match = re.search(r'(.+?)[.\s-]+S(\d{1,2})E(\d{1,2})', filename, re.IGNORECASE)
        if match:
            show = match.group(1)
            season = int(match.group(2))
            episode = int(match.group(3))
            return self.clean_name(show), season, episode

        # Try 1x01 format
        match = re.search(r'(.+?)[.\s-]+(\d{1,2})x(\d{1,2})', filename, re.IGNORECASE)
        if match:
            show = match.group(1)
            season = int(match.group(2))
            episode = int(match.group(3))
            return self.clean_name(show), season, episode

        return None, None, None

    def is_episode_file(self, filename):
        """Check if file is a TV episode."""
        patterns = [
            r'S\d{1,2}E\d{1,2}',
            r'\d{1,2}x\d{1,2}',
        ]
        for pattern in patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
        return False

    def scan_tv_shows(self):
        """Scan TV directory and plan reorganization."""
        if not os.path.exists(TV_PATH):
            print(f"ERROR: TV path not found: {TV_PATH}")
            return

        items = os.listdir(TV_PATH)
        print(f"\nScanning {len(items)} TV items...")

        # Group all content by show name
        show_data = defaultdict(lambda: {'seasons': defaultdict(list), 'loose_episodes': []})

        for item in sorted(items):
            item_path = os.path.join(TV_PATH, item)
            is_file = os.path.isfile(item_path)

            if is_file:
                # Loose episode file in root
                if self.is_episode_file(item):
                    show, season, episode = self.extract_episode_info(item)
                    if show and season:
                        show_data[show]['loose_episodes'].append({
                            'file': item,
                            'path': item_path,
                            'season': season,
                            'episode': episode
                        })
            else:
                # Folder - could be show, season, or scattered seasons
                show, season = self.extract_show_and_season(item)

                # Check if folder contains season subfolders or episodes
                contents = os.listdir(item_path)
                has_season_folders = any('season' in f.lower() for f in contents)
                has_episodes = any(self.is_episode_file(f) for f in contents if os.path.isfile(os.path.join(item_path, f)))

                if has_season_folders:
                    # This is a show folder with season subfolders
                    show_data[show]['has_structure'] = True
                    show_data[show]['main_folder'] = item
                elif season:
                    # This is a season folder
                    show_data[show]['seasons'][season].append({
                        'folder': item,
                        'path': item_path
                    })
                else:
                    # Just a show folder
                    show_data[show]['generic_folders'] = show_data[show].get('generic_folders', [])
                    show_data[show]['generic_folders'].append({
                        'folder': item,
                        'path': item_path
                    })

        # Process each show
        for show_name, data in show_data.items():
            # Apply manual fixes
            corrected_name = SHOW_NAME_FIXES.get(show_name, show_name)

            print(f"\n[TV SHOW] {show_name}")
            if corrected_name != show_name:
                print(f"  -> Corrected to: {corrected_name}")
                show_name = corrected_name

            # Count scattered seasons
            scattered_seasons = len(data['seasons'])
            loose_eps = len(data['loose_episodes'])

            if scattered_seasons > 0:
                print(f"  Found {scattered_seasons} scattered season folders")
                for season_num, folders in data['seasons'].items():
                    print(f"    Season {season_num}: {len(folders)} folder(s)")

            if loose_eps > 0:
                print(f"  Found {loose_eps} loose episode files")

            # Plan consolidation
            if scattered_seasons > 1 or loose_eps > 0:
                # Need to consolidate
                target_folder = os.path.join(TV_PATH, show_name)

                # Merge scattered season folders
                for season_num, folders in data['seasons'].items():
                    target_season = os.path.join(target_folder, f"Season {season_num:02d}")

                    for folder_info in folders:
                        self.changes.append({
                            'type': 'merge_season',
                            'show': show_name,
                            'season': season_num,
                            'old_path': folder_info['path'],
                            'old_name': folder_info['folder'],
                            'target_folder': target_season
                        })

                # Move loose episodes
                for ep in data['loose_episodes']:
                    target_season = os.path.join(target_folder, f"Season {ep['season']:02d}")
                    ext = os.path.splitext(ep['file'])[1]
                    new_filename = f"{show_name} - S{ep['season']:02d}E{ep['episode']:02d}{ext}"
                    new_path = os.path.join(target_season, new_filename)

                    self.changes.append({
                        'type': 'move_episode',
                        'show': show_name,
                        'old_path': ep['path'],
                        'old_name': ep['file'],
                        'new_path': new_path,
                        'new_name': new_filename
                    })

    def show_preview(self):
        """Show preview of changes."""
        print("\n" + "=" * 70)
        print("PREVIEW OF CHANGES")
        print("=" * 70)

        if not self.changes:
            print("\nNo changes needed! TV library looks good.")
            return

        merges = [c for c in self.changes if c['type'] == 'merge_season']
        moves = [c for c in self.changes if c['type'] == 'move_episode']

        print(f"\nTotal changes: {len(self.changes)}")
        print(f"  - Season folders to merge: {len(merges)}")
        print(f"  - Loose episodes to organize: {len(moves)}")

        if merges:
            print("\n" + "-" * 70)
            print("SEASON FOLDERS TO MERGE:")
            print("-" * 70)
            for change in merges[:10]:
                print(f"\n  {change['old_name']}")
                print(f"  -> {change['target_folder']}")

            if len(merges) > 10:
                print(f"\n  ... and {len(merges) - 10} more")

        if moves:
            print("\n" + "-" * 70)
            print("LOOSE EPISODES TO ORGANIZE:")
            print("-" * 70)
            for change in moves[:10]:
                print(f"\n  {change['old_name']}")
                print(f"  -> {change['new_name']}")
                print(f"     (in {os.path.dirname(change['new_path'])})")

            if len(moves) > 10:
                print(f"\n  ... and {len(moves) - 10} more")

    def apply_changes(self):
        """Apply the changes."""
        if self.dry_run:
            print("\n" + "=" * 70)
            print("DRY RUN MODE - No actual changes made")
            print("=" * 70)
            print("\nTo apply changes:")
            print("  Edit script: DRY_RUN = False")
            return

        print("\n" + "=" * 70)
        print("APPLYING CHANGES")
        print("=" * 70)

        # First handle season merges
        for change in [c for c in self.changes if c['type'] == 'merge_season']:
            try:
                print(f"\n[MERGE] {change['old_name']}")
                print(f"     -> {change['target_folder']}")

                os.makedirs(change['target_folder'], exist_ok=True)

                # Move all files from old folder to new
                for item in os.listdir(change['old_path']):
                    src = os.path.join(change['old_path'], item)
                    dst = os.path.join(change['target_folder'], item)

                    if os.path.exists(dst):
                        print(f"  SKIP: {item} (already exists)")
                    else:
                        shutil.move(src, dst)

                # Remove old folder if empty
                try:
                    os.rmdir(change['old_path'])
                    print("  OK")
                except OSError:
                    print("  OK (folder not empty, keeping)")

                self.stats['folders_merged'] += 1
            except Exception as e:
                print(f"  ERROR: {e}")

        # Then handle episode moves
        for change in [c for c in self.changes if c['type'] == 'move_episode']:
            try:
                print(f"\n[MOVE] {change['old_name']}")
                print(f"    -> {change['new_name']}")

                os.makedirs(os.path.dirname(change['new_path']), exist_ok=True)
                shutil.move(change['old_path'], change['new_path'])
                print("  OK")

                self.stats['episodes_moved'] += 1
            except Exception as e:
                print(f"  ERROR: {e}")

        print("\n" + "=" * 70)
        print("COMPLETE!")
        print("=" * 70)
        print(f"\nStats:")
        print(f"  Season folders merged: {self.stats['folders_merged']}")
        print(f"  Episodes organized: {self.stats['episodes_moved']}")

    def run(self):
        """Run the cleanup process."""
        print("=" * 70)
        print("SMART PLEX TV SHOW CLEANUP")
        print("=" * 70)

        self.scan_tv_shows()
        self.show_preview()

        if not self.dry_run and self.changes:
            response = input("\n\nApply these changes? (type 'yes' to confirm): ")
            if response.lower() == 'yes':
                self.apply_changes()
            else:
                print("Cancelled.")
        else:
            self.apply_changes()


if __name__ == '__main__':
    cleanup = TVCleanup(dry_run=DRY_RUN)
    cleanup.run()
