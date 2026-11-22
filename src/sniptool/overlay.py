"""截图遮罩窗口 + 工具栏。坐标模型：全部物理像素；绘制时 painter.scale(1/dpr)。"""
from __future__ import annotations

import json
import math
import time
import traceback

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QPushButton, QWidget,
)

from .annotations import (
    ARROW, MIN_OP_SIZE, RECT, TEXT, Op, arrow_points, clone_op, clone_ops, hit_op, hit_op_handle,
    move_arrow_endpoint, move_op, op_bounds, op_changed, op_handles, paint_ops,
    resize_rect_op, text_box_size,
)
from .config import data_dir
from .geometry import DragState, apply_drag, clamp_rect, handle_rects, hit_test

ACCENT = QColor(64, 158, 255)
MASK_ALPHA = 118


class Toolbar(QWidget):
    """独立的悬浮工具条（作为遮罩的子控件，不打乱窗口层级）。"""

    tool = Signal(str)      # text | rect | arrow
    action = Signal(str)    # copy | save | ocr | translate | pin | undo | done
    cancel = Signal()

    BUTTONS_PREFIX = (
        ("文字", "text", ""),
        ("矩形", "rect", ""),
        ("箭头", "arrow", ""),
        ("清屏", "clear", ""),
    )
    BUTTONS_SUFFIX = (
        ("贴图", "pin", ""),
        ("OCR", "ocr", "⇧C"),
        ("翻译", "translate", "Ctrl+T"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("bar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        self._buttons: dict[str, QPushButton] = {}
        for label, key, hint in self.BUTTONS_PREFIX:
            b = QPushButton(label if not hint else f"{label}")
            b.setToolTip(hint or label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._emit(k))
            lay.addWidget(b)
            self._buttons[key] = b

        # 分隔线 |
        sep = QWidget()
        sep.setObjectName("sep")
        sep.setFixedWidth(1)
        sep.setFixedHeight(18)
        sep.setStyleSheet("background: rgba(255, 255, 255, 45); margin: 0 3px;")
        lay.addWidget(sep)

        for label, key, hint in self.BUTTONS_SUFFIX:
            b = QPushButton(label if not hint else f"{label}")
            b.setToolTip(hint or label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._emit(k))
            lay.addWidget(b)
            self._buttons[key] = b
        done = QPushButton("保存")
        done.setToolTip("保存到设置路径并关闭（Enter）")
        done.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(lambda: self._emit("save"))
        lay.addWidget(done)
        self._done_button = done
        self.setStyleSheet(
            """
            QWidget#bar { background: rgba(32,32,34,235); border-radius: 8px; }
            QPushButton {
                color: #e8e8ea; background: rgba(255,255,255,18);
                border: 1px solid rgba(255,255,255,28); border-radius: 5px;
                padding: 4px 9px; font-size: 12px;
            }
            QPushButton:hover { background: rgba(64,158,255,90); border-color: rgba(64,158,255,160); }
            QPushButton:checked { background: rgba(64,158,255,170); }
            """
        )
        self._active = ""

    def _emit(self, key: str) -> None:
        if key in ("text", "rect", "arrow"):
            self._active = "" if self._active == key else key
            for k, b in self._buttons.items():
                b.setCheckable(k in ("text", "rect", "arrow"))
                b.setChecked(k == self._active)
            self.tool.emit(self._active)
        else:
            self.action.emit(key)

    def clear_tool(self) -> None:
        self._active = ""
        for b in self._buttons.values():
            if b.isCheckable():
                b.setChecked(False)


class Overlay(QWidget):
    done = Signal(str)  # copy | save | ocr | translate | pin | cancel
    toolsChanged = Signal(str)

    def __init__(self, base: QImage, screen=None, parent=None):
        super().__init__(parent)
        self.screen = screen or QGuiApplication.primaryScreen()
        self.dpr = float(self.screen.devicePixelRatio())
        # base 一律按物理像素使用。QScreen.grabWindow() 默认会带 DPR，
        # 若原样交给 drawImage()，Qt 会按 DPR 再缩放一次（缩成左上角一小块）。
        if base.devicePixelRatio() != 1.0:
            base = base.copy()
            base.setDevicePixelRatio(1.0)
        self.base = base
        self.bounds = QRect(0, 0, base.width(), base.height())
        self.sel: QRect | None = None
        self.ops: list[Op] = []
        self.active_tool = ""
        self.drag = DragState()
        self._pending: QRect | None = None
        self._drag_start: QPoint | None = None
        self._drag_cur: QPoint | None = None
        self._editor: QLineEdit | None = None
        self._editor_anchor: QPoint | None = None
        self._text_press: QPoint | None = None
        self._text_dragged = False
        # 标注选中 / 拖动 / 撤销
        self.selected: Op | None = None
        self._op_drag: dict | None = None
        self._undo_stack: list[list[Op]] = []
        self._redo_stack: list[list[Op]] = []
        self._edit_target: Op | None = None
        self._edit_snapshot: list[Op] | None = None
        self._canvas_restart = False
        self.color = "#ff3b30"
        self.line_width = max(2, int(3 * self.dpr))
        self.font_px = max(12, int(24 * self.dpr))

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(self.screen.geometry())

        self.toolbar = Toolbar(self)
        self.toolbar.hide()
        self.toolbar.tool.connect(self._on_tool)
        self.toolbar.action.connect(self._on_action)

        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def finish(self, action: str) -> None:
        self._close_editor(commit=False)
        self._text_press = None
        self._text_dragged = False
        self._op_drag = None
        self._canvas_restart = False
        self.selected = None
        self.toolbar.hide()
        self.hide()
        self.done.emit(action)
        self.close()

    # ------------------------------------------------------------ 坐标
    def _p(self, pos) -> QPoint:
        return QPoint(int(pos.x() * self.dpr), int(pos.y() * self.dpr))

    def _logical(self, r: QRect) -> QRect:
        d = self.dpr
        return QRect(int(r.x() / d), int(r.y() / d), max(1, int(r.width() / d)), max(1, int(r.height() / d)))

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        try:
            self._paint(p)
        except Exception:  # noqa: BLE001
            self._log_paint_error()
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 之后一律按「物理像素」绘制（抵消 Qt 的 dpr 变换）
        p.scale(1.0 / self.dpr, 1.0 / self.dpr)

        p.drawImage(QPoint(0, 0), self.base)
        p.fillRect(self.bounds, QColor(0, 0, 0, MASK_ALPHA))

        sel = self.sel
        preview = self._preview_op()
        if sel is not None and sel.width() >= 2 and sel.height() >= 2:
            # 选区内部恢复原图亮度
            p.drawImage(sel, self.base, sel)
            # 标注（裁剪在选区内，避免画到遮罩上造成"画面错乱"的观感）
            p.save()
            try:
                p.setClipRect(sel)
                editing = self._edit_target
                if editing is not None and editing in self.ops:
                    # 编辑已有文字时隐藏原文字框，只显示一个圆角输入框
                    paint_ops(p, [o for o in self.ops if o is not editing])
                else:
                    paint_ops(p, self.ops)
                if preview is not None:
                    paint_ops(p, [preview])
                if self._edit_target is None:
                    self._paint_selected(p)
            finally:
                p.restore()

            # 边框：外白内蓝，两像素，保证任何背景上都看得见
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 200), max(1, int(1 * self.dpr))))
            p.drawRect(sel.adjusted(-1, -1, 1, 1))
            p.setPen(QPen(ACCENT, max(2, int(2 * self.dpr))))
            p.drawRect(sel)

            # 手柄：蓝底白边，比之前更大
            hs = max(8, int(10 * self.dpr))
            p.setPen(QPen(QColor(255, 255, 255, 235), max(1, int(1.5 * self.dpr))))
            p.setBrush(ACCENT)
            for hr in handle_rects(sel).values():
                c = hr.center()
                p.drawRect(QRect(c.x() - hs // 2, c.y() - hs // 2, hs, hs))

            # 尺寸标签
            label = f"{sel.width()} × {sel.height()}"
            f = QFont()
            f.setPixelSize(max(10, int(12 * self.dpr)))
            p.setFont(f)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(label) + int(12 * self.dpr)
            h = fm.height() + int(6 * self.dpr)
            lx = sel.left()
            ly = sel.top() - h - int(4 * self.dpr)
            if ly < 0:
                ly = sel.top() + int(4 * self.dpr)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(QRect(lx, ly, w, h), 4, 4)
            p.setPen(QColor(235, 235, 235))
            p.drawText(QRect(lx, ly, w, h), Qt.AlignmentFlag.AlignCenter, label)
            return

        # 未选择：只给提示
        f = QFont()
        f.setPixelSize(max(12, int(15 * self.dpr)))
        p.setFont(f)
        hint = "拖动选择区域 · Esc 取消 · Ctrl+C 复制 · Enter 保存 · Shift+C OCR"
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(hint) + int(24 * self.dpr)
        h = fm.height() + int(12 * self.dpr)
        x = (self.bounds.width() - w) // 2
        y = int(self.bounds.height() * 0.08)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(QRect(x, y, w, h), int(6 * self.dpr), int(6 * self.dpr))
        p.setPen(QColor(230, 230, 230))
        p.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, hint)

    def _preview_op(self) -> Op | None:
        """拖拽绘制形状时的实时预览（矩形/箭头跟随鼠标）。"""
        if self.active_tool not in (RECT, ARROW):
            return None
        if self._drag_start is None or self._drag_cur is None:
            return None
        r = QRect(self._drag_start, self._drag_cur).normalized()
        if r.width() < 1 and r.height() < 1:
            return None
        return Op(kind=self.active_tool, rect=r, color=self.color, width=self.line_width,
                  a=self._drag_start, b=self._drag_cur)

    # ------------------------------------------------------------ 选中态绘制
    def _paint_selected(self, p: QPainter) -> None:
        op = self.selected
        if op is None or op not in self.ops:
            return
        dpr = self.dpr
        b = op_bounds(op).adjusted(-2, -2, 2, 2)
        pen = QPen(ACCENT, max(1, int(1.3 * dpr)), Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(b)

        hs = max(9, int(10 * dpr))
        p.setPen(QPen(QColor(255, 255, 255, 235), max(1, int(1.5 * dpr))))
        p.setBrush(ACCENT)
        for hr in op_handles(op, hs).values():
            c = hr.center()
            if op.kind == ARROW:
                p.drawEllipse(c, hs // 2, hs // 2)
            else:
                p.drawRect(QRect(c.x() - hs // 2, c.y() - hs // 2, hs, hs))

    def _handle_size(self) -> int:
        return max(9, int(10 * self.dpr))

    def _hit_tolerance(self) -> int:
        return max(6, int(6 * self.dpr))

    def _selected_alive(self) -> bool:
        return self.selected is not None and self.selected in self.ops

    def _clear_annotations(self) -> None:
        """清空标注（开始新的框选 / Esc 清掉选区时调用）。"""
        self.ops.clear()
        self.selected = None
        self._op_drag = None
        self._undo_stack.clear()
        self._redo_stack.clear()

    # ------------------------------------------------------------ 撤销 / 重做
    def _snapshot(self) -> list[Op]:
        return clone_ops(self.ops)

    def _push_undo(self, snap: list[Op] | None = None) -> None:
        """在任何会改变 ops 的操作之前调用（快照的是变更前的状态）。"""
        self._undo_stack.append(clone_ops(snap if snap is not None else self.ops))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo(self) -> None:
        if self._undo_stack:
            snap = self._undo_stack.pop()
            self._redo_stack.append(self._snapshot())
            self.ops = clone_ops(snap)
        elif self.ops:  # 兼容：直接给 ops 赋值的场景（测试/外部脚本）
            self._redo_stack.append(self._snapshot())
            self.ops.pop()
        self.selected = None
        self._op_drag = None
        self.update()

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        snap = self._redo_stack.pop()
        self._undo_stack.append(self._snapshot())
        self.ops = clone_ops(snap)
        self.selected = None
        self._op_drag = None
        self.update()

    # ------------------------------------------------------------ 标注拖动 / 缩放
    def _begin_op_drag(self, op: Op, pos: QPoint, mode: str) -> None:
        # 与选区拖拽互斥，避免鼠标移动时把选区一起改了
        self.drag = DragState()
        self._text_press = None
        self._text_dragged = False
        self._op_drag = {
            "mode": mode,                 # move | handle:<name>
            "start": clone_op(op),
            "anchor": QPoint(pos),
            "snapshot": self._snapshot(),
            "changed": False,
        }

    @staticmethod
    def _reset_op(op: Op, start: Op) -> None:
        op.rect = QRect(start.rect)
        op.text = start.text
        op.color = start.color
        op.a = QPoint(start.a) if start.a is not None else None
        op.b = QPoint(start.b) if start.b is not None else None

    def _update_op_drag(self, pos: QPoint) -> None:
        d = self._op_drag
        op = self.selected
        if d is None or op is None or op not in self.ops:
            return
        start: Op = d["start"]
        mode: str = d["mode"]
        limit = self.sel if self.sel is not None and self.sel.isValid() else self.bounds
        self._reset_op(op, start)

        if mode == "move":
            delta = pos - d["anchor"]
            move_op(op, delta, limit)
        else:
            handle = mode.split(":", 1)[1]
            cpos = QPoint(
                max(limit.left(), min(pos.x(), limit.right())),
                max(limit.top(), min(pos.y(), limit.bottom())),
            )
            if op.kind == RECT:
                resize_rect_op(op, handle, cpos)
            elif op.kind == ARROW:
                move_arrow_endpoint(op, handle, cpos)

        d["changed"] = op_changed(op, start)

    def _finish_op_drag(self) -> None:
        d = self._op_drag
        self._op_drag = None
        if d is None:
            return
        if d["changed"]:
            self._push_undo(d["snapshot"])
        self.update()

    def _log_paint_error(self) -> None:
        try:
            log = data_dir() / "paint-error.log"
            with log.open("a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(traceback.format_exc())
                f.write(f"sel={self.sel} ops={len(self.ops)} dpr={self.dpr}\n")
        except Exception:  # noqa: BLE001
            pass

    def dump_debug(self) -> str:
        """F12：把当前遮罩画面 + 状态存盘，便于事后用脚本分析（不需要人看）。"""
        out_dir = data_dir() / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        png = out_dir / f"overlay_{ts}.png"
        js = out_dir / f"overlay_{ts}.json"
        try:
            self.grab().save(str(png), "PNG")
        except Exception:  # noqa: BLE001
            pass
        # 同时保存原始底图：用于区分"抓图内容坏了"还是"屏幕合成坏了"
        try:
            self.base.save(str(out_dir / f"base_{ts}.png"), "PNG")
        except Exception:  # noqa: BLE001
            pass
        state = {
            "dpr": self.dpr,
            "screen_logical": list(self.screen.geometry().getRect()),
            "screen_physical": [int(self.screen.geometry().width() * self.dpr),
                                int(self.screen.geometry().height() * self.dpr)],
            "widget_logical": [self.width(), self.height()],
            "widget_physical": [int(self.width() * self.dpr), int(self.height() * self.dpr)],
            "base": [self.base.width(), self.base.height()],
            "sel": list(self.sel.getRect()) if self.sel else None,
            "ops": [{"kind": o.kind, "rect": list(o.rect.getRect()), "text": o.text,
                     "a": [o.a.x(), o.a.y()] if o.a is not None else None,
                     "b": [o.b.x(), o.b.y()] if o.b is not None else None} for o in self.ops],
            "active_tool": self.active_tool,
            "selected": next((i for i, o in enumerate(self.ops) if o is self.selected), None),
            "undo_depth": len(self._undo_stack),
            "redo_depth": len(self._redo_stack),
            "toolbar": list(self.toolbar.geometry().getRect()),
        }
        try:
            js.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return str(out_dir)

    # ------------------------------------------------------------ 鼠标
    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = self._p(ev.position())
        # 点到遮罩的其它位置 = 结束当前文字输入（编辑框自己的点击不会到这里）
        if self._editor is not None:
            self._close_editor(commit=True)

        # 1) 先判选中标注的手柄：矩形可再次缩放，箭头可再调端点
        if self._selected_alive():
            h = hit_op_handle(self.selected, pos, tol=max(4, int(4 * self.dpr)),
                              size=self._handle_size())
            if h:
                self._begin_op_drag(self.selected, pos, f"handle:{h}")
                self.update()
                return

        # 2) 命中已有标注 → 选中；文字工具下点文字直接改原文字
        op = hit_op(self.ops, pos, self._hit_tolerance(), interior=True)
        if op is not None:
            self.selected = op
            if self.active_tool == TEXT and op.kind == TEXT:
                self._open_editor(pos, target=op)
            else:
                self._begin_op_drag(op, pos, "move")
            self.update()
            return

        # 3) 点空白：取消标注选中，继续原来的选区/工具逻辑
        self.selected = None
        if self.active_tool == TEXT:
            # 文字工具不参与选区拖拽；显式清空拖拽状态，避免按住鼠标移动时
            # 被 mouseMoveEvent 当成新建选区，把已有选区缩没。
            self.drag = DragState()
            self._text_press = pos
            self._text_dragged = False
            # 文字只能落在选区内；点外面视为结束当前输入，不新建
            if self.sel is None or not self.sel.contains(pos):
                self._close_editor(commit=True)
            return
        if self.active_tool in (RECT, ARROW):
            self._drag_start = pos
            self._drag_cur = pos
            self.drag = DragState(mode="new", anchor=pos, start_rect=self.sel)
            self.update()
            return
        mode = hit_test(self.sel, pos)
        if mode == "new":
            if self.sel is not None and self.sel.width() > 2 and self.sel.height() > 2:
                # 已经有选区的情况下，点击框外区域直接 pass（忽略，不重置选区也不新建选区）
                self.drag = DragState()
                return
            self.drag = DragState(mode=mode, anchor=pos, start_rect=self.sel)
            self.sel = QRect(pos, pos)
            self.toolbar.hide()
        else:
            self.drag = DragState(mode=mode, anchor=pos, start_rect=self.sel)
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        pos = self._p(ev.position())
        # 拖动已选中的标注（移动 / 手柄缩放）
        if self._op_drag is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
            self._update_op_drag(pos)
            self.update()
            return
        if self.active_tool == TEXT and self._text_press is not None:
            # 文字工具只响应“点击”，拖拽视为误操作：不改选区，也不弹编辑框
            if (ev.buttons() & Qt.MouseButton.LeftButton
                    and (pos - self._text_press).manhattanLength() > max(4, int(4 * self.dpr))):
                self._text_dragged = True
            return
        if (self.drag.mode and self.drag.anchor is not None
                and (ev.buttons() & Qt.MouseButton.LeftButton)):
            if self.active_tool in (RECT, ARROW):
                self._drag_cur = pos
            else:
                self.sel = apply_drag(self.sel, self.drag.mode, self.drag.start_rect,
                                      self.drag.anchor, pos, self.bounds)
            self._place_toolbar()
            self.update()
            return

        # 仅悬停：切光标
        if self._selected_alive():
            h = hit_op_handle(self.selected, pos, tol=max(4, int(4 * self.dpr)),
                              size=self._handle_size())
            if h:
                if self.selected.kind == RECT:
                    shape = {
                        "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                        "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                        "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                        "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                    }.get(h, Qt.CursorShape.CrossCursor)
                else:
                    shape = Qt.CursorShape.CrossCursor
                self.setCursor(shape)
                return
        hover = hit_op(self.ops, pos, self._hit_tolerance(), interior=True)
        if hover is not None:
            self.setCursor(Qt.CursorShape.SizeAllCursor if not self.active_tool
                           else Qt.CursorShape.PointingHandCursor)
            return
        if not self.active_tool:
            mode = hit_test(self.sel, pos)
            shape = {
                "new": Qt.CursorShape.CrossCursor,
                "move": Qt.CursorShape.SizeAllCursor,
                "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
            }.get(mode, Qt.CursorShape.CrossCursor)
            self.setCursor(shape)
        elif self.active_tool == TEXT:
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = self._p(ev.position())
        if self._op_drag is not None:
            self._finish_op_drag()
            return
        if self.active_tool == TEXT:
            press = self._text_press
            dragged = self._text_dragged
            self._text_press = None
            self._text_dragged = False
            self.drag = DragState()
            # 只有真正的单击才开编辑框；拖拽不产生文字
            if press is not None and not dragged:
                op = hit_op(self.ops, pos, self._hit_tolerance(), interior=True)
                if op is not None and op.kind == TEXT:
                    # 点已有文字：修改原文字，不新增
                    self.selected = op
                    self._open_editor(pos, target=op)
                elif self.sel is not None and self.sel.contains(pos):
                    self._open_editor(pos)
                else:
                    self._close_editor(commit=True)
            return
        mode = self.drag.mode
        self.drag = DragState()
        if self.active_tool in (RECT, ARROW) and self._drag_start is not None:
            a, b = self._drag_start, self._drag_cur or pos
            self._drag_start = None
            self._drag_cur = None
            r = QRect(a, b).normalized()
            if r.width() > 2 or r.height() > 2:
                op = Op(kind=self.active_tool, rect=r, color=self.color,
                        width=self.line_width, a=a, b=b)
                self._push_undo()
                self.ops.append(op)
                self.selected = op
            self.update()
            return
        if self.sel is not None and (self.sel.width() < 3 or self.sel.height() < 3):
            self.sel = None
        if mode == "new" and self._canvas_restart:
            self._canvas_restart = False
            if self.sel is not None:
                self._clear_annotations()
        if mode != "new" and self.sel is not None:
            pass
        if self.sel is not None and self.sel.width() > 2 and self.sel.height() > 2:
            self.toolbar.show()
            self.toolbar.raise_()
            self._place_toolbar()
        self.update()

    def wheelEvent(self, ev) -> None:
        """鼠标滚轮：选中文字/矩形/箭头时等比例缩放（以当前尺寸为基准）。
        - 向上滚放大，向下滚缩小
        - 矩形：等比例缩放宽高并同步调整线宽
        - 箭头：等比例调整长度并同步调整线宽
        - 文字：等比例调整字号 font_px
        """
        if self.selected is None:
            return
        op = self.selected
        if op.kind not in (TEXT, RECT, ARROW):
            return
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        factor = 1.1 if delta > 0 else 0.9

        self._push_undo()
        if op.kind == RECT:
            x1, y1, x2, y2 = op.rect.left(), op.rect.top(), op.rect.right(), op.rect.bottom()
            w, h = x2 - x1, y2 - y1
            cx, cy = x1 + w / 2, y1 + h / 2
            nw = max(MIN_OP_SIZE, w * factor)
            nh = max(MIN_OP_SIZE, h * factor)
            ox, oy = cx - nw / 2, cy - nh / 2
            op.rect = QRect(int(round(ox)), int(round(oy)), int(round(nw)), int(round(nh)))
            op.width = max(1, min(50, int(round(op.width * factor))))
        elif op.kind == ARROW:
            a, b = arrow_points(op)
            d = b - a
            length = math.hypot(d.x(), d.y())
            nl = max(MIN_OP_SIZE, length * factor)
            if length > 0:
                ux, uy = d.x() / length, d.y() / length
            else:
                ux, uy = 1.0, 0.0
            na = a
            nb = QPoint(int(round(a.x() + ux * nl)), int(round(a.y() + uy * nl)))
            op.a = na
            op.b = nb
            op.rect = QRect(na, nb).normalized()
            op.width = max(1, min(50, int(round(op.width * factor))))
        elif op.kind == TEXT:
            new_font = max(10, min(120, int(round(op.font_px * factor))))
            if new_font != op.font_px:
                op.font_px = new_font
                w, h = text_box_size(op.text or "", op.font_px)
                op.rect = QRect(op.rect.topLeft(), QSize(w, h))
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = self._p(ev.position())
        op = hit_op(self.ops, pos, self._hit_tolerance(), interior=True)
        if op is not None and self.sel is not None and self.sel.contains(pos):
            self.selected = op
            if op.kind == TEXT:
                self._open_editor(pos, target=op)
            self.update()
        # 双击空白不再保存（避免误触）；保存请用 Enter / Ctrl+S / 工具栏

    # ------------------------------------------------------------ 文字标注
    def _on_text_changed(self, ed: QLineEdit) -> None:
        """输入框文本变化时：自动向右扩展宽度，受当前选区右边界限制。"""
        if self.sel is None:
            return
        tc = ed.text()
        fm = ed.fontMetrics()
        # 预留内边距与光标空间
        needed_w = fm.horizontalAdvance(tc) + 16
        needed_w = max(40, needed_w)
        sl = self._logical(self.sel)
        margin = 4
        geo = ed.geometry()
        # 最右端不能超过选区右边界
        max_w = max(40, (sl.right() - margin) - geo.x())
        new_w = min(needed_w, max_w)
        if new_w != geo.width():
            ed.setGeometry(geo.x(), geo.y(), new_w, geo.height())

    def _open_editor(self, pos: QPoint, target: Op | None = None) -> None:
        self._close_editor(commit=True)
        if self.sel is None:
            return
        if target is not None and target not in self.ops:
            target = None
        if target is None and not self.sel.contains(pos):
            return
        anchor = QPoint(target.rect.topLeft()) if target is not None else QPoint(pos)
        ed = QLineEdit(self)
        ed.setObjectName("annEdit")
        font_logical = max(8, int(round(self.font_px / self.dpr)))
        ed.setStyleSheet(
            f"QLineEdit#annEdit {{ background: rgba(0,0,0,110); color: {self.color};"
            f" border: 1px solid rgba(64,158,255,220); border-radius: 5px;"
            f" padding: 0px 4px; font-size: {font_logical}px; }}"
        )
        # 编辑框限制在选区内，避免把选区边框/手柄整段盖住
        sl = self._logical(self.sel)
        margin = 4
        fm = ed.fontMetrics()
        initial_text = target.text if target is not None else ""
        content_w = fm.horizontalAdvance(initial_text) + 16 if initial_text else 60
        max_avail_w = max(40, sl.right() - margin - (int(anchor.x() / self.dpr) if target is None else self._logical(target.rect).left()))
        if target is not None:
            # 与标注文字框同位置、同尺寸、同圆角：编辑时只看到一个框
            tr = self._logical(target.rect)
            ew = max(40, tr.width())
            eh = max(24, tr.height())
            lx, ly = tr.left(), tr.top()
        else:
            ew = max(40, min(content_w, max(40, sl.width() - margin * 2)))
            eh = max(24, min(font_logical + 12, max(24, sl.height() - margin * 2)))
            lx = int(anchor.x() / self.dpr)
            ly = int(anchor.y() / self.dpr)
        ew = max(40, min(ew, max(40, sl.width() - margin * 2)))
        eh = max(24, min(eh, max(24, sl.height() - margin * 2)))
        lx = max(sl.left() + margin, min(lx, sl.right() - margin - ew))
        ly = max(sl.top() + margin, min(ly, sl.bottom() - margin - eh))
        ed.setGeometry(lx, ly, ew, eh)
        ed.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        ed.installEventFilter(self)
        # 捕获 ed 本身：旧编辑器的迟到信号不能误关新编辑器
        ed.returnPressed.connect(lambda e=ed: self._commit_editor(e))
        ed.editingFinished.connect(lambda e=ed: self._commit_editor(e))
        # 输入框文本变化时自动向右扩展宽度（受选区右边界限制）
        ed.textChanged.connect(lambda _="", e=ed: self._on_text_changed(e))
        self._editor = ed
        self._editor_anchor = anchor
        self._edit_target = target
        self._edit_snapshot = self._snapshot() if target is not None else None
        if target is not None:
            self.selected = target
            ed.blockSignals(True)
            ed.setText(target.text)
            ed.setCursorPosition(len(target.text))
            ed.blockSignals(False)
        ed.show()
        ed.raise_()
        ed.setFocus(Qt.FocusReason.OtherFocusReason)

    def _commit_editor(self, ed: QLineEdit) -> None:
        if self._editor is ed:
            self._close_editor(commit=True)

    def eventFilter(self, obj, ev) -> bool:
        """编辑框里的回车只提交文字，不能继续冒泡成遮罩的 Enter=保存。"""
        if obj is self._editor and ev.type() == QEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._close_editor(commit=True)
                ev.accept()
                return True
            if ev.key() == Qt.Key.Key_Escape:
                # Esc = 确认当前文字 + 关闭文字工具（不是丢弃）
                self._close_editor(commit=True)
                self._deactivate_tool()
                ev.accept()
                return True
        return super().eventFilter(obj, ev)

    def _close_editor(self, commit: bool) -> None:
        ed = self._editor
        if ed is None:
            return
        self._editor = None
        text = ed.text()
        ed.removeEventFilter(self)
        ed.hide()
        ed.deleteLater()
        anchor = self._editor_anchor
        target = self._edit_target
        snapshot = self._edit_snapshot
        self._editor_anchor = None
        self._edit_target = None
        self._edit_snapshot = None

        if commit:
            if target is not None and target in self.ops:
                # 修改已有文字：空内容视为删除
                if not text.strip():
                    if snapshot is not None:
                        self._push_undo(snapshot)
                    self.ops.remove(target)
                    if self.selected is target:
                        self.selected = None
                elif text != target.text:
                    if snapshot is not None:
                        self._push_undo(snapshot)
                    target.text = text
                    w, h = text_box_size(text, target.font_px)
                    target.rect = QRect(target.rect.topLeft(), QSize(w, h))
                    self.selected = target
            elif target is None and text.strip() and anchor is not None:
                w, h = text_box_size(text, self.font_px)
                op = Op(kind=TEXT, rect=QRect(anchor, QSize(w, h)), text=text,
                        color=self.color, width=self.line_width, font_px=self.font_px)
                self._push_undo()
                self.ops.append(op)
                self.selected = op
        # 编辑框拿走过焦点，关掉后要把键盘焦点还给遮罩，
        # 否则 Esc / Ctrl+C / Enter 等快捷键会失灵
        if self.isActiveWindow():
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    # ------------------------------------------------------------ 键盘
    def keyPressEvent(self, ev) -> None:
        if self._editor is not None:
            if ev.key() == Qt.Key.Key_Escape:
                self._close_editor(commit=False)
                return
            super().keyPressEvent(ev)
            return

        mods = ev.modifiers()
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            if self.active_tool:
                # 先退出当前标注工具（同时取消选中），再清选区
                self._deactivate_tool()
            elif self.selected is not None:
                self.selected = None
            elif self.sel is not None:
                # 清掉选区 = 放弃这一轮标注，重新框选时画面干净
                self.sel = None
                self._clear_annotations()
                self.toolbar.hide()
            else:
                self.finish("cancel")
                return
            self.update()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_action("save")
            return
        if mods & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_C:
                self._on_action("copy")
                return
            if key == Qt.Key.Key_S:
                self._on_action("save")
                return
            if key == Qt.Key.Key_Z:
                if mods & Qt.KeyboardModifier.ShiftModifier:
                    self._redo()
                else:
                    self._undo()
                return
            if key == Qt.Key.Key_Y:
                self._redo()
                return
            if key == Qt.Key.Key_T:
                self._on_action("translate")
                return
        if key == Qt.Key.Key_F12:
            path = self.dump_debug()
            print(f"[sniptool] 诊断信息已保存到 {path}", flush=True)
            return
        if mods & Qt.KeyboardModifier.ShiftModifier and key == Qt.Key.Key_C:
            self._on_action("ocr")
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected_alive():
                self._push_undo()
                self.ops.remove(self.selected)
                self.selected = None
                self.update()
                return
            if self.ops:
                self._push_undo()
                self.ops.pop()
                self.update()
                return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
            dx = -step if key == Qt.Key.Key_Left else step if key == Qt.Key.Key_Right else 0
            dy = -step if key == Qt.Key.Key_Up else step if key == Qt.Key.Key_Down else 0
            if self._selected_alive():
                # 选中标注：方向键微调位置（Shift 加速）
                self._push_undo()
                limit = self.sel if self.sel is not None and self.sel.isValid() else self.bounds
                move_op(self.selected, QPoint(dx, dy), limit)
                self.update()
                return
            if self.sel:
                moved = self.sel.translated(dx, dy)
                self.sel = clamp_rect(QRect(moved.topLeft(), moved.size()), self.bounds)
                self._place_toolbar()
                self.update()
                return
        super().keyPressEvent(ev)

    def _deactivate_tool(self) -> None:
        if not self.active_tool:
            return
        self.active_tool = ""
        self.selected = None
        self._text_press = None
        self._text_dragged = False
        self.toolbar.clear_tool()
        self.toolsChanged.emit("")

    def _on_tool(self, tool: str) -> None:
        # 切换/取消工具时先结束正在输入的文字，避免“已经点了取消还能继续打字”
        if self._editor is not None:
            self._close_editor(commit=True)
        self.active_tool = tool
        self._text_press = None
        self._text_dragged = False
        self._pending = None
        self.setCursor(Qt.CursorShape.IBeamCursor if tool == TEXT else Qt.CursorShape.CrossCursor)
        self.update()

    def _on_action(self, action: str) -> None:
        if action == "undo":
            self._undo()
            return
        if action == "clear":
            if self.ops:
                self._push_undo()
                self.ops.clear()
                self.selected = None
                self._op_drag = None
                self.update()
            return
        if self.sel is None or self.sel.width() < 2 or self.sel.height() < 2:
            return
        if action in ("copy", "save", "done", "ocr", "translate", "pin"):
            self._close_editor(commit=True)
            self.finish("save" if action == "done" else action)

    # ------------------------------------------------------------ 工具栏定位
    def _place_toolbar(self) -> None:
        if self.sel is None or not self.toolbar.isVisible():
            return
        lr = self._logical(self.sel)
        self.toolbar.adjustSize()
        sz = self.toolbar.size()
        x = lr.left()
        y = lr.bottom() + 8
        if y + sz.height() > self.height() - 4:
            y = lr.top() - sz.height() - 8
        if y < 4:
            y = 4
        x = max(4, min(x, self.width() - sz.width() - 4))
        self.toolbar.move(x, y)

    def result_image(self) -> QImage | None:
        from .imaging import compose

        if self.sel is None:
            return None
        return compose(self.base, self.sel, self.ops)
