"""开机自启：写入 XDG autostart .desktop，Xfce/GNOME/KDE 通用。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def autostart_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "autostart"


def autostart_file() -> Path:
    return autostart_dir() / "sniptool.desktop"


def launch_command() -> str:
    """优先用仓库里的 sniptool 启动器；安装版退化为 python -m sniptool。"""
    launcher = Path(__file__).resolve().parents[2] / "sniptool"
    if launcher.exists():
        return f'"{launcher}" daemon'
    return f'"{sys.executable}" -m sniptool daemon'


def desktop_entry() -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=sniptool\n"
        "Comment=Screenshot / OCR / translate / clipboard history\n"
        f"Exec={launch_command()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_enabled() -> bool:
    return autostart_file().exists()


def set_enabled(enabled: bool) -> None:
    path = autostart_file()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop_entry(), encoding="utf-8")
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
    elif path.exists():
        path.unlink()
