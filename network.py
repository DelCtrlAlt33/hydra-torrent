import os
import json
import socket
import hashlib
import asyncio
import platform
import requests
import aiofiles
import miniupnpc

from config import (
    SHARED_DIR, PEER_PORT, SERVER_PORT, CHUNK_SIZE, logger,
)
from certs import create_server_ssl_context, create_client_ssl_context


# ----------------------------------------------------------------------
# Public IP & UPnP
# ----------------------------------------------------------------------
def get_public_ip():
    try:
        return requests.get('https://api.ipify.org?format=json', timeout=5).json()['ip']
    except Exception:
        return '127.0.0.1'


MY_IP = get_public_ip()


def setup_upnp():
    try:
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        upnp.discover()
        upnp.selectigd()
        ext_ip = upnp.externalipaddress()
        success = upnp.addportmapping(
            PEER_PORT, 'TCP', upnp.lanaddr, PEER_PORT, 'Hydra Peer', ''
        )
        if success:
            logger.info(f"UPnP: {PEER_PORT}/TCP → {ext_ip}")
            return ext_ip
    except Exception as e:
        logger.warning(f"UPnP failed: {e}")
    return MY_IP


MY_PUBLIC_IP = setup_upnp()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def compute_piece_hashes(file_path, piece_size=CHUNK_SIZE):
    piece_hashes = []
    with open(file_path, 'rb') as f:
        while True:
            piece_data = f.read(piece_size)
            if not piece_data:
                break
            piece_hashes.append(hashlib.sha256(piece_data).hexdigest())
    return piece_hashes


# ----------------------------------------------------------------------
# Async peer file server
# ----------------------------------------------------------------------
async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info('peername')
    logger.info(f"Secure connection from {addr}")
    try:
        request_data = await asyncio.wait_for(reader.readuntil(b'\n'), timeout=10.0)
        request_str = request_data.decode('utf-8', errors='ignore').rstrip('\n')
        try:
            request = json.loads(request_str)
            filename = request.get('filename')
            range_info = request.get('range', {})
        except json.JSONDecodeError:
            filename = request_str.strip()
            logger.debug(f"Legacy plain filename request from {addr}")
            range_info = {}

        if not filename or '..' in filename or filename.startswith('/') or filename.startswith('\\'):
            writer.write(b"Invalid request")
            await writer.drain()
            return

        safe_shared = os.path.realpath(SHARED_DIR)
        file_path = os.path.realpath(os.path.join(SHARED_DIR, filename))
        if not file_path.startswith(safe_shared + os.sep) and file_path != safe_shared:
            logger.warning(f"Path traversal attempt blocked: {filename} from {addr}")
            writer.write(json.dumps({"status": "error", "message": "Invalid path"}).encode() + b'\n')
            await writer.drain()
            return

        if not os.path.exists(file_path):
            logger.warning(f"File not found: {filename} requested by {addr}")
            writer.write(b"File not found")
            await writer.drain()
            return

        file_size = os.path.getsize(file_path)
        logger.info(f"[DEBUG] Peer file_size = {file_size:,} bytes for {filename}")

        start = range_info.get('start', 0)
        end = range_info.get('end')

        if start < 0 or start >= file_size:
            writer.write(json.dumps({"status": "error", "message": "Invalid range"}).encode() + b'\n')
            await writer.drain()
            return

        if end is not None:
            if end < start or end >= file_size:
                writer.write(json.dumps({"status": "error", "message": "Invalid range"}).encode() + b'\n')
                await writer.drain()
                return
            content_length = end - start + 1
        else:
            end = file_size - 1
            content_length = file_size - start

        resp_header = {
            "status": "ok",
            "file_size": file_size,
            "range_start": start,
            "range_end": end,
            "content_length": content_length,
        }
        writer.write(json.dumps(resp_header).encode() + b'\n')
        await writer.drain()

        async with aiofiles.open(file_path, 'rb') as f:
            await f.seek(start)
            remaining = content_length
            logger.info(f"Serving {filename} range {start}-{end} ({content_length} bytes)")
            while remaining > 0:
                chunk_size = min(65536, remaining)
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
                remaining -= len(chunk)

        logger.info(f"Sent range {start}-{end} of {filename} ({content_length} bytes) to {addr}")
    except asyncio.TimeoutError:
        logger.warning(f"Request timeout from {addr}")
    except Exception as e:
        logger.error(f"Download error from {addr}: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_peer_server():
    ctx = create_server_ssl_context()
    try:
        server_kwargs = {
            'host': '0.0.0.0',
            'port': PEER_PORT,
            'ssl': ctx,
            'backlog': 1024,
        }
        if platform.system() != 'Windows':
            server_kwargs['reuse_port'] = True
        server = await asyncio.start_server(handle_client, **server_kwargs)
        logger.info(f"TLS Peer server listening on 0.0.0.0:{PEER_PORT}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Peer server error: {e}")


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------
def register_single_file(server_host, filename, file_size):
    file_path = os.path.join(SHARED_DIR, filename)
    if not os.path.exists(file_path):
        logger.warning(f"File not found: {filename}")
        return
    try:
        current_size = os.path.getsize(file_path)
    except OSError as e:
        logger.error(f"Cannot get size of {filename}: {e}")
        return
    if current_size != file_size:
        logger.info(f"Size changed ({file_size:,} → {current_size:,})")
        file_size = current_size

    piece_hashes = compute_piece_hashes(file_path, CHUNK_SIZE)
    local_ip = get_local_ip()

    payload = {
        'type': 'register',
        'filename': filename,
        'peer_ip_public': MY_PUBLIC_IP,
        'peer_ip_local': local_ip,
        'peer_port': PEER_PORT,
        'size': file_size,
        'piece_size': CHUNK_SIZE,
        'piece_hashes': piece_hashes,
    }
    payload_str = json.dumps(payload) + '\n'
    payload_bytes = payload_str.encode('utf-8')
    logger.info(f"Registering '{filename}' | {file_size:,} bytes | {len(piece_hashes)} pieces")
    try:
        ctx = create_client_ssl_context(server_host)
        with socket.create_connection((server_host, SERVER_PORT), timeout=60) as sock:
            sock.settimeout(60)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            with ctx.wrap_socket(sock, server_hostname=server_host) as ssock:
                ssock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
                ssock.sendall(payload_bytes)
                resp = ssock.recv(4096)
                if not resp:
                    logger.error("Empty response from server")
                    raise Exception("Empty response from server")
                response_text = resp.decode(errors='ignore').strip()
                if "Registered" in response_text:
                    logger.info(f"Successfully registered {filename}")
                else:
                    logger.warning(f"Server said: {response_text}")
    except Exception as e:
        logger.error(f"Error registering {filename}: {e}")
