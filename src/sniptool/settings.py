"""设置窗口：保存路径 / 热键 / OCR / DeepSeek / 自启 / 剪贴板历史。"""
from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QThread, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGraphicsDropShadowEffect, QGroupBox,
    QHBoxLayout, QLabel, QKeySequenceEdit, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import autostart, ocr
from .config import Config, parse_hotkey

_ARROW_PATH = Path(__file__).parent / "resources" / "arrow_down.png"
_ARROW_HOVER_PATH = Path(__file__).parent / "resources" / "arrow_down_hover.png"

QSS = f"""
QWidget {{ color: #dcdcdf; font-size: 13px; }}
QWidget#settingsCard {{
    background: #1e1e20; border: 1px solid #3a3a42; border-radius: 14px;
}}
QWidget#settingsTitleBar {{ background: transparent; }}
QLabel#settingsTitle {{ color: #8ec5ff; font-size: 14px; font-weight: 600; }}
QPushButton#closeBtn {{
    background: transparent; border: none; color: #9a9aa0;
    font-size: 18px; padding: 0; border-radius: 6px;
}}
QPushButton#closeBtn:hover {{ color: #ffffff; background: rgba(230,80,80,140); }}
QTabWidget::pane {{
    background: #1e1e20; border: 1px solid #33333a; border-radius: 8px; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: #9a9aa0; padding: 7px 18px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: #8ec5ff; border-bottom: 2px solid #409eff; }}
QGroupBox {{
    border: 1px solid #2e2e35; border-radius: 8px; margin-top: 12px;
    padding: 14px 10px 10px 10px; color: #8ec5ff; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QGroupBox QLabel {{ color: #dcdcdf; font-weight: normal; }}
QLineEdit, QSpinBox, QKeySequenceEdit {{
    background: #161618; border: 1px solid #2f2f36; border-radius: 5px;
    padding: 4px 6px; color: #e6e6e8; min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {{
    border-color: #409eff;
}}
QComboBox {{
    background: #161618; border: 1px solid #2f2f36; border-radius: 5px;
    padding: 0 22px 0 8px; color: #e6e6e8; min-height: 28px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({_ARROW_PATH.as_posix()});
    width: 10px;
    height: 6px;
}}
QComboBox::down-arrow:hover {{
    image: url({_ARROW_HOVER_PATH.as_posix()});
}}
QComboBox QAbstractItemView {{
    background: #202024; color: #e6e6e8; border: 1px solid #33333a;
    selection-background-color: #2f6fbd; outline: none;
    padding: 2px 0;
}}
QPushButton {{
    background: rgba(255,255,255,16); color: #e8e8ea;
    border: 1px solid rgba(255,255,255,28); border-radius: 6px;
    padding: 6px 16px;
}}
QPushButton:hover {{ background: rgba(64,158,255,80); border-color: rgba(64,158,255,150); }}
QPushButton:disabled {{ color: #77777c; }}
QPushButton#primary {{ background: #3574c4; border-color: #4a88d8; }}
QPushButton#primary:hover {{ background: #409eff; }}
QCheckBox {{ spacing: 6px; }}
"""


class _TranslateTestThread(QThread):
    result = Signal(bool, str)

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self) -> None:
        from .translate import TranslateError, translate_once

        try:
            out = translate_once(self.cfg, "Hello, nice to meet you.")
        except TranslateError as exc:
            self.result.emit(False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.result.emit(False, repr(exc))
        else:
            self.result.emit(True, out)


def _spec_to_seq(spec: str) -> QKeySequence:
    """配置里的 super+v → Qt 的 Meta+V。"""
    s = spec.strip().lower()
    for alias in ("super+", "win+", "meta+"):
        if alias in s:
            s = s.replace(alias, "meta+")
            break
    return QKeySequence(s)


def _seq_to_spec(seq: QKeySequence) -> str:
    if seq.isEmpty():
        return ""
    text = seq.toString(QKeySequence.SequenceFormat.PortableText).lower()
    return text.replace("meta+", "super+")


def _form() -> QFormLayout:
    lay = QFormLayout()
    lay.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lay.setHorizontalSpacing(12)
    lay.setVerticalSpacing(8)
    return lay


class SettingsWindow(QWidget):
    saved = Signal(object)   # Config
    closed = Signal()

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._test_thread: _TranslateTestThread | None = None
        self._drag_origin = None
        self._centered = False
        self.setWindowTitle("sniptool 设置")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)   # 留给阴影
        outer.setSpacing(0)

        self.card = QWidget()
        self.card.setObjectName("settingsCard")
        outer.addWidget(self.card)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 190))
        self.card.setGraphicsEffect(shadow)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(0, 0, 0, 14)
        root.setSpacing(0)
        root.addWidget(self._build_titlebar())

        body = QVBoxLayout()
        body.setContentsMargins(16, 4, 16, 0)
        body.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(), "常规")
        self.tabs.addTab(self._build_translate_tab(), "翻译")
        self.tabs.addTab(self._build_clipboard_tab(), "剪贴板")
        body.addWidget(self.tabs, 1)

        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.btn_defaults = QPushButton("恢复默认")
        self.btn_cancel = QPushButton("取消")
        self.btn_save = QPushButton("保存")
        self.btn_save.setObjectName("primary")
        self.btn_defaults.clicked.connect(self._on_defaults)
        self.btn_cancel.clicked.connect(self.close)
        self.btn_save.clicked.connect(self._on_save)
        foot.addWidget(self.btn_defaults)
        foot.addWidget(self.btn_cancel)
        foot.addWidget(self.btn_save)
        body.addLayout(foot)
        root.addLayout(body, 1)

        self._apply_to_widgets(cfg)
        self.adjustSize()
        self.setFixedSize(max(680, self.width()), max(760, self.height()))

    # ------------------------------------------------------------ 窗框
    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("settingsTitleBar")
        bar.setFixedHeight(44)
        bar.setCursor(Qt.CursorShape.SizeAllCursor)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 6, 12, 6)
        lay.setSpacing(8)
        title = QLabel("sniptool 设置")
        title.setObjectName("settingsTitle")
        title.setCursor(Qt.CursorShape.SizeAllCursor)
        close = QPushButton("×")
        close.setObjectName("closeBtn")
        close.setFixedSize(30, 30)
        close.setToolTip("关闭")
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.close)
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(close)
        self.title_bar = bar
        self.title_label = title
        self.btn_close = close
        bar.installEventFilter(self)
        title.installEventFilter(self)
        return bar

    def eventFilter(self, obj, ev) -> bool:
        """无边框窗口：拖标题栏移动。"""
        if obj in (self.title_bar, self.title_label):
            if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if (ev.type() == QEvent.Type.MouseMove and self._drag_origin is not None
                    and ev.buttons() & Qt.MouseButton.LeftButton):
                self.move(ev.globalPosition().toPoint() - self._drag_origin)
                return True
            if ev.type() == QEvent.Type.MouseButtonRelease:
                self._drag_origin = None
                return True
        return super().eventFilter(obj, ev)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not self._centered:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                self.move(avail.center() - self.rect().center())
            self._centered = True

    # ------------------------------------------------------------ 页面
    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        g_save = QGroupBox("截图保存")
        f = _form()
        row = QHBoxLayout()
        self.save_dir = QLineEdit()
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.setFixedWidth(74)
        self.btn_browse.clicked.connect(self._browse_dir)
        row.addWidget(self.save_dir, 1)
        row.addWidget(self.btn_browse)
        f.addRow("保存目录", row)
        self.save_template = QLineEdit()
        self.save_template.setToolTip("支持 strftime：%Y 年 %m 月 %d 日 %H 时 %M 分 %S 秒")
        f.addRow("文件名模板", self.save_template)
        self.save_format = QComboBox()
        self.save_format.addItem("PNG", "png")
        self.save_format.addItem("JPG", "jpg")
        self.save_format.addItem("WebP", "webp")
        f.addRow("图片格式", self.save_format)
        self.copy_after_save = QCheckBox("保存后同时复制到剪贴板")
        f.addRow("", self.copy_after_save)
        g_save.setLayout(f)
        lay.addWidget(g_save)

        g_hot = QGroupBox("快捷键")
        f2 = _form()
        self.hotkey_capture = QKeySequenceEdit()
        self.hotkey_capture.setMaximumSequenceLength(1)
        f2.addRow("截图", self.hotkey_capture)
        self.hotkey_clipboard = QKeySequenceEdit()
        self.hotkey_clipboard.setMaximumSequenceLength(1)
        f2.addRow("剪贴板历史", self.hotkey_clipboard)
        g_hot.setLayout(f2)
        lay.addWidget(g_hot)

        g_ocr = QGroupBox("OCR")
        f3 = _form()
        self.ocr_lang = QComboBox()
        self.ocr_lang.setEditable(True)
        langs = ocr.languages() if ocr.available() else []
        for lang in langs:
            self.ocr_lang.addItem(lang)
        if not langs:
            for lang in ("chi_sim", "eng", "chi_sim+eng"):
                self.ocr_lang.addItem(lang)
        self.ocr_lang.setToolTip("可多选，用 + 连接；需安装对应 tesseract 语言包")
        f3.addRow("识别语言", self.ocr_lang)
        self.ocr_psm = QComboBox()
        for label, value in (("自动分页 (3)", 3), ("单块文本 (6)", 6),
                             ("稀疏文本 (11)", 11), ("稀疏文本 + OSD (12)", 12)):
            self.ocr_psm.addItem(label, value)
        f3.addRow("版面模式", self.ocr_psm)
        self.ocr_preprocess = QCheckBox("小图放大 + 灰度预处理")
        f3.addRow("", self.ocr_preprocess)
        g_ocr.setLayout(f3)
        lay.addWidget(g_ocr)

        g_start = QGroupBox("启动")
        f4 = _form()
        self.autostart = QCheckBox("登录桌面后自动启动 sniptool（常驻托盘）")
        f4.addRow("", self.autostart)
        g_start.setLayout(f4)
        lay.addWidget(g_start)
        lay.addStretch(1)
        return page

    def _build_translate_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        g = QGroupBox("DeepSeek API")
        f = _form()
        self.base_url = QLineEdit()
        f.addRow("接口地址", self.base_url)
        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-…")
        self.show_key = QCheckBox("显示")
        self.show_key.toggled.connect(
            lambda on: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.show_key)
        f.addRow("API Key", key_row)
        self.model = QLineEdit()
        f.addRow("模型", self.model)
        self.target_lang = QLineEdit()
        self.target_lang.setPlaceholderText("例如：简体中文 / English / 日本語")
        f.addRow("目标语言", self.target_lang)
        self.stream = QCheckBox("流式输出（逐字显示译文）")
        f.addRow("", self.stream)
        g.setLayout(f)
        lay.addWidget(g)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setFixedWidth(110)
        self.btn_test.clicked.connect(self._on_test_translate)
        self.test_status = QLabel("保存前也可以先测试当前填写的 Key")
        self.test_status.setStyleSheet("color:#929298;")
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.test_status, 1)
        lay.addLayout(test_row)

        note = QLabel(
            "翻译 = 本地 tesseract OCR + DeepSeek 文本翻译。\n"
            "接口只接受文本，所以截图会先识别再翻译；面板里可复制译文或直接贴图。"
        )
        note.setStyleSheet("color:#85858b; font-size:12px;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return page

    def _build_clipboard_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        g = QGroupBox("剪贴板历史")
        f = _form()
        self.history_persist = QCheckBox("落盘保存（关闭后只保留本次运行期间的记录）")
        f.addRow("", self.history_persist)
        self.history_limit = QSpinBox()
        self.history_limit.setRange(5, 1000)
        self.history_limit.setSuffix(" 条")
        f.addRow("普通记录最多", self.history_limit)
        self.history_days = QSpinBox()
        self.history_days.setRange(1, 365)
        self.history_days.setSuffix(" 天")
        f.addRow("最长保留", self.history_days)
        g.setLayout(f)
        lay.addWidget(g)

        note = QLabel(
            "Super+V 弹出历史面板；↑↓ 选择、Enter 粘贴、空格收藏、Delete 删除。\n"
            "收藏项永久保留，不占普通记录上限、也不受天数清理；图片粘贴保持原分辨率。"
        )
        note.setStyleSheet("color:#85858b; font-size:12px;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------ 填值 / 取值
    def _apply_to_widgets(self, cfg: Config) -> None:
        self.save_dir.setText(cfg.save_dir)
        self.save_template.setText(cfg.save_template)
        idx = self.save_format.findData(cfg.save_format.lower())
        self.save_format.setCurrentIndex(idx if idx >= 0 else 0)
        self.copy_after_save.setChecked(bool(cfg.copy_after_save))
        self.hotkey_capture.setKeySequence(_spec_to_seq(cfg.hotkey_capture))
        self.hotkey_clipboard.setKeySequence(_spec_to_seq(cfg.hotkey_clipboard))
        self.ocr_lang.setCurrentText(cfg.ocr_lang)
        psm_idx = self.ocr_psm.findData(int(cfg.ocr_psm))
        self.ocr_psm.setCurrentIndex(psm_idx if psm_idx >= 0 else 1)
        self.ocr_preprocess.setChecked(bool(cfg.ocr_preprocess))
        self.autostart.setChecked(autostart.is_enabled())
        self.base_url.setText(cfg.translate.base_url)
        self.api_key.setText(cfg.translate.api_key)
        self.model.setText(cfg.translate.model)
        self.target_lang.setText(cfg.translate.target_lang)
        self.stream.setChecked(bool(cfg.translate.stream))
        self.history_persist.setChecked(bool(cfg.history_persist))
        self.history_limit.setValue(int(cfg.history_limit))
        self.history_days.setValue(int(cfg.history_days))

    def _build_config(self) -> Config:
        cfg = copy.deepcopy(self.cfg)
        cfg.save_dir = self.save_dir.text().strip()
        cfg.save_template = self.save_template.text().strip() or "Shot_%Y%m%d_%H%M%S"
        cfg.save_format = str(self.save_format.currentData() or "png")
        cfg.copy_after_save = self.copy_after_save.isChecked()
        cfg.hotkey_capture = _seq_to_spec(self.hotkey_capture.keySequence())
        cfg.hotkey_clipboard = _seq_to_spec(self.hotkey_clipboard.keySequence())
        cfg.ocr_lang = self.ocr_lang.currentText().strip() or "chi_sim+eng"
        cfg.ocr_psm = int(self.ocr_psm.currentData() or 6)
        cfg.ocr_preprocess = self.ocr_preprocess.isChecked()
        cfg.autostart = self.autostart.isChecked()
        cfg.translate.base_url = self.base_url.text().strip()
        cfg.translate.api_key = self.api_key.text().strip()
        cfg.translate.model = self.model.text().strip() or "deepseek-chat"
        cfg.translate.target_lang = self.target_lang.text().strip() or "简体中文"
        cfg.translate.stream = self.stream.isChecked()
        cfg.history_persist = self.history_persist.isChecked()
        cfg.history_limit = int(self.history_limit.value())
        cfg.history_days = int(self.history_days.value())
        return cfg

    @staticmethod
    def _validate(cfg: Config) -> str:
        if not cfg.save_dir:
            return "保存目录不能为空"
        for label, spec in (("截图热键", cfg.hotkey_capture), ("剪贴板热键", cfg.hotkey_clipboard)):
            if not spec:
                return f"{label}不能为空"
            _mods, key = parse_hotkey(spec)
            if not key:
                return f"{label}需要包含一个普通按键（如 A / V / F1）"
        if cfg.hotkey_capture == cfg.hotkey_clipboard:
            return "两个热键不能相同"
        try:
            Path(cfg.save_dir).expanduser()
        except (OSError, ValueError) as exc:
            return f"保存目录无效：{exc}"
        return ""

    # ------------------------------------------------------------ 动作
    def _browse_dir(self) -> None:
        start_dir = Path(self.save_dir.text() or "~").expanduser()
        start = str(start_dir)

        # 优先使用系统原生文件管理器对话框（如 zenity / kdialog），避免 Qt 内置 QFileDialog 在 Linux 回退时的白屏/无法选中的问题
        if shutil.which("zenity"):
            try:
                cmd = [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    f"--title=选择截图保存目录",
                    f"--filename={start.rstrip('/')}/",
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    self.save_dir.setText(res.stdout.strip())
                    return
                elif res.returncode == 1:
                    # 用户点击了取消
                    return
            except Exception:
                pass

        if shutil.which("kdialog"):
            try:
                cmd = ["kdialog", "--getexistingdirectory", start, "--title", "选择截图保存目录"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    self.save_dir.setText(res.stdout.strip())
                    return
                elif res.returncode == 1:
                    return
            except Exception:
                pass

        # 回退使用 Qt 标准目录对话框（优先原生系统对话框，不强加 DontUseNativeDialog）
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择截图保存目录",
            start,
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self.save_dir.setText(chosen)

    def _on_defaults(self) -> None:
        self._apply_to_widgets(Config())
        self.set_status("已恢复默认值，点击保存后生效", ok=True)

    def _on_save(self) -> None:
        cfg = self._build_config()
        err = self._validate(cfg)
        if err:
            self.set_status(err, ok=False)
            return
        try:
            cfg.save()
        except OSError as exc:
            self.set_status(f"写入配置失败：{exc}", ok=False)
            return
        try:
            autostart.set_enabled(cfg.autostart)
        except OSError as exc:
            self.set_status(f"配置已保存，但开机自启设置失败：{exc}", ok=False)
            return
        self.cfg = cfg
        self.saved.emit(cfg)
        self.close()

    def _on_test_translate(self) -> None:
        if self._test_thread is not None and self._test_thread.isRunning():
            return
        cfg = self._build_config()
        if not cfg.translate.api_key:
            self.test_status.setText("请先填写 API Key")
            self.test_status.setStyleSheet("color:#ff8b8b;")
            return
        self.btn_test.setEnabled(False)
        self.test_status.setText("测试中…")
        self.test_status.setStyleSheet("color:#929298;")
        self._test_thread = _TranslateTestThread(cfg, self)
        self._test_thread.result.connect(self._on_test_result)
        self._test_thread.start()

    def _on_test_result(self, ok: bool, message: str) -> None:
        self.btn_test.setEnabled(True)
        if ok:
            preview = message.strip().replace("\n", " ")[:60]
            self.test_status.setText(f"连接成功：{preview}")
            self.test_status.setStyleSheet("color:#7fd48a;")
        else:
            self.test_status.setText(f"失败：{message[:160]}")
            self.test_status.setStyleSheet("color:#ff8b8b;")

    def set_status(self, message: str, ok: bool = True) -> None:
        self.status.setText(message)
        self.status.setStyleSheet("color:#7fd48a;" if ok else "color:#ff8b8b;")

    def closeEvent(self, ev) -> None:
        self.closed.emit()
        super().closeEvent(ev)
