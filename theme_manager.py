"""
Hydra Torrent Theme Manager
Curated preset themes with cyberpunk/neon/sci-fi aesthetics.
"""

import tkinter as tk
import ttkbootstrap as ttk
import os
import ctypes
from dataclasses import dataclass, field
from typing import Dict, Optional
from config import load_config, save_config


@dataclass
class ThemePreset:
    name: str
    key: str
    ttkbootstrap_base: str
    description: str

    # Core palette
    bg_primary: str
    bg_secondary: str
    fg_primary: str
    fg_secondary: str
    accent: str
    accent_secondary: str

    # Menu colors (tk.Menu doesn't follow ttkbootstrap)
    menu_bg: str
    menu_fg: str
    menu_active_bg: str
    menu_active_fg: str

    # Treeview alternating rows
    treeview_evenrow: str
    treeview_oddrow: str
    treeview_select_bg: str
    treeview_select_fg: str

    # PiecesBar / Canvas colors
    pieces_complete: str
    pieces_empty: str
    pieces_progress: str
    pieces_text: str
    pieces_bg: str

    # Status log text tags
    status_success: str
    status_error: str
    status_warning: str

    # Font family
    font_family: str = "Helvetica"


# ---------------------------------------------------------------------------
# 15 Curated Theme Presets
# ---------------------------------------------------------------------------

THEME_PRESETS: Dict[str, ThemePreset] = {}

# 1. Cyberpunk Neon — hot pink + electric cyan on deep purple
THEME_PRESETS["cyberpunk_neon"] = ThemePreset(
    name="Cyberpunk Neon",
    key="cyberpunk_neon",
    ttkbootstrap_base="cyborg",
    description="Neon-soaked night city",
    bg_primary="#0d0221",
    bg_secondary="#1a0533",
    fg_primary="#e0e0ff",
    fg_secondary="#8888aa",
    accent="#ff2a6d",
    accent_secondary="#05d9e8",
    menu_bg="#0d0221",
    menu_fg="#e0e0ff",
    menu_active_bg="#ff2a6d",
    menu_active_fg="#ffffff",
    treeview_evenrow="#1a0533",
    treeview_oddrow="#0d0221",
    treeview_select_bg="#ff2a6d",
    treeview_select_fg="#ffffff",
    pieces_complete="#ff2a6d",
    pieces_empty="#1a0533",
    pieces_progress="#05d9e8",
    pieces_text="#ffffff",
    pieces_bg="#0d0221",
    status_success="#05d9e8",
    status_error="#ff2a6d",
    status_warning="#f5a623",
)

# 2. Matrix — green on black terminal
THEME_PRESETS["matrix"] = ThemePreset(
    name="Matrix",
    key="matrix",
    ttkbootstrap_base="darkly",
    description="Wake up, Neo...",
    bg_primary="#0a0a0a",
    bg_secondary="#0f1a0f",
    fg_primary="#00ff41",
    fg_secondary="#008f11",
    accent="#00ff41",
    accent_secondary="#39ff14",
    menu_bg="#0a0a0a",
    menu_fg="#00ff41",
    menu_active_bg="#003b00",
    menu_active_fg="#39ff14",
    treeview_evenrow="#0f1a0f",
    treeview_oddrow="#0a0a0a",
    treeview_select_bg="#003b00",
    treeview_select_fg="#39ff14",
    pieces_complete="#00ff41",
    pieces_empty="#0f1a0f",
    pieces_progress="#00ff41",
    pieces_text="#0a0a0a",
    pieces_bg="#0a0a0a",
    status_success="#39ff14",
    status_error="#ff0000",
    status_warning="#ccff00",
    font_family="Consolas",
)

# 3. Tron Legacy — electric blue + orange on black
THEME_PRESETS["tron"] = ThemePreset(
    name="Tron Legacy",
    key="tron",
    ttkbootstrap_base="darkly",
    description="The Grid awaits",
    bg_primary="#000000",
    bg_secondary="#0c141f",
    fg_primary="#6fc3df",
    fg_secondary="#3a7ca5",
    accent="#6fc3df",
    accent_secondary="#df740c",
    menu_bg="#000000",
    menu_fg="#6fc3df",
    menu_active_bg="#0c141f",
    menu_active_fg="#df740c",
    treeview_evenrow="#0c141f",
    treeview_oddrow="#000000",
    treeview_select_bg="#1a3a4a",
    treeview_select_fg="#df740c",
    pieces_complete="#6fc3df",
    pieces_empty="#0c141f",
    pieces_progress="#df740c",
    pieces_text="#ffffff",
    pieces_bg="#000000",
    status_success="#6fc3df",
    status_error="#df740c",
    status_warning="#f5e642",
)

# 4. Synthwave — retro purple/pink sunset
THEME_PRESETS["synthwave"] = ThemePreset(
    name="Synthwave",
    key="synthwave",
    ttkbootstrap_base="vapor",
    description="Retro-futuristic sunset",
    bg_primary="#2b213a",
    bg_secondary="#1e1528",
    fg_primary="#f4eeff",
    fg_secondary="#c4b7d5",
    accent="#ff6ac1",
    accent_secondary="#a855f7",
    menu_bg="#1e1528",
    menu_fg="#f4eeff",
    menu_active_bg="#ff6ac1",
    menu_active_fg="#ffffff",
    treeview_evenrow="#2b213a",
    treeview_oddrow="#1e1528",
    treeview_select_bg="#a855f7",
    treeview_select_fg="#ffffff",
    pieces_complete="#ff6ac1",
    pieces_empty="#2b213a",
    pieces_progress="#a855f7",
    pieces_text="#ffffff",
    pieces_bg="#1e1528",
    status_success="#ff6ac1",
    status_error="#ff4444",
    status_warning="#f5a623",
)

# 5. Deep Ocean — navy + bioluminescent teal
THEME_PRESETS["deep_ocean"] = ThemePreset(
    name="Deep Ocean",
    key="deep_ocean",
    ttkbootstrap_base="superhero",
    description="Abyssal depths",
    bg_primary="#0a1628",
    bg_secondary="#0f2035",
    fg_primary="#c8d6e5",
    fg_secondary="#576574",
    accent="#00d2d3",
    accent_secondary="#0abde3",
    menu_bg="#0a1628",
    menu_fg="#c8d6e5",
    menu_active_bg="#0f2035",
    menu_active_fg="#00d2d3",
    treeview_evenrow="#0f2035",
    treeview_oddrow="#0a1628",
    treeview_select_bg="#0abde3",
    treeview_select_fg="#ffffff",
    pieces_complete="#00d2d3",
    pieces_empty="#0f2035",
    pieces_progress="#0abde3",
    pieces_text="#ffffff",
    pieces_bg="#0a1628",
    status_success="#00d2d3",
    status_error="#ee5a24",
    status_warning="#feca57",
)

# 6. Blood Moon — deep crimson/burgundy
THEME_PRESETS["blood_moon"] = ThemePreset(
    name="Blood Moon",
    key="blood_moon",
    ttkbootstrap_base="cyborg",
    description="Crimson eclipse",
    bg_primary="#1a0000",
    bg_secondary="#2d0a0a",
    fg_primary="#e8c4c4",
    fg_secondary="#8b5e5e",
    accent="#dc143c",
    accent_secondary="#8b0000",
    menu_bg="#1a0000",
    menu_fg="#e8c4c4",
    menu_active_bg="#dc143c",
    menu_active_fg="#ffffff",
    treeview_evenrow="#2d0a0a",
    treeview_oddrow="#1a0000",
    treeview_select_bg="#dc143c",
    treeview_select_fg="#ffffff",
    pieces_complete="#dc143c",
    pieces_empty="#2d0a0a",
    pieces_progress="#ff4444",
    pieces_text="#ffffff",
    pieces_bg="#1a0000",
    status_success="#ff6b6b",
    status_error="#dc143c",
    status_warning="#ff8c42",
)

# 7. Hacker Terminal — amber on black CRT
THEME_PRESETS["hacker_terminal"] = ThemePreset(
    name="Hacker Terminal",
    key="hacker_terminal",
    ttkbootstrap_base="darkly",
    description="Old-school amber CRT",
    bg_primary="#0a0a00",
    bg_secondary="#141400",
    fg_primary="#ffb000",
    fg_secondary="#996600",
    accent="#ffb000",
    accent_secondary="#ff8c00",
    menu_bg="#0a0a00",
    menu_fg="#ffb000",
    menu_active_bg="#332200",
    menu_active_fg="#ff8c00",
    treeview_evenrow="#141400",
    treeview_oddrow="#0a0a00",
    treeview_select_bg="#332200",
    treeview_select_fg="#ff8c00",
    pieces_complete="#ffb000",
    pieces_empty="#141400",
    pieces_progress="#ffb000",
    pieces_text="#0a0a00",
    pieces_bg="#0a0a00",
    status_success="#ffb000",
    status_error="#ff4444",
    status_warning="#ffdd00",
    font_family="Consolas",
)

# 8. Arctic — ice blue on slate
THEME_PRESETS["arctic"] = ThemePreset(
    name="Arctic",
    key="arctic",
    ttkbootstrap_base="superhero",
    description="Frozen digital tundra",
    bg_primary="#1b2838",
    bg_secondary="#1e3246",
    fg_primary="#c7d5e0",
    fg_secondary="#8496a9",
    accent="#66c0f4",
    accent_secondary="#4fc3f7",
    menu_bg="#1b2838",
    menu_fg="#c7d5e0",
    menu_active_bg="#2a475e",
    menu_active_fg="#66c0f4",
    treeview_evenrow="#1e3246",
    treeview_oddrow="#1b2838",
    treeview_select_bg="#2a475e",
    treeview_select_fg="#66c0f4",
    pieces_complete="#66c0f4",
    pieces_empty="#1e3246",
    pieces_progress="#4fc3f7",
    pieces_text="#ffffff",
    pieces_bg="#1b2838",
    status_success="#66c0f4",
    status_error="#e74c3c",
    status_warning="#f39c12",
)

# 9. Neon Tokyo — hot pink + neon green on dark gray
THEME_PRESETS["neon_tokyo"] = ThemePreset(
    name="Neon Tokyo",
    key="neon_tokyo",
    ttkbootstrap_base="cyborg",
    description="Electric Akihabara nights",
    bg_primary="#121212",
    bg_secondary="#1e1e1e",
    fg_primary="#f0f0f0",
    fg_secondary="#888888",
    accent="#fe019a",
    accent_secondary="#00ff87",
    menu_bg="#121212",
    menu_fg="#f0f0f0",
    menu_active_bg="#fe019a",
    menu_active_fg="#ffffff",
    treeview_evenrow="#1e1e1e",
    treeview_oddrow="#121212",
    treeview_select_bg="#fe019a",
    treeview_select_fg="#ffffff",
    pieces_complete="#00ff87",
    pieces_empty="#1e1e1e",
    pieces_progress="#fe019a",
    pieces_text="#ffffff",
    pieces_bg="#121212",
    status_success="#00ff87",
    status_error="#fe019a",
    status_warning="#fdff00",
)

# 10. Void — ultra-minimal monochrome
THEME_PRESETS["void"] = ThemePreset(
    name="Void",
    key="void",
    ttkbootstrap_base="darkly",
    description="Embrace the emptiness",
    bg_primary="#0e0e0e",
    bg_secondary="#181818",
    fg_primary="#a0a0a0",
    fg_secondary="#555555",
    accent="#ffffff",
    accent_secondary="#888888",
    menu_bg="#0e0e0e",
    menu_fg="#a0a0a0",
    menu_active_bg="#2a2a2a",
    menu_active_fg="#ffffff",
    treeview_evenrow="#181818",
    treeview_oddrow="#0e0e0e",
    treeview_select_bg="#333333",
    treeview_select_fg="#ffffff",
    pieces_complete="#ffffff",
    pieces_empty="#181818",
    pieces_progress="#888888",
    pieces_text="#ffffff",
    pieces_bg="#0e0e0e",
    status_success="#a0a0a0",
    status_error="#ff4444",
    status_warning="#cccccc",
)

# 11. Solar Flare — warm orange/yellow on dark brown
THEME_PRESETS["solar_flare"] = ThemePreset(
    name="Solar Flare",
    key="solar_flare",
    ttkbootstrap_base="solar",
    description="Surface of the sun",
    bg_primary="#1a1100",
    bg_secondary="#2d1f00",
    fg_primary="#fdf6e3",
    fg_secondary="#b58900",
    accent="#cb4b16",
    accent_secondary="#dc322f",
    menu_bg="#1a1100",
    menu_fg="#fdf6e3",
    menu_active_bg="#cb4b16",
    menu_active_fg="#fdf6e3",
    treeview_evenrow="#2d1f00",
    treeview_oddrow="#1a1100",
    treeview_select_bg="#cb4b16",
    treeview_select_fg="#fdf6e3",
    pieces_complete="#cb4b16",
    pieces_empty="#2d1f00",
    pieces_progress="#dc322f",
    pieces_text="#fdf6e3",
    pieces_bg="#1a1100",
    status_success="#859900",
    status_error="#dc322f",
    status_warning="#b58900",
)

# 12. Phantom — purple/violet on charcoal (Dracula-inspired)
THEME_PRESETS["phantom"] = ThemePreset(
    name="Phantom",
    key="phantom",
    ttkbootstrap_base="vapor",
    description="Ghostly purple haze",
    bg_primary="#16101e",
    bg_secondary="#201830",
    fg_primary="#d4c5f9",
    fg_secondary="#7e6ba4",
    accent="#bd93f9",
    accent_secondary="#ff79c6",
    menu_bg="#16101e",
    menu_fg="#d4c5f9",
    menu_active_bg="#bd93f9",
    menu_active_fg="#16101e",
    treeview_evenrow="#201830",
    treeview_oddrow="#16101e",
    treeview_select_bg="#bd93f9",
    treeview_select_fg="#16101e",
    pieces_complete="#bd93f9",
    pieces_empty="#201830",
    pieces_progress="#ff79c6",
    pieces_text="#ffffff",
    pieces_bg="#16101e",
    status_success="#50fa7b",
    status_error="#ff5555",
    status_warning="#f1fa8c",
)

# 13. Hydra Default — current look cleaned up (ships as default)
THEME_PRESETS["hydra_default"] = ThemePreset(
    name="Hydra Default",
    key="hydra_default",
    ttkbootstrap_base="cyborg",
    description="Classic Hydra look",
    bg_primary="#1a1a2e",
    bg_secondary="#2c0a3a",
    fg_primary="#ffffff",
    fg_secondary="#aaaaaa",
    accent="#6c5ce7",
    accent_secondary="#a29bfe",
    menu_bg="#2c3e50",
    menu_fg="#ffffff",
    menu_active_bg="#34495e",
    menu_active_fg="#ffffff",
    treeview_evenrow="#3a0a4a",
    treeview_oddrow="#2c0a3a",
    treeview_select_bg="#6c5ce7",
    treeview_select_fg="#ffffff",
    pieces_complete="#00ff00",
    pieces_empty="#333333",
    pieces_progress="#0066ff",
    pieces_text="#ffffff",
    pieces_bg="#333333",
    status_success="#00ff00",
    status_error="#ff4444",
    status_warning="#ffa500",
)

# 14. Emerald Shadow — dark forest green
THEME_PRESETS["emerald_shadow"] = ThemePreset(
    name="Emerald Shadow",
    key="emerald_shadow",
    ttkbootstrap_base="darkly",
    description="Deep forest canopy",
    bg_primary="#0a1f0a",
    bg_secondary="#0f2e0f",
    fg_primary="#b8e6b8",
    fg_secondary="#5a8a5a",
    accent="#2ecc71",
    accent_secondary="#27ae60",
    menu_bg="#0a1f0a",
    menu_fg="#b8e6b8",
    menu_active_bg="#2ecc71",
    menu_active_fg="#0a1f0a",
    treeview_evenrow="#0f2e0f",
    treeview_oddrow="#0a1f0a",
    treeview_select_bg="#2ecc71",
    treeview_select_fg="#0a1f0a",
    pieces_complete="#2ecc71",
    pieces_empty="#0f2e0f",
    pieces_progress="#27ae60",
    pieces_text="#ffffff",
    pieces_bg="#0a1f0a",
    status_success="#2ecc71",
    status_error="#e74c3c",
    status_warning="#f1c40f",
)

# 15. Midnight Steel — cool blue-gray industrial
THEME_PRESETS["midnight_steel"] = ThemePreset(
    name="Midnight Steel",
    key="midnight_steel",
    ttkbootstrap_base="superhero",
    description="Cold precision engineering",
    bg_primary="#1c1e26",
    bg_secondary="#232530",
    fg_primary="#c8ccd4",
    fg_secondary="#636d83",
    accent="#5294e2",
    accent_secondary="#3d84c6",
    menu_bg="#1c1e26",
    menu_fg="#c8ccd4",
    menu_active_bg="#5294e2",
    menu_active_fg="#ffffff",
    treeview_evenrow="#232530",
    treeview_oddrow="#1c1e26",
    treeview_select_bg="#5294e2",
    treeview_select_fg="#ffffff",
    pieces_complete="#5294e2",
    pieces_empty="#232530",
    pieces_progress="#3d84c6",
    pieces_text="#ffffff",
    pieces_bg="#1c1e26",
    status_success="#5294e2",
    status_error="#e06c75",
    status_warning="#e5c07b",
)


# ---------------------------------------------------------------------------
# ThemeManager
# ---------------------------------------------------------------------------

class ThemeManager:
    def __init__(self, root):
        self.root = root
        self._current_theme: Optional[ThemePreset] = None
        self._menus: list = []
        self._pieces_bars: list = []
        self._status_texts: list = []
        self._treeviews: list = []
        self._menubar_frame = None
        self._menubar_buttons: list = []
        self._menubar_separator = None
        self._paned_windows: list = []
        self._ascii_logo = None

    @property
    def current(self) -> ThemePreset:
        return self._current_theme

    @staticmethod
    def get_all_themes() -> Dict[str, ThemePreset]:
        return THEME_PRESETS

    @staticmethod
    def get_theme(key: str) -> Optional[ThemePreset]:
        return THEME_PRESETS.get(key)

    def register_menu(self, menu):
        self._menus.append(menu)

    def register_pieces_bar(self, bar):
        self._pieces_bars.append(bar)

    def register_status_text(self, widget):
        self._status_texts.append(widget)

    def register_treeview(self, tree, has_alternating_rows=False):
        self._treeviews.append((tree, has_alternating_rows))

    def register_paned_window(self, pw):
        self._paned_windows.append(pw)

    def register_ascii_logo(self, label):
        self._ascii_logo = label

    def register_menubar(self, frame, buttons, separator=None):
        self._menubar_frame = frame
        self._menubar_buttons = buttons
        self._menubar_separator = separator

    def load_saved_theme(self) -> str:
        config = load_config()
        return config.get('theme', 'hydra_default')

    def apply_theme(self, key: str):
        theme = THEME_PRESETS.get(key)
        if not theme:
            theme = THEME_PRESETS["hydra_default"]

        self._current_theme = theme

        # Switch ttkbootstrap base theme
        style = ttk.Style()
        try:
            style.theme_use(theme.ttkbootstrap_base)
        except Exception:
            pass  # ttkbootstrap raises on duplicate element creation when switching themes

        # Remove white border on Notebook tabs
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab",
            borderwidth=0,
            padding=(10, 4),
        )
        style.map("TNotebook.Tab",
            bordercolor=[("selected", theme.accent)],
        )

        # Custom menubar (tk widgets, styled directly)
        self._apply_menubar(theme)

        # Re-apply Treeview style overrides (theme_use resets them)
        style.configure("Treeview", rowheight=22, borderwidth=1, relief="flat")
        style.configure("Treeview.Item", borderwidth=1, relief="flat")
        style.map("Treeview",
            background=[("selected", theme.treeview_select_bg)],
            foreground=[("selected", theme.treeview_select_fg)]
        )

        # Style the mode toggle buttons to match theme accent
        style.configure("Hydra.Toolbutton",
            background=theme.bg_secondary,
            foreground=theme.fg_primary,
            font=(theme.font_family, 9),
            padding=(8, 4),
        )
        style.map("Hydra.Toolbutton",
            background=[
                ("selected", theme.accent),
                ("active", theme.accent_secondary),
            ],
            foreground=[
                ("selected", theme.menu_active_fg),
                ("active", theme.fg_primary),
            ],
        )

        # Style all buttons to follow theme colors
        style.configure("Accent.TButton",
            background=theme.accent,
            foreground=theme.menu_active_fg,
            font=(theme.font_family, 9),
        )
        style.map("Accent.TButton",
            background=[("active", theme.accent_secondary)],
        )
        style.configure("Success.TButton",
            background=theme.status_success,
            foreground=theme.bg_primary,
            font=(theme.font_family, 9),
        )
        style.map("Success.TButton",
            background=[("active", theme.accent_secondary)],
        )
        style.configure("Danger.TButton",
            background=theme.status_error,
            foreground="#ffffff",
            font=(theme.font_family, 9),
        )
        style.map("Danger.TButton",
            background=[("active", theme.accent_secondary)],
        )

        # Progressbar
        style.configure("Hydra.Striped.Horizontal.TProgressbar",
            background=theme.accent,
            troughcolor=theme.bg_secondary,
        )

        self._apply_dark_titlebar()
        self._apply_menus(theme)
        self._apply_pieces_bars(theme)
        self._apply_status_texts(theme)
        self._apply_treeviews(theme)
        self._apply_paned_windows(theme)
        self._apply_ascii_logo(theme)

        save_config('theme', key)

    def _apply_menubar(self, theme: ThemePreset):
        if self._menubar_frame is None:
            return
        try:
            self._menubar_frame.configure(bg=theme.menu_bg)
            for lbl in self._menubar_buttons:
                lbl.configure(
                    bg=theme.menu_bg,
                    fg=theme.menu_fg,
                    font=(theme.font_family, 9),
                )
                lbl.bind("<Enter>", lambda e, t=theme: e.widget.configure(
                    bg=t.menu_active_bg, fg=t.menu_active_fg))
                lbl.bind("<Leave>", lambda e, t=theme: e.widget.configure(
                    bg=t.menu_bg, fg=t.menu_fg))
            if self._menubar_separator:
                self._menubar_separator.configure(bg=theme.bg_secondary)
        except tk.TclError:
            pass

    def _apply_dark_titlebar(self):
        if os.name != 'nt':
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

    def _apply_menus(self, theme: ThemePreset):
        for menu in self._menus:
            try:
                menu.configure(
                    bg=theme.menu_bg,
                    fg=theme.menu_fg,
                    activebackground=theme.menu_active_bg,
                    activeforeground=theme.menu_active_fg,
                )
                self._apply_submenu_colors(menu, theme)
            except tk.TclError:
                pass

    def _apply_submenu_colors(self, menu, theme: ThemePreset):
        try:
            end = menu.index('end')
            if end is None:
                return
            for i in range(end + 1):
                try:
                    if menu.type(i) == 'cascade':
                        submenu = menu.nametowidget(menu.entrycget(i, 'menu'))
                        submenu.configure(
                            bg=theme.menu_bg,
                            fg=theme.menu_fg,
                            activebackground=theme.menu_active_bg,
                            activeforeground=theme.menu_active_fg,
                        )
                        self._apply_submenu_colors(submenu, theme)
                except (tk.TclError, ValueError):
                    pass
        except tk.TclError:
            pass

    def _apply_pieces_bars(self, theme: ThemePreset):
        for bar in self._pieces_bars:
            try:
                bar.configure(bg=theme.pieces_bg)
                bar.colors = {
                    'complete': theme.pieces_complete,
                    'empty': theme.pieces_empty,
                    'progress': theme.pieces_progress,
                    'text': theme.pieces_text,
                }
                bar.draw()
            except tk.TclError:
                pass

    def _apply_status_texts(self, theme: ThemePreset):
        for widget in self._status_texts:
            try:
                widget.tag_config("success", foreground=theme.status_success)
                widget.tag_config("error", foreground=theme.status_error)
            except tk.TclError:
                pass

    def _apply_treeviews(self, theme: ThemePreset):
        for tree, has_alternating in self._treeviews:
            try:
                if has_alternating:
                    tree.tag_configure('evenrow', background=theme.treeview_evenrow)
                    tree.tag_configure('oddrow', background=theme.treeview_oddrow)
            except tk.TclError:
                pass

    def _apply_ascii_logo(self, theme: ThemePreset):
        if self._ascii_logo is None:
            return
        try:
            self._ascii_logo.configure(foreground=theme.accent)
        except tk.TclError:
            pass

    def _apply_paned_windows(self, theme: ThemePreset):
        for pw in self._paned_windows:
            try:
                pw.configure(
                    bg=theme.bg_primary,
                    sashrelief='flat',
                )
            except tk.TclError:
                pass

    def get_font(self, size: int = 10, bold: bool = False) -> tuple:
        weight = "bold" if bold else "normal"
        return (self._current_theme.font_family, size, weight)
