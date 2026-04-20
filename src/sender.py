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
    send_key: str = "enter",
) -> tuple[bool, str]:
    """把文字贴到 WeChat 输入框。

    Args:
        text_or_lines: 单条 str 或多条 list[str]。多条作为独立消息依次发送。
        press_enter: True = 粘贴后触发发送；False = 只粘贴不发送
        inter_delay: 多条之间的停顿（秒）
        send_key: 发送快捷键——"enter"（默认，对应 WeChat 默认设置）或
                  "cmd_enter"（对应 WeChat 设置里勾了"⌘+Enter 发送消息"的场景）
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
        f"[sender] send_to_wechat: {len(lines)} 条 · press_enter={press_enter} · send_key={send_key} · inter_delay={inter_delay}s",
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

    # 根据配置决定发送键
    use_cmd_for_enter = send_key == "cmd_enter"

    try:
        for i, line in enumerate(lines):
            pyperclip.copy(line)
            time.sleep(0.1)  # 等系统剪贴板生效
            _post_key(_KEYCODE_V, cmd=True)
            print(
                f"[sender]  [{i+1}/{len(lines)}] 粘贴: {line[:30]}{'…' if len(line) > 30 else ''}",
                flush=True,
            )
            if press_enter:
                # 粘贴到回车之间留 0.45s，确保 WeChat 已经把内容写入输入框
                time.sleep(0.45)
                _post_key(_KEYCODE_RETURN, cmd=use_cmd_for_enter)
                print(
                    f"[sender]  [{i+1}/{len(lines)}] "
                    f"{'⌘+' if use_cmd_for_enter else ''}Enter 已发",
                    flush=True,
                )
            if i < len(lines) - 1:
                time.sleep(inter_delay)
    except Exception as e:  # noqa: BLE001
        return False, f"发送过程出错：{e}"

    if len(lines) == 1:
        return True, "已发送" if press_enter else "已粘贴到输入框（未回车）"
    action = "发送" if press_enter else "粘贴（未回车）"
    return True, f"已连续{action} {len(lines)} 条"
