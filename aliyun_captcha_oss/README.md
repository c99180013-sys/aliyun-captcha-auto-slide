# aliyun-captcha-solver

基于多模态视觉大模型的阿里验证码（1.0/2.0）自动打码框架。

## 支持的题型

| 题型 | 1.0 | 2.0 | 描述 |
|---|---|---|---|
| 滑块 | ✓ | ✓ | 拼图块拖动到缺口 |
| 点选文字 | — | ✓ | 按顺序点击图中的文字 |
| 旋转拼图 | — | ✓ | 旋转拼图到正确角度 |

## 支持的视觉模型

| 模型 | provider 参数 | 需要环境变量 |
|---|---|---|
| 通义千问 Qwen-VL | `qwen` | `DASHSCOPE_API_KEY` |
| OpenAI GPT-4o | `openai` | `OPENAI_API_KEY` |
| Anthropic Claude | `claude` | `ANTHROPIC_API_KEY` |
| 自建/中转站兼容接口 | `openai` | `OPENAI_API_KEY` + `OPENAI_BASE_URL` |
| 自定义 HTTP 端点 | `custom` | `CUSTOM_VISION_ENDPOINT` |

## 快速开始

### 安装

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # 编辑 .env 填入视觉模型 API Key
```

### 运行

```bash
# 测试任意包含阿里验证码的页面
python demo.py --url https://your-captcha-page.com --use-vision

# 指定模型供应商
python demo.py --url https://your-page.com --use-vision --provider openai
python demo.py --url https://your-page.com --use-vision --provider qwen

# 无头模式
python demo.py --url https://your-page.com --use-vision --headless
```

### 代码集成

```python
from aliyun_captcha_oss import AliyunCaptchaSolver
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto("https://your-page.com")

    async with AliyunCaptchaSolver(page, provider="openai") as solver:
        ok = await solver.solve_once()  # 返回 True/False

    await browser.close()
```

## 工作原理

```
┌─────────┐   截图    ┌──────────┐   坐标   ┌────────┐
│ 验证码  │ ────────→ │ 视觉模型 │ ───────→ │ 模拟操作│
│ 页面    │ ←──────── │          │ ←─────── │        │
└─────────┘   反馈    └──────────┘   重试   └────────┘
```

1. **DOM 检测** — 自动识别页面中的阿里验证码类型（1.0/2.0 滑块、点选、旋转）
2. **截图发给视觉模型** — 用 prompt 描述缺口定位任务
3. **解析坐标** — 模型返回 JSON，解析缺口/拼图块坐标
4. **拟人拖动** — 支持两种模式：
   - 内置：贝塞尔曲线 + 随机抖动（默认）
   - AI 轨迹：视觉模型看图生成个性化路径（`--ai-track`）
5. **验证结果检测** — 检测 success 元素或 wrapper 消失，失败自动重试

## AI 轨迹模式

```bash
# 让视觉模型看图决定拖动的鼠标路径（每次都不一样的拟人轨迹）
python demo.py --url https://your-page.com --use-vision --ai-track
```

模型会收到验证码截图和距离信息，返回完整的 `[(dx, dy, dt), ...]` 轨迹。与硬编码贝塞尔曲线相比，AI 轨迹每次都不同，更难被风控系统识别为机器行为。

模型无响应时自动 fallback 到内置缓动轨迹。

兼容 **Anthropic** (image source 块) 和 **OpenAI** (image_url 块) 两种图片格式，自动检测模型类型切换。

## 目录结构

```
aliyun_captcha_oss/
├── __init__.py       # 包入口
├── solver.py         # 主求解器
├── detector.py       # DOM 检测
├── prompts.py        # 视觉模型 prompt 模板
├── vision.py         # 多模态适配器
├── local_detector.py # 本地像素检测
├── .env.example      # 配置模板
└── requirements.txt  # 依赖
demo.py               # 演示脚本
```

## License

MIT
