# Changelog

## 0.7.0 — 搜索链路增强（本地 fork）

以「搜索 → 取内容」为主线补齐能力。所有新增均为可选参数或新命令，原有调用形状不变。

### 搜索
- `search --order totalrank|click|pubdate|dm|stow|scores`：排序（原先只能拿综合排序）
- `search --since / --until`：时间窗，支持 `2026-11-01`、`7d`、`30d`、`today`
- `search --duration lt10|10-30|30-60|gt60`：时长筛选
- `search --tid <分区id>`
- `search --fields full`：补 `published_at`/`published_ts`/`duration_seconds`/`stats`/`up.id`/`cover`/`description`/`tags`/`typename`
- `-n` 超过单页 42 条时自动翻页（最多 10 页）；取不满、触顶都会在 stderr 明示，不再静默截断
- `search --meta`：结构化输出附翻页数与截断状态
- `search --exclude-seen`：翻页过程中跳过本地账本已记录的视频，并继续找补足

### 取内容
- `video` 支持多个 BV / URL，或 `--stdin` 从管道读（单条时输出结构保持原样，多条输出 `data.items[]`）
- `video` 详情补 `published_at` / `tags` / `pages`
- **字幕失败语义修正**：区分 `not_authenticated` / `no_subtitle_track` / `subtitle_unavailable`，写入 `warnings`，`subtitle.reason` 可见；显式 `--subtitle` 且拿不到时退出码为 `2`、顶层 `ok: false`（此前会被误读成「视频没字幕」）
- 新增 `danmaku <BV> [-n] [-p]`
- 新增 `crawl <关键词> [-n] [--order] [--since] [--until] [--tid] [--duration] [--subtitle-chars] [--no-subtitle] [--exclude-seen] [--mark-seen] [-o]`：搜索 + 批量字幕一步出 Markdown
- 新增 `transcribe <BV>`：有字幕直接用，无字幕下载音频走本地 faster-whisper
- 新增 `frames <BV> [-n] [--at 秒] [-o]`：故事板抽帧，不下整片

### 去重与榜单
- 新增 `seen list|add|check|clear`：`~/.bilibili-cli/seen.jsonl` 跨轮判重账本
- `rank --type all|knowledge|tech|game|music|documentary|...`：分区排行榜

### 修复
- `get_audio_url` 不再走上游 `detect_best_streams`（该函数在无视频流/未登录响应下会 `NoneType` 崩溃），改为直读 DASH 清单并支持 `durl` 回退
- 搜索结果 `cover` 协议相对地址补全为 `https:`

### 测试
- 新增 10 个用例覆盖排序/时间窗/翻页/全字段/批量/弹幕/crawl/账本/分区；全套 216 passed

## 0.5.0

- Add subtitle timeline output via `bili video --subtitle-timeline` / `-st`
- Add `--subtitle-format timeline|srt`
- Keep subtitle timeline compatible with current `--yaml` / `--json` command surface
- Ensure subtitle timeline requests load optional credentials like plain subtitles
- Restore README badges and fix CI type-checking with `types-PyYAML`
