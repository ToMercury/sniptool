"""OCR：tesseract 从 stdin 读图，避免临时文件。"""
from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .imaging import png_bytes

TESSERACT = shutil.which("tesseract") or "tesseract"


class OcrUnavailable(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("tesseract") is not None


def languages() -> list[str]:
    try:
        out = subprocess.run([TESSERACT, "--list-langs"], capture_output=True, text=True, timeout=10)
        lines = (out.stdout or "").splitlines()
        return [ln.strip() for ln in lines[1:] if ln.strip() and " " not in ln.strip()] or []
    except (OSError, subprocess.SubprocessError):
        return []


def _prepare(img: QImage, preprocess: bool) -> QImage:
    out = img
    # 小字（高度小）才放大：实测 2x 只多认约 10 字符但慢 300ms
    if preprocess and img.height() < 320:
        out = out.scaled(
            img.width() * 2, img.height() * 2,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    if preprocess:
        out = out.convertToFormat(QImage.Format.Format_Grayscale8)
    return out


def ocr_image(img: QImage, lang: str = "chi_sim+eng", psm: int = 6, preprocess: bool = True) -> str:
    if not available():
        raise OcrUnavailable("未安装 tesseract")
    data = png_bytes(_prepare(img, preprocess))
    cmd = [TESSERACT, "-", "-", "-l", lang, "--psm", str(psm)]
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise OcrUnavailable("OCR 超时") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "ignore").strip()
        raise OcrUnavailable(err or f"tesseract 退出码 {proc.returncode}")
    return proc.stdout.decode("utf-8", "ignore").strip()
