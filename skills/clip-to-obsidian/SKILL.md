---
name: clip-to-obsidian
description: '把 clip2obsidian 产出的 md 落进 Obsidian 知识库的剪藏区：解析 md 文首的来源块 → 现场读取库内权威规范 → 组装 Clippings 属性 → 写入 0_Inbox/Clippings/ → 调用 yzx-obsidian 做断链复核与库级操作。当用户说「入库」「剪藏入库」「把 md 存进知识库」「clip2obsidian 落库」「处理 work 下的 md」「这篇转写存进库里」时使用。工作空间为 clip2obsidian 项目，入库目标是用户的 Obsidian 知识库。'
agent_created: true
skill_path: "Mac: /Users/yangzixuan/个人项目/clip2obsidian/skills/clip-to-obsidian"
---

# clip-to-obsidian · 剪藏入库

把 `clip2obsidian` 产出的 md 变成知识库里的一篇剪藏。这是三层流水线的 **L3**：
L1 登记、L2 转换都由项目代码完成（代码不碰知识库），L3 由本 skill 完成。

## 第一原则：规则以库内权威源为准

知识库的完整规范只有一份：**`9_系统/协作约定.md`**（`AGENTS.md` 是其执行摘要）。

涉及**字段字典、标签词表、目录归属、模板、链接与附件规范、移动铁律**时，一律现场读
`9_系统/协作约定.md`，**不要依赖本 skill 或任何记忆里的转述**。历史教训：内嵌的
词表副本漂移后，会往库里写入已废弃的标签。

本 skill 只写「流程与边界」。

## 职责边界

| 谁 | 做什么 | 不做什么 |
|---|---|---|
| `clip2obsidian` 代码 | L1 登记、L2 转成 md（含来源块） | 不碰知识库任何路径 |
| **本 skill（L3）** | 解析来源块、组属性、**新增**剪藏、调用 yzx-obsidian | 不清理/改写既有剪藏；不批量搬附件 |
| `yzx-obsidian` skill | 库级操作：CLI 移动/重命名、属性读写、断链与孤页体检、日常维护 | 不改写剪藏内容 |

**为什么可以写 `0_Inbox/Clippings/`**：知识库 `AGENTS.md` 的规定是「Agent **不主动**
清理、归档、摘要、改写、移动或纳入待办；只有用户明确点名某篇剪藏或明确要求处理
Clippings 时才介入」。剪藏入库由用户主动发起，属于该例外。因此本 skill 的写入范围
**严格限定为「新增一篇剪藏」**，绝不顺手动库里的既有内容。

## 入库流程

### 1. 读产物

```bash
python clip.py status                       # 看有哪些条目、哪些已产出 md
```

读 `work/{id}.md`，解析文首的 `<!--c2o:meta ... -->` 来源块：

```
<!--c2o:meta
title: 认识 MAF
source_url: https://www.bilibili.com/video/BV1xx411c7mD
author: 某UP主
published: 2026-09-01
clipping_type: bilibili-video
platform: bilibili
native_id: BV1xx411c7mD
extractor: whisper:mlx:large-v3
-->
```

来源块里**没有的字段就不要编造**：宁可缺 `author`，也不要填一个猜的作者。
`clipping_type` 已由 L2 按平台与素材类型推导，可直接采用。

### 2. 现场读规范

读知识库 `9_系统/协作约定.md` 的**字段字典**与**标签词表**两节，取当前的：

- Clippings 的来源层字段名与格式（`title` / `source` / `source_url` / `author` /
  `published` / `created` / `clipping_type` / `description`）
- `类型` 的合法值、`tags` 的数量与词表约束、日期字段的书写要求（裸值 or 引号）

### 3. 组装属性并定名

- `类型: clippings`，来源层字段按上一步的口径逐项填写
- `tags`：从词表里选 1–3 个最能帮助检索的标签；**不确定就留空并说明原因**，
  不要凭感觉造标签
- 文件名：用 `title`，剥掉截断尾巴与营销腔；清洗掉文件名非法字符
- **删除 md 里的来源块**（`<!--c2o:meta ... -->`）：信息已进属性，留着是噪声

### 4. 写入并复核

写到 `0_Inbox/Clippings/{文件名}.md`，然后**调用 yzx-obsidian** 完成库级动作：

- `obsidian unresolved` 复核是否有新增断链
- 需要改属性用 `obsidian property:set`，需要移动用 `obsidian move`（自动更新 wikilink）
- 其它库级整理一律转交 yzx-obsidian，本 skill 不自行发挥

## 硬约束

1. **不编造**：来源块里没有的元信息不要补；不确定的标签留空并说明。
2. **只新增，不改造**：不移动、不改写、不删除库里既有的任何剪藏。
3. **图片交插件**：图文素材的图片在 `work/{id}/images/`。知识库规则明确
   「搬运 / 改名附件交给 CAL 插件或 Obsidian 界面，AI 不写脚本批量搬」——
   在回复里请用户用 Obsidian 处理，不要自己写脚本复制进 `8_附件/`。
4. **CLI 安全**：`obsidian` 的大部分命令**无参数即执行默认行为**（裸调 `create`
   会新建 `Untitled.md`，裸调 `delete` 会把当前活跃文件移入回收站）。
   永远显式传 `path=` / `file=`；查命令名用 `obsidian --help`。
5. **批量操作先确认**：一次入库 ≥3 篇，或涉及移动 / 重命名 / 修改 `9_系统/`，
   先列出计划征得用户同意。
6. **改前备份**：批量改动前备份到库内 `.workbuddy/backups/{日期}/`。

## 图文素材的额外一步

图文的 md 只有骨架（每张图一个 `<!--img: 绝对路径 -->` 占位）。入库前必须：

1. 按路径逐张读图，把图内文字与关键信息写进正文（替换占位说明）
2. 保留图片占位注释，交给用户在 Obsidian 里用插件收进附件目录
3. 正文补完后，再走上面的入库流程

细节与示例见 `references/clipping-ingest.md`。

## 命令参考（L1 / L2，均由本项目的 CLI 提供）

```bash
python clip.py <路径>                # 一步跑完 L1+L2
python clip.py scan                  # 收件目录里有什么
python clip.py status                # 已登记的条目与 md 状态
python clip.py convert <id> --force  # 重新转换某一条
python clip.py doctor                # 环境自检（含转写引擎与云端配置）
```
