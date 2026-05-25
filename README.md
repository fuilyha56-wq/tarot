# tarot 插件移植规划（AstrBot → Neo-MoFox）

> 目标：把 [astrbot_plugin_tarot](https://github.com/XziXmn/astrbot_plugin_tarot) 的核心能力（多牌阵占卜、单张占卜）按照 Neo-MoFox 的插件规范重写为本地插件，并改造为"plugin 只发牌面、由 actor 人格自由解读"的形态。
>
> 本目录用于沉淀**移植设计**与最终交付物，最终插件会落到 `Neo-MoFox/plugins/tarot/`，资源数据落到 `Neo-MoFox/data/tarot/`。

---

## 1. 原插件能力对照

原插件（AstrBot v3.4.39）对外提供 4 个命令：

| 原命令 | 行为 | 关键依赖 |
| --- | --- | --- |
| `占卜 [关键词]` | 选主题 → 关键词模糊匹配 / LLM 匹配牌阵 → 抽 N 张牌 → 顺序发牌面图文 → 调 LLM 生成 200–300 字解析 | `tarot.json`、`resources/<theme>/<sub_type>/*`、Provider.text_chat |
| `塔罗牌 [关键词]` | 抽 1 张牌 → 发图 → LLM 解析 | 同上 |
| `开启/关闭群聊转发` | 切换 `Nodes` 合并转发 | AstrBot 合并转发 |
| `占卜 帮助` / `塔罗牌 帮助` | 帮助文本 | — |

核心数据：
- `tarot.json`：`formations`（17 个牌阵：`cards_num` / `is_cut` / `representations`），`cards`（78 张牌：`name_cn / name_en / type / meaning.up / meaning.down / pic`）。
- `resources/<Theme>/<SubType>/<pic>.{jpg,png}`：图片资源；逆位通过 PIL 旋转 180° 落盘缓存为 `<pic>_rotated.png`。

---

## 2. 移植后的形态变化（与原插件的关键差异）

这次移植不是 1:1 搬运，针对 Neo-MoFox 的"AI 伴侣"定位做了 3 处实质性改造：

### 2.1 plugin 不调 LLM，解析交给 actor

**原插件**：占卜命令完成后，自己调 LLM（用"塔罗师"system_prompt）生成 200–300 字解析，立刻发出。

**新形态**：plugin 只负责"发牌面图文"——图片 + 牌名 + 正/逆位 + `tarot.json` 写好的牌义 + 牌阵位置含义。**不**生成解析。

解析由谁来？由 Neo-MoFox 自己的 actor（即机器人本体人格）在用户后续提问时，凭聊天流上下文自然回应。
- plugin 发出去的牌面文字会进入聊天流上下文，actor 后续看到自然就知道刚才发了哪些牌。
- 用户问"这张愚者逆位什么意思"，actor 会用**自己的人格**回答，不再是冷冰冰的"塔罗师"口吻。
- 用户不问，就不解析；想追问就追问，自然交互。

**好处**：

| 维度 | 原 plugin 调 LLM | 新方案 |
| --- | --- | --- |
| 风格 | 与机器人本体人格脱节 | 完全经过 actor 滤镜，与平时聊天一致 |
| 时机 | 强制喂解析 | 用户想问才问，可追问 |
| LLM 调用 | plugin 多调 1–2 次 | plugin 0 次 LLM；actor 该几次几次 |
| 实现复杂度 | 需要 `_match_formation` + `_generate_ai_interpretation` | 全删 |

### 2.2 牌阵关键词匹配改纯本地

原插件命中失败会调 LLM 选牌阵。配合 §2.1，**plugin 完全不调 LLM**，所以匹配逻辑简化为：

1. **命名匹配**：用户输入完全等于某牌阵名（如"圣三角牌阵"）→ 命中。
2. **关键词模糊匹配**：用户输入含关键词（"情感/爱情/关系/事业/工作/未来/过去/现状/处境/挑战/建议"）且该词在某牌阵 `representations` 里出现 → 命中。
3. **都不命中**：随机选一个。

### 2.3 不保留群聊合并转发

Neo-MoFox 的 `MessageType` 没有 forward 类型，`send_api` 也未提供合并转发。统一**顺序发送**：每张牌一条文字 + 一张图，间隔 2 秒。

---

## 3. 组件选型

按 `AI插件编写规范.md` 第 3 节、第 10 节：

| 能力 | 组件类型 | 名称 | 触发方式 |
| --- | --- | --- | --- |
| 多牌阵占卜 | `Command` | `占卜` | 用户 `/占卜 [关键词]` |
| 单张占卜 | `Command` | `塔罗牌` | 用户 `/塔罗牌` |
| 配置 | `Config` | `config` | PluginManager 加载 |

**不**引入 Action / Tool / Agent / Chatter / Service：
- 行为是用户命令驱动，没有"让 LLM 自主调用塔罗"的需求。
- 完全不调 LLM，无需 Service 层。
- 未来若想"AI 在聊天中主动起卦"，再补一个 `BaseAction`，复用 `_core.py` 里的纯函数即可。

---

## 4. 目录结构

### 4.1 仓库内（代码 + 静态数据）

```text
Neo-MoFox/plugins/tarot/
├── manifest.json
├── plugin.py
├── config.py
├── tarot.json                      # 17 牌阵 + 78 张牌定义（从原仓库直接搬，只读）
└── commands/
    ├── __init__.py
    ├── _core.py                    # 抽牌、匹配、图片处理纯函数；可被命令/未来 action 共享
    ├── divine_command.py           # /占卜
    └── onetime_command.py          # /塔罗牌
```

### 4.2 数据目录（资源 + 运行时缓存）

```text
Neo-MoFox/data/tarot/
├── resources/                      # 用户手动从下载的完整资源 move 过来
│   ├── BilibiliTarot/              # 22 + 4×15 + 5（Extra）= 78 张完整韦特体系
│   │   ├── MajorArcana/            # 22 张大阿卡纳
│   │   ├── Cups/                   # 15 张圣杯（含 Ace 在 Extra）
│   │   ├── Pentacles/              # 15 张星币
│   │   ├── Swords/                 # 15 张宝剑
│   │   ├── Wands/                  # 15 张权杖
│   │   └── Extra/                  # 4 张 Ace + 背景；不参与占卜，仅收藏
│   └── TouhouTarot/
│       └── MajorArcana/            # 22 张东方主题大阿卡纳
└── rotated_cache/                  # 运行时按需生成的逆位图，与 resources/ 同结构
    ├── BilibiliTarot/
    └── TouhouTarot/
```

设计要点：
- **资源放 `data/tarot/resources/`**：完整资源 235MB，不进 plugins 目录（避免污染插件包/同步/备份）。
- **逆位缓存独立**：原插件写到 `resources/` 同目录，会让"只读资源"实际可变；这里改写到 `data/tarot/rotated_cache/`。
- **首次部署**：用户手动把 `Downloads/resource/*` 移动到 `Neo-MoFox/data/tarot/resources/`。README 有部署小节。
- `tarot.json` 留在插件包内（几十 KB 的元数据，跟代码绑定一起版本管理更合适）。

---

## 5. 资源现状（已核对）

完整资源在 `C:\Users\Elysia\Downloads\resource\`，总大小 235MB：

| 主题 | MajorArcana | Cups | Pentacles | Swords | Wands | Extra | 占卜可用张数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **BilibiliTarot** | 22 | 15 | 15 | 15 | 15 | 5 | 78（完整韦特） |
| **TouhouTarot** | 22 | — | — | — | — | — | 22（仅大阿卡纳） |

- `Extra` 目录只在 BilibiliTarot 下，含 4 张 Ace + 1 张背景图。原插件 `main.py:40` 写死 `all_sub_types = ["MajorArcana", "Cups", "Pentacles", "Swords", "Wands"]` 不包含 `Extra`，移植时保持。
- `tarot.json` 自带 78 张全套牌定义（22 大 + 56 小），完全对得上 BilibiliTarot 的图。
- 主题选择：每次占卜**随机**选一套主题（沿用原插件 `pick_theme()` 行为）。如果抽到 TouhouTarot 又恰好抽到小阿卡纳？看下面策略。

### 5.1 主题/张数不匹配的处理

TouhouTarot 只有 22 张大阿卡纳，但 `tarot.json` 含 78 张。原插件 `pick_sub_types(theme)` 已经处理了这个：

```python
# main.py:39-45
def pick_sub_types(self, theme: str) -> List[str]:
    sub_types = [f.name for f in (self.resource_path / theme).iterdir() if f.is_dir() and f.name in all_sub_types]
    return sub_types or all_sub_types
```

它会扫 `theme` 目录下实际存在的子类型，再用这个子集过滤 `cards`。所以 TouhouTarot 主题下只会抽到大阿卡纳的 22 张，不会出现"图片不存在"的问题。**移植时原样保留这段逻辑。**

---

## 6. 组件设计

### 6.1 配置类 `TarotConfig`（`BaseConfig`）

```python
config_name = "config"
config_description = "塔罗牌占卜插件配置"

@config_section("resources")
class ResourcesSection(SectionBase):
    resource_path: str = Field(
        default="data/tarot/resources",
        description="塔罗牌图片资源目录。相对路径相对于 Neo-MoFox 仓库根；也支持绝对路径。",
    )
    rotated_cache_dir: str = Field(
        default="data/tarot/rotated_cache",
        description="逆位旋转图缓存目录（相对仓库根）。",
    )

@config_section("behavior")
class BehaviorSection(SectionBase):
    interval_seconds: float = Field(
        default=2.0,
        description="多张牌之间的发送间隔（秒）",
    )
    show_card_meaning: bool = Field(
        default=True,
        description="是否在牌面文字中包含 tarot.json 的传统含义。关闭后只发牌名+正/逆位，actor 完全靠大模型常识发挥。",
    )
```

**移除的字段**（相对原 `_conf_schema.json`）：
- `chain_reply` / `include_ai_in_chain`：合并转发已砍。
- LLM 相关字段：plugin 不调 LLM。

### 6.2 `DivineCommand`（多牌阵占卜）

```python
command_name = "占卜"
command_description = "多牌阵塔罗占卜，发牌后由你自己解读"
permission_level = PermissionLevel.NORMAL
```

子路由：

| 路由 | 处理函数 | 行为 |
| --- | --- | --- |
| `@cmd_route()` | `handle_divine` | 入口，将剩余文本作为关键词 |
| `@cmd_route("帮助")` | `handle_help` | 帮助文本 |

`handle_divine` 主流程：

1. `_core.pick_theme(resource_path)` → 随机主题。
2. 加载 `tarot.json`，本地匹配牌阵：命名匹配 → 关键词模糊匹配 → 随机兜底。
3. `_core.random_cards(cards, theme, cards_num, sub_types)` 抽 N 张，每张 50% 概率定正逆位。
4. `await send_text(f"启用{formation_name}，正在洗牌中...")`。
5. 对每张牌（用 `asyncio.to_thread` 包 PIL 操作）：
   - 拼牌面文字：`第N张「位置」: 「牌名 正/逆位」「含义」` （`show_card_meaning=False` 时省略含义）。
   - 解析图片路径；逆位先查 `rotated_cache_dir/<theme>/<sub_type>/<pic>_rotated.png`，没有则用 PIL 旋转写入。
   - `send_text + send_image` 顺序发送，间隔 `interval_seconds`。
6. 返回 `(True, "ok")`。**不调 LLM，不发解析。**

### 6.3 `OnetimeDivineCommand`（单张占卜）

```python
command_name = "塔罗牌"
```

`@cmd_route()`：抽一张 → 发图文 → 完。`@cmd_route("帮助")`：帮助。

### 6.4 异步与并发

- 命令 handler 本身就是协程，按规范 §8.6，**不需要** task_manager。
- PIL 旋转 + 文件 IO 用 `asyncio.to_thread(...)` 包一层，避免阻塞事件循环（原插件是同步阻塞的，顺手改进）。

### 6.5 `_core.py` 提供的纯函数

```python
def pick_theme(resource_path: Path) -> str: ...
def pick_sub_types(resource_path: Path, theme: str) -> list[str]: ...
def random_cards(all_cards: dict, theme: str, sub_types: list[str], num: int = 1) -> list[dict]: ...
def match_formation_local(text: str, all_formations: dict) -> str: ...  # 纯本地匹配
async def render_card_image(theme: str, card: dict, is_upright: bool, resource_path: Path, cache_dir: Path) -> Path: ...
def format_card_text(card: dict, is_upright: bool, position: str | None, show_meaning: bool) -> str: ...
```

抽到这一层是为了：(1) 命令组件代码薄；(2) 未来 `BaseAction`（"AI 主动起卦"）可以直接复用；(3) 写单元测试不需要拉起消息系统。

---

## 7. manifest.json

```json
{
  "name": "tarot",
  "display_name": "塔罗牌占卜",
  "version": "1.0.0",
  "description": "本地塔罗牌占卜插件。支持 17 牌阵 + 78 张韦特/东方主题，发牌后由 actor 自由解读",
  "author": "MoFox Team (porting from XziXmn/astrbot_plugin_tarot)",
  "license": "GPL-3.0",
  "dependencies": {
    "plugins": [],
    "components": []
  },
  "include": [
    { "component_type": "command", "component_name": "占卜", "dependencies": [] },
    { "component_type": "command", "component_name": "塔罗牌", "dependencies": [] }
  ],
  "entry_point": "plugin.py",
  "min_core_version": "1.0.0",
  "python_dependencies": ["pillow>=9.2.0"]
}
```

`min_core_version` 显式写死，遵循规范 §5.1.3。

---

## 8. 命令矩阵

| 触发 | 行为 |
| --- | --- |
| `/占卜` | 随机牌阵抽牌 |
| `/占卜 情感` | 关键词匹配（命中 `representations` 中含"情感/爱情/关系"等的牌阵） |
| `/占卜 圣三角牌阵` | 命名匹配 |
| `/占卜 帮助` | 帮助 |
| `/塔罗牌` | 单张抽牌 |
| `/塔罗牌 帮助` | 帮助 |

权限：`PermissionLevel.NORMAL`（任何人可用）。

> 注：之前讨论过 `tarot` 英文别名，因为 plugin 不调 LLM、行为简单，**先不做**。后续如果要补，给两个命令的 `match()` 各加一个分支即可（参考 `utility_commands/commands/clear_command.py:50`）。

---

## 9. 已确认的设计决策

| # | 决策 | 落地 |
| --- | --- | --- |
| 1 | 不保留群聊合并转发 | 顺序发送 `send_text + send_image` |
| 2 | 资源放 `Neo-MoFox/data/tarot/resources/`，用户手动 move 235MB 完整资源 | `config.resources.resource_path = "data/tarot/resources"` |
| 3 | plugin 不调 LLM，解析交 actor | 不引入 LLM API；删除 `_match_formation` 和 `_generate_ai_interpretation` |
| 4 | 牌阵匹配走纯本地（命名 + 关键词 + 随机） | `_core.match_formation_local` |
| 5 | 主题随机选（沿用原插件） | `_core.pick_theme()` |
| 6 | `Extra` 目录不参与占卜 | 沿用 `all_sub_types = ["MajorArcana","Cups","Pentacles","Swords","Wands"]` |
| 7 | 牌面文字默认带 `tarot.json` 含义 | `config.behavior.show_card_meaning = True` |

---

## 10. 用户首次部署步骤（写到最终发布的 README）

1. 把代码目录放到 `Neo-MoFox/plugins/tarot/`。
2. 把完整资源 move 到 `Neo-MoFox/data/tarot/resources/`，最终结构应该是：
   ```
   data/tarot/resources/BilibiliTarot/{MajorArcana,Cups,Pentacles,Swords,Wands,Extra}/
   data/tarot/resources/TouhouTarot/MajorArcana/
   ```
3. 启动 Neo-MoFox。如果资源目录为空或缺失，命令会返回友好错误（不影响其它插件加载）。
4. `/占卜` 或 `/塔罗牌` 试一下。

---

## 11. 风险与注意点

- **资源缺失不让 plugin 崩**：原插件在 `__init__` 里 `raise`，会让 PluginManager 整个失败。我们改成"加载时只 warn，命令执行时再返回友好错误"。
- **PIL 旋转写入只读资源是隐式副作用**：原插件直写 `resources/`。这里改写到独立 `rotated_cache_dir/`。
- **图片扩展名匹配**：原插件 `img_dir.glob(_name + ".*")` 扫所有扩展名；BilibiliTarot 是 `.png`、TouhouTarot 是 `.jpg`，照搬该逻辑即可。
- **`event.stop_event()` 没有等价物**：Neo-MoFox 命令通过 `BaseCommand` 返回值处理，删掉即可。
- **关键词列表写死**：`["情感","爱情","关系","事业","工作","未来","过去","现状","处境","挑战","建议"]`，与原插件保持一致；后续如有需求再做成配置项。
- **版权**：BilibiliTarot 来自 B 站幻星集、TouhouTarot 来自东方 Project 同人圈，原插件未注明再分发授权。如 bot 公开运营，建议在最终 README 加一句"图片资源仅供个人学习，版权归原作者所有"。

---

## 12. 实施步骤

1. 读 `src/app/plugin_system/types/PermissionLevel`、`src/core/components/base/command.py`，确认 `BaseCommand` / `cmd_route` / `match()` 的实际签名。
2. 写 `manifest.json` + `plugin.py` + `config.py` 骨架，跑通"插件能加载、命令能注册"。
3. 写 `commands/_core.py` 纯函数，配最小单测（抽牌、匹配、图片路径解析）。
4. 写 `commands/divine_command.py`、`commands/onetime_command.py`，跑通抽牌 + 图文发送。
5. 把 `tarot.json` 复制进插件目录。
6. 让用户手动 move 资源到 `data/tarot/resources/`，端到端测一次 `/占卜 情感` 和 `/塔罗牌`。

---

## 附：源码事实参考（已核对）

- `BaseCommand` + `@cmd_route` 用法：`Neo-MoFox/plugins/utility_commands/commands/clear_command.py`
- `BasePlugin` + `@register_plugin` 用法：`Neo-MoFox/plugins/utility_commands/plugin.py`
- `send_text` / `send_image`：`src/app/plugin_system/api/send_api.py`
- 配置基类：`src/app/plugin_system/base/__init__.py` → `BaseConfig` / `Field` / `SectionBase` / `config_section`
- `MessageType`：`src/core/models/message.py`（不含 forward）
- 现有 `model_tasks`：`config/model.toml`（`utils` / `utils_small` / `actor` / `sub_actor` / `vlm` / `voice` / `video` / `tool_use` / `embedding`）—— 本插件用不到，列在这里以备未来引入 LLM 调用时参考
