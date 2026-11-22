"""取图、合成、保存。所有坐标/尺寸均为物理像素。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect
from PySide6.QtGui import QGuiApplication, QImage, QPainter

from .annotations import Op, paint_ops


def grab_screen(screen=None) -> QImage:
    screen = screen or QGuiApplication.primaryScreen()
    pm = screen.grabWindow(0)
    img = pm.toImage()
    # QScreen.grabWindow() 返回的 QPixmap/QImage 会带屏幕的 devicePixelRatio。
    # 本项目所有坐标、裁剪、绘制都按「物理像素」处理，必须把 DPR 归一到 1；
    # 否则 QPainter.drawImage() 会按 DPR 再缩一次，遮罩里只剩左上角一小块截图，
    # 右侧/底部透出实时桌面，看起来就像两份桌面重叠。
    img.setDevicePixelRatio(1.0)
    return img


def compose(base: QImage, sel: QRect, ops: list[Op]) -> QImage:
    """裁出选区，并把标注画上去（标注坐标是全局物理像素，这里平移到选区左上角）。"""
    crop = base.copy(sel)
    painter = QPainter(crop)
    painter.setRenderHint(QPainter.Antialiasing, True)
    paint_ops(painter, ops, offset=sel.topLeft())
    painter.end()
    return crop


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_filename(cfg, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    stamp = when.strftime(cfg.save_template)
    ext = cfg.save_format.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    return Path(cfg.save_dir).expanduser() / f"{stamp}.{ext}"


def save_image(img: QImage, cfg) -> Path:
    path = build_filename(cfg)
    if path.exists():  # 同一秒内多次保存不覆盖
        stem, suffix = path.stem, path.suffix
        for i in range(2, 1000):
            cand = path.with_name(f"{stem}_{i}{suffix}")
            if not cand.exists():
                path = cand
                break
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = cfg.save_format.lower().lstrip(".")
    fmt = {"jpeg": "jpg"}.get(fmt, fmt)
    quality = 92 if fmt in ("jpg", "webp") else -1
    if not img.save(str(path), fmt.upper() if fmt != "jpg" else "JPEG", quality):
        raise OSError(f"保存失败: {path}")
    return path
