#!/usr/bin/env python3
"""
Quick fixes for specific Plex naming issues
"""

import os
import shutil

MOVIES_PATH = r'\\192.168.20.4\Plex\movies'

# Specific renames needed
FIXES = {
    # Current bad name -> Correct name
    'John Wick NVEE (2014)': 'John Wick (2014)',
    'The Matrix Rlutions (2003)': 'The Matrix Revolutions (2003)',
    'The Matrix Reloaded 8CH (2003)': 'The Matrix Reloaded (2003)',
    'SpiderMan 2 3Li (2004)': 'Spider-Man 2 (2004)',
    'SpiderMan No Way Home HD TS V3 Line Audio (2021)': 'Spider-Man No Way Home (2021)',
    'Spider Man Homecoming HETeam (2017)': 'Spider-Man Homecoming (2017)',
    'Weapons s Rapta (2025)': 'Weapons (2025)',
    'www UIndex org Frankenstein (2025)': 'Frankenstein (2025)',
    'www UIndex org Warfare WEB ETHEL (2025)': 'Warfare (2025)',
    'www UIndex org Predator Badlands V2 LINE AUDIO AOC (2025)': 'Predator Badlands (2025)',

    # Twilight series
    'Twilight1 Rifftrax 2ch v2 (2008)': 'Twilight (2008)',
    'Twilight2 New Moon Rifftrax 2ch v2 (2009)': 'The Twilight Saga New Moon (2009)',
    'Twilight3 Eclipse 2010 Rifftrax 2ch v2': 'The Twilight Saga Eclipse (2010)',
    'Twilight4 Breaking Dawn Part1 Rifftrax 2ch v2 (2011)': 'The Twilight Saga Breaking Dawn Part 1 (2011)',
    'Twilight5 Breaking Dawn Part2 2012 Rifftrax 2ch v2': 'The Twilight Saga Breaking Dawn Part 2 (2012)',

    # Scary Movie series
    'Scary Movie PMTP P 5 1 H 264 PiRaTeS (2000)': 'Scary Movie (2000)',
    'Scary Movie 2 2001 PMTP P 5 1 H 264 PiRaTeS': 'Scary Movie 2 (2001)',
    'Scary Movie 3 PMTP P 5 1 H 264 PiRaTeS (2003)': 'Scary Movie 3 (2003)',

    # Studio Ghibli / Anime fixes
    'Laputa Castle in the Sky': 'Castle in the Sky (1986)',
    'Spirited Away 2001 English Dubbed': 'Spirited Away (2001)',

    # The Grudge trilogy
    'The Grudge 1, 2, 3 Horror Mystery Eng Subs': 'The Grudge (2004)',

    # Austin Powers
    'Austin Powers 1 3 Trilogy 2002 (1997)': 'Austin Powers International Man of Mystery (1997)',
}

print("=" * 70)
print("QUICK PLEX FIXES")
print("=" * 70)
print("\nFixing specific naming issues...\n")

applied = 0
not_found = []

for old_name, new_name in FIXES.items():
    old_path = os.path.join(MOVIES_PATH, old_name)
    new_path = os.path.join(MOVIES_PATH, new_name)

    if os.path.exists(old_path):
        print(f"[FIX] {old_name}")
        print(f"   -> {new_name}")
        try:
            shutil.move(old_path, new_path)
            print("   OK\n")
            applied += 1
        except Exception as e:
            print(f"   ERROR: {e}\n")
    else:
        not_found.append(old_name)

print("=" * 70)
print(f"Applied {applied} fixes")

if not_found:
    print(f"\nNot found (may already be fixed): {len(not_found)}")
    for name in not_found[:5]:
        print(f"  - {name}")

print("\n" + "=" * 70)
print("MANUAL FIXES NEEDED:")
print("=" * 70)
print("""
Some issues require manual "Fix Match" in Plex:

1. "Once upon a time in hollywood" shows as "Blade Runner 2047"
   -> In Plex: Click "..." on movie -> "Fix Match" -> Search "Blade Runner 2049"

2. "The Grudge 1, 2, 3" - This is a trilogy pack (3 movies in 1 folder)
   -> Best to manually split into 3 separate folders if you have the files

3. Austin Powers Trilogy - Only first movie will show
   -> If you have all 3 movies, split the folder into:
      - Austin Powers International Man of Mystery (1997)
      - Austin Powers The Spy Who Shagged Me (1999)
      - Austin Powers in Goldmember (2002)

4. Fight Club, The Hot Chick - May just need a Plex library refresh
   -> Go to Movies library -> ... -> Scan Library Files

After running this script:
1. Go to Plex -> Movies -> ... -> "Scan Library Files"
2. Wait for scan to complete
3. Any remaining issues: Right-click movie -> "Fix Match"
""")
