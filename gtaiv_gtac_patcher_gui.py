#!/usr/bin/env python3
r"""
GTA IV Retail Patch Installer for GTA Connected

Windows GUI utility that:
- Detects or lets you select a GTA IV: Complete Edition installation.
- Downloads Retail-1080.zip or Retail-1070.zip.
- Extracts the Retail folder into:
    ...\Grand Theft Auto IV\GTAIV\Retail
- Validates:
    ...\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
- Copies that EXE path for GTA Connected -> Tools -> Game Settings.
- Writes the Game EXE Path to the GTA Connected registry key automatically.

No third-party Python packages required.
Build EXE:
    uv sync
    uv run --no-project pyinstaller --onefile --windowed --name GTAIV-GTAConnected-Patcher gtaiv_gtac_patcher_gui.py
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tkinter import (
    BOTH, BOTTOM, END, LEFT, RIGHT, TOP, W, X, Y,
    BooleanVar, StringVar, Tk, Frame,
)
from tkinter import filedialog
from tkinter import ttk

try:
    import winreg  # type: ignore
except ImportError:
    winreg = None  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_ID = "12210"
APP_NAME = "Grand Theft Auto IV"
EXPECTED_RETAIL_EXE = Path("Retail") / "GTAIV.exe"

GTAC_REG_KEY = r"SOFTWARE\Jack's Mini Network\Grand Theft Auto Connected\Grand Theft Auto IV"
GTAC_REG_VALUE = "Game EXE Path"

PATCHES = {
    "1.0.8.0 (recommended)": "https://wiki.gtaconnected.com/downloads/Retail-1080.zip",
    "1.0.7.0": "https://wiki.gtaconnected.com/downloads/Retail-1070.zip",
}

DEFAULT_STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")
DEFAULT_GTA_ROOT = DEFAULT_STEAM_PATH / "steamapps" / "common" / "Grand Theft Auto IV"

# Emoji-aware font stack: Segoe UI Emoji renders colour emoji on Windows 8.1+;
# fall back to plain Segoe UI on older systems or non-Windows.
_EMOJI_FAMILY = "Segoe UI Emoji"
_UI_FAMILY = "Segoe UI"

FONT_UI       = (_UI_FAMILY, 9)
FONT_UI_BOLD  = (_UI_FAMILY, 9, "bold")
FONT_TITLE    = (_UI_FAMILY, 13, "bold")
FONT_SUBTITLE = (_UI_FAMILY, 9)
FONT_MONO     = ("Consolas", 9)
FONT_EMOJI    = (_EMOJI_FAMILY, 9)


# ---------------------------------------------------------------------------
# Domain / backend  (unchanged from original)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstallTarget:
    selected_path: Path
    gta_root: Path
    gtaiv_dir: Path
    retail_dir: Path
    retail_exe: Path


class InstallError(RuntimeError):
    pass


def is_windows() -> bool:
    return os.name == "nt"


def normalize_path(raw: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
    return Path(expanded).resolve()


def looks_like_gta_root(path: Path) -> bool:
    return (path / "GTAIV").is_dir()


def looks_like_gtaiv_dir(path: Path) -> bool:
    return path.name.lower() == "gtaiv" or (path / "GTAIV.exe").exists()


def resolve_install_target(selected: Path) -> InstallTarget:
    if not selected.exists():
        raise InstallError(f"Selected path does not exist:\n{selected}")

    selected = selected.resolve()

    if looks_like_gta_root(selected):
        gta_root = selected
        gtaiv_dir = selected / "GTAIV"
    elif looks_like_gtaiv_dir(selected):
        gtaiv_dir = selected
        gta_root = selected.parent
    else:
        raise InstallError(
            "Selected folder does not look like a GTA IV Complete Edition install.\n\n"
            "Select either:\n"
            r"  ...\Grand Theft Auto IV"
            "\n\nor:\n"
            r"  ...\Grand Theft Auto IV\GTAIV"
        )

    if not gtaiv_dir.is_dir():
        raise InstallError(f"Could not find GTAIV subfolder:\n{gtaiv_dir}")

    retail_dir = gtaiv_dir / "Retail"
    retail_exe = retail_dir / "GTAIV.exe"

    return InstallTarget(
        selected_path=selected,
        gta_root=gta_root,
        gtaiv_dir=gtaiv_dir,
        retail_dir=retail_dir,
        retail_exe=retail_exe,
    )


def reg_read_value(root, subkey: str, value_name: str, wow64_flag: int = 0) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | wow64_flag) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    except OSError:
        return None
    return None


def write_gtac_registry(exe_path: Path) -> None:
    if winreg is None:
        return
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, GTAC_REG_KEY, 0, winreg.KEY_WRITE,
        ) as key:
            winreg.SetValueEx(key, GTAC_REG_VALUE, 0, winreg.REG_SZ, str(exe_path))
    except OSError as exc:
        raise InstallError(
            f"Failed to write GTA Connected registry key:\n{exc}\n\n"
            "You may need to set the Game EXE Path manually in GTA Connected."
        ) from exc


def find_steam_paths_from_registry() -> list[Path]:
    if winreg is None:
        return []
    results: list[Path] = []
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamExe"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ]
    wow_flags = [0]
    if hasattr(winreg, "KEY_WOW64_32KEY"):
        wow_flags.append(winreg.KEY_WOW64_32KEY)
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        wow_flags.append(winreg.KEY_WOW64_64KEY)

    for root, subkey, value_name in candidates:
        for flag in wow_flags:
            value = reg_read_value(root, subkey, value_name, flag)
            if not value:
                continue
            path = Path(value)
            if value_name.lower() == "steamexe":
                path = path.parent
            if path.exists():
                results.append(path.resolve())

    seen: set[str] = set()
    deduped: list[Path] = []
    for p in results:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            deduped.append(p)
    return deduped


def find_gta_install_from_registry() -> Path | None:
    if winreg is None:
        return None
    uninstall_keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 12210",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 12210",
    ]
    wow_flags = [0]
    if hasattr(winreg, "KEY_WOW64_32KEY"):
        wow_flags.append(winreg.KEY_WOW64_32KEY)
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        wow_flags.append(winreg.KEY_WOW64_64KEY)

    for subkey in uninstall_keys:
        for flag in wow_flags:
            value = reg_read_value(winreg.HKEY_LOCAL_MACHINE, subkey, "InstallLocation", flag)
            if not value:
                continue
            path = Path(value)
            if path.exists():
                return path.resolve()
    return None


def parse_steam_library_paths(steam_path: Path) -> list[Path]:
    paths = [steam_path]
    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return paths
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return paths

    for m in re.finditer(r'"path"\s+"([^"]+)"', text, re.IGNORECASE):
        raw = m.group(1).replace(r"\\", "\\")
        p = Path(raw)
        if p.exists():
            paths.append(p.resolve())

    for m in re.finditer(r'"\d+"\s+"([A-Za-z]:\\\\[^"]+)"', text):
        raw = m.group(1).replace(r"\\", "\\")
        p = Path(raw)
        if p.exists():
            paths.append(p.resolve())

    seen: set[str] = set()
    deduped: list[Path] = []
    for p in paths:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            deduped.append(p)
    return deduped


def parse_installdir_from_manifest(manifest_path: Path) -> str | None:
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
    return m.group(1) if m else None


def find_gta_install_from_steam_libraries() -> Path | None:
    steam_candidates = find_steam_paths_from_registry()
    if DEFAULT_STEAM_PATH.exists():
        steam_candidates.append(DEFAULT_STEAM_PATH.resolve())

    seen_steam: set[str] = set()
    steam_paths: list[Path] = []
    for p in steam_candidates:
        k = str(p).lower()
        if k not in seen_steam:
            seen_steam.add(k)
            steam_paths.append(p)

    for steam in steam_paths:
        for library in parse_steam_library_paths(steam):
            steamapps = library / "steamapps"
            manifest = steamapps / f"appmanifest_{APP_ID}.acf"
            if manifest.exists():
                install_dir = parse_installdir_from_manifest(manifest) or APP_NAME
                candidate = steamapps / "common" / install_dir
                if candidate.exists():
                    return candidate.resolve()
            fallback = steamapps / "common" / APP_NAME
            if fallback.exists():
                return fallback.resolve()

    if DEFAULT_GTA_ROOT.exists():
        return DEFAULT_GTA_ROOT.resolve()
    return None


def find_gta_install() -> Path | None:
    return find_gta_install_from_registry() or find_gta_install_from_steam_libraries()


def ensure_zip_has_retail(zip_path: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [info.filename.replace("\\", "/") for info in zf.infolist()]
    except zipfile.BadZipFile as exc:
        raise InstallError(f"Downloaded file is not a valid ZIP archive:\n{zip_path}") from exc

    has_retail_folder = any(name.rstrip("/").split("/")[0].lower() == "retail" for name in names)
    has_gtaiv_exe = any(
        name.lower().endswith("/gtaiv.exe") and name.lower().startswith("retail/")
        for name in names
    )
    if not has_retail_folder:
        raise InstallError("Patch ZIP does not contain a top-level Retail folder.")
    if not has_gtaiv_exe:
        raise InstallError("Patch ZIP does not contain Retail/GTAIV.exe.")


def safe_extract_retail(zip_path: Path, target_gtaiv_dir: Path) -> None:
    target_gtaiv_dir = target_gtaiv_dir.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            zip_name = info.filename.replace("\\", "/")
            parts = [p for p in zip_name.split("/") if p not in ("", ".")]
            if not parts:
                continue
            if parts[0].lower() != "retail":
                continue
            if any(part == ".." for part in parts):
                raise InstallError(f"Unsafe ZIP entry rejected:\n{info.filename}")
            destination = (target_gtaiv_dir / Path(*parts)).resolve()
            try:
                destination.relative_to(target_gtaiv_dir)
            except ValueError as exc:
                raise InstallError(f"Unsafe ZIP entry rejected:\n{info.filename}") from exc
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst)


def backup_existing_retail(retail_dir: Path) -> Path:
    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = retail_dir.with_name(f"Retail.backup-{timestamp}")
    suffix = 1
    while backup.exists():
        backup = retail_dir.with_name(f"Retail.backup-{timestamp}-{suffix}")
        suffix += 1
    shutil.move(str(retail_dir), str(backup))
    return backup


def download_file(url: str, dest: Path, progress_cb) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "GTAIV-GTAConnected-Patcher/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            downloaded = 0
            chunk_size = 1024 * 128
            with open(dest, "wb") as fh:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    progress_cb(downloaded, total)
    except urllib.error.URLError as exc:
        raise InstallError(f"Download failed:\n{exc}") from exc
    except TimeoutError as exc:
        raise InstallError("Download timed out.") from exc


def open_folder(path: Path) -> None:
    if is_windows():
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        os.system(f'open "{path}"')
    else:
        os.system(f'xdg-open "{path}"')


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _apply_emoji_font(widget, size: int = 9) -> None:
    """Best-effort: configure a widget's font to include emoji fallback."""
    try:
        widget.configure(font=(_EMOJI_FAMILY, size))
    except Exception:
        try:
            widget.configure(font=(_UI_FAMILY, size))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SetupFrame  (Screen 1)
# ---------------------------------------------------------------------------

class SetupFrame(ttk.Frame):
    """
    Path selection, version picker, backup option, and the Install button.
    Advanced options (registry write) are hidden under a disclosure toggle.
    """

    def __init__(self, master, app: "App") -> None:
        super().__init__(master)
        self._app = app
        self._adv_visible = False
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        # ── Header ──────────────────────────────────────────────────────────
        hdr = ttk.Frame(self)
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 14))
        hdr.columnconfigure(0, weight=1)

        ttk.Label(hdr, text="GTA IV Patch Installer", font=FONT_TITLE).grid(
            row=0, column=0, sticky=W
        )
        ttk.Label(
            hdr,
            text="For GTA Connected  \u2014  installs the Retail patch into your GTAIV folder",
            font=FONT_SUBTITLE,
            foreground="#666666",
        ).grid(row=1, column=0, sticky=W, pady=(2, 0))

        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, sticky="ew", padx=20, pady=(0, 16)
        )

        # ── Installation path ────────────────────────────────────────────────
        sec1 = ttk.LabelFrame(self, text="GTA IV Installation Folder", padding=(12, 8))
        sec1.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))
        sec1.columnconfigure(0, weight=1)

        path_row = ttk.Frame(sec1)
        path_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        path_row.columnconfigure(0, weight=1)

        self._path_entry = ttk.Entry(path_row, textvariable=self._app.path_var, font=FONT_MONO)
        self._path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ttk.Button(path_row, text="Browse\u2026", command=self._browse, width=9).grid(
            row=0, column=1, padx=(0, 4)
        )
        ttk.Button(path_row, text="Auto-detect", command=self._autodetect, width=11).grid(
            row=0, column=2
        )

        self._path_hint = ttk.Label(
            sec1, text="", font=("Segoe UI", 8), foreground="#888888"
        )
        self._path_hint.grid(row=1, column=0, sticky=W)

        # ── Patch version ────────────────────────────────────────────────────
        sec2 = ttk.LabelFrame(self, text="Patch Version", padding=(12, 8))
        sec2.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 12))
        sec2.columnconfigure(1, weight=1)

        ttk.Label(sec2, text="Version:", font=FONT_UI_BOLD).grid(
            row=0, column=0, sticky=W, padx=(0, 10)
        )
        self._version_combo = ttk.Combobox(
            sec2,
            textvariable=self._app.version_var,
            values=list(PATCHES.keys()),
            state="readonly",
            width=28,
            font=FONT_UI,
        )
        self._version_combo.grid(row=0, column=1, sticky=W)

        # ── Options ──────────────────────────────────────────────────────────
        sec3 = ttk.LabelFrame(self, text="Options", padding=(12, 8))
        sec3.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 4))
        sec3.columnconfigure(0, weight=1)

        self._backup_cb = ttk.Checkbutton(
            sec3,
            variable=self._app.backup_var,
            text="Back up existing Retail folder before replacing",
        )
        self._backup_cb.grid(row=0, column=0, sticky=W)

        # Advanced toggle link
        self._adv_toggle = ttk.Label(
            sec3,
            text="\u25b6  Advanced options",
            font=("Segoe UI", 8),
            foreground="#0066cc",
            cursor="hand2",
        )
        self._adv_toggle.grid(row=1, column=0, sticky=W, pady=(6, 0))
        self._adv_toggle.bind("<Button-1>", self._toggle_advanced)

        # Advanced panel (hidden by default)
        self._adv_frame = ttk.Frame(sec3)
        # not gridded until toggled

        self._reg_cb = ttk.Checkbutton(
            self._adv_frame,
            variable=self._app.registry_var,
            text="Automatically write Game EXE Path to GTA Connected registry",
        )
        self._reg_cb.grid(row=0, column=0, sticky=W, padx=(16, 0))

        # ── Install button ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(16, 20))
        btn_frame.columnconfigure(0, weight=1)

        self._install_btn = ttk.Button(
            btn_frame,
            text="Download & Install Patch",
            command=self._app.start_install,
            width=28,
        )
        self._install_btn.grid(row=0, column=0, sticky=W)

        self._err_label = ttk.Label(
            btn_frame, text="", foreground="#cc0000", font=FONT_UI, wraplength=460
        )
        self._err_label.grid(row=1, column=0, sticky=W, pady=(6, 0))

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _browse(self) -> None:
        sel = filedialog.askdirectory(title="Select GTA IV installation folder")
        if sel:
            self._app.path_var.set(sel)
            self._update_hint()

    def _autodetect(self) -> None:
        found = find_gta_install()
        if found:
            self._app.path_var.set(str(found))
            self._update_hint(f"Auto-detected: {found}")
        else:
            self._update_hint("Could not auto-detect. Please browse manually.", error=True)

    def _update_hint(self, msg: str = "", error: bool = False) -> None:
        self._path_hint.configure(
            text=msg,
            foreground="#cc0000" if error else "#888888",
        )

    def _toggle_advanced(self, _event=None) -> None:
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._adv_frame.grid(row=2, column=0, sticky=W, pady=(4, 0))
            self._adv_toggle.configure(text="\u25bc  Advanced options")
        else:
            self._adv_frame.grid_remove()
            self._adv_toggle.configure(text="\u25b6  Advanced options")

    # ── Public API ───────────────────────────────────────────────────────────

    def show_error(self, msg: str) -> None:
        self._err_label.configure(text=msg)

    def clear_error(self) -> None:
        self._err_label.configure(text="")

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self._install_btn.configure(state=state)
        self._path_entry.configure(state=state)
        self._version_combo.configure(state="disabled" if busy else "readonly")

    def populate_autodetect(self) -> None:
        """Called on startup to pre-fill the path field."""
        found = find_gta_install()
        if found:
            self._app.path_var.set(str(found))
            self._update_hint(f"Auto-detected: {found}")
        else:
            if not self._app.path_var.get():
                self._app.path_var.set(str(DEFAULT_GTA_ROOT))
            self._update_hint("Could not auto-detect. Please verify or browse.", error=True)


# ---------------------------------------------------------------------------
# ProgressFrame  (Screen 2)
# ---------------------------------------------------------------------------

class ProgressFrame(ttk.Frame):
    """
    Shown once installation starts. Owns:
    - Two-stage progress (download with %, then install indeterminate).
    - Inline success / error result panels.
    - Collapsible detail log.
    - Reset / Open Folder / Copy Path actions.
    """

    _LOG_HEIGHT_COLLAPSED = 0
    _LOG_HEIGHT_EXPANDED  = 160

    def __init__(self, master, app: "App") -> None:
        super().__init__(master)
        self._app = app
        self._log_visible = False
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        # ── Header ──────────────────────────────────────────────────────────
        hdr = ttk.Frame(self)
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 14))
        hdr.columnconfigure(0, weight=1)

        ttk.Label(hdr, text="Installing Patch", font=FONT_TITLE).grid(
            row=0, column=0, sticky=W
        )
        self._subtitle = ttk.Label(
            hdr, text="", font=FONT_SUBTITLE, foreground="#666666"
        )
        self._subtitle.grid(row=1, column=0, sticky=W, pady=(2, 0))

        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, sticky="ew", padx=20, pady=(0, 16)
        )

        # ── Stage indicators ─────────────────────────────────────────────────
        stages = ttk.Frame(self)
        stages.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        stages.columnconfigure(0, weight=1)

        # Download stage
        dl_row = ttk.Frame(stages)
        dl_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        dl_row.columnconfigure(1, weight=1)

        self._dl_icon = ttk.Label(dl_row, text="\u23f3", font=FONT_EMOJI, width=2)
        self._dl_icon.grid(row=0, column=0, sticky=W, padx=(0, 6))
        ttk.Label(dl_row, text="Download", font=FONT_UI_BOLD, width=10).grid(
            row=0, column=1, sticky=W
        )
        self._dl_bar = ttk.Progressbar(dl_row, mode="determinate", length=300)
        self._dl_bar.grid(row=0, column=2, sticky="ew", padx=(8, 8))
        dl_row.columnconfigure(2, weight=1)
        self._dl_pct = ttk.Label(dl_row, text="", font=FONT_UI, width=12)
        self._dl_pct.grid(row=0, column=3, sticky=W)

        # Install stage
        inst_row = ttk.Frame(stages)
        inst_row.grid(row=1, column=0, sticky="ew")
        inst_row.columnconfigure(1, weight=1)

        self._inst_icon = ttk.Label(inst_row, text="\u23f3", font=FONT_EMOJI, width=2)
        self._inst_icon.grid(row=0, column=0, sticky=W, padx=(0, 6))
        ttk.Label(inst_row, text="Install", font=FONT_UI_BOLD, width=10).grid(
            row=0, column=1, sticky=W
        )
        self._inst_bar = ttk.Progressbar(inst_row, mode="determinate", length=300)
        self._inst_bar.grid(row=0, column=2, sticky="ew", padx=(8, 8))
        inst_row.columnconfigure(2, weight=1)
        self._inst_status = ttk.Label(inst_row, text="Waiting\u2026", font=FONT_UI, width=12)
        self._inst_status.grid(row=0, column=3, sticky=W)

        # ── Result panel (hidden until done/error) ───────────────────────────
        self._result_frame = ttk.Frame(self)
        # gridded on demand at row 3

        self._result_icon = ttk.Label(self._result_frame, text="", font=(_EMOJI_FAMILY, 20))
        self._result_icon.grid(row=0, column=0, rowspan=2, sticky=W, padx=(0, 12))

        self._result_title = ttk.Label(self._result_frame, text="", font=FONT_UI_BOLD)
        self._result_title.grid(row=0, column=1, sticky=W)

        self._result_detail = ttk.Label(
            self._result_frame, text="", font=FONT_UI, foreground="#444444", wraplength=380
        )
        self._result_detail.grid(row=1, column=1, sticky=W, pady=(2, 0))

        # EXE path display (success only)
        self._exe_frame = ttk.Frame(self)
        # gridded on demand at row 4

        ttk.Label(self._exe_frame, text="Game EXE Path", font=FONT_UI_BOLD).grid(
            row=0, column=0, sticky=W, pady=(0, 4)
        )
        self._exe_entry = ttk.Entry(
            self._exe_frame,
            textvariable=self._app.exe_path_var,
            state="readonly",
            font=FONT_MONO,
        )
        self._exe_entry.grid(row=1, column=0, sticky="ew")
        self._exe_frame.columnconfigure(0, weight=1)

        # ── Action buttons ───────────────────────────────────────────────────
        self._action_frame = ttk.Frame(self)
        # gridded on demand at row 5

        self._copy_btn = ttk.Button(
            self._action_frame,
            text="Copy Path to Clipboard",
            command=self._copy_path,
            width=22,
        )
        self._copy_btn.grid(row=0, column=0, padx=(0, 6))

        self._open_btn = ttk.Button(
            self._action_frame,
            text="Open Retail Folder",
            command=self._open_folder,
            width=18,
        )
        self._open_btn.grid(row=0, column=1, padx=(0, 6))

        self._reset_btn = ttk.Button(
            self._action_frame,
            text="Start Over",
            command=self._app.go_setup,
            width=10,
        )
        self._reset_btn.grid(row=0, column=2)

        # ── Log toggle + log ─────────────────────────────────────────────────
        log_toggle_frame = ttk.Frame(self)
        log_toggle_frame.grid(row=6, column=0, sticky="ew", padx=20, pady=(14, 0))

        self._log_toggle = ttk.Label(
            log_toggle_frame,
            text="\u25b6  Show details",
            font=("Segoe UI", 8),
            foreground="#0066cc",
            cursor="hand2",
        )
        self._log_toggle.pack(side=LEFT)
        self._log_toggle.bind("<Button-1>", self._toggle_log)

        self._log_frame = ttk.Frame(self)
        # gridded on demand at row 7

        from tkinter import Text, Scrollbar
        self._log_text = Text(
            self._log_frame,
            height=8,
            state="disabled",
            font=FONT_MONO,
            wrap="none",
            relief="flat",
            borderwidth=1,
        )
        self._log_text.pack(side=LEFT, fill=BOTH, expand=True, padx=(20, 0), pady=(6, 0))

        sb = Scrollbar(self._log_frame, orient="vertical", command=self._log_text.yview)
        sb.pack(side=RIGHT, fill=Y, padx=(0, 20), pady=(6, 0))
        self._log_text.configure(yscrollcommand=sb.set)

        # Tag colours
        self._log_text.tag_configure("ok",   foreground="#007700")
        self._log_text.tag_configure("err",  foreground="#cc0000")
        self._log_text.tag_configure("info", foreground="#444444")

    # ── Log helpers ──────────────────────────────────────────────────────────

    def append_log(self, msg: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        tag = "err" if msg.startswith("\u274c") else "ok" if msg.startswith("\u2713") else "info"
        self._log_text.configure(state="normal")
        self._log_text.insert(END, f"[{ts}] {msg}\n", tag)
        self._log_text.see(END)
        self._log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", END)
        self._log_text.configure(state="disabled")

    def _toggle_log(self, _event=None) -> None:
        self._log_visible = not self._log_visible
        if self._log_visible:
            self._log_frame.grid(row=7, column=0, sticky="ew", pady=(0, 16))
            self._log_toggle.configure(text="\u25bc  Hide details")
        else:
            self._log_frame.grid_remove()
            self._log_toggle.configure(text="\u25b6  Show details")

    # ── Stage control ────────────────────────────────────────────────────────

    def reset_stages(self, subtitle: str) -> None:
        """Called when install begins."""
        self._subtitle.configure(text=subtitle)
        self._result_frame.grid_remove()
        self._exe_frame.grid_remove()
        self._action_frame.grid_remove()

        self._dl_icon.configure(text="\u23f3")
        self._dl_bar.configure(mode="determinate", value=0, maximum=100)
        self._dl_pct.configure(text="")

        self._inst_icon.configure(text="\u23f3")
        self._inst_bar.configure(mode="determinate", value=0)
        self._inst_status.configure(text="Waiting\u2026")

        self.clear_log()

        if self._log_visible:
            self._toggle_log()

    def set_download_progress(self, downloaded: int, total: int | None) -> None:
        self._dl_icon.configure(text="\u23f3")
        if total:
            pct = int(downloaded * 100 / total)
            mb_d = downloaded / 1_048_576
            mb_t = total / 1_048_576
            self._dl_bar.configure(mode="determinate", maximum=total, value=downloaded)
            self._dl_pct.configure(text=f"{pct}%  ({mb_d:.1f}/{mb_t:.1f} MB)")
        else:
            self._dl_bar.configure(mode="indeterminate")
            self._dl_bar.start(10)
            self._dl_pct.configure(text=f"{downloaded // 1024} KB")

    def set_download_done(self) -> None:
        self._dl_bar.stop()
        self._dl_bar.configure(mode="determinate", maximum=100, value=100)
        self._dl_icon.configure(text="\u2705")
        self._dl_pct.configure(text="Done")

    def set_install_active(self) -> None:
        self._inst_icon.configure(text="\u23f3")
        self._inst_bar.configure(mode="indeterminate")
        self._inst_bar.start(10)
        self._inst_status.configure(text="Installing\u2026")

    def set_install_done(self) -> None:
        self._inst_bar.stop()
        self._inst_bar.configure(mode="determinate", maximum=100, value=100)
        self._inst_icon.configure(text="\u2705")
        self._inst_status.configure(text="Done")

    def show_success(self, exe_path: Path, reg_written: bool) -> None:
        self._result_icon.configure(text="\U0001f389")
        self._result_title.configure(text="Patch installed successfully!", foreground="#007700")
        reg_note = (
            "Game EXE Path has been written to the GTA Connected registry automatically."
            if reg_written
            else "Set the Game EXE Path manually in GTA Connected \u2192 Tools \u2192 Game Settings."
        )
        self._result_detail.configure(text=reg_note)
        self._result_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=(14, 0))

        self._exe_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=(12, 0))
        self._action_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(10, 0))

        self._copy_btn.configure(state="normal")
        self._open_btn.configure(state="normal")

    def show_error(self, msg: str) -> None:
        self._result_icon.configure(text="\u274c")
        self._result_title.configure(text="Installation failed", foreground="#cc0000")
        self._result_detail.configure(text=msg)
        self._result_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=(14, 0))

        self._action_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(10, 0))
        self._copy_btn.configure(state="disabled")
        self._open_btn.configure(state="disabled")

        # Auto-expand log on error so the user sees detail immediately.
        if not self._log_visible:
            self._toggle_log()

    # ── Actions ──────────────────────────────────────────────────────────────

    def _copy_path(self) -> None:
        path = self._app.exe_path_var.get().strip()
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self.update_idletasks()
        self._copy_btn.configure(text="Copied!")
        self.after(2000, lambda: self._copy_btn.configure(text="Copy Path to Clipboard"))

    def _open_folder(self) -> None:
        try:
            target = resolve_install_target(normalize_path(self._app.path_var.get()))
            if target.retail_dir.exists():
                open_folder(target.retail_dir)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# App  (coordinator / root window)
# ---------------------------------------------------------------------------

class App(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GTA IV Patch Installer for GTA Connected")
        self.resizable(False, False)

        style = ttk.Style()
        style.theme_use("clam")

        # Shared state vars
        self.path_var    = StringVar()
        self.version_var = StringVar(value=list(PATCHES.keys())[0])
        self.backup_var  = BooleanVar(value=True)
        self.registry_var = BooleanVar(value=True)
        self.exe_path_var = StringVar()

        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_progress_emit = 0.0

        # Build both frames; only one visible at a time
        self._setup_frame    = SetupFrame(self, self)
        self._progress_frame = ProgressFrame(self, self)

        self._active_frame: ttk.Frame = self._setup_frame
        self._setup_frame.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.after(100, self._process_queue)
        self.after(0, self._startup)

    # ── Frame switching ──────────────────────────────────────────────────────

    def _show_frame(self, frame: ttk.Frame) -> None:
        self._active_frame.grid_remove()
        self._active_frame = frame
        frame.grid(row=0, column=0, sticky="nsew")
        self.update_idletasks()

    def go_setup(self) -> None:
        self._show_frame(self._setup_frame)

    def go_progress(self) -> None:
        self._show_frame(self._progress_frame)

    # ── Startup ──────────────────────────────────────────────────────────────

    def _startup(self) -> None:
        self._setup_frame.populate_autodetect()

    # ── Queue processing ─────────────────────────────────────────────────────

    def _process_queue(self) -> None:
        pf = self._progress_frame
        try:
            while True:
                kind, payload = self._queue.get_nowait()

                if kind == "log":
                    pf.append_log(str(payload))

                elif kind == "stage":
                    if payload == "install":
                        pf.set_download_done()
                        pf.set_install_active()

                elif kind == "dl_progress":
                    downloaded, total = payload  # type: ignore[misc]
                    pf.set_download_progress(downloaded, total)

                elif kind == "done":
                    target, reg_written = payload  # type: ignore[misc]
                    self.exe_path_var.set(str(target.retail_exe))
                    pf.set_download_done()
                    pf.set_install_done()
                    pf.show_success(target.retail_exe, reg_written)
                    # Auto-copy to clipboard
                    self.clipboard_clear()
                    self.clipboard_append(str(target.retail_exe))
                    self.update_idletasks()

                elif kind == "error":
                    pf.set_install_done()   # stop spinners
                    pf.show_error(str(payload))

        except queue.Empty:
            pass

        self.after(100, self._process_queue)

    # ── Installation ─────────────────────────────────────────────────────────

    def start_install(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        self._setup_frame.clear_error()

        try:
            selected = normalize_path(self.path_var.get())
            target   = resolve_install_target(selected)
        except InstallError as exc:
            self._setup_frame.show_error(str(exc))
            return

        version = self.version_var.get()
        url = PATCHES.get(version)
        if not url:
            self._setup_frame.show_error("Please select a patch version.")
            return

        self.exe_path_var.set(str(target.retail_exe))

        subtitle = f"Installing {version}  \u2192  {target.gtaiv_dir}"
        self._progress_frame.reset_stages(subtitle)
        self.go_progress()

        self._worker = threading.Thread(
            target=self._install_worker,
            args=(target, version, url, self.backup_var.get(), self.registry_var.get()),
            daemon=True,
        )
        self._worker.start()

    def _install_worker(
        self,
        target: InstallTarget,
        version: str,
        url: str,
        backup_existing: bool,
        write_registry: bool,
    ) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gtaiv-gtac-patcher-"))
        zip_path = temp_dir / "patch.zip"

        try:
            self._queue.put(("log", f"Downloading {version} from wiki.gtaconnected.com\u2026"))

            def on_progress(downloaded: int, total: int | None) -> None:
                now = time.monotonic()
                if now - self._last_progress_emit < 0.05:
                    return
                self._last_progress_emit = now
                self._queue.put(("dl_progress", (downloaded, total)))

            download_file(url, zip_path, on_progress)
            self._queue.put(("log", "\u2713 Download complete"))

            self._queue.put(("log", "Validating patch archive\u2026"))
            ensure_zip_has_retail(zip_path)
            self._queue.put(("log", "\u2713 Archive validated: Retail/GTAIV.exe present"))

            # Switch UI to install stage
            self._queue.put(("stage", "install"))

            if target.retail_dir.exists():
                if not backup_existing:
                    raise InstallError(
                        "Retail folder already exists.\n"
                        "Enable 'Back up existing Retail folder' to replace it."
                    )
                self._queue.put(("log", "Backing up existing Retail folder\u2026"))
                backup_path = backup_existing_retail(target.retail_dir)
                self._queue.put(("log", f"\u2713 Backed up to: {backup_path}"))

            self._queue.put(("log", "Extracting patch files\u2026"))
            safe_extract_retail(zip_path, target.gtaiv_dir)
            self._queue.put(("log", "\u2713 Retail folder extracted"))

            if not target.retail_exe.exists():
                raise InstallError(
                    f"Extraction completed but GTAIV.exe not found.\nExpected: {target.retail_exe}"
                )

            if write_registry:
                self._queue.put(("log", "Writing GTA Connected registry key\u2026"))
                write_gtac_registry(target.retail_exe)
                self._queue.put(("log", f"\u2713 Registry: {GTAC_REG_VALUE} written"))

            self._queue.put(("log", "\u2713 Patch installed successfully"))
            self._queue.put(("done", (target, write_registry)))

        except Exception as exc:
            self._queue.put(("log", f"\u274c {exc}"))
            self._queue.put(("error", str(exc)))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def enable_windows_dpi_awareness() -> None:
    if not is_windows():
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def main() -> int:
    enable_windows_dpi_awareness()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())