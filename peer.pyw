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
import ctypes

# PyInstaller resource path helper
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText
from ttkbootstrap.widgets.scrolled import ScrolledFrame
import tkinter as tk
from tkinter import messagebox, filedialog
import tkinter.ttk as standard_ttk
import libtorrent as lt
import maxminddb
from PIL import Image, ImageTk

# --- Hydra modules ---
from config import (
    SHARED_DIR, DOWNLOAD_DIR, DOWNLOAD_DIR_INCOMPLETE, DOWNLOAD_DIR_COMPLETE,
    MEDIA_DIR_MOVIES, MEDIA_DIR_TV, PEER_PORT, SERVER_PORT, CHUNK_SIZE,
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
from theme_manager import ThemeManager
from media_organizer import auto_move_completed_download

hide_console()


# ---------------------------------------------------------------------------
# File-type icons with checkbox (drawn programmatically, 32x16 composite)
# ---------------------------------------------------------------------------
_file_icons = {}

VIDEO_EXTS = {'mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mpg', 'mpeg', 'm4v', 'ts'}
AUDIO_EXTS = {'mp3', 'flac', 'wav', 'aac', 'ogg', 'wma', 'm4a', 'opus'}
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'svg', 'ico'}
TEXT_EXTS = {'txt', 'nfo', 'log', 'md', 'csv', 'srt', 'sub', 'ass', 'ssa', 'xml', 'json', 'yml', 'yaml', 'ini', 'cfg'}
ARCHIVE_EXTS = {'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'iso'}
EXEC_EXTS = {'exe', 'msi', 'bat', 'sh', 'cmd', 'app', 'dmg'}


def _ext_to_category(ext: str) -> str:
    ext = ext.lower().lstrip('.')
    if ext in VIDEO_EXTS:
        return 'video'
    elif ext in AUDIO_EXTS:
        return 'audio'
    elif ext in IMAGE_EXTS:
        return 'image'
    elif ext in TEXT_EXTS:
        return 'text'
    elif ext in ARCHIVE_EXTS:
        return 'archive'
    elif ext in EXEC_EXTS:
        return 'exec'
    return 'generic'


def _draw_base_page(draw, outline, fill, ox=0):
    """Draw a basic page/document shape with folded corner, offset by ox."""
    draw.rectangle([ox + 2, 1, ox + 13, 15], fill=fill, outline=outline)
    draw.polygon([(ox + 10, 1), (ox + 13, 4), (ox + 10, 4)], fill=outline)


def _draw_file_icon(draw, category, ox=0):
    """Draw a 16x16 file-type icon at horizontal offset ox."""
    if category == 'video':
        _draw_base_page(draw, '#4A90D9', '#2C3E6B', ox)
        draw.polygon([(ox + 6, 7), (ox + 6, 13), (ox + 11, 10)], fill='#4FC3F7')
    elif category == 'audio':
        _draw_base_page(draw, '#9B59B6', '#4A235A', ox)
        draw.ellipse([ox + 5, 11, ox + 8, 14], fill='#CE93D8')
        draw.line([(ox + 8, 7), (ox + 8, 13)], fill='#CE93D8', width=1)
        draw.line([(ox + 8, 7), (ox + 11, 6)], fill='#CE93D8', width=1)
    elif category == 'image':
        _draw_base_page(draw, '#27AE60', '#1B4332', ox)
        draw.polygon([(ox + 4, 13), (ox + 7, 9), (ox + 10, 13)], fill='#66BB6A')
        draw.ellipse([ox + 9, 7, ox + 12, 10], fill='#FDD835')
    elif category == 'text':
        _draw_base_page(draw, '#7F8C8D', '#2C3E50', ox)
        for y in [7, 9, 11, 13]:
            draw.line([(ox + 5, y), (ox + 11, y)], fill='#BDC3C7', width=1)
    elif category == 'archive':
        _draw_base_page(draw, '#E67E22', '#7D4E1A', ox)
        for y in [6, 8, 10, 12]:
            draw.rectangle([ox + 7, y, ox + 9, y + 1], fill='#F39C12')
    elif category == 'exec':
        _draw_base_page(draw, '#E74C3C', '#6B2020', ox)
        draw.rectangle([ox + 6, 8, ox + 10, 12], fill='#EF5350')
        draw.rectangle([ox + 7, 9, ox + 9, 11], fill='#6B2020')
    else:
        _draw_base_page(draw, '#95A5A6', '#34495E', ox)


def _draw_folder_icon(draw, ox=0):
    """Draw a 16x16 folder icon at horizontal offset ox."""
    draw.rectangle([ox + 1, 3, ox + 6, 5], fill='#F5A623', outline='#C68410')
    draw.rectangle([ox + 1, 5, ox + 14, 14], fill='#F5A623', outline='#C68410')
    draw.rectangle([ox + 2, 6, ox + 13, 7], fill='#FDCB6E')


def _draw_checkbox(draw, checked, ox=0):
    """Draw a 12x12 checkbox at offset ox, vertically centered in 16px."""
    y0, y1 = 2, 14
    draw.rectangle([ox, y0, ox + 12, y1], fill='#2C3E50', outline='#7F8C8D')
    if checked:
        # Checkmark
        draw.line([(ox + 2, 8), (ox + 5, 11)], fill='#2ECC71', width=2)
        draw.line([(ox + 5, 11), (ox + 10, 4)], fill='#2ECC71', width=2)


def get_file_icon(extension: str, checked: bool = True) -> "ImageTk.PhotoImage":
    """Return a 32x16 composite: [checkbox][file icon] for the given extension."""
    category = _ext_to_category(extension)
    cache_key = f"{category}_{'c' if checked else 'u'}"
    if cache_key in _file_icons:
        return _file_icons[cache_key]

    from PIL import Image as PilImage, ImageDraw
    img = PilImage.new('RGBA', (32, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_checkbox(draw, checked, ox=0)
    _draw_file_icon(draw, category, ox=16)

    tk_img = ImageTk.PhotoImage(img)
    _file_icons[cache_key] = tk_img
    return tk_img


def get_folder_icon(checked: bool = True) -> "ImageTk.PhotoImage":
    """Return a 32x16 composite: [checkbox][folder icon]."""
    cache_key = f"folder_{'c' if checked else 'u'}"
    if cache_key in _file_icons:
        return _file_icons[cache_key]

    from PIL import Image as PilImage, ImageDraw
    img = PilImage.new('RGBA', (32, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_checkbox(draw, checked, ox=0)
    _draw_folder_icon(draw, ox=16)

    tk_img = ImageTk.PhotoImage(img)
    _file_icons[cache_key] = tk_img
    return tk_img


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


class FileSharingApp:
    def __init__(self, root):
        self.root = root
        self.theme_manager = ThemeManager(self.root)
        try:
            self.root.iconbitmap(resource_path("image8.ico"))
        except Exception as e:
            print(f"Could not load icon: {e}")
        self.root.title(f"Hydra Torrent v0.1 - ({MY_PUBLIC_IP}:{PEER_PORT})")
        self.status_visible_var = tk.BooleanVar(value=True)
        # ====================
        # CUSTOM MENU BAR (frame-based for full theme control)
        # ====================
        self.menubar_frame = tk.Frame(root, pady=0)
        self.menubar_frame.pack(fill='x')
        self.menubar_separator = tk.Frame(root, height=1)
        self.menubar_separator.pack(fill='x')
        # File Menu
        file_menu = tk.Menu(root, tearoff=0)
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
        edit_menu = tk.Menu(root, tearoff=0)
        edit_menu.add_command(label="Preferences...",
                              command=self.open_preferences_dialog,
                              accelerator="Ctrl+P")
        edit_menu.add_command(label="Clear Completed Transfers",
                              command=self.clear_completed_transfers)
        # View Menu
        view_menu = tk.Menu(root, tearoff=0)
        view_menu.add_command(label="Refresh Library",
                              command=self.refresh_library,
                              accelerator="F5")
        view_menu.add_checkbutton(label="Show Status Log",
                                  command=self.toggle_status_log,
                                  variable=self.status_visible_var)
        view_menu.add_separator()
        theme_menu = tk.Menu(view_menu, tearoff=0)
        view_menu.add_cascade(label="Theme", menu=theme_menu)
        self.theme_var = tk.StringVar(value=self.theme_manager.load_saved_theme())
        for key, preset in ThemeManager.get_all_themes().items():
            theme_menu.add_radiobutton(
                label=f"{preset.name}  -  {preset.description}",
                variable=self.theme_var,
                value=key,
                command=lambda k=key: self.theme_manager.apply_theme(k)
            )
        # Tools Menu
        tools_menu = tk.Menu(root, tearoff=0)
        tools_menu.add_command(label="Create Torrent...",
                               command=self.open_torrent_creator_dialog)
        tools_menu.add_command(label="Check Port Status",
                               command=self.check_port_open)
        tools_menu.add_command(label="Open Shared Folder",
                               command=lambda: self.open_folder(SHARED_DIR))
        tools_menu.add_command(label="Open Downloads Folder",
                               command=lambda: self.open_folder(DOWNLOAD_DIR_COMPLETE))
        tools_menu.add_command(label="Open Movies Folder",
                               command=lambda: self.open_folder(MEDIA_DIR_MOVIES))
        tools_menu.add_command(label="Open TV Shows Folder",
                               command=lambda: self.open_folder(MEDIA_DIR_TV))
        # Help Menu
        help_menu = tk.Menu(root, tearoff=0)
        help_menu.add_command(label="About Hydra Torrent",
                              command=self.show_about)
        help_menu.add_command(label="Documentation / GitHub",
                              command=lambda: webbrowser.open("https://github.com/yourusername/hydra-torrent"))
        # Create menu labels in the custom bar
        self._menu_buttons = []
        for label_text, menu in [("File", file_menu), ("Edit", edit_menu),
                                 ("View", view_menu), ("Tools", tools_menu),
                                 ("Help", help_menu)]:
            lbl = tk.Label(self.menubar_frame, text=label_text,
                           padx=6, pady=3, bd=0, highlightthickness=0,
                           cursor="hand2")
            lbl.pack(side='left')
            lbl.bind("<Button-1>", lambda e, m=menu: m.post(e.widget.winfo_rootx(),
                     e.widget.winfo_rooty() + e.widget.winfo_height()))
            self._menu_buttons.append(lbl)
        self.theme_manager.register_menubar(self.menubar_frame, self._menu_buttons, self.menubar_separator)
        # Register dropdown menus with theme manager
        for m in [file_menu, edit_menu, view_menu, tools_menu, help_menu, theme_menu]:
            self.theme_manager.register_menu(m)
        # Load saved config
        config = load_config()
        saved_server = config.get('indexing_server', 'localhost')
        saved_mode = config.get('search_mode', 'online')
        self.jackett_api_key = config.get('jackett_api_key', "")
        # Main paned window for resizable panels
        self.main_pane = tk.PanedWindow(root, orient='vertical', sashwidth=4, bd=0,
                                        opaqueresize=True)
        self.main_pane.pack(fill='both', expand=True, padx=10, pady=(5, 5))
        self.theme_manager.register_paned_window(self.main_pane)
        self.notebook = ttk.Notebook(self.main_pane)
        self.main_pane.add(self.notebook, minsize=150, stretch='always')
        # Mode controls floating right, inline with notebook tabs
        mode_frame = ttk.Frame(root)
        mode_frame.place(relx=1.0, x=-10, y=self.notebook.winfo_y(), anchor='ne')
        key_label = ttk.Label(mode_frame, text="\U0001f511 API Key", cursor="hand2")
        key_label.pack(side='right', padx=(10, 0))
        key_label.bind("<Button-1>", lambda e: self.change_jackett_key())
        self.server_entry = ttk.Entry(mode_frame, width=20)
        self.server_entry.insert(0, saved_server)
        self.server_entry.bind("<FocusOut>", self.save_indexing_server)
        self.server_entry.bind("<Return>", self.save_indexing_server)
        self.server_label = ttk.Label(mode_frame, text="Server:")
        self.search_mode = tk.StringVar(value=saved_mode)
        self.local_btn = ttk.Radiobutton(
            mode_frame, text="Local Network", variable=self.search_mode,
            value="local", style="Hydra.Toolbutton",
            command=self._on_mode_change
        )
        self.local_btn.pack(side='right', padx=(2, 0))
        self.online_btn = ttk.Radiobutton(
            mode_frame, text="Online", variable=self.search_mode,
            value="online", style="Hydra.Toolbutton",
            command=self._on_mode_change
        )
        self.online_btn.pack(side='right', padx=(0, 2))
        self._on_mode_change()
        # Position mode_frame after layout settles
        def _position_mode_frame(event=None):
            try:
                # Get notebook's absolute screen position and convert to root-relative
                nb_screen_y = self.notebook.winfo_rooty()
                root_screen_y = self.root.winfo_rooty()
                nb_y = nb_screen_y - root_screen_y
                mode_frame.place(relx=1.0, x=-10, y=nb_y, anchor='ne')
            except tk.TclError:
                pass
        root.after(100, _position_mode_frame)
        root.bind("<Configure>", _position_mode_frame)
        # Library Tab
        lib = ttk.Frame(self.notebook)
        self.notebook.add(lib, text='Library')
        lib_frame = standard_ttk.Frame(lib)
        lib_frame.pack(fill='both', expand=True)
        self.lib_tree = ttk.Treeview(lib_frame, columns=('Name', 'Size'), show='headings')
        self.lib_tree.heading('Name', text='Name', anchor='w')
        self.lib_tree.heading('Size', text='Size', anchor='e')
        self.lib_tree.column('Name', width=500, anchor='w')
        self.lib_tree.column('Size', width=120, anchor='e')
        self.lib_tree.grid(row=0, column=0, sticky='nsew')
        lib_scroll = standard_ttk.Scrollbar(lib_frame, orient='vertical', command=self.lib_tree.yview)
        lib_scroll.grid(row=0, column=1, sticky='ns')
        self.lib_tree.configure(yscrollcommand=lib_scroll.set)
        lib_frame.rowconfigure(0, weight=1)
        lib_frame.columnconfigure(0, weight=1)
        ttk.Button(lib, text="Refresh", style="Accent.TButton", command=self.refresh_library).pack(pady=5, anchor='w', padx=5)
        # Search Tab
        search = ttk.Frame(self.notebook)
        self.notebook.add(search, text='Search')
        search_bar = ttk.Frame(search)
        search_bar.pack(fill='x', pady=(5, 5), padx=5)
        ttk.Label(search_bar, text="Keyword:").pack(side='left', padx=(0, 5))
        self.search_entry = ttk.Entry(search_bar, width=40)
        self.search_entry.pack(side='left', padx=(0, 10))
        self.search_entry.bind("<Return>", lambda e: self.search())
        ttk.Button(search_bar, text="Search", style="Accent.TButton", command=self.search).pack(side='left')
        self.search_loading = ttk.Progressbar(search, mode='indeterminate', style="Hydra.Striped.Horizontal.TProgressbar")
        # Search Treeview with qBittorrent-like columns
        self.search_tree_frame = standard_ttk.Frame(search)
        self.search_tree_frame.pack(fill='both', expand=True, pady=(5, 0))
        self.search_tree = ttk.Treeview(
            self.search_tree_frame,
            columns=('Name', 'Size', 'Seeders', 'Leechers', 'Engine', 'Published', 'Engine URL'),
            show='headings'
        )
        self.search_tree.heading('Name', text='Name', anchor='w')
        self.search_tree.heading('Size', text='Size', anchor='e')
        self.search_tree.heading('Seeders', text='Seeds', anchor='center')
        self.search_tree.heading('Leechers', text='Leech', anchor='center')
        self.search_tree.heading('Engine', text='Engine', anchor='center')
        self.search_tree.heading('Published', text='Published On', anchor='center')
        self.search_tree.heading('Engine URL', text='Engine URL', anchor='w')
        self.search_tree.column('Name', width=350, anchor='w')
        self.search_tree.column('Size', width=100, anchor='e')
        self.search_tree.column('Seeders', width=80, anchor='center')
        self.search_tree.column('Leechers', width=80, anchor='center')
        self.search_tree.column('Engine', width=120, anchor='center')
        self.search_tree.column('Published', width=140, anchor='center')
        self.search_tree.column('Engine URL', width=200, anchor='w')
        self.search_tree.grid(row=0, column=0, sticky='nsew')
        search_scroll = standard_ttk.Scrollbar(self.search_tree_frame, orient='vertical', command=self.search_tree.yview)
        search_scroll.grid(row=0, column=1, sticky='ns')
        self.search_tree.configure(yscrollcommand=search_scroll.set)
        self.search_tree_frame.rowconfigure(0, weight=1)
        self.search_tree_frame.columnconfigure(0, weight=1)
        self.search_tree.bind("<Double-1>", lambda e: self.download())
        self.download_btn = ttk.Button(search, text="Download", style="Success.TButton", command=self.download, state='disabled')
        self.download_btn.pack(pady=5)
        # Transfers Tab
        trans = ttk.Frame(self.notebook)
        self.notebook.add(trans, text='Transfers')
        trans_frame = standard_ttk.Frame(trans)
        trans_frame.pack(fill='both', expand=True)
        self.trans_tree = ttk.Treeview(
            trans_frame, columns=('File', 'Size', 'Prog', 'Status', 'Peer', 'Speed', 'ETA'), show='headings'
        )
        self.trans_tree.heading('File', text='File', anchor='w')
        self.trans_tree.heading('Size', text='Size', anchor='e')
        self.trans_tree.heading('Prog', text='Progress', anchor='center')
        self.trans_tree.heading('Status', text='Status', anchor='center')
        self.trans_tree.heading('Peer', text='Peer', anchor='w')
        self.trans_tree.heading('Speed', text='Speed', anchor='e')
        self.trans_tree.heading('ETA', text='ETA', anchor='e')
        self.theme_manager.register_treeview(self.trans_tree, has_alternating_rows=True)
        self.trans_tree.column('File', width=250, anchor='w')
        self.trans_tree.column('Size', width=80, anchor='e')
        self.trans_tree.column('Prog', width=150, anchor='center')
        self.trans_tree.column('Status', width=100, anchor='center')
        self.trans_tree.column('Peer', width=150, anchor='w')
        self.trans_tree.column('Speed', width=80, anchor='e')
        self.trans_tree.column('ETA', width=80, anchor='e')
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
        self.bottom_notebook = ttk.Notebook(self.main_pane)
        self.main_pane.add(self.bottom_notebook, minsize=80, stretch='never')
        # General tab
        general_tab = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(general_tab, text="General")
        ttk.Label(general_tab, text="Progress:").grid(row=0, column=0, sticky='w', padx=5, pady=2)
        self.progress_widget = PiecesBar(general_tab, height=20)
        self.progress_widget.grid(row=0, column=1, sticky='ew', padx=5, pady=2)
        self.progress_widget.bind("<Configure>", self.progress_widget.draw)
        self.theme_manager.register_pieces_bar(self.progress_widget)
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
        # ASCII art logo on the right
        HYDRA_ASCII = (
            "                  ++++\n"
            "           +##++#%@%########+++\n"
            "       +++%@@%%%%%#++%%%%%%###++\n"
            "      #@%%%%%%%%%%++#%###%%%%####++\n"
            "    #%@%%%%%%%%##+%%%#++#%#+++####++\n"
            "  +%@%%%%%%%%%%++#%%##%%%#+ +++++##+\n"
            "  %%%%%%%@%##+#%%%++ #%##++  ++++++#+\n"
            "  #%%%%%%% ## %%%%#+###+++     ++++ ++\n"
            " +%%%%%%#+###%###+++++++#++     +++ ++\n"
            "#%%##%%%####++#  +++++#+         +++ +\n"
            "##%####+++ +  +++##++++           +\n"
            "+##+++%  + + #+++++\n"
            " +++  + + +++\n"
            "   ++++++++       +      ++\n"
            "    +++++               +\n"
            "               +++++++++\n"
            "             +#+++++++  ++\n"
            "             #+++++++  +\n"
            "           #++++++++  +\n"
            "          +#+++++++\n"
            "          +++##++++\n"
            "         ++ +++++++\n"
            "\n"
            "      H Y D R A  T O R R E N T"
        )
        self.ascii_logo = ttk.Label(
            general_tab,
            text=HYDRA_ASCII,
            font=("Consolas", 7),
            justify='left',
            anchor='ne',
        )
        self.ascii_logo.grid(row=0, column=2, rowspan=7, sticky='ne', padx=(10, 5))
        self.theme_manager.register_ascii_logo(self.ascii_logo)
        # Trackers tab
        trackers_tab = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(trackers_tab, text="Trackers")
        self.trackers_tree = ttk.Treeview(trackers_tab, columns=('URL', 'Status', 'Peers', 'Fails'), show='headings')
        self.trackers_tree.heading('URL', text='URL', anchor='w')
        self.trackers_tree.heading('Status', text='Status', anchor='center')
        self.trackers_tree.heading('Peers', text='Peers', anchor='e')
        self.trackers_tree.heading('Fails', text='Fails', anchor='e')
        self.trackers_tree.column('URL', width=350, anchor='w')
        self.trackers_tree.column('Status', width=100, anchor='center')
        self.trackers_tree.column('Peers', width=80, anchor='e')
        self.trackers_tree.column('Fails', width=80, anchor='e')
        self.trackers_tree.pack(fill='both', expand=True)
        # Peers tab
        peers_tab = ttk.Frame(self.bottom_notebook)
        self.bottom_notebook.add(peers_tab, text="Peers")
        self.peers_tree = ttk.Treeview(peers_tab, columns=('Country/Region', 'IP', 'Down Speed', 'Up Speed', 'Client'), show='tree headings')
        self.peers_tree.heading('#0', text='Flag', anchor='center')
        self.peers_tree.heading('Country/Region', text='Country/Region', anchor='w')
        self.peers_tree.heading('IP', text='IP', anchor='w')
        self.peers_tree.heading('Down Speed', text='Down Speed', anchor='e')
        self.peers_tree.heading('Up Speed', text='Up Speed', anchor='e')
        self.peers_tree.heading('Client', text='Client', anchor='w')
        self.peers_tree.column('#0', width=30, anchor='center')
        self.peers_tree.column('Country/Region', width=120, anchor='w')
        self.peers_tree.column('IP', width=130, anchor='w')
        self.peers_tree.column('Down Speed', width=80, anchor='e')
        self.peers_tree.column('Up Speed', width=80, anchor='e')
        self.peers_tree.column('Client', width=140, anchor='w')
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
        self.theme_manager.register_menu(self.trans_context_menu)
        self.trans_tree.bind("<Button-3>", self.show_trans_context_menu)
        self.item_data = {}
        self.status = ScrolledText(self.main_pane, height=6, autohide=True, bootstyle="dark")
        self.main_pane.add(self.status, minsize=60, stretch='never')
        self.theme_manager.register_status_text(self.status)
        # Apply saved theme now that all widgets are registered
        self.theme_manager.apply_theme(self.theme_manager.load_saved_theme())
        about_label = ttk.Label(
            root,
            text="Hydra Torrent v0.1 \u2013 Built with \u2764\ufe0f",
            foreground=self.theme_manager.current.accent,
            justify="center",
            cursor="hand2"
        )
        about_label.pack(pady=(0, 5))
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
            self.geo_reader = maxminddb.open_database(resource_path('GeoLite2-Country.mmdb'))
        except Exception as e:
            logger.error(f"GeoIP DB load failed: {e} - Flags disabled")

    def _style_toplevel(self, win):
        """Apply icon and dark title bar to a child Toplevel window."""
        try:
            win.iconbitmap(resource_path("image8.ico"))
        except Exception:
            pass
        if os.name == 'nt':
            try:
                win.update()
                hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20,
                    ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

    def _on_mode_change(self):
        mode = self.search_mode.get()
        save_config('search_mode', mode)
        if mode == "local":
            self.server_entry.pack(side='right', padx=(0, 5), before=self.online_btn)
            self.server_label.pack(side='right', padx=(0, 3), before=self.server_entry)
        else:
            self.server_label.pack_forget()
            self.server_entry.pack_forget()

    def yscroll_set(self, *args):
        self.yscroll.set(*args)
        self.trans_tree.after(50, self.transfer_manager.update_progress_positions)

    def prompt_add_magnet(self):
        magnet = self.custom_askstring(
            "Add Magnet Link",
            "Paste the magnet link here:"
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
        prefs.geometry("600x500")
        prefs.transient(self.root)
        prefs.grab_set()
        self._style_toplevel(prefs)

        # Header
        ttk.Label(prefs, text="Hydra Torrent Preferences",
                 font=self.theme_manager.get_font(12, bold=True)).pack(pady=10)

        # Create notebook for tabs
        pref_notebook = ttk.Notebook(prefs)
        pref_notebook.pack(fill='both', expand=True, padx=10, pady=10)

        # Downloads Tab
        downloads_tab = ttk.Frame(pref_notebook)
        pref_notebook.add(downloads_tab, text="Downloads")

        config = load_config()

        # Auto-move setting
        ttk.Label(downloads_tab, text="Media Organization",
                 font=self.theme_manager.get_font(10, bold=True)).pack(anchor='w', pady=(10, 5), padx=10)

        auto_move_var = tk.BooleanVar(value=config.get('auto_move_to_plex', True))
        ttk.Checkbutton(downloads_tab,
                       text="Automatically organize completed downloads into Plex media folders",
                       variable=auto_move_var).pack(anchor='w', padx=20, pady=5)

        ttk.Label(downloads_tab,
                 text="When enabled, movies and TV shows will be automatically\n"
                      "categorized and moved to the appropriate Plex folders.",
                 foreground='gray').pack(anchor='w', padx=40, pady=(0, 10))

        # Directory settings
        ttk.Label(downloads_tab, text="Download Directories",
                 font=self.theme_manager.get_font(10, bold=True)).pack(anchor='w', pady=(10, 5), padx=10)

        dir_frame = ttk.Frame(downloads_tab)
        dir_frame.pack(fill='x', padx=20, pady=5)

        ttk.Label(dir_frame, text="Incomplete Downloads:").grid(row=0, column=0, sticky='w', pady=5)
        ttk.Label(dir_frame, text=DOWNLOAD_DIR_INCOMPLETE, foreground='gray').grid(row=0, column=1, sticky='w', padx=10)

        ttk.Label(dir_frame, text="Complete Downloads:").grid(row=1, column=0, sticky='w', pady=5)
        ttk.Label(dir_frame, text=DOWNLOAD_DIR_COMPLETE, foreground='gray').grid(row=1, column=1, sticky='w', padx=10)

        ttk.Label(dir_frame, text="Movies:").grid(row=2, column=0, sticky='w', pady=5)
        ttk.Label(dir_frame, text=MEDIA_DIR_MOVIES, foreground='gray').grid(row=2, column=1, sticky='w', padx=10)

        ttk.Label(dir_frame, text="TV Shows:").grid(row=3, column=0, sticky='w', pady=5)
        ttk.Label(dir_frame, text=MEDIA_DIR_TV, foreground='gray').grid(row=3, column=1, sticky='w', padx=10)

        # Plex Integration Tab
        plex_tab = ttk.Frame(pref_notebook)
        pref_notebook.add(plex_tab, text="Plex Integration")

        ttk.Label(plex_tab, text="Plex Server Settings",
                 font=self.theme_manager.get_font(10, bold=True)).pack(anchor='w', pady=(10, 5), padx=10)

        ttk.Label(plex_tab,
                 text="Configure Plex server to enable automatic library updates",
                 foreground='gray').pack(anchor='w', padx=20, pady=(0, 10))

        plex_frame = ttk.Frame(plex_tab)
        plex_frame.pack(fill='x', padx=20, pady=10)

        ttk.Label(plex_frame, text="Plex URL:").grid(row=0, column=0, sticky='w', pady=5)
        plex_url_var = tk.StringVar(value=config.get('plex_url', 'http://localhost:32400'))
        plex_url_entry = ttk.Entry(plex_frame, textvariable=plex_url_var, width=40)
        plex_url_entry.grid(row=0, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(plex_frame, text="Plex Token:").grid(row=1, column=0, sticky='w', pady=5)
        plex_token_var = tk.StringVar(value=config.get('plex_token', ''))
        plex_token_entry = ttk.Entry(plex_frame, textvariable=plex_token_var, width=40, show='*')
        plex_token_entry.grid(row=1, column=1, sticky='w', padx=10, pady=5)

        ttk.Label(plex_tab,
                 text="How to find your Plex token:\n"
                      "1. Open Plex Web App\n"
                      "2. Play any media file\n"
                      "3. Click the ⓘ icon → View XML\n"
                      "4. Look for X-Plex-Token in the URL",
                 foreground='gray', justify='left').pack(anchor='w', padx=20, pady=10)

        # Save button
        def save_preferences():
            save_config('auto_move_to_plex', auto_move_var.get())
            save_config('plex_url', plex_url_var.get().strip())
            save_config('plex_token', plex_token_var.get().strip())
            messagebox.showinfo("Saved", "Preferences saved successfully!")
            prefs.destroy()

        button_frame = ttk.Frame(prefs)
        button_frame.pack(fill='x', padx=10, pady=10)
        ttk.Button(button_frame, text="Save", command=save_preferences,
                  style="Success.TButton").pack(side='right', padx=5)
        ttk.Button(button_frame, text="Cancel", command=prefs.destroy,
                  style="Danger.TButton").pack(side='right', padx=5)

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
            self.main_pane.forget(self.status)
            self.status_visible_var.set(False)
        else:
            self.main_pane.add(self.status, minsize=60, stretch='never')
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
        self._style_toplevel(creator_win)
        ttk.Label(creator_win, text="Torrent Creator (Basic - Expand Later)", font=self.theme_manager.get_font(12)).pack(pady=15)
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

        ttk.Button(creator_win, text="Create .torrent", style="Success.TButton", command=create).pack(pady=20)

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
                    # Use Plex path if file was moved, otherwise use incomplete dir
                    save_path = t.get('plex_path', DOWNLOAD_DIR_INCOMPLETE)

                    params = {
                        'url': t['magnet'],
                        'save_path': save_path,
                        'storage_mode': lt.storage_mode_t(2),
                    }
                    handle = lt.add_magnet_uri(self.ses, t['magnet'], params)
                    handle.resume_data = t['resume_data']
                    if t.get('intended_pause', False):
                        handle.pause()
                    else:
                        handle.resume()
                    t['handle'] = handle
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
            'save_path': DOWNLOAD_DIR_INCOMPLETE,
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
            # HTTP/HTTPS trackers (more reliable, harder to block)
            "https://tracker.gbitt.info:443/announce",
            "https://tracker.tamersunion.org:443/announce",
            "http://tracker.opentrackr.org:1337/announce",
            # UDP trackers
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.tracker.cl:1337/announce",
            "udp://tracker.openbittorrent.com:6969/announce",
            "udp://open.demonii.com:1337/announce",
            "udp://open.stealth.si:80/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "udp://exodus.desync.com:6969/announce",
            "udp://bt1.archive.org:6969/announce",
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

        # Auto-move to Plex if enabled
        config = load_config()
        auto_move_enabled = config.get('auto_move_to_plex', True)  # Default enabled

        if auto_move_enabled:
            status_text.insert(tk.END, f" \u21b3 Auto-organizing media file...\n")
            status_text.see(tk.END)

            # Get the actual file path from the torrent
            ti = handle.get_torrent_info()
            files = ti.files()

            # Process each file in the torrent
            for file_info in files:
                file_path = os.path.join(DOWNLOAD_DIR_INCOMPLETE, file_info.path)

                if os.path.exists(file_path):
                    success, dest_path, error = auto_move_completed_download(
                        os.path.basename(file_path),
                        file_path
                    )

                    if success:
                        # Store the new location for resume
                        t['moved_to_plex'] = True
                        t['plex_path'] = os.path.dirname(dest_path)
                        status_text.insert(tk.END,
                            f" \u2705 Moved to Plex: {dest_path}\n", "success")
                    elif error and "Not a video file" not in error:
                        status_text.insert(tk.END,
                            f" \u26a0 Could not move: {error}\n", "error")

            status_text.see(tk.END)

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
                        # Use cached flag if available
                        if flag_key in self.flag_cache:
                            flag_img = self.flag_cache[flag_key]
                        else:
                            # Download flag asynchronously in background (non-blocking)
                            def download_flag(fkey):
                                try:
                                    flag_url = f"https://flagcdn.com/16x12/{fkey}.png"
                                    resp = requests.get(flag_url, timeout=1)
                                    if resp.status_code == 200:
                                        img = Image.open(io.BytesIO(resp.content))
                                        self.flag_cache[fkey] = ImageTk.PhotoImage(img)
                                except Exception:
                                    pass  # Silently fail for flags

                            # Start download in background, don't wait for it
                            threading.Thread(target=download_flag, args=(flag_key,), daemon=True).start()
                            flag_img = None  # Will show on next refresh
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
        dialog.geometry("900x850")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_toplevel(dialog)

        scrolled_main = ScrolledFrame(dialog, autohide=True, bootstyle="dark")
        scrolled_main.pack(fill='both', expand=True, padx=10, pady=10)
        main_frame = ttk.Frame(scrolled_main)
        main_frame.pack(fill='both', expand=True)

        save_label = ttk.Label(main_frame, text="Save at", font=self.theme_manager.get_font(10, bold=True))
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
        ttk.Checkbutton(main_frame, text="Remember last used save path", variable=remember_var).pack(anchor='w', pady=5)

        file_section_label = ttk.Label(main_frame, text="Files", font=self.theme_manager.get_font(10, bold=True))
        file_section_label.pack(anchor='w', pady=(20, 5))

        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill='x', pady=(0, 5))
        tree = ttk.Treeview(main_frame, columns=('Total Size', 'Priority'), show='tree headings', height=10)
        ttk.Button(controls_frame, text="Select All", command=lambda: self.select_all(tree, True)).pack(side='left', padx=5)
        ttk.Button(controls_frame, text="Select None", command=lambda: self.select_all(tree, False)).pack(side='left', padx=5)
        filter_label = ttk.Label(controls_frame, text="Filter:")
        filter_label.pack(side='left', padx=(10, 5))
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(controls_frame, textvariable=filter_var, width=30)
        filter_entry.pack(side='left', padx=5)
        filter_entry.bind("<KeyRelease>", lambda e: self.filter_tree(tree, filter_var.get()))

        tree.heading('#0', text='Name', anchor='w')
        tree.heading('Total Size', text='Total Size')
        tree.heading('Priority', text='Download Priority')
        tree.column('#0', width=450, anchor='w')
        tree.column('Total Size', width=120, anchor='e')
        tree.column('Priority', width=150, anchor='center')
        tree.pack(fill='both', expand=True)
        tree.bind('<Button-1>', lambda e: self.handle_tree_click(tree, e))

        status_label = ttk.Label(main_frame, text="Fetching metadata...", foreground=self.theme_manager.current.status_warning)
        status_label.pack(pady=5)

        info_label = ttk.Label(main_frame, text="Torrent Information", font=self.theme_manager.get_font(10, bold=True))
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
        ttk.Button(bottom_frame, text="Cancel", command=lambda: self.cancel_magnet(dialog), style="Danger.TButton").pack(side='right', padx=5)
        ok_btn = ttk.Button(bottom_frame, text="OK", state='disabled', style="Success.TButton")
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
                    dialog.after(0, lambda: status_label.config(text="Metadata ready \u2713", foreground=self.theme_manager.current.status_success))
                    dialog.after(0, lambda: ok_btn.config(state='normal'))
                    dialog.after(0, lambda: ok_btn.config(command=lambda: self.confirm_magnet_download(dialog, temp_handle, file_list, save_var.get(), never_var.get(), tree)))
            except Exception as e:
                if dialog.winfo_exists():
                    dialog.after(0, lambda: status_label.config(text=f"Error: {str(e)}", foreground=self.theme_manager.current.status_error))
                    dialog.after(0, lambda: ok_btn.config(state='disabled'))

        if magnet:
            threading.Thread(target=fetch_metadata, daemon=True).start()
        else:
            size_str = f"{file_size / (1024**3):.2f} GiB" if file_size > 0 else "Unknown"
            ext = os.path.splitext(filename)[1]
            icon = get_file_icon(ext, checked=True)
            self._tree_icon_refs = [icon]
            self._item_ext = {}
            item = tree.insert('', 'end', text=filename, image=icon, values=(size_str, 'Normal'), tags=('checked',))
            self._item_ext[item] = ext
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
        self._tree_icon_refs = []
        # Track each item's extension/type for icon toggling
        self._item_ext = {}
        for path, size, priority in file_list:
            parts = path.split(os.sep)
            current_parent = ''
            for i, part in enumerate(parts):
                full_part = os.sep.join(parts[:i+1])
                if full_part not in self.parent_map:
                    is_leaf = (i == len(parts) - 1)
                    size_str = format_size(size) if is_leaf else ""
                    if is_leaf:
                        ext = os.path.splitext(part)[1]
                        icon = get_file_icon(ext, checked=True)
                    else:
                        ext = '__folder__'
                        icon = get_folder_icon(checked=True)
                    self._tree_icon_refs.append(icon)
                    item = tree.insert(
                        current_parent, 'end',
                        text=part,
                        image=icon,
                        values=(size_str, priority if is_leaf else ''),
                        tags=('checked',),
                        open=not is_leaf,
                    )
                    self._item_ext[item] = ext
                    self.parent_map[full_part] = item
                    if is_leaf:
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
        if col == '#0':
            # Click on checkbox/icon/name area — toggle check state
            if tree.get_children(item):
                self.toggle_folder_check(tree, item)
            else:
                tags = tree.item(item)['tags']
                checked = 'checked' not in tags
                new_tags = ('checked',) if checked else ()
                tree.item(item, tags=new_tags)
                self._update_item_icon(tree, item, checked)
        elif col == '#2' and not tree.get_children(item):
            self.edit_priority(tree, item, event)

    def _update_item_icon(self, tree, item, checked):
        """Swap the icon to show checked/unchecked checkbox."""
        ext = getattr(self, '_item_ext', {}).get(item, '')
        if ext == '__folder__':
            icon = get_folder_icon(checked=checked)
        else:
            icon = get_file_icon(ext, checked=checked)
        self._tree_icon_refs.append(icon)
        tree.item(item, image=icon)

    def toggle_folder_check(self, tree, item):
        checked = 'checked' not in tree.item(item)['tags']
        def recurse(subitem):
            tree.item(subitem, tags=('checked',) if checked else ())
            self._update_item_icon(tree, subitem, checked)
            for child in tree.get_children(subitem):
                recurse(child)
        recurse(item)

    def edit_priority(self, tree, item, event):
        bbox = tree.bbox(item, column='#2')
        if not bbox:
            return
        combo = ttk.Combobox(tree, values=['Do not download', 'Low', 'Normal', 'High'], state='readonly')
        combo.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        combo.current(['Do not download', 'Low', 'Normal', 'High'].index(self.item_priorities.get(item, 'Normal')))
        def on_select(e):
            priority = combo.get()
            tree.set(item, column='#2', value=priority)
            self.item_priorities[item] = priority
            combo.destroy()
        combo.bind('<<ComboboxSelected>>', on_select)
        combo.bind('<FocusOut>', lambda e: combo.destroy())
        combo.focus_set()

    def filter_tree(self, tree, filter_text):
        filter_text = filter_text.lower()
        def recurse(item):
            visible = False
            name = tree.item(item, 'text').lower()
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
        def recurse(item):
            tree.item(item, tags=('checked',) if select else ())
            self._update_item_icon(tree, item, select)
            for child in tree.get_children(item):
                recurse(child)
        for item in tree.get_children(''):
            recurse(item)

    def get_free_space(self, folder):
        total, used, free = shutil.disk_usage(folder)
        return free

    def custom_askstring(self, title, prompt, initialvalue=''):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_toplevel(dialog)

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

        ttk.Button(btn_frame, text="OK", style="Success.TButton", command=ok).pack(side=LEFT, padx=10)
        ttk.Button(btn_frame, text="Cancel", style="Danger.TButton", command=cancel).pack(side=LEFT, padx=10)

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
        about = ttk.Toplevel(self.root)
        about.title("About Hydra Torrent")
        about.geometry("520x680")
        about.transient(self.root)
        about.grab_set()
        about.resizable(False, False)
        self._style_toplevel(about)

        # Logo at top
        try:
            logo_img = Image.open(resource_path("image8.ico"))
            logo_img = logo_img.resize((64, 64), Image.Resampling.LANCZOS)
            logo_photo = ImageTk.PhotoImage(logo_img)
            logo_label = ttk.Label(about, image=logo_photo)
            logo_label.image = logo_photo  # Keep reference
            logo_label.pack(pady=(15, 10))
        except Exception:
            # Fallback if icon not found
            ttk.Label(about, text="⚡", font=("Segoe UI", 48)).pack(pady=(15, 10))

        ttk.Label(about, text="Hydra Torrent",
                 font=self.theme_manager.get_font(18, bold=True)).pack()
        ttk.Label(about, text="v0.1",
                 font=self.theme_manager.get_font(10)).pack(pady=5)

        ttk.Separator(about, orient='horizontal').pack(fill='x', padx=20, pady=15)

        # Scrollable text area
        text_frame = ttk.Frame(about)
        text_frame.pack(fill='both', expand=True, padx=20)

        about_text = ScrolledText(text_frame, height=22, wrap='word', relief='flat',
                                 borderwidth=0, highlightthickness=0)
        about_text.pack(fill='both', expand=True)

        # The message
        message = """Look, we're all tired of the same story. You "buy" a movie on some streaming service and six months later it's just... gone. License expired, they say. Region locked. Not available in your country. Sorry, we removed it from your library.

That's not how ownership works. When you buy something, it's yours. You get to keep it, watch it whenever you want, share it with your friends. That's what Hydra Torrent is about.

This is a BitTorrent client that actually respects you. Download what you want, and it'll automatically organize everything for your Plex server. Movies go to movies, TV shows go to TV shows. You can see peers connecting from all over the world in real-time, resume your downloads if something crashes, search across multiple sites without opening fifteen browser tabs.

We built this because subscriptions keep going up while content keeps disappearing. This is for the people who remember when you could actually own your media collection, the archivists keeping culture alive, anyone who's tired of being told what they're allowed to watch.

The whole thing runs on Python and libtorrent doing the heavy lifting under the hood. We used ttkbootstrap to make it look decent instead of like something from Windows XP, and tkinter for the GUI. Plex handles our media libraries, MaxMind GeoLite2 shows us where our peers are connecting from around the globe. Huge thanks to the qBittorrent team for proving what a torrent client should look like and how it should work.

This is free software. No tracking, no ads, no premium tier, no bullshit. Fork it if you want, share it with everyone you know, make it better. The code's right there."""

        about_text.insert('1.0', message)
        about_text.configure(state='disabled')

        # OK button
        ttk.Button(about, text="OK", style="Accent.TButton",
                  command=about.destroy, width=15).pack(pady=15)

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
                api_key = self.custom_askstring(
                    "Jackett API Key",
                    "Paste your Jackett API key here:\n\n"
                    "(open http://127.0.0.1:9117 in your browser \u2192 look at top right corner)",
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
                sz = format_size(os.path.getsize(path))
                self.lib_tree.insert('', 'end', values=(f, sz))
                count += 1
        self.status.insert(tk.END, f"Library: {count} file(s)\n")
        self.status.see(tk.END)

    def search(self):
        kw = self.search_entry.get().strip()
        mode = self.search_mode.get()
        if not kw:
            messagebox.showerror("Error", "Enter a search keyword")
            return
        if mode == "local":
            srv = self.server_entry.get().strip()
            if not srv:
                messagebox.showerror("Error", "Enter a server address for local network search")
                return
        else:
            srv = None
        self.search_tree.delete(*self.search_tree.get_children())
        self.item_data = {}
        self.download_btn.config(state='disabled')
        self.search_loading.pack(fill='x', padx=10, pady=5, before=self.search_tree_frame)
        self.search_loading.start(15)
        mode_label = "online" if mode == "online" else "local network"
        self.status.insert(tk.END, f"Searching {mode_label} for '{kw}'...\n")
        self.status.see(tk.END)

        def do_search():
            try:
                local_results = self.search_local_index(srv, kw) if srv else []
                online_results = []
                if mode == "online":
                    # Try Jackett first if API key is configured
                    if self.jackett_api_key:
                        try:
                            jackett_url = "http://localhost:9117"
                            self.root.after(0, lambda: self.status.insert(tk.END, "Searching via Jackett...\n"))
                            online_results = search_jackett(jackett_url, kw, self.jackett_api_key)
                        except Exception as e:
                            logger.error(f"Jackett search failed: {e}")
                            self.root.after(0, lambda: self.status.insert(tk.END, f"Jackett unavailable, falling back to public sources...\n", "error"))
                    # Fall back to public scrapers if Jackett returned nothing
                    if not online_results:
                        online_results = search_online_public(kw)
                all_matches = local_results + online_results
                self.root.after(0, lambda: self._populate_search_results(all_matches))
            except Exception as e:
                logger.error(f"Search error: {e}")
                self.root.after(0, lambda: self._stop_search_loading())
                self.root.after(0, lambda: self.status.insert(tk.END, f"Search failed: {e}\n", "error"))
                self.root.after(0, lambda: self.status.see(tk.END))

        threading.Thread(target=do_search, daemon=True).start()

    def _stop_search_loading(self):
        self.search_loading.stop()
        self.search_loading.pack_forget()

    def _populate_search_results(self, all_matches):
        self._stop_search_loading()
        self.search_tree.delete(*self.search_tree.get_children())
        self.item_data = {}
        for match in all_matches:
            filename = match.get('filename', 'Unknown')
            size_bytes = match.get('size', 0)
            piece_size = match.get('piece_size', 0)
            piece_hashes = match.get('piece_hashes', [])
            size_display = format_size(size_bytes) if size_bytes > 0 else "Unknown"
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
                        item_id = self.search_tree.insert(
                            '', 'end',
                            values=(filename, size_display, '1', '0', 'Local', '', ''),
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
        self.status.insert(tk.END, f"Found {len(all_matches)} result(s).\n", "success")
        self.status.see(tk.END)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Load saved theme base for initial window creation
    _cfg = load_config()
    _saved_key = _cfg.get('theme', 'hydra_default')
    _preset = ThemeManager.get_theme(_saved_key)
    _base = _preset.ttkbootstrap_base if _preset else "cyborg"
    app = ttk.Window(themename=_base)
    app.geometry("1100x750")
    FileSharingApp(app)
    app.mainloop()
