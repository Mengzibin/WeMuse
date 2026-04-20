"""调用本机 Claude Code CLI（复用其登录态）生成回复。"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from typing import Callable


def _find_claude_cli() -> str:
    """优先 PATH 中的 claude；否则回落到 VSCode 扩展内置二进制。"""
    path_claude = shutil.which("claude")
    if path_claude:
        return path_claude

    # VSCode 扩展内置 claude（版本号会变，取最新）
    pattern = os.path.expanduser(
        "~/.vscode/extensions/anthropic.claude-code-*-darwin-arm64/resources/native-binary/claude"
    )
    candidates = sorted(glob.glob(pattern))
    if candidates:
        return candidates[-1]

    raise RuntimeError(
        "未找到 claude CLI。请确认 Claude Code 已安装（VSCode 扩展或 `npm i -g @anthropic-ai/claude-code`）。"
    )


CLAUDE_BIN = _find_claude_cli()

# Anthropic API 在国内无法直连。优先沿用父进程 HTTPS_PROXY；否则兜底 Clash/Surge 常用端口。
# 想改代理端口，启动前 `export CLAUDE_PROXY=http://127.0.0.1:7897`，或编辑这里。
DEFAULT_PROXY = os.environ.get("CLAUDE_PROXY", "http://127.0.0.1:7890")


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    if not (env.get("HTTPS_PROXY") or env.get("https_proxy")):
        env["HTTPS_PROXY"] = DEFAULT_PROXY
        env["HTTP_PROXY"] = DEFAULT_PROXY
    return env


def generate_reply(prompt: str, timeout: int = 60) -> str:
    """阻塞调用 claude -p，返回纯文本回复。"""
    result = subprocess.run(
        [CLAUDE_BIN, "-p", "--output-format", "text", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_build_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI 返回非零退出码 {result.returncode}：{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def generate_reply_stream(
    prompt: str,
    on_chunk: Callable[[str], None],
    timeout: int = 90,
) -> str:
    """流式调用：每拿到一个文本 delta 就回调 on_chunk(delta)。返回最终完整文本。

    用 --output-format stream-json + --include-partial-messages，从 stream_event
    里的 content_block_delta 抽 text 字段。失败抛 RuntimeError。
    """
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        prompt,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # 行缓冲
        env=_build_env(),
    )
    chunks: list[str] = []
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "stream_event":
                ev = obj.get("event") or {}
                if ev.get("type") == "content_block_delta":
                    delta = ev.get("delta") or {}
                    text = delta.get("text", "")
                    if text:
                        chunks.append(text)
                        try:
                            on_chunk(text)
                        except Exception:  # noqa: BLE001
                            pass
            elif obj.get("type") == "result":
                break
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("claude 调用超时") from None
    except Exception:
        proc.kill()
        raise

    if proc.returncode not in (None, 0):
        err = (proc.stderr.read() if proc.stderr else "") or ""
        raise RuntimeError(
            f"claude CLI 返回 {proc.returncode}：{err.strip() or '（无 stderr）'}"
        )

    return "".join(chunks).strip()
