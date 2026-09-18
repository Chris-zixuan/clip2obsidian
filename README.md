# clip2obsidian · 本地素材 → 待入库的 md

把**已经下载到本地**的素材（视频 / 音频 / 图文）转成一份干净 md，再交给 agent 落进
Obsidian 知识库。

前 90%（抽音轨、转写、模板渲染、断链校验）用现成工具；最后 10%（落成符合自己知识库
规范的笔记）自己写 —— 那 10% 才是这套东西存在的理由。

## 三层

| 层 | 做什么 | 谁做 | 产物 |
|---|---|---|---|
| **L1** route | 类型判定、内容指纹、元信息降级 | 代码 | `raw/{id}/job.json` |
| **L2** convert | 视频/音频转写、图文骨架、md 组装 | 代码 | `work/{id}.md`（纯正文 + 来源块） |
| **L3** ingest | 组属性、落 `0_Inbox/Clippings/`、断链复核 | agent + `skills/clip-to-obsidian` | 知识库笔记 |

**本项目代码只读写 `raw/` 与 `work/`，不出现任何知识库路径。** 这条边界是物理保证的，
不是口头约定 —— 入库完全由 skill 与 agent 完成。

## 支持什么

| 输入 | 处理 |
|---|---|
| 视频（`.mp4` `.mov` `.mkv` …） | ffmpeg 抽音轨 → **本地字幕优先**（命中则不转写）→ 分片转写 |
| 音频（`.mp3` `.wav` `.m4a` …） | 直接转写 |
| 图文（图片目录 / zip） | 图片按自然序复制到 `work/{id}/images/`，产出骨架 md |
| 目录 | 含视频/音频/zip → 批量逐项；只含图片 → 当作一条图文 |
| zip | 安全解压（防目录穿越与解压炸弹）后重新判定 |

素材被改名、移动、重新打包，都还是**同一条** —— id 取文件内容指纹，不取文件名。

## 快速开始

```bash
# 1) 依赖（用你装依赖的那个解释器；项目根 .venv 已软链到它）
pip install mlx-whisper      # Apple Silicon，快（实测 884 秒音频约 230 秒）
# 或 pip install faster-whisper   # CPU 也能跑，慢一倍多
pip install zhconv           # 繁转简（可选，缺了会自动跳过）
brew install ffmpeg          # 需要 ffmpeg 与 ffprobe

# 2) 配置
cp config.example.toml config.toml

# 3) 自检
python clip.py doctor

# 4) 跑
python clip.py ~/Downloads/xxx_哔哩哔哩_bilibili.mp4   # 一步跑完 L1+L2
python clip.py scan                                     # 看收件目录里有什么
python clip.py status                                   # 看处理到哪一步
```

之后由 agent 读 `work/{id}.md`，用 `skills/clip-to-obsidian` 入库。

## 配套 skill 的安装

`skills/clip-to-obsidian/` 是 L3 的编排说明，但 CodeBuddy 只从固定位置加载 skill，
所以软链一次即可：

```bash
mkdir -p ~/.codebuddy/skills
ln -sfn "$PWD/skills/clip-to-obsidian" ~/.codebuddy/skills/clip-to-obsidian
```

软链指向仓库，之后改 skill 立刻生效，不用重复安装。装好后在会话里说
「把这批 md 入库」就会触发。

该 skill 只做一件事：**新增**剪藏（读来源块 → 现场读库内 `9_系统/协作约定.md`
→ 组属性 → 落 `0_Inbox/Clippings/` → 调用 `yzx-obsidian` 做断链复核）。
库级的移动、重命名、整理、维护全部转交 `yzx-obsidian`，避免两套规则并行。

## 转写：本地优先，云端留插槽

```toml
[transcribe]
provider = "auto"     # auto：本地可用就用本地，否则云端
engine   = "auto"     # auto：依次探测 mlx-whisper、faster-whisper
model    = "large-v3" # 短名会按引擎映射（mlx → mlx-community/whisper-large-v3-mlx）
chunk_sec = 600       # 分片时长：确定进度、限制内存、单片刻重试
```

- **本地**（默认）：免费、离线、时间戳完整、无时长限制
- **云端**（插槽）：`[transcribe.cloud]` 填 `base_url` / `api_key` / `model` 即启用，
  走 OpenAI 兼容的 `/audio/transcriptions`，直接 multipart 上传本地音频，
  **不需要对象存储中转**。本次未实测，配置齐了才会被选中

分片的理由：`mlx-whisper` 是滑窗跑完一次性返回，没有细粒度回调；切片后每片完成
就是一格确定进度，顺带拿到「长音频不吃满内存」与「单片可重试」。片间重叠 1 秒，
落在重叠区的片段丢弃，避免边界处截断词句。

## 进度可见

```bash
python clip.py <路径> --progress inline   # 默认：终端单行原地刷新
python clip.py <路径> --progress window   # 另开一个终端窗口显示进度条（macOS）
python clip.py <路径> --progress off      # 不打进度
```

- 抽音轨：ffmpeg `-progress` + ffprobe 总时长 → **确定百分比**
- 转写：按片上报（第 i/N 片）→ 确定进度
- 重定向到文件（非终端）时自动降级为里程碑行，不会写一堆回车符进日志

## 转写结果的去向

```
raw/{id}/job.json          登记备忘（不是跨层契约）
work/{id}.wav              统一规格音轨（16k 单声道 PCM），可复用
work/{id}.asr.json         转写结果缓存，重跑不重复计费
work/{id}.chunks/          分片（命中缓存后不再使用）
work/{id}.md               ★ 交付物：纯正文 + 来源块
work/{id}/images/          图文素材的图片副本
```

`work/{id}.md` 顶部是一段 HTML 注释形式的来源块（渲染不可见、agent 可解析）：

```
<!--c2o:meta
title: 认识 MAF
source_url: https://www.bilibili.com/video/BV1xx411c7mD
author: 某UP主
published: 2026-09-01
clipping_type: bilibili-video
-->
```

**正文里没有 frontmatter** —— 属性由入库 skill 组装，本项目不碰知识库格式。

## 边界

1. **不写知识库**：附件也不搬（知识库规则：搬运/改名附件交给 CAL 插件，AI 不写脚本
   批量搬）。图片只复制到 `work/{id}/images/`。
2. **不编造元信息**：sidecar → `--url`（yt-dlp 只取 JSON、绝不下载）→ 文件名 → 留空。
   拿不到就留空，空值在渲染层优雅降级，不产出 `unknown` 这类脏值。
3. **只读不删**：本项目不删除任何用户素材，`raw/` 与 `work/` 都是可随时清的缓存。
4. **文档类型只在 `core/route.py` 注册**：新增平台加一条 `PlatformSpec`
   （名字特征 + 原生 id 正则 + 链接模板），转换流程完全不认识平台。

## 目录结构

```
clip.py                    唯一 CLI 入口（route / convert / run / status / scan / doctor）
core/
  config.py                配置加载与严格校验（未知段/键直接报错）
  paths.py                 路径与平台假设集中处
  route.py                 类型判定、目录二义性消解、平台与元信息降级
  fingerprint.py           内容指纹（单文件 / 图集）
  progress.py              进度上报（inline / json / window / null）
  textnorm.py              繁转简、标点归一、碎句合段、时间格式化
l1/
  ingest.py                素材登记 → raw/{id}/job.json
  ziputil.py               安全解压（防目录穿越与解压炸弹）
l2/
  convert.py               按类型分派
  video.py / audio.py / images.py   三条分支
  media.py                 ffprobe 取时长、ffmpeg 抽音轨（带进度）
  subtitle.py              本地字幕解析（srt / vtt / ass）
  md.py                    来源块 + 正文组装
  transcribe/              provider：local（mlx/faster）与 cloud（OpenAI 兼容）
skills/clip-to-obsidian/   L3 入库 skill（配套本项目的 agent 编排说明）
tests/                     141 项离线回归
```

## 测试

```bash
python -m pytest --basetemp=/tmp/c2o_pytest
```

`tests/conftest.py` 把 `raw/` / `work/` 重定向到临时目录 —— 否则用例清理逻辑可能删掉
使用者的真实产物（真实踩过）。受管控环境需显式给可写 basetemp。

## 待办

- [ ] 云端 provider 接入实测（当前仅插槽，未验证）
- [ ] 分片边界的截断：目前靠 1 秒重叠兜底，可加静音点切分
- [ ] 抖音 / 小红书图文的元信息与命名细节（识别规则已在 `core/route.py`）
- [ ] 更细的转写进度（片内叠加静音切分）
