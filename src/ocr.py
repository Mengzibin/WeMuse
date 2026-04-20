"""OCR：Apple Vision 识别 + 通过气泡颜色/位置判断发言人。"""
from __future__ import annotations

from PIL import Image
from ocrmac import ocrmac

# 微信气泡颜色（macOS 客户端）：
#   绿色（我）： 亮色 ≈ #95EC69 (149,236,105)；暗色 ≈ (75,135,70)
#   对方（白/灰）：亮色 ≈ 白；暗色 ≈ 深灰
# 判定核心：在气泡背景上，G 通道显著高于 R、B 则视作绿色。


def _classify_speaker(img: Image.Image, bbox: tuple[float, float, float, float]) -> str:
    """bbox 是 Vision 的归一化坐标 (x, y, w, h)，原点在左下。返回 'me' 或 'them'。"""
    W, H = img.size
    x, y, w, h = bbox
    px = int(x * W)
    py = int((1 - y - h) * H)  # 转成 PIL 的左上原点
    pw = max(int(w * W), 1)
    ph = max(int(h * H), 1)

    # 在文本框外+内各采几个点拼成样本集，尽量避开文字笔画（深色）
    pad = 6
    sample_positions = [
        (px - pad, py + ph // 2),             # 左侧外
        (px + pw + pad, py + ph // 2),        # 右侧外
        (px + pw // 2, py - pad),             # 上方外
        (px + pw // 2, py + ph + pad),        # 下方外
        (px + 3, py + 3),                     # 左上角内
        (px + pw - 3, py + ph - 3),           # 右下角内
    ]

    greens = 0
    non_greens = 0
    for sx, sy in sample_positions:
        sx = max(0, min(W - 1, sx))
        sy = max(0, min(H - 1, sy))
        r, g, b = img.getpixel((sx, sy))[:3]
        # 跳过近黑色（文字笔画或阴影）
        if r + g + b < 80:
            continue
        # 绿色判定：g 显著高于 r 和 b
        if g > r + 12 and g > b + 12:
            greens += 1
        else:
            non_greens += 1

    if greens > non_greens:
        return "me"
    if non_greens > greens:
        return "them"
    # 平票兜底：用横向位置（微信里右侧的是自己）
    return "me" if px + pw // 2 > W * 0.55 else "them"


def ocr_image(image_path: str) -> str:
    """识别微信聊天截图，按时间顺序返回带发言人标签的文本：
        对方：……
        我：……
        对方：……
    """
    annotations = ocrmac.OCR(
        image_path,
        language_preference=["zh-Hans", "zh-Hant", "en-US"],
        recognition_level="accurate",
    ).recognize()

    img = Image.open(image_path).convert("RGB")

    items: list[tuple[str, str, tuple[float, float, float, float]]] = []
    for text, conf, bbox in annotations:
        if not text.strip() or conf < 0.3:
            continue
        speaker = _classify_speaker(img, bbox)
        items.append((speaker, text, bbox))

    # Vision 的 y 原点在下方，y 越大越靠上；从上到下 = y 降序
    items.sort(key=lambda it: (-it[2][1], it[2][0]))

    # 合并相邻的同发言人片段（同一气泡内可能切成多行）
    merged: list[tuple[str, list[str]]] = []
    for speaker, text, _ in items:
        if merged and merged[-1][0] == speaker:
            merged[-1][1].append(text)
        else:
            merged.append((speaker, [text]))

    lines = []
    for speaker, chunks in merged:
        label = "我" if speaker == "me" else "对方"
        lines.append(f"{label}：{' '.join(chunks)}")
    return "\n".join(lines)
