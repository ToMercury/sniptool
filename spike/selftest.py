"""sniptool 自检：几何数学、鼠标交互、渲染像素、OCR 链路。无需人工看图。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from sniptool.bootstrap import setup_qt_plugins

setup_qt_plugins()

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

from sniptool import imaging
from sniptool.annotations import ARROW, RECT, TEXT, Op
from sniptool.geometry import apply_drag, clamp_rect, hit_test
from sniptool.overlay import Overlay

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}", flush=True)
    if not cond:
        FAILS.append(name)


def rgb(c: QColor):
    return (c.red(), c.green(), c.blue())


# ---------------------------------------------------------------- 1. 几何
def test_geometry() -> None:
    bounds = QRect(0, 0, 1920, 1080)
    sel = QRect(100, 100, 400, 300)
    check("hit_test 内部=move", hit_test(sel, QPoint(300, 250)) == "move")
    check("hit_test 左上角=nw", hit_test(sel, QPoint(100, 100)) == "nw")
    check("hit_test 右下角=se", hit_test(sel, QPoint(500, 400)) == "se")
    check("hit_test 外部=new", hit_test(sel, QPoint(900, 900)) == "new")

    moved = apply_drag(sel, "move", sel, QPoint(300, 250), QPoint(340, 270), bounds)
    check("move 平移", moved.topLeft() == QPoint(140, 120), f"{moved.getRect()}")
    moved2 = apply_drag(sel, "move", sel, QPoint(300, 250), QPoint(3000, 3000), bounds)
    check("move 不越界", bounds.contains(moved2), f"{moved2.getRect()}")

    r = apply_drag(sel, "resize:se", sel, QPoint(500, 400), QPoint(600, 500), bounds)
    check("resize se", r.right() == 600 and r.bottom() == 500 and r.topLeft() == QPoint(100, 100),
          f"{r.getRect()}")
    r2 = apply_drag(sel, "resize:nw", sel, QPoint(100, 100), QPoint(50, 20), bounds)
    check("resize nw 固定对角", r2.bottomRight() == QPoint(499, 399), f"{r2.getRect()}")
    r3 = apply_drag(sel, "resize:e", sel, QPoint(500, 250), QPoint(120, 250), bounds)
    check("resize 最小尺寸保护", r3.width() >= 4, f"{r3.getRect()}")
    check("clamp_rect", clamp_rect(QRect(-50, -50, 100, 100), bounds) == QRect(0, 0, 50, 50))


# ---------------------------------------------------------------- 2. 交互 + 渲染
def mouse(ov, typ, x, y, buttons=Qt.MouseButton.NoButton, button=Qt.MouseButton.LeftButton):
    ev = QMouseEvent(typ, QPointF(x, y), QPointF(x, y), button, buttons, Qt.KeyboardModifier.NoModifier)
    if typ == typ.MouseButtonPress:
        ov.mousePressEvent(ev)
    elif typ == typ.MouseMove:
        ov.mouseMoveEvent(ev)
    else:
        ov.mouseReleaseEvent(ev)


def test_interaction(app: QApplication) -> Overlay | None:
    base = imaging.grab_screen()
    if base.isNull():
        check("抓图", False, "grab_screen 返回空")
        return None
    check("抓图", True, f"{base.width()}x{base.height()}")
    # 屏幕休眠 / DPMS 关闭 / 锁屏时 grabWindow 会得到全黑图，此时所有
    # 「亮度差」类断言都无意义，标记 SKIP 而不是误报失败。
    blank = True
    for x in range(0, base.width(), max(1, base.width() // 8)):
        for y in range(0, base.height(), max(1, base.height() // 8)):
            c = base.pixelColor(x, y)
            if c.red() > 8 or c.green() > 8 or c.blue() > 8:
                blank = False
                break
        if not blank:
            break
    if blank:
        print("[SKIP] 屏幕抓图全黑（DPMS/锁屏），跳过亮度差断言", flush=True)

    ov = Overlay(base)
    dpr = ov.dpr
    check("dpr 读取", dpr > 0, f"dpr={dpr:.4f}")
    ov.show()
    app.processEvents()
    time.sleep(0.15)
    app.processEvents()

    # 合成一次框选：逻辑 (100,120) -> (420,360)
    mouse(ov, QMouseEvent.Type.MouseButtonPress, 100, 120, Qt.MouseButton.LeftButton)
    mouse(ov, QMouseEvent.Type.MouseMove, 420, 360, Qt.MouseButton.LeftButton)
    mouse(ov, QMouseEvent.Type.MouseButtonRelease, 420, 360)
    app.processEvents()

    expect = QRect(int(100 * dpr), int(120 * dpr),
                   int((420 - 100) * dpr), int((360 - 120) * dpr))
    check("框选坐标 dpr 换算", ov.sel is not None and abs(ov.sel.width() - expect.width()) <= 2
          and abs(ov.sel.height() - expect.height()) <= 2,
          f"sel={ov.sel.getRect() if ov.sel else None} expect≈{expect.getRect()}")
    check("工具栏出现", ov.toolbar.isVisible())
    tb = ov.toolbar.geometry()
    check("工具栏在窗口内", ov.rect().contains(tb.topLeft()) and tb.width() > 0, f"{tb.getRect()}")

    # 加标注
    ov.ops = [
        Op(kind=RECT, rect=ov.sel.adjusted(10, 10, -10, -10), color="#ff3b30", width=ov.line_width),
        Op(kind=ARROW, rect=ov.sel.adjusted(20, 40, -60, -40), color="#34c759", width=ov.line_width),
        Op(kind=TEXT, rect=QRect(ov.sel.topLeft() + QPoint(30, 30), QSize(200, 40)), text="标注测试", color="#ffd60a",
           font_px=ov.font_px),
    ]
    ov.update()
    app.processEvents()
    time.sleep(0.1)
    app.processEvents()

    img = ov.result_image()
    check("结果图存在", img is not None and not img.isNull())
    if img is not None:
        check("结果图尺寸=选区", abs(img.width() - ov.sel.width()) <= 1
              and abs(img.height() - ov.sel.height()) <= 1,
              f"{img.width()}x{img.height()} vs {ov.sel.width()}x{ov.sel.height()}")
        # 红线必须出现在图像里
        found_red = False
        for x in range(0, img.width(), 3):
            c = img.pixelColor(x, 10)
            if c.red() > 180 and c.green() < 90 and c.blue() < 90:
                found_red = True
                break
        check("标注已合成进结果图（红框）", found_red)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".test", "selftest_result.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out, "PNG")
        print(f"       结果图存于 {out}")

    # 遮罩渲染：整窗 grab 后检查选区内外亮度差
    shot = ov.grab()
    img_shot = shot.toImage()
    if ov.sel is not None and not img_shot.isNull():
        sx, sy = int(ov.sel.center().x() / dpr), int(ov.sel.center().y() / dpr)
        ox, oy = 5, 5
        inside = img_shot.pixelColor(min(sx, img_shot.width() - 1), min(sy, img_shot.height() - 1))
        outside = img_shot.pixelColor(ox, oy)
        base_out = base.pixelColor(ox, oy)
        base_in = base.pixelColor(int(ov.sel.center().x()), int(ov.sel.center().y()))
        if not blank:
            check("选区外被压暗", sum(rgb(outside)) < sum(rgb(base_out)) * 0.85,
                  f"{rgb(base_out)} -> {rgb(outside)}")
        # 选区内保持原始像素：直接比对结果图（无标注处）与原图同一点
        if img is not None:
            px, py = int(img.width() * 0.9), int(img.height() * 0.9)
            got_c = img.pixelColor(px, py)
            base_c = base.pixelColor(ov.sel.left() + px, ov.sel.top() + py)
            diff = abs(sum(rgb(got_c)) - sum(rgb(base_c)))
            if not blank:
                check("选区内保持原始像素", diff == 0, f"{rgb(base_c)} -> {rgb(got_c)} diff={diff}")

    # 键盘：Ctrl+Z 撤销
    n0 = len(ov.ops)
    from PySide6.QtGui import QKeyEvent
    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    ov.keyPressEvent(ev)
    check("Ctrl+Z 撤销", len(ov.ops) == n0 - 1, f"{n0} -> {len(ov.ops)}")

    # Enter 触发 save
    got = {}
    ov.done.connect(lambda a: got.setdefault("action", a))
    ev2 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    ov.keyPressEvent(ev2)
    app.processEvents()
    check("Enter = 保存", got.get("action") == "save", f"action={got.get('action')}")
    return ov


# ---------------------------------------------------------------- 3. OCR
def test_ocr(ov: Overlay | None) -> None:
    from sniptool import ocr as ocr_mod

    if ov is None:
        return
    img = ov.result_image()
    if img is None:
        check("OCR 输入", False)
        return
    t0 = time.monotonic()
    try:
        text = ocr_mod.ocr_image(img, "chi_sim+eng", 6, True)
    except Exception as exc:  # noqa: BLE001
        check("OCR 调用", False, repr(exc))
        return
    dt = int((time.monotonic() - t0) * 1000)
    check("OCR 调用", True, f"{dt}ms, {len(text)} 字符")


def main() -> int:
    app = QApplication(sys.argv)
    print(f"会话={os.environ.get('XDG_SESSION_TYPE')}  屏幕={QGuiApplication.primaryScreen().geometry().getRect()}")
    print("\n-- 1. 几何数学 --")
    test_geometry()
    print("\n-- 2. 交互与渲染 --")
    ov = test_interaction(app)
    print("\n-- 3. OCR --")
    test_ocr(ov)
    if ov is not None:
        ov.hide()
        ov.close()
    app.processEvents()
    print(f"\n{'全部通过' if not FAILS else '失败项: ' + ', '.join(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
