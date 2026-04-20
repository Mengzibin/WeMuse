"""把生成的回复自动贴到微信输入框（可选回车发送），支持单条与多条连发。

键盘模拟走 **Quartz CGEvent**（不用 pynput），macOS 26+ 任意线程安全。
文本承载走剪贴板 + ⌘V，对中文 / emoji / 输入法状态都稳。
"""
from __future__ import annotations

import time

import pyperclip

try:
    from AppKit import NSWorkspace
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        CGEventSourceCreate,
        kCGEventFlagMaskCommand,
        kCGEventSourceStateHIDSystemState,
        kCGHIDEventTap,
    )

    _READY = True
    _IMPORT_ERR = ""
except Exception as e:  # noqa: BLE001
    _READY = False
    _IMPORT_ERR = str(e)

WECHAT_BUNDLE = "com.tencent.xinWeChat"

# macOS 虚拟键码 (kVK_*)
_KEYCODE_V = 9
_KEYCODE_RETURN = 36
_KEYCODE_CMD_LEFT = 55  # 0x37 — 用它显式模拟 Cmd 键的按下和抬起，避免 flag 残留

# 事件源：用 HIDSystemState 让合成的事件更接近真实硬件键盘
_event_source = None


def _get_event_source():
    global _event_source
    if _event_source is None and _READY:
        try:
            _event_source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)
        except Exception:  # noqa: BLE001
            _event_source = None
    return _event_source


def activate_wechat() -> bool:
    """把 WeChat.app 切到前台。成功返回 True。"""
    if not _READY:
        return False
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        try:
            bid = app.bundleIdentifier()
            name = app.localizedName() or ""
        except Exception:  # noqa: BLE001
            continue
        if bid == WECHAT_BUNDLE or name in ("WeChat", "微信"):
            try:
                # NSApplicationActivateIgnoringOtherApps = 2
                app.activateWithOptions_(2)
            except Exception:  # noqa: BLE001
                try:
                    app.activate()
                except Exception:  # noqa: BLE001
                    pass
            return True
    return False


def _post_event(keycode: int, down: bool, flags: int = 0) -> None:
    """发一个 key-down 或 key-up 事件。**永远显式 CGEventSetFlags**，避免继承残留。"""
    src = _get_event_source()
    ev = CGEventCreateKeyboardEvent(src, keycode, down)
    CGEventSetFlags(ev, flags)  # 就算 flags=0 也要调，不要依赖默认
    CGEventPost(kCGHIDEventTap, ev)


def _paste_cmd_v() -> None:
    """显式序列模拟 ⌘V: Cmd-down → V-down(带 Cmd flag) → V-up(带 Cmd flag) → Cmd-up。

    相比只在 V 事件上设 Cmd flag，这样做让 macOS 能清楚看到 Cmd 的按下-抬起，
    避免后续的 Enter 被某些 app 误判为"Cmd 还按着"或"带有别的修饰符"。
    """
    _post_event(_KEYCODE_CMD_LEFT, True, 0)
    time.sleep(0.02)
    _post_event(_KEYCODE_V, True, kCGEventFlagMaskCommand)
    _post_event(_KEYCODE_V, False, kCGEventFlagMaskCommand)
    time.sleep(0.02)
    _post_event(_KEYCODE_CMD_LEFT, False, 0)


def _press_return(cmd: bool = False) -> None:
    """按 Enter 或 ⌘+Enter，同样用显式序列（和 _paste_cmd_v 一致）。"""
    if cmd:
        _post_event(_KEYCODE_CMD_LEFT, True, 0)
        time.sleep(0.02)
        _post_event(_KEYCODE_RETURN, True, kCGEventFlagMaskCommand)
        _post_event(_KEYCODE_RETURN, False, kCGEventFlagMaskCommand)
        time.sleep(0.02)
        _post_event(_KEYCODE_CMD_LEFT, False, 0)
    else:
        # 纯 Enter：两个事件都显式 flags=0，防止有残留修饰符
        _post_event(_KEYCODE_RETURN, True, 0)
        _post_event(_KEYCODE_RETURN, False, 0)


def send_to_wechat(
    text_or_lines: "str | list[str]",
    press_enter: bool = True,
    inter_delay: float = 0.4,
) -> tuple[bool, str]:
    """把文字贴到 WeChat 输入框，可选地按 Enter 发送。

    Args:
        text_or_lines: 单条 str 或多条 list[str]。多条作为独立消息依次发送。
        press_enter: True = 粘贴后按 Enter 发送；False = 只粘贴不发送
        inter_delay: 多条之间的停顿（秒）

    发送键固定用 **Enter**（对应 WeChat 默认设置：Enter=发送 / Shift+Enter=换行）。
    如果你的 WeChat 勾选了 "⌘+Enter 发送"，请在微信设置里改回默认。
    """
    if not _READY:
        return False, f"macOS 框架不可用：{_IMPORT_ERR}"

    if isinstance(text_or_lines, str):
        lines = [text_or_lines]
    else:
        lines = list(text_or_lines)
    lines = [ln for ln in (x.strip() for x in lines) if ln]
    if not lines:
        return False, "内容为空"

    print(
        f"[sender] send_to_wechat: {len(lines)} 条 · press_enter={press_enter} · inter_delay={inter_delay}s",
        flush=True,
    )

    if not activate_wechat():
        return False, "未找到 WeChat 进程（未启动或未登录？）"

    # 验证激活是否真的生效（有时候激活会被其他 app 抢回去）
    try:
        fm = NSWorkspace.sharedWorkspace().frontmostApplication()
        fm_name = fm.localizedName() if fm else "(none)"
        fm_bid = fm.bundleIdentifier() if fm else ""
        print(f"[sender] 激活后前台 app = {fm_name} ({fm_bid})", flush=True)
        if fm_bid != WECHAT_BUNDLE and fm_name not in ("WeChat", "微信"):
            print("[sender] ⚠ WeChat 没有拿到前台焦点，发送可能会打到错误的 app", flush=True)
    except Exception:  # noqa: BLE001
        pass

    # 给 WeChat 更充足的时间拿焦点 + 聚焦到输入框
    time.sleep(0.5)

    try:
        for i, line in enumerate(lines):
            pyperclip.copy(line)
            time.sleep(0.1)  # 等系统剪贴板生效
            _paste_cmd_v()
            print(
                f"[sender]  [{i+1}/{len(lines)}] 粘贴: {line[:30]}{'…' if len(line) > 30 else ''}",
                flush=True,
            )
            if press_enter:
                # 粘贴到回车之间留 0.6s，确保 WeChat 已经把内容写入输入框 AXValue
                time.sleep(0.6)
                _press_return(cmd=False)  # 固定用 Enter，不再走 ⌘+Enter
                print(
                    f"[sender]  [{i+1}/{len(lines)}] Enter 已发", flush=True
                )
            if i < len(lines) - 1:
                time.sleep(inter_delay)
    except Exception as e:  # noqa: BLE001
        return False, f"发送过程出错：{e}"

    if len(lines) == 1:
        return True, "已发送" if press_enter else "已粘贴到输入框（未回车）"
    action = "发送" if press_enter else "粘贴（未回车）"
    return True, f"已连续{action} {len(lines)} 条"
