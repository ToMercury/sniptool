"""回归测试：设置窗口保存/校验/开机自启；配置落在临时目录，不污染本机。"""
import json
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

tmp = tempfile.mkdtemp(prefix="sniptool_cfg_")
os.environ["SNITTOOL_CONFIG"] = os.path.join(tmp, "config.json")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")

from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sniptool import autostart
from sniptool.config import Config, config_path, parse_hotkey
from sniptool.settings import SettingsWindow

app = QApplication(sys.argv)
fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        fails.append(name)


cfg = Config()
win = SettingsWindow(cfg)
win.show()
app.processEvents()

check("设置窗口打开", win.isVisible() and win.tabs.count() == 3)
check("读取现有配置", win.save_template.text() == cfg.save_template
      and win.base_url.text() == cfg.translate.base_url)
check("设置窗口无边框", bool(win.windowFlags() & Qt.WindowType.FramelessWindowHint))
img = win.grab().toImage()
check("设置窗口圆角透明", img.pixelColor(0, 0).alpha() == 0
      and img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 200,
      f"corner={img.pixelColor(0, 0).getRgb()} center={img.pixelColor(img.width() // 2, img.height() // 2).getRgb()}")

# 拖标题栏移动窗口
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QMouseEvent

def mouse_ev(kind, local, global_, buttons):
    return QMouseEvent(kind, QPointF(local), QPointF(global_), Qt.MouseButton.LeftButton,
                       buttons, Qt.KeyboardModifier.NoModifier)

pos0 = win.pos()
start = QPoint(120, 20)
g0 = pos0 + start
win.eventFilter(win.title_label, mouse_ev(QMouseEvent.Type.MouseButtonPress, start, g0,
                                          Qt.MouseButton.LeftButton))
win.eventFilter(win.title_label, mouse_ev(QMouseEvent.Type.MouseMove, start + QPoint(40, 25),
                                          g0 + QPoint(40, 25), Qt.MouseButton.LeftButton))
win.eventFilter(win.title_label, mouse_ev(QMouseEvent.Type.MouseButtonRelease, start, g0,
                                          Qt.MouseButton.NoButton))
check("标题栏可拖动窗口", win.pos() != pos0, f"{pos0.toTuple()} -> {win.pos().toTuple()}")

# 修改并保存
save_dir = os.path.join(tmp, "shots")
win.save_dir.setText(save_dir)
win.save_template.setText("MyShot_%Y%m%d")
win.save_format.setCurrentIndex(win.save_format.findData("jpg"))
win.copy_after_save.setChecked(True)
win.hotkey_capture.setKeySequence(QKeySequence("Ctrl+Alt+B"))
win.hotkey_clipboard.setKeySequence(QKeySequence("Meta+V"))
win.api_key.setText("sk-test-key")
win.target_lang.setText("English")
win.history_limit.setValue(42)
win.history_days.setValue(3)
win.autostart.setChecked(True)

saved = []
win.saved.connect(lambda c: saved.append(c))
QTest.mouseClick(win.btn_save, Qt.MouseButton.LeftButton)
app.processEvents()

check("保存发出信号", bool(saved))
check("配置文件已写入", config_path().exists(), str(config_path()))
raw = json.loads(config_path().read_text(encoding="utf-8"))
check("保存目录已保存", raw["save_dir"] == save_dir, raw["save_dir"])
check("格式已保存", raw["save_format"] == "jpg", raw["save_format"])
check("保存后复制已保存", raw["copy_after_save"] is True)
check("截图热键已保存", raw["hotkey_capture"] == "ctrl+alt+b", raw["hotkey_capture"])
check("剪贴板热键 Meta→super", raw["hotkey_clipboard"] == "super+v", raw["hotkey_clipboard"])
check("API Key 已保存", raw["translate"]["api_key"] == "sk-test-key")
check("目标语言已保存", raw["translate"]["target_lang"] == "English")
check("历史上限已保存", raw["history_limit"] == 42, raw["history_limit"])
check("开机自启已写入 desktop", autostart.autostart_file().exists(),
      str(autostart.autostart_file()))
desktop = autostart.autostart_file().read_text(encoding="utf-8")
check("desktop 指向 sniptool daemon", "sniptool" in desktop and "daemon" in desktop,
      desktop.replace("\n", " | "))

loaded = Config.load()
check("重新加载配置一致", loaded.hotkey_capture == "ctrl+alt+b"
      and loaded.translate.api_key == "sk-test-key" and loaded.history_limit == 42)
check("热键可被解析", parse_hotkey(loaded.hotkey_capture)[1] == "b"
      and parse_hotkey(loaded.hotkey_clipboard)[1] == "v",
      f"{parse_hotkey(loaded.hotkey_capture)} / {parse_hotkey(loaded.hotkey_clipboard)}")

# 关闭自启
win2 = SettingsWindow(loaded)
win2.show()
app.processEvents()
check("自启状态回显", win2.autostart.isChecked())
win2.autostart.setChecked(False)
QTest.mouseClick(win2.btn_save, Qt.MouseButton.LeftButton)
app.processEvents()
check("关闭自启删除 desktop", not autostart.autostart_file().exists())

# 校验：两个热键相同不能保存
win3 = SettingsWindow(Config.load())
win3.show()
app.processEvents()
win3.hotkey_capture.setKeySequence(QKeySequence("Ctrl+Alt+A"))
win3.hotkey_clipboard.setKeySequence(QKeySequence("Ctrl+Alt+A"))
bad = []
win3.saved.connect(lambda c: bad.append(c))
QTest.mouseClick(win3.btn_save, Qt.MouseButton.LeftButton)
app.processEvents()
check("相同热键被拒绝", not bad and "不能相同" in win3.status.text(), win3.status.text())

# 校验：空热键被拒绝
win3.hotkey_clipboard.setKeySequence(QKeySequence(""))
QTest.mouseClick(win3.btn_save, Qt.MouseButton.LeftButton)
app.processEvents()
check("空热键被拒绝", not bad and "不能为空" in win3.status.text(), win3.status.text())

win.close()
win2.close()
win3.close()
app.processEvents()
print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
