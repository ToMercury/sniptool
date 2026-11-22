"""验证 XTEST 注入 Ctrl+V：靶子是我自己建的 QLineEdit 窗口，不干扰用户窗口。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qtplugins"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLineEdit
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest

app = QApplication(sys.argv)
cb = QGuiApplication.clipboard()
orig = cb.text()

w = QLineEdit()
w.setWindowTitle("sniptool paste probe")
w.setGeometry(200, 200, 420, 48)
w.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
w.show()
w.raise_()
w.activateWindow()
w.setFocus()
for _ in range(20):
    app.processEvents()
    time.sleep(0.01)

wid = int(w.winId())
dpy = xdisplay.Display()

# 强制把输入焦点给靶子窗口（模拟"回到原窗口"这一步）
target = dpy.create_resource_object("window", wid)
target.set_input_focus(X.RevertToParent, X.CurrentTime)
dpy.sync()
dpy.set_input_focus(target, X.RevertToParent, X.CurrentTime)
dpy.sync()
time.sleep(0.15)

marker = "XTEST-PASTE-OK-7319"
cb.setText(marker)
dpy.sync()
time.sleep(0.1)

ctrl = dpy.keysym_to_keycode(XK.string_to_keysym("Control_L"))
vkey = dpy.keysym_to_keycode(XK.string_to_keysym("v"))

xtest.fake_input(dpy, X.KeyPress, ctrl)
xtest.fake_input(dpy, X.KeyPress, vkey)
xtest.fake_input(dpy, X.KeyRelease, vkey)
xtest.fake_input(dpy, X.KeyRelease, ctrl)
dpy.sync()

for _ in range(40):
    app.processEvents()
    time.sleep(0.01)

got = w.text()
print(f"靶子窗口收到文本长度={len(got)}", flush=True)
print(f"注入内容匹配={got == marker}", flush=True)
print(f"靶子实际内容={got!r}", flush=True)

if orig:
    cb.setText(orig)
else:
    cb.clear()
app.processEvents()
w.close()
print("RESULT:", "PASS" if got == marker else "FAIL", flush=True)
sys.exit(0 if got == marker else 1)
