"""aliyun-captcha-solver: 基于多模态视觉大模型的阿里验证码（1.0/2.0）打码框架。

支持的题型：滑块（1.0/2.0）、点选文字、旋转拼图
支持的视觉模型：通义千问 Qwen-VL、OpenAI GPT-4o、Anthropic Claude、自定义 HTTP 端点
"""
from .solver import AliyunCaptchaSolver
from .vision import (
    BaseVisionAdapter, ClaudeAdapter, CustomHTTPAdapter,
    OpenAICompatAdapter, QwenVLAdapter, build_adapter,
)
from .detector import CaptchaContext, CaptchaKind, detect_captcha

__all__ = [
    "AliyunCaptchaSolver",
    "BaseVisionAdapter",
    "QwenVLAdapter",
    "OpenAICompatAdapter",
    "ClaudeAdapter",
    "CustomHTTPAdapter",
    "build_adapter",
    "CaptchaContext",
    "CaptchaKind",
    "detect_captcha",
]

__version__ = "0.2.0"
