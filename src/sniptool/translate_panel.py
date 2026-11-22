"""翻译结果面板：上原文、下译文（流式），可复制/重译/关闭。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from .config import Config
from .overlay import ACCENT
from .translate import StreamWorker

QSS = """
QWidget#card { background: transparent; border: none; }
QLabel#title { color: #8ec5ff; font-size: 13px; font-weight: 600; }
QLabel#status { color: #9a9a9e; font-size: 12px; }
QTextEdit {
    background: #161618; color: #dcdcdf; border: 1px solid #2b2b2f;
    border-radius: 6px; padding: 6px; font-size: 13px;
}
QTextEdit#src { color: #a9a9ad; }
QPushButton {
    color: #e8e8ea; background: rgba(255,255,255,16);
    border: 1px solid rgba(255,255,255,26); border-radius: 6px; padding: 5px 12px; font-size: 12px;
}
QPushButton:hover { background: rgba(64,158,255,90); }
"""


class TranslatePanel(QWidget):
    delta = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, cfg: Config, source_text: str, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.source_text = source_text
        self._started = None
        self._worker: StreamWorker | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("card")
        self.setStyleSheet(QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        title = QLabel("截图翻译")
        title.setObjectName("title")
        root.addWidget(title)

        self.src = QTextEdit()
        self.src.setObjectName("src")
        self.src.setPlainText(source_text)
        self.src.setFixedHeight(92)
        self.src.setReadOnly(True)
        root.addWidget(self.src)

        self.dst = QTextEdit()
        self.dst.setReadOnly(True)
        self.dst.setPlainText("")
        root.addWidget(self.dst, 1)

        self.status = QLabel("准备中…")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_copy = QPushButton("复制译文")
        self.btn_copy_src = QPushButton("复制原文")
        self.btn_close = QPushButton("关闭")
        for b in (self.btn_copy, self.btn_copy_src, self.btn_close):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.btn_copy.clicked.connect(self._copy_dst)
        self.btn_copy_src.clicked.connect(self._copy_src)
        self.btn_close.clicked.connect(self.close)

        self.delta.connect(self._on_delta)
        self.done.connect(self._on_done)
        self.error.connect(self._on_error)

        self._place()
        self.restart()

    def _place(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        w, h = 560, 420
        x = geo.right() - w - 28
        y = geo.bottom() - h - 28
        self.setGeometry(max(geo.left() + 8, x), max(geo.top() + 8, y), w, h)

    # ------------------------------------------------------------ 请求
    def restart(self) -> None:
        self.dst.setPlainText("")
        self.status.setText("翻译中…")
        self._started = time.monotonic()
        self._worker = StreamWorker(
            self.cfg, self.source_text,
            on_delta=lambda d: self.delta.emit(d),
            on_done=lambda full: self.done.emit(full),
            on_error=lambda msg: self.error.emit(msg),
        )
        self._worker.start()

    # ------------------------------------------------------------ 回调（主线程）
    def _on_delta(self, text: str) -> None:
        cur = self.dst.toPlainText()
        self.dst.setPlainText(cur + text)
        sb = self.dst.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_done(self, full: str) -> None:
        elapsed = int((time.monotonic() - (self._started or time.monotonic())) * 1000)
        self.status.setText(f"完成 · {elapsed}ms · {len(full)} 字符（已可复制）")

    def _on_error(self, msg: str) -> None:
        self.status.setText(f"失败：{msg[:160]}")
        if "API Key" in msg:
            self.dst.setPlainText("未配置 DeepSeek API Key。\n在 ~/.config/sniptool/config.json 的 translate.api_key 填好后重试。")

    # ------------------------------------------------------------ 按钮
    def _copy_dst(self) -> None:
        QGuiApplication.clipboard().setText(self.dst.toPlainText())

    def _copy_src(self) -> None:
        QGuiApplication.clipboard().setText(self.source_text)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(ev)

    def paintEvent(self, _ev) -> None:
        # 透明顶层窗口：显式画圆角背景 + 左侧强调条
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor(51, 51, 51), 1))
            p.setBrush(QColor(30, 30, 32, 250))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ACCENT)
            p.drawRoundedRect(QRect(0, 12, 3, self.height() - 24), 1.5, 1.5)
        finally:
            p.end()
