"""macOS Accessibility 读取微信聊天内容。

核心思路（v5）：
  1. 定位到 WeChat 当前窗口（不是整个 app）
  2. 优先在最大的 AXScrollArea 子树里抓；否则整窗口 + 百分比过滤兜底
  3. 几何过滤：仅在 whole-window 模式启用；scroll-area 模式下子树本身即聊天区
  4. 噪音词 / 时间日期 / 表情贴纸 / 过短文字统一过滤
  5. 发言人靠文本前缀 `"我说:"` / `"XXX说:"` 解析（比 x 坐标精确 100%）
  6. `read_wechat_multi_pass(passes)` —— 向上翻页触发微信懒加载历史，合并去重
"""
from __future__ import annotations

import re
import time
from collections import Counter

try:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementSetAttributeValue,
        AXValueGetValue,
        kAXValueCGPointType,
        kAXValueCGSizeType,
    )
    from AppKit import NSWorkspace

    _AVAILABLE = True
    _IMPORT_ERR = ""
except Exception as e:  # noqa: BLE001
    _AVAILABLE = False
    _IMPORT_ERR = str(e)

WECHAT_BUNDLE = "com.tencent.xinWeChat"

# 几何比例（根据 macOS WeChat 典型布局调）
# 注意：窄高窗口（外接竖屏）下侧边栏比例更大，所以 SIDEBAR_FRAC 宁可偏大
SIDEBAR_FRAC = 0.32   # 左侧约 32% 是联系人列表
TOOLBAR_FRAC = 0.05   # 顶部 5% 是标题栏
INPUT_FRAC = 0.18     # 底部 18% 是输入框 + 表情/图片按钮区
CHAT_LEFT_FRAC = SIDEBAR_FRAC  # 聊天区域左边界
CHAT_RIGHT_FRAC = 1.0          # 聊天区域右边界

# 硬过滤：已知的 UI 噪音字符串（按钮、占位、导航项、容器标题）
_UI_NOISE: set[str] = {
    "折叠置顶聊天", "置顶聊天", "展开置顶聊天",
    "搜索", "Search", "搜索聊天",
    "通讯录", "发现", "我", "设置",
    "微信", "WeChat",
    "Field", "TextField",
    "发送", "Send",
    "新的朋友", "公众号", "订阅号", "收藏", "聊天",
    "星标朋友", "群聊",
    "表情", "图片", "文件", "位置", "视频", "语音",
    "消息",  # AXTable 的标题
}

# 正则：时间/日期分隔条（居中显示，和气泡分类冲突，直接丢）
_TIME_RE = re.compile(r"^\s*\d{1,2}[:：]\d{2}\s*$")
_DATE_WORDS = ("昨天", "今天", "前天", "明天", "星期", "周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 微信 Accessibility 把发言人写在文本里：
#   "我说:..." / "我:..." (后者多见于表情/图片等系统消息)
#   "葛诗霖说:..." / "葛诗霖:..."
# 这是比 x 坐标更可靠的发言人信号
_SPEAKER_RE = re.compile(r"^(.{1,10}?)(?:说)?\s*[:：]\s*")

# 系统型消息（表情/图片/撤回）——不带信息量，直接过滤，避免污染回复生成
_STICKER_RE = re.compile(r"发送了一(?:个表情|张图片|张动画表情|段视频|段语音|条语音)|撤回了一条消息")


def available() -> bool:
    return _AVAILABLE


def import_error() -> str:
    return _IMPORT_ERR


def trusted() -> bool:
    if not _AVAILABLE:
        return False
    try:
        return bool(AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        return False


def wechat_pid() -> int | None:
    if not _AVAILABLE:
        return None
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        try:
            bid = app.bundleIdentifier()
            name = app.localizedName() or ""
        except Exception:  # noqa: BLE001
            continue
        if bid == WECHAT_BUNDLE or name in ("WeChat", "微信"):
            return int(app.processIdentifier())
    return None


# ---------- AX helpers ----------

def _attr(elem, name: str):
    try:
        err, val = AXUIElementCopyAttributeValue(elem, name, None)
        if err != 0:
            return None
        return val
    except Exception:  # noqa: BLE001
        return None


_POINT_RE = re.compile(r"x\s*:\s*([-\d.]+)\s+y\s*:\s*([-\d.]+)")
_SIZE_RE = re.compile(r"w(?:idth)?\s*:\s*([-\d.]+)\s+h(?:eight)?\s*:\s*([-\d.]+)")


def _parse_point(v) -> tuple[float, float] | None:
    if v is None:
        return None
    try:
        ok, point = AXValueGetValue(v, kAXValueCGPointType, None)
        if ok:
            return float(point.x), float(point.y)
    except Exception:  # noqa: BLE001
        pass
    try:
        m = _POINT_RE.search(str(v))
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:  # noqa: BLE001
        pass
    return None


def _parse_size(v) -> tuple[float, float] | None:
    if v is None:
        return None
    try:
        ok, size = AXValueGetValue(v, kAXValueCGSizeType, None)
        if ok:
            return float(size.width), float(size.height)
    except Exception:  # noqa: BLE001
        pass
    try:
        m = _SIZE_RE.search(str(v))
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:  # noqa: BLE001
        pass
    return None


def _walk(elem, out: list[dict], depth: int = 0, max_depth: int = 30, cap: int = 3000) -> None:
    if len(out) >= cap or depth > max_depth:
        return

    role = _attr(elem, "AXRole") or ""
    text: str | None = None
    for attr_name in ("AXValue", "AXDescription", "AXTitle"):
        v = _attr(elem, attr_name)
        if isinstance(v, str) and v.strip():
            text = v.strip()
            break

    if text:
        pos = _parse_point(_attr(elem, "AXPosition"))
        size = _parse_size(_attr(elem, "AXSize"))
        out.append(
            {
                "text": text,
                "role": role,
                "x": pos[0] if pos else None,
                "y": pos[1] if pos else None,
                "w": size[0] if size else None,
                "h": size[1] if size else None,
            }
        )

    children = _attr(elem, "AXChildren")
    if children:
        for child in children:
            _walk(child, out, depth + 1, max_depth, cap)


# ---------- filter ----------

def _is_timestamp(text: str) -> bool:
    t = text.strip()
    if len(t) > 12:
        return False
    if _TIME_RE.match(t):
        return True
    if any(w in t for w in _DATE_WORDS):
        return True
    return False


def _parse_speaker(text: str) -> tuple[str | None, str]:
    """把 '我说:xxx' / '葛诗霖说:yyy' 拆成 (发言人名, 正文)。匹配不到返回 (None, 原文)。"""
    m = _SPEAKER_RE.match(text)
    if not m:
        return None, text
    name = m.group(1).strip()
    if not name or name == "":
        return None, text
    return name, text[m.end():].strip()


def _is_chat_message(
    item: dict, wx: float, wy: float, ww: float, wh: float
) -> bool:
    text = item["text"]
    x, y = item.get("x"), item.get("y")

    # 1. 几何：必须在聊天区域（不在侧边栏、不在顶栏、不在输入框）
    if x is None or y is None:
        return False
    if x < wx + ww * SIDEBAR_FRAC:
        return False
    if y < wy + wh * TOOLBAR_FRAC:
        return False
    if y > wy + wh * (1 - INPUT_FRAC):
        return False
    # 2. 噪音词
    if text in _UI_NOISE:
        return False
    # 3. 时间分隔条
    if _is_timestamp(text):
        return False
    # 4. 太短（< 2 字）的文字多半是按钮 / 标识符
    if len(text.strip()) < 2:
        return False
    return True


# ---------- public ----------

def _window_and_items(win) -> tuple[tuple[float, float, float, float] | None, list[dict]]:
    pos = _parse_point(_attr(win, "AXPosition"))
    size = _parse_size(_attr(win, "AXSize"))
    bounds = None
    if pos and size:
        bounds = (pos[0], pos[1], size[0], size[1])
    items: list[dict] = []
    _walk(win, items)
    return bounds, items


def _find_scroll_areas(elem, out: list, depth: int = 0, max_depth: int = 20) -> None:
    """深度遍历找所有 AXScrollArea 及其几何。"""
    if depth > max_depth:
        return
    role = _attr(elem, "AXRole") or ""
    if role == "AXScrollArea":
        pos = _parse_point(_attr(elem, "AXPosition"))
        size = _parse_size(_attr(elem, "AXSize"))
        if pos and size:
            out.append(
                {
                    "elem": elem,
                    "x": pos[0],
                    "y": pos[1],
                    "w": size[0],
                    "h": size[1],
                }
            )
    children = _attr(elem, "AXChildren") or []
    for child in children:
        _find_scroll_areas(child, out, depth + 1, max_depth)


def _pick_chat_scroll_area(
    scrolls: list[dict], window: tuple[float, float, float, float]
) -> dict | None:
    """从窗口内所有 AXScrollArea 里挑出"最可能是聊天区"的那个。

    规则：
      1. 必须在窗口右半边（排除左侧联系人列表）
      2. 再按面积（w*h）取最大
    """
    if not scrolls:
        return None
    wx, _, ww, _ = window
    # 过滤条件：中心点落在窗口右 60% 区域
    mid_x = wx + ww * 0.4
    right_side = [s for s in scrolls if s["x"] + s["w"] / 2 >= mid_x]
    candidates = right_side or scrolls
    candidates.sort(key=lambda s: s["w"] * s["h"], reverse=True)
    return candidates[0]


def read_wechat_as_text(debug: bool = True) -> tuple[str, int] | None:
    """读取当前 WeChat 聊天窗口，返回（我/对方 标注文本，消息条数）。

    策略（v3）：
      1. 先找聊天区的 AXScrollArea 子树，只在它里面抓文本（最稳）
      2. 找不到 scroll area 就退回到全窗口遍历 + 百分比过滤
    失败返回 None；debug 始终在 stdout 打印诊断，方便定位。
    """
    # 永远打印开头行：便于定位到底走到哪一步
    print(f"[AX] trusted={trusted()}  wechat_pid={wechat_pid()}", flush=True)

    if not trusted():
        print("[AX] ✗ 未授权辅助功能。去「系统设置 → 隐私与安全性 → 辅助功能」给当前启动者打勾。", flush=True)
        return None

    pid = wechat_pid()
    if pid is None:
        print("[AX] ✗ 没找到微信进程（未启动 / 未登录？）", flush=True)
        return None
    app_elem = AXUIElementCreateApplication(pid)
    if app_elem is None:
        print("[AX] ✗ AXUIElementCreateApplication 返回 None", flush=True)
        return None

    win = _attr(app_elem, "AXFocusedWindow")
    source = "AXFocusedWindow"
    if win is None:
        wins = _attr(app_elem, "AXWindows")
        print(f"[AX] AXFocusedWindow=None, AXWindows count={len(wins) if wins else 0}", flush=True)
        if wins:
            win = wins[0]
            source = "AXWindows[0]"
    if win is None:
        print("[AX] ✗ 连一个窗口都拿不到（微信可能整个最小化了 / 只显示在 Dock）", flush=True)
        return None
    print(f"[AX] window source = {source}", flush=True)

    win_pos = _parse_point(_attr(win, "AXPosition"))
    win_size = _parse_size(_attr(win, "AXSize"))
    if not win_pos or not win_size:
        print(f"[AX] ✗ 拿不到窗口几何  pos={win_pos} size={win_size}", flush=True)
        return None
    window_bounds = (win_pos[0], win_pos[1], win_size[0], win_size[1])
    wx, wy, ww, wh = window_bounds

    # --- 尝试走 scroll-area 子树 ---
    scrolls: list[dict] = []
    _find_scroll_areas(win, scrolls)
    chat_scroll = _pick_chat_scroll_area(scrolls, window_bounds)

    mode_used = ""
    if chat_scroll is not None:
        mode_used = "scroll-area"
        root_elem = chat_scroll["elem"]
        area_x, area_y = chat_scroll["x"], chat_scroll["y"]
        area_w, area_h = chat_scroll["w"], chat_scroll["h"]
    else:
        mode_used = "whole-window"
        root_elem = win
        # fallback 按原来的比例切
        area_x = wx + ww * SIDEBAR_FRAC
        area_y = wy + wh * TOOLBAR_FRAC
        area_w = ww * (1 - SIDEBAR_FRAC)
        area_h = wh * (1 - TOOLBAR_FRAC - INPUT_FRAC)

    raw_items: list[dict] = []
    _walk(root_elem, raw_items)

    # scroll-area 模式下：子树本身就是聊天区，y 坐标可能远超窗口（滚动内容虚拟高度），不做 y 过滤
    # whole-window 模式下：做完整的 x/y 几何过滤兜底
    use_strict_geometry = mode_used == "whole-window"

    def _accept(it: dict) -> bool:
        text = it["text"]
        if text in _UI_NOISE:
            return False
        # 注意：**不再丢掉时间戳**——时间是重要的上下文，改在输出时格式化为分隔符
        if _STICKER_RE.search(text):
            return False
        if len(text.strip()) < 2:
            return False
        if use_strict_geometry:
            x, y = it.get("x"), it.get("y")
            if x is None or y is None:
                return False
            if not (area_x - 2 <= x <= area_x + area_w + 2):
                return False
            if not (area_y - 2 <= y <= area_y + area_h + 2):
                return False
        return True

    filtered = [it for it in raw_items if _accept(it)]

    role_counts = Counter((it.get("role") or "?") for it in raw_items)
    print(
        f"[AX] roles in subtree: {dict(role_counts.most_common(8))}  "
        f"geometry_filter={'on' if use_strict_geometry else 'off'}",
        flush=True,
    )

    print(
        f"[AX] mode={mode_used}  window=x:{wx:.0f} y:{wy:.0f} w:{ww:.0f} h:{wh:.0f}",
        flush=True,
    )
    print(
        f"[AX] chat_area=x:{area_x:.0f} y:{area_y:.0f} w:{area_w:.0f} h:{area_h:.0f}",
        flush=True,
    )
    print(
        f"[AX] scrolls_found={len(scrolls)}  raw={len(raw_items)}  filtered={len(filtered)}",
        flush=True,
    )
    # 打印前 20 条原始文本（包括过滤掉的），帮助你看气泡到底在哪个 role 里
    for it in raw_items[:20]:
        print(
            f"  raw  role={it['role']:<18} x={it['x']} y={it['y']} text={it['text'][:40]!r}",
            flush=True,
        )
    for it in filtered[:20]:
        print(
            f"  kept role={it['role']:<18} x={it['x']} y={it['y']} text={it['text'][:40]!r}",
            flush=True,
        )

    if not filtered:
        print("[AX] ✗ 0 条通过过滤。上面 raw 样本里如果能看到你微信里的消息文本，说明是几何/噪音词过滤把它们误杀——告诉我 raw 里的 text + x/y，我再调阈值。", flush=True)
        return None

    filtered.sort(key=lambda it: (it.get("y") or 0, it.get("x") or 0))

    # 发言人分类：优先用 "我说:" / "XXX说:" 文本前缀（微信 AX 亲手写的，最准）；
    # 前缀缺失时回退到 x 坐标分类
    split = area_x + area_w / 2

    lines: list[str] = []
    for it in filtered:
        text = it["text"]
        # 1. 时间戳 → 渲染成居中分隔条
        if _is_timestamp(text):
            lines.append(f"  ──【{text.strip()}】──")
            continue
        # 2. 带发言人前缀的正常消息
        name, body = _parse_speaker(text)
        if name == "我":
            lines.append(f"我：{body}")
        elif name is not None:
            # 对方：用「对方」统一标签，不直接暴露真实姓名
            lines.append(f"对方：{body}")
        else:
            # 无前缀兜底：靠 x 坐标
            x_center = (it.get("x") or 0) + (it.get("w") or 0) / 2
            speaker = "我" if x_center >= split else "对方"
            lines.append(f"{speaker}：{text}")
    return "\n".join(lines), len(filtered)


# --- 精准滚动：只动聊天区的 AXScrollBar，不发键盘事件（避免把侧边栏一起滚了）---

def _find_wechat_chat_scroll() -> dict | None:
    """定位聊天区的 AXScrollArea 并返回它的元素信息。无法定位返回 None。"""
    pid = wechat_pid()
    if pid is None:
        return None
    app_elem = AXUIElementCreateApplication(pid)
    if app_elem is None:
        return None
    win = _attr(app_elem, "AXFocusedWindow")
    if win is None:
        wins = _attr(app_elem, "AXWindows")
        if wins:
            win = wins[0]
    if win is None:
        return None
    win_pos = _parse_point(_attr(win, "AXPosition"))
    win_size = _parse_size(_attr(win, "AXSize"))
    if not win_pos or not win_size:
        return None
    bounds = (win_pos[0], win_pos[1], win_size[0], win_size[1])
    scrolls: list[dict] = []
    _find_scroll_areas(win, scrolls)
    return _pick_chat_scroll_area(scrolls, bounds)


def _get_chat_scroll_value(chat_scroll: dict) -> float | None:
    """读当前聊天区 vertical scroll bar 的 AXValue（0.0 最顶，1.0 最底）。"""
    vscroll = _attr(chat_scroll["elem"], "AXVerticalScrollBar")
    if vscroll is None:
        return None
    v = _attr(vscroll, "AXValue")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _set_chat_scroll_value(chat_scroll: dict, value: float) -> bool:
    """写聊天区 vertical scroll bar 的 AXValue。成功返回 True。"""
    vscroll = _attr(chat_scroll["elem"], "AXVerticalScrollBar")
    if vscroll is None:
        return False
    value = max(0.0, min(1.0, float(value)))
    try:
        err = AXUIElementSetAttributeValue(vscroll, "AXValue", value)
        # 某些绑定返回 int (0=成功)，某些直接抛异常
        return err in (0, None)
    except Exception as e:  # noqa: BLE001
        print(f"[AX] 设置 scrollbar AXValue 失败: {e}", flush=True)
        return False


def read_wechat_multi_pass(passes: int = 2, step: float = 0.3) -> tuple[str, int] | None:
    """读取微信并自动向上滚动 N 次收集更多历史。

    **精准滚动，只动聊天区**：通过 AXUIElementSetAttributeValue 直接写聊天区
    `AXVerticalScrollBar` 的 `AXValue`（0 最顶 / 1 最底），**完全不碰键盘、不激活微信、
    不影响左侧联系人列表**。读取结束后精确恢复原始滚动位置。

    失败（AX 不支持写 AXValue 等）会退回只读当前视图。
    """
    first = read_wechat_as_text()
    if first is None or passes <= 0:
        return first

    chat_scroll = _find_wechat_chat_scroll()
    if chat_scroll is None:
        print("[AX] 没定位到聊天滚动区，跳过多轮", flush=True)
        return first

    original = _get_chat_scroll_value(chat_scroll)
    if original is None:
        print("[AX] 拿不到 scrollbar 当前值（可能是 WeChat 不暴露 AXValue），跳过多轮", flush=True)
        return first
    print(f"[AX] 聊天区 scrollbar 原位置 = {original:.3f}", flush=True)

    outputs: list[list[str]] = [first[0].splitlines()]
    try:
        current = original
        for i in range(1, passes + 1):
            # 向上滚动 step 个单位（一次大约看到几屏历史；微信会懒加载更老的）
            target = max(0.0, current - step)
            if not _set_chat_scroll_value(chat_scroll, target):
                print(f"[AX] pass {i}: AX 写 scrollbar 失败，停止", flush=True)
                break
            current = target
            # 给微信时间触发懒加载 + 渲染
            time.sleep(0.7)
            r = read_wechat_as_text()
            if r is None:
                print(f"[AX] pass {i}: 读取失败", flush=True)
                continue
            lines = r[0].splitlines()
            print(
                f"[AX] pass {i}: scroll→{target:.3f}，读到 {len(lines)} 行",
                flush=True,
            )
            outputs.append(lines)
            if current <= 0.001:
                print("[AX] 已触顶，停止", flush=True)
                break
    finally:
        # 恢复用户原本的滚动位置，视角零打扰
        _set_chat_scroll_value(chat_scroll, original)

    # 合并：最后一次 pass（对应最老的消息）放最上面；按文本去重
    seen: set[str] = set()
    merged: list[str] = []
    for pass_lines in reversed(outputs):
        for line in pass_lines:
            if line and line not in seen:
                seen.add(line)
                merged.append(line)
    total = len(merged)
    print(f"[AX] 多轮合并后总计 {total} 条", flush=True)
    return "\n".join(merged), total
