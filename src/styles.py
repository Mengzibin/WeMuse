"""聊天风格预设。每个风格对应一段 system-prompt 片段。"""

STYLES: dict[str, str] = {
    "幽默": "风趣俏皮，带一点机灵小玩笑，但不要油腻，不要强行谐音梗。",
    "严肃": "语气正经、克制、不带情绪词，句子简短，直接回应要点。",
    "认真": "诚恳、就事论事，把关键信息讲清楚，必要时分点陈述。",
    "正式": "使用书面语，礼貌得体，适合工作或长辈场景，避免口语词和表情。",
    "嬉皮笑脸": "轻松调侃、有网感、可适度使用「哈哈」「嘿嘿」「(doge)」一类气口，但不过火。",
    "温柔": "关怀体贴、语气柔和，多用「呀」「呢」「好的哦」等软化词，让对方感觉被在意。",
    "专业": "工作语境下的同事口吻，逻辑清晰、简练，不寒暄废话，必要时给出下一步。",
    "高冷": "简短、留白、不解释过多，一两句带过，保持距离感但不失礼。",
    "暧昧": "含蓄带点小心思，语气轻，有进有退，不直白但留余地。",
    "怼回去": "不客气地反驳或吐槽，占住理但不骂人，句子利落有力。",
}

DEFAULT_STYLE = "认真"


def extract_my_examples(chat_text: str, limit: int = 10) -> list[str]:
    """从标注过发言人的聊天文本里抽取"我说过的话"，做风格模仿的 few-shot 样本。

    只要标记为「我：」的行；过滤长度 < 3 的（大多是 "嗯" "啊" 这种虚词，不带风格信息）。
    """
    out: list[str] = []
    for line in chat_text.splitlines():
        line = line.strip()
        if not (line.startswith("我：") or line.startswith("我:")):
            continue
        msg = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        if len(msg) >= 3:
            out.append(msg)
    return out[-limit:]


def calculate_length_target(chat_text: str) -> tuple[int, str]:
    """计算当前轮次回复的目标字数 + 模式。

    Returns:
        (target_chars, mode)
          mode="reply":    最后一行是「对方：…」；target = 本轮对方累计字数
                           （"本轮" = 自最近一条「我：…」之后）
          mode="continue": 最后一行是「我：…」；target = 我那条消息的字数
          mode="none":     无法判断（空聊天等）；target = 0
    """
    lines = [ln.strip() for ln in chat_text.splitlines() if ln.strip()]
    if not lines:
        return 0, "none"

    # 找最近的「我：」行
    last_me_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("我：") or line.startswith("我:"):
            last_me_idx = i

    # 「我：」之后的所有「对方：」消息长度之和
    tail = lines[last_me_idx + 1:]
    opp_total = 0
    for line in tail:
        if line.startswith("对方：") or line.startswith("对方:"):
            body = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            opp_total += len(body)

    if opp_total > 0:
        return opp_total, "reply"

    if last_me_idx >= 0:
        # 最后是我：续说模式
        last_me_body = lines[last_me_idx].split("：", 1)[-1].split(":", 1)[-1].strip()
        return len(last_me_body), "continue"

    return 0, "none"


def build_prompt(
    chat_text: str,
    style: str,
    extra_instruction: str = "",
    mimic_user: bool = False,
    num_sentences: int = 1,
) -> str:
    """拼接最终送给 Claude 的 prompt。

    参数：
      mimic_user:    True 时把聊天里「我：…」的历史作为 few-shot，贴近用户本人说话方式
      num_sentences: 1 = 生成单条回复；>1 = 生成 N 条换行分隔的独立短消息（会被依次发送）
    """
    style_desc = STYLES.get(style, style)
    extra = f"\n额外要求：{extra_instruction}" if extra_instruction.strip() else ""

    # few-shot 模仿
    mimic_block = ""
    if mimic_user:
        examples = extract_my_examples(chat_text)
        if examples:
            bullets = "\n".join(f"  - {e}" for e in examples)
            mimic_block = (
                "\n\n以下是「我」在这段对话里已经发过的消息。请仔细观察用词、长度、语气、习惯表达，"
                "让生成的回复在满足风格要求的前提下，尽量贴近【我】本人的说话方式：\n"
                f"{bullets}\n"
            )

    # 长度目标：自动累计"本轮对方发来"的字数
    target_len, mode = calculate_length_target(chat_text)
    if mode == "reply" and target_len > 0:
        low = max(3, int(target_len * 0.7))
        high = max(int(target_len * 1.3), 15)
        length_rule = (
            f"**长度目标**：本轮（自我上一次发言以来）对方累计发来 {target_len} 字。"
            f"你的整个回复（{num_sentences} 条合计，如为多句模式）控制在 **{low} - {high} 字**之间。"
        )
    elif mode == "continue":
        low = max(3, int(target_len * 0.6))
        high = max(int(target_len * 1.3), 10)
        length_rule = (
            f"**长度目标**：续接我刚发的消息（约 {target_len} 字），"
            f"新消息总长度控制在 **{low} - {high} 字**之间。"
        )
    else:
        length_rule = "**长度目标**：自然、符合微信聊天习惯（通常不超过 40 字）。"

    # 单句 / 多句模式
    if num_sentences > 1:
        output_rule = (
            f"\n**多句模式**：请输出**恰好 {num_sentences} 条独立的短消息**，"
            f"**每条单独占一行（用换行分隔）**。"
            f"每条都是一条完整的微信消息（不是半句），它们会被作为 {num_sentences} 条独立消息依次发送。"
            f"整体语气连贯、承接合理，但绝不要合并成一条长消息。"
            f"**只输出这 {num_sentences} 行纯文本**，不要加行号、引号、说明或空行。"
        )
    else:
        output_rule = "\n**单句模式**：输出一条回复，尽量一行完成，除非必要不分行。"

    return f"""你是用户的微信聊天助手。下面是用户当前聊天窗口里识别到的对话内容，按时间从上到下排列。
每行格式约定：
- 以「我：」开头 → 用户本人发出的消息（绿色气泡）
- 以「对方：」开头 → 聊天对方发来的消息（白/灰气泡）
- 以「──【HH:MM】──」或「──【昨天 12:30】──」这样的格式 → 时间分隔条，帮助你判断消息的时间跨度；**不要在回复里提这些标记**

<chat>
{chat_text.strip()}
</chat>{mimic_block}

请站在「我」的角度，生成下一条要发出的消息。根据对话最后一行的发言人判断任务类型：

- 最后一行是「对方：」→ **回复模式**：针对"本轮"对方累计发来的所有消息一并作答（而不仅仅是最后一句）。
- 最后一行是「我：」→ **续接模式**：沿着我刚发的那句话再补一句（补理由 / 抛新问题 / 缓和语气 / 推进话题），**绝不要跳过我的话去回答对方之前的消息**，也不要和我刚才那句语义重复。

风格要求：【{style}】——{style_desc}{extra}
{output_rule}

硬性规则：
1. 只输出消息正文本身，不要加「回复：」「我：」之类的前缀，不要加引号，不要解释。
2. {length_rule}
3. 符合中文微信聊天习惯：偏口语，除非必要不分段、不列要点。
4. 续接时，新消息要承接上一句的语气和话题，有新增信息或推进，不要单纯重复。
5. 不要编造用户没说过的事实（时间、地点、承诺、人名等）。
"""
