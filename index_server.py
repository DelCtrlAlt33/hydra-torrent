#!/usr/bin/env python3
import socket
import threading
import json
import logging
import os
import time
import requests

from certs import ensure_certificates, create_server_ssl_context
from config import CHAIN_PATH

# ----------------------------------------------------------------------
# CONFIG & LOGGING
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

HOST = '0.0.0.0'
PORT = 5000

file_index = {}           # filename -> {'peers': [...], 'size': int, 'piece_size': int, 'piece_hashes': list, 'last_seen': float}
lock = threading.Lock()

PEER_TIMEOUT = 3600       # 1 hour - remove peers not seen for this long

# ----------------------------------------------------------------------
# Utility
# ----------------------------------------------------------------------
def now():
    return time.time()

def cleanup_stale_peers():
    """Remove peers that haven't registered recently"""
    with lock:
        to_remove = []
        for fname, data in file_index.items():
            data['peers'] = [
                p for p in data['peers']
                if now() - p.get('last_seen', 0) < PEER_TIMEOUT
            ]
            if not data['peers']:
                to_remove.append(fname)
        for fname in to_remove:
            del file_index[fname]
            logger.info(f"Removed stale file entry: {fname}")

# ----------------------------------------------------------------------
# Client Handler
# ----------------------------------------------------------------------
def handle_client(ssock, addr):
    peer_str = f"{addr[0]}:{addr[1]}"
    logger.info(f"Connection from {peer_str}")

    try:
        ssock.settimeout(45)
        data = b''
        while True:
            try:
                chunk = ssock.recv(32768)
                if not chunk:
                    break
                data += chunk
                if b'\n' in data or len(data) > 2*1024*1024:
                    break
            except socket.timeout:
                logger.warning(f"Timeout reading from {peer_str}")
                return
            except Exception as e:
                logger.error(f"Read error from {peer_str}: {e}")
                return

        if not data:
            return

        request_str = data.decode('utf-8', errors='ignore').rstrip()
        logger.debug(f"Request from {peer_str}: {request_str[:400]}{'...' if len(request_str)>400 else ''}")

        try:
            req = json.loads(request_str)
        except json.JSONDecodeError:
            ssock.sendall(b'{"error":"Invalid JSON"}\n')
            return

        cmd = req.get('type')

        if cmd == 'register':
            filename = req.get('filename')
            if not filename:
                ssock.sendall(b'{"error":"Missing filename"}\n')
                return

            peer = {
                'peer_ip_public': req.get('peer_ip_public'),
                'peer_ip_local':  req.get('peer_ip_local'),
                'peer_port':      req.get('peer_port'),
                'size':           req.get('size', 0),
                'last_seen':      now()
            }

            if not all([peer['peer_ip_public'], peer['peer_port'], peer['size'] > 0]):
                ssock.sendall(b'{"error":"Missing required peer/file info"}\n')
                return

            with lock:
                if filename not in file_index:
                    file_index[filename] = {
                        'peers': [],
                        'size': req.get('size', 0),
                        'piece_size': req.get('piece_size', 0),
                        'piece_hashes': req.get('piece_hashes', []),
                    }

                # Update or add peer
                existing = next((p for p in file_index[filename]['peers']
                               if p['peer_ip_public'] == peer['peer_ip_public'] and
                                  p['peer_port'] == peer['peer_port']), None)
                if existing:
                    existing.update(peer)
                else:
                    file_index[filename]['peers'].append(peer)

                # Keep consistent file metadata
                file_index[filename]['size'] = req.get('size', file_index[filename]['size'])
                file_index[filename]['piece_size'] = req.get('piece_size', file_index[filename]['piece_size'])
                if req.get('piece_hashes'):
                    file_index[filename]['piece_hashes'] = req.get('piece_hashes')

            logger.info(f"Registered {filename} from {peer['peer_ip_public']}:{peer['peer_port']}")
            ssock.sendall(b'{"status":"Registered"}\n')

        elif cmd == 'search':
            keyword = (req.get('keyword') or '').strip().lower()
            with lock:
                matches = []
                for fname, data in file_index.items():
                    if not keyword or keyword in fname.lower():
                        matches.append({
                            'filename': fname,
                            'peers': data['peers'],
                            'size': data['size'],
                            'piece_size': data.get('piece_size', 0),
                            'piece_hashes': data.get('piece_hashes', []),
                        })
            ssock.sendall((json.dumps({'matches': matches}) + '\n').encode())
            logger.info(f"Search '{keyword}' -> {len(matches)} matches sent to {peer_str}")

        elif cmd == 'unregister':
            filename = req.get('filename')
            peer_ip = req.get('peer_ip_public')
            peer_port = req.get('peer_port')
            if not all([filename, peer_ip, peer_port]):
                ssock.sendall(b'{"error":"Missing fields"}\n')
                return

            with lock:
                if filename in file_index:
                    old_len = len(file_index[filename]['peers'])
                    file_index[filename]['peers'] = [
                        p for p in file_index[filename]['peers']
                        if not (p['peer_ip_public'] == peer_ip and p['peer_port'] == peer_port)
                    ]
                    if len(file_index[filename]['peers']) < old_len:
                        logger.info(f"Unregistered {filename} from {peer_ip}:{peer_port}")
                    if not file_index[filename]['peers']:
                        del file_index[filename]
                        logger.info(f"Removed empty file entry: {filename}")

            ssock.sendall(b'{"status":"Unregistered"}\n')

        else:
            ssock.sendall(b'{"error":"Unknown command"}\n')

    except Exception as e:
        logger.error(f"Handler error for {peer_str}: {e}", exc_info=True)
    finally:
        try:
            ssock.shutdown(socket.SHUT_RDWR)
            ssock.close()
        except:
            pass

# ----------------------------------------------------------------------
# Server Main Loop
# ----------------------------------------------------------------------
def start_server():
    # Auto-generate certs if missing
    ensure_certificates()

    context = create_server_ssl_context()
    if os.path.exists(CHAIN_PATH):
        context.load_verify_locations(cafile=CHAIN_PATH)
    logger.info("TLS context loaded successfully")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, PORT))
        server_sock.listen(50)
        logger.info(f"Index server listening on {HOST}:{PORT} (TLS)")
        try:
            logger.info(f"Public IP: {requests.get('https://api.ipify.org', timeout=5).text}")
        except:
            logger.warning("Could not fetch public IP")

        last_cleanup = time.time()

        while True:
            try:
                conn, addr = server_sock.accept()
                ssock = context.wrap_socket(conn, server_side=True)
                threading.Thread(target=handle_client, args=(ssock, addr), daemon=True).start()

                # Periodic cleanup
                if time.time() - last_cleanup > 300:  # every 5 min
                    cleanup_stale_peers()
                    last_cleanup = time.time()

            except KeyboardInterrupt:
                logger.info("Shutdown requested")
                break
            except Exception as e:
                logger.error(f"Accept error: {e}")
                time.sleep(1)

if __name__ == "__main__":
    start_server()
