"""打码 Prompt 模板 + 结果解析。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


SYSTEM_PROMPT = (
    "你是一个专业的验证码图像分析助手。你只输出严格的 JSON，"
    "不要任何解释、Markdown 代码块、前后缀。坐标以图像左上角为原点，"
    "单位是像素。无法判断时返回 {\"type\":\"unknown\"}。"
)


def prompt_slider_gap(image_size: Tuple[int, int]) -> str:
    w, h = image_size
    return (
        f"图片尺寸 {w}x{h} 像素。这是一道滑块验证码："
        "图中有【一块拼图缺口】在背景图上呈现为更暗的轮廓，"
        "还有一个【完整拼图块】漂浮在画布某处。\n"
        "请定位两处像素坐标：\n"
        "  - gap_x, gap_y：缺口中心的坐标\n"
        "  - piece_x, piece_y：拼图块中心的坐标\n\n"
        "输出严格 JSON，5 个字段都要有，不能重复 key：\n"
        '{"type":"slider","gap_x":123,"gap_y":80,"piece_x":15,"piece_y":85}\n'
        "若看不见则返回 {\"type\":\"unknown\"}。"
    )


def prompt_slider_target(image_size: Tuple[int, int]) -> str:
    w, h = image_size
    return (
        f"图片尺寸 {w}x{h} 像素。这是一道滑块验证码："
        "图中有【一块带阴影的拼图缺口】在背景图上，"
        "还有【一块拼图块】需要被拖动到缺口处。\n"
        "请定位【缺口中心】的像素坐标。"
        '严格输出 JSON：{"type":"slider","target_x":<int>,"target_y":<int>}\n'
        "若看不见则返回 {\"type\":\"unknown\"}。"
    )


def prompt_click_order(image_size: Tuple[int, int]) -> str:
    w, h = image_size
    return (
        f"图片尺寸 {w}x{h} 像素。题目文字会要求按特定顺序点击图中的若干个汉字/字符。"
        "请按题目要求的【顺序】输出这些字符的【像素中心坐标】。\n"
        '严格输出 JSON：{"type":"click","points":[[x,y],[x,y],...]}\n'
        "若题目不清楚则返回 {\"type\":\"unknown\"}。"
    )


def prompt_rotate(image_size: Tuple[int, int]) -> str:
    w, h = image_size
    return (
        f"图片尺寸 {w}x{h} 像素。图中有一张倾斜的图片需要转正。"
        "请判断它【逆时针】需要旋转多少度才能大致水平（0~360）。\n"
        '严格输出 JSON：{"type":"rotate","angle":<float>}\n'
        "若无法判断则返回 {\"type\":\"unknown\"}。"
    )


def prompt_ai_track(distance: int, image_size: Tuple[int, int]) -> str:
    """让模型看图，生成一段拟人的滑块拖动轨迹。"""
    w, h = image_size
    return (
        f"图片尺寸 {w}x{h} 像素。滑块需要水平向右拖动约 {distance} 像素到达缺口位置。\n\n"
        "请你模拟真人的鼠标拖动行为，生成从 x=0 到 x≈{distance} 的完整轨迹。\n\n"
        "真人的拖动特征：\n"
        "- 起始有短暂的停顿（80~180ms）\n"
        "- 前 20% 加速，中段匀速但有微小的速度波动\n"
        "- 末尾 10~15% 明显减速\n"
        "- 到达目标后有 2~3 次微小前后抖动（±1~2px）\n"
        "- Y 轴有微小的自然偏移（±0.5~1.5px）\n"
        "- 整个过程持续时间 400~800ms\n"
        "- 每步时间间隔 20~80ms 不等\n\n"
        "输出格式：严格 JSON 数组，每个元素为 [dx, dy, dt_ms]：\n"
        "- dx：水平位移（float，可带一位小数，累计最终应接近 {distance}）\n"
        "- dy：垂直偏移（float，可为正可负，绝对值 < 2）\n"
        "- dt_ms：该步耗时（int，单位毫秒）\n\n"
        f"示例（距离 100px）："
        '{"type":"track","points":[[0,0,120],[3.2,0.1,45],[5.8,-0.3,40],...]}\n'
        f"最终 dx 累计应在 {distance}±2 范围内。输出 JSON 即可。"
    )


def prompt_ai_track_visionless(distance: int) -> str:
    """不需要看图，仅根据距离让模型生成轨迹。"""
    return (
        f"模拟一段真人鼠标拖动轨迹，滑块需要水平向右拖动 {distance} 像素。\n\n"
        "真实人类拖动特征：\n"
        "- 起始停顿 80~180ms\n"
        "- 前 20% 加速，中段匀速带微波动，末尾减速\n"
        "- 到达后 2~3 次 ±1~2px 微抖\n"
        "- Y 轴 ±0.5~1.5px 自然偏移\n"
        "- 总时长 400~800ms，每步 20~80ms\n\n"
        "输出严格 JSON 数组（不要 Markdown 包裹）：\n"
        '{"type":"track","points":[[dx,dy,dt],[dx,dy,dt],...]}\n'
        f"最终 dx 累计 {distance}±2。"
    )


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
@dataclass
class SolverDecision:
    type: str
    target_x: Optional[int] = None
    target_y: Optional[int] = None
    piece_x: Optional[int] = None
    piece_y: Optional[int] = None
    dx: Optional[int] = None
    points: Optional[List[Tuple[int, int]]] = None
    angle: Optional[float] = None
    index: Optional[int] = None
    raw: str = ""

    @property
    def is_known(self) -> bool:
        return self.type != "unknown"


_JSON_RE = re.compile(r"\{.*\}", re.S)


def _get_int(data: dict, *keys) -> Optional[int]:
    for k in keys:
        v = data.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def parse_decision(text: str) -> SolverDecision:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    m = _JSON_RE.search(text)
    if not m:
        return SolverDecision(type="unknown", raw=text)
    blob = m.group(0)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return SolverDecision(type="unknown", raw=text)

    t = data.get("type", "unknown")
    dec = SolverDecision(type=t, raw=text)

    if t == "slider":
        gap_x = _get_int(data, "gap_x", "target_x")
        gap_y = _get_int(data, "gap_y", "target_y")
        piece_x = _get_int(data, "piece_x")
        piece_y = _get_int(data, "piece_y")
        if gap_x is not None:
            dec.target_x = gap_x
            dec.target_y = gap_y
        if piece_x is not None:
            dec.piece_x = piece_x
            dec.piece_y = piece_y
    elif t == "click":
        pts = data.get("points") or []
        dec.points = [(int(round(p[0])), int(round(p[1]))) for p in pts if len(p) >= 2]
    elif t == "rotate":
        try:
            dec.angle = float(data.get("angle", 0))
        except (TypeError, ValueError):
            dec.angle = None
    return dec


def compute_slider_offset(dec: SolverDecision) -> Optional[Tuple[int, int]]:
    if dec.type != "slider":
        return None
    if dec.dx is not None:
        return dec.dx, 0
    if dec.target_x is None:
        return None
    if dec.piece_x is not None:
        return dec.target_x - dec.piece_x, (dec.target_y or 0) - (dec.piece_y or 0)
    return dec.target_x, dec.target_y or 0


# ---------------------------------------------------------------------------
# 轨迹解析
# ---------------------------------------------------------------------------
def parse_track(text: str, expected_distance: int = 0) -> List[Tuple[float, float, int]]:
    """解析模型返回的轨迹 JSON，返回 [(dx, dy, dt), ...] 列表。

    容错：自动清理 Markdown 包裹、支持 track 类型或纯数组两种格式。
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    m = _JSON_RE.search(text)
    if not m:
        return _fallback_track(expected_distance)
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return _fallback_track(expected_distance)

    # 兼容 {"type":"track","points":[...]} 和纯数组 [...] 两种格式
    pts = data if isinstance(data, list) else data.get("points", [])
    if not pts or not isinstance(pts, list):
        return _fallback_track(expected_distance)

    track: List[Tuple[float, float, int]] = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 3:
            continue
        track.append((float(p[0]), float(p[1]), int(p[2])))
    return track or _fallback_track(expected_distance)


def _fallback_track(distance: int) -> List[Tuple[float, float, int]]:
    """模型返回不可用时，生成一段简单的缓动轨迹。"""
    import random
    if distance <= 0:
        return [(0, 0, 500)]
    steps = random.randint(10, 16)
    pts: List[Tuple[float, float, int]] = [(0, 0, random.randint(80, 150))]
    total_ms = random.randint(450, 750)
    step_dur = total_ms // steps
    for i in range(1, steps + 1):
        t = i / steps
        eased = 1 - (1 - t) ** 3  # ease-out
        x = distance * eased
        y = random.uniform(-0.8, 0.8)
        dt = int(step_dur * random.uniform(0.8, 1.2))
        pts.append((round(x, 1), round(y, 1), dt))
    return pts
