"""入口：启动 UI + 全局热键（Cocoa NSEvent，主线程监听）。

热键：
  ⌘⇧R  显示/隐藏窗口
  ⌘⇧G  截图 OCR 一把梭
  ⌘⇧A  Accessibility 一把梭（读 → 生成 → 如勾选自动发送）
"""
from __future__ import annotations

from . import hotkey, menubar
from .ui import AssistantWindow


def _toggle_window(app: AssistantWindow) -> None:
    if app.root.state() == "withdrawn":
        app.show()
    else:
        app.hide()


def _capture_and_generate(app: AssistantWindow) -> None:
    app.show()
    original = app._do_capture

    def wrapped() -> None:
        original()
        if app.chat_text.get("1.0", "end").strip():
            app.on_generate()

    app._do_capture = wrapped  # type: ignore[assignment]
    app.on_capture()
    app._do_capture = original  # type: ignore[assignment]


def _ax_and_generate(app: AssistantWindow) -> None:
    app.show()
    app.on_read_ax()
    if app.chat_text.get("1.0", "end").strip():
        app.on_generate()


def _quit(app: AssistantWindow) -> None:
    try:
        app.root.destroy()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    app = AssistantWindow()

    # 关闭窗口（红叉）= 隐藏到菜单栏，保留后台进程；真正退出走菜单栏的「退出」
    app.root.protocol("WM_DELETE_WINDOW", app.hide)

    # 在主线程注册热键；monitors 必须被长期引用住（保存到 app），否则会被 GC 干掉
    if hotkey.available():
        app._hotkey_monitors = [  # type: ignore[attr-defined]
            hotkey.register("r", lambda: app.root.after(0, _toggle_window, app)),
            hotkey.register("g", lambda: app.root.after(0, _capture_and_generate, app)),
            hotkey.register("a", lambda: app.root.after(0, _ax_and_generate, app)),
        ]
    else:
        print(f"⚠ Cocoa 不可用，全局热键禁用：{hotkey.import_error()}")

    # 菜单栏常驻图标
    if menubar.available():
        app._menubar_ref = menubar.setup(  # type: ignore[attr-defined]
            "💬",
            [
                ("📥 显示面板 (⌘⇧R)", lambda: app.root.after(0, app.show)),
                ("✨ Accessibility 读 + 生成 (⌘⇧A)", lambda: app.root.after(0, _ax_and_generate, app)),
                ("📸 截图 OCR 生成 (⌘⇧G)", lambda: app.root.after(0, _capture_and_generate, app)),
                (None, None),
                ("👋 退出", lambda: app.root.after(0, _quit, app)),
            ],
        )
    else:
        print(f"⚠ 菜单栏不可用：{menubar.import_error()}")

    print(
        "微信聊天助手已启动：\n"
        "  💬 菜单栏右上角常驻 · 红叉关窗不会退出\n"
        "  ⌘⇧R 呼出/隐藏窗口  ⌘⇧G 截图 OCR  ⌘⇧A Accessibility 读取\n"
        "（首次需授权：辅助功能 / 屏幕录制 / 自动化）"
    )
    app.run()


if __name__ == "__main__":
    main()
