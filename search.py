import json
import socket
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from xml.etree import ElementTree as ET

from config import SERVER_PORT, logger
from certs import create_client_ssl_context


# ----------------------------------------------------------------------
# Size parser
# ----------------------------------------------------------------------
def parse_size_to_bytes(size_str: str) -> int:
    if not size_str:
        return 0
    size_str = size_str.upper().replace(',', '.').strip()
    try:
        num_str = ''.join(c for c in size_str if c.isdigit() or c == '.' or c == ' ')
        num = float(num_str.strip())
        if any(u in size_str for u in ['GI', 'GB', 'GIB']):
            return int(num * 1024**3)
        if any(u in size_str for u in ['MI', 'MB', 'MIB']):
            return int(num * 1024**2)
        if any(u in size_str for u in ['KI', 'KB', 'KIB']):
            return int(num * 1024)
        return int(num)
    except Exception:
        return 0


# ----------------------------------------------------------------------
# Online public search (1337x, Nyaa, TPB)
# ----------------------------------------------------------------------
def search_online_public(keyword: str) -> list:
    results = []
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    ]

    # 1337x manual scraping
    try:
        logger.info(f"Searching 1337x for '{keyword}'")
        headers = {
            'User-Agent': random.choice(user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/',
        }
        mirrors = [
            "https://1337x.st",
            "https://x1337x.ws",
            "https://1337x.is",
            "https://1337x.tw",
        ]
        added = 0
        for base_url in mirrors:
            try:
                search_url = f"{base_url}/sort-search/{quote(keyword)}/seeders/desc/1/"
                resp = requests.get(search_url, headers=headers, timeout=20, allow_redirects=True)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, 'html.parser')
                if "checking your browser" in soup.get_text().lower() or "cloudflare" in soup.get_text().lower():
                    continue
                table = soup.find('table', class_='table-list') or soup.find('table')
                if not table:
                    continue
                rows = table.find_all('tr')[1:]
                for row in rows:
                    if added >= 25:
                        break
                    cols = row.find_all('td')
                    if len(cols) < 5:
                        continue
                    name_as = cols[0].find_all('a')
                    name = name_as[1].text.strip() if len(name_as) > 1 else name_as[0].text.strip() if name_as else 'Unknown'
                    if not name or name.isdigit() or len(name) < 5:
                        continue
                    magnet_a = row.find('a', href=lambda h: h and 'magnet:' in h)
                    magnet = magnet_a['href'] if magnet_a else ''
                    if not magnet:
                        continue
                    size_text = cols[3].text.strip()
                    size_bytes = parse_size_to_bytes(size_text)
                    seeders = int(cols[1].text.strip().replace(',', '') or 0)
                    leechers = int(cols[2].text.strip().replace(',', '') or 0)
                    results.append({
                        'filename': name,
                        'size': size_bytes,
                        'seeders': seeders,
                        'leechers': leechers,
                        'magnet': magnet,
                        'source': '1337x',
                        'published': 'Unknown',
                        'engine_url': '',
                    })
                    added += 1
                if added > 0:
                    break
            except Exception:
                pass
        time.sleep(random.uniform(2, 5))
    except Exception as e:
        logger.error(f"1337x manual search failed: {e}")

    # Nyaa.si
    try:
        logger.info(f"Searching Nyaa.si for '{keyword}'")
        url = f"https://nyaa.si/?f=0&c=0_0&q={quote(keyword)}&s=seeders&o=desc"
        headers = {'User-Agent': random.choice(user_agents)}
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            rows = soup.select('table.torrent-list tbody tr')
            added = 0
            for row in rows:
                if added >= 30:
                    break
                cols = row.find_all('td')
                if len(cols) < 5:
                    continue
                name_col = cols[1].find('a', href=True)
                if not name_col:
                    continue
                name = name_col.text.strip()
                if not name or name.isdigit():
                    continue
                magnet_col = cols[2].find('a', href=lambda h: h and 'magnet:' in h)
                magnet = magnet_col['href'] if magnet_col else ''
                if not magnet:
                    continue
                size_text = cols[3].text.strip()
                size_bytes = parse_size_to_bytes(size_text)
                seeders = int(cols[5].text.strip() or 0)
                leechers = int(cols[6].text.strip() or 0)
                results.append({
                    'filename': name,
                    'size': size_bytes,
                    'seeders': seeders,
                    'leechers': leechers,
                    'magnet': magnet,
                    'source': 'nyaa',
                    'published': 'Unknown',
                    'engine_url': '',
                })
                added += 1
        time.sleep(random.uniform(2, 4))
    except Exception as e:
        logger.error(f"Nyaa.si search failed: {e}")

    # TPB via apibay
    try:
        logger.info(f"Searching TPB (apibay) for '{keyword}'")
        url = f"https://apibay.org/q.php?q={quote(keyword)}&cat="
        headers = {'User-Agent': random.choice(user_agents)}
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and data[0].get('name') != 'No results returned':
                added = 0
                for entry in data:
                    if added >= 40:
                        break
                    name = entry.get('name', 'Unknown')
                    info_hash = entry.get('info_hash')
                    if not info_hash or name == 'No results returned':
                        continue
                    magnet = (
                        f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"
                        f"&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
                        f"&tr=udp%3A%2F%2Fopen.tracker.cl%3A1337%2Fannounce"
                    )
                    size_bytes = int(entry.get('size', 0))
                    seeders = int(entry.get('seeders', 0))
                    leechers = int(entry.get('leechers', 0))
                    results.append({
                        'filename': name,
                        'size': size_bytes,
                        'seeders': seeders,
                        'leechers': leechers,
                        'magnet': magnet,
                        'source': 'thepiratebay',
                        'published': 'Unknown',
                        'engine_url': '',
                    })
                    added += 1
    except Exception as e:
        logger.error(f"TPB/apibay search failed: {e}")

    results.sort(key=lambda x: (-x.get('seeders', 0), -x.get('size', 0)))
    return results[:100]


# ----------------------------------------------------------------------
# Jackett search (pure function, no GUI refs)
# ----------------------------------------------------------------------
def search_jackett(server_url, keyword, api_key):
    """Query a Jackett torznab endpoint. Returns list of result dicts."""
    results = []
    url = f"{server_url}/api/v2.0/indexers/all/results/torznab/"
    params = {
        'q': keyword,
        'apikey': api_key,
        'cat': '1000,2000,5000,5030,5040,5045',
        'extended': '1',
    }

    resp = requests.get(url, params=params, timeout=60)
    if resp.status_code != 200:
        raise Exception(f"Jackett returned HTTP {resp.status_code}")

    root = ET.fromstring(resp.content)
    for item in root.findall('.//item'):
        title = item.find('title').text or 'Unknown'
        magnet = None
        for enc in item.findall('enclosure'):
            u = enc.get('url', '')
            if u and u.startswith('magnet:'):
                magnet = u
                break
        if not magnet:
            continue

        size = int(item.find('size').text or 0) if item.find('size') is not None else 0

        # seeders
        seeders = 0
        seeders_elem = item.find('{http://torznab.com/schemas/2015/feed}seeders')
        if seeders_elem is not None:
            seeders = int(seeders_elem.text or 0)
        else:
            for attr in item.findall('{http://torznab.com/schemas/2015/feed}attr'):
                if attr.get('name') == 'seeders':
                    seeders = int(attr.get('value') or 0)
                    break

        # peers
        peers = 0
        peers_elem = item.find('{http://torznab.com/schemas/2015/feed}peers')
        if peers_elem is not None:
            peers = int(peers_elem.text or 0)
        else:
            for attr in item.findall('{http://torznab.com/schemas/2015/feed}attr'):
                if attr.get('name') == 'peers':
                    peers = int(attr.get('value') or 0)
                    break

        # leechers
        leechers = 0
        leechers_elem = item.find('{http://torznab.com/schemas/2015/feed}leechers')
        if leechers_elem is not None:
            leechers = int(leechers_elem.text or 0)
        else:
            for attr in item.findall('{http://torznab.com/schemas/2015/feed}attr'):
                if attr.get('name') == 'leechers':
                    leechers = int(attr.get('value') or 0)
                    break
        if leechers == 0 and peers > 0:
            leechers = max(0, peers - seeders)

        pub_date = item.find('pubDate').text or 'Unknown'
        comments = item.find('comments').text or ''

        indexer = None
        for attr in item.findall('{http://torznab.com/schemas/2015/feed}attr'):
            if attr.get('name') == 'indexer':
                indexer = attr.get('value')
                break

        results.append({
            'filename': title,
            'size': size,
            'peers': [],
            'magnet': magnet,
            'seeders': seeders,
            'leechers': leechers,
            'piece_size': 0,
            'piece_hashes': [],
            'source': indexer or 'Jackett',
            'published': pub_date[:19] if pub_date != 'Unknown' else 'Unknown',
            'engine_url': comments,
        })

    logger.info(f"Jackett returned {len(results)} results")
    return results


# ----------------------------------------------------------------------
# Local index server search (pure function, no GUI refs)
# ----------------------------------------------------------------------
def search_index_server(host, keyword):
    """Query the Hydra index server over TLS. Returns list of result dicts."""
    results = []
    ctx = create_client_ssl_context(host)
    host_only = host.replace('http://', '').replace('https://', '').split(':')[0]
    port = SERVER_PORT

    with socket.create_connection((host_only, port), timeout=15) as sock:
        sock.settimeout(15)
        with ctx.wrap_socket(sock, server_hostname=host_only) as ssock:
            payload_dict = {'type': 'search', 'keyword': keyword}
            payload_str = json.dumps(payload_dict) + '\n'
            ssock.sendall(payload_str.encode('utf-8'))
            response_data = b''
            while True:
                chunk = ssock.recv(8192)
                if not chunk:
                    break
                response_data += chunk
                if b'\n' in chunk:
                    break
                if len(response_data) > 65536:
                    break
            if not response_data:
                return results
            response_text = response_data.decode('utf-8', errors='ignore').rstrip('\n')
            result = json.loads(response_text)
            for match in result.get('matches', []):
                results.append({
                    'filename': match.get('filename', 'Unknown'),
                    'peers': match.get('peers', []),
                    'size': match.get('size', 0),
                    'piece_size': match.get('piece_size', 0),
                    'piece_hashes': match.get('piece_hashes', []),
                    'source': 'Local Index',
                })
    return results
