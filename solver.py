"""阿里验证码 1.0 / 2.0 打码求解器 —— 核心流程。

典型用法：
    async with AliyunCaptchaSolver(page, provider="openai") as solver:
        ok = await solver.solve_once()  # 解决一次当前页面验证码
"""
from __future__ import annotations

import asyncio
import math
import os
import random
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image
from playwright.async_api import Page, ElementHandle

from .detector import (
    CaptchaContext, CaptchaKind, detect_captcha, image_to_page,
)
from .prompts import (
    SYSTEM_PROMPT, compute_slider_offset, parse_decision, parse_track,
    prompt_ai_track, prompt_ai_track_visionless,
    prompt_click_order, prompt_rotate, prompt_slider_gap, prompt_slider_target,
)
from .vision import BaseVisionAdapter, build_adapter


# ---- 工具函数 ----
def _img_size(path: str) -> Tuple[int, int]:
    with Image.open(path) as im:
        return im.size


# ---------------------------------------------------------------------------
# 轨迹模拟：贝塞尔曲线 + 加速度扰动
# ---------------------------------------------------------------------------
def human_like_track(distance: int, duration_ms: int = 520,
                     steps: int = 14) -> List[Tuple[int, int, int]]:
    """返回 [(dx, dy, dt_ms), ...] 的拟人轨迹。

    策略（v3，无过冲）：
      1. 起步停顿 80~150ms
      2. 主体：ease-out 三段缓动到 95%
      3. 末段慢爬到 100%，不过冲、不回拉
      4. 到达后 ±1px 微抖 2~3 次
      5. 松手前停顿 40~100ms
      6. Y 轴全程自然抖动
    """
    if distance == 0:
        return [(0, 0, duration_ms)]
    sign = 1 if distance > 0 else -1
    distance = abs(distance)
    pts: List[Tuple[int, int, int]] = []

    # 起步停顿
    pts.append((0, 0, random.randint(80, 150)))

    # 主体（80% 时长）
    main_steps = max(10, steps - 2)
    main_duration = int(duration_ms * 0.8)
    for i in range(1, main_steps + 1):
        t = i / main_steps
        if t < 0.2:
            eased = (t / 0.2) ** 2 * 0.2
        elif t < 0.7:
            eased = 0.2 + (t - 0.2) / 0.5 * 0.5
        else:
            u = (t - 0.7) / 0.3
            eased = 0.7 + (1 - (1 - u) ** 2) * 0.3
        x = distance * eased
        y = random.uniform(-0.5, 0.5) if 0.2 < t < 0.8 else random.uniform(-0.2, 0.2)
        dt = int(main_duration / main_steps * random.uniform(0.85, 1.15))
        pts.append((int(round(x * sign)), int(round(y)), dt))

    # 微抖
    jitter_count = random.randint(2, 3)
    jitter_duration = int(duration_ms * 0.12)
    for _ in range(jitter_count):
        jx = distance + random.choice([-1, 0, 0, 1])
        jy = random.uniform(-0.3, 0.3)
        dt = int(jitter_duration / jitter_count * random.uniform(0.9, 1.1))
        pts.append((int(round(jx * sign)), int(round(jy)), dt))

    # 松手前停顿
    pts.append((int(round(distance * sign)), 0, random.randint(40, 100)))
    return pts


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------
class AliyunCaptchaSolver:
    def __init__(self, page: Page, *,
                 provider: Optional[str] = None,
                 adapter: Optional[BaseVisionAdapter] = None,
                 max_retry: int = 3,
                 debug_dir: Optional[str] = None,
                 use_ai_track: bool = False):
        self.page = page
        self.adapter = adapter or build_adapter(
            provider or os.getenv("SOLVER_MODEL_PROVIDER", "qwen")
        )
        self.max_retry = max_retry
        self.debug_dir = debug_dir
        self.use_ai_track = use_ai_track
        if self.debug_dir:
            Path(self.debug_dir).mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    # ----------------------------------------------------------------
    # 公开 API
    # ----------------------------------------------------------------
    async def solve_once(self, timeout: float = 8.0) -> bool:
        ctx = await detect_captcha(self.page, timeout=timeout)
        if not ctx:
            print("[solver] 页面未发现验证码")
            return False

        for attempt in range(1, self.max_retry + 1):
            print(f"[solver] 第 {attempt}/{self.max_retry} 次 (kind={ctx.kind.value}, v={ctx.version})")
            try:
                if ctx.kind == CaptchaKind.SLIDER_V1:
                    ok = await self._solve_v1_slider(ctx)
                elif ctx.kind == CaptchaKind.SLIDER_V2:
                    ok = await self._solve_v2_slider(ctx)
                elif ctx.kind == CaptchaKind.CLICK_TEXT:
                    ok = await self._solve_click_text(ctx)
                elif ctx.kind == CaptchaKind.ROTATE:
                    ok = await self._solve_rotate(ctx)
                else:
                    print(f"[solver] 暂不支持的题型: {ctx.kind}")
                    return False
            except Exception as e:
                print(f"[solver] 异常: {e!r}")
                ok = False

            if ok and await self._is_passed(ctx):
                print("[solver] 通过!")
                return True
            print("[solver] 未通过，准备重试")
            await self._refresh_if_needed(ctx)
            await asyncio.sleep(0.6 + random.random() * 0.6)

        return False

    # ----------------------------------------------------------------
    # 1.0 滑块
    # ----------------------------------------------------------------
    async def _solve_v1_slider(self, ctx: CaptchaContext) -> bool:
        if not ctx.bg_image or not ctx.slider_handle:
            return False
        img_path = await self._snap_element(ctx.bg_image, "v1_bg")
        prompt = prompt_slider_target(self._image_size(img_path))
        text = self.adapter.ask(img_path, prompt, system=SYSTEM_PROMPT)
        dec = parse_decision(text)
        if not dec.is_known or dec.target_x is None:
            print(f"[solver] 模型未识别: {text[:120]}")
            return False
        offset = compute_slider_offset(dec)
        if not offset:
            return False
        return await self._drag_handle(ctx.slider_handle, offset[0],
                                        bg_path=img_path)

    # ----------------------------------------------------------------
    # 2.0 滑块
    # ----------------------------------------------------------------
    async def _solve_v2_slider(self, ctx: CaptchaContext) -> bool:
        if not ctx.bg_image or not ctx.slider_handle:
            return False
        bg_path = await self._snap_element(ctx.bg_image, "v2_bg")
        prompt = prompt_slider_gap(self._image_size(bg_path))
        text = self.adapter.ask(bg_path, prompt, system=SYSTEM_PROMPT)
        dec = parse_decision(text)
        offset = compute_slider_offset(dec)
        if not offset:
            print(f"[solver] 模型未识别缺口: {text[:120]}")
            return False
        dx, dy = offset
        print(f"[solver] 决策 dx={dx} dy={dy}")
        return await self._drag_handle(ctx.slider_handle, dx, bg_path=bg_path)

    # ----------------------------------------------------------------
    # 点选文字
    # ----------------------------------------------------------------
    async def _solve_click_text(self, ctx: CaptchaContext) -> bool:
        if not ctx.bg_image:
            return False
        img_path = await self._snap_element(ctx.bg_image, "click")
        prompt = prompt_click_order(self._image_size(img_path)) + \
                 f"\n题目文字：{ctx.instruction_text}"
        text = self.adapter.ask(img_path, prompt, system=SYSTEM_PROMPT)
        dec = parse_decision(text)
        if not dec.points:
            print(f"[solver] 点选识别失败: {text[:120]}")
            return False
        img_box = await ctx.bg_image.bounding_box()
        iw, ih = self._image_size(img_path)
        page_points = [image_to_page(img_box, iw, ih, x, y) for (x, y) in dec.points]
        for (px, py) in page_points:
            await self.page.mouse.move(px, py)
            await asyncio.sleep(0.05 + random.random() * 0.1)
            await self.page.mouse.down()
            await asyncio.sleep(0.04)
            await self.page.mouse.up()
            await asyncio.sleep(0.2 + random.random() * 0.15)
        return True

    # ----------------------------------------------------------------
    # 旋转
    # ----------------------------------------------------------------
    async def _solve_rotate(self, ctx: CaptchaContext) -> bool:
        if not ctx.bg_image:
            return False
        img_path = await self._snap_element(ctx.bg_image, "rotate")
        prompt = prompt_rotate(self._image_size(img_path))
        text = self.adapter.ask(img_path, prompt, system=SYSTEM_PROMPT)
        dec = parse_decision(text)
        if dec.angle is None:
            print(f"[solver] 旋转识别失败: {text[:120]}")
            return False
        return await self._rotate_to(ctx, dec.angle)

    async def _rotate_to(self, ctx: CaptchaContext, angle: float) -> bool:
        handle = ctx.slider_handle
        if not handle:
            return False
        box = await handle.bounding_box()
        if not box:
            return False
        start = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await self.page.mouse.move(*start)
        await self.page.mouse.down()
        await self.page.mouse.move(start[0] + 30, start[1], steps=10)
        await self.page.mouse.up()
        steps = int(angle / 5)
        for _ in range(abs(steps)):
            key = "ArrowRight" if steps > 0 else "ArrowLeft"
            await self.page.keyboard.press(key)
            await asyncio.sleep(0.04)
        return True

    # ----------------------------------------------------------------
    # 拖动（AI 轨迹 或 贝塞尔兜底）
    # ----------------------------------------------------------------
    async def _drag_handle(self, handle: ElementHandle, dx: int, dy: int = 0,
                           bg_path: str = "") -> bool:
        if not handle:
            return False
        box = await handle.bounding_box()
        if not box:
            return False
        start = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

        if self.use_ai_track and self.adapter:
            track = await self._ai_track(abs(dx), bg_path)
        else:
            track = human_like_track(int(dx),
                                     duration_ms=800 + random.randint(-150, 250))

        print(f"[solver] 轨迹共 {len(track)} 步, ai={self.use_ai_track}")

        await self.page.mouse.move(*start)
        await asyncio.sleep(0.1 + random.random() * 0.15)
        await self.page.mouse.down()
        for (x, y, dt) in track:
            await asyncio.sleep(dt / 1000.0)
            sign = 1 if dx >= 0 else -1
            target = (start[0] + float(x) * sign, start[1] + float(y))
            await self.page.mouse.move(target[0], target[1], steps=3)
        await asyncio.sleep(0.05 + random.random() * 0.1)
        await self.page.mouse.up()
        return True

    async def _ai_track(self, distance: int,
                        bg_path: str = "") -> List[Tuple[float, float, int]]:
        """调用视觉模型看图生成拟人轨迹。"""
        from .prompts import parse_track, prompt_ai_track, prompt_ai_track_visionless
        from .prompts import _fallback_track

        if bg_path and os.path.isfile(bg_path):
            prompt = prompt_ai_track(distance, _img_size(bg_path))
            text = self.adapter.ask(bg_path, prompt, system=SYSTEM_PROMPT)
        else:
            prompt = prompt_ai_track_visionless(distance)
            try:
                text = self.adapter.ask("", prompt, system=SYSTEM_PROMPT)
            except Exception:
                return _fallback_track(distance)

        print(f"[solver] AI 轨迹返回: {text[:150]}")
        track = parse_track(text, distance)
        print(f"[solver] AI 轨迹解析: {len(track)} 步, "
              f"总dx={sum(p[0] for p in track):.1f}")
        return track

    # ----------------------------------------------------------------
    # 工具
    # ----------------------------------------------------------------
    async def _snap_element(self, elem: ElementHandle, tag: str) -> str:
        path = os.path.join(self.debug_dir or tempfile.gettempdir(),
                            f"cap_{tag}_{int(time.time()*1000)}.png")
        await elem.screenshot(path=path, omit_background=False)
        return path

    def _image_size(self, path: str) -> Tuple[int, int]:
        with Image.open(path) as im:
            return im.size

    async def _is_passed(self, ctx: CaptchaContext) -> bool:
        from .detector import SELECTORS
        try:
            await self.page.wait_for_function(
                f"() => !!document.querySelector('{SELECTORS['success']}')",
                timeout=1500,
            )
            return True
        except Exception:
            try:
                box = await (ctx.wrapper.bounding_box() if ctx.wrapper else None)
                if not box or box["height"] < 5:
                    return True
            except Exception:
                return True
            return False

    async def _refresh_if_needed(self, ctx: CaptchaContext):
        from .detector import SELECTORS
        for sel in SELECTORS["refresh"].split(", "):
            try:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.4)
                    new_ctx = await detect_captcha(self.page, timeout=4.0)
                    if new_ctx:
                        ctx.__dict__.update(new_ctx.__dict__)
                    return
            except Exception:
                continue
