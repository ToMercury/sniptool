"""回归测试：标注选中、再次编辑文字、矩形再次缩放、箭头端点、Delete、撤销/重做。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sniptool.annotations import ARROW, RECT, TEXT, clone_op, op_handles
from sniptool.overlay import Overlay

app = QApplication(sys.argv)
fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        fails.append(name)


def settle(seconds=0.12):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.01)


base = QImage(1920, 1080, QImage.Format.Format_RGB32)
base.fill(QColor(235, 238, 242))
base.setDevicePixelRatio(1.0)
ov = Overlay(base)
ov.show()
settle(0.2)
actions = []
ov.done.connect(lambda a: actions.append(a))
d = ov.dpr


def ev(kind, x, y, buttons):
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                       Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)


def press(x, y):
    ov.mousePressEvent(ev(QMouseEvent.Type.MouseButtonPress, x, y, Qt.MouseButton.LeftButton))
    settle(0.05)


def move(x, y):
    ov.mouseMoveEvent(ev(QMouseEvent.Type.MouseMove, x, y, Qt.MouseButton.LeftButton))
    settle(0.05)


def release(x, y):
    ov.mouseReleaseEvent(ev(QMouseEvent.Type.MouseButtonRelease, x, y, Qt.MouseButton.NoButton))
    settle(0.08)


def click(x, y):
    press(x, y)
    release(x, y)


def dblclick(x, y):
    ov.mouseDoubleClickEvent(ev(QMouseEvent.Type.MouseButtonDblClick, x, y, Qt.MouseButton.NoButton))
    settle(0.08)


def key(k, mods=Qt.KeyboardModifier.NoModifier):
    ov.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, k, mods))
    settle(0.06)


def tool(name):
    ov.toolbar._emit(name)
    settle(0.06)


def to_logical(p: QPoint) -> QPoint:
    return QPoint(int(p.x() / d), int(p.y() / d))


# 框选 (100,100)-(900,800)
press(100, 100)
move(500, 450)
move(900, 800)
release(900, 800)
check("框选成立", ov.sel is not None and ov.sel.width() > 700 and ov.sel.height() > 600,
      f"{ov.sel.getRect() if ov.sel else None}")
sel0 = ov.sel.getRect()

# 绘制矩形
tool("rect")
press(200, 200)
move(350, 280)
move(500, 380)
release(500, 380)
check("矩形已创建", len(ov.ops) == 1 and ov.ops[0].kind == RECT,
      f"{[(o.kind, o.rect.getRect()) for o in ov.ops]}")
check("新矩形自动选中", ov.selected is ov.ops[0])
check("矩形有 8 个缩放手柄", len(op_handles(ov.ops[0], 12)) == 8)

# 矩形工具仍然激活时，直接拖手柄也能再次缩放（用户预期）
rect = ov.ops[0]
se = op_handles(rect, ov._handle_size())["se"].center()
press(*to_logical(se).toTuple())
move(*to_logical(se + QPoint(int(40 * d), int(30 * d))).toTuple())
release(*to_logical(se + QPoint(int(40 * d), int(30 * d))).toTuple())
check("矩形工具激活时也能缩放", rect.rect.width() >= 340 and rect.rect.height() >= 207,
      f"{rect.rect.getRect()}")
key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
check("缩放后撤销复原", ov.ops[0].rect.width() <= 344,
      f"{ov.ops[0].rect.getRect()}")
tool("rect")  # 关闭矩形工具

rect = ov.ops[0]
start_rect = clone_op(rect).rect

# 点空白取消选中
click(700, 650)
check("点空白取消选中", ov.selected is None)
check("点空白未改变选区", ov.sel.getRect() == sel0, f"{ov.sel.getRect()} vs {sel0}")

# 再点矩形选中
click(350, 290)
check("点击矩形可选中", ov.selected is rect)

# 拖动 se 手柄再次缩放
se = op_handles(rect, ov._handle_size())["se"].center()
press(*to_logical(se).toTuple())
move(*to_logical(se + QPoint(int(60 * d), int(40 * d))).toTuple())
release(*to_logical(se + QPoint(int(60 * d), int(40 * d))).toTuple())
check("矩形可再次放大", rect.rect.width() > start_rect.width()
      and rect.rect.height() > start_rect.height(),
      f"{start_rect.getRect()} -> {rect.rect.getRect()}")

# 撤销 / 重做
key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
check("撤销恢复矩形尺寸", ov.ops[0].rect == start_rect, f"{ov.ops[0].rect.getRect()}")
key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
check("重做恢复缩放", ov.ops[0].rect != start_rect, f"{ov.ops[0].rect.getRect()}")

# Delete 删除选中标注
click(350, 290)
check("重做后可再次选中", ov.selected is ov.ops[0])
key(Qt.Key.Key_Delete)
check("Delete 删除选中矩形", len(ov.ops) == 0, f"{len(ov.ops)}")
key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
check("撤销恢复被删矩形", len(ov.ops) == 1 and ov.ops[0].kind == RECT, f"{len(ov.ops)}")

# 箭头：创建、选中、拖端点
tool("arrow")
press(600, 250)
move(700, 350)
move(800, 450)
release(800, 450)
arrow = ov.ops[-1]
check("箭头已创建", arrow.kind == ARROW and arrow.a is not None and arrow.b is not None,
      f"a={arrow.a} b={arrow.b}")
tool("arrow")
click(750, 700)  # 选区内空白处取消选中
mid = to_logical(QPoint((arrow.a.x() + arrow.b.x()) // 2, (arrow.a.y() + arrow.b.y()) // 2))
click(*mid.toTuple())
check("点击箭头线可选中", ov.selected is arrow)
a0 = QPoint(arrow.a)
b0 = QPoint(arrow.b)
hb = op_handles(arrow, ov._handle_size())["b"].center()
new_pt = hb + QPoint(int(50 * d), int(-60 * d))
press(*to_logical(hb).toTuple())
move(*to_logical(new_pt).toTuple())
release(*to_logical(new_pt).toTuple())
check("拖动端点可调箭头", arrow.b != b0 and arrow.a == a0,
      f"a={arrow.a} b={b0}->{arrow.b}")
check("拖端点只改终点", arrow.b.x() != b0.x() or arrow.b.y() != b0.y(), f"{arrow.b}")

# 文字：创建、点击选中、双击改字、Delete
tool("text")
click(300, 550)
ed = ov._editor
check("文字编辑框打开", ed is not None)
if ed:
    ed.setText("SelectMe")
    key(Qt.Key.Key_Return)
tool("text")
text_op = [o for o in ov.ops if o.kind == TEXT][-1]
tc = to_logical(text_op.rect.center())
click(*tc.toTuple())
check("点击文字可选中", ov.selected is text_op)
dblclick(*tc.toTuple())
ed = ov._editor
check("双击文字打开编辑框", ed is not None and ed.text() == "SelectMe",
      f"editor={ed.text() if ed else None!r}")
if ed:
    ed.setText("EditedByDbl")
    QTest.keyClick(ed, Qt.Key.Key_Return)
settle(0.1)
check("双击修改原文字", any(o.text == "EditedByDbl" for o in ov.ops)
      and not any(o.text == "SelectMe" for o in ov.ops),
      f"{[o.text for o in ov.ops if o.kind == TEXT]}")

text_op = [o for o in ov.ops if o.kind == TEXT][-1]
click(*to_logical(text_op.rect.center()).toTuple())
check("修改后文字可再选中", ov.selected is text_op)
key(Qt.Key.Key_Delete)
check("Delete 删除文字", text_op not in ov.ops, f"{[(o.kind, o.text) for o in ov.ops]}")

# 双击空白不再保存
n_ops = len(ov.ops)
dblclick(750, 700)
check("双击空白不保存", not actions and ov.isVisible() and len(ov.ops) == n_ops,
      f"actions={actions} ops={len(ov.ops)}")

# Esc 只取消选中，不关闭遮罩
click(*to_logical(arrow.rect.center()).toTuple())
if ov.selected is None:
    click(*mid.toTuple())
key(Qt.Key.Key_Escape)
check("Esc 取消选中", ov.selected is None)
check("Esc 没有关闭遮罩", ov.isVisible() and not actions, f"visible={ov.isVisible()} actions={actions}")

check("选区始终未被破坏", ov.sel is not None and ov.sel.getRect() == sel0,
      f"{ov.sel.getRect() if ov.sel else None} vs {sel0}")

# Esc 清掉选区 → 同时清掉旧标注；重新框选是干净画布
key(Qt.Key.Key_Escape)
check("Esc 清掉选区与标注", ov.sel is None and len(ov.ops) == 0,
      f"sel={ov.sel} ops={len(ov.ops)}")
press(100, 100)
move(500, 450)
move(900, 800)
release(900, 800)
check("重新框选后画布为空", ov.sel is not None and len(ov.ops) == 0,
      f"sel={ov.sel.getRect() if ov.sel else None} ops={len(ov.ops)}")
tool("rect")
press(300, 300)
move(450, 380)
release(500, 420)
check("新一轮标注只保留新图形", len(ov.ops) == 1,
      f"{[(o.kind, o.rect.getRect()) for o in ov.ops]}")
tool("rect")
# 选区外点击直接 pass，不改选区也不重新框选
click(1000, 850)
check("框外点击直接 pass 不破坏选区与标注", len(ov.ops) == 1 and ov.sel is not None,
      f"ops={len(ov.ops)} sel={ov.sel.getRect() if ov.sel else None}")
# 按 Esc 清除选区后再重新框选
key(Qt.Key.Key_Escape)
press(1000, 100)
move(1200, 300)
move(1400, 700)
release(1400, 700)
check("重新框选自动清空旧标注", len(ov.ops) == 0 and ov.sel is not None,
      f"ops={len(ov.ops)} sel={ov.sel.getRect() if ov.sel else None}")

log = os.path.expanduser("~/.local/share/sniptool/paint-error.log")
check("没有绘制异常", not os.path.exists(log), log)

ov.finish("cancel")
app.processEvents()
print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
