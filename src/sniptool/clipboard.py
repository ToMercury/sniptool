"""剪贴板历史：sqlite 落盘 + XFixes/Qt 监听 + Super+V 面板 + 选中回贴。"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from .imaging import png_bytes

TEXT, IMAGE = "text", "image"
MAX_TEXT = 200_000
MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass
class ClipItem:
    id: int
    kind: str
    text: str = ""
    thumb: QImage | None = None
    created: float = 0.0
    size: int = 0
    pinned: bool = False
    image: QImage | None = None   # 只有 get() 才加载原图


class HistoryStore:
    """剪贴板历史存储。path=None 时用内存库（关闭持久化）。"""

    def __init__(self, path: Path | str | None, limit: int = 100, days: int = 7):
        self.limit = max(1, int(limit))
        self.days = max(1, int(days))
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path else ":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS items("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT NOT NULL,"
            " text TEXT,"
            " image BLOB,"
            " thumb BLOB,"
            " digest TEXT NOT NULL,"
            " created REAL NOT NULL,"
            " size INTEGER NOT NULL DEFAULT 0,"
            " pinned INTEGER NOT NULL DEFAULT 0)"
        )
        # 老库（v0.7 早期）没有 pinned 列，就地迁移
        cols = {row["name"] for row in self._db.execute("PRAGMA table_info(items)")}
        if "pinned" not in cols:
            self._db.execute("ALTER TABLE items ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_items_created ON items(created DESC)")
        self._db.commit()
        if path:
            try:  # 里面可能有剪贴板原文/图片，限制为本用户可读
                os.chmod(path, 0o600)
            except OSError:
                pass
        self.prune()

    # ------------------------------------------------------------ 写入
    @staticmethod
    def digest_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()

    @staticmethod
    def _digest_image(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def add_text(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT]
        return self._add(TEXT, text, None, None, self.digest_text(text), len(text.encode("utf-8", "ignore")))

    def add_image(self, img: QImage) -> bool:
        if img is None or img.isNull():
            return False
        data = png_bytes(img)
        if len(data) > MAX_IMAGE_BYTES:
            return False
        thumb = img.scaled(
            QSize(160, 108),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return self._add(IMAGE, None, data, png_bytes(thumb), self._digest_image(data), len(data))

    def _add(self, kind: str, text: str | None, image: bytes | None,
             thumb: bytes | None, digest: str, size: int) -> bool:
        latest = self._db.execute("SELECT digest FROM items ORDER BY created DESC, id DESC LIMIT 1").fetchone()
        if latest is not None and latest["digest"] == digest:
            return False
        now = time.time()
        row = self._db.execute("SELECT id FROM items WHERE digest = ? LIMIT 1", (digest,)).fetchone()
        if row is not None:
            # 重复内容：把旧记录提到最前，不新增
            self._db.execute("UPDATE items SET created = ? WHERE id = ?", (now, row["id"]))
            self._db.execute("DELETE FROM items WHERE digest = ? AND id != ?", (digest, row["id"]))
        else:
            self._db.execute(
                "INSERT INTO items(kind, text, image, thumb, digest, created, size) VALUES(?,?,?,?,?,?,?)",
                (kind, text, image, thumb, digest, now, size),
            )
        self._db.commit()
        self.prune()
        return True

    # ------------------------------------------------------------ 读取
    @staticmethod
    def _row_to_item(row: sqlite3.Row, with_image: bool = False) -> ClipItem:
        item = ClipItem(
            id=int(row["id"]), kind=row["kind"], text=row["text"] or "",
            created=float(row["created"]), size=int(row["size"]),
            pinned=bool(row["pinned"]),
        )
        thumb_blob = row["thumb"]
        if thumb_blob:
            thumb = QImage.fromData(bytes(thumb_blob), "PNG")
            if not thumb.isNull():
                item.thumb = thumb
        if with_image and row["image"]:
            img = QImage.fromData(bytes(row["image"]), "PNG")
            if not img.isNull():
                item.image = img
        return item

    def items(self, pinned_only: bool = False, exclude_pinned: bool = False) -> list[ClipItem]:
        if pinned_only:
            rows = self._db.execute(
                "SELECT id, kind, text, thumb, created, size, pinned FROM items "
                "WHERE pinned = 1 ORDER BY created DESC, id DESC"
            ).fetchall()
        elif exclude_pinned:
            rows = self._db.execute(
                "SELECT id, kind, text, thumb, created, size, pinned FROM items "
                "WHERE pinned = 0 ORDER BY created DESC, id DESC"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, kind, text, thumb, created, size, pinned FROM items "
                "ORDER BY pinned DESC, created DESC, id DESC"
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get(self, item_id: int) -> ClipItem | None:
        row = self._db.execute("SELECT * FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if row is None:
            return None
        return self._row_to_item(row, with_image=True)

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"])

    def pinned_count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) AS n FROM items WHERE pinned = 1").fetchone()["n"])

    def toggle_pin(self, item_id: int) -> bool:
        """收藏/取消收藏；返回收藏后的状态。"""
        row = self._db.execute("SELECT pinned FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if row is None:
            return False
        pinned = 0 if row["pinned"] else 1
        self._db.execute("UPDATE items SET pinned = ? WHERE id = ?", (pinned, int(item_id)))
        self._db.commit()
        return bool(pinned)

    # ------------------------------------------------------------ 维护
    def remove(self, item_id: int) -> None:
        self._db.execute("DELETE FROM items WHERE id = ?", (int(item_id),))
        self._db.commit()

    def clear(self) -> None:
        self._db.execute("DELETE FROM items")
        self._db.commit()

    def clear_unpinned(self) -> None:
        """清空所有未收藏记录，保留已收藏内容。"""
        self._db.execute("DELETE FROM items WHERE pinned = 0")
        self._db.commit()

    def set_limits(self, limit: int, days: int) -> None:
        self.limit = max(1, int(limit))
        self.days = max(1, int(days))
        self.prune()

    def prune(self) -> None:
        cutoff = time.time() - self.days * 86400
        # 收藏项永久保留，不受天数/条数上限影响
        self._db.execute("DELETE FROM items WHERE pinned = 0 AND created < ?", (cutoff,))
        self._db.execute(
            "DELETE FROM items WHERE pinned = 0 AND id NOT IN "
            "(SELECT id FROM items WHERE pinned = 0 ORDER BY created DESC, id DESC LIMIT ?)",
            (self.limit,),
        )
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass


class ClipboardHistory(QObject):
    """监听系统剪贴板，把文字/图片写进历史。"""

    changed = Signal()

    def __init__(self, store: HistoryStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._guard = False
        self._clip = QGuiApplication.clipboard()
        self._clip.dataChanged.connect(self._on_changed)

    def _on_changed(self) -> None:
        if self._guard:
            return
        md = self._clip.mimeData()
        if md is None:
            return
        added = False
        if md.hasImage():
            img = self._clip.image()
            if not img.isNull():
                added = self.store.add_image(img)
        elif md.hasText():
            added = self.store.add_text(self._clip.text())
        if added:
            self.changed.emit()

    def set_clipboard(self, item: ClipItem) -> bool:
        """把历史记录放回剪贴板；屏蔽自身写入产生的 dataChanged。"""
        if item.kind == IMAGE:
            if item.image is None or item.image.isNull():
                return False
            self._guard = True
            self._clip.setImage(item.image)
        else:
            self._guard = True
            self._clip.setText(item.text)
        # 某些平台 dataChanged 是异步的，延迟一小段时间再放开
        QTimer.singleShot(300, self._release_guard)
        return True

    def _release_guard(self) -> None:
        self._guard = False


QSS = """
QWidget#clipCard {
    background: transparent; border: none;
}
QLabel#clipTitle { color: #8ec5ff; font-size: 14px; font-weight: 600; }
QLabel#clipCount { color: #929298; font-size: 12px; }
QLabel#clipHint { color: #85858b; font-size: 11px; }
QListWidget {
    background: transparent; border: none; outline: none;
}
QListWidget::item {
    color: #dcdcdf; background: rgba(255,255,255,9);
    border: 1px solid rgba(255,255,255,14); border-radius: 6px;
    margin: 2px 0; padding: 6px 8px;
}
QListWidget::item:hover { background: rgba(64,158,255,40); }
QListWidget::item:selected { background: rgba(64,158,255,90); border-color: rgba(64,158,255,170); }
QPushButton#pinBtn {
    color: #e8e8ea; background: rgba(255,255,255,16);
    border: 1px solid rgba(255,255,255,28); border-radius: 6px;
    padding: 4px 12px; font-size: 12px;
}
QPushButton#pinBtn:hover { background: rgba(64,158,255,80); }
QPushButton#pinBtn:disabled { color: #77777c; }
QPushButton#closeBtnClip {
    background: transparent; border: none; color: #9a9aa0;
    font-size: 17px; padding: 0; border-radius: 6px;
}
QPushButton#closeBtnClip:hover { color: #fff; background: rgba(230,80,80,140); }
QPushButton#modeBtn {
    color: #b8b8bd; background: rgba(255,255,255,10);
    border: 1px solid rgba(255,255,255,20); border-radius: 6px;
    padding: 4px 12px; font-size: 12px;
}
QPushButton#modeBtn:hover { background: rgba(64,158,255,50); }
QPushButton#modeBtn:checked {
    color: #ffffff; background: rgba(64,158,255,150);
    border-color: rgba(64,158,255,220);
}
QPushButton#clearBtnClip {
    color: #b8b8bd; background: rgba(255,255,255,10);
    border: 1px solid rgba(255,255,255,20); border-radius: 6px;
    padding: 4px 12px; font-size: 12px;
}
QPushButton#clearBtnClip:hover {
    color: #ff9999; background: rgba(230,80,80,60);
    border-color: rgba(230,80,80,120);
}
"""


class ClipboardPanel(QWidget):
    """剪贴板历史面板：上下选择、双击/Enter 粘贴、空格收藏、可拖拽、不置顶。"""

    chosen = Signal(int)

    def __init__(self, store: HistoryStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.mode = "all"          # all | pinned
        self._drag_origin = None
        self._focuser = None
        # 普通无边框窗口：不强制置顶，可以被其它窗口覆盖，也支持拖拽移动
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("clipCard")
        self.setStyleSheet(QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(460, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # 标题栏（可拖动）
        self.head = QWidget()
        self.head.setObjectName("clipHead")
        self.head.setCursor(Qt.CursorShape.SizeAllCursor)
        head = QHBoxLayout(self.head)
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(8)
        self.title = QLabel("剪贴板历史")
        self.title.setObjectName("clipTitle")
        self.title.setCursor(Qt.CursorShape.SizeAllCursor)
        self.count = QLabel("")
        self.count.setObjectName("clipCount")
        self.count.setCursor(Qt.CursorShape.SizeAllCursor)
        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("closeBtnClip")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setToolTip("关闭  Esc")
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.clicked.connect(self.hide)
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addStretch(1)
        head.addWidget(self.btn_close)
        root.addWidget(self.head)
        for w in (self.head, self.title, self.count):
            w.installEventFilter(self)

        # 一级：全部 / 收藏 ★ / 清空（右对齐）
        modes = QHBoxLayout()
        modes.setSpacing(6)
        self.btn_all = QPushButton("全部")
        self.btn_all.setObjectName("modeBtn")
        self.btn_all.setCheckable(True)
        self.btn_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav = QPushButton("收藏 ★")
        self.btn_fav.setObjectName("modeBtn")
        self.btn_fav.setCheckable(True)
        self.btn_fav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("clearBtnClip")
        self.btn_clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip("清空未收藏的剪贴板历史")
        self.btn_clear.clicked.connect(self._clear_unpinned)

        modes.addWidget(self.btn_all)
        modes.addWidget(self.btn_fav)
        modes.addStretch(1)
        modes.addWidget(self.btn_clear)

        self.btn_all.setChecked(True)
        self.btn_all.clicked.connect(lambda: self.set_mode("all"))
        self.btn_fav.clicked.connect(lambda: self.set_mode("pinned"))
        root.addLayout(modes)

        self.list = QListWidget(self)
        self.list.setIconSize(QSize(64, 44))
        self.list.setSpacing(2)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Fusion 风格下双击会激活 itemActivated；itemDoubleClicked 一并接上并去重
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        self.list.itemActivated.connect(self._on_double_clicked)
        self.list.installEventFilter(self)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        root.addWidget(self.list, 1)

        # 底部单行提示
        foot = QHBoxLayout()
        self.hint = QLabel("↑↓ 选择 · Space 收藏 · 双击/Enter 粘贴 · Delete 删除 · Esc 关闭")
        self.hint.setObjectName("clipHint")
        self.hint.setWordWrap(False)
        foot.addWidget(self.hint)
        root.addLayout(foot)

        self.refresh()

    # ------------------------------------------------------------ 拖动窗口
    def eventFilter(self, obj, ev) -> bool:
        if obj in (self.head, self.title, self.count):
            if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if (ev.type() == QEvent.Type.MouseMove and self._drag_origin is not None
                    and ev.buttons() & Qt.MouseButton.LeftButton):
                self.move(ev.globalPosition().toPoint() - self._drag_origin)
                return True
            if ev.type() == QEvent.Type.MouseButtonRelease:
                self._drag_origin = None
                return True
        if obj is self.list and ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key == Qt.Key.Key_Escape:
                self.hide()
                ev.accept()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._paste_current()
                ev.accept()
                return True
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._delete_current()
                ev.accept()
                return True
            if key == Qt.Key.Key_Space:
                self._toggle_pin_current()
                ev.accept()
                return True
        return super().eventFilter(obj, ev)

    def paintEvent(self, _ev) -> None:
        # 顶层透明窗口上直接用 QSS 画圆角背景在部分 Qt/X11 组合下不生效，
        # 这里显式画圆角卡片（子控件样式仍由 QSS 负责）。
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor(52, 52, 58), 1))
            p.setBrush(QColor(28, 28, 30, 250))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        finally:
            p.end()

    # ------------------------------------------------------------ 数据 / 视图
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if hasattr(self, "btn_all"):
            self.btn_all.setChecked(mode == "all")
        if hasattr(self, "btn_fav"):
            self.btn_fav.setChecked(mode == "pinned")
        self.refresh(prefer_row=0, keep_id=False)

    def _current_id(self) -> int | None:
        item = self.list.currentItem()
        if item is None:
            return None
        item_id = item.data(Qt.ItemDataRole.UserRole)
        return int(item_id) if item_id is not None else None

    def refresh(self, prefer_row: int | None = None, keep_id: bool = True) -> None:
        keep = self._current_id() if keep_id else None
        if prefer_row is None:
            prefer_row = self.list.currentRow() if self.list.currentRow() >= 0 else None
        self.list.clear()
        pinned_only = self.mode == "pinned"
        items = self.store.items(pinned_only=pinned_only, exclude_pinned=(not pinned_only))
        total = self.store.count()
        pinned = self.store.pinned_count()
        if pinned_only:
            self.count.setText(f"收藏 {pinned} 条")
        else:
            self.count.setText(f"共 {len(items)} 条")
        selected = False
        for it in items:
            text = it.text.replace("\n", " ⏎ ").strip() or "图片"
            if it.kind == IMAGE:
                text = f"图片 · {it.size // 1024} KB"
            prefix = "★ " if it.pinned else ""
            snippet = prefix + text[:90] + ("…" if len(text) > 90 else "")
            row = QListWidgetItem(snippet)
            row.setData(Qt.ItemDataRole.UserRole, it.id)
            row.setData(Qt.ItemDataRole.UserRole + 1, it.pinned)
            row.setToolTip(it.text[:500])
            row.setSizeHint(QSize(0, 58))
            if it.thumb is not None:
                row.setIcon(QIcon(QPixmap.fromImage(it.thumb)))
            self.list.addItem(row)
            if keep is not None and it.id == keep:
                self.list.setCurrentItem(row)
                selected = True
        if self.list.count() == 0:
            empty = QListWidgetItem("暂无剪贴板记录")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(empty)
        elif not selected:
            row = max(0, min(prefer_row if prefer_row is not None else 0, self.list.count() - 1))
            first = self.list.item(row)
            if first is not None and (first.flags() & Qt.ItemFlag.ItemIsSelectable):
                self.list.setCurrentItem(first)

    # ------------------------------------------------------------ 交互
    def _paste_current(self) -> None:
        if not self.isVisible():
            return
        item = self.list.currentItem()
        if item is None:
            return
        item_id = item.data(Qt.ItemDataRole.UserRole)
        if item_id is None:
            return
        self.hide()
        self.chosen.emit(int(item_id))

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        self.list.setCurrentItem(item)
        self._paste_current()

    def _delete_current(self) -> None:
        item_id = self._current_id()
        if item_id is None:
            return
        row = self.list.currentRow()
        self.store.remove(item_id)
        # 删除后停在同一行（最后一条则停到新的最后一条），不跳回开头
        self.refresh(prefer_row=row, keep_id=False)

    def _clear_unpinned(self) -> None:
        self.store.clear_unpinned()
        self.refresh(prefer_row=0, keep_id=False)

    def _toggle_pin_current(self) -> None:
        item_id = self._current_id()
        if item_id is None:
            return
        self.store.toggle_pin(item_id)
        self.refresh()

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#202024; color:#e6e6e8; border:1px solid #38383e; padding:4px; }"
            "QMenu::item { padding:5px 18px; border-radius:4px; }"
            "QMenu::item:selected { background:#2f6fbd; }"
        )
        pinned = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        act_pin = menu.addAction("取消收藏" if pinned else "收藏")
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.list.mapToGlobal(pos))
        if chosen is act_pin:
            self._toggle_pin_current()
        elif chosen is act_del:
            self._delete_current()

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._paste_current()
            return
        if key == Qt.Key.Key_Space:
            self._toggle_pin_current()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row = self.list.currentRow() + (-1 if key == Qt.Key.Key_Up else 1)
            row = max(0, min(row, self.list.count() - 1))
            self.list.setCurrentRow(row)
            return
        super().keyPressEvent(ev)

    def show_at_cursor(self) -> None:
        # 每次打开回到最新一条
        self.refresh(prefer_row=0, keep_id=False)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        cur = QCursor.pos()
        x = cur.x() + 10
        y = cur.y() + 10
        if x + self.width() > avail.right():
            x = avail.right() - self.width() - 4
        if y + self.height() > avail.bottom():
            y = avail.bottom() - self.height() - 4
        x = max(avail.left() + 4, x)
        y = max(avail.top() + 4, y)
        self.move(QPoint(x, y))
        self.bring_to_front()

    def bring_to_front(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.list.setFocus(Qt.FocusReason.OtherFocusReason)
        # 窗口刚 map 时 activateWindow 可能被 WM 忽略，稍后再确保一次
        QTimer.singleShot(30, self._ensure_focus)

    def _ensure_focus(self) -> None:
        if not self.isVisible():
            return
        if not self.isActiveWindow():
            try:
                if self._focuser is None:
                    from .x11 import X11Focus

                    self._focuser = X11Focus()
                self._focuser.focus(int(self.winId()))
            except Exception:  # noqa: BLE001
                pass
            self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.list.setFocus(Qt.FocusReason.OtherFocusReason)
