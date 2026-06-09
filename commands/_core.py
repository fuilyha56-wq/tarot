"""塔罗插件核心纯函数。

把抽牌、主题选择、关键词匹配、图片渲染等业务逻辑独立成纯函数模块，
让命令组件保持薄；同时为未来可能的 ``BaseAction``（"AI 主动起卦"）
预留可复用的入口。

本模块刻意**不依赖** Neo-MoFox 的消息系统，只依赖标准库与 PIL。
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import PIL.Image

# 与原插件 main.py:40 保持一致：仅这五个子类型参与占卜
ALL_SUB_TYPES: tuple[str, ...] = (
    "MajorArcana",
    "Cups",
    "Pentacles",
    "Swords",
    "Wands",
)

# 与原插件 main.py:105 保持一致的关键词集合
FORMATION_KEYWORDS: tuple[str, ...] = (
    "情感",
    "爱情",
    "关系",
    "事业",
    "工作",
    "未来",
    "过去",
    "现状",
    "处境",
    "挑战",
    "建议",
)


@dataclass
class DrawnCard:
    """单次抽牌结果。

    Attributes:
        card_id: tarot.json 里 cards 的键（"0"/"1"/...）
        info: 整张牌的元数据字典（含 name_cn/name_en/type/meaning/pic）
        is_upright: 是否正位（True=正位，False=逆位）
        position_label: 该牌在牌阵中的位置含义（单张占卜时为 "当前情况"）
    """

    card_id: str
    info: dict[str, Any]
    is_upright: bool
    position_label: str


@dataclass
class DivineResult:
    """一次占卜的完整结果。

    Attributes:
        theme: 选中的主题（如 "BilibiliTarot"）
        formation_name: 牌阵名称（单张占卜时为 "单张牌占卜"）
        is_cut: 是否包含切牌；最后一张为切牌
        cards: 抽到的牌列表，长度等于牌阵 cards_num
    """

    theme: str
    formation_name: str
    is_cut: bool
    cards: list[DrawnCard]


class TarotResourceError(Exception):
    """资源相关错误（目录缺失、主题为空、张数不足等）。"""


# =============================================================================
# 数据加载
# =============================================================================


def load_tarot_data(tarot_json_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """加载 tarot.json 中的牌阵和卡片数据。

    Args:
        tarot_json_path: tarot.json 文件路径

    Returns:
        (formations, cards) 两个字典

    Raises:
        TarotResourceError: 文件不存在或结构异常
    """
    if not tarot_json_path.exists():
        raise TarotResourceError(f"tarot.json 文件缺失: {tarot_json_path}")
    try:
        with tarot_json_path.open("r", encoding="utf-8") as f:
            content = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise TarotResourceError(f"tarot.json 解析失败: {exc}") from exc

    formations = content.get("formations") or {}
    cards = content.get("cards") or {}
    if not formations or not cards:
        raise TarotResourceError("tarot.json 缺少 formations 或 cards 字段")
    return formations, cards


# =============================================================================
# 主题与子类型
# =============================================================================


def pick_theme(resource_path: Path) -> str:
    """随机选一个主题（资源目录下的子文件夹名）。

    Args:
        resource_path: 资源根目录（包含 BilibiliTarot/ 等主题子目录）

    Returns:
        主题名

    Raises:
        TarotResourceError: 资源目录不存在或没有任何主题子目录
    """
    if not resource_path.exists() or not resource_path.is_dir():
        raise TarotResourceError(
            f"资源目录不存在: {resource_path}（请把图片资源放到此目录或调整 config.resources.resource_path）"
        )
    themes = [f.name for f in resource_path.iterdir() if f.is_dir()]
    if not themes:
        raise TarotResourceError(f"资源目录 {resource_path} 下没有任何主题子目录")
    return random.choice(themes)


def pick_sub_types(resource_path: Path, theme: str) -> list[str]:
    """列出主题下实际存在的子类型，按 ALL_SUB_TYPES 过滤。

    Args:
        resource_path: 资源根目录
        theme: 主题名

    Returns:
        实际存在的子类型列表；若全空则返回 ALL_SUB_TYPES 全集（兜底）
    """
    theme_dir = resource_path / theme
    if not theme_dir.exists():
        return list(ALL_SUB_TYPES)
    sub_types = [
        f.name
        for f in theme_dir.iterdir()
        if f.is_dir() and f.name in ALL_SUB_TYPES
    ]
    return sub_types or list(ALL_SUB_TYPES)


# =============================================================================
# 抽牌
# =============================================================================


def random_cards(
    all_cards: dict[str, Any],
    sub_types: list[str],
    num: int,
) -> list[tuple[str, dict[str, Any]]]:
    """从指定子类型范围内不放回抽 num 张牌。

    Args:
        all_cards: tarot.json 的 cards 字典
        sub_types: 允许抽取的子类型列表
        num: 抽取张数

    Returns:
        [(card_id, card_info), ...] 长度为 num 的列表

    Raises:
        TarotResourceError: 候选张数不足
    """
    subset = {k: v for k, v in all_cards.items() if v.get("type") in sub_types}
    if len(subset) < num:
        raise TarotResourceError(
            f"候选牌数量不足，需要 {num} 张，实际 {len(subset)} 张（子类型: {sub_types}）"
        )
    chosen_ids = random.sample(list(subset.keys()), num)
    return [(cid, subset[cid]) for cid in chosen_ids]


def roll_upright() -> bool:
    """以 50% 概率决定正位/逆位。

    Returns:
        True 表示正位，False 表示逆位
    """
    return random.random() < 0.5


# =============================================================================
# 牌阵匹配（纯本地，无 LLM）
# =============================================================================


def match_formation_local(
    user_input: str,
    all_formations: dict[str, Any],
) -> str:
    """根据用户输入选牌阵：命名匹配 → 关键词模糊匹配 → 随机兜底。

    Args:
        user_input: 用户在命令后输入的文本
        all_formations: tarot.json 的 formations 字典

    Returns:
        匹配到的牌阵名称
    """
    text = (user_input or "").strip().lower()
    formation_names = list(all_formations.keys())

    # 1. 命名精确匹配（完全包含牌阵名）
    for name in formation_names:
        if name and name in user_input:
            return name

    # 2. 关键词模糊匹配
    if text:
        for name in formation_names:
            reps = all_formations[name].get("representations") or []
            if not reps:
                continue
            # representations 是 list[list[str]]，原插件只看第一组
            rep_text = " ".join(reps[0]).lower()
            for keyword in FORMATION_KEYWORDS:
                if keyword in text and keyword in rep_text:
                    return name

    # 3. 随机兜底
    return random.choice(formation_names)


# =============================================================================
# 图片渲染
# =============================================================================


def find_card_image(resource_path: Path, theme: str, card_info: dict[str, Any]) -> Path | None:
    """在资源目录里查找一张牌的图片（不区分扩展名）。

    Args:
        resource_path: 资源根目录
        theme: 主题名
        card_info: 单张牌的元数据（含 type 和 pic）

    Returns:
        命中的图片路径，找不到时返回 None
    """
    sub_type = card_info.get("type") or ""
    pic = card_info.get("pic") or ""
    if not sub_type or not pic:
        return None
    img_dir = resource_path / theme / sub_type
    if not img_dir.exists():
        return None
    # 与原插件一致：扫所有扩展名
    for p in img_dir.glob(pic + ".*"):
        if p.is_file():
            return p
    return None


def _do_rotate(src_path: Path, dst_path: Path) -> None:
    """同步执行 PIL 180° 旋转，并写入目标路径。"""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with PIL.Image.open(src_path) as img:
        rotated = img.rotate(180)
        rotated.save(dst_path, format="png")


async def render_card_image(
    resource_path: Path,
    cache_dir: Path,
    theme: str,
    card_info: dict[str, Any],
    is_upright: bool,
) -> Path | None:
    """获取一张牌应当发送的最终图片路径。

    正位返回原图路径；逆位优先复用 cache_dir 下的旋转缓存，没有则用
    PIL 旋转后写入缓存（用 ``asyncio.to_thread`` 避免阻塞事件循环）。

    Args:
        resource_path: 资源根目录
        cache_dir: 逆位旋转图缓存根目录（与 resource_path 同结构）
        theme: 主题名
        card_info: 单张牌元数据
        is_upright: 是否正位

    Returns:
        最终图片路径，原图缺失时返回 None
    """
    src = find_card_image(resource_path, theme, card_info)
    if src is None:
        return None
    if is_upright:
        return src

    sub_type = card_info.get("type") or ""
    pic = card_info.get("pic") or ""
    rotated_path = cache_dir / theme / sub_type / f"{pic}_rotated.png"

    if rotated_path.exists():
        return rotated_path

    await asyncio.to_thread(_do_rotate, src, rotated_path)
    return rotated_path


# =============================================================================
# 牌面文本格式化
# =============================================================================


def format_card_header(
    index: int,
    total: int,
    is_cut: bool,
    position_label: str,
) -> str:
    """生成牌面前缀（"第N张牌「位置」" 或 "切牌「位置」"）。

    Args:
        index: 0-based 序号
        total: 总张数
        is_cut: 牌阵是否包含切牌
        position_label: 位置含义文本

    Returns:
        前缀字符串（不带换行）
    """
    if is_cut and index == total - 1:
        return f"切牌「{position_label}」"
    return f"第{index + 1}张牌「{position_label}」"


def format_card_body(
    card_info: dict[str, Any],
    is_upright: bool,
    show_meaning: bool,
) -> str:
    """生成牌面正文（牌名 正/逆位 + 可选含义）。

    Args:
        card_info: 单张牌元数据
        is_upright: 是否正位
        show_meaning: 是否带 tarot.json 的传统含义

    Returns:
        正文字符串
    """
    name_cn = card_info.get("name_cn") or "未知"
    pos_text = "正位" if is_upright else "逆位"
    if not show_meaning:
        return f"「{name_cn} {pos_text}」"
    meaning_dict = card_info.get("meaning") or {}
    meaning = meaning_dict.get("up" if is_upright else "down") or ""
    if meaning:
        return f"「{name_cn} {pos_text}」「{meaning}」"
    return f"「{name_cn} {pos_text}」"


# =============================================================================
# 一次完整占卜的高层组合
# =============================================================================


def perform_divination(
    resource_path: Path,
    formations: dict[str, Any],
    all_cards: dict[str, Any],
    user_input: str,
) -> DivineResult:
    """执行一次多牌阵占卜的纯逻辑部分（不发图，不调 LLM）。

    Args:
        resource_path: 资源根目录
        formations: 牌阵字典
        all_cards: 卡片字典
        user_input: 用户输入

    Returns:
        DivineResult；调用方据此决定怎么发牌

    Raises:
        TarotResourceError: 资源问题（透传 random_cards / pick_theme 的异常）
    """
    theme = pick_theme(resource_path)
    formation_name = match_formation_local(user_input, formations)
    formation = formations.get(formation_name) or {}
    cards_num: int = int(formation.get("cards_num") or 1)
    is_cut: bool = bool(formation.get("is_cut") or False)
    rep_groups = formation.get("representations") or [[""] * cards_num]
    representations: list[str] = list(random.choice(rep_groups))

    sub_types = pick_sub_types(resource_path, theme)
    drawn = random_cards(all_cards, sub_types, cards_num)

    cards: list[DrawnCard] = []
    for idx, (cid, info) in enumerate(drawn):
        label = representations[idx] if idx < len(representations) else ""
        cards.append(
            DrawnCard(
                card_id=cid,
                info=info,
                is_upright=roll_upright(),
                position_label=label,
            )
        )

    return DivineResult(
        theme=theme,
        formation_name=formation_name,
        is_cut=is_cut,
        cards=cards,
    )


def perform_onetime(
    resource_path: Path,
    all_cards: dict[str, Any],
) -> DivineResult:
    """执行一次单张占卜的纯逻辑部分。

    Args:
        resource_path: 资源根目录
        all_cards: 卡片字典

    Returns:
        DivineResult，cards 长度恒为 1，formation_name 固定为 "单张牌占卜"
    """
    theme = pick_theme(resource_path)
    sub_types = pick_sub_types(resource_path, theme)
    drawn = random_cards(all_cards, sub_types, 1)
    cid, info = drawn[0]
    return DivineResult(
        theme=theme,
        formation_name="单张牌占卜",
        is_cut=False,
        cards=[
            DrawnCard(
                card_id=cid,
                info=info,
                is_upright=roll_upright(),
                position_label="当前情况",
            )
        ],
    )
