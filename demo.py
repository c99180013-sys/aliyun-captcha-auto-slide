"""阿里验证码 1.0/2.0 打码演示脚本。

用法：
  # 测试本地像素检测（不调用视觉模型）
  python demo.py --url https://your-page.com

  # 使用通义千问视觉模型
  python demo.py --url https://your-page.com --use-vision

  # 使用 OpenAI 兼容中转站
  python demo.py --url https://your-page.com --use-vision --provider openai

  # 无头模式
  python demo.py --url https://your-page.com --use-vision --headless

说明：
  - 脚本打开目标页面，点击触发元素（如登录按钮），等待验证码弹出
  - 截图发给视觉模型 → 模型输出缺口坐标 → 模拟拖动/点选/旋转
  - 重复执行直到验证通过或达到最大重试次数
  - 所有截图保存在 ./captcha_debug/ 目录
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aliyun_captcha_oss import AliyunCaptchaSolver


async def main():
    parser = argparse.ArgumentParser(description="阿里验证码打码演示")
    parser.add_argument("--url", required=True,
                        help="目标页面 URL（包含阿里验证码 1.0/2.0）")
    parser.add_argument("--use-vision", action="store_true",
                        help="启用多模态视觉模型（禁用时仅返回本地像素检测结果）")
    parser.add_argument("--provider", default=os.getenv("SOLVER_MODEL_PROVIDER", "qwen"),
                        help="模型供应商: qwen / openai / claude / custom")
    parser.add_argument("--headless", action="store_true",
                        help="无头模式（调试时建议关掉）")
    parser.add_argument("--debug-dir", default="./captcha_debug",
                        help="调试截图保存目录")
    parser.add_argument("--max-retry", type=int, default=3,
                        help="最大重试次数")
    parser.add_argument("--ai-track", action="store_true",
                        help="启用 AI 轨迹生成：让视觉模型看图决定拖动轨迹")
    args = parser.parse_args()

    load_dotenv()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        # 反检测
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        page = await context.new_page()

        url = args.url
        # 支持本地 HTML 文件
        if not url.startswith("http"):
            url = "file:///" + str(Path(url).resolve()).replace("\\", "/")
        print(f"[demo] 打开 {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # 可选：自动点击触发按钮（可根据需要自定义）
        print("[demo] 尝试自动触发验证码...")
        triggers = [
            "#trigger", "#captcha-button",
            "text=登录", "text=注册", "text=发送验证码", "text=Click to Verify",
            "button[type='submit']", "#J_SubmitStatic",
            "[class*='submit']", "[class*='login']",
        ]
        triggered = False
        for sel in triggers:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    print(f"[demo] 点击触发: {sel}")
                    triggered = True
                    await page.wait_for_timeout(1500)
                    break
            except Exception:
                continue
        if not triggered:
            print("[demo] 未能自动触发，等待验证码自然出现...")

        # 打码
        async with AliyunCaptchaSolver(
            page,
            provider=args.provider if args.use_vision else None,
            max_retry=args.max_retry,
            debug_dir=args.debug_dir,
            use_ai_track=args.ai_track,
        ) as solver:
            ok = await solver.solve_once(timeout=10.0)

        # 结果截图
        result_dir = Path(args.debug_dir)
        result_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(result_dir / "demo_result.png"))
        print(f"\n[demo] 结果截图: {result_dir / 'demo_result.png'}")

        if ok:
            print("[demo] 验证通过!")
        else:
            print("[demo] 验证未通过")

        await browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
