"""Native folder picker for the local webapp (browsers cannot expose absolute paths)."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


class FolderPickerError(RuntimeError):
    pass


def _pick_macos(initial: str | None) -> str | None:
    prompt = "Select Obsidian vault folder"
    script_parts = [f'choose folder with prompt "{prompt}"']
    if initial:
        start = Path(initial).expanduser()
        if start.is_file():
            start = start.parent
        if start.is_dir():
            # AppleScript needs HFS-style or POSIX file reference
            posix = str(start.resolve()).replace("\\", "\\\\").replace('"', '\\"')
            script_parts.append(f'default location (POSIX file "{posix}")')
    script = "POSIX path of (" + " ".join(script_parts) + ")"
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FolderPickerError(f"Could not open macOS folder picker: {exc}") from exc
    if result.returncode != 0:
        # User cancelled → usually exit 1 with empty or "User canceled."
        return None
    path = (result.stdout or "").strip()
    return path.rstrip("/") if path else None


def _pick_linux(initial: str | None) -> str | None:
    start = ""
    if initial:
        start_path = Path(initial).expanduser()
        if start_path.is_file():
            start_path = start_path.parent
        if start_path.is_dir():
            start = str(start_path.resolve())

    # Prefer zenity, then kdialog
    if _command_exists("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--title=Select Obsidian vault folder"]
        if start:
            cmd.append(f"--filename={start}/")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0:
            return None
        path = (result.stdout or "").strip()
        return path or None

    if _command_exists("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", start or str(Path.home()), "Select Obsidian vault folder"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0:
            return None
        path = (result.stdout or "").strip()
        return path or None

    return _pick_tkinter(initial)


def _pick_windows(initial: str | None) -> str | None:
    # PowerShell FolderBrowserDialog — works without extra deps
    initial_ps = ""
    if initial:
        start = Path(initial).expanduser()
        if start.is_file():
            start = start.parent
        if start.is_dir():
            escaped = str(start.resolve()).replace("'", "''")
            initial_ps = f"$f.SelectedPath = '{escaped}'; "
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select Obsidian vault folder'; "
        "$f.ShowNewFolderButton = $true; "
        f"{initial_ps}"
        "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath } "
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FolderPickerError(f"Could not open Windows folder picker: {exc}") from exc
    if result.returncode != 0:
        return None
    path = (result.stdout or "").strip()
    return path or None


def _pick_tkinter(initial: str | None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise FolderPickerError(
            "No native folder picker available (install zenity/kdialog or tkinter)."
        ) from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    kwargs: dict = {"title": "Select Obsidian vault folder", "mustexist": True}
    if initial:
        start = Path(initial).expanduser()
        if start.is_file():
            start = start.parent
        if start.is_dir():
            kwargs["initialdir"] = str(start.resolve())
    try:
        path = filedialog.askdirectory(**kwargs)
    finally:
        root.destroy()
    return path or None


def _command_exists(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def pick_folder(initial: str | None = None) -> str | None:
    """Open a native directory dialog. Returns absolute path or None if cancelled."""
    system = platform.system()
    if system == "Darwin":
        path = _pick_macos(initial)
    elif system == "Windows":
        path = _pick_windows(initial)
    else:
        path = _pick_linux(initial)
    if not path:
        return None
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise FolderPickerError(f"Selected path is not a directory: {resolved}")
    return str(resolved)
