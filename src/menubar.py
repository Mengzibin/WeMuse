"""macOS 菜单栏常驻图标 + 快捷菜单。

NSStatusItem 会添加到 Tkinter 启动的同一个 NSApplication 上，共享主线程 run loop，
菜单项 action 在主线程触发，和 Tk 事件循环互不干扰。
"""
from __future__ import annotations

try:
    import objc
    from AppKit import (
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSObject

    _AVAIL = True
    _ERR = ""
except Exception as e:  # noqa: BLE001
    _AVAIL = False
    _ERR = str(e)


def available() -> bool:
    return _AVAIL


def import_error() -> str:
    return _ERR


if _AVAIL:

    class _Target(NSObject):
        """接收菜单项 action 的 NSObject。"""

        def initWithCallbacks_(self, callbacks):  # noqa: N802
            self = objc.super(_Target, self).init()
            if self is not None:
                self._callbacks = list(callbacks)
            return self

        def invoke_(self, sender):  # noqa: N802
            tag = int(sender.tag())
            if 0 <= tag < len(self._callbacks):
                try:
                    self._callbacks[tag]()
                except Exception as e:  # noqa: BLE001
                    print(f"[menubar] callback {tag} 异常：{e}")


def setup(title: str, items: list[tuple[str | None, object]]):
    """创建菜单栏图标 + 菜单。

    title: 菜单栏上显示的字符串（可以是 emoji，比如 "💬"）
    items: [(label, callback_or_None), ...]；label=None 表示分隔线

    返回 (status_item, target) —— 调用方必须把它**保存到实例上**，
    否则会被 Python 的 GC 干掉，图标随即消失。
    """
    if not _AVAIL:
        return None

    status_bar = NSStatusBar.systemStatusBar()
    status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    status_item.button().setTitle_(title)

    callbacks = [cb for (_lbl, cb) in items if cb is not None]
    target = _Target.alloc().initWithCallbacks_(callbacks)

    menu = NSMenu.alloc().init()
    fn_idx = 0
    for label, cb in items:
        if label is None:
            menu.addItem_(NSMenuItem.separatorItem())
            continue
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            label, "invoke:", ""
        )
        if cb is not None:
            item.setTarget_(target)
            item.setTag_(fn_idx)
            fn_idx += 1
        else:
            item.setEnabled_(False)
        menu.addItem_(item)

    status_item.setMenu_(menu)
    return (status_item, target)
