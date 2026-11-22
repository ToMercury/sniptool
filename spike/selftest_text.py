"""回归测试：文字工具。

覆盖用户反馈的 4 个问题：
- 回车提交文字不能继续冒泡成 Enter=保存（否则遮罩/边框直接消失）
- 同一选区连续添加多次文字，选区边框必须一直在
- 再次点击“文字”取消后，编辑框必须关闭，不能继续输入
- 切换工具/点选区外/Esc 时编辑框的收尾行为
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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


def border_pixels() -> int:
    """数选区蓝色边框上的强调色像素（物理坐标直接采样 grab 图）。"""
    if ov.sel is None:
        return 0
    img = ov.grab().toImage()
    n = 0
    sel = ov.sel
    for x in range(sel.left(), sel.right() + 1):
        for y in (sel.top(), sel.bottom()):
            c = img.pixelColor(min(x, img.width() - 1), min(y, img.height() - 1))
            if abs(c.red() - 64) < 55 and abs(c.green() - 158) < 55 and abs(c.blue() - 255) < 55:
                n += 1
    for y in range(sel.top(), sel.bottom() + 1):
        for x in (sel.left(), sel.right()):
            c = img.pixelColor(min(x, img.width() - 1), min(y, img.height() - 1))
            if abs(c.red() - 64) < 55 and abs(c.green() - 158) < 55 and abs(c.blue() - 255) < 55:
                n += 1
    return n


def select_region():
    # 直接构造带按键状态的鼠标事件：QTest.mouseMove 不带 buttons，
    # 遮罩会把它当成悬停而不是拖拽。
    def ev(kind, x, y, buttons):
        return QMouseEvent(kind, QPointF(x, y), QPointF(x, y),
                           Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)

    ov.mousePressEvent(ev(QMouseEvent.Type.MouseButtonPress, 100, 100, Qt.MouseButton.LeftButton))
    ov.mouseMoveEvent(ev(QMouseEvent.Type.MouseMove, 300, 250, Qt.MouseButton.LeftButton))
    ov.mouseMoveEvent(ev(QMouseEvent.Type.MouseMove, 500, 400, Qt.MouseButton.LeftButton))
    ov.mouseReleaseEvent(ev(QMouseEvent.Type.MouseButtonRelease, 500, 400, Qt.MouseButton.NoButton))
    settle()


def click_tool(name):
    QTest.mouseClick(ov.toolbar._buttons[name], Qt.MouseButton.LeftButton)
    settle(0.06)


def open_editor(pos):
    QTest.mouseClick(ov, Qt.MouseButton.LeftButton, pos=pos)
    settle(0.06)
    return ov._editor


select_region()
check("框选成立", ov.sel is not None, f"{ov.sel.getRect() if ov.sel else None}")
border0 = border_pixels()
check("初始选区边框存在", border0 > 100, f"像素={border0}")

# 0) 文字工具激活后拖拽：不能改选区、不能弹编辑框（用户反馈的拖拽 bug）
click_tool("text")
sel_before = ov.sel.getRect()
QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=QPoint(150, 150))
QTest.mouseMove(ov, QPoint(260, 240))
QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=QPoint(260, 240))
settle(0.1)
check("文字工具拖拽不改变选区", ov.sel is not None and ov.sel.getRect() == sel_before,
      f"{ov.sel.getRect() if ov.sel else None} vs {sel_before}")
check("文字工具拖拽不弹编辑框", ov._editor is None, f"editor={ov._editor}")
check("文字工具拖拽不产生文字", not ov.ops, f"ops={[(o.kind, o.text) for o in ov.ops]}")
check("文字工具拖拽后边框仍在", border_pixels() > border0 * 0.5, f"像素={border_pixels()}/{border0}")
click_tool("text")  # 退出拖拽测试用的文字工具
check("拖拽测试后文字工具已关闭", ov.active_tool == "", f"active={ov.active_tool!r}")

# 1) 回车提交：不能触发保存/关闭遮罩
click_tool("text")
ed = open_editor(QPoint(160, 160))
check("文字编辑框打开且可见", ed is not None and ed.isVisible(), f"editor={ed is not None}")
if ed:
    QTest.keyClicks(ed, "Hello")
    QTest.keyClick(ed, Qt.Key.Key_Return)
settle(0.15)
check("回车只提交文字", len(ov.ops) == 1 and ov.ops[0].text == "Hello",
      f"ops={[(o.kind, o.text) for o in ov.ops]}")
check("回车没有触发保存/关闭", not actions and ov.isVisible(),
      f"actions={actions} visible={ov.isVisible()}")
check("回车后边框仍在", border_pixels() > border0 * 0.5, f"像素={border_pixels()}/{border0}")

# 2) 连续多次添加文字，边框不能消失（位置错开，不能落在已有文字框里）
for i, y in enumerate((210, 260, 310), start=2):
    ed = open_editor(QPoint(150, y))
    if ed:
        ed.setText(f"text{i}")
        QTest.keyClick(ed, Qt.Key.Key_Return)
    settle(0.1)
    check(f"第 {i} 次文字后边框仍在", border_pixels() > border0 * 0.5,
          f"ops={len(ov.ops)} 像素={border_pixels()}/{border0}")
check("连续文字都保留", len(ov.ops) == 4, f"{[o.text for o in ov.ops]}")
check("连续文字没有触发保存", not actions and ov.isVisible(), f"actions={actions}")

# 2.1) 再次点击原来的文字框：应该修改原文字，而不是新增一条
target = ov.ops[0]
n_before = len(ov.ops)
center = QPoint(int(target.rect.center().x() / ov.dpr), int(target.rect.center().y() / ov.dpr))
ed = open_editor(center)
check("点已有文字打开编辑框", ed is not None and ed.isVisible())
check("编辑框预填原文字", ed is not None and ed.text() == target.text,
      f"editor={ed.text() if ed else None!r}")
if ed:
    tr = ov._logical(target.rect)
    check("编辑框与原文字框重合（不是两个框）",
          abs(ed.x() - tr.x()) <= 3 and abs(ed.y() - tr.y()) <= 3
          and abs(ed.width() - tr.width()) <= 3 and abs(ed.height() - tr.height()) <= 3,
          f"editor={ed.geometry().getRect()} text={tr.getRect()}")
    check("编辑框使用圆角样式",
          "border-radius" in ed.styleSheet() and "dashed" not in ed.styleSheet())
if ed:
    ed.setText("HelloEdited")
    QTest.keyClick(ed, Qt.Key.Key_Return)
settle(0.1)
check("修改文字不新增标注", len(ov.ops) == n_before, f"{len(ov.ops)} vs {n_before}")
check("原文字已被修改", ov.ops[0].text == "HelloEdited", f"{[o.text for o in ov.ops]}")
check("修改文字后边框仍在", border_pixels() > border0 * 0.5, f"像素={border_pixels()}/{border0}")

# 3) 再次点击“文字”取消：编辑框必须关闭，且文字已收尾
check("连续文字后文字工具仍激活", ov.active_tool == "text", f"active={ov.active_tool!r}")
ed = open_editor(QPoint(300, 220))
check("取消前编辑框存在", ed is not None and ed.isVisible())
if ed:
    QTest.keyClicks(ed, "CancelMe")
click_tool("text")  # 取消工具
check("取消文字工具后编辑框关闭", ov._editor is None, f"active={ov.active_tool!r}")
check("取消时已输入文字被提交", any(o.text == "CancelMe" for o in ov.ops),
      f"{[o.text for o in ov.ops]}")
check("取消后不能继续输入", ov._editor is None and ov.active_tool == "",
      f"active={ov.active_tool!r} editor={ov._editor}")
check("取消后边框仍在", border_pixels() > border0 * 0.5, f"像素={border_pixels()}/{border0}")

# 4) 编辑中按 Esc：提交当前文字 + 关闭文字工具
click_tool("text")
check("重新打开文字工具", ov.active_tool == "text", f"active={ov.active_tool!r}")
ed = open_editor(QPoint(350, 320))
n_before = len(ov.ops)
if ed:
    QTest.keyClicks(ed, "EscKeep")
    QTest.keyClick(ed, Qt.Key.Key_Escape)
settle(0.1)
check("Esc 关闭编辑框", ov._editor is None)
check("Esc 提交当前文字", len(ov.ops) == n_before + 1 and any(o.text == "EscKeep" for o in ov.ops),
      f"{[o.text for o in ov.ops]}")
check("Esc 关闭文字工具", ov.active_tool == "", f"active={ov.active_tool!r}")

# 5) 编辑中点选区外：收尾当前文字，不新建
click_tool("text")
ed = open_editor(QPoint(300, 300))
n_before = len(ov.ops)
if ed:
    QTest.keyClicks(ed, "Outside")
QTest.mouseClick(ov, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
settle(0.1)
check("点选区外关闭编辑框", ov._editor is None)
check("点选区外提交当前文字", len(ov.ops) == n_before + 1 and any(o.text == "Outside" for o in ov.ops),
      f"{[o.text for o in ov.ops]}")
check("点选区外后边框仍在", border_pixels() > border0 * 0.5, f"像素={border_pixels()}/{border0}")

# 6) 编辑中切到矩形：文字收尾、工具切换
ed = open_editor(QPoint(450, 220))
if ed:
    ed.setText("ToRect")
click_tool("rect")
check("切矩形后编辑框关闭", ov._editor is None)
check("切矩形前文字已提交", any(o.text == "ToRect" for o in ov.ops))
check("工具已切到矩形", ov.active_tool == "rect", f"active={ov.active_tool!r}")
check("切矩形后边框仍在", border_pixels() > border0 * 0.5, f"像素={border_pixels()}/{border0}")

log = os.path.expanduser("~/.local/share/sniptool/paint-error.log")
check("没有绘制异常", not os.path.exists(log), log)

ov.finish("cancel")
app.processEvents()
print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
