"""阿里验证码 1.0 / 2.0 DOM 检测与元素定位。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from playwright.async_api import Page


class CaptchaKind(str, Enum):
    UNKNOWN     = "unknown"
    SLIDER_V1   = "slider_v1"       # 1.0 老滑块
    SLIDER_V2   = "slider_v2"       # 2.0 滑块（含缺口 + 拼图）
    CLICK_TEXT  = "click_text"      # 点选文字
    ROTATE      = "rotate"          # 旋转


# --------------- 选择器库 ---------------
SELECTORS = {
    # 1.0
    "v1_wrapper":    "#nc_1_wrapper, .nc_wrapper, .nc-container",
    "v1_slide_btn":  ".nc_iconfont.btn_slide, .btn_slide, .nc_1_n1t",
    "v1_track":      ".nc_1_noc, .nc-scale, .nc_scale",
    # 2.0 弹窗
    "v2_dialog":     ".baxia-dialog, [class*='baxia-dialog'], .nc-cc",
    "v2_wrapper":    "[class*='baxia-captcha'], .nc_container, .nc-cc",
    "v2_slider_handle": (
        "[class*='baxia-slider'] [class*='handle'], "
        "[class*='nc-cc'] [class*='slider'] [class*='handle'], "
        "[class*='slider'] [class*='handle'], [class*='slider-btn'], "
        "[class*='slider'] button"
    ),
    "v2_bg_image": (
        "[class*='baxia'] img[src*='.jpg'], [class*='baxia'] img[src*='.png'], "
        "[class*='nc-cc'] img"
    ),
    "v2_puzzle": "[class*='baxia-puzzle'], [class*='puzzle-image'], "
                 "[class*='nc-cc'] [class*='puzzle']",
    "v2_instruct": "[class*='instruct'], [class*='prompt'], [class*='title']",
    # 通用
    "refresh": ".nc_1_refresh, .baxia-refresh, [class*='refresh'], [class*='change']",
    "success": ".nc-ccsuccess, .baxia-captcha-success, [class*='success'], [class*='passed']",
}


@dataclass
class CaptchaContext:
    kind: CaptchaKind = CaptchaKind.UNKNOWN
    version: str = "unknown"
    wrapper = None
    slider_handle = None
    bg_image = None
    puzzle = None
    instruction_text: str = ""
    bbox: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 检测入口
# ---------------------------------------------------------------------------
async def detect_captcha(page: Page, timeout: float = 8.0) -> Optional[CaptchaContext]:
    try:
        await page.wait_for_selector(
            ",".join([SELECTORS["v1_wrapper"], SELECTORS["v2_wrapper"],
                       SELECTORS["v2_dialog"]]),
            timeout=int(timeout * 1000),
        )
    except Exception:
        return None

    ctx = CaptchaContext()

    # 1.0
    v1 = await page.query_selector(SELECTORS["v1_wrapper"])
    if v1 and await v1.is_visible():
        await _fill_v1(ctx, page, v1)
        return ctx

    # 2.0
    v2 = await page.query_selector(SELECTORS["v2_dialog"]) or \
         await page.query_selector(SELECTORS["v2_wrapper"])
    if v2 and await v2.is_visible():
        await _fill_v2(ctx, page, v2)
        return ctx

    return ctx if ctx.kind != CaptchaKind.UNKNOWN else None


async def _fill_v1(ctx: CaptchaContext, page: Page, wrapper):
    ctx.version = "1.0"
    ctx.kind = CaptchaKind.SLIDER_V1
    ctx.wrapper = wrapper
    ctx.slider_handle = await wrapper.query_selector(SELECTORS["v1_slide_btn"]) or \
                        await wrapper.query_selector(".nc_1_n1z, .nc_1_n1t, button")
    ctx.bg_image = await wrapper.query_selector("img")
    box = await wrapper.bounding_box()
    if box:
        ctx.bbox = box


async def _fill_v2(ctx: CaptchaContext, page: Page, wrapper):
    ctx.version = "2.0"
    ctx.wrapper = wrapper

    handle = await wrapper.query_selector(SELECTORS["v2_slider_handle"])
    if not handle:
        for sel in [".nc_iconfont", "[class*='slider']", "[class*='btn']"]:
            handle = await wrapper.query_selector(sel)
            if handle:
                break
    ctx.slider_handle = handle

    ctx.bg_image = await wrapper.query_selector(SELECTORS["v2_bg_image"])
    if not ctx.bg_image:
        imgs = await wrapper.query_selector_all("img")
        biggest, biggest_area = None, 0
        for im in imgs:
            try:
                bb = await im.bounding_box()
            except Exception:
                continue
            if bb and (area := bb["width"] * bb["height"]) > biggest_area:
                biggest_area = area
                biggest = im
        ctx.bg_image = biggest

    ctx.puzzle = await wrapper.query_selector(SELECTORS["v2_puzzle"])

    text_el = await wrapper.query_selector(SELECTORS["v2_instruct"])
    if text_el:
        try:
            ctx.instruction_text = (await text_el.inner_text()).strip()
        except Exception:
            pass

    if ctx.puzzle and ctx.bg_image and ctx.slider_handle:
        ctx.kind = CaptchaKind.SLIDER_V2
    elif ctx.slider_handle and ctx.bg_image and not ctx.puzzle:
        ctx.kind = CaptchaKind.SLIDER_V2
    elif ctx.instruction_text and "旋转" in ctx.instruction_text:
        ctx.kind = CaptchaKind.ROTATE
    elif ctx.instruction_text:
        ctx.kind = CaptchaKind.CLICK_TEXT
    else:
        ctx.kind = CaptchaKind.SLIDER_V2

    if wrapper:
        try:
            box = await wrapper.bounding_box()
            if box:
                ctx.bbox = box
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 坐标转换
# ---------------------------------------------------------------------------
def image_to_page(img_box: dict, img_w: int, img_h: int, x: int, y: int) -> tuple:
    if not img_box:
        return (x, y)
    sx = img_box["width"] / img_w
    sy = img_box["height"] / img_h
    return (img_box["x"] + x * sx, img_box["y"] + y * sy)
