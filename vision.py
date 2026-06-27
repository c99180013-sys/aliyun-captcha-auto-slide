"""多模态视觉模型适配层。

所有适配器必须实现 ask(image_path, prompt) -> str。
"""
from __future__ import annotations

import base64
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import requests


class BaseVisionAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def ask(self, image_path: str, prompt: str, *,
            system: Optional[str] = None,
            temperature: float = 0.0,
            max_tokens: int = 1024) -> str:
        """发送图片 + prompt，返回模型原始文本。"""


# ---------------------------------------------------------------------------
# 通义千问 Qwen-VL（DashScope OpenAI 兼容接口）
# ---------------------------------------------------------------------------
class QwenVLAdapter(BaseVisionAdapter):
    name = "qwen"

    def __init__(self, api_key: str, model: str = "qwen-vl-max"):
        self.api_key = api_key
        self.model = model

    def ask(self, image_path, prompt, *, system=None, temperature=0.0, max_tokens=1024):
        from openai import OpenAI
        client = OpenAI(
            api_key=self.api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        b64 = _b64_image(image_path)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        })
        resp = client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# OpenAI 兼容协议（OpenAI、DeepSeek、豆包、vLLM 等通用）
# ---------------------------------------------------------------------------
class OpenAICompatAdapter(BaseVisionAdapter):
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def ask(self, image_path, prompt, *, system=None, temperature=0.0, max_tokens=1024):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        b64 = _b64_image(image_path)
        mime = _mime_of(image_path)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        })
        resp = client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Anthropic Claude
# ---------------------------------------------------------------------------
class ClaudeAdapter(BaseVisionAdapter):
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.api_key = api_key
        self.model = model

    def ask(self, image_path, prompt, *, system=None, temperature=0.0, max_tokens=1024):
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        b64 = _b64_image(image_path)
        mime = _mime_of(image_path)
        kwargs = dict(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        return "".join(
            block.text for block in resp.content
            if getattr(block, "type", "") == "text"
        ).strip()


# ---------------------------------------------------------------------------
# 自定义 HTTP 端点
# ---------------------------------------------------------------------------
class CustomHTTPAdapter(BaseVisionAdapter):
    name = "custom"

    def __init__(self, endpoint: str, headers: Optional[dict] = None,
                 timeout: int = 60, method: str = "POST"):
        self.endpoint = endpoint
        self.headers = headers or {}
        self.timeout = timeout
        self.method = method.upper()

    def ask(self, image_path, prompt, *, system=None, temperature=0.0, max_tokens=1024):
        b64 = _b64_image(image_path)
        payload = {
            "image_base64": b64, "prompt": prompt,
            "system": system, "temperature": temperature, "max_tokens": max_tokens,
        }
        for attempt in range(3):
            r = requests.request(self.method, self.endpoint, json=payload,
                                 headers=self.headers, timeout=self.timeout)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, str):
                    return data.strip()
                return (data.get("text") or data.get("answer")
                        or data.get("content") or "").strip()
            time.sleep(1 + attempt)
        raise RuntimeError(
            f"custom vision endpoint failed: {r.status_code} {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# 中转站兼容适配器（OpenAI API 路径 + Anthropic 图片格式）
# ---------------------------------------------------------------------------
class _RelayAdapter(BaseVisionAdapter):
    name = "relay"

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def ask(self, image_path, prompt, *, system=None, temperature=0.0, max_tokens=1024):
        b64 = _b64_image(image_path)
        mime = _mime_of(image_path)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": b64,
                }},
                {"type": "text", "text": prompt},
            ],
        })
        payload = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        for attempt in range(3):
            r = requests.post(url, json=payload,
                              headers={"Authorization": f"Bearer {self.api_key}",
                                       "Content-Type": "application/json"},
                              timeout=120)
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"].strip()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f"relay {r.status_code}: {r.text[:300]}")
        raise RuntimeError(
            f"relay failed after 3 retries: {r.status_code} {r.text[:300]}"
        )


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _b64_image(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _mime_of(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(ext, "image/png")


def build_adapter(provider: str = "qwen") -> BaseVisionAdapter:
    p = (provider or "qwen").lower()
    if p == "qwen":
        key = os.getenv("DASHSCOPE_API_KEY")
        if not key:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置")
        return QwenVLAdapter(key, os.getenv("QWEN_MODEL", "qwen-vl-max"))
    if p == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY 未配置")
        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        # 如果模型名含 claude，中转站可能需要 Anthropic 图片格式
        if "claude" in model.lower():
            return _RelayAdapter(
                key,
                os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model,
            )
        return OpenAICompatAdapter(
            key,
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model,
        )
    if p == "claude":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY 未配置")
        return ClaudeAdapter(
            key, os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
        )
    if p == "custom":
        endpoint = os.getenv("CUSTOM_VISION_ENDPOINT")
        if not endpoint:
            raise RuntimeError("CUSTOM_VISION_ENDPOINT 未配置")
        return CustomHTTPAdapter(endpoint)
    raise ValueError(f"未知 provider: {provider}")
