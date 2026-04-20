"""把生成的回复自动贴到微信输入框（可选回车发送）。

思路：clipboard → 激活 WeChat → 模拟 ⌘V → 可选回车。
（相比直接 type 文字，clipboard+⌘V 对中文 / emoji / 输入法状态都更鲁棒。）
"""
from __future__ import annotations

import time

import pyperclip
from pynput.keyboard import Controller, Key

try:
    from AppKit import NSWorkspace

    _NS = True
except Exception:  # noqa: BLE001
    _NS = False

WECHAT_BUNDLE = "com.tencent.xinWeChat"


def activate_wechat() -> bool:
    """把 WeChat.app 切到前台。成功返回 True。"""
    if not _NS:
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


def send_to_wechat(text: str, press_enter: bool = True) -> tuple[bool, str]:
    """把 text 贴到 WeChat 输入框。press_enter=True 时直接发送。

    Returns: (success, message)
    """
    if not text.strip():
        return False, "回复内容为空"
    if not activate_wechat():
        return False, "未找到 WeChat 进程（未启动或未登录？）"

    pyperclip.copy(text)
    # WeChat 拿到焦点 + 输入框聚焦需要时间
    time.sleep(0.25)

    kb = Controller()
    try:
        with kb.pressed(Key.cmd):
            kb.press("v")
            kb.release("v")
    except Exception as e:  # noqa: BLE001
        return False, f"粘贴失败：{e}"

    if press_enter:
        time.sleep(0.15)
        try:
            kb.press(Key.enter)
            kb.release(Key.enter)
        except Exception as e:  # noqa: BLE001
            return False, f"回车失败：{e}"
        return True, "已发送"
    return True, "已粘贴到输入框（未回车）"
