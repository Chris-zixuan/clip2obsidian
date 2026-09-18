---
name: clip-to-obsidian
description: 把本地视频导入为 source.json（L1）、提取成 clip.json（L2）、
  由 agent 写摘要（L3）、渲染成符合知识库规范的 Obsidian 笔记（L4）。
  当用户提到「收藏入库」「剪藏」「视频转笔记」「clip.json」「publish 到知识库」
  等关键词时使用；也可只跑 L1/L2，之后再接着做 L3/L4。
---

# clip-to-obsidian · 把本地文件变成知识库里的笔记

## 四层与职责

| 层 | 命令 | 谁做 | 产物 |
|---|---|---|---|
| **L1** 导入 | `clip.py ingest <路径>` | 代码 | `raw/{id}/source.json` |
| **L2** 提取 | `clip.py extract <clip_id>` | 代码 | `work/{id}.clip.json` |
| **L3** 摘要 | — | **你（agent）** | `work/{id}.digest.md` |
| **L4** 入库 | `clip.py publish <clip_id> --tags ...` | 代码渲染 + 你定标签/标题 | 笔记 |

代码负责机械劳动（抽音频、转写、模板、附件、校验）；
**判断类工作全部由你完成**：写摘要、精简标题、选知识库标签。

## 编排流程

### 1. L1 + L2（代码）

```bash
python clip.py "<本地文件路径>"            # 一步跑完 L1+L2
python clip.py scan                        # 先看收件目录里有什么
```

`--url <链接>` 可选：只做轻量元信息增强（`yt-dlp --skip-download`），
**不下载媒体**，失败只记 warning 不阻断。

识别不出平台时，文件名需带平台特征（B站：`哔哩哔哩` / `bilibili` / BV 号）。

### 2. L3 写摘要（你）

读 `work/{id}.clip.json` 的 `content.transcript`，写 `work/{id}.digest.md`：

```markdown
## 要点

- 要点一
- 要点二

## 我的思考

- 记录「我为什么收这个」和可能怎么用
```

摘要是**能脱离原文独立阅读**的产物，不是原文复读机，也不是索引占位符。

### 3. L4 入库（代码 + 你）

```bash
python clip.py publish bilibili:BV1xx411c7mD \
    --tags 生活,成长 \
    --title "精简后的标题" \
    --digest work/bilibili_BV1xx411c7mD.digest.md
```

- `--tags`：受控词表标签，至少 1 个，**不带** `clippings`
- `--title`：剥掉原文标题的截断尾巴与营销腔，用你自己的话概括
- 想先看会写出什么：`--dry-run`

## 硬约束

1. **绝不编造**：`clip.json` 里没有的元信息（作者、日期、链接）不要在笔记里补。
   缺了就是缺了，留空由用户在 publish 时补。
2. **标签只用受控词表**：不知道该用什么标签就问用户，不要凭感觉造。
3. **转写有同音字错误**：引用原文要能看出是转写；专有名词以视频画面/常识为准时，
   在摘要里说明。
4. **覆盖只读不删**：代码会先把同名笔记备份到 `.workbuddy/backups/`，不直接删。

## 常见报错

| 报错 | 含义 | 处理 |
|---|---|---|
| `无法从输入识别平台` | 文件名无平台特征 | 改名加特征，或确认平台是否已在 `registry.py` 注册 |
| `找不到视频文件` | 路径不存在 | 检查路径；L1 只记录绝对路径、不复制文件 |
| `转写结果为空` | 纯音乐或静音 | 可接受，L3 基于元信息写摘要 |
| `没有指定知识库标签` | 缺 `--tags` | 补受控标签 |
| `clip.json 结构校验未通过` | 产物不完整 | 按提示修，或 `--force` 重跑 L2 |

## 命令参考

```
python clip.py [<路径>...]            # 默认 run = ingest + extract
python clip.py ingest <路径> [--url U] [--force]
python clip.py extract <clip_id|source.json> [--force]
python clip.py publish <clip_id> --tags T [--title T] [--digest P] [--dry-run]
python clip.py status                 # 列出已有 clip
python clip.py scan                   # 收件目录里有什么
python clip.py doctor                 # 环境自检
```

## 环境

- 依赖装在独立 venv 里，`config.toml` 的 `[tools].python` 必须指向它，
  否则子步骤（转写）会用错解释器。
- 知识库标签词表见知识库内的受控词表笔记（L4 的标签必须来自它）。
