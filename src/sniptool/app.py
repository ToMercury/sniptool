"""应用入口：daemon / capture / --diagnose / --demo。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import imaging, ocr as ocr_mod
from .annotations import ARROW, RECT, TEXT, Op
from .config import Config, config_path as config_file, data_dir, parse_hotkey
from .overlay import Overlay
from .toast import Toaster
from .x11 import ClipboardWatcher, HotkeyGrabber, X11Focus, detect_session, probe_capabilities

APP_NAME = "sniptool"


# ---------------------------------------------------------------- 单实例
def socket_name() -> str:
    # SNITTOOL_SOCKET 便于测试/多环境隔离（默认按 uid 命名）
    return os.environ.get("SNITTOOL_SOCKET") or f"{APP_NAME}-{os.getuid()}"


class InstanceServer(QObject):
    command = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        QLocalServer.removeServer(socket_name())
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._on_conn)
        self.server.listen(socket_name())

    def _on_conn(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.waitForReadyRead(300)
        data = bytes(sock.readAll()).decode("utf-8", "ignore").strip()
        sock.disconnectFromServer()
        if data:
            self.command.emit(data)


def send_command(cmd: str, timeout_ms: int = 800) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(socket_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(cmd.encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


# ---------------------------------------------------------------- 图标
def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(64, 158, 255))
    p.drawRoundedRect(QRect(2, 2, 60, 60), 14, 14)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRect(14, 20, 36, 28), 5, 5)
    p.setBrush(QColor(64, 158, 255))
    p.drawEllipse(QPoint(32, 34), 7, 7)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- 截图流程
class CaptureSession(QObject):
    finished = Signal()

    def __init__(self, cfg: Config, toaster: Toaster, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.toaster = toaster
        self.overlay: Overlay | None = None
        self.pins: list = []

    def start(self) -> None:
        if self.overlay is not None:
            self.overlay.raise_()
            return
        # QScreen 包装对象可能因为屏幕热插拔/休眠流程失效（贴图关闭后曾触发
        # "Internal C++ object already deleted"）。每次都重新取，失败再取一次。
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.toaster.show("截图失败", "当前会话没有可用屏幕")
            self.finished.emit()
            return
        try:
            base = imaging.grab_screen(screen)
        except RuntimeError:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                self.toaster.show("截图失败", "当前会话没有可用屏幕")
                self.finished.emit()
                return
            try:
                base = imaging.grab_screen(screen)
            except RuntimeError as exc:
                self.toaster.show("截图失败", f"抓取屏幕失败：{exc}")
                self.finished.emit()
                return
        if base.isNull():
            self.toaster.show("截图失败", "抓取屏幕失败（当前会话可能不允许）")
            self.finished.emit()
            return
        ov = Overlay(base, screen)
        self.overlay = ov
        ov.done.connect(self._on_done)
        ov.start()

    def _on_done(self, action: str) -> None:
        ov = self.overlay
        self.overlay = None
        try:
            self._handle(action, ov)
        finally:
            if ov is not None:
                ov.deleteLater()
            self.finished.emit()

    def _handle(self, action: str, ov: Overlay | None) -> None:
        if ov is None or action == "cancel":
            return
        img = ov.result_image()
        if img is None or img.isNull():
            return
        if action == "copy":
            QGuiApplication.clipboard().setImage(img)
            self.toaster.show("已复制", f"{img.width()} × {img.height()} 图片已放入剪贴板", 3000)
        elif action == "save":
            try:
                path = imaging.save_image(img, self.cfg)
            except OSError as exc:
                self.toaster.show("保存失败", str(exc))
                return
            if self.cfg.copy_after_save:
                QGuiApplication.clipboard().setImage(img)
            self.toaster.show("已保存", str(path), 4000)
        elif action == "ocr":
            self._do_ocr(img)
        elif action == "translate":
            self._do_translate(img)
        elif action == "pin":
            self._do_pin(img, ov)

    def _do_pin(self, img: QImage, ov: Overlay) -> None:
        from .pin import PinWindow

        pos = None
        if ov.sel is not None:
            pos = QPoint(int(ov.sel.x() / ov.dpr), int(ov.sel.y() / ov.dpr))
        pin = PinWindow(img, self.cfg, self.toaster, pos)
        self.pins.append(pin)
        pin.closed.connect(lambda p=pin: self._forget_pin(p))
        pin.show()
        pin.raise_()
        pin.activateWindow()

    def _forget_pin(self, pin) -> None:
        try:
            self.pins.remove(pin)
        except ValueError:
            pass

    def _do_translate(self, img: QImage) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            text = ocr_mod.ocr_image(img, self.cfg.ocr_lang, self.cfg.ocr_psm, self.cfg.ocr_preprocess)
        except ocr_mod.OcrUnavailable as exc:
            QApplication.restoreOverrideCursor()
            self.toaster.show("翻译失败", f"OCR 不可用：{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not text.strip():
            self.toaster.show("翻译", "选区内没有识别到文字")
            return
        from .translate_panel import TranslatePanel

        self.panel = TranslatePanel(self.cfg, text)
        self.panel.show()
        self.panel.raise_()

    def _do_ocr(self, img: QImage) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        t0 = time.monotonic()
        try:
            text = ocr_mod.ocr_image(img, self.cfg.ocr_lang, self.cfg.ocr_psm, self.cfg.ocr_preprocess)
        except ocr_mod.OcrUnavailable as exc:
            QApplication.restoreOverrideCursor()
            self.toaster.show("OCR 失败", f"{exc}\n请确认已安装 tesseract 与中文语言包")
            return
        finally:
            QApplication.restoreOverrideCursor()
        elapsed = int((time.monotonic() - t0) * 1000)
        if not text:
            self.toaster.show("OCR", f"没识别到文字（{elapsed}ms）")
            return
        QGuiApplication.clipboard().setText(text)
        self.toaster.show(f"OCR 完成 · {elapsed}ms · {len(text)} 字符", text)


# ---------------------------------------------------------------- 守护进程
class Daemon(QObject):
    def __init__(self, app: QApplication, cfg: Config):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.toaster = Toaster()
        self.capture = CaptureSession(cfg, self.toaster)
        self.focus = None
        self.watcher = None
        self.settings = None
        self.hotkey_status: dict[str, bool] = {}
        self._settings_applied = False
        self._clip_store = None
        self._clip_history = None
        self._clip_panel = None
        self._clip_prev_window = 0

        if detect_session() == "x11":
            try:
                self.watcher = ClipboardWatcher(self)
                self.focus = X11Focus()
            except Exception:  # noqa: BLE001
                pass

        self.server = InstanceServer(self)
        self.server.command.connect(self._on_command)
        self._setup_clipboard()

        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        self.act_capture = QAction("截图", menu)
        self.act_capture.triggered.connect(self.start_capture)
        self.act_clipboard = QAction("粘贴板", menu)
        self.act_clipboard.triggered.connect(self.show_clipboard)
        act_settings = QAction("设置", menu)
        act_settings.triggered.connect(self.open_settings)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.app.quit)
        menu.addAction(self.act_capture)
        menu.addAction(self.act_clipboard)
        menu.addSeparator()
        menu.addAction(act_settings)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("sniptool")
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        self.hotkeys = HotkeyGrabber(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self._register_hotkeys()

    # ------------------------------------------------------------ 剪贴板历史
    def _setup_clipboard(self) -> None:
        try:
            from .clipboard import ClipboardHistory, ClipboardPanel, HistoryStore
        except Exception:  # noqa: BLE001
            return
        path = data_dir() / "clipboard.db" if self.cfg.history_persist else None
        try:
            store = HistoryStore(path, self.cfg.history_limit, self.cfg.history_days)
        except Exception:  # noqa: BLE001  磁盘异常时退化为内存
            try:
                store = HistoryStore(None, self.cfg.history_limit, self.cfg.history_days)
            except Exception:
                return
        self._clip_store = store
        self._clip_history = ClipboardHistory(store, self)
        self._clip_panel = ClipboardPanel(store)
        self._clip_panel.chosen.connect(self._paste_clip_item)
        self._clip_history.changed.connect(self._on_history_changed)

    def _on_history_changed(self) -> None:
        if self._clip_panel is not None and self._clip_panel.isVisible():
            self._clip_panel.refresh()

    def _rebuild_clipboard(self) -> None:
        panel, hist, store = self._clip_panel, self._clip_history, self._clip_store
        self._clip_panel = self._clip_history = self._clip_store = None
        if panel is not None:
            panel.hide()
            panel.deleteLater()
        if hist is not None:
            hist.deleteLater()
        if store is not None:
            store.close()
        self._setup_clipboard()

    def show_clipboard(self) -> None:
        panel = self._clip_panel
        if panel is None:
            self.toaster.show("剪贴板历史", "当前环境无法初始化剪贴板历史")
            return
        if panel.isVisible():
            if panel.isActiveWindow():
                panel.hide()
            else:
                # 被其它窗口盖住时，热键把面板提到前面，而不是把它关掉
                panel.refresh(prefer_row=0, keep_id=False)
                panel.bring_to_front()
            return
        self._clip_prev_window = self.focus.active_window() if self.focus else 0
        panel.show_at_cursor()

    def _paste_clip_item(self, item_id: int) -> None:
        if self._clip_store is None or self._clip_panel is None or self._clip_history is None:
            return
        item = self._clip_store.get(item_id)
        if item is None:
            return
        self._clip_history.set_clipboard(item)
        self._clip_panel.hide()
        prev = self._clip_prev_window
        if self.focus is not None and prev:
            self.focus.focus(prev)
            QTimer.singleShot(90, self._send_paste)
        else:
            self.toaster.show("已复制", "已放入剪贴板，请到目标窗口按 Ctrl+V", 2500)

    def _send_paste(self) -> None:
        if self.focus is not None:
            self.focus.send_ctrl_v()

    # ------------------------------------------------------------ 设置
    def open_settings(self) -> None:
        from .settings import SettingsWindow

        if self.settings is None:
            self.settings = SettingsWindow(self.cfg)
            self.settings.saved.connect(self._apply_config)
            self.settings.closed.connect(self._on_settings_closed)
        if not self.settings.isVisible():
            # 录制热键时不能被 daemon 自己的全局 grab 抢走；设置窗口关闭后再恢复
            self.hotkeys.ungrab_all()
            self._settings_applied = False
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _on_settings_closed(self) -> None:
        if self._settings_applied:
            self._settings_applied = False
            return
        # 取消/直接关闭：恢复原来的热键
        self.hotkeys.ungrab_all()
        self._register_hotkeys()

    def _apply_config(self, cfg: Config) -> None:
        old_persist = self.cfg.history_persist
        self.cfg = cfg
        self.capture.cfg = cfg
        self.hotkeys.ungrab_all()
        self._register_hotkeys()
        self._settings_applied = True
        self.act_capture.setText("截图")
        self.act_clipboard.setText("粘贴板")
        if cfg.history_persist != old_persist:
            self._rebuild_clipboard()
        elif self._clip_store is not None:
            self._clip_store.set_limits(cfg.history_limit, cfg.history_days)
        self.toaster.show("设置已保存", "新配置与快捷键已即时生效", 2500)

    def _on_tray(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.start_capture()

    def _register_hotkeys(self) -> None:
        mods, key = parse_hotkey(self.cfg.hotkey_capture)
        ok = self.hotkeys.grab("capture", mods, key or "a")
        self.hotkey_status["capture"] = ok
        if not ok:
            self.toaster.show("热键失败", f"{self.cfg.hotkey_capture} 无法识别或已被其他程序占用，可在设置里更改")
        mods2, key2 = parse_hotkey(self.cfg.hotkey_clipboard)
        ok2 = self.hotkeys.grab("clipboard", mods2, key2 or "v")
        self.hotkey_status["clipboard"] = ok2
        if not ok2:
            self.toaster.show("剪贴板热键失败",
                              f"{self.cfg.hotkey_clipboard} 无法识别或已被其他程序占用，可在设置里更改")

    def _on_hotkey(self, name: str) -> None:
        if name == "capture":
            self.start_capture()
        elif name == "clipboard":
            self.show_clipboard()

    def _on_command(self, cmd: str) -> None:
        if cmd == "capture":
            self.start_capture()
        elif cmd == "clipboard":
            self.show_clipboard()
        elif cmd == "settings":
            self.open_settings()
        elif cmd == "ping":
            pass
        elif cmd == "quit":
            self.app.quit()

    def start_capture(self) -> None:
        self.capture.start()


# ---------------------------------------------------------------- CLI
def diagnose() -> int:
    from . import autostart

    caps = probe_capabilities()
    cfg = Config.load()
    print(f"sniptool 诊断")
    print(f"  会话类型     : {caps.session}")
    print(f"  抓图         : {'OK' if caps.capture else 'NO'}")
    print(f"  全屏遮罩     : {'OK' if caps.overlay_fullscreen else 'NO'}")
    print(f"  贴图置顶     : {'OK' if caps.pin_always_on_top else 'NO'}")
    print(f"  全局热键     : {'OK' if caps.hotkey_grab else 'NO'}")
    print(f"  剪贴板监听   : {'OK' if caps.clipboard_watch else 'NO'}")
    print(f"  粘贴注入     : {'OK' if caps.paste_inject else 'NO'}")
    print(f"  tesseract    : {'OK' if ocr_mod.available() else 'NO'}")
    print(f"  OCR 语言     : {', '.join(ocr_mod.languages()) or '无'}")
    print(f"  截图热键     : {cfg.hotkey_capture}")
    print(f"  剪贴板热键   : {cfg.hotkey_clipboard}")
    print(f"  开机自启     : {'已启用' if autostart.is_enabled() else '未启用'}")
    db = data_dir() / "clipboard.db"
    print(f"  剪贴板历史   : {db} ({db.stat().st_size // 1024} KB)" if db.exists() else "  剪贴板历史   : 无")
    if caps.notes:
        print(f"  备注         : {caps.notes}")
    return 0


def demo(image_path: str, out_path: str) -> int:
    """渲染一次遮罩并把自己截图存下来，用于无人工检查 UI。"""
    app = QApplication(sys.argv)
    base = QImage(image_path)
    if base.isNull():
        print("无法读取图片", file=sys.stderr)
        return 2
    ov = Overlay(base)
    ov.show()
    app.processEvents()
    ov.sel = QRect(int(base.width() * 0.18), int(base.height() * 0.18),
                   int(base.width() * 0.42), int(base.height() * 0.38))
    d = ov.dpr
    ov.ops = [
        Op(kind=RECT, rect=QRect(int(300 * d), int(300 * d), int(520 * d), int(220 * d)),
           color="#ff3b30", width=ov.line_width),
        Op(kind=ARROW, rect=QRect(int(380 * d), int(600 * d), int(700 * d), int(820 * d)),
           color="#34c759", width=ov.line_width),
        Op(kind=TEXT, rect=QRect(int(340 * d), int(360 * d), int(400 * d), int(60 * d)),
           text="标注文字 Chinese 测试", color="#ffd60a", font_px=ov.font_px),
    ]
    ov.toolbar.show()
    ov._place_toolbar()
    ov.update()
    app.processEvents()
    QTimer.singleShot(300, lambda: _snap_and_quit(app, ov, out_path))
    return app.exec()


def _snap_and_quit(app: QApplication, ov: Overlay, out_path: str) -> None:
    pm = ov.grab()
    pm.save(out_path, "PNG")
    print(f"已保存遮罩渲染图: {out_path}")
    ov.hide()
    app.quit()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    cmd = argv[0] if argv and not argv[0].startswith("-") else "daemon"

    if "--diagnose" in argv:
        return diagnose()
    if "--set-api-key" in argv:
        i = argv.index("--set-api-key")
        key = argv[i + 1] if len(argv) > i + 1 else ""
        cfg = Config.load()
        cfg.translate.api_key = key.strip()
        cfg.save()
        print(f"已写入 API Key（长度 {len(cfg.translate.api_key)}）到 {config_file()}")
        return 0 if key else 1
    if "--translate-test" in argv:
        from .translate import TranslateError, translate_once

        i = argv.index("--translate-test")
        text = argv[i + 1] if len(argv) > i + 1 else "Hello, world."
        cfg = Config.load()
        t0 = time.monotonic()
        try:
            out = translate_once(cfg, text)
        except TranslateError as exc:
            print(f"翻译失败: {exc}", file=sys.stderr)
            return 1
        dt = int((time.monotonic() - t0) * 1000)
        print(f"原文: {text}")
        print(f"译文: {out}")
        print(f"耗时: {dt}ms")
        return 0
    if "--demo" in argv:
        i = argv.index("--demo")
        img = argv[i + 1] if len(argv) > i + 1 else "spike/spike_capture.png"
        out = argv[i + 2] if len(argv) > i + 2 else "spike/demo_overlay.png"
        return demo(img, out)

    cfg = Config.load()
    if cmd == "capture":
        if not send_command("capture"):
            print("sniptool daemon 未运行，请先启动：sniptool daemon", file=sys.stderr)
            return 1
        return 0
    if cmd == "clipboard":
        if not send_command("clipboard"):
            print("sniptool daemon 未运行，无法打开剪贴板历史", file=sys.stderr)
            return 1
        return 0
    if cmd == "settings":
        if send_command("settings"):
            return 0
        # daemon 不在时独立打开设置窗口（只改配置，不影响运行中的实例）
        from .settings import SettingsWindow

        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        win = SettingsWindow(cfg)
        win.show()
        return app.exec()
    if cmd == "quit":
        send_command("quit")
        return 0

    # daemon
    if send_command("ping"):
        print("sniptool 已在运行")
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    daemon = Daemon(app, cfg)  # noqa: F841  保持引用
    print(f"sniptool daemon 已启动（会话 {detect_session()}，热键 {cfg.hotkey_capture}"
          f" / {cfg.hotkey_clipboard}）", flush=True)
    return app.exec()
