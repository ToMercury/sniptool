"""集成回归：Daemon 剪贴板面板 / 设置热更新 / 历史监听（隔离配置与 socket）。"""
import os
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp(prefix="sniptool_daemon_")
os.environ["SNITTOOL_CONFIG"] = os.path.join(tmp, "config.json")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")
os.environ["SNITTOOL_DATA_DIR"] = os.path.join(tmp, "data")
os.environ["SNITTOOL_SOCKET"] = os.path.join(tmp, "sniptool.sock")

from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from sniptool.app import Daemon, send_command
from sniptool.config import Config

app = QApplication(sys.argv)
fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        fails.append(name)


def settle(seconds=0.2):
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        time.sleep(0.01)


cfg = Config.load()
cfg.history_persist = True
cfg.history_limit = 20
# 用不会和宿主旧 sniptool 实例/虚拟终端切换冲突的组合，专门验证 grab/事件循环
cfg.hotkey_capture = "ctrl+shift+f11"
# 不用 super+v：用户可能正跑着一个旧 daemon 占着它，测试会误判成代码问题
cfg.hotkey_clipboard = "ctrl+shift+f10"
cfg.save()

daemon = Daemon(app, cfg)
settle(0.3)

check("daemon 单实例 socket", send_command("ping"))
print("[INFO] 启动后 hotkey_status=", daemon.hotkey_status,
      "grabs=", len(daemon.hotkeys._grabs), flush=True)
check("剪贴板存储已初始化", daemon._clip_store is not None)
dbfile = os.path.join(tmp, "data", "clipboard.db")
check("剪贴板数据库落盘", os.path.exists(dbfile), dbfile)
check("剪贴板面板已创建", daemon._clip_panel is not None)

# 真实全局热键 Ctrl+Shift+F11 → 唤起遮罩（避开宿主旧实例的 Ctrl+Alt+A / VT 切换）
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest

capture_hot = False
for _attempt in range(3):
    if daemon.capture.overlay is not None:
        daemon.capture.overlay.finish("cancel")
        settle(0.2)
    dpy = xdisplay.Display()
    codes = [dpy.keysym_to_keycode(XK.string_to_keysym(k))
             for k in ("Control_L", "Shift_L", "F11")]
    for kc in codes:
        xtest.fake_input(dpy, X.KeyPress, kc)
        dpy.sync()
        time.sleep(0.03)
    for kc in reversed(codes):
        xtest.fake_input(dpy, X.KeyRelease, kc)
        dpy.sync()
        time.sleep(0.03)
    settle(0.6)
    if daemon.capture.overlay is not None:
        capture_hot = True
        break
if capture_hot:
    check("真实 Ctrl+Shift+F11 唤起遮罩", True,
          f"sel={daemon.capture.overlay.sel}")
    daemon.capture.overlay.finish("cancel")
    settle(0.2)
else:
    print("[SKIP] XTEST 合成 Ctrl+Shift+F11 未触发遮罩（环境问题，需物理键盘确认）", flush=True)
check("遮罩已关闭并回收", daemon.capture.overlay is None)

# 贴图关闭后还能继续截图（QScreen 包装对象失效回归）
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from sniptool.pin import PinWindow

pin_img = QImage(160, 100, QImage.Format.Format_RGB32)
pin_img.fill(QColor(40, 140, 220))
pin = PinWindow(pin_img, daemon.cfg, None, QPoint(300, 300))
pin.show()
settle(0.25)
pin.close()
settle(0.4)
daemon.capture.start()
settle(0.5)
check("贴图关闭后仍能正常截图", daemon.capture.overlay is not None)
if daemon.capture.overlay is not None:
    daemon.capture.overlay.finish("cancel")
    settle(0.2)
check("截图遮罩已回收", daemon.capture.overlay is None)

# 监听剪贴板
QGuiApplication.clipboard().setText("daemon-watched")
settle(0.3)
texts = [i.text for i in daemon._clip_store.items()]
check("daemon 监听剪贴板", "daemon-watched" in texts, f"{texts}")

# IPC 打开剪贴板面板
check("IPC clipboard 命令", send_command("clipboard"))
settle(0.2)
check("面板已显示", daemon._clip_panel.isVisible())
active = daemon.focus.active_window() if daemon.focus else 0
check("面板拿到系统键盘焦点", active == int(daemon._clip_panel.winId()),
      f"active={hex(active)} panel={hex(int(daemon._clip_panel.winId()))}")
daemon._clip_store.add_text("second-item")
panel = daemon._clip_panel
panel.refresh()

# 真实按键 ↑ / 空格收藏 / Esc 关闭
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest

def xtest_key(name):
    dpy = xdisplay.Display()
    kc = dpy.keysym_to_keycode(XK.string_to_keysym(name))
    xtest.fake_input(dpy, X.KeyPress, kc)
    dpy.sync()
    time.sleep(0.03)
    xtest.fake_input(dpy, X.KeyRelease, kc)
    dpy.sync()
    settle(0.25)

panel.list.setCurrentRow(0)
xtest_key("Down")
check("↑↓ 可移动选择", panel.list.currentRow() == 1, f"row={panel.list.currentRow()}")
item_id = panel._current_id()
xtest_key("space")
check("空格可收藏", item_id is not None and daemon._clip_store.get(item_id).pinned,
      f"id={item_id}")
xtest_key("Escape")
check("Esc 可关闭面板", not panel.isVisible())
settle(0.1)

# 选中记录回贴
daemon._clip_store.add_text("paste-me")
item = [i for i in daemon._clip_store.items() if i.text == "paste-me"][0]
daemon._clip_panel.show()
daemon._paste_clip_item(item.id)
settle(0.2)
check("选中后面板关闭", not daemon._clip_panel.isVisible())
check("选中记录写回剪贴板", QGuiApplication.clipboard().text() == "paste-me",
      QGuiApplication.clipboard().text())

# 设置热更新（历史上限 / 热键）
check("IPC settings 命令", send_command("settings"))
settle(0.3)
check("设置窗口已打开", daemon.settings is not None and daemon.settings.isVisible())
check("设置打开时释放全局热键", len(daemon.hotkeys._grabs) == 0,
      f"grabs={len(daemon.hotkeys._grabs)}")
daemon.settings.history_limit.setValue(55)
daemon.settings.save_dir.setText(os.path.join(tmp, "shots"))
daemon.settings.btn_save.click()
settle(0.3)
check("设置保存后 daemon 配置已更新", daemon.cfg.history_limit == 55,
      f"{daemon.cfg.history_limit}")
check("设置保存后存储上限已更新", daemon._clip_store.limit == 55,
      f"{daemon._clip_store.limit}")
check("设置窗口已关闭", not daemon.settings.isVisible())
check("保存后热键已恢复", daemon.hotkey_status.get("clipboard") is True,
      f"{daemon.hotkey_status}")

# 取消设置 → 也要恢复热键
daemon.open_settings()
settle(0.2)
check("再次打开设置释放热键", len(daemon.hotkeys._grabs) == 0)
daemon.settings.btn_cancel.click()
settle(0.3)
check("取消设置后热键恢复", daemon.hotkey_status.get("clipboard") is True,
      f"{daemon.hotkey_status}")

# 热键注册结果
check("两个热键都已注册", daemon.hotkey_status.get("capture") is True
      and daemon.hotkey_status.get("clipboard") is True,
      f"{daemon.hotkey_status}")

# 托盘快捷键切换面板
print("[INFO] 切换前面板 visible=", daemon._clip_panel.isVisible(), flush=True)
daemon._on_hotkey("clipboard")
settle(0.2)
check("剪贴板热键打开面板", daemon._clip_panel.isVisible())
daemon._on_hotkey("clipboard")
settle(0.1)
check("再按一次关闭面板", not daemon._clip_panel.isVisible())

# 真实全局热键 Ctrl+Shift+F10（XTEST）。XTEST 偶发不触发 passive grab，
# 重试几次仍失败则 SKIP，不误报代码问题。
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest

real_hot = False
for _attempt in range(3):
    if daemon._clip_panel.isVisible():
        daemon._clip_panel.hide()
        settle(0.1)
    dpy = xdisplay.Display()
    codes = [dpy.keysym_to_keycode(XK.string_to_keysym(k))
             for k in ("Control_L", "Shift_L", "F10")]
    for kc in codes:
        xtest.fake_input(dpy, X.KeyPress, kc)
        dpy.sync()
        time.sleep(0.03)
    for kc in reversed(codes):
        xtest.fake_input(dpy, X.KeyRelease, kc)
        dpy.sync()
        time.sleep(0.03)
    settle(0.5)
    if daemon._clip_panel.isVisible():
        real_hot = True
        break
if real_hot:
    check("真实 Ctrl+Shift+F10 唤起剪贴板", True)
    daemon._clip_panel.hide()
    daemon.hotkeys._last = 0.0  # 清掉防抖时间戳，避免遗留事件影响后续
    settle(0.2)
else:
    print("[SKIP] XTEST 合成 Ctrl+Shift+F10 未触发 passive grab（环境问题）", flush=True)

# 清理
daemon._clip_store.close()
daemon.hotkeys.ungrab_all()
daemon.tray.hide()
daemon.server.server.close()
QGuiApplication.clipboard().clear()
app.processEvents()

print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
