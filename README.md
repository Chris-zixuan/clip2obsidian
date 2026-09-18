# clip2obsidian · 本地文件 → Obsidian 笔记

把**已经下载到本地**的视频，蒸馏成符合私人知识库规范的 Obsidian 笔记。

前 90%（抽音频、转写、模板渲染、附件落位、断链校验）用现成工具；
最后 10%（落成符合自己知识库规范的笔记）自己写 —— 这 10% 才是项目存在的理由。

## 四层流水线

| 层 | 做什么 | 产物 | 谁做 |
|---|---|---|---|
| **L1** ingest | 本地文件 → 精简物料清单 | `raw/{id}/source.json` | 代码 |
| **L2** extract | 物料 → 统一契约 | `work/{id}.clip.json` | 代码 |
| **L3** distill | 读契约、写摘要 | `work/{id}.digest.md` | **agent** |
| **L4** publish | 契约 + 摘要 → 笔记 | `vault/0_Inbox/Clippings/{标题}.md` | 代码渲染 + agent 定标签/标题 |

每层产物落盘、可单独检查、可单独重跑。`clip.json`（`core/schema.py`）是四层之间
**唯一的通信契约**：平台 extractor 只负责填满它，下游完全不认识平台。

**代码不抓链接、不下载**：用户把文件下到本地后直接 `ingest`。
大视频不复制进 `raw/`，只记绝对路径。

## 快速开始

```bash
# 1) 环境（用你有依赖的那个解释器）
pip install faster-whisper zhconv yt-dlp       # yt-dlp 可选
brew install ffmpeg

# 2) 配置
cp config.example.toml config.toml             # 改 vault.path / tools.python

# 3) 自检
python clip.py doctor

# 4) 跑
python clip.py "~/Downloads/xxx_哔哩哔哩_bilibili.mp4"   # = ingest + extract
python clip.py scan                                      # 看收件目录里有什么
```

之后由 agent 完成 L3 写摘要，再：

```bash
python clip.py publish bilibili:BV1xx411c7mD --tags 生活 --title "精简标题" \
    --digest work/bilibili_BV1xx411c7mD.digest.md
```

## 核心设计

1. **契约唯一**：新增平台只改 `core/registry.py` + 新建 `pipelines/{平台}/extract.py`。
   若必须改 `publish/` 或 `schema.py`，说明抽象失败。
2. **职责边界**：代码只做可重跑的机械劳动；需要判断的事（摘要、标题精简、标签）
   全部收敛到 L3 一个点，通过参数传入，不污染其它环节。
3. **防幻觉**：元信息按 sidecar → `--url` → 文件名 → 留空逐层降级，
   **拿不到就留空**，绝不从外部知识补。空 `source_url` / `author` 在渲染层优雅降级。
4. **入库闸门**：`validate()` 结构校验 + 必须有受控标签，任一不过即拒绝落库。
5. **只读不删**：覆盖同名笔记前先 `mv` 到 `.workbuddy/backups/`。
6. **配置零硬编码**：所有路径/参数在 `config.toml`，未知段与未知键直接报错。

`description` 默认取平台自带文案而非 ASR 转写 —— 同音字错误会直接进属性面板
（实测「RAW 原片」→「REW圆片」）。口径由 `[publish].description_source` 控制。

## 目录结构

```
clip.py                    # 唯一 CLI 入口
core/
  schema.py                # clip.json 契约（validate / 序列化）
  registry.py              # 平台路由（本地文件 → 平台）
  config.py                # 配置加载与严格校验
  paths.py                 # 路径集中（平台差异只在这里）
  textnorm.py              # 文本清洗、时间格式化
pipelines/
  common.py                # 视频类共享：字幕解析 → ASR、元信息映射
  local/ingest.py          # L1 本地导入 → source.json
  bilibili/extract.py      # L2 B站
publish/render.py          # L4 渲染与落库
asr/                       # 转写引擎（可插拔，见 asr/base.py 注册表）
tests/                     # 契约与渲染回归
skills/clip-to-obsidian/   # agent 编排说明
```

## 新增平台

1. `core/registry.py` 的 `PLATFORMS` 加一条 `PlatformSpec`（含文件名特征与 id 正则）
2. 新建 `pipelines/{平台}/extract.py`，实现 `extract(source_path, *, cfg, force) -> Clip`

L1 由 `pipelines/local/ingest.py` 统一完成，无需每平台各写一套。

## 测试

```bash
python -m pytest --basetemp=/tmp/c2o_pytest
```

`tests/conftest.py` 会把 `raw/` / `work/` / 备份区整体重定向到临时目录 ——
否则用例的清理逻辑可能删掉使用者的真实产物（真实踩过）。
受管控沙盒里需显式给可写 basetemp，否则报 `EEXIST: pytest-of-unknown`。

## 待办（重建计划）

- [ ] `detect_local` 已数据驱动，但平台 id 仍依赖文件名（改名即视为新条目）
- [ ] L3 缺少机器校验：digest 结构不合规没有检查点
- [ ] 多平台（抖音 / 小红书图文）按上述 SOP 长回来
- [ ] 图文形态（`image_text`）与附件落位在精简时移除，需重新设计
