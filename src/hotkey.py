"""macOS 全局热键：用 Cocoa NSEvent 主线程监听器实现。

之前用 pynput.keyboard.GlobalHotKeys，它的监听器在子线程调 TSM API，
macOS 26+ 的主线程断言会直接 SIGTRAP 杀进程。
改用 NSEvent.addGlobalMonitorForEventsMatchingMask 让回调在主线程触发，避开问题。

注意：NSEvent 全局监听仍需要"辅助功能"权限（和 pynput 一样）。
"""
from __future__ import annotations

try:
    from Cocoa import (
        NSEvent,
        NSEventMaskKeyDown,
        NSEventModifierFlagCommand,
        NSEventModifierFlagControl,
        NSEventModifierFlagOption,
        NSEventModifierFlagShift,
    )

    _AVAIL = True
    _ERR = ""
except Exception as e:  # noqa: BLE001
    _AVAIL = False
    _ERR = str(e)


def available() -> bool:
    return _AVAIL


def import_error() -> str:
    return _ERR


def register(
    key_char: str,
    callback,
    cmd: bool = True,
    shift: bool = True,
    ctrl: bool = False,
    opt: bool = False,
):
    """注册一个全局热键。

    key_char: 单个字符，如 'a' / 'g' / 'r'
    callback: 无参回调。会在主线程被调用。

    返回 monitor 对象 —— 调用方必须保存住这个引用，否则会被 GC 掉热键就没了。
    不可用时返回 None。
    """
    if not _AVAIL:
        return None

    req = 0
    if cmd:
        req |= NSEventModifierFlagCommand
    if shift:
        req |= NSEventModifierFlagShift
    if ctrl:
        req |= NSEventModifierFlagControl
    if opt:
        req |= NSEventModifierFlagOption

    mask_of_interest = (
        NSEventModifierFlagCommand
        | NSEventModifierFlagShift
        | NSEventModifierFlagControl
        | NSEventModifierFlagOption
    )

    key_lower = key_char.lower()

    def _handler(event):
        try:
            mods = int(event.modifierFlags()) & mask_of_interest
            if mods != req:
                return
            chars = event.charactersIgnoringModifiers() or ""
            if chars.lower() == key_lower:
                callback()
        except Exception as e:  # noqa: BLE001
            # handler 内异常不能抛出到 AppKit，否则可能搞坏 runloop
            print(f"[hotkey] handler 异常：{e}")

    monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
        NSEventMaskKeyDown, _handler
    )
    return monitor
