"""X11 平台层：会话探测、全局热键、焦点、XTEST 粘贴、剪贴板监听。"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSocketNotifier, Signal

from Xlib import X, XK, display as xdisplay
from Xlib.ext import xfixes, xtest


@dataclass
class Capabilities:
    session: str = "unknown"  # x11 | wayland
    capture: bool = False
    overlay_fullscreen: bool = False
    pin_always_on_top: bool = False
    hotkey_grab: bool = False
    clipboard_watch: bool = False
    paste_inject: bool = False
    tray: bool = True
    notes: str = ""


def detect_session() -> str:
    return (os.environ.get("XDG_SESSION_TYPE") or "").lower() or (
        "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11" if os.environ.get("DISPLAY") else "unknown"
    )


def probe_capabilities() -> Capabilities:
    sess = detect_session()
    caps = Capabilities(session=sess)
    if sess != "x11":
        caps.notes = "非 X11 会话：需要 Wayland 后端（portal）"
        return caps
    try:
        dpy = xdisplay.Display()
        root = dpy.screen().root
        exts = {name: bool(dpy.query_extension(name)) for name in ("XFIXES", "XTEST", "MIT-SHM", "SHAPE")}
        prop = root.get_full_property(dpy.intern_atom("_NET_SUPPORTED"), X.AnyPropertyType)
        net = set(prop.value) if prop and prop.value else set()
        above = dpy.intern_atom("_NET_WM_STATE_ABOVE")
        caps.capture = exts["MIT-SHM"] or True
        caps.overlay_fullscreen = True
        caps.pin_always_on_top = above in net
        caps.hotkey_grab = True
        caps.clipboard_watch = exts["XFIXES"]
        caps.paste_inject = exts["XTEST"]
        dpy.close()
    except Exception as exc:  # noqa: BLE001
        caps.notes = f"X11 探测失败: {exc!r}"
    return caps


# 配置里的热键统一存小写，但 X keysym 名字对功能键/特殊键是大小写敏感的：
# "f11" 找不到，"F11" 才行。这里做一次别名/大小写归一。
_KEYSYM_ALIASES = {
    "esc": "Escape", "enter": "Return", "return": "Return", "tab": "Tab",
    "space": "space", "del": "Delete", "delete": "Delete", "ins": "Insert",
    "insert": "Insert", "backspace": "BackSpace", "bksp": "BackSpace",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "print": "Print", "pause": "Pause", "menu": "Menu",
    "capslock": "Caps_Lock", "numlock": "Num_Lock", "scrolllock": "Scroll_Lock",
}


def keysym_for(name: str) -> int:
    """把配置里的按键名解析成 X keysym；兼容 f11/F11、esc/Escape 等写法。"""
    n = (name or "").strip()
    if not n:
        return 0
    low = n.lower()
    if low in _KEYSYM_ALIASES:
        n = _KEYSYM_ALIASES[low]
    candidates = [n]
    if re.fullmatch(r"f\d+", low):
        candidates = [low.upper()]
    else:
        candidates += [n.capitalize(), n.upper()]
    for cand in candidates:
        try:
            ks = XK.string_to_keysym(cand)
        except Exception:  # noqa: BLE001
            continue
        if ks:
            return int(ks)
    return 0


def _mod_variants(mods: int) -> list[int]:
    """NumLock(Mod2) 与 CapsLock(LockMask) 的 4 种组合，否则开着大写就抢不到键。"""
    return [mods, mods | (1 << 4), mods | (1 << 1), mods | (1 << 4) | (1 << 1)]


class HotkeyGrabber(QObject):
    """被动 grab 全局热键。owner_events=True 保证遮罩自己也能正常收键。"""

    triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dpy = xdisplay.Display()
        self._root = self._dpy.screen().root
        self._grabs: list[tuple[int, int]] = []
        self._last = 0.0
        self._names: dict[tuple[int, int], str] = {}
        self._notifier = QSocketNotifier(self._dpy.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)

    def grab(self, name: str, mods: int, keysym_name: str) -> bool:
        keysym = keysym_for(keysym_name)
        keycode = self._dpy.keysym_to_keycode(keysym) if keysym else 0
        if os.environ.get("SNITTOOL_DEBUG_GRAB"):
            print(f"[grab:{name}] keysym={keysym_name!r} mods={mods} keycode={keycode}", flush=True)
        if not keycode:
            return False
        errors: list[BaseException] = []
        added: list[tuple[int, int]] = []

        def _on_grab_error(err, request=None):
            errors.append(err)
            return 1  # 告诉 python-xlib 已处理，避免往 stderr 打印预期内的 BadAccess

        for mod in _mod_variants(mods):
            try:
                self._root.grab_key(
                    keycode, mod, True,
                    X.GrabModeAsync, X.GrabModeAsync,
                    onerror=_on_grab_error,
                )
                added.append((keycode, mod))
            except Exception:  # noqa: BLE001  已被占用
                continue
        # sync 之后异步错误才会被派发；必须据此判断是否真的抢到了
        self._dpy.sync()
        if errors:
            if os.environ.get("SNITTOOL_DEBUG_GRAB"):
                print(f"[grab:{name}] errors={[repr(e) for e in errors]}", flush=True)
            # 回滚这一组里成功的部分，避免只抢到 NumLock 变体这种半吊子状态
            for code, mod in added:
                try:
                    self._root.ungrab_key(code, mod)
                except Exception:  # noqa: BLE001
                    pass
            self._dpy.sync()
            return False
        for code, mod in added:
            self._grabs.append((code, mod))
            self._names[(code, mod)] = name
        return bool(added)

    def ungrab_all(self) -> None:
        for keycode, mod in self._grabs:
            try:
                self._root.ungrab_key(keycode, mod)
            except Exception:  # noqa: BLE001
                pass
        self._grabs.clear()
        self._names.clear()
        try:
            self._dpy.sync()
        except Exception:  # noqa: BLE001
            pass

    def _on_readable(self) -> None:
        triggered = None
        while self._dpy.pending_events():
            ev = self._dpy.next_event()
            if ev.type == X.KeyPress:
                name = self._names.get((ev.detail, ev.state))
                if name:
                    triggered = name
        if triggered and time.monotonic() - self._last > 0.35:
            self._last = time.monotonic()
            self.triggered.emit(triggered)


class ClipboardWatcher(QObject):
    """XFixes 选 CLIPBOARD 的 owner 变更；内容由 Qt 侧读取（免自己实现 INCR）。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dpy = xdisplay.Display()
        self._root = self._dpy.screen().root
        self._ok = False
        try:
            xfixes.query_version(self._dpy)
            clipboard = int(self._dpy.intern_atom("CLIPBOARD"))
            xfixes.select_selection_input(
                self._dpy, self._root, clipboard, xfixes.XFixesSetSelectionOwnerNotifyMask
            )
            self._dpy.sync()
            self._ok = True
        except Exception:  # noqa: BLE001
            self._ok = False
        self._notifier = QSocketNotifier(self._dpy.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)

    @property
    def supported(self) -> bool:
        return self._ok

    def _on_readable(self) -> None:
        changed = False
        while self._dpy.pending_events():
            ev = self._dpy.next_event()
            if "SelectionNotify" in type(ev).__name__:
                changed = True
        if changed:
            self.changed.emit()


class X11Focus:
    """记录/恢复焦点窗口，并注入 Ctrl+V。"""

    def __init__(self) -> None:
        self._dpy = xdisplay.Display()
        self._root = self._dpy.screen().root
        self._atom_active = self._dpy.intern_atom("_NET_ACTIVE_WINDOW")

    def active_window(self) -> int:
        prop = self._root.get_full_property(self._atom_active, X.AnyPropertyType)
        return int(prop.value[0]) if prop and prop.value else 0

    def focus(self, wid: int) -> None:
        if not wid:
            return
        try:
            win = self._dpy.create_resource_object("window", wid)
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
            self._dpy.sync()
        except Exception:  # noqa: BLE001
            pass

    def send_ctrl_v(self) -> None:
        ctrl = self._dpy.keysym_to_keycode(XK.string_to_keysym("Control_L"))
        v = self._dpy.keysym_to_keycode(XK.string_to_keysym("v"))
        for kc, press in ((ctrl, True), (v, True), (v, False), (ctrl, False)):
            xtest.fake_input(self._dpy, X.KeyPress if press else X.KeyRelease, kc)
        self._dpy.sync()

    def paste_to(self, wid: int, delay_ms: int = 30) -> None:
        self.focus(wid)
        time.sleep(delay_ms / 1000.0)
        self.send_ctrl_v()
