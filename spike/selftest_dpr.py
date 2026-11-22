"""回归测试：高 DPI 下遮罩必须铺满整屏，不能只显示左上角一块截图。

复现的 bug：QScreen.grabWindow() 返回的图带 devicePixelRatio（本机 1.1458），
drawImage() 会再缩一次，导致遮罩里是「一小块冻结截图 + 右侧/底部实时桌面」，
看起来像两份桌面重叠（桌宠、输入框都出现两次）。框选后选区被按物理像素重绘，
所以选区内部反而正常。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication

from sniptool import imaging
from sniptool.overlay import MASK_ALPHA, Overlay

app = QApplication(sys.argv)
fails = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    if not ok:
        fails.append(name)


screen = QGuiApplication.primaryScreen()
dpr = float(screen.devicePixelRatio())
phys_w = int(screen.geometry().width() * dpr)
phys_h = int(screen.geometry().height() * dpr)

# 1) 真实抓图：DPR 必须归一为 1，尺寸仍是物理像素
real = imaging.grab_screen(screen)
check("真实抓图非空", not real.isNull(), f"{real.width()}x{real.height()}")
check("抓图 DPR 归一为 1", real.devicePixelRatio() == 1.0, f"dpr={real.devicePixelRatio()}")
check("抓图尺寸=物理分辨率", (real.width(), real.height()) == (phys_w, phys_h),
      f"{real.width()}x{real.height()} vs {phys_w}x{phys_h}")

# 2) 合成底图：故意带高 DPR，验证 Overlay 也会归一
base_color = QColor(200, 100, 50)
synth = QImage(phys_w, phys_h, QImage.Format.Format_RGB32)
synth.fill(base_color)
synth.setDevicePixelRatio(dpr)

ov = Overlay(synth, screen)
ov.show()
app.processEvents()
time.sleep(0.15)
app.processEvents()

check("Overlay 底图 DPR 归一", ov.base.devicePixelRatio() == 1.0,
      f"dpr={ov.base.devicePixelRatio()}")

shot = ov.grab().toImage()
# 取右下角（仍在物理底图 1920x1080 内，避开 grab 因逻辑高度取整多出的 1px）
x = min(phys_w - 5, shot.width() - 1)
y = min(phys_h - 10, shot.height() - 1)
c = shot.pixelColor(x, y)
masked = QColor(
    round(base_color.red() * (255 - MASK_ALPHA) / 255),
    round(base_color.green() * (255 - MASK_ALPHA) / 255),
    round(base_color.blue() * (255 - MASK_ALPHA) / 255),
)
# 底图铺满时该点应不透明且接近「底图 × 遮罩」；只显示左上角时该点是透明黑。
covered = c.alpha() > 240 and abs(c.red() - masked.red()) < 6 \
    and abs(c.green() - masked.green()) < 6 and abs(c.blue() - masked.blue()) < 6
check("遮罩右下角被底图覆盖", covered,
      f"({x},{y}) RGB{tuple(c.getRgb()[:3])} alpha={c.alpha()} 期望≈RGB{tuple(masked.getRgb()[:3])}")

# 3) 四角都应有底图覆盖（排除只覆盖左/上一条）
for name, (px, py) in {
    "左上": (5, 5),
    "右上": (phys_w - 5, 5),
    "左下": (5, phys_h - 10),
    "右下": (phys_w - 5, phys_h - 10),
}.items():
    cc = shot.pixelColor(min(px, shot.width() - 1), min(py, shot.height() - 1))
    check(f"遮罩{name}角不透明", cc.alpha() > 240, f"alpha={cc.alpha()}")

ov.finish("cancel")
app.processEvents()

print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
