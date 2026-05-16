#!/usr/bin/env python3
"""
GTA IV Retail Patch Installer for GTA Connected

Windows GUI utility that:
- Detects or lets you select a GTA IV: Complete Edition installation.
- Downloads Retail-1080.zip or Retail-1070.zip.
- Extracts the Retail folder into:
    ...\Grand Theft Auto IV\GTAIV\Retail
- Validates:
    ...\Grand Theft Auto IV\GTAIV\Retail\GTAIV.exe
- Copies that EXE path for GTA Connected -> Tools -> Game Settings.

No third-party Python packages required.
Build EXE:
    py -m pip install pyinstaller
    py -m PyInstaller --onefile --windowed --name GTAIV-GTAConnected-Patcher gtaiv_gtac_patcher_gui.py
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
from tkinter import BOTH, END, LEFT, RIGHT, TOP, W, X, BooleanVar, StringVar, Tk
from tkinter import filedialog, messagebox
from tkinter import ttk

try:
    import winreg  # type: ignore
except ImportError:  # Non-Windows dev/test environment.
    winreg = None  # type: ignore


APP_ID = "12210"
APP_NAME = "Grand Theft Auto IV"
EXPECTED_RETAIL_EXE = Path("Retail") / "GTAIV.exe"

PATCHES = {
    "1.0.8.0 recommended": "https://wiki.gtaconnected.com/downloads/Retail-1080.zip",
    "1.0.7.0": "https://wiki.gtaconnected.com/downloads/Retail-1070.zip",
}

DEFAULT_STEAM_PATH = Path(r"C:\Program Files (x86)\Steam")
DEFAULT_GTA_ROOT = DEFAULT_STEAM_PATH / "steamapps" / "common" / "Grand Theft Auto IV"


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
    # Complete Edition commonly has GTAIV.exe in this folder.
    return path.name.lower() == "gtaiv" or (path / "GTAIV.exe").exists()


def resolve_install_target(selected: Path) -> InstallTarget:
    """
    Accept either:
      ...\\Grand Theft Auto IV
    or:
      ...\\Grand Theft Auto IV\\GTAIV

    Always returns the correct patch destination:
      ...\\Grand Theft Auto IV\\GTAIV\\Retail
    """
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

    # Keep order while deduping.
    seen: set[str] = set()
    deduped: list[Path] = []
    for p in results:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
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
    """
    Best-effort parser for Steam's libraryfolders.vdf.

    Supports common modern form:
      "0" { "path" "C:\\Program Files (x86)\\Steam" ... }

    Supports older form:
      "1" "D:\\SteamLibrary"
    """
    paths = [steam_path]
    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return paths

    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return paths

    # Modern lines: "path" "D:\\SteamLibrary"
    for m in re.finditer(r'"path"\s+"([^"]+)"', text, re.IGNORECASE):
        raw = m.group(1).replace(r"\\", "\\")
        p = Path(raw)
        if p.exists():
            paths.append(p.resolve())

    # Older lines: "1" "D:\\SteamLibrary"
    for m in re.finditer(r'"\d+"\s+"([A-Za-z]:\\\\[^"]+)"', text):
        raw = m.group(1).replace(r"\\", "\\")
        p = Path(raw)
        if p.exists():
            paths.append(p.resolve())

    seen: set[str] = set()
    deduped: list[Path] = []
    for p in paths:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped


def parse_installdir_from_manifest(manifest_path: Path) -> str | None:
    try:
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    m = re.search(r'"installdir"\s+"([^"]+)"', text, re.IGNORECASE)
    if not m:
        return None
    return m.group(1)


def find_gta_install_from_steam_libraries() -> Path | None:
    steam_candidates = find_steam_paths_from_registry()
    if DEFAULT_STEAM_PATH.exists():
        steam_candidates.append(DEFAULT_STEAM_PATH.resolve())

    seen_steam: set[str] = set()
    steam_paths: list[Path] = []
    for p in steam_candidates:
        key = str(p).lower()
        if key not in seen_steam:
            seen_steam.add(key)
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
    has_gtaiv_exe = any(name.lower().endswith("/gtaiv.exe") and name.lower().startswith("retail/") for name in names)

    if not has_retail_folder:
        raise InstallError("Patch ZIP does not contain a top-level Retail folder.")
    if not has_gtaiv_exe:
        raise InstallError("Patch ZIP does not contain Retail/GTAIV.exe.")


def safe_extract_retail(zip_path: Path, target_gtaiv_dir: Path) -> None:
    """
    Extract only the top-level Retail folder from the ZIP.
    Prevents path traversal and avoids extracting unrelated top-level files.
    """
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
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GTAIV-GTAConnected-Patcher/1.0",
        },
    )

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


class App(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GTA IV Patch Installer for GTA Connected")
        self.geometry("760x560")
        self.minsize(720, 520)

        self.install_path_var = StringVar()
        self.version_var = StringVar(value="1.0.8.0 recommended")
        self.status_var = StringVar(value="Ready.")
        self.exe_path_var = StringVar(value="")
        self.backup_existing_var = BooleanVar(value=True)

        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_progress_time = 0.0

        self._build_ui()
        self.after(100, self._process_queue)
        self._autodetect_install()

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}

        header = ttk.Frame(self)
        header.pack(side=TOP, fill=X, padx=12, pady=(12, 6))

        title = ttk.Label(
            header,
            text="GTA IV Retail Patch Installer",
            font=("Segoe UI", 15, "bold"),
        )
        title.pack(anchor=W)

        subtitle = ttk.Label(
            header,
            text=r"Installs Retail into ...\Grand Theft Auto IV\GTAIV\Retail for GTA Connected.",
        )
        subtitle.pack(anchor=W, pady=(2, 0))

        main = ttk.Frame(self)
        main.pack(side=TOP, fill=BOTH, expand=True, padx=12, pady=6)

        path_box = ttk.LabelFrame(main, text="GTA IV installation")
        path_box.pack(fill=X, pady=(0, 10))

        row = ttk.Frame(path_box)
        row.pack(fill=X, **pad)

        self.path_entry = ttk.Entry(row, textvariable=self.install_path_var)
        self.path_entry.pack(side=LEFT, fill=X, expand=True)

        ttk.Button(row, text="Browse...", command=self._browse).pack(side=LEFT, padx=(8, 0))
        ttk.Button(row, text="Auto-detect", command=self._autodetect_install).pack(side=LEFT, padx=(8, 0))

        hint = ttk.Label(
            path_box,
            text=r"Select either the parent Grand Theft Auto IV folder or its GTAIV subfolder. Retail will always be installed inside GTAIV.",
        )
        hint.pack(anchor=W, padx=12, pady=(0, 8))

        patch_box = ttk.LabelFrame(main, text="Patch")
        patch_box.pack(fill=X, pady=(0, 10))

        row2 = ttk.Frame(patch_box)
        row2.pack(fill=X, **pad)

        ttk.Label(row2, text="Version:").pack(side=LEFT)
        self.version_combo = ttk.Combobox(
            row2,
            textvariable=self.version_var,
            values=list(PATCHES.keys()),
            state="readonly",
            width=24,
        )
        self.version_combo.pack(side=LEFT, padx=(8, 16))

        ttk.Checkbutton(
            row2,
            variable=self.backup_existing_var,
            text="Back up existing Retail folder before replacing it",
        ).pack(side=LEFT)

        install_box = ttk.LabelFrame(main, text="Install")
        install_box.pack(fill=X, pady=(0, 10))

        row3 = ttk.Frame(install_box)
        row3.pack(fill=X, **pad)

        self.install_button = ttk.Button(row3, text="Download and Install Patch", command=self._start_install)
        self.install_button.pack(side=LEFT)

        self.open_button = ttk.Button(row3, text="Open Retail Folder", command=self._open_retail_folder, state="disabled")
        self.open_button.pack(side=LEFT, padx=(8, 0))

        self.copy_button = ttk.Button(row3, text="Copy EXE Path", command=self._copy_exe_path, state="disabled")
        self.copy_button.pack(side=LEFT, padx=(8, 0))

        self.progress = ttk.Progressbar(install_box, mode="determinate")
        self.progress.pack(fill=X, padx=12, pady=(0, 8))

        result_box = ttk.LabelFrame(main, text="GTA Connected Game EXE Path")
        result_box.pack(fill=X, pady=(0, 10))

        self.exe_entry = ttk.Entry(result_box, textvariable=self.exe_path_var, state="readonly")
        self.exe_entry.pack(fill=X, **pad)

        log_box = ttk.LabelFrame(main, text="Log")
        log_box.pack(fill=BOTH, expand=True)

        self.log = ttk.Treeview(log_box, columns=("message",), show="headings", height=10)
        self.log.heading("message", text="Message")
        self.log.column("message", width=700, anchor=W)
        self.log.pack(side=LEFT, fill=BOTH, expand=True, padx=(12, 0), pady=12)

        scrollbar = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        scrollbar.pack(side=RIGHT, fill="y", padx=(0, 12), pady=12)
        self.log.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Frame(self)
        footer.pack(side=TOP, fill=X, padx=12, pady=(0, 12))

        ttk.Label(footer, textvariable=self.status_var).pack(side=LEFT)

    def _log(self, message: str) -> None:
        timestamp = _dt.datetime.now().strftime("%H:%M:%S")
        self.log.insert("", END, values=(f"[{timestamp}] {message}",))
        self.log.yview_moveto(1.0)

    def _queue_log(self, message: str) -> None:
        self._queue.put(("log", message))

    def _queue_status(self, message: str) -> None:
        self._queue.put(("status", message))

    def _queue_progress(self, value: int, maximum: int | None) -> None:
        self._queue.put(("progress", (value, maximum)))

    def _process_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "progress":
                    value, maximum = payload  # type: ignore[misc]
                    if maximum:
                        self.progress.configure(mode="determinate", maximum=maximum)
                        self.progress["value"] = min(value, maximum)
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(10)
                elif kind == "done":
                    target = payload  # type: ignore[assignment]
                    self._install_done(target)
                elif kind == "error":
                    self._install_error(str(payload))
        except queue.Empty:
            pass

        self.after(100, self._process_queue)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.install_button.configure(state=state)
        self.version_combo.configure(state="disabled" if busy else "readonly")

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="Select GTA IV installation folder")
        if selected:
            self.install_path_var.set(selected)
            self._preview_target()

    def _autodetect_install(self) -> None:
        found = find_gta_install()
        if found:
            self.install_path_var.set(str(found))
            self._log(f"Detected GTA IV install: {found}")
            self._preview_target()
        else:
            self._log("Could not auto-detect GTA IV. Use Browse.")
            if not self.install_path_var.get():
                self.install_path_var.set(str(DEFAULT_GTA_ROOT))

    def _preview_target(self) -> None:
        try:
            target = resolve_install_target(normalize_path(self.install_path_var.get()))
            self.exe_path_var.set(str(target.retail_exe))
            self.open_button.configure(state="normal" if target.retail_dir.exists() else "disabled")
            self.copy_button.configure(state="normal" if target.retail_exe.exists() else "disabled")
        except Exception:
            self.open_button.configure(state="disabled")
            self.copy_button.configure(state="disabled")

    def _start_install(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        try:
            selected = normalize_path(self.install_path_var.get())
            target = resolve_install_target(selected)
        except Exception as exc:
            messagebox.showerror("Invalid GTA IV folder", str(exc))
            return

        version = self.version_var.get()
        url = PATCHES.get(version)
        if not url:
            messagebox.showerror("Invalid patch", "Select a patch version.")
            return

        self.exe_path_var.set(str(target.retail_exe))
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self._set_busy(True)
        self.status_var.set("Installing...")
        self._log(f"Target GTAIV folder: {target.gtaiv_dir}")
        self._log(f"Retail folder destination: {target.retail_dir}")
        self._log(f"Selected patch: {version}")

        self._worker = threading.Thread(
            target=self._install_worker,
            args=(target, version, url, self.backup_existing_var.get()),
            daemon=True,
        )
        self._worker.start()

    def _install_worker(self, target: InstallTarget, version: str, url: str, backup_existing: bool) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="gtaiv-gtac-patcher-"))
        zip_path = temp_dir / "patch.zip"

        try:
            self._queue_status("Downloading patch...")
            self._queue_log(f"Downloading from: {url}")

            def progress(downloaded: int, total: int | None) -> None:
                # Throttle queue noise.
                now = time.time()
                if now - self._last_progress_time < 0.05:
                    return
                self._last_progress_time = now
                if total:
                    percent = int(downloaded * 100 / total)
                    self._queue_status(f"Downloading patch... {percent}%")
                    self._queue_progress(downloaded, total)
                else:
                    self._queue_status(f"Downloading patch... {downloaded // 1024} KiB")
                    self._queue_progress(downloaded, None)

            download_file(url, zip_path, progress)
            self._queue_progress(100, 100)
            self._queue_log(f"Downloaded patch ZIP: {zip_path}")

            self._queue_status("Validating ZIP...")
            ensure_zip_has_retail(zip_path)
            self._queue_log("ZIP validation passed: Retail/GTAIV.exe found.")

            if target.retail_dir.exists():
                if not backup_existing:
                    raise InstallError(
                        "Retail folder already exists and backup/replace is disabled:\n"
                        f"{target.retail_dir}"
                    )
                self._queue_status("Backing up existing Retail folder...")
                backup_path = backup_existing_retail(target.retail_dir)
                self._queue_log(f"Existing Retail folder moved to: {backup_path}")

            self._queue_status("Extracting Retail folder...")
            safe_extract_retail(zip_path, target.gtaiv_dir)

            if not target.retail_exe.exists():
                raise InstallError(
                    "Install completed, but GTAIV.exe was not found at the expected path:\n"
                    f"{target.retail_exe}"
                )

            self._queue_log("Patch installed successfully.")
            self._queue_log(f"Use this in GTA Connected Game EXE Path: {target.retail_exe}")
            self._queue.put(("done", target))

        except Exception as exc:
            self._queue.put(("error", exc))
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except OSError:
                pass

    def _install_done(self, target: InstallTarget) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=100)
        self.status_var.set("Done.")
        self._set_busy(False)
        self.exe_path_var.set(str(target.retail_exe))
        self.open_button.configure(state="normal")
        self.copy_button.configure(state="normal")
        self._copy_exe_path(silent=True)

        messagebox.showinfo(
            "Patch installed",
            "Retail patch installed successfully.\n\n"
            "The GTA Connected Game EXE Path has been copied to your clipboard:\n\n"
            f"{target.retail_exe}",
        )

    def _install_error(self, error: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Failed.")
        self._set_busy(False)
        self._log(f"ERROR: {error}")
        messagebox.showerror("Install failed", error)
        self._preview_target()

    def _copy_exe_path(self, silent: bool = False) -> None:
        path = self.exe_path_var.get().strip()
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self.update_idletasks()
        if not silent:
            self._log(f"Copied EXE path: {path}")

    def _open_retail_folder(self) -> None:
        try:
            target = resolve_install_target(normalize_path(self.install_path_var.get()))
            if not target.retail_dir.exists():
                messagebox.showwarning("Retail folder missing", f"Folder does not exist:\n{target.retail_dir}")
                return
            open_folder(target.retail_dir)
        except Exception as exc:
            messagebox.showerror("Could not open folder", str(exc))


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
