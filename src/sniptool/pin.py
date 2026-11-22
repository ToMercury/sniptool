"""贴图（Pin）：置顶窗口，可拖动、拖动边缘等比缩放、滚轮缩放、右键菜单。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

BORDER = 1
MIN_W = 60
MIN_H = 40
EDGE = 7          # 边缘命中宽度（逻辑像素）
RADIUS = 10       # 圆角半径（逻辑像素）


class PinWindow(QWidget):
    """把一张图钉在屏幕上。图像按物理像素提供，显示时除以 dpr。"""

    closed = Signal()

    def __init__(self, image: QImage, cfg, toaster=None, pos: QPoint | None = None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.toaster = toaster
        self.image = image
        screen = QGuiApplication.primaryScreen()
        self.dpr = float(screen.devicePixelRatio())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self._aspect = image.width() / max(1, image.height())
        w = max(MIN_W, int(round(image.width() / self.dpr)) + BORDER * 2)
        h = max(MIN_H, int(round(image.height() / self.dpr)) + BORDER * 2)
        if pos is None:
            pos = screen.availableGeometry().center() - QPoint(w // 2, h // 2)
        avail = screen.availableGeometry()
        x = max(avail.left(), min(pos.x(), avail.right() - w))
        y = max(avail.top(), min(pos.y(), avail.bottom() - h))
        self.setGeometry(x, y, w, h)

        self._drag_mode = ""        # move | nw | n | ...
        self._press_global = QPoint()
        self._start_geo = QRect()
        self._move_offset = QPoint()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QPainterPath()
            path.addRoundedRect(outer, RADIUS, RADIUS)
            p.fillPath(path, QColor(24, 24, 26, 245))
            inner = self.rect().adjusted(BORDER, BORDER, -BORDER, -BORDER)
            if not inner.isEmpty():
                p.save()
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(inner), max(1, RADIUS - BORDER),
                                    max(1, RADIUS - BORDER))
                p.setClipPath(clip)
                p.drawImage(inner, self.image)
                p.restore()
            p.setPen(QPen(QColor(64, 158, 255, 220), BORDER))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        finally:
            p.end()

    # ------------------------------------------------------------ 缩放
    def _zone(self, pos: QPoint) -> str:
        r = self.rect()
        left = pos.x() <= EDGE
        right = pos.x() >= r.width() - 1 - EDGE
        top = pos.y() <= EDGE
        bottom = pos.y() >= r.height() - 1 - EDGE
        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if bottom and left:
            return "sw"
        if bottom and right:
            return "se"
        if left:
            return "w"
        if right:
            return "e"
        if top:
            return "n"
        if bottom:
            return "s"
        return ""

    @staticmethod
    def _cursor_for(zone: str):
        return {
            "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
        }.get(zone, Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position().toPoint()
        zone = self._zone(pos)
        self._press_global = ev.globalPosition().toPoint()
        self._start_geo = QRect(self.geometry())
        if zone:
            self._drag_mode = zone
        else:
            self._drag_mode = "move"
            self._move_offset = self._press_global - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev) -> None:
        if not (ev.buttons() & Qt.MouseButton.LeftButton):
            zone = self._zone(ev.position().toPoint())
            self.setCursor(self._cursor_for(zone) if zone else Qt.CursorShape.SizeAllCursor)
            return
        if self._drag_mode == "move":
            self.move(ev.globalPosition().toPoint() - self._move_offset)
            return
        if not self._drag_mode:
            return
        self._resize_from_drag(ev.globalPosition().toPoint())

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = ""

    def _resize_from_drag(self, global_pos: QPoint) -> None:
        g = self._start_geo
        dx = global_pos.x() - self._press_global.x()
        dy = global_pos.y() - self._press_global.y()
        zone = self._drag_mode
        aspect = self._aspect
        # 以拖拽的主方向决定尺寸，另一方向按比例跟随，避免图片被拉变形
        horizontal = zone in ("e", "w")
        vertical = zone in ("n", "s")
        if horizontal:
            w = g.width() + (dx if "e" in zone else -dx)
            h = w / aspect
        elif vertical:
            h = g.height() + (dy if "s" in zone else -dy)
            w = h * aspect
        elif abs(dx) >= abs(dy * aspect):
            w = g.width() + (dx if "e" in zone else -dx)
            h = w / aspect
        else:
            h = g.height() + (dy if "s" in zone else -dy)
            w = h * aspect
        w = max(MIN_W, int(round(w)))
        h = max(MIN_H, int(round(h)))
        x = g.right() - w + 1 if "w" in zone else g.x()
        y = g.bottom() - h + 1 if "n" in zone else g.y()
        avail = self.screen().availableGeometry()
        w = min(w, avail.width())
        h = min(h, avail.height())
        x = max(avail.left(), min(x, avail.right() - w))
        y = max(avail.top(), min(y, avail.bottom() - h))
        self.setGeometry(x, y, w, h)

    def wheelEvent(self, ev) -> None:
        steps = ev.angleDelta().y() / 120.0
        if not steps:
            return
        factor = math.pow(1.1, steps)
        g = self.geometry()
        w = max(MIN_W, int(round(g.width() * factor)))
        h = max(MIN_H, int(round(g.height() * factor)))
        center = g.center()
        x = center.x() - w // 2
        y = center.y() - h // 2
        avail = self.screen().availableGeometry()
        w = min(w, avail.width())
        h = min(h, avail.height())
        x = max(avail.left(), min(x, avail.right() - w))
        y = max(avail.top(), min(y, avail.bottom() - h))
        self.setGeometry(x, y, w, h)
        ev.accept()

    # ------------------------------------------------------------ 菜单 / 快捷键
    def contextMenuEvent(self, ev) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#202024; color:#e6e6e8; border:1px solid #38383e; padding:4px; }"
            "QMenu::item { padding:5px 18px; border-radius:4px; }"
            "QMenu::item:selected { background:#2f6fbd; }"
        )
        act_copy = menu.addAction("复制图片  Ctrl+C")
        act_save = menu.addAction("保存图片  Ctrl+S")
        menu.addSeparator()
        zoom_menu = menu.addMenu("缩放")
        act_50 = zoom_menu.addAction("50%")
        act_100 = zoom_menu.addAction("100%")
        act_150 = zoom_menu.addAction("150%")
        menu.addSeparator()
        act_close = menu.addAction("关闭  Esc")
        chosen = menu.exec(ev.globalPos())
        if chosen is act_copy:
            self.copy_image()
        elif chosen is act_save:
            self.save_image()
        elif chosen is act_close:
            self.close()
        elif chosen is act_50:
            self._zoom_to(0.5)
        elif chosen is act_100:
            self._zoom_to(1.0)
        elif chosen is act_150:
            self._zoom_to(1.5)

    def _zoom_to(self, factor: float) -> None:
        base_w = self.image.width() / self.dpr + BORDER * 2
        base_h = self.image.height() / self.dpr + BORDER * 2
        g = self.geometry()
        center = g.center()
        w = max(MIN_W, int(round(base_w * factor)))
        h = max(MIN_H, int(round(base_h * factor)))
        avail = self.screen().availableGeometry()
        w = min(w, avail.width())
        h = min(h, avail.height())
        x = max(avail.left(), min(center.x() - w // 2, avail.right() - w))
        y = max(avail.top(), min(center.y() - h // 2, avail.bottom() - h))
        self.setGeometry(x, y, w, h)

    def copy_image(self) -> None:
        QGuiApplication.clipboard().setImage(self.image)
        if self.toaster:
            self.toaster.show("贴图已复制", f"{self.image.width()} × {self.image.height()} 图片已放入剪贴板", 2500)

    def save_image(self) -> None:
        from . import imaging

        try:
            path = imaging.save_image(self.image, self.cfg)
        except OSError as exc:
            if self.toaster:
                self.toaster.show("保存失败", str(exc))
            return
        if self.toaster:
            self.toaster.show("贴图已保存", str(path), 3500)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if ev.key() == Qt.Key.Key_C:
                self.copy_image()
                return
            if ev.key() == Qt.Key.Key_S:
                self.save_image()
                return
        super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.close()

    def closeEvent(self, ev) -> None:
        self.closed.emit()
        super().closeEvent(ev)
