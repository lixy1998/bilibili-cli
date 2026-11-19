---
name: bilibili-cli
description: "通过终端命令行操作 Bilibili（哔哩哔哩）——搜索（排序/时间窗/分区/全字段）、读字幕与弹幕、抽画面帧、本地转写、榜单、动态、收藏夹；需登录时自动检测浏览器 Cookie 或扫码登录。当用户要求用 B 站 CLI 搜索或抓取 B 站内容时使用。"
title: bilibili-cli
author: jackwener（上游）／ lixy1998（fork 增强）
source: https://github.com/lixy1998/bilibili-cli
version: 0.7.0
model: default
---

# bilibili-cli — B站 CLI（增强版）

命令行操作 Bilibili。相对上游 0.6.2，增强全在「搜索 → 取内容」这条链上：搜索能排序/卡时间窗/筛分区/出全字段/自动翻页；取内容能批量、能读弹幕、能抽帧、能跨轮去重。

## 前置（每台机器一次）

```bash
bili --version     # 期望 0.7.0 及以上
```

没有或版本偏低，任选一种装：

```bash
# 1) 从仓库装固定版本（跨机器可复现）
pip install --upgrade "git+https://github.com/lixy1998/bilibili-cli@v0.7.0"

# 2) 本地已有源码：可编辑安装，改代码即生效
pip install -e "<源码目录>" --no-deps

# 3) 需要抽帧 / 本地转写时补 extras
pip install "bilibili-cli[frames,asr] @ git+https://github.com/lixy1998/bilibili-cli@v0.7.0"
```

- 仓库是 `jackwener/bilibili-cli` 的 fork，**上游版本没有下面这些搜索参数**。
- `bili` 由 Python 环境的 `Scripts` 目录提供；命令找不到时先查该目录是否在 PATH。
- 抽帧要 Pillow，本地转写要 faster-whisper（模型需自备，见文末）。

## 搜索（主用法）

```bash
bili search "历史教学" --type video --order pubdate --since 7d -n 30 --fields full --meta --yaml
```

| 参数 | 作用 |
|---|---|
| `--order` | `totalrank` 综合 / `click` 最多播放 / `pubdate` 最新 / `dm` 最多弹幕 / `stow` 最多收藏 / `scores` 最多评论 |
| `--since` / `--until` | `2026-11-01`、`7d`、`30d`、`today`；不传则不限 |
| `--duration` | `lt10` / `10-30` / `30-60` / `gt60`（分钟） |
| `--tid` | 分区 id 数字（如 188 科技） |
| `--fields full` | 输出发布时间、播放/弹幕/收藏/评论数、UP 的 UID、封面、简介、标签、分区名、时长秒数 |
| `-n` | 目标条数，**超过单页 42 条会自动翻页**（最多 10 页） |
| `--meta` | 结构化输出附 `meta`（翻页数、是否截断、跳过账本条数） |
| `--exclude-seen` | 跳过本地账本里已记录的视频（见「账本」） |

- 默认 `--fields basic` 只给 6 个字段（bvid/标题/作者/播放/时长）；**要判断「值不值得看」必须加 `--fields full`**。
- 新参数只对 `--type video` 生效；`--type user` 走原逻辑。
- 取不满或触到翻页上限时 stderr 会明说，不会静默截断。

## 取内容

| 命令 | 说明 |
|---|---|
| `bili video <BV> [<BV>...] --yaml` | 视频详情；**支持一次多个 BV**，多条时输出 `data.items[]` |
| `bili video --stdin` | 从标准输入读 BV 列表（空白/换行分隔），可管道接搜索结果 |
| `bili video <BV> --subtitle` | 字幕（需登录）；失败会区分 `not_authenticated` / `no_subtitle_track` |
| `bili video <BV> --ai / -c / -r` | AI 摘要 / 评论 / 相关推荐 |
| `bili danmaku <BV> -n 100` | 弹幕（时间点+内容，判断真实反馈用） |
| `bili crawl "<关键词>" -n 5 --since 7d` | **搜索+字幕一步到位**，输出可读 Markdown（`-o file.md` 落盘） |
| `bili transcribe <BV>` | 文字稿：有字幕直接用，没字幕本地 faster-whisper 转写 |

`crawl` 常用组合：

```bash
# 每天一轮「只看新的」：抓 5 条近 7 天的，抓完记账，下次自动跳过
bili crawl "历史 教学" -n 5 --since 7d --exclude-seen --mark-seen -o out/today.md
```

- `--subtitle-chars 3000` 控制每条字幕保留字数（0 = 不限）；`--no-subtitle` 只出搜索结果。
- 非交互环境默认输出 YAML；要 Markdown 就加 `OUTPUT=rich`。

## 画面帧（无字幕的演示类视频）

```bash
bili frames <BV> -n 3          # 均匀取 3 帧
bili frames <BV> --at 143      # 取最接近 143 秒的一帧
```

走 B站故事板，**不下整片**，产出 PNG 路径（默认 `~/.bilibili-cli/frames/<BV>/`）。

## 跨轮去重账本

```bash
bili seen list -n 20
bili seen check BV1xx BV1yy     # 是否看过
bili seen add BV1xx --note "历史教学"
bili seen clear --yes
```

账本：`~/.bilibili-cli/seen.jsonl`（`BILI_CLI_HOME` 可改目录）。`search` / `crawl` 加 `--exclude-seen` 会在**翻页过程中**跳过已记录的，并继续往后找补足条数。

## 榜单与其它

```bash
bili rank --type knowledge -n 20     # 分区：all/knowledge/tech/game/music/documentary/...
bili hot / bili feed / bili favorites / bili following / bili history / bili watch-later
bili like / coin / triple <BV>
```

## 输出格式与退出码

- 非 tty 自动 YAML；显式 `--yaml` / `--json`；`OUTPUT=rich` 强制人读表格。
- 只读命令无需登录；字幕、收藏、互动需要登录（`bili login` 扫码，凭证存 `~/.bilibili-cli/credential.json`）。
- **字幕取不到时退出码 2**（`ok: false` + `error.code`），不再伪装成「视频没字幕」；视频详情本身仍返回。
- 其它失败退出码 1，`error.code` 取值：`invalid_input` / `not_authenticated` / `rate_limited` / `network_error` / `upstream_error` / `asr_unavailable`。

## 干活顺序（推荐）

1. `bili search "<kw>" --type video --order pubdate --since 7d --fields full --meta --yaml` 拿候选
2. 批量读内容 → `bili crawl`；单条深看 → `bili video <BV> --subtitle --yaml`
3. 无字幕的演示类 → `bili frames <BV> -n 3` 看画面
4. 判断真实反馈 → `bili danmaku <BV> -n 100`
5. 要文字稿而字幕没有 → `bili transcribe <BV>`
6. 记住看过的 → `bili seen add` 或 crawl `--mark-seen`

## 注意事项

- **字幕优先于 AI 摘要**；都没有再转写。
- 搜索别只看播放量：`--order pubdate --since 7d` 才能拿到近期新内容；`published_at` 是判断「旧闻」最快的字段。
- 全部命令支持 `--yaml`，比 `--json` 更省 token。
- **本地转写的模型要自备**：`--model` 可传 huggingface 仓库名或**本地模型目录**。网络受限时设 `HF_ENDPOINT=https://hf-mirror.com`；仍拉不下来就把已下好的 faster-whisper 模型目录用 `--model <dir>` 指过去，或改用 `bili audio <BV> --no-split -o <目录>` 导出音频交外部 ASR。
