"""OCR 结果浮窗：半透明、置顶、可复制、自动关闭。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .overlay import ACCENT


class Toast(QWidget):
    closed = Signal()

    def __init__(self, title: str, text: str, parent=None, timeout_ms: int = 9000):
        super().__init__(parent)
        self.title = title
        self.text = text
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._lay_out()
        QTimer.singleShot(timeout_ms, self.close)

    def _lay_out(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        width = min(720, int(screen.width() * 0.55))
        lines = self.text.splitlines() or [""]
        show = lines[:6]
        line_h = 20
        height = 44 + line_h * len(show) + 10
        x = screen.left() + (screen.width() - width) // 2
        y = screen.bottom() - height - 40
        self.setGeometry(x, y, width, height)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(28, 28, 30, 240))
        p.drawRoundedRect(r, 10, 10)
        p.setBrush(ACCENT)
        p.drawRoundedRect(QRect(r.left(), r.top(), 4, r.height()), 2, 2)

        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(150, 200, 255))
        p.drawText(QRect(r.left() + 14, r.top() + 8, r.width() - 28, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)

        f2 = QFont()
        f2.setPixelSize(13)
        p.setFont(f2)
        p.setPen(QColor(232, 232, 235))
        lines = (self.text.splitlines() or [""])[:6]
        y = r.top() + 32
        for ln in lines:
            p.drawText(QRect(r.left() + 14, y, r.width() - 48, 20),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       ln[:120])
            y += 20
        if len(self.text.splitlines()) > 6:
            p.setPen(QColor(150, 150, 155))
            p.drawText(QRect(r.left() + 14, y, r.width() - 48, 18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "…（已复制完整内容到剪贴板）")
        p.end()

    def mousePressEvent(self, _ev) -> None:
        self.close()

    def closeEvent(self, ev) -> None:
        self.closed.emit()
        super().closeEvent(ev)


class Toaster:
    """保证同一时间只显示一个浮窗。"""

    def __init__(self) -> None:
        self._current: Toast | None = None

    def show(self, title: str, text: str, timeout_ms: int = 9000) -> None:
        if self._current is not None:
            self._current.close()
        t = Toast(title, text, timeout_ms=timeout_ms)
        t.closed.connect(lambda: setattr(self, "_current", None))
        self._current = t
        t.show()
