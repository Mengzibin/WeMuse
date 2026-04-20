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
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

    _READY = True
    _IMPORT_ERR = ""
except Exception as e:  # noqa: BLE001
    _READY = False
    _IMPORT_ERR = str(e)

WECHAT_BUNDLE = "com.tencent.xinWeChat"

# macOS 虚拟键码
_KEYCODE_V = 9
_KEYCODE_RETURN = 36


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


def _post_key(keycode: int, cmd: bool = False) -> None:
    """发一次 key-down + key-up。cmd=True 时附带 ⌘ 修饰符。"""
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    if cmd:
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    if cmd:
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, up)


def send_to_wechat(
    text_or_lines: "str | list[str]",
    press_enter: bool = True,
    inter_delay: float = 0.4,
) -> tuple[bool, str]:
    """把文字贴到 WeChat 输入框。

    Args:
        text_or_lines: 单条 str 或多条 list[str]。多条会作为独立消息依次发送。
        press_enter: True = 粘贴后回车发送；False = 只粘贴不发送（多条模式下仍按顺序粘贴）
        inter_delay: 多条之间的停顿（秒），避免微信判定为连发 spam

    Returns:
        (success, human_readable_status)
    """
    if not _READY:
        return False, f"macOS 框架不可用：{_IMPORT_ERR}"

    # 统一成 list，剥空行
    if isinstance(text_or_lines, str):
        lines = [text_or_lines]
    else:
        lines = list(text_or_lines)
    lines = [ln for ln in (x.strip() for x in lines) if ln]
    if not lines:
        return False, "内容为空"

    if not activate_wechat():
        return False, "未找到 WeChat 进程（未启动或未登录？）"

    # 等微信拿到焦点 + 输入框聚焦
    time.sleep(0.25)

    try:
        for i, line in enumerate(lines):
            pyperclip.copy(line)
            time.sleep(0.08)  # 等系统剪贴板生效
            _post_key(_KEYCODE_V, cmd=True)
            if press_enter:
                time.sleep(0.12)
                _post_key(_KEYCODE_RETURN)
            if i < len(lines) - 1:
                time.sleep(inter_delay)
    except Exception as e:  # noqa: BLE001
        return False, f"发送过程出错：{e}"

    if len(lines) == 1:
        return True, "已发送" if press_enter else "已粘贴到输入框（未回车）"
    action = "发送" if press_enter else "粘贴（未回车）"
    return True, f"已连续{action} {len(lines)} 条"
