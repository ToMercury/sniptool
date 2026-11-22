"""DeepSeek 翻译：流式 SSE，线程里跑，不阻塞 UI。"""
from __future__ import annotations

import json
import threading
from typing import Callable

import httpx

from .config import Config

SYSTEM_PROMPT = (
    "你是截图翻译引擎。把用户给出的文本翻译成{lang}。"
    "只输出译文本身，不要解释、不要加引号、不要保留原文。"
    "保留原文的换行结构与专有名词；若原文已是目标语言，原样返回。"
)


class TranslateError(RuntimeError):
    pass


def _payload(cfg: Config, text: str) -> dict:
    tr = cfg.translate
    return {
        "model": tr.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(lang=tr.target_lang)},
            {"role": "user", "content": text},
        ],
        "stream": bool(tr.stream),
        "temperature": float(tr.temperature),
    }


def _headers(cfg: Config) -> dict:
    return {
        "Authorization": f"Bearer {cfg.translate.api_key.strip()}",
        "Content-Type": "application/json",
    }


def translate_once(cfg: Config, text: str, timeout: float = 60.0) -> str:
    """非流式，便于命令行测试与小文本。"""
    if not cfg.translate.api_key.strip():
        raise TranslateError("未配置 API Key")
    url = cfg.translate.base_url.rstrip("/") + "/chat/completions"
    body = _payload(cfg, text)
    body["stream"] = False
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=_headers(cfg))
    except httpx.HTTPError as exc:
        raise TranslateError(f"网络错误：{exc}") from exc
    if r.status_code != 200:
        raise TranslateError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise TranslateError(f"返回格式异常: {r.text[:200]}") from exc


def translate_stream(cfg: Config, text: str, on_delta: Callable[[str], None],
                     timeout: float = 120.0) -> str:
    """流式；on_delta 在调用线程里被调用（由调用方负责切回 UI 线程）。"""
    if not cfg.translate.api_key.strip():
        raise TranslateError("未配置 API Key")
    url = cfg.translate.base_url.rstrip("/") + "/chat/completions"
    body = _payload(cfg, text)
    out: list[str] = []
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", url, json=body, headers=_headers(cfg)) as r:
                if r.status_code != 200:
                    raise TranslateError(f"HTTP {r.status_code}: {r.read()[:200]!r}")
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        j = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = j["choices"][0]["delta"].get("content") or ""
                    except (KeyError, IndexError):
                        continue
                    if delta:
                        out.append(delta)
                        on_delta(delta)
    except httpx.HTTPError as exc:
        raise TranslateError(f"网络错误：{exc}") from exc
    return "".join(out)


class StreamWorker(threading.Thread):
    """在后台线程跑流式请求；delta 通过回调抛给上层（上层用信号切回主线程）。"""

    def __init__(self, cfg: Config, text: str, on_delta, on_done, on_error):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.text = text
        self.on_delta = on_delta
        self.on_done = on_done
        self.on_error = on_error

    def run(self) -> None:
        try:
            full = translate_stream(self.cfg, self.text, self.on_delta)
        except TranslateError as exc:
            self.on_error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.on_error(repr(exc))
            return
        self.on_done(full)
