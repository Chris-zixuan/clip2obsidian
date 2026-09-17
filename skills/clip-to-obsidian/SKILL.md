---
name: clip-to-obsidian
description: 把收藏的链接（抖音视频 / 小红书图文）蒸馏成符合知识库规范的 Obsidian 笔记。当用户发来抖音或小红书链接、分享口令（"复制打开抖音""直达【小红书】围观"）并说"转笔记""存知识库""整理到 Obsidian""收藏这个""转文字"时触发。
agent_created: true
source_repo: "https://github.com/Chris-zixuan/clip2obsidian"
---

# 链接 → Obsidian 笔记

代码在 `~/个人项目/clip2obsidian/`，本 skill 只讲**怎么串联、怎么判断**。

## 核心认知：四层分工

| 层 | 谁做 | 命令 / 动作 |
|---|---|---|
| L1 采集 | 代码 | `python3 clip.py fetch "<链接>"` |
| L2 提取 | 代码（**图文类需 agent 读图**） | `python3 clip.py extract <id>` |
| L3 蒸馏 | **agent** | 自己读 `clip.json`，写 `digest.md` |
| L4 入库 | 代码渲染 + **agent 定标签/标题** | `python3 clip.py publish <id> --tags ...` |

一条命令跑完 L1+L2：`python3 clip.py run "<链接>"`（也接受整段分享文案，会自动抠链接）。

> **不要用 curl / yt-dlp 重造流程。** 抓取的坑（cookie、移动端 UA、图片顺序）
> 已经在代码里处理过了，绕过它只会重踩。

---

## 标准执行流程

### 1. 跑 L1+L2

```bash
cd ~/个人项目/clip2obsidian
python3 clip.py run "<链接>" 2>&1 | tail -20
```

转写较慢（61 秒视频约 40 秒），务必用 `run_in_background`。

**失败时**：错误信息已翻译成人话。常见三类——

- `504 / timed out` → 本机代理瞬时抖动。`curl -s -o /dev/null -w "%{http_code}" -I <链接>`
  应返回 302，正常就直接重跑，别改配置。
- `需要登录态` → 确认 Edge 已登录、`config.toml` 的 `browser = edge`。
- `未命中平台字幕` → 正常，会走 ASR。

### 2. 图文类：读图并回填（**关键步骤，不可跳过**）

小红书图文笔记的正文常只有几句引子，**干货全在图片里**。

```bash
# 看哪些图片待回填
python3 -c "
import json;d=json.load(open('work/xiaohongshu_<id>.clip.json'))
print([ (a['order'], a['path']) for a in d['assets'] if a['role']=='content' ])
"
```

然后：

1. 用 Read 工具逐张读取 `raw/xiaohongshu_<id>/*.jpg`
2. 把图内的清单 / 表格 / 要点转写成 **markdown**，保持"名称 + 备注"两列结构
3. 回填到 `work/xiaohongshu_<id>.clip.json` 的 `content.images_ocr`：

```json
{ "order": 1, "text": "### 车辆应急\n- 车载充气泵：……", "status": "done" }
```

`status` 必须改成 `done`，否则 publish 会拒绝入库。

### 3. 写摘要（L3，agent 的活）

读 `clip.json`（`content.transcript` 或 `images_ocr`），写
`work/<id>.digest.md`（id 里的 `:` 换成 `_`）。

**固定模板，不允许换结构**：

```markdown
## 一句话概括
（≤50 字：这是什么 + 讲了什么事）

## 要点
| # | 要点 | 位置 |
|---|------|------|
| 1 | …… | 00:12 |

## 关键结论
- （3–5 条作者的核心主张）

## 对我的用处 / 待核实
- 与我已有的什么相关
- 作者没说清、需要另行核实的信息
```

**硬约束**：
- 只写 `clip.json` 里有的内容，**不补外部知识**——补了就是往知识库里灌幻觉
- 时间戳必须来自 `transcript`，不得编造
- 「待核实」段必须诚实标注信息缺口（例如：视频未明示工具名称与价格）

### 4. 入库（L4）

```bash
python3 clip.py publish <id> --tags <词表标签> \
    [--digest work/<id>.digest.md] \
    [--title "精简标题"]
```

**标签必须从受控词表里选**（协作约定 §4）：

| 域 | 可选值 |
|---|---|
| 技术域 | `3D视觉` `定位抓取` `标定` `缺陷检测` `算法` `AI` `开发工具` `网络运维` `Obsidian` |
| 个人域 | `生活` `成长` `求职` `健康` `英语` |

- 每篇至少 1 个一级标签，**总数不超过 4 个**，宁少勿滥
- **`clippings` 不做标签**（§4.4，`类型` 字段已表达）
- **平台名、项目名不当标签**
- 拿不准就只给一个最宽的（如 `生活`），并在回复里说明理由

`--title` 用精简标题（抖音标题常被 yt-dlp 截断带 `...`，且超长导致文件名臃肿）。

落库口径（已定，不要重问用户）：
- 落点 `0_Inbox/Clippings/`
- `类型: clippings`；平台用 `clipping_type` 区分（`douyin-video` / `xiaohongshu-note`）
- 来源层字段由工具写入，视同 importer 产物

### 5. 校验

```bash
cd "<vault>" && obsidian unresolved | grep "<笔记名>"    # 空 = 附件嵌入全部解析成功
```

`obsidian` CLI 的安全红线：多数子命令没有 `--help`，裸调会直接执行默认行为
（裸调 `create` 会建 `Untitled.md`）。**永远显式传 `path=`**。

---

## 坑位

1. **小红书 PC UA 必被登录墙拦** → 代码已用移动端 UA，不要绕过去自己 curl。
2. **图片顺序不能 `set()` 排序**（会按 URL 字符串排），须按 HTML 中出现位置。
3. **图片下载必须带 `Referer: https://www.xiaohongshu.com/`**，否则 403。
4. **Whisper 输出必过 `zhconv` 转简体** —— 长音频后半程会退化繁体。代码已处理。
5. **同名笔记会被备份不删除** —— 渲染层自动 `mv` 到 `.workbuddy/backups/`，
   不要在 vault 里直接删。
6. **`xsec_token` 会过期** → 报错时让用户重新从 App 复制分享链接。
7. **转写必然有同音字错误** → 笔记里保留 warning callout，不要假装已经校对过。

---

## 与其他 skill 的分工

- 自驾/旅行类内容落库后，可提示 `drive-trip-planner` 复用其中的清单项。
- 入库后如需整理笔记属性 / 移动目录，走 `yzx-obsidian`（不要手改 wikilink）。
