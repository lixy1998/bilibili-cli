"""Stable structured payload builders for bilibili-cli commands."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


def _to_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _format_duration(seconds: object) -> str:
    total = max(_to_int(seconds, 0), 0)
    if total >= 3600:
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _strip_html(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _normalize_url(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip()
    if value.startswith("//"):
        return "https:" + value
    return value


def _pubdate_fields(source: dict[str, Any]) -> dict[str, Any]:
    """Extract a comparable publish time from any B站 video payload shape."""
    raw = source.get("pubdate") or source.get("senddate") or source.get("ctime") or 0
    ts = _to_int(raw, 0)
    if ts <= 0:
        return {"published_at": "", "published_ts": 0}
    try:
        return {
            "published_at": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
            "published_ts": ts,
        }
    except (OSError, OverflowError, ValueError):
        return {"published_at": "", "published_ts": 0}


def _parse_duration_text(text: object) -> int:
    """Parse "MM:SS" / "HH:MM:SS" into total seconds."""
    if isinstance(text, int):
        return max(text, 0)
    if not isinstance(text, str) or not text.strip():
        return 0
    parts = text.strip().split(":")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 1:
        return numbers[0]
    return 0


def _normalize_tags(source: dict[str, Any]) -> list[str]:
    """Normalize both search-shaped ("tag": "a,b") and info-shaped ("tag": [{tag_name}])."""
    tags = source.get("tags") or source.get("tag") or []
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    if isinstance(tags, list):
        out: list[str] = []
        for t in tags:
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, dict):
                name = t.get("tag_name") or t.get("name")
                if name:
                    out.append(str(name))
        return out
    return []


def normalize_user(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(info.get("mid", "")),
        "name": info.get("name", ""),
        "username": info.get("name", ""),
        "level": _to_int(info.get("level"), 0),
        "coins": _to_int(info.get("coins"), 0),
        "sign": info.get("sign", ""),
        "vip": info.get("vip", {}) if isinstance(info.get("vip"), dict) else {},
    }


def normalize_relation(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "following": _to_int(info.get("following"), 0),
        "follower": _to_int(info.get("follower"), 0),
    }


def normalize_video_summary(video: dict[str, Any]) -> dict[str, Any]:
    owner = video.get("owner", {}) if isinstance(video.get("owner"), dict) else {}
    stat = video.get("stat", {}) if isinstance(video.get("stat"), dict) else {}
    duration_seconds = _to_int(video.get("duration"), _to_int(video.get("length"), 0))
    url = ""
    if isinstance(video.get("bvid"), str) and video.get("bvid"):
        url = f"https://www.bilibili.com/video/{video['bvid']}"

    return {
        "id": str(video.get("bvid") or video.get("aid") or ""),
        "bvid": video.get("bvid", ""),
        "aid": _to_int(video.get("aid"), 0),
        "title": _strip_html(video.get("title")),
        "description": video.get("desc", "") or video.get("description", ""),
        "duration_seconds": duration_seconds,
        "duration": _format_duration(duration_seconds),
        "url": url,
        "owner": {
            "id": str(owner.get("mid", owner.get("id", ""))),
            "name": owner.get("name", owner.get("uname", "")),
        },
        "stats": {
            "view": _to_int(stat.get("view", video.get("play", 0)), 0),
            "danmaku": _to_int(stat.get("danmaku"), 0),
            "like": _to_int(stat.get("like"), 0),
            "coin": _to_int(stat.get("coin"), 0),
            "favorite": _to_int(stat.get("favorite"), 0),
            "share": _to_int(stat.get("share"), 0),
        },
        **_pubdate_fields(video),
        "tags": _normalize_tags(video),
        "pages": len(video.get("pages") or []) or _to_int(video.get("videos"), 0),
    }


def normalize_subtitle_items(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "from": float(item.get("from", 0.0) or 0.0),
                "to": float(item.get("to", 0.0) or 0.0),
                "content": item.get("content", ""),
            }
        )
    return items


def normalize_comment(item: dict[str, Any]) -> dict[str, Any]:
    member = item.get("member", {}) if isinstance(item.get("member"), dict) else {}
    content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
    return {
        "id": str(item.get("rpid_str") or item.get("rpid") or ""),
        "author": {
            "id": str(member.get("mid", "")),
            "name": member.get("uname", ""),
        },
        "message": content.get("message", ""),
        "like": _to_int(item.get("like"), 0),
        "reply_count": _to_int(item.get("rcount"), 0),
    }


def normalize_related_video(item: dict[str, Any]) -> dict[str, Any]:
    return normalize_video_summary(item)


def normalize_search_user(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("mid", "")),
        "name": item.get("uname", ""),
        "sign": item.get("usign", ""),
        "fans": _to_int(item.get("fans"), 0),
        "videos": _to_int(item.get("videos"), 0),
    }


def normalize_search_video(item: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    duration = item.get("duration", "")
    if not isinstance(duration, str):
        duration = _format_duration(duration)

    bvid = str(item.get("bvid", ""))
    payload = {
        "id": bvid,
        "bvid": bvid,
        "title": _strip_html(item.get("title")),
        "author": item.get("author", ""),
        "play": _to_int(item.get("play"), 0),
        "duration": duration,
    }
    if not full:
        return payload

    payload.update(
        {
            **_pubdate_fields(item),
            "duration_seconds": _parse_duration_text(duration),
            "up": {
                "id": str(item.get("mid") or item.get("uid") or ""),
                "name": item.get("author", ""),
            },
            "stats": {
                "view": _to_int(item.get("play"), 0),
                "danmaku": _to_int(item.get("video_review") or item.get("danmaku"), 0),
                "favorite": _to_int(item.get("favorites"), 0),
                "like": _to_int(item.get("like"), 0),
                "review": _to_int(item.get("review"), 0),
            },
            "cover": _normalize_url(item.get("pic") or item.get("cover")),
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            "description": _strip_html(item.get("description") or item.get("desc")),
            "tags": _normalize_tags(item),
            "typename": item.get("typename", ""),
        }
    )
    return payload


def normalize_favorite_folder(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _to_int(item.get("id"), 0),
        "title": item.get("title", ""),
        "media_count": _to_int(item.get("media_count"), 0),
    }


def normalize_favorite_media(item: dict[str, Any]) -> dict[str, Any]:
    upper = item.get("upper", {}) if isinstance(item.get("upper"), dict) else {}
    return {
        "id": str(item.get("bvid", "") or item.get("id", "")),
        "bvid": item.get("bvid", ""),
        "title": item.get("title", ""),
        "duration_seconds": _to_int(item.get("duration"), 0),
        "duration": _format_duration(item.get("duration")),
        "upper": {
            "name": upper.get("name", ""),
        },
    }


def normalize_following_user(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("mid", "")),
        "name": item.get("uname", ""),
        "sign": item.get("sign", ""),
    }


def normalize_history_item(item: dict[str, Any]) -> dict[str, Any]:
    history = item.get("history", {}) if isinstance(item.get("history"), dict) else {}
    owner = item.get("owner", {}) if isinstance(item.get("owner"), dict) else {}
    view_at = _to_int(history.get("view_at", item.get("view_at", 0)), 0)
    viewed_at = datetime.fromtimestamp(view_at).isoformat() if view_at > 0 else ""
    return {
        "id": str(history.get("bvid") or item.get("bvid") or history.get("oid") or ""),
        "bvid": history.get("bvid") or item.get("bvid", ""),
        "title": item.get("title", "") or item.get("name", ""),
        "author": owner.get("name", "") or item.get("author_name", "") or item.get("author", ""),
        "viewed_at": viewed_at,
    }


def normalize_watch_later_item(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner", {}) if isinstance(item.get("owner"), dict) else {}
    return {
        "id": str(item.get("bvid", "")),
        "bvid": item.get("bvid", ""),
        "title": item.get("title", ""),
        "author": owner.get("name", ""),
        "duration_seconds": _to_int(item.get("duration"), 0),
        "duration": _format_duration(item.get("duration")),
    }


def _decode_json(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_dynamic_item(item: dict[str, Any]) -> dict[str, Any]:
    modules = item.get("modules", {}) if isinstance(item.get("modules"), dict) else {}
    author = modules.get("module_author", {}) if isinstance(modules.get("module_author"), dict) else {}
    dynamic_mod = modules.get("module_dynamic", {}) if isinstance(modules.get("module_dynamic"), dict) else {}
    stat = modules.get("module_stat", {}) if isinstance(modules.get("module_stat"), dict) else {}
    desc = dynamic_mod.get("desc", {}) if isinstance(dynamic_mod.get("desc"), dict) else {}
    major = dynamic_mod.get("major", {}) if isinstance(dynamic_mod.get("major"), dict) else {}
    archive = major.get("archive", {}) if isinstance(major.get("archive"), dict) else {}
    article = major.get("article", {}) if isinstance(major.get("article"), dict) else {}

    card = _decode_json(item.get("card"))
    desc_info = item.get("desc", {}) if isinstance(item.get("desc"), dict) else {}
    dynamic_id = desc_info.get("dynamic_id_str") or desc_info.get("dynamic_id") or item.get("id_str") or item.get("id") or ""
    ts = _to_int(desc_info.get("timestamp"), 0)
    published_at = datetime.fromtimestamp(ts).isoformat() if ts > 0 else ""

    text = desc.get("text", "")
    if not text:
        for key in ("dynamic", "description", "summary", "title"):
            if isinstance(card.get(key), str) and card.get(key):
                text = card[key]
                break
        item_info = card.get("item")
        if isinstance(item_info, dict) and not text:
            text = item_info.get("content", "") or item_info.get("description", "") or item_info.get("title", "")

    title = archive.get("title", "") or article.get("title", "")
    comment_info = stat.get("comment", {}) if isinstance(stat.get("comment"), dict) else {}
    like_info = stat.get("like", {}) if isinstance(stat.get("like"), dict) else {}

    return {
        "id": str(dynamic_id),
        "author": {
            "name": author.get("name", ""),
        },
        "published_at": published_at,
        "published_label": author.get("pub_time", ""),
        "title": title,
        "text": text,
        "stats": {
            "comment": _to_int(comment_info.get("count"), 0),
            "like": _to_int(like_info.get("count"), 0),
        },
    }


def normalize_video_command_payload(
    info: dict[str, Any],
    *,
    subtitle_text: str = "",
    subtitle_items: list[dict[str, Any]] | None = None,
    subtitle_format: str = "timeline",
    subtitle_reason: str = "",
    ai_summary: str = "",
    comments: list[dict[str, Any]] | None = None,
    related: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    subtitle_payload = {
        "available": bool(subtitle_text or subtitle_items),
        "reason": subtitle_reason,
        "format": subtitle_format,
        "text": subtitle_text,
        "items": normalize_subtitle_items(subtitle_items),
    }
    return {
        "video": normalize_video_summary(info),
        "subtitle": subtitle_payload,
        "ai_summary": ai_summary,
        "comments": [normalize_comment(item) for item in comments or []],
        "related": [normalize_related_video(item) for item in related or []],
        "warnings": warnings or [],
    }


def action_result(action: str, *, success: bool = True, **fields: Any) -> dict[str, Any]:
    payload = {"success": success, "action": action}
    payload.update(fields)
    return payload
