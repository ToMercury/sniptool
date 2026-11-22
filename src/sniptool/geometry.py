"""选区几何：全部用「物理像素」坐标系，只在鼠标/绘制边界处做 dpr 换算。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect

HANDLE = 12  # 手柄边长（物理像素）
MIN_SIZE = 4


@dataclass
class DragState:
    # 空字符串表示「没有拖拽」。不能用 "new" 当默认值：文字工具按下鼠标时
    # 并不进入拖拽，若默认是 "new"，后续鼠标移动会被当成新建选区，
    # 把已有选区缩成 1x1 并在松开时清空。
    mode: str = ""  # "" | new | move | resize:<handle>
    anchor: QPoint | None = None
    start_rect: QRect | None = None


def normalize(p0: QPoint, p1: QPoint) -> QRect:
    return QRect(p0, p1).normalized()


def clamp_rect(r: QRect, bounds: QRect) -> QRect:
    x1 = max(bounds.left(), min(r.left(), bounds.right()))
    y1 = max(bounds.top(), min(r.top(), bounds.bottom()))
    x2 = max(bounds.left(), min(r.right(), bounds.right()))
    y2 = max(bounds.top(), min(r.bottom(), bounds.bottom()))
    return QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()


def handle_rects(sel: QRect, size: int = HANDLE) -> dict[str, QRect]:
    x1, y1, x2, y2 = sel.left(), sel.top(), sel.right(), sel.bottom()
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    pts = {
        "nw": (x1, y1), "n": (cx, y1), "ne": (x2, y1),
        "w": (x1, cy), "e": (x2, cy),
        "sw": (x1, y2), "s": (cx, y2), "se": (x2, y2),
    }
    return {k: QRect(vx - size // 2, vy - size // 2, size, size) for k, (vx, vy) in pts.items()}


def hit_test(sel: QRect | None, pos: QPoint, tol: int = HANDLE) -> str:
    """返回 'nw'..'se' / 'move' / 'new'。"""
    if sel is None or sel.width() < 1 or sel.height() < 1:
        return "new"
    grown = sel.adjusted(-tol, -tol, tol, tol)
    if not grown.contains(pos):
        return "new"
    for name, hr in handle_rects(sel).items():
        if hr.adjusted(-tol // 2, -tol // 2, tol // 2, tol // 2).contains(pos):
            return name
    if sel.contains(pos):
        return "move"
    return "new"


CURSORS = {
    "nw": "SizeFDiagCursor", "se": "SizeFDiagCursor",
    "ne": "SizeBDiagCursor", "sw": "SizeBDiagCursor",
    "n": "SizeVerCursor", "s": "SizeVerCursor",
    "e": "SizeHorCursor", "w": "SizeHorCursor",
    "move": "SizeAllCursor", "new": "CrossCursor",
}


def apply_drag(sel: QRect | None, mode: str, start_rect: QRect | None, anchor: QPoint | None,
               pos: QPoint, bounds: QRect) -> QRect | None:
    """根据拖拽模式算出新选区（物理像素）。"""
    if mode == "new":
        if anchor is None:
            return QRect(pos, pos)
        return clamp_rect(normalize(anchor, pos), bounds)

    if sel is None or start_rect is None:
        return sel

    if mode == "move":
        if anchor is None:
            return sel
        delta = pos - anchor
        moved = start_rect.translated(delta)
        # 限制在屏幕内
        dx = min(0, bounds.right() - moved.right()) + max(0, bounds.left() - moved.left())
        dy = min(0, bounds.bottom() - moved.bottom()) + max(0, bounds.top() - moved.top())
        return moved.translated(dx, dy)

    # resize：固定对角，移动对应边
    x1, y1, x2, y2 = start_rect.left(), start_rect.top(), start_rect.right(), start_rect.bottom()
    hx, hy = pos.x(), pos.y()
    handle = mode.split(":")[-1]   # mode 形如 resize:nw，只看手柄名
    if "n" in handle:
        y1 = min(hy, y2 - MIN_SIZE)
    if "s" in handle:
        y2 = max(hy, y1 + MIN_SIZE)
    if "w" in handle:
        x1 = min(hx, x2 - MIN_SIZE)
    if "e" in handle:
        x2 = max(hx, x1 + MIN_SIZE)
    return clamp_rect(QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized(), bounds)
