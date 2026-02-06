import os
import json
import time
import threading
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from config import logger


class TransferManager:
    def __init__(self, treeview, root):
        self.treeview = treeview
        self.root = root
        self.transfers = {}
        self.lock = threading.Lock()
        self.progress_widgets = {}
        self.show_piecesbar = True

    def add_transfer(self, filename, peer_ip, peer_port, file_size):
        with self.lock:
            self.transfers[filename] = {
                'size': file_size,
                'peer': f"{peer_ip}:{peer_port}",
                'bytes': 0,
                'start_time': time.time(),
                'status': 'Downloading',
                'progress': 0,
                'speed': 0,
                'eta': 'Calculating...',
            }
        self._update_table_internal(filename)

    def update_transfer(self, filename, bytes_downloaded, total_size):
        with self.lock:
            if filename not in self.transfers:
                return
            t = self.transfers[filename]
            t['bytes'] = bytes_downloaded
            t['progress'] = min(100.0, (bytes_downloaded / total_size * 100)) if total_size > 0 else 0
            remaining = total_size - bytes_downloaded
            if remaining <= 0:
                t['eta'] = 'Done'
            else:
                if t['speed'] > 0:
                    eta_sec = remaining / t['speed']
                    t['eta'] = f"{int(eta_sec)}s" if eta_sec < 60 else f"{int(eta_sec // 60)}m {int(eta_sec % 60)}s"
                else:
                    t['eta'] = 'Calculating...'
        self._update_table_internal(filename)

    def complete_transfer(self, filename):
        with self.lock:
            if filename in self.transfers:
                t = self.transfers[filename]
                t['status'] = 'Seeding'
                t['progress'] = 100.0
                t['eta'] = 'Seeding'
        self._update_table_internal(filename)

    def fail_transfer(self, filename, error):
        with self.lock:
            if filename in self.transfers:
                t = self.transfers[filename]
                t['status'] = f"Failed: {error}"
                t['eta'] = 'N/A'
        self._update_table_internal(filename)

    def _update_table_internal(self, filename):
        with self.lock:
            if filename not in self.transfers:
                return
            t_copy = self.transfers[filename].copy()

        def do_update():
            size_mb = round(t_copy['size'] / (1024 * 1024), 1) if t_copy['size'] > 0 else 0
            speed_mb = round(t_copy['speed'] / (1024 * 1024), 2) if t_copy['speed'] > 1024 * 1024 else round(t_copy['speed'] / 1024, 2)
            speed_unit = "MB/s" if t_copy['speed'] > 1024 * 1024 else "KB/s"
            iid = None
            for item in self.treeview.get_children():
                if self.treeview.item(item)['values'][0] == filename:
                    iid = item
                    break
            if iid:
                self.treeview.item(iid, values=(
                    filename, f"{size_mb} MB", '', t_copy['status'],
                    t_copy['peer'], f"{speed_mb} {speed_unit}", t_copy['eta']
                ))
            else:
                iid = self.treeview.insert('', 'end', values=(
                    filename, f"{size_mb} MB", '', t_copy['status'],
                    t_copy['peer'], f"{speed_mb} {speed_unit}", t_copy['eta']
                ))
                children = self.treeview.get_children()
                index = children.index(iid)
                tag = 'evenrow' if index % 2 == 0 else 'oddrow'
                self.treeview.item(iid, tags=(tag,))
            if iid not in self.progress_widgets:
                if self.show_piecesbar:
                    pb = PiecesBar(self.treeview)
                else:
                    pb = ttk.Progressbar(self.treeview, orient=HORIZONTAL, mode='determinate', bootstyle="info")
                self.progress_widgets[iid] = pb
            pb = self.progress_widgets[iid]
            progress = t_copy['progress'] / 100.0
            if self.show_piecesbar:
                pb.set_pieces([], 0)
                pb.set_fraction(progress)
            else:
                pb['value'] = t_copy['progress']
            self.place_progress_widget(iid)

        self.root.after(0, do_update)

    def place_progress_widget(self, iid):
        if iid not in self.treeview.get_children():
            if iid in self.progress_widgets:
                self.progress_widgets[iid].place_forget()
                del self.progress_widgets[iid]
            return
        bbox = self.treeview.bbox(iid, column="#3")
        if not bbox:
            if iid in self.progress_widgets:
                self.progress_widgets[iid].place_forget()
            return
        x, y, width, height = bbox
        pad = 2
        pb = self.progress_widgets[iid]
        pb.place(x=x, y=y + pad, width=width, height=height - pad * 2)

    def update_progress_positions(self):
        for iid in list(self.progress_widgets):
            self.place_progress_widget(iid)

    def save_transfers(self):
        with self.lock:
            data = {}
            for k, v in self.transfers.items():
                if v['status'] != 'Downloading':
                    saved_v = v.copy()
                    saved_v.pop('handle', None)
                    data[k] = saved_v
            with open('transfers.json', 'w', encoding='utf-8') as f:
                json.dump(data, f)

    def load_transfers(self):
        if os.path.exists('transfers.json'):
            try:
                with open('transfers.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                with self.lock:
                    for k, v in data.items():
                        v['speed'] = 0
                        self.transfers[k] = v
                for k in data:
                    self._update_table_internal(k)
            except Exception as e:
                logger.error(f"Failed to load transfers: {e}")


class PiecesBar(tk.Canvas):
    def __init__(self, master, height=20, bg='gray', **kwargs):
        super().__init__(master, height=height, bg=bg, highlightthickness=0, **kwargs)
        self.fraction = 0.0
        self.pieces = []
        self.num_pieces = 0
        self.colors = {
            'complete': 'green',
            'empty': 'gray',
            'progress': 'blue',
            'text': 'white',
        }
        self.bind("<Configure>", self.draw)

    def set_fraction(self, fraction):
        self.fraction = fraction
        self.draw()

    def set_pieces(self, pieces, num_pieces):
        self.pieces = pieces
        self.num_pieces = num_pieces
        self.draw()

    def draw(self, event=None):
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 0 or height <= 0:
            return

        if self.num_pieces > 0 and self.pieces:
            piece_width = max(1, width / self.num_pieces)
            for i in range(self.num_pieces):
                color = self.colors['complete'] if self.pieces[i] else self.colors['empty']
                self.create_rectangle(i * piece_width, 0, (i + 1) * piece_width, height, fill=color, outline="")
        else:
            self.create_rectangle(0, 0, width * self.fraction, height, fill=self.colors['progress'], outline="")

        self.create_text(width / 2, height / 2, text=f"{self.fraction * 100:.1f}%", fill=self.colors['text'], anchor="center")
