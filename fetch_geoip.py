#!/usr/bin/env python3
"""Downloads the offline GeoIP country database used for peer/VPN country
flags on the map.

Source: DB-IP's Lite Country database (CC BY 4.0, no account required,
updated monthly) - not MaxMind's GeoLite2, which requires a personal
account and forbids redistribution, so it can never be auto-fetched or
bundled for a public project like this one. Data (c) DB-IP.com,
https://db-ip.com, licensed under CC BY 4.0.

Safe to run repeatedly - skips the download if the file already exists,
unless --force is passed. Called from install.sh, the Dockerfile, and
build_installer.py so nobody needs to source this file by hand.
"""
import datetime
import gzip
import os
import shutil
import sys
import urllib.error
import urllib.request

GEOIP_FILENAME = "geoip-country.mmdb"
_HERE = os.path.dirname(os.path.abspath(__file__))
DEST_PATH = os.path.join(_HERE, GEOIP_FILENAME)


def _download_month(year: int, month: int) -> bytes:
    url = f"https://download.db-ip.com/free/dbip-country-lite-{year:04d}-{month:02d}.mmdb.gz"
    print(f"[geoip] trying {url}")
    # Cloudflare (fronting db-ip.com) blocks urllib's default
    # "Python-urllib/x.x" User-Agent with a 403 - any normal-looking one
    # gets through fine.
    req = urllib.request.Request(url, headers={"User-Agent": "hydra-torrent/geoip-fetch"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch(force: bool = False) -> bool:
    if os.path.isfile(DEST_PATH) and not force:
        print(f"[geoip] {GEOIP_FILENAME} already present, skipping")
        return True

    today = datetime.date.today()
    # DB-IP publishes on the 1st of each month; usually up within a day
    # or two, but fall back to the previous month if it's not out yet.
    prev = today.replace(day=1) - datetime.timedelta(days=1)
    attempts = [(today.year, today.month), (prev.year, prev.month)]

    for year, month in attempts:
        try:
            data = _download_month(year, month)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"[geoip] {year:04d}-{month:02d} unavailable ({e}), trying previous month")
            continue

        tmp_gz = DEST_PATH + ".gz.tmp"
        try:
            with open(tmp_gz, "wb") as f:
                f.write(data)
            with gzip.open(tmp_gz, "rb") as f_in, open(DEST_PATH, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        finally:
            if os.path.isfile(tmp_gz):
                os.remove(tmp_gz)

        print(f"[geoip] saved {GEOIP_FILENAME} ({os.path.getsize(DEST_PATH):,} bytes)")
        print("[geoip] data (c) DB-IP.com, CC BY 4.0 - https://db-ip.com")
        return True

    print("[geoip] WARNING: could not download GeoIP database - country flags/map marker will be disabled")
    return False


if __name__ == "__main__":
    ok = fetch(force="--force" in sys.argv)
    sys.exit(0 if ok else 1)
