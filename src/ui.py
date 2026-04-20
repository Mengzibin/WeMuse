"""Tkinter 主窗口：展示 OCR 文本 → 选风格 → 生成 → 一键复制。"""
from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import pyperclip
from PIL import Image, ImageTk

from . import accessibility as ax
from .capture import capture_region
from .llm import generate_reply, generate_reply_stream
from .ocr import ocr_image
from .sender import send_to_wechat
from .styles import DEFAULT_STYLE, STYLES, build_prompt

THUMB_MAX_W = 260
THUMB_MAX_H = 86

# 多风格对比时使用的三个默认候选风格（涵盖"差异最大的三种语气"）
MULTI_STYLES: tuple[str, str, str] = ("幽默", "认真", "温柔")

# --- 视觉常量 ---
WINDOW_W = 720
WINDOW_H = 820
BG = "#f2f2f5"        # 窗口背景（类 macOS 浅灰）
CARD_BG = "#ffffff"   # 内嵌 Text 的背景
TEXT_FG = "#1d1d1f"
MUTED_FG = "#86868b"
OK_FG = "#34a853"
WARN_FG = "#ff9500"
ERR_FG = "#e5484d"
BORDER = "#d2d2d7"

FONT_BASE = ("PingFang SC", 12)
FONT_BODY = ("PingFang SC", 13)
FONT_H = ("PingFang SC", 13, "bold")
FONT_SM = ("PingFang SC", 11)


class AssistantWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("微信聊天助手")
        # 固定尺寸 + 禁止拖拽 —— 所有内容经过排版计算正好容纳
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self._apply_style()
        # state
        self.topmost_var = tk.BooleanVar(value=False)
        self.auto_regen = tk.BooleanVar(value=True)
        self.auto_send_var = tk.BooleanVar(value=False)
        self.mimic_var = tk.BooleanVar(value=True)
        self.style_var = tk.StringVar(value=DEFAULT_STYLE)
        self.extra_var = tk.StringVar()
        self.num_sentences_var = tk.IntVar(value=1)  # 回复句数：1 = 单句，>1 = 多句连发
        self.send_key_var = tk.StringVar(value="enter")  # 发送键: "enter" | "cmd_enter"
        self._last_capture_path: str | None = None
        self._thumb_image: ImageTk.PhotoImage | None = None
        self._last_prompt: str | None = None  # 最近一次发给 Claude 的 prompt（供查看）
        self._build_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._activate_app)
        # 只在整个窗口获得焦点时激活 NSApp；子控件获得焦点时不动（否则抢走 Entry/Text 的键盘焦点）
        self.root.bind("<FocusIn>", self._on_root_focus_in, add="+")

    def _on_root_focus_in(self, event: "tk.Event") -> None:
        if event.widget is self.root:
            self._activate_app()

    def _apply_style(self) -> None:
        s = ttk.Style()
        try:
            s.theme_use("aqua")  # macOS 原生风格
        except tk.TclError:
            pass
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, font=FONT_BASE, foreground=TEXT_FG)
        s.configure("Heading.TLabel", background=BG, font=FONT_H, foreground=TEXT_FG)
        s.configure("Muted.TLabel", background=BG, font=FONT_SM, foreground=MUTED_FG)
        s.configure("Status.TLabel", background=BG, font=FONT_SM, foreground=MUTED_FG)
        s.configure("TCheckbutton", background=BG, font=FONT_BASE)
        s.configure("TRadiobutton", background=BG, font=FONT_BASE)
        s.configure("TLabelframe", background=BG, font=FONT_H, foreground=TEXT_FG)
        s.configure("TLabelframe.Label", background=BG, font=FONT_H, foreground=TEXT_FG)
        s.configure("TButton", font=FONT_BASE)
        s.configure("TNotebook", background=BG)
        s.configure("TNotebook.Tab", font=FONT_BASE, padding=(16, 6))
        s.configure("TEntry", font=FONT_BODY)

    def _build_widgets(self) -> None:
        # 统一外边距
        PX = 14

        # ---------- Row 1: 数据源 + 置顶 + 状态 ----------
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, padx=PX, pady=(PX, 4))
        ttk.Button(top, text="📥 读取微信", command=self.on_read_ax).pack(side=tk.LEFT)
        ttk.Button(top, text="📸 截图 OCR", command=self.on_capture).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Checkbutton(
            top, text="置顶", variable=self.topmost_var, command=self._apply_topmost
        ).pack(side=tk.LEFT, padx=8)
        self.status = ttk.Label(top, text="就绪", style="Status.TLabel")
        self.status.pack(side=tk.LEFT, padx=8)

        # ---------- Row 2: 截图预览（固定高度，避免出现时挤压其他区域） ----------
        preview_frame = ttk.LabelFrame(self.root, text=" 截图预览 ")
        preview_frame.pack(fill=tk.X, padx=PX, pady=4)
        inner = ttk.Frame(preview_frame)
        inner.pack(fill=tk.X, padx=6, pady=6)
        self.thumb_label = tk.Label(
            inner,
            text="未截图 · 点「📸 截图 OCR」开始",
            fg=MUTED_FG,
            bg=CARD_BG,
            width=34,
            height=4,
            relief="flat",
            bd=1,
            highlightthickness=1,
            highlightbackground=BORDER,
            font=FONT_SM,
        )
        self.thumb_label.pack(side=tk.LEFT)
        preview_btns = ttk.Frame(inner)
        preview_btns.pack(side=tk.LEFT, padx=10)
        self.preview_open_btn = ttk.Button(
            preview_btns, text="🔍 查看大图", command=self.on_open_preview, state=tk.DISABLED
        )
        self.preview_open_btn.pack(anchor="w", pady=2)
        self.recapture_btn = ttk.Button(
            preview_btns, text="🔁 重新框选", command=self.on_capture, state=tk.DISABLED
        )
        self.recapture_btn.pack(anchor="w", pady=2)

        # ---------- Row 3: 对话内容（带滚动条，放得下多轮历史）----------
        ttk.Label(self.root, text="对话内容（可编辑）", style="Heading.TLabel").pack(
            anchor="w", padx=PX, pady=(8, 2)
        )
        chat_frame = ttk.Frame(self.root)
        chat_frame.pack(fill=tk.X, padx=PX, pady=(0, 6))
        chat_scroll = ttk.Scrollbar(chat_frame, orient=tk.VERTICAL)
        chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_text = tk.Text(
            chat_frame,
            height=6,
            wrap=tk.WORD,
            font=FONT_BODY,
            bg=CARD_BG,
            fg=TEXT_FG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=8,
            pady=6,
            yscrollcommand=chat_scroll.set,
        )
        self.chat_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chat_scroll.config(command=self.chat_text.yview)

        # ---------- Row 4: 风格选择 ----------
        style_frame = ttk.LabelFrame(self.root, text=" 风格（点击切换） ")
        style_frame.pack(fill=tk.X, padx=PX, pady=4)
        names = list(STYLES.keys())
        cols = 5
        for i, name in enumerate(names):
            rb = ttk.Radiobutton(
                style_frame,
                text=name,
                value=name,
                variable=self.style_var,
                command=self._on_style_change,
            )
            rb.grid(row=i // cols, column=i % cols, sticky="w", padx=10, pady=4)
        for c in range(cols):
            style_frame.columnconfigure(c, weight=1)

        # ---------- Row 5: 额外要求 + 句数 ----------
        extra_row = ttk.Frame(self.root)
        extra_row.pack(fill=tk.X, padx=PX, pady=4)
        ttk.Label(extra_row, text="额外要求").pack(side=tk.LEFT)
        entry = tk.Entry(
            extra_row,
            textvariable=self.extra_var,
            font=FONT_BODY,
            bg=CARD_BG,
            fg=TEXT_FG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            insertbackground=TEXT_FG,
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=4)
        ttk.Label(extra_row, text="句数").pack(side=tk.LEFT, padx=(10, 2))
        ttk.Spinbox(
            extra_row,
            from_=1,
            to=5,
            width=3,
            textvariable=self.num_sentences_var,
            state="readonly",
        ).pack(side=tk.LEFT)

        # 发送键：跟 WeChat「设置→通用→用 ⌘Enter 发送消息」对应
        ttk.Label(extra_row, text="发送键").pack(side=tk.LEFT, padx=(10, 2))
        send_key_cb = ttk.Combobox(
            extra_row,
            textvariable=self.send_key_var,
            values=["enter", "cmd_enter"],
            state="readonly",
            width=10,
        )
        send_key_cb.pack(side=tk.LEFT)

        # ---------- Row 6: 生成按钮行 ----------
        action = ttk.Frame(self.root)
        action.pack(fill=tk.X, padx=PX, pady=(4, 2))
        self.gen_btn = ttk.Button(action, text="✨ 生成回复", command=self.on_generate)
        self.gen_btn.pack(side=tk.LEFT)
        ttk.Button(action, text="🔄 换一条", command=self.on_generate).pack(
            side=tk.LEFT, padx=6
        )
        self.multi_btn = ttk.Button(
            action, text="🎲 对比 3 种风格", command=self.on_generate_multi
        )
        self.multi_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(action, text="🔍 查看 Prompt", command=self.on_view_prompt).pack(
            side=tk.LEFT, padx=6
        )

        # ---------- Row 7: 选项 ----------
        opts = ttk.Frame(self.root)
        opts.pack(fill=tk.X, padx=PX, pady=(0, 6))
        ttk.Checkbutton(
            opts, text="换风格自动重生成", variable=self.auto_regen
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            opts, text="自动发送", variable=self.auto_send_var
        ).pack(side=tk.LEFT, padx=14)
        ttk.Checkbutton(
            opts, text="模仿我的说话风格", variable=self.mimic_var
        ).pack(side=tk.LEFT, padx=14)

        # ---------- Row 8: 结果 Notebook ----------
        self.result_nb = ttk.Notebook(self.root)
        self.result_nb.pack(fill=tk.BOTH, expand=True, padx=PX, pady=(4, PX))

        # Tab 1：建议回复
        tab_single = ttk.Frame(self.result_nb)
        self.result_nb.add(tab_single, text="  建议回复  ")
        self.reply_text = tk.Text(
            tab_single,
            height=5,
            wrap=tk.WORD,
            font=("PingFang SC", 14),
            bg=CARD_BG,
            fg=TEXT_FG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=8,
            pady=6,
        )
        self.reply_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        send_row = ttk.Frame(tab_single)
        send_row.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(
            send_row, text="📤 发送 (回车)", command=lambda: self.on_send(True)
        ).pack(side=tk.LEFT)
        ttk.Button(
            send_row, text="📝 只粘贴", command=lambda: self.on_send(False)
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(send_row, text="📋 复制", command=self.on_copy).pack(
            side=tk.LEFT, padx=6
        )

        # Tab 2：3 候选对比
        tab_multi = ttk.Frame(self.result_nb)
        self.result_nb.add(tab_multi, text="  🎲 3 候选对比  ")
        ttk.Label(
            tab_multi,
            text="点任一条的「选中」→ 回填到「建议回复」并复制",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=8, pady=(6, 2))
        self.candidate_widgets: list[dict] = []
        for i in range(3):
            row = ttk.Frame(tab_multi)
            row.pack(fill=tk.BOTH, expand=True, pady=3, padx=8)
            label = ttk.Label(row, text="", width=6, style="Muted.TLabel")
            label.pack(side=tk.LEFT, anchor="n", pady=4)
            ctext = tk.Text(
                row,
                height=3,
                wrap=tk.WORD,
                font=FONT_BODY,
                bg=CARD_BG,
                fg=TEXT_FG,
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=BORDER,
                padx=6,
                pady=4,
            )
            ctext.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
            pick_btn = ttk.Button(row, text="选中", state=tk.DISABLED, width=6)
            pick_btn.pack(side=tk.LEFT, anchor="n", pady=4)
            self.candidate_widgets.append({"label": label, "text": ctext, "btn": pick_btn})

    # ---------------------- actions ----------------------

    def _apply_topmost(self) -> None:
        self.root.attributes("-topmost", self.topmost_var.get())

    def _set_status(self, text: str, color: str = MUTED_FG) -> None:
        self.status.config(text=text, foreground=color)
        self.root.update_idletasks()

    def _on_style_change(self) -> None:
        self._set_status(f"已选风格：{self.style_var.get()}", OK_FG)
        if (
            self.auto_regen.get()
            and self.reply_text.get("1.0", "end").strip()
            and self.chat_text.get("1.0", "end").strip()
        ):
            self.on_generate()

    def on_capture(self) -> None:
        self.root.withdraw()
        self.root.after(200, self._do_capture)

    def _do_capture(self) -> None:
        try:
            path, reason = capture_region()
            if path is None:
                # 把失败原因直接打到预览区 + 状态栏 + 终端，方便定位
                self.thumb_label.config(
                    image="",
                    text=f"⚠ 截图未成功\n{reason}",
                    fg=ERR_FG,
                    width=40,
                    height=5,
                )
                self._thumb_image = None
                self._set_status(reason, ERR_FG)
                print(f"[capture] FAIL: {reason}")
                return
            # 替换上一张截图（保留当前这张直到下次截图或关窗）
            old = self._last_capture_path
            self._last_capture_path = path
            if old and os.path.exists(old):
                try:
                    os.unlink(old)
                except OSError:
                    pass

            self._show_thumbnail(path)
            self.preview_open_btn.config(state=tk.NORMAL)
            self.recapture_btn.config(state=tk.NORMAL)

            self._set_status("识别中…", OK_FG)
            text = ocr_image(path)
            self.chat_text.delete("1.0", tk.END)
            self.chat_text.insert("1.0", text)
            self._set_status(
                f"识别完成（{len(text)} 字）· 上方缩略图可核对截图范围", OK_FG
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("OCR 失败", str(e))
            self._set_status("OCR 失败", ERR_FG)
        finally:
            self.root.deiconify()
            self._activate_app()

    def _show_thumbnail(self, path: str) -> None:
        img = Image.open(path)
        img.thumbnail((THUMB_MAX_W, THUMB_MAX_H), Image.Resampling.LANCZOS)
        self._thumb_image = ImageTk.PhotoImage(img)
        self.thumb_label.config(image=self._thumb_image, text="", width=0, height=0)

    def on_open_preview(self) -> None:
        if self._last_capture_path and os.path.exists(self._last_capture_path):
            subprocess.run(["open", self._last_capture_path])
        else:
            self._set_status("还没有截图可查看", ERR_FG)

    def _on_close(self) -> None:
        if self._last_capture_path and os.path.exists(self._last_capture_path):
            try:
                os.unlink(self._last_capture_path)
            except OSError:
                pass
        self.root.destroy()

    def on_generate(self) -> None:
        chat = self.chat_text.get("1.0", tk.END).strip()
        if not chat:
            messagebox.showwarning("提示", "聊天内容为空，先框选截图或手动粘贴。")
            return
        self.result_nb.select(0)  # 切到「建议回复」tab
        self.gen_btn.config(state=tk.DISABLED)
        self._set_status(f"正在用【{self.style_var.get()}】生成…", OK_FG)
        self.reply_text.delete("1.0", tk.END)
        threading.Thread(
            target=self._generate_worker,
            args=(chat, self.style_var.get(), self.extra_var.get()),
            daemon=True,
        ).start()

    def _generate_worker(self, chat: str, style: str, extra: str) -> None:
        try:
            n = max(1, int(self.num_sentences_var.get() or 1))
            prompt = build_prompt(
                chat,
                style,
                extra,
                mimic_user=self.mimic_var.get(),
                num_sentences=n,
            )
            self._last_prompt = prompt
            print(
                f"[llm] 生成中 · style={style} · sentences={n} · prompt {len(prompt)} 字",
                flush=True,
            )
            # 开始前清空回复区，流式 chunk 来一条 append 一条
            self.root.after(0, lambda: self.reply_text.delete("1.0", tk.END))

            def on_chunk(delta: str) -> None:
                # 来自子线程 → 通过 after 切回主线程刷 UI
                self.root.after(0, self._append_reply_chunk, delta)

            full = generate_reply_stream(prompt, on_chunk)
            self.root.after(0, lambda: self._finalize_reply(full, style))
        except Exception as e:  # noqa: BLE001
            err = str(e)
            self.root.after(0, lambda: self._show_error(err))

    def _append_reply_chunk(self, delta: str) -> None:
        self.reply_text.insert(tk.END, delta)
        self.reply_text.see(tk.END)

    def _finalize_reply(self, reply: str, style: str) -> None:
        self._set_status(f"已生成 · 风格【{style}】· 已复制到剪贴板", OK_FG)
        self.gen_btn.config(state=tk.NORMAL)
        pyperclip.copy(reply)
        if self.auto_send_var.get():
            self.root.after(250, lambda: self.on_send(True))

    # ---------- 多风格并行对比 ----------

    def on_generate_multi(self) -> None:
        chat = self.chat_text.get("1.0", tk.END).strip()
        if not chat:
            messagebox.showwarning("提示", "聊天内容为空，先读取微信或粘贴对话。")
            return
        self.result_nb.select(1)  # 切到「3 候选对比」tab
        extra = self.extra_var.get()
        self.multi_btn.config(state=tk.DISABLED)
        self.gen_btn.config(state=tk.DISABLED)
        # 清空候选区
        for w in self.candidate_widgets:
            w["label"].config(text="… 生成中")
            w["text"].delete("1.0", tk.END)
            w["btn"].config(state=tk.DISABLED, command=None)
        self._set_status(f"并发生成 3 种风格：{' / '.join(MULTI_STYLES)}…", OK_FG)

        # 计数器让我们知道 3 条都完成后重新启用按钮
        self._multi_remaining = len(MULTI_STYLES)
        for i, style in enumerate(MULTI_STYLES):
            threading.Thread(
                target=self._multi_worker, args=(i, style, chat, extra), daemon=True
            ).start()

    def _multi_worker(self, i: int, style: str, chat: str, extra: str) -> None:
        try:
            n = max(1, int(self.num_sentences_var.get() or 1))
            prompt = build_prompt(
                chat,
                style,
                extra,
                mimic_user=self.mimic_var.get(),
                num_sentences=n,
            )
            if i == 0:
                self._last_prompt = prompt
            reply = generate_reply(prompt)
            self.root.after(0, lambda: self._show_candidate(i, style, reply))
        except Exception as e:  # noqa: BLE001
            err = str(e)
            self.root.after(0, lambda: self._show_candidate(i, style, f"[失败] {err}"))

    def _show_candidate(self, i: int, style: str, reply: str) -> None:
        w = self.candidate_widgets[i]
        w["label"].config(text=f"【{style}】")
        w["text"].delete("1.0", tk.END)
        w["text"].insert("1.0", reply)
        if not reply.startswith("[失败]"):
            w["btn"].config(
                state=tk.NORMAL, command=lambda r=reply, s=style: self._pick_candidate(r, s)
            )
        else:
            w["btn"].config(state=tk.DISABLED)
        self._multi_remaining -= 1
        if self._multi_remaining <= 0:
            self.multi_btn.config(state=tk.NORMAL)
            self.gen_btn.config(state=tk.NORMAL)
            self._set_status("3 种风格已生成 · 点任一条的「选中」发送", OK_FG)

    def _pick_candidate(self, reply: str, style: str) -> None:
        self.reply_text.delete("1.0", tk.END)
        self.reply_text.insert("1.0", reply)
        pyperclip.copy(reply)
        self.result_nb.select(0)  # 选中后切回「建议回复」，便于继续发送
        self._set_status(f"已选【{style}】· 已复制到剪贴板", OK_FG)
        if self.auto_send_var.get():
            self.root.after(150, lambda: self.on_send(True))

    def _show_error(self, msg: str) -> None:
        messagebox.showerror("生成失败", msg)
        self._set_status("生成失败", ERR_FG)
        self.gen_btn.config(state=tk.NORMAL)

    def on_copy(self) -> None:
        text = self.reply_text.get("1.0", tk.END).strip()
        if not text:
            return
        pyperclip.copy(text)
        self._set_status("已复制到剪贴板", OK_FG)

    def on_view_prompt(self) -> None:
        """弹窗显示最近一次发给 Claude 的完整 prompt + 模型信息，便于调试/理解输出。"""
        from .llm import CLAUDE_BIN

        win = tk.Toplevel(self.root)
        win.title("最近一次 Prompt · Claude Code CLI")
        win.geometry("680x620")
        win.configure(bg=BG)

        info = ttk.Label(
            win,
            text=(
                f"CLI: {CLAUDE_BIN}\n"
                f"调用方式: claude -p --output-format stream-json（流式）\n"
                f"模型: 使用 Claude Code 订阅的默认模型（通常是 Opus / Sonnet）\n"
                f"登录态: 复用 Claude Code 本机 session，无需 API key"
            ),
            style="Muted.TLabel",
            justify=tk.LEFT,
        )
        info.pack(fill=tk.X, padx=12, pady=(10, 6))

        ttk.Label(win, text="Prompt 正文：", style="Heading.TLabel").pack(
            anchor="w", padx=12, pady=(2, 4)
        )
        scr = ttk.Scrollbar(win, orient=tk.VERTICAL)
        txt = tk.Text(
            win,
            wrap=tk.WORD,
            font=FONT_SM,
            bg=CARD_BG,
            fg=TEXT_FG,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=8,
            pady=6,
            yscrollcommand=scr.set,
        )
        scr.config(command=txt.yview)
        scr.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 12), pady=(0, 12))
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        content = self._last_prompt or "（还没调用过生成，点「✨ 生成回复」或「🎲 对比 3 种风格」后再来看）"
        txt.insert("1.0", content)
        txt.config(state=tk.DISABLED)

    def on_read_ax(self) -> None:
        """通过 macOS Accessibility 读取当前微信，并自动向上翻页 2 次收集更多历史。"""
        if not ax.available():
            messagebox.showerror(
                "Accessibility 不可用",
                "未找到 pyobjc。运行 `./run.sh` 重装依赖，然后重启本应用。\n\n"
                f"详细错误：{ax.import_error()}",
            )
            return
        if not ax.trusted():
            messagebox.showwarning(
                "需要辅助功能权限",
                "在「系统设置 → 隐私与安全性 → 辅助功能」里给当前启动者打勾，然后完全退出本应用并重开。",
            )
            return
        self._set_status("正在从微信读取（含向上滚动补齐历史）…", OK_FG)
        self.root.update_idletasks()
        # 放到后台线程，避免 ~2 秒的滚动等待阻塞 UI
        threading.Thread(target=self._read_ax_worker, daemon=True).start()

    def _read_ax_worker(self) -> None:
        try:
            result = ax.read_wechat_multi_pass(passes=2)
        except Exception as e:  # noqa: BLE001
            err = str(e)
            self.root.after(0, lambda: self._show_error(f"AX 读取异常：{err}"))
            return
        self.root.after(0, lambda: self._on_ax_done(result))

    def _on_ax_done(self, result: tuple[str, int] | None) -> None:
        if result is None:
            messagebox.showwarning(
                "读取失败",
                "未读到聊天内容。常见原因：\n"
                "• 微信未启动 / 未登录\n"
                "• 不在聊天界面（在「通讯录」/「发现」）\n"
                "• 微信窗口最小化\n"
                "• 辅助功能权限未生效（改完权限要完全退出本应用再开）",
            )
            self._set_status("读取失败", ERR_FG)
            return
        text, n = result
        self.chat_text.delete("1.0", tk.END)
        self.chat_text.insert("1.0", text)
        self.chat_text.see(tk.END)  # 聚焦到最新消息
        self._set_status(f"读取完成（{n} 条消息 · {len(text)} 字 · 已合并多轮）", OK_FG)

    def on_send(self, press_enter: bool) -> None:
        reply = self.reply_text.get("1.0", tk.END).strip()
        if not reply:
            messagebox.showwarning("提示", "当前没有待发送的回复。先点「✨ 生成回复」。")
            return
        n = max(1, int(self.num_sentences_var.get() or 1))

        # 决定走单条还是多条发送
        payload: "str | list[str]"
        if n > 1:
            lines = [ln.strip() for ln in reply.splitlines() if ln.strip()]
            if len(lines) >= 2:
                payload = lines[:n]
                self._set_status(f"正在依次发送 {len(payload)} 条…", OK_FG)
                print(
                    f"[on_send] 多句模式 · 句数设置={n} · 实际切出 {len(lines)} 行 · 取前 {len(payload)}",
                    flush=True,
                )
                for idx, ln in enumerate(payload, 1):
                    print(f"[on_send]   行{idx}: {ln}", flush=True)
            else:
                # 句数 > 1 但模型只给了 1 行 —— 按单条发
                payload = reply
                print(
                    f"[on_send] 句数={n} 但模型只输出 1 行，回退单句发送：{reply[:40]}",
                    flush=True,
                )
        else:
            payload = reply
            print(f"[on_send] 单句模式: {reply[:40]}", flush=True)

        send_key = self.send_key_var.get() or "enter"
        print(f"[on_send] press_enter={press_enter}  send_key={send_key}", flush=True)

        # 子线程：多句之间有 sleep，主线程要保持响应
        def _worker() -> None:
            ok, msg = send_to_wechat(
                payload, press_enter=press_enter, send_key=send_key
            )
            self.root.after(
                0, lambda: self._set_status(msg, OK_FG if ok else ERR_FG)
            )
            if not ok:
                self.root.after(0, lambda: messagebox.showerror("发送失败", msg))

        threading.Thread(target=_worker, daemon=True).start()

    # ---------------------- lifecycle ----------------------

    def _activate_app(self) -> None:
        """同步把 Python 进程切到前台。**不再调 focus_force**——那会抢走 Entry/Text
        的键盘焦点，导致用户点文本框后输入失效。
        """
        try:
            from AppKit import NSApplication

            app = NSApplication.sharedApplication()
            app.activateIgnoringOtherApps_(True)
        except Exception:  # noqa: BLE001
            try:
                subprocess.Popen(
                    [
                        "osascript",
                        "-e",
                        f'tell application "System Events" to set frontmost of '
                        f"(first process whose unix id is {os.getpid()}) to true",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            self.root.lift()
        except Exception:  # noqa: BLE001
            pass

    def show(self) -> None:
        self.root.deiconify()
        self._activate_app()

    def hide(self) -> None:
        self.root.withdraw()

    def run(self) -> None:
        self.root.mainloop()
