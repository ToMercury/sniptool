"""配置：JSON 落在 $SNITTOOL_CONFIG 或 ~/.config/sniptool/config.json。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def config_path() -> Path:
    env = os.environ.get("SNITTOOL_CONFIG")
    if env:
        return Path(env)
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "sniptool" / "config.json"


def data_dir() -> Path:
    env = os.environ.get("SNITTOOL_DATA_DIR")
    if env:
        p = Path(env)
    else:
        p = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")) / "sniptool"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class TranslateConfig:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.3
    target_lang: str = "简体中文"
    stream: bool = True
    cache: bool = True


@dataclass
class Config:
    # 保存
    save_dir: str = str(Path.home() / "Pictures" / "Screenshots")
    save_template: str = "Shot_%Y%m%d_%H%M%S"
    save_format: str = "png"  # png | jpg | webp
    # 热键
    hotkey_capture: str = "ctrl+alt+a"
    hotkey_clipboard: str = ""  # disabled; set e.g. "ctrl+shift+v" to re-enable
    # OCR
    ocr_lang: str = "chi_sim+eng"
    ocr_psm: int = 6
    ocr_preprocess: bool = True
    # 行为
    autostart: bool = False
    copy_after_save: bool = False
    notify: bool = True
    # 剪贴板历史
    history_persist: bool = True
    history_limit: int = 100
    history_days: int = 7
    # 翻译
    translate: TranslateConfig = field(default_factory=TranslateConfig)

    def save(self) -> None:
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)  # 里面可能含 API Key
        except OSError:
            pass

    @classmethod
    def load(cls) -> "Config":
        p = config_path()
        if not p.exists():
            c = cls()
            c.save()
            return c
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        tr_raw = raw.pop("translate", None) or {}
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in raw.items() if k in known and k != "translate"}
        cfg = cls(**kwargs)
        tr_known = {f.name for f in fields(TranslateConfig)}
        cfg.translate = TranslateConfig(**{k: v for k, v in tr_raw.items() if k in tr_known})
        if not cfg.translate.api_key:
            cfg.translate.api_key = os.environ.get("SNITTOOL_DEEPSEEK_KEY", "")
        return cfg


def parse_hotkey(spec: str) -> tuple[int, str]:
    """'ctrl+alt+a' -> (修饰键掩码, keysym 名)。返回 X11 掩码，非 X11 平台忽略掩码。"""
    mods = 0
    key = ""
    for part in spec.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= 1 << 2  # ControlMask
        elif part == "alt":
            mods |= 1 << 3  # Mod1Mask
        elif part in ("super", "meta", "win"):
            mods |= 1 << 6  # Mod4Mask
        elif part == "shift":
            mods |= 1 << 0  # ShiftMask
        else:
            key = part
    return mods, key
