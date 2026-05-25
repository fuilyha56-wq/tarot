"""tarot — 塔罗牌占卜插件入口。

提供两种调用方式：
1. 命令模式：/占卜 [关键词] 多牌阵、/塔罗牌 单张（可由配置关闭）
2. Agent 模式：主 Actor 判断用户意图后调用 TarotAgent，返回结构化结果

问答模式下，/占卜 无参会进入问答模式，由 TarotAnswerHandler 拦截用户回答后触发占卜。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from .agent import TarotAgent
from .commands.divine_command import DivineCommand
from .commands.handlers.answer_handler import TarotAnswerHandler
from .commands.onetime_command import OnetimeDivineCommand
from .config import TarotConfig
from .resources import ensure_tarot_resources

logger = get_logger("tarot")


@register_plugin
class TarotPlugin(BasePlugin):
    """塔罗牌占卜插件根组件。"""

    plugin_name: str = "tarot"
    plugin_description: str = "塔罗牌占卜插件：支持命令模式和 Agent 模式，多牌阵 + 单张占卜"
    plugin_version: str = "2.0.0"

    configs: list[type] = [TarotConfig]
    dependent_components: list[str] = []

    async def on_plugin_loaded(self) -> None:
        """插件加载时下载缺失的塔罗牌图片资源。"""
        if self.config is None:
            return
        if not self.config.resources.auto_download:
            return
        await ensure_tarot_resources(
            resource_path=self.config.resources.resource_path,
            base_url=self.config.resources.download_base_url,
        )

    def get_components(self) -> list[type]:
        """返回插件内所有组件类。

        TarotAgent 和 TarotAnswerHandler 始终注册；
        命令组件根据 behavior.enable_commands 配置决定是否注册。

        Returns:
            list[type]: 组件类列表
        """
        components: list[type] = [TarotAgent, TarotAnswerHandler]

        if self.config is not None and self.config.behavior.enable_commands:
            components.extend([DivineCommand, OnetimeDivineCommand])

        return components
