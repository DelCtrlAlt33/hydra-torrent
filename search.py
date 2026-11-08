import html
import json
import socket
import requests
from defusedxml import ElementTree as ET

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
# Jackett search (pure function, no GUI refs)
# ----------------------------------------------------------------------
def search_jackett(server_url, keyword, api_key):
    """Query a Jackett torznab endpoint. Returns list of result dicts."""
    results = []
    url = f"{server_url}/api/v2.0/indexers/all/results/torznab/"
    params = {
        'q': keyword,
        'apikey': api_key,
        'extended': '1',
    }

    resp = requests.get(url, params=params, timeout=60)
    if resp.status_code != 200:
        raise Exception(f"Jackett returned HTTP {resp.status_code}")

    root = ET.fromstring(resp.content)
    for item in root.findall('.//item'):
        # Some indexers double-escape titles ("&amp;amp;"), which the XML parser
        # decodes once and leaves a literal "&amp;" in the name.
        title = html.unescape(item.find('title').text or 'Unknown')
        magnet = None
        # Check enclosure URLs
        for enc in item.findall('enclosure'):
            u = enc.get('url', '')
            if u and u.startswith('magnet:'):
                magnet = u
                break
        # Check torznab magneturl attribute
        if not magnet:
            for attr in item.findall('{http://torznab.com/schemas/2015/feed}attr'):
                if attr.get('name') == 'magneturl':
                    magnet = attr.get('value')
                    break
        # Check <link> element
        if not magnet:
            link_el = item.find('link')
            if link_el is not None and link_el.text and link_el.text.startswith('magnet:'):
                magnet = link_el.text

        # Fall back to Jackett proxy download URL (torrent file) when no magnet
        torrent_url = None
        if not magnet:
            for enc in item.findall('enclosure'):
                u = enc.get('url', '')
                if u and u.startswith('http'):
                    torrent_url = u
                    break
            if not torrent_url:
                link_el = item.find('link')
                if link_el is not None and link_el.text and link_el.text.startswith('http'):
                    torrent_url = link_el.text
        if not magnet and not torrent_url:
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
            'torrent_url': torrent_url,
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
