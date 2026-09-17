# clip2obsidian

把日常收藏的**链接**（视频 / 图文）蒸馏成符合个人知识库规范的 **Obsidian 笔记**。

> 输入一条链接，输出一篇可入库的笔记。中间分为「采集 / 提取 / 蒸馏 / 入库」四层，
> 每层产物落盘，可单独检查、单独重跑、单独替换。

---

## 为什么自己写

生态里做「链接 → 结构化笔记」的开源项目不少，但**没有一个能落成符合私人知识库
frontmatter / 标签 / 附件规范的笔记**——那一公里只能自己走。

所以本项目的设计是：**前 90% 借现成工具，最后 10% 自己写**。

| 层 | 借了什么 |
|---|---|
| L1 采集 | `yt-dlp`（视频下载 + 字幕） |
| L2 提取 | `faster-whisper` / `mlx-whisper`（语音转文字） |
| L3 蒸馏 | agent（LLM 摘要 + 标签判断） |
| L4 入库 | **自己写**（知识库规范渲染） |

---

## 架构

```
 输入链接
    │
    ▼
┌────────────────────────────────────────────────────┐
│ L1  fetch     链接    → raw/{id}/source.json + 媒体 │  代码
│ L2  extract   物料    → work/{id}.clip.json        │  代码
│ L3  distill   clip    → work/{id}.digest.md        │  agent
│ L4  publish   clip    → vault 笔记 + 附件           │  代码
└────────────────────────────────────────────────────┘
```

**分层原则**：`clip.json` 是四层之间**唯一**的通信契约。所有平台提取器都产出它，
下游只认它。新增平台时，只要能填满这份结构，蒸馏与入库零改动。

职责划分：

- **代码做机械劳动** —— 抓取、转写、模板渲染、附件落位、断链校验。
  可重跑、幂等、有缓存，出了问题能定位到层。
- **agent 做需要判断的事** —— 摘要蒸馏、标题精简、知识库标签选择。
  收敛在 L3 一个点上，通过文件与参数传递，不污染其余环节。

---

## 安装

```bash
git clone git@github.com:Chris-zixuan/clip2obsidian.git
cd clip2obsidian
cp config.example.toml config.toml    # 然后按注释改路径
python3 clip.py doctor                # 环境自检
```

依赖：

```bash
# 必需
brew install ffmpeg
pip install yt-dlp

# 转写（二选一）
pip install faster-whisper     # 通用，CPU 可跑
pip install mlx-whisper        # Apple Silicon，快数倍

# 中文处理
pip install zhconv
```

> `config.toml` 里的 `[tools].python` 必须指向**装好这些依赖的那个解释器**。
> 依赖装在独立 venv 里时尤其注意，否则会报 `ModuleNotFoundError: yt_dlp`。

---

## 用法

```bash
# L1 + L2：抓取并提取出 clip.json
python clip.py "https://v.douyin.com/xxxxxx/"

# 也接受整段分享文案（会自动抠出链接）
python clip.py "3.87 复制打开抖音，看看【某人的作品】…… https://v.douyin.com/xxxxxx/ d@A.Ty"

# L4：入库（标签与标题由 agent 决定后传入）
python clip.py publish douyin:7686034803698754161 \
    --tags 生活 \
    --digest work/douyin_7686034803698754161.digest.md \
    --title "摄影师不用熬夜选片了"

# 其它
python clip.py status          # 列出已产出的 clip
python clip.py doctor          # 环境自检
python clip.py publish <id> --tags 生活 --dry-run   # 只看会做什么，不落盘
```

L3（蒸馏）没有对应命令 —— 它由 agent 完成，见 [`skills/clip-to-obsidian`](./skills/clip-to-obsidian)。

---

## 测试

回归测试只覆盖**结构性契约**，不碰网络与真实知识库（渲染层全部指向临时目录）：

```bash
python -m pytest tests -q                       # 推荐
python -m unittest discover -s tests -t .       # 零依赖跑法，效果相同
```

| 文件 | 覆盖 |
|---|---|
| `test_schema.py` | clip.json 往返序列化、必填字段校验、`ocr_ready` 放行条件、封面/正文图区分 |
| `test_registry.py` | 真实分享文案抠链接、平台路由、未注册链接报错 |
| `test_render.py` | YAML frontmatter 完整性（换行/引号/裸值日期）、`description` 三种口径与边界、附件命名与落位、入库三道闸门 |
| `test_textnorm.py` | 繁转简、半角标点转全角（不误伤 `3.5`/`a:b`）、碎句合段、时间格式 |

---

## 配置

全部平台假设都在 `config.toml` 里，**代码里没有硬编码**：

| 配置项 | 作用 | 换机器时改什么 |
|---|---|---|
| `[vault].path` | 知识库根目录 | 必改 |
| `[fetch].browser` | cookie 来源浏览器 | `edge` / `chrome` / `safari` / `firefox` / `none` |
| `[fetch].prefer_subtitle` | 字幕优先（命中则跳过 ASR；实测命中率低，属锦上添花） | 保持 `true` |
| `[asr].backend` | 转写引擎 | `faster` / `mlx` / `none` |
| `[tools].python` | 运行子步骤的解释器 | 指向装好依赖的那个 |
| `[tools].ffmpeg` | ffmpeg 路径 | 留空则从 PATH 找 |

也支持环境变量临时覆盖：`C2O_VAULT_PATH` / `C2O_BROWSER` / `C2O_ASR_BACKEND` / `C2O_ASR_MODEL` / `C2O_FFMPEG`。

---

## 扩展一个新平台

目标：**只改两处**，不动 `core/`、`publish/`。

1. `core/registry.py` 的 `PLATFORMS` 里加一条 `PlatformSpec`（URL 正则 + 模块名）
2. 新建 `pipelines/{平台}/fetch.py` 与 `extract.py`

`fetch` 产出 `raw/{id}/source.json`，`extract` 产出 `clip.json`。

> 若发现必须修改 `publish/` 或 `core/schema.py` 才能接入新平台，
> 说明抽象失败了 —— 应该回头改抽象，而不是打补丁。

---

## 目录结构

```
clip2obsidian/
├── clip.py                  # CLI 入口（四层编排）
├── config.toml              # 本机配置（不入库）
├── config.example.toml      # 配置模板
├── core/
│   ├── schema.py            # clip.json 契约（唯一关键设计）
│   ├── registry.py          # URL → 平台路由
│   ├── config.py            # 配置加载 + 环境变量覆盖
│   ├── paths.py             # 路径与平台假设集中管理
│   └── textnorm.py          # 繁转简 / 标点统一 / 碎句合段
├── pipelines/
│   ├── douyin/              # fetch.py + extract.py
│   └── xiaohongshu/
├── asr/                     # 转写引擎（可插拔：faster / mlx / none）
├── publish/render.py        # 入库渲染
├── skills/clip-to-obsidian/ # 配套的 agent 编排 skill
├── tests/                   # 回归测试（契约层，不碰网络与真实知识库）
├── raw/                     # L1 物料缓存（可随时删）
└── work/                    # L2/L3 中间产物
```

---

## 已知坑位

1. **抖音标题会被 yt-dlp 截断**并带 `...` 尾巴，长度常超 60 字符。
   渲染层会自动去尾巴，仍建议用 `--title` 传入精简标题。
2. **话题标签常只出现在 `description` 里**，`title` 中没有 `#`。
3. **小红书 PC UA 必被登录墙拦** —— 必须用移动端 UA 抓页面。
   图片 URL 下载必须带 `Referer: https://www.xiaohongshu.com/`，否则 403。
4. **小红书图片顺序**须按 URL 在 HTML 中首次出现的位置排序，
   用 `set()` 会按字符串排序导致顺序错乱。
5. **Whisper 转中文长音频，后半程会退化输出繁体**，必须过 `zhconv` 转简体。
6. **替换同名笔记前会先备份**到 `.workbuddy/backups/`，不做直接删除。
7. **平台字幕命中率很低** —— 抖音/小红书绝大多数视频不带字幕（实测两条样本的
   `subtitles` / `automatic_captions` 均为空），ASR 才是主力路径。字幕优先
   属锦上添花：探测不额外耗时，命中则省掉下载与转写。
8. **平台文案常等于标题** —— 此时 `description_source = auto` 会退回转写
   （清掉话题后不足 20 字），这是设计行为。
9. **CAL 附件插件会接管命名** —— 移动/重命名笔记时，插件会把 `8_附件/{笔记名}/`
   下的文件原地重命名（时间戳变为操作时刻）并同步改写笔记嵌入。
   别指望 `_place_assets` 写下的文件名长期稳定，判断一致性只看断链。
10. **入库前先查同标题笔记**，否则 `Clippings/` 与别处同名会造成 wikilink 歧义；
    旧笔记应备份出库而不是删除。

---

## 配套 Skill

`skills/clip-to-obsidian` 是给 agent 用的编排说明书：何时触发、四层怎么串、
L3 摘要模板、标签选择规则、坑位清单。

安装（软链接，仓库侧改完即生效）：

```bash
ln -s "$PWD/skills/clip-to-obsidian" ~/.workbuddy/skills/clip-to-obsidian
```

---

## License

MIT
