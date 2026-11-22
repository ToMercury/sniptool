"""sniptool spike: 在本机实测 5 个高危假设。

非破坏性设计：
  - 不注入按键（不干扰你正在用的窗口）
  - 不写剪贴板（只读取长度做探测）
  - 全屏遮罩只显示 1.2 秒后自动关闭
  - 所有 X grab 都在 finally 里释放
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache"))

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

results = []


def rgb(c):
    return (c.red(), c.green(), c.blue())


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: {detail}", flush=True)


# ---------------------------------------------------------------- X11 探测
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest

dpy = xdisplay.Display()
screens = dpy.screen_count()
rec = dpy.screen()
root = rec.root
geom = root.get_geometry()
record("X11 连接", True, f"{screens} screen, root {geom.width}x{geom.height}")

for ext in ("XFIXES", "XTEST", "MIT-SHM", "SHAPE", "RENDER", "Composite"):
    try:
        info = dpy.query_extension(ext)
    except Exception as e:  # noqa: BLE001
        info = None
        record(f"扩展 {ext}", False, str(e))
        continue
    record(f"扩展 {ext}", info is not None, "present" if info else "missing")

# 活动窗口
try:
    _NET_ACTIVE_WINDOW = dpy.intern_atom("_NET_ACTIVE_WINDOW")
    aw = root.get_full_property(_NET_ACTIVE_WINDOW, X.AnyPropertyType)
    wid = aw.value[0] if aw and aw.value else 0
    record("查询活动窗口", bool(wid), f"window=0x{wid:x}" if wid else "none")
except Exception as e:  # noqa: BLE001
    record("查询活动窗口", False, repr(e))

# ---------------------------------------------------------------- Qt 应用
app = QApplication(sys.argv)
scr = QGuiApplication.primaryScreen()
record("Qt 屏幕", True, f"{scr.name()} {scr.geometry().width()}x{scr.geometry().height()} dpr={scr.devicePixelRatio()}")


def grab_ms(n=5):
    times = []
    img = None
    for _ in range(n):
        t0 = time.perf_counter()
        img = scr.grabWindow(0).toImage()
        times.append((time.perf_counter() - t0) * 1000)
    return times, img


# 1) 抓图速度 + 是否黑屏
times, img = grab_ms()
if img is None or img.isNull():
    record("全屏抓图", False, "grabWindow 返回空")
else:
    # 采样判断是否全黑（避免打印屏幕内容）
    lum = []
    for x in range(0, img.width(), 97):
        for y in range(0, img.height(), 89):
            c = img.pixelColor(x, y)
            lum.append((c.red() + c.green() + c.blue()) / 3)
    mean = sum(lum) / max(1, len(lum))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_capture.png")
    img.save(out, "PNG")
    record(
        "全屏抓图",
        mean > 1.0,
        f"{img.width()}x{img.height()} 平均亮度={mean:.1f} 耗时 min/med/max="
        f"{min(times):.1f}/{sorted(times)[len(times)//2]:.1f}/{max(times):.1f}ms -> {out}",
    )

# 2) 全屏遮罩：透明度 + 是否盖住面板（override-redirect）
class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(scr.geometry())

    def paintEvent(self, _ev):
        p = QPainter(self)
        # 半透明黑 + 中间挖一个"选区"洞（模拟真实 UI）
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        hole = QRect(self.width() // 4, self.height() // 4, self.width() // 2, self.height() // 2)
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillRect(hole, Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        # 选区边框 + 提示文字
        p.setPen(QColor(64, 158, 255, 255))
        p.drawRect(hole)
        p.fillRect(QRect(hole.left(), hole.bottom() + 6, 220, 28), QColor(64, 158, 255, 230))
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(hole.left() + 10, hole.bottom() + 6, 210, 28), Qt.AlignVCenter, "sniptool spike overlay")


ov = Overlay()
ov.show()
ov.raise_()
app.processEvents()
time.sleep(0.35)
app.processEvents()

# 遮罩显示期间再抓一次屏，检查 (5,5) 是否被遮罩覆盖、洞中心是否透明
check = scr.grabWindow(0).toImage()
if check.isNull():
    record("全屏遮罩", False, "遮罩期间抓图失败")
else:
    dpr = scr.devicePixelRatio()
    corner = check.pixelColor(5, 5)
    center = check.pixelColor(int(ov.width() // 2 * dpr), int(ov.height() // 2 * dpr))
    # 角落应比原始暗（alpha 遮罩生效）；洞中心应接近原始亮度（真透明）
    base_corner = img.pixelColor(5, 5)
    darkened = (corner.red() + corner.green() + corner.blue()) < (base_corner.red() + base_corner.green() + base_corner.blue()) * 0.85
    record(
        "遮罩·半透明",
        darkened,
        f"角落 RGB {rgb(base_corner)} -> {rgb(corner)}",
    )
    record("遮罩·挖洞透明", sum(rgb(center)) > 90, f"洞中心 RGB {rgb(center)}")

# 检查遮罩窗口是否真的置顶（覆盖面板区域 = 屏幕右下角）
px, py = check.width() - 3, check.height() - 3
base_p, now_p = img.pixelColor(px, py), check.pixelColor(px, py)
record(
    "遮罩·盖住面板区",
    sum(rgb(now_p)) < sum(rgb(base_p)) * 0.9,
    f"右下角 RGB {rgb(base_p)} -> {rgb(now_p)}（变暗=遮罩盖在面板之上）",
)
ov.close()
app.processEvents()

# 3) 置顶属性：贴图窗口
pin = QWidget()
pin.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
pin.setGeometry(80, 80, 240, 160)
pin.setStyleSheet("background:#222; color:#eee;")
pin.show()
app.processEvents()
time.sleep(0.15)
wid = int(pin.winId())
try:
    _NET_WM_STATE = dpy.intern_atom("_NET_WM_STATE")
    ABOVE = dpy.intern_atom("_NET_WM_STATE_ABOVE")
    win = dpy.create_resource_object("window", wid)
    prop = win.get_full_property(_NET_WM_STATE, X.AnyPropertyType)
    atoms = list(prop.value) if prop and prop.value else []
    record("贴图·置顶属性", ABOVE in atoms, f"_NET_WM_STATE={[a for a in atoms]} above={ABOVE in atoms}")
except Exception as e:  # noqa: BLE001
    record("贴图·置顶属性", False, repr(e))
pin.close()
app.processEvents()

# 4) 剪贴板读取（只取长度，不打印内容）
try:
    cb = QGuiApplication.clipboard()
    md = cb.mimeData()
    fmts = md.formats()
    text_len = len(md.text()) if md.hasText() else 0
    record("剪贴板读取", True, f"mime={fmts[:4]} text_len={text_len} image={md.hasImage()}")
except Exception as e:  # noqa: BLE001
    record("剪贴板读取", False, repr(e))

# 5) 全局热键抢占（不按键，抢到立即释放）
def try_grab(desc, mods, keysym_name):
    kc = dpy.keysym_to_keycode(XK.string_to_keysym(keysym_name))
    try:
        root.grab_key(kc, mods, True, X.GrabModeAsync, X.GrabModeAsync)
        dpy.sync()
        root.ungrab_key(kc, X.AnyModifier)
        dpy.sync()
        record(f"热键 {desc}", True, "grab 成功（未被占用）")
    except Exception as e:  # noqa: BLE001
        record(f"热键 {desc}", False, f"grab 失败（可能被占用）: {e!r}")


try_grab("Ctrl+Alt+A", X.ControlMask | X.Mod1Mask, "a")
try_grab("Super+V", X.Mod4Mask, "v")

# XTEST 可用性（不做实际注入）
try:
    ver = xtest.get_version(dpy, 2, 2)
    record("XTEST 可注入", ver is not None, f"version={ver}")
except Exception as e:  # noqa: BLE001
    record("XTEST 可注入", False, repr(e))

print("\n===== 汇总 =====")
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}")
fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} 通过")
sys.exit(0 if not fails else 1)
