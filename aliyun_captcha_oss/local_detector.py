"""本地像素级缺口检测器 —— 不依赖任何外部 API。

用 Pillow + numpy 对滑块背景图做边缘检测，找"拼图缺口"的大致 x 坐标。
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def detect_gap_x(image_path: str, *,
                 min_x: int = 60,
                 max_x_ratio: float = 0.95,
                 edge_threshold: float = 1.0) -> dict:
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]

    gray = np.mean(arr, axis=2).astype(np.float32)
    diff_x = np.abs(np.diff(gray, axis=1))
    col_energy = np.mean(diff_x, axis=0)

    x_start = min_x
    x_end = int(w * max_x_ratio)
    search_zone = col_energy[x_start:x_end]

    if len(search_zone) == 0:
        return {"gap_x": 0, "gap_y": h // 2, "confidence": 0.0,
                "width": w, "height": h}

    median_energy = np.median(search_zone)
    threshold = median_energy * (2.0 + edge_threshold)
    above = search_zone > threshold

    if not above.any():
        gap_x = x_start + int(np.argmax(search_zone))
        confidence = 0.3
    else:
        best_start, best_len = 0, 0
        cur_start, cur_len = 0, 0
        for i, v in enumerate(above):
            if v:
                if cur_len == 0:
                    cur_start = i
                cur_len += 1
                if cur_len > best_len:
                    best_len = cur_len
                    best_start = cur_start
            else:
                cur_len = 0
        gap_x = x_start + best_start + best_len // 2
        confidence = min(1.0, best_len / 40.0)

    # 估算 gap_y
    window = diff_x[:, max(0, gap_x - 20):min(w, gap_x + 20)]
    if window.size > 0:
        row_energy = np.mean(window, axis=1)
        gap_y = int(np.argmax(row_energy))
    else:
        gap_y = h // 2

    return {
        "gap_x": int(gap_x), "gap_y": int(gap_y),
        "confidence": round(float(confidence), 3),
        "width": int(w), "height": int(h),
    }
