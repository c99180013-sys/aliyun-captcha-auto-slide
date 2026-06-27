# Aliyun CAPTCHA Auto-Slide Solver

基于多模态视觉大模型的阿里云滑块验证码自动识别与通过工具。

## 特性

- 🧠 **多模态 AI 识别**：支持通义千问 Qwen-VL、OpenAI GPT-4o、Claude、自定义 HTTP 端点
- 🎯 **全题型支持**：滑块验证（1.0/2.0）、点选文字、旋转拼图
- 🔧 **本地像素检测**：无需 AI 模型即可运行基础检测（可选）
- 🎭 **人类轨迹模拟**：六阶段拟人轨迹生成（加速 → 主体 → 减速 → 微调）
- 🔌 **即插即用**：一个 Python 文件调用，支持异步上下文管理

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 配置环境变量

复制 `.env.example` 为 `.env`，根据使用的视觉模型填写对应配置：

```bash
# 通义千问（推荐，国内访问快）
DASHSCOPE_API_KEY=sk-your-dashscope-key
QWEN_MODEL=qwen-vl-max

# OpenAI GPT-4o（或兼容中转站）
OPENAI_API_KEY=sk-your-openai-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-your-key
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# 行为配置
SOLVER_MODEL_PROVIDER=qwen  # qwen / openai / claude / custom
SOLVER_MAX_RETRY=3
```

### 运行示例

```bash
# 本地 HTML 演示
python demo.py --url demo.html --use-vision --provider qwen

# 真实网站
python demo.py --url https://example.com/login --use-vision --headless

# 仅本地像素检测（不调用 AI）
python demo.py --url demo.html
```

## 使用方法

### 基础用法

```python
from playwright.async_api import async_playwright
from solver import AliyunCaptchaSolver

async def solve():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto("https://your-page.com")
        
        # 触发验证码
        await page.click("#login-button")
        
        # 自动识别并通过
        async with AliyunCaptchaSolver(
            page,
            provider="qwen",  # qwen / openai / claude
            max_retry=3,
            debug_dir="./captcha_debug"
        ) as solver:
            success = await solver.solve_once(timeout=10.0)
        
        if success:
            print("验证通过！")
        
        await browser.close()
```

### 高级配置

```python
solver = AliyunCaptchaSolver(
    page,
    provider="openai",           # 模型提供商
    max_retry=5,                 # 最大重试次数
    debug_dir="./debug",         # 调试截图目录
    use_ai_track=False,          # 使用 AI 生成轨迹（实验性）
)
```

## 支持的验证码类型

| 类型 | 说明 | 支持情况 |
|------|------|---------|
| 滑块 1.0 | 经典拼图滑块 | ✅ 完全支持 |
| 滑块 2.0 | 带旋转、形变的高级滑块 | ✅ 完全支持 |
| 点选文字 | 按顺序点击指定文字 | ✅ 完全支持 |
| 旋转拼图 | 拖动旋转图片至正确角度 | ✅ 完全支持 |

## 工作原理

1. **自动检测**：识别页面中的阿里云验证码类型（滑块/点选/旋转）
2. **截图分析**：
   - 本地像素检测：基于 canvas 透明度通道快速定位缺口
   - AI 视觉识别：将截图发送给多模态大模型，返回精确坐标
3. **轨迹生成**：六阶段人类拟人轨迹（贝塞尔曲线 + 随机抖动 + 暂停）
4. **自动重试**：失败后自动重试，直到成功或达到最大次数

## 项目结构

```
.
├── solver.py           # 核心求解器
├── vision.py          # 视觉模型适配层
├── detector.py        # 验证码类型检测
├── local_detector.py  # 本地像素检测
├── prompts.py         # AI 提示词模板
├── demo.py            # 演示脚本
├── demo.html          # 本地测试页面
├── requirements.txt   # 依赖清单
├── .env.example       # 环境变量模板
└── README.md
```

## 依赖项

- Python 3.8+
- playwright >= 1.45.0
- requests >= 2.31.0
- python-dotenv >= 1.0.0
- openai >= 1.0.0（使用 OpenAI/Qwen 时）
- anthropic >= 0.20.0（使用 Claude 时）

## 注意事项

⚠️ **免责声明**：本项目仅供学习研究使用，请勿用于非法用途。使用者需自行承担法律风险。

- 建议配置合理的重试次数（3-5次），避免频繁请求
- AI 模型调用产生费用，建议优先使用本地像素检测
- 真实环境中建议加入随机延迟，降低被检测风险
- 请遵守目标网站的服务条款和机器人协议

## 许可证

MIT License

Copyright (c) 2026

本软件按"原样"提供，不提供任何明示或暗示的保证。作者不对使用本软件造成的任何损害负责。
