"""tarot 插件配置。

提供资源路径、命令开关与 Tarot Agent 内部独立解释模型相关配置。
"""

from __future__ import annotations

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class TarotConfig(BaseConfig):
    """塔罗牌占卜插件配置。"""

    config_name: str = "config"
    config_description: str = "塔罗牌占卜插件配置"

    @config_section("resources")
    class ResourcesSection(SectionBase):
        """资源相关路径配置。"""

        resource_path: str = Field(
            default="data/tarot/resources",
            description=(
                "塔罗牌图片资源目录。相对路径相对于 Neo-MoFox 仓库根；"
                "也支持绝对路径。子目录结构应为 <theme>/<sub_type>/<pic>.png|jpg。"
            ),
        )
        rotated_cache_dir: str = Field(
            default="data/tarot/rotated_cache",
            description="逆位旋转图缓存目录（相对仓库根，与 resource_path 同结构）",
        )

    @config_section("behavior")
    class BehaviorSection(SectionBase):
        """发送与注册行为配置。"""

        enable_commands: bool = Field(
            default=True,
            description="是否注册 /占卜 与 /塔罗牌 命令。关闭后只保留 Tarot Agent 供主 Actor 调用。",
        )
        question_timeout_seconds: float = Field(
            default=300.0,
            description="问答模式等待用户回答的超时秒数，超时后 pending session 自动失效。",
        )
        interval_seconds: float = Field(
            default=2.0,
            description="命令模式下多张牌之间的发送间隔（秒），避免被平台限流。",
        )
        show_card_meaning: bool = Field(
            default=True,
            description=(
                "命令模式牌面文字是否包含 tarot.json 里的传统含义。"
                "关闭后只发牌名+正/逆位。Agent 返回始终包含完整牌义供主 Actor 参考。"
            ),
        )

    @config_section("agent")
    class AgentSection(SectionBase):
        """Tarot Agent 内部解释模型配置。"""

        task_name: str = Field(
            default="sub_actor",
            description=(
                "Tarot Agent 内部独立解释用的模型任务名，对应 config/model.toml 的 model_tasks。"
                "默认使用 sub_actor，避免直接复用主 Actor 请求链。"
            ),
        )
        max_interpretation_chars: int = Field(
            default=450,
            description="Tarot Agent 内部解释文本的建议上限字数。",
        )

    resources: ResourcesSection = Field(default_factory=ResourcesSection)
    behavior: BehaviorSection = Field(default_factory=BehaviorSection)
    agent: AgentSection = Field(default_factory=AgentSection)
