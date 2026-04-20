"""OCR：走 Apple Vision（ocrmac），本地免费、中文精度高。

v2（bubble-aware）：
  1. 每条 OCR 文本按 y 从上到下排列
  2. **气泡边界检测**：纵向间距 > 文字高度 × 0.4 就判定为新气泡（关键改进）
  3. 时间戳（"02:08" / "昨天" 等）单独抽出来格式化为 ──【…】── 分隔条
  4. 发言人：气泡背景色（绿色 → 我，白/灰 → 对方）
  5. 输出和 Accessibility 读取保持一致的格式
"""
from __future__ import annotations

import re

from PIL import Image
from ocrmac import ocrmac

# 时间戳：整段文本刚好是 HH:MM / HH:MM:SS，或含日期关键词
_TIME_RE = re.compile(r"^\s*\d{1,2}[:：]\d{2}(?:[:：]\d{2})?\s*$")
_DATE_WORDS = (
    "昨天", "今天", "前天", "明天",
    "星期", "周一", "周二", "周三", "周四", "周五", "周六", "周日",
)


def _is_timestamp(text: str) -> bool:
    t = text.strip()
    if len(t) > 12:
        return False
    if _TIME_RE.match(t):
        return True
    return any(w in t for w in _DATE_WORDS)


def _classify_speaker(img: Image.Image, bbox: tuple[float, float, float, float]) -> str:
    """在 bbox 周围 6 个点采样气泡背景色。G 显著高于 R、B 判为绿（我），否则对方。"""
    W, H = img.size
    x, y, w, h = bbox
    px = int(x * W)
    py = int((1 - y - h) * H)  # Vision y 原点在下 → PIL 的左上原点
    pw = max(int(w * W), 1)
    ph = max(int(h * H), 1)

    pad = 6
    sample_positions = [
        (px - pad, py + ph // 2),
        (px + pw + pad, py + ph // 2),
        (px + pw // 2, py - pad),
        (px + pw // 2, py + ph + pad),
        (px + 3, py + 3),
        (px + pw - 3, py + ph - 3),
    ]

    greens = 0
    non_greens = 0
    for sx, sy in sample_positions:
        sx = max(0, min(W - 1, sx))
        sy = max(0, min(H - 1, sy))
        r, g, b = img.getpixel((sx, sy))[:3]
        if r + g + b < 80:  # 跳过近黑（文字笔画）
            continue
        if g > r + 12 and g > b + 12:
            greens += 1
        else:
            non_greens += 1

    if greens > non_greens:
        return "me"
    if non_greens > greens:
        return "them"
    # 平票：右半边算我
    return "me" if px + pw // 2 > W * 0.55 else "them"


def ocr_image(image_path: str) -> str:
    """识别微信聊天截图，返回按气泡分行、带发言人标签和时间分隔条的文本。

    输出格式与 Accessibility 路径一致：
        ──【02:08】──
        对方：气不气
        对方：你个傻逼
        ...
        我：行吧
    """
    annotations = ocrmac.OCR(
        image_path,
        language_preference=["zh-Hans", "zh-Hant", "en-US"],
        recognition_level="accurate",
    ).recognize()

    img = Image.open(image_path).convert("RGB")

    # 1) 收集有效 OCR 文本（conf > 0.3，去空白）
    items: list[tuple[str, tuple[float, float, float, float]]] = []
    for text, conf, bbox in annotations:
        if not text.strip() or conf < 0.3:
            continue
        items.append((text.strip(), bbox))
    if not items:
        return ""

    # 2) 按屏幕从上到下排序（Vision y 越大越靠上，所以 -y 升序）
    items.sort(key=lambda it: (-it[1][1], it[1][0]))

    # 3) 逐条处理，按气泡分组
    output: list[str] = []
    cur_speaker: str | None = None
    cur_texts: list[str] = []
    prev_bbox: tuple[float, float, float, float] | None = None

    def flush() -> None:
        nonlocal cur_texts, cur_speaker, prev_bbox
        if cur_texts:
            label = "我" if cur_speaker == "me" else "对方"
            output.append(f"{label}：{' '.join(cur_texts)}")
        cur_texts = []
        cur_speaker = None

    for text, bbox in items:
        # 时间戳独占一行，作为分隔条
        if _is_timestamp(text):
            flush()
            output.append(f"  ──【{text}】──")
            prev_bbox = None
            continue

        speaker = _classify_speaker(img, bbox)

        # 气泡边界判定
        same_bubble = False
        if cur_speaker is not None and speaker == cur_speaker and prev_bbox is not None:
            # Vision: y = bbox 底边；y + h = 顶边。上一条在上（y_prev 大），当前在下（y_cur 小）
            y_prev_bottom = prev_bbox[1]
            y_cur_top = bbox[1] + bbox[3]
            gap = y_prev_bottom - y_cur_top  # 两条文字之间的纵向空白
            avg_h = (prev_bbox[3] + bbox[3]) / 2
            # gap < 字高的 40% → 多半是同一气泡内的多行文字
            same_bubble = gap < avg_h * 0.4

        if same_bubble:
            cur_texts.append(text)
        else:
            flush()
            cur_speaker = speaker
            cur_texts = [text]
        prev_bbox = bbox

    flush()
    return "\n".join(output)
