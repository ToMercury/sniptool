"""用假的 translate_stream 测翻译面板 UI，不消耗 API token。"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins
setup_qt_plugins()
from PySide6.QtWidgets import QApplication
import sniptool.translate as tr

app = QApplication(sys.argv)
fails = []
def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}  {d}", flush=True)
    if not c: fails.append(n)

calls = {"n": 0}
def fake_stream(cfg, text, on_delta, timeout=120.0):
    calls["n"] += 1
    out = []
    for chunk in ("这是", "一段", "测试译文"):
        time.sleep(0.05)
        out.append(chunk); on_delta(chunk)
    return "".join(out)
tr.translate_stream = fake_stream

from sniptool.config import Config
from sniptool.translate_panel import TranslatePanel

cfg = Config()
cfg.translate.api_key = "fake"
panel = TranslatePanel(cfg, "This is a test.")
panel.show()
t = time.time()
while time.time() - t < 1.5:
    app.processEvents(); time.sleep(0.02)

check("流式增量已显示", panel.dst.toPlainText() == "这是一段测试译文", repr(panel.dst.toPlainText()))
check("状态显示完成", "完成" in panel.status.text(), panel.status.text())
check("原文展示正确", panel.src.toPlainText() == "This is a test.")

img = panel.grab().toImage()
check("面板可渲染", not img.isNull(), f"{img.width()}x{img.height()}")
check("翻译面板圆角透明", img.pixelColor(0, 0).alpha() == 0
      and img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 200,
      f"corner={img.pixelColor(0, 0).getRgb()} center={img.pixelColor(img.width() // 2, img.height() // 2).getRgb()}")
accent = 0
for y in range(0, img.height(), 2):
    c = img.pixelColor(1, y)
    if abs(c.red()-64) < 40 and abs(c.green()-158) < 40 and abs(c.blue()-255) < 40: accent += 1
check("左侧强调条存在", accent > 5, f"{accent}")

# 复制译文
panel._copy_dst()
from PySide6.QtGui import QGuiApplication
check("复制译文按钮", QGuiApplication.clipboard().text() == "这是一段测试译文")

# 错误路径
def fake_err(cfg, text, on_delta, timeout=120.0):
    raise tr.TranslateError("HTTP 401: bad key")
tr.translate_stream = fake_err
panel.restart()
t = time.time()
while time.time() - t < 0.8:
    app.processEvents(); time.sleep(0.02)
check("错误提示可见", "失败" in panel.status.text() or "失败" in panel.dst.toPlainText(),
      panel.status.text()[:60])
check("只发起预期次数", calls["n"] == 1, f"真实请求次数={calls['n']}")

panel.close(); app.processEvents()
print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
