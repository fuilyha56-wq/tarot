"""问答模式回答处理器：监听用户消息，匹配 pending session 后触发占卜。

当用户在问答模式下回复（非命令消息），本处理器拦截该消息，
将其作为占卜主题传入抽牌流程，然后返回 STOP 阻止消息继续进入 chatter。

若该流无 pending session 或发送者不匹配，则 PASS 放行。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.app.plugin_system.types import EventType

from .._session import SESSION_STORE
from ..divine_command import CANCEL_KEYWORDS, run_divine_flow

logger = get_logger("tarot.answer_handler")


class TarotAnswerHandler(BaseEventHandler):
    """问答模式回答处理器：匹配 pending session 后触发占卜。"""

    handler_name: str = "tarot_answer"
    handler_description: str = "问答模式下拦截用户回答并触发占卜"
    weight: int = 50
    intercept_message: bool = True
    init_subscribe: list[EventType | str] = [EventType.ON_MESSAGE_RECEIVED]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理收到的消息，检查是否为问答模式的回答。

        Args:
            event_name: 事件名
            params: 事件参数，含 "message" 键

        Returns:
            (EventDecision, 更新后的 params)
        """
        message = params.get("message")
        if message is None:
            return EventDecision.PASS, params

        stream_id = getattr(message, "stream_id", None)
        sender_id = getattr(message, "sender_id", None)
        text = (getattr(message, "processed_plain_text", "") or "").strip()

        if not stream_id or not sender_id or not text:
            return EventDecision.PASS, params

        # 跳过命令消息（以 / 开头）
        if text.startswith("/"):
            return EventDecision.PASS, params

        # 尝试匹配 pending session
        session = SESSION_STORE.pop_if_match(stream_id, sender_id)
        if session is None:
            return EventDecision.PASS, params

        # 检查用户是否主动取消
        if text in CANCEL_KEYWORDS:
            await send_text("好的，已取消本次占卜～", stream_id=stream_id, platform=session.platform or None)
            logger.info(f"用户 {sender_id} 在 {stream_id} 主动取消占卜问答")
            return EventDecision.STOP, params

        # 触发占卜流程
        logger.info(f"问答模式匹配成功：{sender_id} 在 {stream_id}，输入：{text[:40]}")
        success, status = await run_divine_flow(
            plugin=self.plugin,
            stream_id=stream_id,
            platform=session.platform or None,
            user_input=text,
        )

        if not success:
            await send_text(
                "占卜出了点问题，请稍后再试～",
                stream_id=stream_id,
                platform=session.platform or None,
            )

        # STOP：阻止消息进入 chatter，避免 actor 对用户的回答再做一轮回复
        return EventDecision.STOP, params
