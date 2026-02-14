#!/usr/bin/env python3
"""
Quick Plex Library Fixer
Fixes obvious naming issues without API calls
"""

import os
import re
import shutil

MOVIES_PATH = r'\\192.168.20.4\Plex\movies'
TV_PATH = r'\\192.168.20.4\Plex\tv'


def clean_name(name):
    """Remove quality tags and clean up"""
    # Remove quality/release tags
    tags = [
        r'\d{3,4}p', r'BluRay', r'BDRip', r'WEB-?DL', r'WEBRip', r'HDTV', r'HDRip',
        r'x26[45]', r'[Hh]\.?26[45]', r'HEVC', r'XviD', r'AAC', r'AC3', r'DDP\d\.\d',
        r'10bit', r'5\.1', r'6CH', r'\[.*?\]', r'-[A-Z]{2,}$',
        r'YTS\.?[A-Z]{2}', r'YIFY', r'RARBG', r'eztv', r'BONE', r'JYK', r'PSA',
        r'REMASTERED', r'DC', r'Special Edition', r'Final Cut', r'UNRATED',
    ]

    clean = name
    for tag in tags:
        clean = re.sub(tag, '', clean, flags=re.IGNORECASE)

    # Clean separators
    clean = re.sub(r'[\.\-_]+', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    return clean


def extract_year(text):
    """Get year from filename"""
    match = re.search(r'\((\d{4})\)|(\d{4})', text)
    if match:
        return match.group(1) or match.group(2)
    return None


def standardize_movie_name(folder_name, is_file=False):
    """Convert to: Movie Name (YEAR)"""
    year = extract_year(folder_name)

    if is_file:
        name_part = os.path.splitext(folder_name)[0]
        ext = os.path.splitext(folder_name)[1]
    else:
        name_part = folder_name
        ext = None

    # Remove year from name
    if year:
        name_part = re.sub(r'\(?' + year + r'\)?', '', name_part)

    # Clean it
    clean = clean_name(name_part).strip()

    # Standardize format
    if year:
        return f"{clean} ({year})", ext
    else:
        return clean, ext


print("=" * 60)
print("QUICK PLEX FIXER")
print("Analyzing library...")
print("=" * 60 + "\n")

# Analyze movies
movie_changes = []
if os.path.exists(MOVIES_PATH):
    items = os.listdir(MOVIES_PATH)
    print(f"Found {len(items)} movies\n")

    for item in sorted(items):
        is_file = os.path.isfile(os.path.join(MOVIES_PATH, item))
        new_name, ext = standardize_movie_name(item, is_file)

        if new_name != item and new_name != os.path.splitext(item)[0]:
            movie_changes.append({
                'old': item,
                'new': new_name,
                'is_file': is_file,
                'ext': ext
            })
            print(f"[RENAME] {item}")
            print(f"      -> {new_name}")
            if is_file:
                print(f"      (will create folder)")
            print()

print("\n" + "=" * 60)
print(f"SUMMARY: {len(movie_changes)} movies need fixes")
print("=" * 60)

print("\nThis is a preview only. No changes made.")
print("Edit the script and set DRY_RUN=False to apply changes.")
