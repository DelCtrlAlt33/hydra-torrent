import os
import json
import math
import queue
import socket
import hashlib
import time
import threading
import concurrent.futures
import tkinter as tk

from config import DOWNLOAD_DIR, CHUNK_SIZE, NUM_PARALLEL_CONNECTIONS, logger
from certs import create_client_ssl_context


def download_from_peer(peer_ip, peer_port, filename, status_text, transfer_manager,
                       total_size, piece_size=CHUNK_SIZE, piece_hashes=None, use_tls=True):
    if total_size is None or total_size <= 0:
        logger.warning("No total_size provided - using placeholder")
        total_size = 0

    logger.info(f"Starting download: {filename} - {total_size:,} bytes | TLS: {use_tls}")
    download_path = os.path.join(DOWNLOAD_DIR, filename)
    part_path = download_path + '.part'

    if os.path.exists(part_path):
        logger.info(f"Deleting old .part file ({os.path.getsize(part_path):,} bytes)")
        os.remove(part_path)

    # --- BUG FIX: initialise num_pieces, pending_pieces, completed_pieces, file_handle ---
    num_pieces = max(1, math.ceil(total_size / piece_size)) if total_size > 0 else 0
    completed_pieces = [0] * num_pieces

    # Thread-safe queue instead of bare list (fixes race on .pop(0))
    pending_queue = queue.Queue()
    for i in range(num_pieces):
        pending_queue.put(i)

    # Pre-allocate .part file
    file_handle = open(part_path, 'wb')
    if total_size > 0:
        file_handle.seek(total_size - 1)
        file_handle.write(b'\x00')
        file_handle.flush()

    downloaded = 0
    status_text.insert(tk.END, f"Starting download ({downloaded:,} / {total_size:,})\n")
    status_text.see(tk.END)

    start_time = time.time()
    completed_bytes = [0]
    completed_lock = threading.Lock()
    stop_progress = threading.Event()
    file_lock = threading.Lock()

    def gui_progress_timer():
        prev_dl = 0
        prev_time = time.time()
        while not stop_progress.is_set():
            time.sleep(1.0)
            with completed_lock:
                dl = completed_bytes[0]
            if dl >= total_size and total_size > 0:
                break
            current_time = time.time()
            delta_dl = dl - prev_dl
            delta_time = current_time - prev_time
            speed = delta_dl / delta_time if delta_time > 0 else 0
            prev_dl = dl
            prev_time = current_time
            progress_pct = (dl / total_size * 100) if total_size > 0 else 0
            transfer_manager.transfers[filename]['speed'] = speed
            transfer_manager.update_transfer(filename, dl, total_size)
            status_text.insert(tk.END, f"Progress: {progress_pct:.1f}% | Speed: {speed / 1024 / 1024:.2f} MB/s\n")
            status_text.see(tk.END)

    # --- BUG FIX: actually start the progress thread ---
    progress_thread = threading.Thread(target=gui_progress_timer, daemon=True)
    progress_thread.start()

    def download_worker():
        local_bytes = 0
        while True:
            try:
                piece_idx = pending_queue.get_nowait()
            except queue.Empty:
                break

            start = piece_idx * piece_size
            end = min(start + piece_size - 1, total_size - 1) if total_size > 0 else 0
            length = end - start + 1 if total_size > 0 else 0

            try:
                sock = socket.create_connection((peer_ip, peer_port), timeout=60)
                if use_tls:
                    ssock = create_client_ssl_context(peer_ip).wrap_socket(sock, server_hostname=peer_ip)
                else:
                    ssock = sock
                with ssock:
                    req = {"type": "download", "filename": filename, "range": {"start": start, "end": end}}
                    ssock.sendall(json.dumps(req).encode('utf-8') + b'\n')
                    header_data = b''
                    while b'\n' not in header_data:
                        chunk = ssock.recv(16384)
                        if not chunk:
                            raise Exception("Peer closed before header")
                        header_data += chunk
                    header_str, rest = header_data.split(b'\n', 1)
                    header = json.loads(header_str.decode('utf-8'))
                    if header.get("status") != "ok":
                        raise Exception(header.get('message', 'Peer error'))
                    content_length = header["content_length"]
                    logger.info(f"DEBUG: Expected content length for piece {piece_idx}: {content_length:,} bytes")
                    received = 0
                    received_piece = bytearray()
                    while received < content_length:
                        remaining = content_length - received
                        data = ssock.recv(min(65536, remaining))
                        if not data:
                            logger.warning(f"Piece {piece_idx} - peer closed early")
                            break
                        received_piece.extend(data)
                        received += len(data)
                    if received != content_length:
                        logger.error(f"Piece {piece_idx} incomplete ({received:,}/{content_length:,})")
                        pending_queue.put(piece_idx)
                        continue
                    actual = hashlib.sha256(received_piece).hexdigest()
                    if piece_hashes and piece_idx < len(piece_hashes):
                        expected = piece_hashes[piece_idx]
                        if actual != expected:
                            logger.error(f"Piece {piece_idx} HASH MISMATCH!")
                            pending_queue.put(piece_idx)
                            continue
                        else:
                            logger.info(f"Piece {piece_idx} hash OK")
                            with completed_lock:
                                completed_pieces[piece_idx] = 1
                                completed_bytes[0] += length
                    else:
                        logger.warning(f"No piece hash available for {piece_idx} – skipping verification")
                        with completed_lock:
                            completed_pieces[piece_idx] = 1
                            completed_bytes[0] += length
                    with file_lock:
                        file_handle.seek(start)
                        file_handle.write(received_piece)
                        file_handle.flush()
                        os.fsync(file_handle.fileno())
                    local_bytes += received
            except Exception as e:
                logger.error(f"Piece {piece_idx} failed: {e}")
                pending_queue.put(piece_idx)
        return local_bytes

    total_new = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_PARALLEL_CONNECTIONS) as ex:
        futures = [ex.submit(download_worker) for _ in range(NUM_PARALLEL_CONNECTIONS)]
        for future in concurrent.futures.as_completed(futures):
            got = future.result()
            total_new += got

    with completed_lock:
        num_completed = sum(completed_pieces)
        dl = completed_bytes[0]

    file_handle.flush()
    os.fsync(file_handle.fileno())
    file_handle.close()

    if num_completed == num_pieces:
        os.rename(part_path, download_path)
        status_text.insert(tk.END, "Download complete \u2713\n", "success")
        transfer_manager.complete_transfer(filename)
        stop_progress.set()
    else:
        status_text.insert(tk.END, f"Incomplete ({num_completed}/{num_pieces} pieces)\n")
        transfer_manager.fail_transfer(filename, "Incomplete pieces")
        stop_progress.set()
