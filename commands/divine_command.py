"""/占卜 命令：多牌阵塔罗占卜。

两种入口：

- 无参 ``/占卜``：进入"问答模式"。actor 用自己人格生成 3 个问题，
  插件登记一个 pending session，等用户的下一条非命令消息作为咨询主题，
  再走完整抽牌流程。这部分由 ``handlers/answer_handler.py`` 负责接管回答。
- 带参 ``/占卜 <关键词>``：保持原行为，关键词参与牌阵匹配，直接抽。

抽完牌**不**调 LLM 解析；牌面文本入聊天流上下文，由 actor 在用户后续提问时
凭上下文自由解读。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image, send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

from . import _core
from ._questioner import ask_questions
from ._session import SESSION_STORE, PendingSession

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin

logger = get_logger("tarot.divine")

HELP_TEXT = (
    "塔罗牌占卜命令：\n"
    "  /占卜              进入问答模式（我会先问你三个问题）\n"
    "  /占卜 情感         按关键词匹配牌阵，直接抽\n"
    "  /占卜 圣三角牌阵    指定牌阵，直接抽\n"
    "  /占卜 帮助         显示本帮助\n"
    "  /塔罗牌            单张占卜\n"
    "问答模式中输入「取消」可主动退出。\n"
    "牌发出来后，可以直接问我具体某张是什么意思~"
)

# 用户主动退出问答模式的关键词
CANCEL_KEYWORDS: frozenset[str] = frozenset({"取消", "算了", "cancel"})


def _resolve_path(raw: str) -> Path:
    """把配置里的相对/绝对路径解析为绝对 Path。

    相对路径相对于 Neo-MoFox 仓库根（即进程工作目录）。

    Args:
        raw: 配置里写的路径字符串

    Returns:
        绝对 Path
    """
    p = Path(raw)
    return p if p.is_absolute() else Path.cwd() / p


def _read_as_base64(path: Path) -> str:
    """同步读取文件并转 base64（供 ``asyncio.to_thread`` 调用）。"""
    return base64.b64encode(path.read_bytes()).decode("ascii")


class DivineCommand(BaseCommand):
    """``/占卜`` 命令：多牌阵塔罗占卜。"""

    command_name: str = "占卜"
    command_description: str = "多牌阵塔罗占卜；无参进入问答模式，带参直接抽"
    permission_level: PermissionLevel = PermissionLevel.USER

    @cmd_route()
    async def handle_divine(self, keyword: str = "") -> tuple[bool, str]:
        """占卜入口。

        Args:
            keyword: 牌阵匹配关键词（如 "情感" 或具体牌阵名 "圣三角牌阵"）。
                留空则进入问答模式。

        Returns:
            (是否成功, 状态描述)
        """
        keyword = keyword.strip()
        if keyword:
            return await run_divine_flow(
                plugin=self.plugin,
                stream_id=self.stream_id,
                platform=self._message.platform if self._message is not None else None,
                user_input=keyword,
            )
        return await _enter_question_mode(self)

    @cmd_route("帮助")
    async def handle_help(self) -> tuple[bool, str]:
        """显示帮助文本。"""
        await send_text(HELP_TEXT, stream_id=self.stream_id)
        return True, "help"


# =============================================================================
# 问答模式入口
# =============================================================================


async def _enter_question_mode(cmd: BaseCommand) -> tuple[bool, str]:
    """无参 ``/占卜`` 入口：调 actor 出题、登记 pending session。

    Args:
        cmd: 命令实例（提供 stream_id / 发送者信息）

    Returns:
        (是否成功, 状态描述)
    """
    if cmd._message is None:
        await send_text("无法获取消息上下文，请稍后再试。", stream_id=cmd.stream_id)
        return False, "no message"

    sender_id = cmd._message.sender_id or ""
    if not sender_id:
        await send_text("没拿到你的身份信息，没法登记占卜会话。", stream_id=cmd.stream_id)
        return False, "no sender id"

    user_display = (
        cmd._message.sender_cardname
        or cmd._message.sender_name
        or sender_id
    )
    platform = cmd._message.platform or None

    # 调 actor 生成问题（失败有兜底，函数内部保证返回非空）
    questions_text = await ask_questions(stream_id=cmd.stream_id, user_display=user_display)

    # 登记 pending；同流的旧 session 会被替换
    config = cmd.plugin.config
    if config is not None:
        SESSION_STORE.set_ttl(float(config.behavior.question_timeout_seconds))
    SESSION_STORE.put(
        PendingSession(
            stream_id=cmd.stream_id,
            sender_id=sender_id,
            platform=platform or "",
            questions_text=questions_text,
        )
    )

    await send_text(questions_text, stream_id=cmd.stream_id, platform=platform)
    logger.info(f"已为 {user_display}({sender_id}) 在 {cmd.stream_id} 登记 tarot pending session")
    return True, "asked"


# =============================================================================
# 抽牌发送主流程（被 cmd / EventHandler 共用）
# =============================================================================


async def run_divine_flow(
    plugin: "BasePlugin",
    stream_id: str,
    platform: str | None,
    user_input: str,
) -> tuple[bool, str]:
    """执行多牌阵占卜的发送流程。

    被 ``DivineCommand`` 和 ``TarotAnswerHandler`` 共同调用。
    本函数只负责"已经知道用户输入"之后的部分：选阵 → 抽牌 → 发图文。

    Args:
        plugin: 插件实例（提供 config）
        stream_id: 聊天流 ID
        platform: 平台标识，可为 None（让 send_api 自行从 stream_id 推断）
        user_input: 用户输入文本（关键词或回答）

    Returns:
        (是否成功, 状态描述)
    """
    config = plugin.config
    if config is None:
        await send_text("塔罗插件配置缺失，无法占卜。", stream_id=stream_id, platform=platform)
        return False, "no config"

    resource_path = _resolve_path(config.resources.resource_path)
    cache_dir = _resolve_path(config.resources.rotated_cache_dir)
    tarot_json = Path(__file__).resolve().parent.parent / "tarot.json"
    interval = float(config.behavior.interval_seconds)
    show_meaning = bool(config.behavior.show_card_meaning)

    try:
        formations, all_cards = _core.load_tarot_data(tarot_json)
        result = _core.perform_divination(
            resource_path=resource_path,
            formations=formations,
            all_cards=all_cards,
            user_input=user_input,
        )
    except _core.TarotResourceError as exc:
        await send_text(f"占卜失败：{exc}", stream_id=stream_id, platform=platform)
        logger.warning(f"占卜资源错误: {exc}")
        return False, str(exc)

    await send_text(
        f"启用 {result.formation_name}，正在洗牌中...",
        stream_id=stream_id,
        platform=platform,
    )

    total = len(result.cards)
    for idx, card in enumerate(result.cards):
        header = _core.format_card_header(idx, total, result.is_cut, card.position_label)
        body = _core.format_card_body(card.info, card.is_upright, show_meaning)
        text = f"{header}\n{body}"

        img_path = await _core.render_card_image(
            resource_path=resource_path,
            cache_dir=cache_dir,
            theme=result.theme,
            card_info=card.info,
            is_upright=card.is_upright,
        )

        await send_text(text, stream_id=stream_id, platform=platform)

        if img_path is None:
            await send_text(
                f"({card.info.get('name_cn', '未知')} 的图片未找到，主题 {result.theme})",
                stream_id=stream_id,
                platform=platform,
            )
        else:
            try:
                b64 = await asyncio.to_thread(_read_as_base64, img_path)
                await send_image(
                    image_data=b64,
                    stream_id=stream_id,
                    platform=platform,
                    processed_plain_text=f"[塔罗牌图: {card.info.get('name_cn', '')}]",
                )
            except OSError as exc:
                logger.warning(f"读取塔罗牌图失败: {img_path} - {exc}")
                await send_text(
                    f"(图片读取失败：{img_path.name})",
                    stream_id=stream_id,
                    platform=platform,
                )

        if idx < total - 1 and interval > 0:
            await asyncio.sleep(interval)

    return True, "ok"
