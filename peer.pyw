#!/usr/bin/env python3
import os
import socket
import threading
import json
import time
import hashlib
import requests
import platform
import sys
import random
import webbrowser
import re
import shutil
import warnings
import io

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText
from ttkbootstrap.scrolled import ScrolledFrame
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
import tkinter.ttk as standard_ttk
import libtorrent as lt
import maxminddb
from PIL import Image, ImageTk

# --- Hydra modules ---
from config import (
    SHARED_DIR, DOWNLOAD_DIR, PEER_PORT, SERVER_PORT, CHUNK_SIZE,
    logger, hide_console, load_config, save_config,
)
from certs import create_client_ssl_context
from network import (
    MY_PUBLIC_IP, register_single_file, start_peer_server,
)
from search import (
    search_online_public, search_jackett, search_index_server,
)
from download import download_from_peer
from transfer_manager import TransferManager, PiecesBar

hide_console()


class FileSharingApp:
    def __init__(self, root):
        self.root = root
        try:
            self.root.iconbitmap("image8.ico")
        except Exception as e:
            print(f"Could not load icon: {e}")
        self.root.title(f"Hydra Torrent v0.1 - ({MY_PUBLIC_IP}:{PEER_PORT})")
        self.status_visible_var = tk.BooleanVar(value=True)
        # ====================
        # MENU BAR
        # ====================
        menubar = tk.Menu(self.root, bg="#2c3e50", fg="white", activebackground="#34495e")
        self.root.config(menu=menubar)
        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Add Magnet Link...",
                              command=self.prompt_add_magnet,
                              accelerator="Ctrl+M")
        file_menu.add_command(label="Add Torrent File...",
                              command=self.prompt_add_torrent_file,
                              accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Exit",
                              command=self.on_close,
                              accelerator="Ctrl+Q")
        # Edit Menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Preferences...",
                              command=self.open_preferences_dialog,
                              accelerator="Ctrl+P")
        edit_menu.add_command(label="Clear Completed Transfers",
                              command=self.clear_completed_transfers)
        # View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Refresh Library",
                              command=self.refresh_library,
                              accelerator="F5")
        view_menu.add_checkbutton(label="Show Status Log",
                                  command=self.toggle_status_log,
                                  variable=self.status_visible_var)
        # Tools Menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Create Torrent...",
                               command=self.open_torrent_creator_dialog)
        tools_menu.add_command(label="Check Port Status",
                               command=self.check_port_open)
        tools_menu.add_command(label="Open Shared Folder",
                               command=lambda: self.open_folder(SHARED_DIR))
        tools_menu.add_command(label="Open Download Folder",
                               command=lambda: self.open_folder(DOWNLOAD_DIR))
        # Help Menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About Hydra Torrent",
                              command=self.show_about)
        help_menu.add_command(label="Documentation / GitHub",
                              command=lambda: webbrowser.open("https://github.com/yourusername/hydra-torrent"))
        ttk.Label(root, text="Indexing Server:").pack(pady=10)
        self.server_entry = ttk.Entry(root, width=50)
        self.server_entry.pack(pady=5)
        # Load saved server address
        config = load_config()
        saved_server = config.get('indexing_server', 'localhost')
        self.server_entry.insert(0, saved_server)
        # Save on change
        self.server_entry.bind("<FocusOut>", self.save_indexing_server)
        self.server_entry.bind("<Return>", self.save_indexing_server)
        self.jackett_api_key = config.get('jackett_api_key', "")
        # Add clickable key icon for changing API key
        key_label = ttk.Label(root, text="\U0001f511 Change Jackett API Key", bootstyle="info", cursor="hand2")
        key_label.pack(pady=5)
        key_label.bind("<Button-1>", lambda e: self.change_jackett_key())
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(pady=10, fill='both', expand=True)
        # Library Tab
        lib = ttk.Frame(self.notebook)
        self.notebook.add(lib, text='Library')
        self.lib_tree = ttk.Treeview(lib, columns=('Name', 'Size KB'), show='headings')
        self.lib_tree.heading('Name', text='Name')
        self.lib_tree.heading('Size KB', text='Size KB')
        self.lib_tree.pack(fill='both', expand=True)
        ttk.Button(lib, text="Refresh", bootstyle="info", command=self.refresh_library).pack(pady=8)
        # Search Tab
        search = ttk.Frame(self.notebook)
        self.notebook.add(search, text='Search')
        ttk.Label(search, text="Keyword:").pack(pady=(20, 5))
        self.search_entry = ttk.Entry(search, width=40)
        self.search_entry.pack(pady=5)
        ttk.Button(search, text="Search", bootstyle="primary", command=self.search).pack(pady=10)
        # Search Treeview with qBittorrent-like columns
        self.search_tree = ttk.Treeview(
            search,
            columns=('Name', 'Size', 'Seeders', 'Leechers', 'Engine', 'Published', 'Engine URL'),
            show='headings'
        )
        self.search_tree.heading('Name', text='Name')
        self.search_tree.heading('Size', text='Size')
        self.search_tree.heading('Seeders', text='Seeds')
        self.search_tree.heading('Leechers', text='Leech')
        self.search_tree.heading('Engine', text='Engine')
        self.search_tree.heading('Published', text='Published On')
        self.search_tree.heading('Engine URL', text='Engine URL')
        self.search_tree.column('Name', width=350, anchor='w')
        self.search_tree.column('Size', width=100, anchor='e')
        self.search_tree.column('Seeders', width=80, anchor='center')
        self.search_tree.column('Leechers', width=80, anchor='center')
        self.search_tree.column('Engine', width=120, anchor='center')
        self.search_tree.column('Published', width=140, anchor='center')
        self.search_tree.column('Engine URL', width=200, anchor='w')
        self.search_tree.pack(fill='both', expand=True, pady=10)
        self.download_btn = ttk.Button(search, text="Download", bootstyle="success", command=self.download, state='disabled')
        self.download_btn.pack(pady=10)
        # Transfers Tab
        trans = ttk.Frame(self.notebook)
        self.notebook.add(trans, text='Transfers')
        trans_frame = standard_ttk.Frame(trans)
        trans_frame.pack(fill='both', expand=True)
        self.trans_tree = ttk.Treeview(
            trans_frame, columns=('File', 'Size', 'Prog', 'Status', 'Peer', 'Speed', 'ETA'), show='headings'
        )
        for c in self.trans_tree['columns']:
            self.trans_tree.heading(c, text=c)
        # Alternating rows for visual separation/gaps
        self.trans_tree.tag_configure('evenrow', background='#3a0a4a')
        self.trans_tree.tag_configure('oddrow', background='#2c0a3a')
        # Column widths
        self.trans_tree.column('File', width=250, anchor='w')
        self.trans_tree.column('Size', width=80, anchor='e')
        self.trans_tree.column('Prog', width=150, anchor='center')
        self.trans_tree.column('Status', width=100, anchor='center')
        self.trans_tree.column('Peer', width=150, anchor='w')
        self.trans_tree.column('Speed', width=80, anchor='e')
        self.trans_tree.column('ETA', width=80, anchor='e')
        # Taller rows + border for gap/separation
        style = ttk.Style()
        style.configure("Treeview", rowheight=35, borderwidth=1, relief="flat")
        style.configure("Treeview.Item", borderwidth=1, relief="flat")
        self.trans_tree.grid(row=0, column=0, sticky='nsew')
        self.yscroll = standard_ttk.Scrollbar(trans_frame, orient='vertical', command=self.trans_tree.yview)
        self.yscroll.grid(row=0, column=1, sticky='ns')
        self.trans_tree.configure(yscrollcommand=self.yscroll_set)
        trans_frame.rowconfigure(0, weight=1)
        trans_frame.columnconfigure(0, weight=1)
        self.trans_tree.bind("<Configure>", lambda e: self.transfer_manager.update_progress_positions())
        self.transfer_manager = TransferManager(self.trans_tree, self.root)
        self.transfer_manager.load_transfers()
        self.transfer_manager.show_piecesbar = True
        # Bottom status panel
        self.bottom_notebook = ttk.Notebook(self.root)
        self.bottom_notebook.pack(pady=5, padx=10, fill='x')
        # General tab
        general_tab = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(general_tab, text="General")
        ttk.Label(general_tab, text="Progress:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.progress_widget = PiecesBar(general_tab, height=20)
        self.progress_widget.grid(row=0, column=1, sticky='ew', padx=5, pady=2)
        self.progress_widget.bind("<Configure>", self.progress_widget.draw)
        ttk.Label(general_tab, text="Transfer:").grid(row=1, column=0, sticky='w', padx=5, pady=2)
        self.transfer_label = ttk.Label(general_tab, text="Down: 0 MB/s | Up: 0 MB/s | ETA: N/A")
        self.transfer_label.grid(row=1, column=1, sticky='w', padx=5, pady=2)
        ttk.Label(general_tab, text="Active Time:").grid(row=2, column=0, sticky='w', padx=5, pady=2)
        self.active_time_label = ttk.Label(general_tab, text="0s")
        self.active_time_label.grid(row=2, column=1, sticky='w', padx=5, pady=2)
        ttk.Label(general_tab, text="Downloaded:").grid(row=3, column=0, sticky='w', padx=5, pady=2)
        self.downloaded_label = ttk.Label(general_tab, text="0 MB")
        self.downloaded_label.grid(row=3, column=1, sticky='w', padx=5, pady=2)
        ttk.Label(general_tab, text="Uploaded:").grid(row=4, column=0, sticky='w', padx=5, pady=2)
        self.uploaded_label = ttk.Label(general_tab, text="0 MB")
        self.uploaded_label.grid(row=4, column=1, sticky='w', padx=5, pady=2)
        ttk.Label(general_tab, text="Seeds | Peers:").grid(row=5, column=0, sticky='w', padx=5, pady=2)
        self.seeds_peers_label = ttk.Label(general_tab, text="0 | 0")
        self.seeds_peers_label.grid(row=5, column=1, sticky='w', padx=5, pady=2)
        ttk.Label(general_tab, text="Connections:").grid(row=6, column=0, sticky='w', padx=5, pady=2)
        self.connections_label = ttk.Label(general_tab, text="0")
        self.connections_label.grid(row=6, column=1, sticky='w', padx=5, pady=2)
        general_tab.columnconfigure(1, weight=1)
        # Trackers tab
        trackers_tab = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(trackers_tab, text="Trackers")
        self.trackers_tree = ttk.Treeview(trackers_tab, columns=('URL', 'Status', 'Peers', 'Fails'), show='headings')
        self.trackers_tree.heading('URL', text='URL')
        self.trackers_tree.heading('Status', text='Status')
        self.trackers_tree.heading('Peers', text='Peers')
        self.trackers_tree.heading('Fails', text='Fails')
        self.trackers_tree.pack(fill='both', expand=True)
        # Peers tab
        peers_tab = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(peers_tab, text="Peers")
        self.peers_tree = ttk.Treeview(peers_tab, columns=('Country/Region', 'IP', 'Down Speed', 'Up Speed', 'Client'), show='tree headings')
        self.peers_tree.heading('#0', text='Flag')
        self.peers_tree.heading('Country/Region', text='Country/Region')
        self.peers_tree.heading('IP', text='IP')
        self.peers_tree.heading('Down Speed', text='Down Speed')
        self.peers_tree.heading('Up Speed', text='Up Speed')
        self.peers_tree.heading('Client', text='Client')
        self.peers_tree.column('#0', width=30, anchor='center')
        self.peers_tree.column('Country/Region', width=120, anchor='w')
        self.peers_tree.column('IP', width=130, anchor='w')
        self.peers_tree.column('Down Speed', width=80, anchor='e')
        self.peers_tree.column('Up Speed', width=80, anchor='e')
        self.peers_tree.column('Client', width=140, anchor='w')
        style = ttk.Style()
        style.configure("Treeview", rowheight=20)
        self.peers_tree.pack(fill='both', expand=True)
        # Bind selection and start recurring update
        self.trans_tree.bind("<<TreeviewSelect>>", self.update_bottom_status)
        # Global libtorrent session for all torrents
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="libtorrent")
        self.ses = lt.session({
            'listen_interfaces': '0.0.0.0:' + str(PEER_PORT),
            'enable_dht': True,
            'enable_lsd': True,
            'enable_upnp': True,
            'enable_natpmp': True,
            'connections_limit': 200,
            'download_rate_limit': 0,
            'upload_rate_limit': 0,
        })
        self.ses.add_dht_router("router.utorrent.com", 6881)
        self.ses.add_dht_router("router.bittorrent.com", 6881)
        self.ses.add_dht_router("dht.transmissionbt.com", 6881)
        # Resume persisted torrents
        self.resume_persisted_torrents()
        self.update_bottom_status()
        # Add context menu for removal
        self.trans_context_menu = tk.Menu(self.root, tearoff=0)
        self.trans_tree.bind("<Button-3>", self.show_trans_context_menu)
        self.item_data = {}
        self.status = ScrolledText(root, height=8, autohide=True, bootstyle="dark")
        self.status.pack(pady=10, padx=10, fill='both', expand=True)
        self.status.tag_config("success", foreground="#00ff00")
        self.status.tag_config("error", foreground="#ff4444")
        about_label = ttk.Label(
            root,
            text="Hydra Torrent v0.1 \u2013 Built with \u2764\ufe0f",
            bootstyle="success",
            justify="center",
            cursor="hand2"
        )
        about_label.pack(pady=10)
        about_label.bind("<Button-1>", lambda e: self.show_about())

        def run_peer_server():
            import asyncio
            asyncio.run(start_peer_server())
        threading.Thread(target=run_peer_server, daemon=True).start()
        self.root.after(1000, self.start_file_watcher)
        self.root.after(4000, self.refresh_library)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.check_port_open()

        self.flag_cache = {}
        self.image_refs = []
        self.geo_reader = None
        try:
            self.geo_reader = maxminddb.open_database('GeoLite2-Country.mmdb')
        except Exception as e:
            logger.error(f"GeoIP DB load failed: {e} - Flags disabled")

    def yscroll_set(self, *args):
        self.yscroll.set(*args)
        self.trans_tree.after(50, self.transfer_manager.update_progress_positions)

    def prompt_add_magnet(self):
        magnet = simpledialog.askstring(
            "Add Magnet Link",
            "Paste the magnet link here:",
            parent=self.root
        )
        if magnet and magnet.strip().startswith("magnet:?"):
            filename_guess = "unnamed_torrent"
            threading.Thread(
                target=self.download_torrent,
                args=(magnet.strip(), filename_guess, self.status, self.transfer_manager),
                daemon=True
            ).start()
            self.status.insert(tk.END, f"Added magnet: {magnet[:80]}...\n", "success")
            self.status.see(tk.END)
        elif magnet:
            messagebox.showwarning("Invalid", "Not a valid magnet link!")

    def prompt_add_torrent_file(self):
        torrent_path = filedialog.askopenfilename(
            title="Select .torrent file",
            filetypes=[("Torrent files", "*.torrent")]
        )
        if torrent_path:
            messagebox.showinfo("Coming Soon", "Full .torrent support is planned!\nFor now, try pasting as magnet if possible.")

    def open_preferences_dialog(self):
        prefs = ttk.Toplevel(self.root)
        prefs.title("Preferences")
        prefs.geometry("450x350")
        prefs.transient(self.root)
        prefs.grab_set()
        ttk.Label(prefs, text="Hydra Torrent Preferences", font=("Helvetica", 12, "bold")).pack(pady=10)
        ttk.Label(prefs, text="(Expand this dialog with real options soon!)").pack(pady=20)
        ttk.Button(prefs, text="Close", command=prefs.destroy, bootstyle="primary").pack(pady=10)

    def clear_completed_transfers(self):
        with self.transfer_manager.lock:
            to_remove = []
            for filename, t in self.transfer_manager.transfers.items():
                if t['status'] in ('Complete', 'Seeding', 'Failed'):
                    if 'handle' in t:
                        self.ses.remove_torrent(t['handle'])
                    to_remove.append(filename)
            for filename in to_remove:
                del self.transfer_manager.transfers[filename]
                self.transfer_manager._update_table_internal(filename)
        self.transfer_manager.save_transfers()
        self.status.insert(tk.END, "Cleared completed/failed transfers.\n", "success")
        self.status.see(tk.END)

    def toggle_status_log(self):
        if self.status.winfo_ismapped():
            self.status.pack_forget()
            self.status_visible_var.set(False)
        else:
            self.status.pack(pady=10, padx=10, fill='both', expand=True)
            self.status_visible_var.set(True)

    def open_folder(self, path):
        import subprocess
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif os.name == 'posix':
                if platform.system() == 'Darwin':
                    subprocess.call(['open', path])
                else:
                    subprocess.call(['xdg-open', path])
        except Exception as e:
            self.status.insert(tk.END, f"Couldn't open folder: {e}\n", "error")
            self.status.see(tk.END)

    def open_torrent_creator_dialog(self):
        creator_win = ttk.Toplevel(self.root)
        creator_win.title("Create Torrent")
        creator_win.geometry("500x400")
        ttk.Label(creator_win, text="Torrent Creator (Basic - Expand Later)", font=("Helvetica", 12)).pack(pady=15)
        ttk.Label(creator_win, text="Select file/folder to share:").pack()
        path_var = tk.StringVar()
        ttk.Entry(creator_win, textvariable=path_var, width=50).pack(pady=5)
        ttk.Button(creator_win, text="Browse",
                   command=lambda: path_var.set(filedialog.askdirectory() or filedialog.askopenfilename())).pack()
        ttk.Label(creator_win, text="Trackers (one per line):").pack(pady=10)
        trackers_text = ScrolledText(creator_win, height=5, width=60)
        trackers_text.pack()
        trackers_text.insert(tk.END, "udp://tracker.opentrackr.org:1337/announce\nudp://open.tracker.cl:1337/announce")

        def create():
            messagebox.showinfo("Placeholder", "Torrent creation not fully implemented yet!\nUse qBittorrent/mktorrent for now.")
            creator_win.destroy()

        ttk.Button(creator_win, text="Create .torrent", bootstyle="success", command=create).pack(pady=20)

    def save_indexing_server(self, event=None):
        address = self.server_entry.get().strip()
        if address:
            is_jackett = any([
                ':9117' in address,
                'jackett' in address.lower(),
                'torznab' in address.lower()
            ])
            if is_jackett and not self.jackett_api_key:
                self.prompt_jackett_key()
            save_config('indexing_server', address)
            logger.debug(f"Saved Indexing Server: {address}")

    def check_port_open(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('0.0.0.0', PEER_PORT))
            s.close()
            logger.info(f"Port {PEER_PORT} is open for listening")
        except OSError:
            logger.warning(f"Port {PEER_PORT} may be blocked - check firewall/router")

    def resume_persisted_torrents(self):
        with self.transfer_manager.lock:
            for filename, t in list(self.transfer_manager.transfers.items()):
                if t['status'] == 'Seeding' and 'magnet' in t and 'resume_data' in t:
                    params = {
                        'url': t['magnet'],
                        'save_path': DOWNLOAD_DIR,
                        'storage_mode': lt.storage_mode_t(2),
                    }
                    handle = lt.add_magnet_uri(self.ses, t['magnet'], params)
                    handle.resume_data = t['resume_data']
                    if t.get('intended_pause', False):
                        handle.pause()
                    else:
                        handle.resume()
                    t['handle'] = handle
                    t['status'] = 'Resuming'
                    self.transfer_manager._update_table_internal(filename)
                    threading.Thread(target=self.monitor_seeding, args=(filename,), daemon=True).start()

    def monitor_seeding(self, filename):
        state_map = {
            0: 'Queued',
            1: 'Checking',
            2: 'DL Metadata',
            3: 'Downloading',
            4: 'Finished',
            5: 'Seeding',
            6: 'Allocating',
            7: 'Resuming',
        }
        while filename in self.transfer_manager.transfers:
            t = self.transfer_manager.transfers.get(filename)
            if not t or 'handle' not in t:
                break
            handle = t['handle']
            s = handle.status()
            t['pieces'] = s.pieces[:] if s.pieces else []
            t['num_pieces'] = s.num_pieces
            current_state = state_map.get(s.state, 'Unknown')
            current_time = time.time()
            delta_bytes = s.total_upload - t.get('prev_bytes', 0)
            delta_time = current_time - t.get('prev_time', current_time)
            speed = delta_bytes / delta_time if delta_time > 0 else s.upload_rate
            t['prev_bytes'] = s.total_upload
            t['prev_time'] = current_time
            if t.get('intended_pause', False):
                if not s.paused:
                    handle.pause()
                t['status'] = 'Paused'
                t['speed'] = 0
                t['eta'] = 'Paused'
            else:
                if s.paused:
                    handle.resume()
                t['status'] = current_state
                if s.state in [1, 7]:
                    t['speed'] = 0
                    t['eta'] = 'Checking...'
                else:
                    t['speed'] = speed
                    t['eta'] = 'Done' if current_state == 'Seeding' else 'N/A'
            self.transfer_manager.update_transfer(filename, s.total_done, t['size'])
            time.sleep(2)

    def get_country_from_ip_offline(self, ip):
        if not self.geo_reader:
            return 'Unknown', ''
        try:
            result = self.geo_reader.get(ip)
            if result and 'country' in result:
                country = result['country'].get('names', {}).get('en', 'Unknown')
                code = result['country'].get('iso_code', '')
                return country, code
        except Exception as e:
            logger.debug(f"GeoIP lookup failed for {ip}: {e}")
        return 'Unknown', ''

    def pause_transfer(self, filename):
        with self.transfer_manager.lock:
            t = self.transfer_manager.transfers.get(filename)
            if t and 'handle' in t:
                t['intended_pause'] = True
                t['handle'].pause()
                t['status'] = 'Paused'
                t['speed'] = 0
                t['eta'] = 'Paused'
                self.transfer_manager._update_table_internal(filename)

    def resume_transfer(self, filename):
        with self.transfer_manager.lock:
            t = self.transfer_manager.transfers.get(filename)
            if t and 'handle' in t:
                t['intended_pause'] = False
                t['handle'].resume()
                self.transfer_manager._update_table_internal(filename)

    def remove_selected_transfer(self):
        sel = self.trans_tree.selection()
        if not sel:
            return
        item = sel[0]
        vals = self.trans_tree.item(item)['values']
        filename = vals[0]

        def do_removal():
            with self.transfer_manager.lock:
                if filename in self.transfer_manager.transfers:
                    t = self.transfer_manager.transfers[filename]
                    if 'handle' in t:
                        self.ses.remove_torrent(t['handle'])
                    del self.transfer_manager.transfers[filename]
            self.root.after(0, lambda: self.cleanup_ui(item))

        threading.Thread(target=do_removal, daemon=True).start()
        self.transfer_manager.save_transfers()

    def cleanup_ui(self, iid):
        if iid in self.transfer_manager.progress_widgets:
            self.transfer_manager.progress_widgets[iid].place_forget()
            del self.transfer_manager.progress_widgets[iid]
        if iid in self.trans_tree.get_children():
            self.trans_tree.delete(iid)
        self.update_bottom_status()

    def show_trans_context_menu(self, event):
        self.trans_context_menu.delete(0, tk.END)
        sel = self.trans_tree.selection()
        if not sel:
            return
        item = sel[0]
        vals = self.trans_tree.item(item)['values']
        filename = vals[0]
        with self.transfer_manager.lock:
            if filename not in self.transfer_manager.transfers:
                return
            t = self.transfer_manager.transfers[filename]
            if 'handle' in t:
                s = t['handle'].status()
                if s.paused:
                    self.trans_context_menu.add_command(label="Resume", command=lambda f=filename: self.resume_transfer(f))
                else:
                    self.trans_context_menu.add_command(label="Pause", command=lambda f=filename: self.pause_transfer(f))
            self.trans_context_menu.add_command(label="Remove", command=self.remove_selected_transfer)
        try:
            self.trans_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.trans_context_menu.grab_release()

    def on_close(self):
        with self.transfer_manager.lock:
            for t in self.transfer_manager.transfers.values():
                if 'handle' in t:
                    resume_data = t['handle'].save_resume_data()
                    t['resume_data'] = resume_data
        self.transfer_manager.save_transfers()
        self.root.destroy()

    def download_torrent(self, magnet_uri, filename, status_text, transfer_manager):
        logger.info(f"Starting torrent download for {filename} via magnet: {magnet_uri}")
        params = {
            'url': magnet_uri,
            'save_path': DOWNLOAD_DIR,
            'storage_mode': lt.storage_mode_t(2),
        }
        handle = lt.add_magnet_uri(self.ses, magnet_uri, params)
        status_text.insert(tk.END, f"\U0001f680 Added magnet \u2192 fetching metadata...\n")
        status_text.see(tk.END)
        start_wait = time.time()
        while not handle.has_metadata():
            if time.time() - start_wait > 120:
                status_text.insert(tk.END, "\u274c Metadata timeout\n", "error")
                self.ses.remove_torrent(handle)
                return
            time.sleep(0.5)
            s = handle.status()
            status_text.insert(tk.END, f" \u21b3 {s.state} | Peers: {s.num_peers}\n")
            status_text.see(tk.END)
        ti = handle.get_torrent_info()
        total_size = ti.total_size()
        real_name = ti.name() or filename
        logger.info(f"Metadata OK \u2192 {real_name} ({total_size:,} bytes)")
        status_text.insert(tk.END, f"\u2705 Metadata ready \u2192 '{real_name}' ({total_size / (1024**3):.2f} GB)\n")
        status_text.insert(tk.END, " \u21b3 Download running\n")
        status_text.see(tk.END)
        transfer_manager.add_transfer(real_name, 'Torrent Peers', PEER_PORT, total_size)
        transfer_manager.transfers[real_name]['magnet'] = magnet_uri
        transfer_manager.transfers[real_name]['handle'] = handle
        transfer_manager.transfers[real_name]['intended_pause'] = False
        transfer_manager.transfers[real_name]['prev_bytes'] = 0
        transfer_manager.transfers[real_name]['prev_time'] = time.time()
        trackers = [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.tracker.cl:1337/announce",
            "udp://tracker.openbittorrent.com:6969/announce",
            "udp://open.demonii.com:1337/announce",
            "udp://open.stealth.si:80/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "udp://exodus.desync.com:6969/announce",
            "udp://bt1.archive.org:6969/announce",
            "udp://tracker.dler.org:6969/announce",
            "udp://tracker.tiny-vps.com:6969/announce",
        ]
        for tracker in trackers:
            handle.add_tracker({'url': tracker})
        handle.force_reannounce()
        start_time = time.time()
        stop_progress = threading.Event()

        def gui_progress_timer():
            while not stop_progress.is_set():
                try:
                    s = handle.status()
                    t = transfer_manager.transfers[real_name]
                    t['pieces'] = s.pieces[:] if s.pieces else []
                    t['num_pieces'] = s.num_pieces
                    if t.get('intended_pause', False):
                        if not s.paused:
                            handle.pause()
                        t['status'] = 'Paused'
                        t['speed'] = 0
                        t['eta'] = 'Paused'
                        transfer_manager._update_table_internal(real_name)
                        time.sleep(2)
                        continue
                    else:
                        if s.paused:
                            handle.resume()
                    dl_bytes = s.total_done
                    progress_pct = s.progress * 100
                    current_time = time.time()
                    delta_bytes = dl_bytes - t['prev_bytes']
                    delta_time = current_time - t['prev_time']
                    speed = delta_bytes / delta_time if delta_time > 0 else 0
                    t['prev_bytes'] = dl_bytes
                    t['prev_time'] = current_time
                    speed_mb = speed / (1024 * 1024)
                    eta = "N/A"
                    if speed > 0:
                        remaining = total_size - dl_bytes
                        eta_sec = remaining / speed
                        eta = f"{int(eta_sec // 60)}m {int(eta_sec % 60)}s"
                    t['status'] = 'Downloading' if progress_pct < 100 else 'Seeding'
                    t['speed'] = speed
                    transfer_manager.update_transfer(real_name, dl_bytes, total_size)
                    status_text.insert(tk.END,
                        f" {progress_pct:>5.1f}% | {speed_mb:>5.2f} MB/s | "
                        f"Peers: {s.num_peers:>3} | Seeds: {s.num_seeds:>3} | ETA: {eta}\n")
                    status_text.see(tk.END)
                    if progress_pct >= 100.0:
                        break
                except Exception as e:
                    logger.error(f"Progress error: {e}")
                    break
                time.sleep(2.0)

        progress_thread = threading.Thread(target=gui_progress_timer, daemon=True)
        progress_thread.start()
        while True:
            s = handle.status()
            if s.progress >= 1.0 or s.is_seeding or s.is_finished:
                break
            if s.error:
                err_msg = str(s.error)
                logger.error(f"Torrent error: {err_msg}")
                status_text.insert(tk.END, f"\u274c Error: {err_msg}\n", "error")
                transfer_manager.fail_transfer(real_name, err_msg)
                self.ses.remove_torrent(handle)
                stop_progress.set()
                break
            time.sleep(1)

        stop_progress.set()
        time.sleep(1.0)
        progress_thread.join(timeout=3.0)
        status_text.insert(tk.END, f"\U0001f389 '{real_name}' COMPLETE \u2713\n", "success")
        transfer_manager.complete_transfer(real_name)

        t = transfer_manager.transfers[real_name]
        s = handle.status()
        t['prev_bytes'] = s.total_upload
        t['prev_time'] = time.time()

        threading.Thread(target=self.monitor_seeding, args=(real_name,), daemon=True).start()
        logger.info(f"Now seeding: {real_name}")

    def update_bottom_status(self, event=None):
        sel = self.trans_tree.selection()
        if not sel:
            try:
                s = self.ses.status()
                down_speed = f"{s.download_rate / (1024*1024):.2f} MB/s"
                up_speed = f"{s.upload_rate / (1024*1024):.2f} MB/s"
                self.transfer_label['text'] = f"Down: {down_speed} | Up: {up_speed} | ETA: N/A (Aggregate)"
                self.active_time_label['text'] = "N/A (Aggregate)"
                self.downloaded_label['text'] = f"{s.total_download / (1024*1024):.2f} MB"
                self.uploaded_label['text'] = f"{s.total_upload / (1024*1024):.2f} MB"
                self.seeds_peers_label['text'] = f"N/A | {s.num_peers}"
                self.connections_label['text'] = f"{getattr(s, 'num_connections', s.num_peers)}"
                avg_fraction = 0.0
                if self.transfer_manager.transfers:
                    progresses = [t.get('progress', 0) / 100.0 for t in self.transfer_manager.transfers.values()]
                    avg_fraction = sum(progresses) / len(progresses) if progresses else 0.0
                self.progress_widget.set_pieces([], 0)
                self.progress_widget.set_fraction(avg_fraction)
                self.progress_widget.draw()
            except Exception as e:
                self.transfer_label['text'] = f"Error reading session: {e}"
            self.trackers_tree.delete(*self.trackers_tree.get_children())
            self.peers_tree.delete(*self.peers_tree.get_children())
        else:
            try:
                item = sel[0]
                filename = self.trans_tree.item(item)['values'][0]
                t = self.transfer_manager.transfers.get(filename)
                if not t or 'handle' not in t:
                    raise ValueError("No handle for selected torrent")
                handle = t['handle']
                s = handle.status()
                down_speed = f"{s.download_rate / (1024*1024):.2f} MB/s"
                up_speed = f"{s.upload_rate / (1024*1024):.2f} MB/s"
                eta = t.get('eta', 'N/A')
                self.transfer_label['text'] = f"Down: {down_speed} | Up: {up_speed} | ETA: {eta}"
                active_sec = int(time.time() - t.get('start_time', time.time()))
                h = active_sec // 3600
                m = (active_sec % 3600) // 60
                sec = active_sec % 60
                self.active_time_label['text'] = f"{h}h {m}m {sec}s"
                self.downloaded_label['text'] = f"{s.total_done / (1024*1024):.2f} MB"
                self.uploaded_label['text'] = f"{s.total_upload / (1024*1024):.2f} MB"
                self.seeds_peers_label['text'] = f"{s.num_seeds} | {s.num_peers}"
                self.connections_label['text'] = f"{getattr(s, 'num_connections', s.num_peers)}"
                self.progress_widget.set_pieces(
                    s.pieces[:] if s.pieces else [],
                    s.num_pieces
                )
                self.progress_widget.set_fraction(s.progress)
                self.progress_widget.draw()
                # Trackers tab
                self.trackers_tree.delete(*self.trackers_tree.get_children())
                for tr in handle.trackers():
                    status = "Working" if tr['fails'] == 0 else f"Failed ({tr['fails']})"
                    self.trackers_tree.insert('', 'end', values=(
                        tr['url'],
                        status,
                        tr.get('peers', 0),
                        tr['fails']
                    ))
                # Peers tab
                self.peers_tree.delete(*self.peers_tree.get_children())
                for p in handle.get_peer_info():
                    ip = f"{p.ip[0]}:{p.ip[1]}"
                    down = f"{p.down_speed / 1024:.1f} KB/s"
                    up = f"{p.up_speed / 1024:.1f} KB/s"
                    client = p.client or "Unknown"
                    country, code = self.get_country_from_ip_offline(p.ip[0])
                    flag_img = None
                    country_str = country if country != 'Unknown' else 'Unknown'
                    if code:
                        flag_key = code.lower()
                        if flag_key not in self.flag_cache:
                            try:
                                flag_url = f"https://flagcdn.com/16x12/{flag_key}.png"
                                resp = requests.get(flag_url, timeout=3)
                                if resp.status_code == 200:
                                    img_data = resp.content
                                    img = Image.open(io.BytesIO(img_data))
                                    self.flag_cache[flag_key] = ImageTk.PhotoImage(img)
                            except Exception as e:
                                logger.debug(f"Flag load failed for {flag_key}: {e}")
                        flag_img = self.flag_cache.get(flag_key)
                    if flag_img:
                        self.image_refs.append(flag_img)
                    self.peers_tree.insert('', 'end', image=flag_img, values=(country_str, ip, down, up, client))
            except Exception as e:
                self.transfer_label['text'] = f"Error: {e}"
        self.root.after(2000, self.update_bottom_status)

    def download(self):
        sel = self.search_tree.selection()
        if not sel:
            return
        item = sel[0]
        vals = self.search_tree.item(item)['values']
        filename = vals[0]
        data = self.item_data.get(item, {})
        piece_size = data.get('piece_size', 0)
        piece_hashes = data.get('piece_hashes', [])
        file_size = data.get('size_bytes', 0)
        magnet = data.get('magnet', '')
        ip = data.get('peer_ip')
        port = data.get('peer_port')
        if not self.show_download_dialog(filename, file_size, magnet, ip, port, piece_size, piece_hashes):
            return
        if magnet:
            real_filename = data.get('filename', filename)
            threading.Thread(target=self.download_torrent,
                             args=(magnet, real_filename, self.status, self.transfer_manager),
                             daemon=True).start()
        else:
            if file_size <= 0 or not ip or not port:
                self.status.insert(tk.END, "Error: Invalid file/peer\n")
                self.status.see(tk.END)
                return
            self.transfer_manager.add_transfer(filename, ip, port, file_size)
            threading.Thread(target=download_from_peer,
                             args=(ip, port, filename, self.status, self.transfer_manager,
                                   file_size, piece_size, piece_hashes, True),
                             daemon=True).start()

    def show_download_dialog(self, filename, file_size, magnet=None, ip=None, port=None, piece_size=0, piece_hashes=None):
        from datetime import datetime
        dialog = ttk.Toplevel(self.root)
        dialog.title(f"Add Download: {filename[:50]}...")
        dialog.geometry("900x700")
        dialog.transient(self.root)
        dialog.grab_set()

        scrolled_main = ScrolledFrame(dialog, autohide=True, bootstyle="dark")
        scrolled_main.pack(fill='both', expand=True, padx=10, pady=10)
        main_frame = ttk.Frame(scrolled_main)
        main_frame.pack(fill='both', expand=True)

        save_label = ttk.Label(main_frame, text="Save at", font=("Helvetica", 10, "bold"))
        save_label.pack(anchor='w', pady=(0, 5))
        save_var = tk.StringVar(value=DOWNLOAD_DIR)
        save_entry = ttk.Entry(main_frame, textvariable=save_var, width=60)
        save_entry.pack(fill='x', pady=5)
        browse_btn = ttk.Button(main_frame, text="Browse...",
                                command=lambda: save_var.set(filedialog.askdirectory(initialdir=save_var.get()) or save_var.get()))
        browse_btn.pack(anchor='w', pady=5)

        incomplete_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(main_frame, text="Use another path for incomplete torrent", variable=incomplete_var).pack(anchor='w', pady=5)
        remember_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(main_frame, text="Remember last used save path", variable=remember_var, bootstyle="info").pack(anchor='w', pady=5)

        file_section_label = ttk.Label(main_frame, text="Files", font=("Helvetica", 10, "bold"))
        file_section_label.pack(anchor='w', pady=(20, 5))

        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill='x', pady=(0, 5))
        tree = ttk.Treeview(main_frame, columns=('Name', 'Total Size', 'Priority'), show='tree headings', height=15)
        ttk.Button(controls_frame, text="Select All", command=lambda: self.select_all(tree, True)).pack(side='left', padx=5)
        ttk.Button(controls_frame, text="Select None", command=lambda: self.select_all(tree, False)).pack(side='left', padx=5)
        filter_label = ttk.Label(controls_frame, text="Filter:")
        filter_label.pack(side='left', padx=(10, 5))
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(controls_frame, textvariable=filter_var, width=30)
        filter_entry.pack(side='left', padx=5)
        filter_entry.bind("<KeyRelease>", lambda e: self.filter_tree(tree, filter_var.get()))

        tree.heading('Name', text='Name')
        tree.heading('Total Size', text='Total Size')
        tree.heading('Priority', text='Download Priority')
        tree.column('Name', width=400, anchor='w')
        tree.column('Total Size', width=120, anchor='e')
        tree.column('Priority', width=150, anchor='center')
        tree.pack(fill='both', expand=True)
        tree.bind('<Button-1>', lambda e: self.handle_tree_click(tree, e))

        status_label = ttk.Label(main_frame, text="Fetching metadata...", foreground="orange")
        status_label.pack(pady=5)

        info_label = ttk.Label(main_frame, text="Torrent Information", font=("Helvetica", 10, "bold"))
        info_label.pack(anchor='w', pady=(20, 5))
        info_frame = ttk.LabelFrame(main_frame, text="Details")
        info_frame.pack(fill='x', pady=5)
        size_str = f"{file_size / (1024**3):.2f} GiB" if file_size > 0 else "Unknown"
        free_space = self.get_free_space(save_var.get()) / (1024**3)
        ttk.Label(info_frame, text=f"Size: {size_str} (Free space on disk: {free_space:.2f} GiB)").pack(anchor='w', pady=2)
        date_str = "Not available" if magnet else datetime.now().strftime("%Y-%m-%d")
        ttk.Label(info_frame, text=f"Date: {date_str}").pack(anchor='w', pady=2)
        info_hash = "N/A"
        if magnet:
            hash_match = re.search(r'btih:([a-fA-F0-9]{40})', magnet)
            info_hash = hash_match.group(1).upper() if hash_match else "Unknown"
        elif piece_hashes:
            combined = ''.join(piece_hashes)
            info_hash = hashlib.sha256(combined.encode()).hexdigest().upper()
        ttk.Label(info_frame, text=f"Info hash v1: {info_hash}").pack(anchor='w', pady=2)
        ttk.Label(info_frame, text="Info hash v2: N/A").pack(anchor='w', pady=2)
        ttk.Label(info_frame, text="Comment: ").pack(anchor='w', pady=2)

        bottom_frame = ttk.Frame(dialog)
        bottom_frame.pack(fill='x', pady=10, padx=10)
        never_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom_frame, text="Never show again", variable=never_var).pack(side='left')
        ttk.Button(bottom_frame, text="Cancel", command=lambda: self.cancel_magnet(dialog), bootstyle="danger").pack(side='right', padx=5)
        ok_btn = ttk.Button(bottom_frame, text="OK", state='disabled', bootstyle="success")
        ok_btn.pack(side='right', padx=5)

        temp_handle = None
        file_list = []
        self.parent_map = {}
        self.item_priorities = {}

        def fetch_metadata():
            nonlocal temp_handle, file_list
            try:
                params = {'url': magnet, 'save_path': DOWNLOAD_DIR, 'storage_mode': lt.storage_mode_t(2)}
                temp_handle = lt.add_magnet_uri(self.ses, magnet, params)
                start = time.time()
                peers_found = 0
                while not temp_handle.has_metadata():
                    if not dialog.winfo_exists():
                        raise Exception("Fetch canceled by user")
                    elapsed = time.time() - start
                    if elapsed > 60:
                        raise Exception("Metadata timeout - no peers found?")
                    s = temp_handle.status()
                    if s.num_peers > peers_found:
                        peers_found = s.num_peers
                        dialog.after(0, lambda: status_label.config(text=f"Fetching metadata... Peers: {peers_found}") if status_label.winfo_exists() else None)
                    time.sleep(0.5)

                ti = temp_handle.get_torrent_info()
                file_list = [(f.path, f.size, 'Normal') for f in ti.files()]
                total_size = ti.total_size()

                if dialog.winfo_exists():
                    dialog.after(0, lambda: self.populate_file_tree(tree, file_list, total_size))
                    dialog.after(0, lambda: status_label.config(text="Metadata ready \u2713", foreground="green"))
                    dialog.after(0, lambda: ok_btn.config(state='normal'))
                    dialog.after(0, lambda: ok_btn.config(command=lambda: self.confirm_magnet_download(dialog, temp_handle, file_list, save_var.get(), never_var.get(), tree)))
            except Exception as e:
                if dialog.winfo_exists():
                    dialog.after(0, lambda: status_label.config(text=f"Error: {str(e)}", foreground="red"))
                    dialog.after(0, lambda: ok_btn.config(state='disabled'))

        if magnet:
            threading.Thread(target=fetch_metadata, daemon=True).start()
        else:
            size_str = f"{file_size / (1024**3):.2f} GiB" if file_size > 0 else "Unknown"
            item = tree.insert('', 'end', values=(filename, size_str, 'Normal'), tags=('checked',))
            self.parent_map[filename] = item
            self.item_priorities[item] = 'Normal'
            status_label.config(text="Ready")
            ok_btn.config(state='normal', command=lambda: self.confirm_download(dialog, save_var.get(), never_var.get()))

        dialog.protocol("WM_DELETE_WINDOW", lambda: self.cancel_magnet(dialog))
        self.root.wait_window(dialog)
        return getattr(self, '_download_confirmed', False)

    def populate_file_tree(self, tree, file_list, total_size):
        for item in tree.get_children():
            tree.delete(item)
        self.item_priorities = {}
        self.parent_map = {}
        for path, size, priority in file_list:
            parts = path.split(os.sep)
            current_parent = ''
            for i, part in enumerate(parts):
                full_part = os.sep.join(parts[:i+1])
                if full_part not in self.parent_map:
                    size_str = f"{size / (1024**3):.2f} GiB" if size > 1e9 else f"{size / (1024**2):.1f} MiB" if i == len(parts)-1 else ""
                    item = tree.insert(current_parent, 'end', values=(part, size_str, priority if i == len(parts)-1 else ''), tags=('checked',) if i == len(parts)-1 else ())
                    self.parent_map[full_part] = item
                    if i == len(parts)-1:
                        self.item_priorities[item] = priority
                current_parent = self.parent_map[full_part]

    def confirm_magnet_download(self, dialog, handle, file_list, save_path, never_show, tree):
        priority_map = {'Do not download': 0, 'Low': 1, 'Normal': 4, 'High': 7}
        for idx, (path, size, _) in enumerate(file_list):
            item = self.parent_map.get(path)
            if item and 'checked' not in tree.item(item)['tags']:
                handle.file_priority(idx, 0)
            else:
                prio_str = self.item_priorities.get(item, 'Normal')
                handle.file_priority(idx, priority_map.get(prio_str, 4))
        self._download_confirmed = True
        dialog.destroy()

    def confirm_download(self, dialog, save_path, never_show):
        self._download_confirmed = True
        dialog.destroy()

    def handle_tree_click(self, tree, event):
        item = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not item:
            return
        if col == '#1':
            tags = tree.item(item)['tags']
            if tree.get_children(item):
                self.toggle_folder_check(tree, item)
            else:
                if 'checked' in tags:
                    tree.item(item, tags=())
                else:
                    tree.item(item, tags=('checked',))
        elif col == '#3' and not tree.get_children(item):
            self.edit_priority(tree, item, event)

    def toggle_folder_check(self, tree, item):
        checked = 'checked' not in tree.item(item)['tags']
        def recurse(subitem):
            if tree.get_children(subitem):
                tree.item(subitem, tags=('checked',) if checked else ())
                for child in tree.get_children(subitem):
                    recurse(child)
            else:
                tree.item(subitem, tags=('checked',) if checked else ())
        recurse(item)

    def edit_priority(self, tree, item, event):
        bbox = tree.bbox(item, column='#3')
        if not bbox:
            return
        combo = ttk.Combobox(tree, values=['Do not download', 'Low', 'Normal', 'High'], state='readonly')
        combo.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        combo.current(['Do not download', 'Low', 'Normal', 'High'].index(self.item_priorities.get(item, 'Normal')))
        def on_select(e):
            priority = combo.get()
            tree.set(item, column='#3', value=priority)
            self.item_priorities[item] = priority
            combo.destroy()
        combo.bind('<<ComboboxSelected>>', on_select)
        combo.bind('<FocusOut>', lambda e: combo.destroy())
        combo.focus_set()

    def filter_tree(self, tree, filter_text):
        filter_text = filter_text.lower()
        def recurse(item):
            visible = False
            name = tree.set(item, 'Name').lower()
            if filter_text in name:
                visible = True
            for child in tree.get_children(item):
                child_visible = recurse(child)
                visible = visible or child_visible
            if visible:
                tree.item(item, open=True)
            else:
                tree.detach(item)
            return visible
        for item in tree.get_children(''):
            recurse(item)

    def cancel_magnet(self, dialog):
        dialog.destroy()
        self._download_confirmed = False

    def select_all(self, tree, select=True):
        for item in tree.get_children(''):
            tree.item(item, tags=('checked',) if select else ())

    def get_free_space(self, folder):
        total, used, free = shutil.disk_usage(folder)
        return free

    def custom_askstring(self, title, prompt, initialvalue=''):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        try:
            icon_path = os.path.abspath("image8.ico")
            dialog.iconbitmap(icon_path)
        except Exception as e:
            self.status.insert(tk.END, f"Icon load failed: {e}\n", "error")
            self.status.see(tk.END)

        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text=prompt).pack(pady=10, padx=20)
        entry = ttk.Entry(dialog, width=50)
        entry.pack(pady=5, padx=20)
        entry.insert(0, initialvalue)

        result = [None]

        def ok():
            result[0] = entry.get().strip()
            dialog.destroy()

        def cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)

        ttk.Button(btn_frame, text="OK", bootstyle="success", command=ok).pack(side=LEFT, padx=10)
        ttk.Button(btn_frame, text="Cancel", bootstyle="danger", command=cancel).pack(side=LEFT, padx=10)

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        self.root.wait_window(dialog)

        return result[0]

    def prompt_jackett_key(self):
        api_key = self.custom_askstring(
            "Jackett API Key",
            "Paste your Jackett API key here:\n\n"
            "(open http://127.0.0.1:9117 in your browser \u2192 look at top right corner)",
            ''
        )
        if api_key:
            self.jackett_api_key = api_key
            save_config('jackett_api_key', self.jackett_api_key)

    def change_jackett_key(self):
        api_key = self.custom_askstring(
            "Change Jackett API Key",
            "Enter new Jackett API Key:",
            self.jackett_api_key
        )
        if api_key:
            self.jackett_api_key = api_key
            save_config('jackett_api_key', self.jackett_api_key)

    def show_about(self):
        messagebox.showinfo("About", "Hydra Torrent v0.1\nBuilt by [Your Name]")

    def ping_peer(self, ip, port):
        start = time.time()
        try:
            s = socket.create_connection((ip, port), timeout=2)
            s.close()
            ms = (time.time() - start) * 1000
            return f"{int(ms)}ms"
        except Exception:
            return "Offline"

    def search_local_index(self, server_host, keyword):
        """Thin wrapper around search_jackett / search_index_server with GUI callbacks."""
        results = []

        if not server_host:
            return results

        if not server_host.startswith(('http://', 'https://')):
            server_host = 'http://' + server_host
        server_host = server_host.rstrip('/')

        is_jackett = any([
            ':9117' in server_host,
            'jackett' in server_host.lower(),
            'torznab' in server_host.lower()
        ])

        if is_jackett:
            self.status.insert(tk.END, "Using Jackett for enhanced search...\n", "success")
            api_key = self.jackett_api_key

            if not api_key:
                api_key = simpledialog.askstring(
                    "Jackett API Key",
                    "Paste your Jackett API key here:\n\n"
                    "(open http://127.0.0.1:9117 in your browser \u2192 look at top right corner)",
                    parent=self.root
                )
                if api_key:
                    self.jackett_api_key = api_key
                    save_config('jackett_api_key', api_key)
                else:
                    self.status.insert(tk.END, "Jackett search skipped \u2014 API key required\n", "error")

            if api_key:
                try:
                    results.extend(search_jackett(server_host, keyword, api_key))
                except Exception as e:
                    self.status.insert(tk.END, f"Jackett failed: {e}\n", "error")

        # Always try the original local index server as fallback
        try:
            results.extend(search_index_server(server_host, keyword))
        except Exception as e:
            logger.error(f"Local index server error: {e}")

        return results

    def _unregister_file(self, server_host, filename):
        payload = {'type': 'unregister', 'filename': filename}
        payload_str = json.dumps(payload) + '\n'
        payload_bytes = payload_str.encode('utf-8')
        try:
            ctx = create_client_ssl_context(server_host)
            with socket.create_connection((server_host, SERVER_PORT), timeout=60) as sock:
                with ctx.wrap_socket(sock, server_hostname=server_host) as ssock:
                    ssock.sendall(payload_bytes)
                    resp = ssock.recv(4096).decode(errors='ignore').strip()
                    logger.info(f"Unregistered {filename}: {resp}")
        except Exception as e:
            logger.error(f"Error unregistering {filename}: {e}")

    def start_file_watcher(self):
        threading.Thread(target=self.watch_shared_folder, daemon=True).start()
        self.root.after(4000, self.refresh_library)

    def watch_shared_folder(self):
        last_state = {}
        while True:
            try:
                server_host = self.server_entry.get().strip()
                if not server_host:
                    time.sleep(5)
                    continue
                current_files = {}
                for item in os.listdir(SHARED_DIR):
                    item_path = os.path.join(SHARED_DIR, item)
                    if os.path.isfile(item_path):
                        current_files[item] = {
                            'size': os.path.getsize(item_path),
                            'mtime': os.path.getmtime(item_path),
                        }
                for filename in set(last_state) - set(current_files):
                    self._unregister_file(server_host, filename)
                for filename, info in current_files.items():
                    size = info['size']
                    mtime = info['mtime']
                    if filename in last_state:
                        old_size, old_mtime = last_state[filename]['size'], last_state[filename]['mtime']
                        if mtime > old_mtime or size != old_size:
                            self._unregister_file(server_host, filename)
                            time.sleep(1)
                            register_single_file(server_host, filename, size)
                    else:
                        if size > 0:
                            time.sleep(2)
                            current_path = os.path.join(SHARED_DIR, filename)
                            if os.path.exists(current_path) and os.path.getsize(current_path) == size:
                                register_single_file(server_host, filename, size)
                    last_state[filename] = info
            except Exception as e:
                def error_status():
                    self.status.insert(tk.END, f"Watcher error: {e}\n", "error")
                    self.status.see(tk.END)
                self.root.after(0, error_status)
            time.sleep(5)

    def refresh_library(self):
        self.lib_tree.delete(*self.lib_tree.get_children())
        count = 0
        for f in os.listdir(SHARED_DIR):
            path = os.path.join(SHARED_DIR, f)
            if os.path.isfile(path):
                sz = round(os.path.getsize(path) / 1024, 2)
                self.lib_tree.insert('', 'end', values=(f, sz))
                count += 1
        self.status.insert(tk.END, f"Library: {count} file(s)\n")
        self.status.see(tk.END)

    def search(self):
        kw = self.search_entry.get().strip()
        srv = self.server_entry.get().strip()
        if not kw or not srv:
            messagebox.showerror("Error", "Enter a keyword and server")
            return
        use_online = kw.lower().startswith("online:")
        if use_online:
            kw = kw[7:].strip()
        try:
            local_results = self.search_local_index(srv, kw)
            online_results = search_online_public(kw) if use_online else []
            all_matches = local_results + online_results
            self.search_tree.delete(*self.search_tree.get_children())
            self.item_data = {}
            for match in all_matches:
                filename = match.get('filename', 'Unknown')
                size_bytes = match.get('size', 0)
                piece_size = match.get('piece_size', 0)
                piece_hashes = match.get('piece_hashes', [])
                if size_bytes >= 1024**3:
                    size_display = f"{size_bytes / (1024**3):.2f} GB"
                elif size_bytes >= 1024**2:
                    size_display = f"{size_bytes / (1024**2):.2f} MB"
                elif size_bytes > 0:
                    size_display = f"{size_bytes / 1024:.0f} KB"
                else:
                    size_display = "Unknown"
                peers = match.get('peers', [])
                magnet = match.get('magnet', '')
                source = match.get('source', 'Unknown')
                published = match.get('published', 'Unknown')
                engine_url = match.get('engine_url', '')
                if peers:
                    for peer in peers:
                        ip = peer.get('peer_ip') or peer.get('peer_ip_public') or peer.get('peer_ip_local')
                        port = peer.get('peer_port')
                        if ip and port:
                            ping = self.ping_peer(ip, port)
                            item_id = self.search_tree.insert(
                                '', 'end',
                                values=(filename, size_display, '1', '0', 'Local', ping, ''),
                                tags=()
                            )
                            self.item_data[item_id] = {
                                'piece_size': piece_size,
                                'piece_hashes': piece_hashes,
                                'size_bytes': size_bytes,
                                'peer_ip': ip,
                                'peer_port': port,
                            }
                else:
                    seeders = match.get('seeders', 0) if match.get('seeders', 0) > 0 else '0'
                    leechers = match.get('leechers', 0) if match.get('leechers', 0) > 0 else '0'
                    item_id = self.search_tree.insert(
                        '', 'end',
                        values=(filename, size_display, seeders, leechers, source, published, engine_url),
                        tags=()
                    )
                    self.item_data[item_id] = {
                        'piece_size': piece_size,
                        'piece_hashes': piece_hashes,
                        'size_bytes': size_bytes,
                        'magnet': magnet,
                    }
                if engine_url:
                    def open_url(event, url=engine_url):
                        webbrowser.open(url)
                    self.search_tree.tag_bind(item_id, '<Button-1>', open_url)
            self.download_btn.config(state='normal' if self.search_tree.get_children() else 'disabled')
        except Exception as e:
            logger.error(f"Search error: {e}")
            self.status.insert(tk.END, f"Search failed: {e}\n", "error")
            self.status.see(tk.END)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app = ttk.Window(themename="vapor")
    app.geometry("1100x750")
    FileSharingApp(app)
    app.mainloop()
