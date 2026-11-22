"""必须在 import PySide6 之前调用 setup_qt_plugins()：让 bundled Qt 能加载系统 fcitx5 输入法插件。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# Debian 与 Fedora 的系统 Qt6 插件根目录
QPA_CANDIDATES = (
    "/usr/lib/x86_64-linux-gnu/qt6/plugins",
    "/usr/lib64/qt6/plugins",
    "/usr/lib/qt6/plugins",
)

PLUGIN_NAME = "libfcitx5platforminputcontextplugin.so"


def _data_home() -> Path:
    env = os.environ.get("SNITTOOL_DATA_DIR") or os.environ.get("XDG_DATA_HOME")
    base = Path(env) if env else (Path.home() / ".local/share" / "sniptool")
    return base / "sniptool" if env and base.name != "sniptool" else base


def setup_qt_plugins(verbose: bool = False) -> str | None:
    """把系统 fcitx5 插件放进一个「只含该插件」的目录，并设 QT_PLUGIN_PATH 指向它。

    为什么不直接 QT_PLUGIN_PATH=/usr/lib/.../qt6/plugins：那样 Qt 会优先加载系统的
    libqxcb.so（平台插件），与 bundled Qt 混搭，风险更大且没必要。
    """
    if "QT_LOGGING_RULES" not in os.environ:
        os.environ["QT_LOGGING_RULES"] = "qt.qpa.clipboard=false;qt.svg.draw=false"
    else:
        rules = os.environ["QT_LOGGING_RULES"]
        if "qt.qpa.clipboard" not in rules:
            rules += ";qt.qpa.clipboard=false"
        if "qt.svg.draw" not in rules:
            rules += ";qt.svg.draw=false"
        os.environ["QT_LOGGING_RULES"] = rules

    dst_root = _data_home() / "qtplugins"
    dst_dir = dst_root / "platforminputcontexts"
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    for cand in QPA_CANDIDATES:
        src = Path(cand) / "platforminputcontexts" / PLUGIN_NAME
        if not src.exists():
            continue
        link = dst_dir / PLUGIN_NAME
        try:
            if link.is_symlink() or link.exists():
                if link.resolve() != src.resolve():
                    link.unlink()
            if not link.exists():
                link.symlink_to(src)
        except OSError:
            shutil.copy2(src, link)
        existing = os.environ.get("QT_PLUGIN_PATH", "")
        parts = [str(dst_root)] + [p for p in existing.split(":") if p and p != str(dst_root)]
        os.environ["QT_PLUGIN_PATH"] = ":".join(parts)
        if verbose:
            print(f"[bootstrap] fcitx5 插件 -> {src}")
        return str(src)
    if verbose:
        print("[bootstrap] 未找到 fcitx5 输入法插件（中文输入将退化为粘贴文字）")
    return None
