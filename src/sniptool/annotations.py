"""标注对象模型、绘制与命中/几何编辑。坐标一律物理像素。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

TEXT, RECT, ARROW = "text", "rect", "arrow"

# 最小可命中/可编辑尺寸（物理像素）
MIN_OP_SIZE = 6


@dataclass
class Op:
    kind: str
    rect: QRect                      # rect/arrow 的包围盒；text 的锚点+尺寸
    text: str = ""
    color: str = "#ff3b30"
    width: int = 3                  # 线宽（物理像素）
    font_px: int = 28               # 字号（物理像素）
    a: QPoint | None = None         # 箭头起点（物理像素，保留拖拽方向）
    b: QPoint | None = None         # 箭头终点


def paint_ops(painter: QPainter, ops: list[Op], offset: QPoint = QPoint(0, 0)) -> None:
    for op in ops:
        r = op.rect.translated(-offset.x(), -offset.y())
        color = QColor(op.color)
        if op.kind == RECT:
            pen = QPen(color, op.width)
            pen.setJoinStyle(Qt.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(r)
        elif op.kind == ARROW:
            p0 = op.a if op.a is not None else r.topLeft()
            p1 = op.b if op.b is not None else r.bottomRight()
            _paint_arrow(painter, p0 - offset, p1 - offset, color, op.width)
        elif op.kind == TEXT:
            _paint_text(painter, op, r, color)


def _paint_arrow(painter: QPainter, p0: QPoint, p1: QPoint, color: QColor, width: int) -> None:
    d = p1 - p0
    length = math.hypot(d.x(), d.y())
    if length < 2:
        return
    ux, uy = d.x() / length, d.y() / length
    head = max(12, width * 5)
    base = QPoint(int(p1.x() - ux * head), int(p1.y() - uy * head))
    # 主杆（略短，避免穿过箭头尖）
    pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(p0, base)
    # 箭头
    ang = math.atan2(uy, ux)
    spread = math.radians(24)
    a = QPoint(int(p1.x() - head * math.cos(ang - spread)), int(p1.y() - head * math.sin(ang - spread)))
    b = QPoint(int(p1.x() - head * math.cos(ang + spread)), int(p1.y() - head * math.sin(ang + spread)))
    path = QPainterPath(p1)
    path.lineTo(a)
    path.lineTo(b)
    path.closeSubpath()
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawPath(path)


def _paint_text(painter: QPainter, op: Op, r: QRect, color: QColor) -> None:
    font = QFont()
    font.setPixelSize(op.font_px)
    font.setBold(False)
    painter.setFont(font)
    text = op.text or ""
    metrics = painter.fontMetrics()
    lines = text.split("\n") or [""]
    line_h = metrics.height()
    width = max((metrics.horizontalAdvance(ln) for ln in lines), default=0)
    box = QRect(r.topLeft(), QRect(0, 0, width + 10, line_h * len(lines) + 6).size())
    # 半透明底衬，保证在任意背景上可读
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 110))
    painter.drawRoundedRect(box.adjusted(-4, -2, 4, 2), 5, 5)
    painter.setPen(QPen(color))
    y = box.top() + metrics.ascent() + 3
    for ln in lines:
        painter.drawText(QPoint(box.left() + 5, y), ln)
        y += line_h


def text_box_size(text: str, font_px: int) -> tuple[int, int]:
    from PySide6.QtGui import QFontMetrics

    f = QFont()
    f.setPixelSize(font_px)
    metrics = QFontMetrics(f)
    lines = text.split("\n") or [""]
    w = max((metrics.horizontalAdvance(ln) for ln in lines), default=0)
    return w + 10, metrics.height() * len(lines) + 6


# ---------------------------------------------------------------- 选中与编辑
def clone_op(op: Op) -> Op:
    """深拷贝一个标注（QPoint/QRect 都是值类型，这里显式复制一份）。"""
    return Op(
        kind=op.kind,
        rect=QRect(op.rect),
        text=op.text,
        color=op.color,
        width=op.width,
        font_px=op.font_px,
        a=QPoint(op.a) if op.a is not None else None,
        b=QPoint(op.b) if op.b is not None else None,
    )


def clone_ops(ops: list[Op]) -> list[Op]:
    return [clone_op(o) for o in ops]


def arrow_points(op: Op) -> tuple[QPoint, QPoint]:
    a = op.a if op.a is not None else op.rect.topLeft()
    b = op.b if op.b is not None else op.rect.bottomRight()
    return a, b


def op_bounds(op: Op) -> QRect:
    """用于画选中框的包围盒（比 rect 稍微包住线宽/箭头头部）。"""
    if op.kind == ARROW:
        a, b = arrow_points(op)
        pad = max(2, op.width)
        return QRect(a, b).normalized().adjusted(-pad, -pad, pad, pad)
    return QRect(op.rect)


def _segment_distance(p: QPoint, a: QPoint, b: QPoint) -> float:
    ax, ay = float(a.x()), float(a.y())
    bx, by = float(b.x()), float(b.y())
    px, py = float(p.x()), float(p.y())
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def hit_op(ops: list[Op], pos: QPoint, tol: int = 6, interior: bool = True) -> Op | None:
    """返回命中的最上层标注。tol 为物理像素容差。

    interior=True 时点击矩形内部也算命中（无工具模式下可直接拖动）；
    矩形工具绘制时用 interior=False，优先在内部画新矩形。
    """
    for op in reversed(ops):
        if op.kind == TEXT:
            if op.rect.adjusted(-tol // 2, -tol // 2, tol // 2, tol // 2).contains(pos):
                return op
        elif op.kind == RECT:
            r = QRect(op.rect)
            if interior and r.contains(pos):
                return op
            outer = r.adjusted(-tol, -tol, tol, tol)
            inner = r.adjusted(tol, tol, -tol, -tol)
            if outer.contains(pos) and (not inner.isValid() or not inner.contains(pos)):
                return op
        elif op.kind == ARROW:
            a, b = arrow_points(op)
            if _segment_distance(pos, a, b) <= tol + op.width / 2:
                return op
    return None


def op_handles(op: Op, size: int = 8) -> dict[str, QRect]:
    """选中标注后显示的操作手柄。

    - 矩形：8 个边/角手柄，用于再次微调大小
    - 箭头：两个端点手柄（a=起点，b=终点），拖动可改方向/长度
    - 文字：无手柄（移动整体 + 点击改文字）
    """
    if op.kind == RECT:
        from .geometry import handle_rects

        return handle_rects(op.rect, size)
    if op.kind == ARROW:
        a, b = arrow_points(op)
        half = size // 2
        return {
            "a": QRect(a.x() - half, a.y() - half, size, size),
            "b": QRect(b.x() - half, b.y() - half, size, size),
        }
    return {}


def hit_op_handle(op: Op, pos: QPoint, tol: int = 6, size: int = 8) -> str | None:
    """命中选中标注的手柄名；没有则返回 None。"""
    for name, hr in op_handles(op, size).items():
        if hr.adjusted(-tol, -tol, tol, tol).contains(pos):
            return name
    return None


def translate_op(op: Op, delta: QPoint) -> None:
    op.rect = op.rect.translated(delta)
    if op.a is not None:
        op.a = op.a + delta
    if op.b is not None:
        op.b = op.b + delta


def clamp_delta_for_rect(r: QRect, limit: QRect) -> QPoint:
    """让矩形整体平移后仍留在 limit 内的最大偏移量。"""
    dx = min(0, limit.right() - r.right()) + max(0, limit.left() - r.left())
    dy = min(0, limit.bottom() - r.bottom()) + max(0, limit.top() - r.top())
    return QPoint(dx, dy)


def move_op(op: Op, delta: QPoint, limit: QRect) -> QPoint:
    """按偏移移动标注并钳制在 limit 内；返回实际生效的偏移。"""
    bounds = op_bounds(op)
    d = clamp_delta_for_rect(bounds, limit)
    applied = QPoint(delta.x() + d.x(), delta.y() + d.y())
    if applied.x() or applied.y():
        translate_op(op, applied)
    return applied


def resize_rect_op(op: Op, handle: str, pos: QPoint) -> None:
    """按手柄拖动矩形（固定对角/对边）。"""
    x1, y1, x2, y2 = op.rect.left(), op.rect.top(), op.rect.right(), op.rect.bottom()
    hx, hy = pos.x(), pos.y()
    if "n" in handle:
        y1 = min(hy, y2 - MIN_OP_SIZE)
    if "s" in handle:
        y2 = max(hy, y1 + MIN_OP_SIZE)
    if "w" in handle:
        x1 = min(hx, x2 - MIN_OP_SIZE)
    if "e" in handle:
        x2 = max(hx, x1 + MIN_OP_SIZE)
    op.rect = QRect(QPoint(x1, y1), QPoint(x2, y2)).normalized()


def move_arrow_endpoint(op: Op, handle: str, pos: QPoint) -> None:
    """拖动箭头端点；a=起点，b=终点。"""
    a, b = arrow_points(op)
    if handle == "a":
        op.a = QPoint(pos)
        op.b = QPoint(b)
    else:
        op.a = QPoint(a)
        op.b = QPoint(pos)
    op.rect = QRect(op.a, op.b).normalized()


def op_changed(op: Op, start: Op) -> bool:
    if op.rect != start.rect or op.text != start.text or op.color != start.color:
        return True
    if op.a != start.a or op.b != start.b:
        return True
    return False
