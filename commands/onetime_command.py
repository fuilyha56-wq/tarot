"""/塔罗牌 命令：单张占卜。

抽完一张牌后**不**调用 LLM 生成解析；牌面图文进入聊天流上下文，
由 actor 在用户后续提问时自由解读。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image, send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

from . import _core
from .divine_command import HELP_TEXT, _read_as_base64, _resolve_path

logger = get_logger("tarot.onetime")


class OnetimeDivineCommand(BaseCommand):
    """``/塔罗牌`` 命令：抽一张塔罗牌。"""

    command_name: str = "塔罗牌"
    command_description: str = "抽一张塔罗牌；解读由 actor 凭上下文自由发挥"
    permission_level: PermissionLevel = PermissionLevel.USER

    @cmd_route()
    async def handle_onetime(self) -> tuple[bool, str]:
        """单张占卜入口。

        Returns:
            (是否成功, 状态描述)
        """
        return await _run_onetime(self)

    @cmd_route("帮助")
    async def handle_help(self) -> tuple[bool, str]:
        """显示帮助文本。"""
        await send_text(HELP_TEXT, stream_id=self.stream_id)
        return True, "help"


async def _run_onetime(cmd: BaseCommand) -> tuple[bool, str]:
    """执行单张占卜的发送流程。

    Args:
        cmd: 调用方命令实例

    Returns:
        (是否成功, 状态描述)
    """
    config = cmd.plugin.config
    if config is None:
        await send_text("塔罗插件配置缺失，无法占卜。", stream_id=cmd.stream_id)
        return False, "no config"

    resource_path = _resolve_path(config.resources.resource_path)
    cache_dir = _resolve_path(config.resources.rotated_cache_dir)
    tarot_json = Path(__file__).resolve().parent.parent / "tarot.json"
    show_meaning = bool(config.behavior.show_card_meaning)
    platform = cmd._message.platform if cmd._message is not None else None

    try:
        _formations, all_cards = _core.load_tarot_data(tarot_json)
        result = _core.perform_onetime(resource_path=resource_path, all_cards=all_cards)
    except _core.TarotResourceError as exc:
        await send_text(f"占卜失败：{exc}", stream_id=cmd.stream_id)
        logger.warning(f"单张占卜资源错误: {exc}")
        return False, str(exc)

    card = result.cards[0]
    body = _core.format_card_body(card.info, card.is_upright, show_meaning)
    text = f"回应是 {body}"

    img_path = await _core.render_card_image(
        resource_path=resource_path,
        cache_dir=cache_dir,
        theme=result.theme,
        card_info=card.info,
        is_upright=card.is_upright,
    )

    await send_text(text, stream_id=cmd.stream_id, platform=platform)

    if img_path is None:
        await send_text(
            f"（{card.info.get('name_cn', '未知')} 的图片未找到，主题 {result.theme}）",
            stream_id=cmd.stream_id,
            platform=platform,
        )
        return True, "ok (image missing)"

    try:
        b64 = await asyncio.to_thread(_read_as_base64, img_path)
    except OSError as exc:
        logger.warning(f"读取塔罗牌图失败: {img_path} - {exc}")
        await send_text(
            f"（图片读取失败：{img_path.name}）",
            stream_id=cmd.stream_id,
            platform=platform,
        )
        return True, "ok (image io error)"

    await send_image(
        image_data=b64,
        stream_id=cmd.stream_id,
        platform=platform,
        processed_plain_text=f"[塔罗牌图: {card.info.get('name_cn', '')}]",
    )

    return True, "ok"
