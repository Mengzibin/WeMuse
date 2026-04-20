"""截屏：调用 macOS 原生 `screencapture -i`，让用户框选要识别的区域。"""
from __future__ import annotations

import os
import subprocess
import tempfile


def capture_region() -> tuple[str | None, str]:
    """弹出 macOS 系统框选截图工具。

    Returns:
        (path, reason)
          - 成功：(png_path, "ok")
          - 失败/取消：(None, 可读原因)
    """
    tmp = tempfile.NamedTemporaryFile(prefix="wx_assistant_", suffix=".png", delete=False)
    tmp.close()
    path = tmp.name

    result = subprocess.run(
        ["screencapture", "-i", "-x", "-o", path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        if os.path.exists(path):
            os.unlink(path)
        stderr = result.stderr.strip()
        return None, f"screencapture 命令失败（多半是没授权屏幕录制）; rc={result.returncode}; {stderr}"

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        if os.path.exists(path):
            os.unlink(path)
        return None, "没有产生有效截图：按了 Esc 取消 / 只点了一下没拖出矩形 / 屏幕录制权限未开"

    return path, "ok"
