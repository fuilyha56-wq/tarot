"""问答模式：让 actor 用自己人格生成三个塔罗占卜前置问题。

模块只导出 ``ask_questions``：输入聊天上下文，输出一段已格式化好的问句文本，
直接发送给用户即可。
"""

from __future__ import annotations

from src.app.plugin_system.api import llm_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api import stream_api
from src.core.config import get_core_config
from src.kernel.llm import LLMPayload, ROLE, Text

logger = get_logger("tarot.questioner")

# 兜底问题：LLM 调用失败时使用，保证占卜流程不被打断
_FALLBACK_QUESTIONS: str = (
    "嗯……让我先了解一下你的情况吧～\n"
    "1. 你最近最在意的是哪方面的事呀？是感情、工作还是别的？\n"
    "2. 这件事现在大概发展到哪一步了？\n"
    "3. 你最希望从塔罗牌里得到什么样的指引？"
)

# 用作 actor 人格说明的兜底（当 core_config 取不到时）
_FALLBACK_PERSONA: str = "友好、活泼、乐于助人"


def _gather_persona() -> tuple[str, str]:
    """从 core_config 取 (nickname, persona) 字段。

    Returns:
        (昵称, 人格描述)；任一字段为空时使用兜底
    """
    try:
        cfg = get_core_config()
        nickname = (cfg.personality.nickname or "").strip() or "塔罗师"
        persona = (cfg.personality.personality_core or "").strip() or _FALLBACK_PERSONA
        side = (cfg.personality.personality_side or "").strip()
        if side:
            persona = f"{persona}；{side}"
        return nickname, persona
    except Exception as exc:  # noqa: BLE001 — 取不到配置就走兜底
        logger.debug(f"读取人格配置失败，使用默认值: {exc}")
        return "塔罗师", _FALLBACK_PERSONA


async def _gather_recent_context(stream_id: str, limit: int = 8) -> str:
    """拉聊天流最近 N 条消息组成简短上下文摘要。

    Args:
        stream_id: 聊天流 ID
        limit: 最近多少条

    Returns:
        多行文本；取不到时返回空串
    """
    try:
        msgs = await stream_api.get_stream_messages(stream_id, limit=limit, offset=0)
    except Exception as exc:  # noqa: BLE001 — 上下文不是必需，失败就跳过
        logger.debug(f"获取聊天上下文失败: {exc}")
        return ""

    lines: list[str] = []
    # 数据库返回多按时间倒序；此处按需正序
    for m in reversed(list(msgs)):
        text = (getattr(m, "processed_plain_text", "") or "").strip()
        if not text:
            continue
        sender = (
            getattr(m, "sender_cardname", None)
            or getattr(m, "sender_name", None)
            or getattr(m, "sender_id", "")
            or "用户"
        )
        # 防止注入：截断每条
        text = text[:120]
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)


def _build_prompt(
    nickname: str,
    persona: str,
    user_display: str,
    recent_context: str,
) -> tuple[str, str]:
    """构造 (system_prompt, user_prompt)。"""
    system_prompt = (
        f"你的名字是「{nickname}」，性格是：{persona}。\n"
        "现在用户请你帮 ta 占卜塔罗牌。在抽牌前，"
        "你需要用你自己的口吻向 ta 提出 **三个**有助于解读的问题，"
        "比如关心的领域、目前的处境、最想得到的指引等。\n\n"
        "硬性要求：\n"
        "1. 必须只输出三个问题，每个一行，用「1. / 2. / 3.」编号；\n"
        "2. 三个问题之间循序渐进、覆盖不同方面，不要语义重复；\n"
        "3. 用你自己的语气说话，可以带轻量颜文字或语气词，不要过于正式；\n"
        "4. 不要添加任何额外解释、寒暄或引导语，直接给三行编号问题；\n"
        "5. 用中文。"
    )
    ctx_block = recent_context.strip() or "（暂无最近聊天记录可供参考）"
    user_prompt = (
        f"咨询者：{user_display}\n"
        f"最近的聊天上下文（只是辅助你了解 ta 的近况，可以参考可以忽略）：\n"
        f"{ctx_block}\n\n"
        "请按要求输出三个问题。"
    )
    return system_prompt, user_prompt


async def _resolve_text(response) -> str:  # noqa: ANN001 — response 类型由内核决定
    """提取 LLM 响应文本，回退到 reasoning_content。"""
    raw = await response
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    reasoning = getattr(response, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    return raw.strip() if isinstance(raw, str) else ""


async def ask_questions(stream_id: str, user_display: str) -> str:
    """让 actor 生成三个塔罗占卜前置问题。

    Args:
        stream_id: 聊天流 ID（用于拉最近上下文）
        user_display: 用户显示名（昵称 / 群名片 / QQ 号）

    Returns:
        一段可直接发送的文本（包含 3 行编号问题）；
        LLM 失败时返回 ``_FALLBACK_QUESTIONS``
    """
    nickname, persona = _gather_persona()
    recent_context = await _gather_recent_context(stream_id, limit=8)
    system_prompt, user_prompt = _build_prompt(nickname, persona, user_display, recent_context)

    try:
        model_set = llm_api.get_model_set_by_task("actor")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"找不到 actor 模型集，使用兜底问题: {exc}")
        return _FALLBACK_QUESTIONS

    try:
        request = llm_api.create_llm_request(
            model_set=model_set,
            request_name="tarot_pre_question",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))
        response = await request.send(stream=False)
        text = await _resolve_text(response)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"actor 生成问题失败，使用兜底: {exc}")
        return _FALLBACK_QUESTIONS

    text = text.strip()
    if not text:
        return _FALLBACK_QUESTIONS

    # 简单兜底校验：模型偶尔会忘加编号或多输出。这里只做最低保留：
    # 若一行都没包含 "1." 则贴上备注（不强行重排，避免过度处理）
    if "1." not in text and "1、" not in text and "①" not in text:
        logger.debug(f"actor 返回未带编号，仍直接发送：{text[:60]}")

    return text
