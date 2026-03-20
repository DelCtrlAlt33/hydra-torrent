"""rss_poller.py — RSS/Jackett Auto-DL poller.

Shared between peer.pyw (GUI) and hydra_daemon.py (headless daemon).
Has no FastAPI or tkinter dependency — pure threading + stdlib.

Usage:
    poller = RssPoller(add_magnet_fn=my_callback)
    poller.start()   # background thread
    poller.stop()    # on shutdown
"""
import os
import re
import uuid
import json
import time
import threading
from typing import Optional

from config import BASE_DIR, load_config, logger
from search import search_jackett

RSS_POLL_INTERVAL = 1800  # 30 minutes


class RssPoller:
    """Background thread that polls Jackett every 30 minutes per enabled rule
    and auto-adds any new matching torrents it hasn't seen before.

    add_magnet_fn: callable(magnet_uri: str) -> None
        Called when a matching torrent should be added.  In the daemon this
        wraps engine.add_magnet(); in peer.pyw it spawns a download_torrent()
        thread.
    """

    def __init__(self, add_magnet_fn, rules_path: Optional[str] = None) -> None:
        self._add_magnet = add_magnet_fn
        self._rules: dict = {}
        self._lock = threading.Lock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._path = rules_path or os.path.join(BASE_DIR, 'rss_rules.json')
        self._load()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        threading.Thread(target=self._poll_loop, daemon=True, name="rss-poller").start()

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()

    # ── Internal poll loop ───────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._check_all()
            self._wakeup.wait(timeout=RSS_POLL_INTERVAL)
            self._wakeup.clear()

    def _check_all(self) -> None:
        with self._lock:
            rules = [r for r in self._rules.values() if r['enabled']]
        for rule in rules:
            try:
                self._check_rule(rule)
            except Exception as e:
                logger.error(f"RSS rule check failed [{rule['name']}]: {e}")

    def _check_rule(self, rule: dict) -> None:
        cfg = load_config()
        server = cfg.get('indexing_server', '')
        api_key = cfg.get('jackett_api_key', '')
        if not server or not api_key:
            rule['last_checked'] = time.time()
            self._save()
            return

        results = search_jackett(server, rule['query'], api_key)

        mode = rule.get('episode_mode', 'pack')
        season = rule.get('season')
        # Build set of episode slots already downloaded (e.g. "s01e06")
        # so alternate releases of the same episode are never re-added
        matched_slots: set = set()
        for mt in rule['matched_titles']:
            slot = self._season_key(mt)
            if slot:
                matched_slots.add(slot)

        candidates = []
        for r in results:
            name = r.get('filename') or r.get('name', '')
            magnet = r.get('magnet')
            if not magnet or not name:
                continue
            if not self._matches_quality(name, rule['quality']):
                continue
            if not self._matches_season(name, season):
                continue
            is_pack = self._is_season_pack(name)
            if mode == 'pack' and not is_pack:
                continue
            if mode == 'episodes' and is_pack:
                continue
            start_ep = rule.get('start_episode')
            if start_ep and mode == 'episodes':
                ep_num = self._episode_number(name)
                if ep_num is not None and ep_num < start_ep:
                    continue
            if name in rule['matched_titles']:
                continue
            # Skip if a different release of the same episode slot was already downloaded
            slot = self._season_key(name)
            if slot and slot in matched_slots:
                continue
            candidates.append(r)

        # Sort by seeders desc — pick healthiest release per slot
        candidates.sort(key=lambda x: x.get('seeders', 0), reverse=True)

        # Dedup within one check by season slot (prevents double-adding same ep)
        seen_slots: set = set()
        deduped = []
        for r in candidates:
            name = r.get('filename') or r.get('name', '')
            slot = self._season_key(name)
            if slot is not None:
                if slot in seen_slots:
                    logger.debug(f"RSS dedup skip '{name}' (slot {slot} already queued)")
                    continue
                seen_slots.add(slot)
            deduped.append(r)

        added = 0
        for r in deduped:
            name = r.get('filename') or r.get('name', '')
            magnet = r.get('magnet')
            try:
                self._add_magnet(magnet)
                rule['matched_titles'].append(name)
                rule['matched_count'] += 1
                added += 1
                logger.info(f"RSS auto-added: '{name}' (rule: {rule['name']})")
            except Exception as e:
                logger.warning(f"RSS add_magnet failed for '{name}': {e}")

        rule['last_checked'] = time.time()
        self._save()
        if added:
            logger.info(f"RSS rule '{rule['name']}': added {added} new torrent(s)")

    # ── Matching helpers ─────────────────────────────────────────────────────

    def _matches_quality(self, name: str, quality: str) -> bool:
        if quality == "Any":
            return True
        n = name.lower()
        if quality == "4K":
            return "4k" in n or "2160p" in n
        return quality.lower() in n  # "720p" or "1080p"

    def _is_season_pack(self, name: str) -> bool:
        """True if this looks like a full-season pack rather than a single episode."""
        n = name.lower()
        if re.search(r's\d{1,2}e\d+', n):
            return False   # SxxExx → individual episode
        if re.search(r's\d{1,2}\b', n) or re.search(r'season\s*\d+', n):
            return True    # Season indicator without episode number → pack
        return True        # No season info (e.g. a movie) → treat as pack

    def _matches_season(self, name: str, season: Optional[int]) -> bool:
        """True if the result matches the requested season number (None = any)."""
        if season is None:
            return True
        n = name.lower()
        # (?<![a-z]) — don't match mid-word 's' (e.g. "kingdoms")
        # (?!\d)     — don't partially match a longer number
        # This handles both S01E05 (episode) and S01.COMPLETE (pack)
        m = re.search(r'(?<![a-z])s(\d{1,2})(?!\d)', n)
        if m and int(m.group(1)) == season:
            return True
        m = re.search(r'season\s*(\d+)', n)
        if m and int(m.group(1)) == season:
            return True
        return False

    def _episode_number(self, name: str) -> Optional[int]:
        """Return the episode number from SxxEyy, or None if not found."""
        m = re.search(r's\d{1,2}e(\d+)', name.lower())
        return int(m.group(1)) if m else None

    def _season_key(self, name: str) -> Optional[str]:
        """Return a dedup slot key for one poll cycle.

        Individual episode  → "s01e03"
        Season pack         → "s01"
        No season info      → None  (treat each as unique)
        """
        n = name.lower()
        m = re.search(r's(\d{1,2})e(\d+)', n)
        if m:
            return f"s{int(m.group(1)):02d}e{int(m.group(2)):02d}"
        m = re.search(r's(\d{1,2})\b', n)
        if m:
            return f"s{int(m.group(1)):02d}"
        m = re.search(r'season\s*(\d+)', n)
        if m:
            return f"s{int(m.group(1)):02d}"
        return None

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def add_rule(self, name: str, quality: str, query: Optional[str] = None,
                 season: Optional[int] = None, episode_mode: str = 'pack',
                 start_episode: Optional[int] = None) -> dict:
        rule = {
            "id": str(uuid.uuid4()),
            "name": name,
            "query": query or name,
            "quality": quality,
            "season": season,
            "episode_mode": episode_mode,
            "start_episode": start_episode,
            "enabled": True,
            "created": time.time(),
            "last_checked": 0.0,
            "matched_count": 0,
            "matched_titles": [],
        }
        with self._lock:
            self._rules[rule['id']] = rule
        self._save()
        self._wakeup.set()  # Trigger an immediate check for the new rule
        return rule

    def patch_rule(self, rule_id: str, **kwargs) -> Optional[dict]:
        with self._lock:
            rule = self._rules.get(rule_id)
            if not rule:
                return None
            for k, v in kwargs.items():
                if k in ('enabled', 'quality') and v is not None:
                    rule[k] = v
        self._save()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            if rule_id not in self._rules:
                return False
            del self._rules[rule_id]
        self._save()
        return True

    def get_all(self) -> list:
        with self._lock:
            return list(self._rules.values())

    def get_one(self, rule_id: str) -> Optional[dict]:
        with self._lock:
            return self._rules.get(rule_id)

    def check_now(self, rule_id: str) -> None:
        rule = self.get_one(rule_id)
        if rule:
            threading.Thread(
                target=self._check_rule, args=(rule,), daemon=True,
                name=f"rss-check-{rule_id[:8]}"
            ).start()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding='utf-8') as f:
                    for r in json.load(f):
                        self._rules[r['id']] = r
                logger.info(f"Loaded {len(self._rules)} RSS rule(s) from {self._path}")
            except Exception as e:
                logger.error(f"RssPoller._load failed: {e}")

    def _save(self) -> None:
        with self._lock:
            data = list(self._rules.values())
        try:
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"RssPoller._save failed: {e}")
