"""对比两种全屏遮罩方案的：是否能盖住面板 / 是否能自然拿到键盘焦点。"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qtplugins"))
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from Xlib import X, display as xdisplay

app = QApplication(sys.argv)
scr = QGuiApplication.primaryScreen()
geo = scr.geometry()

def rgb(c): return (c.red(), c.green(), c.blue())

class Ov(QWidget):
    def __init__(self, mode):
        super().__init__()
        if mode == "bypass":
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.X11BypassWindowManagerHint)
        elif mode == "fullscreen":
            self.setWindowFlags(Qt.FramelessWindowHint)
        else:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(geo)
        self.mode = mode
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))

def test(mode):
    base = scr.grabWindow(0).toImage()
    ov = Ov(mode); ov.show()
    if mode == "fullscreen":
        ov.showFullScreen()
    ov.raise_()
    t0 = time.time()
    while time.time() - t0 < 0.5:
        app.processEvents(); time.sleep(0.01)
    img = scr.grabWindow(0).toImage()
    # 面板区右下角
    px, py = img.width()-3, img.height()-3
    b, n = base.pixelColor(px, py), img.pixelColor(px, py)
    covered = sum(rgb(n)) < sum(rgb(b)) * 0.9
    # 键盘焦点：Qt 认为自己是活动窗口？
    active = app.activeWindow() is ov
    dpy = xdisplay.Display()
    focused = dpy.get_input_focus().focus
    fid = focused.id if hasattr(focused, "id") else 0
    is_us = fid == int(ov.winId())
    dpy.close()
    ov.close(); app.processEvents(); time.sleep(0.2)
    print(f"{mode:11} 盖住面板={covered}  Qt活动={active}  X焦点=0x{fid:x} 焦点在我们={is_us}  面板RGB {rgb(b)}->{rgb(n)}")
    return covered, is_us

for m in ("bypass", "fullscreen", "tool_top"):
    try:
        test(m)
    except Exception as e:
        print(m, "ERROR", repr(e))
