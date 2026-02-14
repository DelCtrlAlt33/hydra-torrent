#!/usr/bin/env python3
"""
Smart Plex Library Cleanup
Handles edge cases, duplicates, and provides detailed preview
"""

import os
import re
import shutil
from collections import defaultdict

MOVIES_PATH = r'\\192.168.20.4\Plex\movies'
TV_PATH = r'\\192.168.20.4\Plex\tv'
DRY_RUN = False  # Set to False to apply changes

# Manual fixes for known issues
MANUAL_FIXES = {
    'American Psyco': 'American Psycho',
    'Dredd': 'Dredd',  # Not "Dre"
    'Isle of Dogs': 'Isle of Dogs',
    'Howls Moving Castle': "Howl's Moving Castle",
    'Kikis Delivery Service': "Kiki's Delivery Service",
}

# Titles that include years (don't extract year from these)
TITLES_WITH_YEARS = [
    'Blade Runner 2049',
    '2001',
    '2012',
    '2010',
]

# Quality/release patterns to remove
QUALITY_PATTERNS = [
    r'\d{3,4}p',  # Resolution
    r'BluRay|BDRip|BRRip',
    r'WEB-?DL|WEBRip',
    r'HDTV|HDRip',
    r'x26[45]|[Hh]\.?26[45]|HEVC|XviD',
    r'AAC|AC3|DDP\d\.\d|DD\+?|DTS',
    r'10bit|5\.1|6CH|Quad Audio',
    r'\bCh\b',  # Standalone "Ch" from 5.1Ch
    r'REMASTERED|DC|Special Edition|Final Cut|UNRATED',
    r'COMPLETE|SEASON',
    # Release groups and sites
    r'\[?(YTS\.?[A-Z]{2}|YIFY|RARBG|eztv\.re|TGx|EtHD)\]?',
    r'-?(BONE|JYK|PSA|EVO|LAMA|GalaxyRG\d*|Tigole|AMIABLE|VPPV|DDR|MSubs|Ozlem|DXO|jbr)',
    r'SUJAIDR|HighCode|TheWretched|MkvCage',
    r'Crazy4TV\.com',
    # Size indicators
    r'\d{3,4}MB',
    # Misc
    r'\(Multi\)',
    r'multisub|Multi\s*Audio',
    r'Mayan',  # Language indicator
    r'RUSSIAN',
    r'En-?Fr',
    r'ENG\.?ITA',
    r'\bws\b',  # "ws" from MkvCage.ws
]

# Patterns that indicate leftover junk
JUNK_PATTERNS = [
    r'^-\s*',  # Leading dash
    r'\s+$',   # Trailing spaces
    r'^\s+',   # Leading spaces
    r'\s{2,}', # Multiple spaces
]


class SmartCleanup:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.changes = []
        self.duplicates = defaultdict(list)
        self.stats = {'renamed': 0, 'merged': 0, 'folders_created': 0}

    def clean_title(self, title):
        """Aggressively clean title, removing all junk."""
        clean = title

        # Remove all quality patterns
        for pattern in QUALITY_PATTERNS:
            clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)

        # Clean separators
        clean = re.sub(r'[\.\-_]+', ' ', clean)

        # Remove brackets and contents
        clean = re.sub(r'\[.*?\]', '', clean)
        clean = re.sub(r'\((?!19|20)\d+.*?\)', '', clean)  # Remove non-year parentheses

        # Remove empty parentheses
        clean = re.sub(r'\(\s*\)', '', clean)

        # Remove standalone numbers at the end (from ranges like "1-3")
        clean = re.sub(r'\s+\d+\s+\d+\s*$', '', clean)

        # Remove trailing "Rip" variations
        clean = re.sub(r'\s+(Rip|rip)\s*$', '', clean)

        # Clean junk patterns
        for pattern in JUNK_PATTERNS:
            clean = re.sub(pattern, ' ', clean)

        clean = clean.strip()

        # Apply manual fixes
        for wrong, right in MANUAL_FIXES.items():
            if wrong.lower() in clean.lower():
                clean = re.sub(re.escape(wrong), right, clean, flags=re.IGNORECASE)

        # Final cleanup - remove multiple spaces
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Remove trailing/leading punctuation
        clean = clean.strip('.-_ ')

        return clean

    def extract_year(self, text):
        """Extract 4-digit year."""
        # Try to find year in parentheses first
        match = re.search(r'\((\d{4})\)', text)
        if match:
            year = match.group(1)
            if 1900 <= int(year) <= 2030:
                return year

        # Try to find standalone year
        match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', text)
        if match:
            return match.group(1)

        return None

    def standardize_movie(self, name, is_file=False):
        """Convert to proper Plex format: Movie Name (YEAR)"""
        if is_file:
            name_part = os.path.splitext(name)[0]
            ext = os.path.splitext(name)[1]
        else:
            name_part = name
            ext = None

        # Extract year first
        year = self.extract_year(name_part)

        # Check if title has a year in it (like "Blade Runner 2049")
        title_has_year = any(title_with_year in name_part for title_with_year in TITLES_WITH_YEARS)

        # Remove year from title (unless it's part of the title)
        if year and not title_has_year:
            name_part = re.sub(r'\(?' + re.escape(year) + r'\)?', '', name_part)

        # Clean the title
        title = self.clean_title(name_part)

        # Handle edge cases
        if not title:
            title = name_part  # Use original if cleaning fails

        # For titles with years in them, append the year in parentheses
        if title_has_year and year:
            # Make sure year isn't already in the title
            if year not in title:
                proper_name = f"{title} ({year})"
            else:
                proper_name = title
        elif year:
            proper_name = f"{title} ({year})"
        else:
            proper_name = title

        return proper_name, ext, year

    def detect_tv_episode(self, name):
        """Check if this is a TV episode file."""
        patterns = [
            r'S\d{1,2}E\d{1,2}',  # S01E01
            r'\d{1,2}x\d{1,2}',   # 1x01
        ]
        for pattern in patterns:
            if re.search(pattern, name, re.IGNORECASE):
                return True
        return False

    def scan_movies(self):
        """Scan and plan movie reorganization."""
        if not os.path.exists(MOVIES_PATH):
            print(f"ERROR: Movies path not found: {MOVIES_PATH}")
            return

        items = os.listdir(MOVIES_PATH)
        print(f"\nScanning {len(items)} movies...")

        movie_groups = defaultdict(list)  # Group by standardized name

        for item in sorted(items):
            item_path = os.path.join(MOVIES_PATH, item)
            is_file = os.path.isfile(item_path)

            # Skip TV episodes that ended up in movies
            if is_file and self.detect_tv_episode(item):
                print(f"  [SKIP] TV episode in movies folder: {item}")
                continue

            # Standardize the name
            proper_name, ext, year = self.standardize_movie(item, is_file)

            # Group by standardized name (for duplicate detection)
            movie_groups[proper_name].append({
                'original': item,
                'path': item_path,
                'is_file': is_file,
                'ext': ext,
                'year': year
            })

        # Process groups (handle duplicates)
        for proper_name, group in movie_groups.items():
            if len(group) > 1:
                # Duplicates detected
                print(f"\n[DUPLICATE] {proper_name}")
                print(f"  Found {len(group)} versions:")
                for item in group:
                    print(f"    - {item['original']}")

                # Pick best version (prefer folders over files, longer names)
                best = max(group, key=lambda x: (not x['is_file'], len(x['original'])))
                print(f"  Keeping: {best['original']}")

                for item in group:
                    if item == best:
                        # Rename if needed
                        if item['original'] != proper_name:
                            self.changes.append({
                                'type': 'rename',
                                'old_path': item['path'],
                                'old_name': item['original'],
                                'new_name': proper_name,
                                'is_file': item['is_file'],
                                'ext': item['ext']
                            })
                    else:
                        # Mark for deletion/merge
                        self.changes.append({
                            'type': 'delete_duplicate',
                            'path': item['path'],
                            'name': item['original'],
                            'duplicate_of': proper_name
                        })
            else:
                # Single item, check if rename needed
                item = group[0]
                if item['original'] != proper_name:
                    self.changes.append({
                        'type': 'rename',
                        'old_path': item['path'],
                        'old_name': item['original'],
                        'new_name': proper_name,
                        'is_file': item['is_file'],
                        'ext': item['ext']
                    })

    def show_preview(self):
        """Show detailed preview of changes."""
        print("\n" + "=" * 70)
        print("PREVIEW OF CHANGES")
        print("=" * 70)

        if not self.changes:
            print("\nNo changes needed! Library looks good.")
            return

        renames = [c for c in self.changes if c['type'] == 'rename']
        duplicates = [c for c in self.changes if c['type'] == 'delete_duplicate']

        print(f"\nTotal changes: {len(self.changes)}")
        print(f"  - Renames: {len(renames)}")
        print(f"  - Duplicates to remove: {len(duplicates)}")

        if renames:
            print("\n" + "-" * 70)
            print("RENAMES:")
            print("-" * 70)
            for change in renames[:20]:  # Show first 20
                print(f"\n  {change['old_name']}")
                print(f"  -> {change['new_name']}")
                if change['is_file']:
                    print(f"     (will create folder)")

            if len(renames) > 20:
                print(f"\n  ... and {len(renames) - 20} more")

        if duplicates:
            print("\n" + "-" * 70)
            print("DUPLICATES TO REMOVE:")
            print("-" * 70)
            for change in duplicates:
                print(f"  DELETE: {change['name']}")
                print(f"    (duplicate of: {change['duplicate_of']})")

    def apply_changes(self):
        """Apply all planned changes."""
        if self.dry_run:
            print("\n" + "=" * 70)
            print("DRY RUN MODE - No actual changes made")
            print("=" * 70)
            print("\nTo apply changes:")
            print("  1. Review the preview above")
            print("  2. Edit script: DRY_RUN = False")
            print("  3. Run again")
            return

        print("\n" + "=" * 70)
        print("APPLYING CHANGES")
        print("=" * 70)

        # First handle deletions
        for change in [c for c in self.changes if c['type'] == 'delete_duplicate']:
            try:
                print(f"\n[DELETE] {change['name']}")
                if os.path.isfile(change['path']):
                    os.remove(change['path'])
                else:
                    shutil.rmtree(change['path'])
                print("  OK")
                self.stats['merged'] += 1
            except Exception as e:
                print(f"  ERROR: {e}")

        # Then handle renames
        for change in [c for c in self.changes if c['type'] == 'rename']:
            try:
                old_path = change['old_path']
                new_name = change['new_name']

                print(f"\n[RENAME] {change['old_name']}")
                print(f"      -> {new_name}")

                if change['is_file']:
                    # Create folder and move file into it
                    new_folder = os.path.join(MOVIES_PATH, new_name)
                    os.makedirs(new_folder, exist_ok=True)
                    new_file_path = os.path.join(new_folder, f"{new_name}{change['ext']}")
                    shutil.move(old_path, new_file_path)
                    print("  OK (folder created)")
                    self.stats['folders_created'] += 1
                else:
                    # Rename folder
                    new_path = os.path.join(MOVIES_PATH, new_name)
                    if not os.path.exists(new_path):
                        shutil.move(old_path, new_path)
                        print("  OK")
                    else:
                        print("  SKIP (destination exists)")

                self.stats['renamed'] += 1
            except Exception as e:
                print(f"  ERROR: {e}")

        print("\n" + "=" * 70)
        print("COMPLETE!")
        print("=" * 70)
        print(f"\nStats:")
        print(f"  Renamed: {self.stats['renamed']}")
        print(f"  Duplicates removed: {self.stats['merged']}")
        print(f"  Folders created: {self.stats['folders_created']}")

    def run(self):
        """Run the cleanup process."""
        print("=" * 70)
        print("SMART PLEX LIBRARY CLEANUP")
        print("=" * 70)

        self.scan_movies()
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
    cleanup = SmartCleanup(dry_run=DRY_RUN)
    cleanup.run()
