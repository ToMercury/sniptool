"""回归测试：实时预览、箭头方向、边框保持、裁剪、文件名冲突、无绘制异常。"""
import glob, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from sniptool.bootstrap import setup_qt_plugins
setup_qt_plugins()
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QGuiApplication
from PySide6.QtWidgets import QApplication
from sniptool import imaging
from sniptool.annotations import ARROW, RECT, TEXT
from sniptool.config import Config, data_dir
from sniptool.overlay import Overlay

app = QApplication(sys.argv)
fails = []
def check(n, c, d=""):
    print(f"[{'PASS' if c else 'FAIL'}] {n}  {d}", flush=True)
    if not c: fails.append(n)

from sniptool.config import data_dir as _dd
try:
    (_dd()/"paint-error.log").unlink()
except OSError:
    pass

base = imaging.grab_screen()
ov = Overlay(base)
ov.show(); app.processEvents(); time.sleep(0.2); app.processEvents()

def press(x,y): ov.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress,QPointF(x,y),QPointF(x,y),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
def move(x,y): ov.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove,QPointF(x,y),QPointF(x,y),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
def release(x,y): ov.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease,QPointF(x,y),QPointF(x,y),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
def click(x,y): press(x,y); release(x,y)
def settle(s=0.15):
    t=time.time()
    while time.time()-t<s: app.processEvents(); time.sleep(0.01)

def count_color(img, rgb_, tol=45):
    r,g,b = rgb_
    n=0
    for x in range(0, img.width(), 2):
        for y in range(0, img.height(), 2):
            c=img.pixelColor(x,y)
            if abs(c.red()-r)<tol and abs(c.green()-g)<tol and abs(c.blue()-b)<tol: n+=1
    return n

# 框选
press(200,180); move(420,320); move(760,600); release(760,600); settle()
check("框选成立", ov.sel is not None, f"{ov.sel.getRect() if ov.sel else None}")

# --- 实时预览（矩形） ---
ov.toolbar._emit("rect"); settle()
press(300,280); move(360,320); move(430,380)
settle(0.2)
prev = ov.grab().toImage()
red_prev = count_color(prev, (255,59,48))
check("矩形拖拽中有实时预览", red_prev > 10, f"红色像素={red_prev}")
release(430,380); settle()
check("矩形已提交", len(ov.ops)==1 and ov.ops[-1].kind==RECT)

# --- 实时预览 + 箭头方向（右下 -> 左上） ---
ov.toolbar._emit("arrow"); settle()
press(700,560); move(600,470); move(480,380)
settle(0.2)
prev2 = ov.grab().toImage()
red_prev2 = count_color(prev2, (255,59,48))
check("箭头拖拽中有实时预览", red_prev2 > 10, f"红色像素={red_prev2}")
release(480,380); settle()
ar = ov.ops[-1]
check("箭头方向被保留", ar.kind==ARROW and ar.a is not None and ar.b is not None,
      f"a={ar.a} b={ar.b}")
check("起点在右下、终点在左上", ar.a.x()>ar.b.x() and ar.a.y()>ar.b.y(), f"{ar.a}->{ar.b}")

# --- 边框在加完标注后仍然存在 ---
ov.toolbar.show(); ov.toolbar.raise_(); settle(0.1)
shot = ov.grab().toImage()
accent = count_color(shot, (64,158,255), tol=40)
check("加标注后选区边框仍在", accent > 60, f"强调色像素={accent}")
check("工具栏仍可见", ov.toolbar.isVisible(), f"{ov.toolbar.geometry().getRect()}")

# --- 两次文字 ---
ov.toolbar._emit("text"); settle()
click(260,240)
if ov._editor: ov._editor.setText("第一段"); ov._close_editor(commit=True)
settle()
click(320,560)
if ov._editor: ov._editor.setText("第二段"); ov._close_editor(commit=True)
settle()
texts = [o for o in ov.ops if o.kind==TEXT]
check("两次文字都保留", len(texts)==2, f"{[o.text for o in texts]}")
ov.toolbar.show(); ov.toolbar.raise_(); settle(0.1)
shot2 = ov.grab().toImage()
check("两次文字后边框仍在", count_color(shot2,(64,158,255),40) > 60,
      f"强调色={count_color(shot2,(64,158,255),40)}")

# --- 标注裁剪：画到选区外的标注不应出现在遮罩区 ---
ov.ops.append(type(ov.ops[0])(kind=RECT, rect=QRect(ov.sel.right()-20, ov.sel.bottom()-20, 400, 300),
                              color="#ff3b30", width=ov.line_width))
ov.update(); settle(0.15)
shot3 = ov.grab().toImage()
d = ov.dpr
out_probe = shot3.pixelColor(min(int((ov.sel.right()+60)/d), shot3.width()-1),
                             min(int((ov.sel.bottom()+60)/d), shot3.height()-1))
c = out_probe
is_red = abs(c.red()-255)<50 and c.green()<100 and c.blue()<100
check("标注被裁剪在选区内", not is_red, f"选区外像素 RGB{tuple(c.getRgb()[:3])}")

# --- 无绘制异常日志 ---
log = data_dir()/"paint-error.log"
check("没有绘制异常", not log.exists(), str(log))

# --- 文件名冲突 ---
cfg = Config()
cfg.save_dir = str(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".test", "saved2"))
os.makedirs(cfg.save_dir, exist_ok=True)
for f in glob.glob(os.path.join(cfg.save_dir, "*.png")): os.remove(f)
img = ov.result_image()
p1 = imaging.save_image(img, cfg); p2 = imaging.save_image(img, cfg)
check("同秒保存不覆盖", p1 != p2 and p1.exists() and p2.exists(), f"{p1.name} / {p2.name}")

# --- 诊断转储 ---
path = ov.dump_debug()
files = glob.glob(os.path.join(path, "overlay_*.json"))
check("F12 诊断转储", bool(files), f"{path} ({len(files)} 个 json)")

ov.finish("cancel"); app.processEvents()
print("\n" + ("全部通过" if not fails else "失败: " + ", ".join(fails)))
sys.exit(0 if not fails else 1)
