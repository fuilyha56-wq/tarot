"""Tarot Agent：由主 Actor 调用的塔罗牌占卜代理。

流程：
1. 主 Actor 判断用户意图需要占卜 → 调用本 Agent
2. Agent 随机抽牌（支持单张 / 多牌阵）
3. Agent 用插件独立 tarot actor 模型生成牌面解读
4. Agent 返回结构化结果（牌面图路径 + 牌义 + 解读文本）给主 Actor
5. 主 Actor 用自己人格整合后输出给用户

返回格式为 dict，包含：
- "formation": 牌阵名
- "cards": 每张牌的详情列表（name_cn / upright / meaning / image_path）
- "interpretation": tarot actor 生成的解读文本
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Annotated, Any

from src.app.plugin_system.api import llm_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image
from src.app.plugin_system.base import BaseAgent
from src.kernel.llm import LLMPayload, ROLE, Text

from .commands import _core

logger = get_logger("tarot.agent")


def _resolve_path(raw: str) -> Path:
    """把配置里的相对/绝对路径解析为绝对 Path。"""
    p = Path(raw)
    return p if p.is_absolute() else Path.cwd() / p


def _read_as_base64(path: Path) -> str:
    """同步读取文件并转 base64。"""
    return base64.b64encode(path.read_bytes()).decode("ascii")


async def _resolve_text(response) -> str:  # noqa: ANN001
    """提取 LLM 响应文本，回退到 reasoning_content。"""
    raw = await response
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    reasoning = getattr(response, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    return raw.strip() if isinstance(raw, str) else ""


def _build_interpretation_prompt(
    formation_name: str,
    cards: list[_core.DrawnCard],
    user_input: str,
    max_chars: int,
) -> tuple[str, str]:
    """构造 tarot actor 的 system_prompt 和 user_prompt。"""
    system_prompt = (
        "你是一位专业的塔罗牌占卜师，擅长提供深入且简洁的解析。"
        "请根据牌阵和每张牌的位置，提供一个连贯的解析，"
        "解释这些牌可能对用户的生活、情感或决策的启示。"
        "回答需简洁但有深度，善用换行与颜表情进行美化。"
    )

    prompt = f"用户占卜指令：'{user_input}'\n\n牌阵：{formation_name}\n抽到的牌及位置：\n"
    for i, card in enumerate(cards):
        pos = "正位" if card.is_upright else "逆位"
        meaning = card.info.get("meaning", {})
        meaning_text = meaning.get("up" if card.is_upright else "down", "")
        prompt += f"第{i+1}张「{card.position_label}」: 「{card.info.get('name_cn', '')} {pos}」「{meaning_text}」\n"
    prompt += f"\n请结合用户指令，提供约 {max_chars} 字以内的解析。"

    return system_prompt, prompt


async def _generate_interpretation(
    formation_name: str,
    cards: list[_core.DrawnCard],
    user_input: str,
    task_name: str,
    max_chars: int,
) -> str:
    """调用插件独立 tarot actor 模型生成解读。失败时返回兜底文本。"""
    system_prompt, user_prompt = _build_interpretation_prompt(
        formation_name, cards, user_input, max_chars,
    )

    try:
        model_set = llm_api.get_model_set_by_task(task_name)
    except Exception as exc:
        logger.warning(f"找不到模型任务 '{task_name}': {exc}")
        return "（塔罗解读生成失败：模型任务未配置）"

    try:
        request = llm_api.create_llm_request(
            model_set=model_set,
            request_name="tarot.interpret",
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))
        response = await request.send(stream=False)
        text = await _resolve_text(response)
    except Exception as exc:
        logger.warning(f"tarot actor 解读失败: {exc}")
        return "（塔罗解读生成失败，请稍后再试）"

    return text.strip() if text else "（塔罗解读为空）"


class TarotAgent(BaseAgent):
    """塔罗牌占卜 Agent：由主 Actor 调用，返回抽牌结果与解读。"""

    agent_name: str = "tarot_divine"
    associated_types: list[str] = ["text", "image"]
    agent_description: str = (
        "塔罗牌占卜。当用户想要占卜、算命、抽塔罗牌时调用。"
        "调用前你应该先问用户想占卜什么方面（感情/事业/学业等），"
        "得到回答后再调用本 Agent，将用户的具体问题作为 user_input 传入。"
        "本 Agent 会自动发送牌面图片，你只需根据返回的牌义和解读文本，"
        "用自己的人格风格整合后回复用户即可，无需再发图片。"
    )

    async def execute(
        self,
        user_input: Annotated[str, "用户的占卜请求，如 '我想占卜一下感情' 或 '抽一张牌'"],
        mode: Annotated[str, "占卜模式：'single' 单张 / 'formation' 多牌阵（默认）"] = "formation",
    ) -> tuple[bool, str | dict]:
        """执行塔罗牌占卜。

        Args:
            user_input: 用户的占卜请求文本
            mode: 占卜模式，single=单张，formation=多牌阵

        Returns:
            (是否成功, 结果dict或错误文本)
            dict 结构：{"formation": str, "cards": [...], "interpretation": str}
            每张 card 含：name_cn, name_en, upright, position, meaning
            图片由 Agent 直接发送，不在返回值中。
        """
        config = self.plugin.config
        if config is None:
            return False, "塔罗插件配置缺失"

        resource_path = _resolve_path(config.resources.resource_path)
        cache_dir = _resolve_path(config.resources.rotated_cache_dir)
        tarot_json = Path(__file__).resolve().parent / "tarot.json"
        task_name = str(config.agent.task_name)
        max_chars = int(config.agent.max_interpretation_chars)

        try:
            formations, all_cards = _core.load_tarot_data(tarot_json)
            if mode.strip().lower() == "single":
                result = _core.perform_onetime(resource_path=resource_path, all_cards=all_cards)
            else:
                result = _core.perform_divination(
                    resource_path=resource_path,
                    formations=formations,
                    all_cards=all_cards,
                    user_input=user_input,
                )
        except _core.TarotResourceError as exc:
            logger.warning(f"Tarot Agent 资源错误: {exc}")
            return False, f"占卜失败：{exc}"

        # 生成解读
        interpretation = await _generate_interpretation(
            formation_name=result.formation_name,
            cards=result.cards,
            user_input=user_input,
            task_name=task_name,
            max_chars=max_chars,
        )

        # 组装返回
        cards_data: list[dict[str, Any]] = []
        for card in result.cards:
            pos = "正位" if card.is_upright else "逆位"
            meaning = card.info.get("meaning", {})
            meaning_text = meaning.get("up" if card.is_upright else "down", "")

            # 获取图片并直接发送
            try:
                img_path = await _core.render_card_image(
                    resource_path=resource_path,
                    cache_dir=cache_dir,
                    theme=result.theme,
                    card_info=card.info,
                    is_upright=card.is_upright,
                )
                if img_path is not None:
                    img_b64 = await asyncio.to_thread(_read_as_base64, img_path)
                    await send_image(
                        image_data=img_b64,
                        stream_id=self.stream_id,
                        processed_plain_text=f"[塔罗牌图: {card.info.get('name_cn', '')} {pos}]",
                    )
            except Exception as exc:
                logger.debug(f"发送牌面图失败: {exc}")

            cards_data.append({
                "name_cn": card.info.get("name_cn", ""),
                "name_en": card.info.get("name_en", ""),
                "upright": card.is_upright,
                "position": card.position_label,
                "pos_text": pos,
                "meaning": meaning_text,
            })

        return True, {
            "formation": result.formation_name,
            "theme": result.theme,
            "is_cut": result.is_cut,
            "cards": cards_data,
            "interpretation": interpretation,
        }
