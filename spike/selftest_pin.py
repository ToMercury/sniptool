"""回归测试：贴图窗口（置顶、拖动、等比缩放、滚轮/菜单缩放、复制/保存、双击关闭）。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QGuiApplication
from PySide6.QtWidgets import QApplication

from sniptool.config import Config
from sniptool.pin import PinWindow

app = QApplication(sys.argv)
fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        fails.append(name)


def settle(seconds=0.1):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.01)


img = QImage(400, 300, QImage.Format.Format_RGB32)
img.fill(QColor(30, 120, 220))

cfg = Config()
tmp = tempfile.mkdtemp(prefix="sniptool_pin_")
cfg.save_dir = tmp
cfg.save_template = "Pin_%H%M%S"

pin = PinWindow(img, cfg, None, QPoint(250, 180))
closed = []
pin.closed.connect(lambda: closed.append(True))
pin.show()
settle(0.15)

check("贴图窗口置顶", bool(pin.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
pimg = pin.grab().toImage()
check("贴图窗口圆角透明", pimg.pixelColor(0, 0).alpha() == 0
      and pimg.pixelColor(pimg.width() // 2, pimg.height() // 2).alpha() > 200,
      f"corner={pimg.pixelColor(0, 0).getRgb()} center={pimg.pixelColor(pimg.width() // 2, pimg.height() // 2).getRgb()}")
dpr = pin.dpr
expect_w = int(round(img.width() / dpr)) + 2
check("初始尺寸按 dpr 显示", abs(pin.width() - expect_w) <= 2, f"{pin.width()} vs {expect_w}")

g0 = pin.geometry().getRect()


def mev(kind, local, buttons):
    global_pos = pin.pos() + local
    return QMouseEvent(kind, QPointF(local), QPointF(global_pos), Qt.MouseButton.LeftButton,
                       buttons, Qt.KeyboardModifier.NoModifier)


# 拖动移动
p = QPoint(pin.width() // 2, pin.height() // 2)
pin.mousePressEvent(mev(QMouseEvent.Type.MouseButtonPress, p, Qt.MouseButton.LeftButton))
g = pin.pos() + p + QPoint(80, 50)
pin.mouseMoveEvent(mev(QMouseEvent.Type.MouseMove, g - pin.pos(), Qt.MouseButton.LeftButton))
pin.mouseReleaseEvent(mev(QMouseEvent.Type.MouseButtonRelease, g - pin.pos(), Qt.MouseButton.NoButton))
settle()
check("贴图可拖动移动", pin.geometry().getRect() != g0,
      f"{g0} -> {pin.geometry().getRect()}")

# 右下角拖动等比缩放
w0, h0 = pin.width(), pin.height()
corner = QPoint(w0 - 2, h0 - 2)
asp0 = w0 / h0
pin.mousePressEvent(mev(QMouseEvent.Type.MouseButtonPress, corner, Qt.MouseButton.LeftButton))
target = corner + QPoint(120, 90)
pin.mouseMoveEvent(mev(QMouseEvent.Type.MouseMove, target, Qt.MouseButton.LeftButton))
pin.mouseReleaseEvent(mev(QMouseEvent.Type.MouseButtonRelease, target, Qt.MouseButton.NoButton))
settle()
asp1 = pin.width() / pin.height()
check("拖动边缘可放大", pin.width() > w0 and pin.height() > h0,
      f"{w0}x{h0} -> {pin.width()}x{pin.height()}")
check("缩放保持图片比例", abs(asp1 - asp0) < 0.03, f"{asp0:.3f} -> {asp1:.3f}")

# 菜单缩放
before = pin.geometry().getRect()
pin._zoom_to(1.5)
check("菜单缩放 150%", pin.width() > before[2], f"{before} -> {pin.geometry().getRect()}")

# 复制
pin.copy_image()
settle(0.05)
clip = QGuiApplication.clipboard().image()
check("贴图复制到剪贴板", not clip.isNull() and clip.width() == img.width(),
      f"{clip.width()}x{clip.height()}")

# 保存
pin.save_image()
files = [f for f in os.listdir(tmp) if f.endswith(".png")]
check("贴图可保存", len(files) == 1, f"{files}")

# 双击关闭
pin.mouseDoubleClickEvent(mev(QMouseEvent.Type.MouseButtonDblClick, p, Qt.MouseButton.NoButton))
settle(0.1)
check("双击关闭贴图", bool(closed) and len(closed) == 1, f"closed={closed}")

print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
