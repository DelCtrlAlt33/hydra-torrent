#!/usr/bin/env python3
"""
Split trilogy packs into separate movie folders
"""

import os
import shutil

MOVIES_PATH = r'\\192.168.20.4\Plex\movies'

print("=" * 70)
print("SPLITTING TRILOGY PACKS")
print("=" * 70)

# ============================================================================
# THE GRUDGE TRILOGY
# ============================================================================
print("\n[1/2] Splitting The Grudge trilogy...")

grudge_base = os.path.join(MOVIES_PATH, 'The Grudge (2004)')
grudge_trilogy = os.path.join(grudge_base, 'The Grudge Trilogy')

if os.path.exists(grudge_trilogy):
    # Create folders for each movie
    grudge_folders = {
        '01 The Grudge - Unrated Extended Directors Cut Horror Eng Subs 720p [H264-mp4].mp4':
            'The Grudge (2004)',
        '02 The Grudge 2 - Unrated Directors Cut Horror Eng Subs 720p [H264-mp4].mp4':
            'The Grudge 2 (2006)',
        '03 The Grudge 3 - Horror Eng Subs 720p [H264-mp4].mp4':
            'The Grudge 3 (2009)',
    }

    for filename, folder_name in grudge_folders.items():
        src_file = os.path.join(grudge_trilogy, filename)
        dest_folder = os.path.join(MOVIES_PATH, folder_name)
        dest_file = os.path.join(dest_folder, f"{folder_name}.mp4")

        if os.path.exists(src_file):
            print(f"\n  Creating: {folder_name}")
            os.makedirs(dest_folder, exist_ok=True)
            shutil.copy2(src_file, dest_file)  # Copy instead of move to be safe
            print(f"  OK - File copied")

    # Remove old folder (optional - comment out if you want to keep original)
    try:
        shutil.rmtree(grudge_base)
        print("\n  Removed old trilogy folder")
    except Exception as e:
        print(f"\n  Kept old folder (remove manually if desired)")

else:
    print("  Not found or already split")


# ============================================================================
# AUSTIN POWERS TRILOGY
# ============================================================================
print("\n[2/2] Splitting Austin Powers trilogy...")

austin_base = os.path.join(MOVIES_PATH, 'Austin Powers International Man of Mystery (1997)')

if os.path.exists(austin_base):
    # Define the 3 movies
    austin_movies = [
        {
            'video': 'Austin Powers 1 1997 International Man of Mystery.mp4',
            'subs': 'Austin Powers 1 1997 International Man of Mystery.eng.srt',
            'folder': 'Austin Powers International Man of Mystery (1997)',
            'year': '1997'
        },
        {
            'video': 'Austin Powers 2 1999 The Spy Who Shagged Me.mp4',
            'subs': 'Austin Powers 2 1999 The Spy Who Shagged Me.eng.srt',
            'folder': 'Austin Powers The Spy Who Shagged Me (1999)',
            'year': '1999'
        },
        {
            'video': 'Austin Powers 3 2002 in Goldmember.mp4',
            'subs': 'Austin Powers 3 2002 in Goldmember.eng.srt',
            'folder': 'Austin Powers in Goldmember (2002)',
            'year': '2002'
        },
    ]

    for movie in austin_movies:
        src_video = os.path.join(austin_base, movie['video'])
        src_subs = os.path.join(austin_base, movie['subs'])
        dest_folder = os.path.join(MOVIES_PATH, movie['folder'])
        dest_video = os.path.join(dest_folder, f"{movie['folder']}.mp4")
        dest_subs = os.path.join(dest_folder, f"{movie['folder']}.eng.srt")

        if os.path.exists(src_video):
            print(f"\n  Creating: {movie['folder']}")
            os.makedirs(dest_folder, exist_ok=True)

            # Copy video
            shutil.copy2(src_video, dest_video)
            print(f"  OK - Video copied")

            # Copy subs if exist
            if os.path.exists(src_subs):
                shutil.copy2(src_subs, dest_subs)
                print(f"  OK - Subtitles copied")

    # Remove old folder (optional - comment out if you want to keep original)
    try:
        shutil.rmtree(austin_base)
        print("\n  Removed old trilogy folder")
    except Exception as e:
        print(f"\n  Kept old folder (remove manually if desired)")

else:
    print("  Not found or already split")


print("\n" + "=" * 70)
print("COMPLETE!")
print("=" * 70)
print("""
Next steps:
1. Go to Plex -> Movies -> ... -> "Scan Library Files"
2. Wait for scan to complete
3. All 6 movies should now appear separately with proper posters!

Split movies:
- The Grudge (2004)
- The Grudge 2 (2006)
- The Grudge 3 (2009)
- Austin Powers International Man of Mystery (1997)
- Austin Powers The Spy Who Shagged Me (1999)
- Austin Powers in Goldmember (2002)
""")
