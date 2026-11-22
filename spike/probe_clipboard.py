"""验证 XFixes 剪贴板监听 + Qt 读取的完整链路（测完恢复原剪贴板）。"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qtplugins"))

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from Xlib import X, display as xdisplay
from Xlib.ext import xfixes

app = QApplication(sys.argv)
cb = QGuiApplication.clipboard()
orig = cb.text()
print(f"原剪贴板文本长度={len(orig)}（内容不打印）", flush=True)

dpy = xdisplay.Display()
root = dpy.screen().root
CLIPBOARD = dpy.intern_atom("CLIPBOARD")

xfixes.query_version(dpy)
xfixes.select_selection_input(dpy, root, int(CLIPBOARD), xfixes.XFixesSetSelectionOwnerNotifyMask)
dpy.sync()
print("已注册 XFixes CLIPBOARD 监听", flush=True)

marker = "sniptool-spike-marker-42"
cb.setText(marker)
app.processEvents()

events = []
deadline = time.time() + 2.0
while time.time() < deadline:
    app.processEvents()
    while dpy.pending_events():
        ev = dpy.next_event()
        events.append(ev)
    if events:
        break
    time.sleep(0.01)

print(f"2 秒内收到 {len(events)} 个事件", flush=True)
saw_selection = False
for ev in events:
    name = type(ev).__name__
    if "Selection" in name or "selection" in name:
        saw_selection = True
    print(f"  event={name} type={getattr(ev, 'type', '?')}", flush=True)

# Qt 侧读回验证
app.processEvents()
readback = cb.text()
print(f"读回一致={readback == marker} 长度={len(readback)}", flush=True)

# 恢复原剪贴板
if orig:
    cb.setText(orig)
else:
    cb.clear()
app.processEvents()
print(f"已恢复原剪贴板: {'是' if (cb.text() == orig) else '否'}", flush=True)

print("RESULT:", "PASS" if (saw_selection and readback == marker) else "FAIL", flush=True)
sys.exit(0 if (saw_selection and readback == marker) else 1)
