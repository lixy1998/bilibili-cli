"""Bilibili API client — thin async wrappers around bilibili-api-python.

All public functions are async and accept an optional Credential for
authenticated operations (subtitles, favorites, etc.).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import aiohttp
from bilibili_api import comment, dynamic, favorite_list, homepage, hot, rank, search, user, video
from bilibili_api.exceptions import (
    ApiException,
    CredentialNoBiliJctException,
    CredentialNoSessdataException,
    NetworkException,
    ResponseCodeException,
    ResponseException,
)
from bilibili_api.utils.network import Credential

from .exceptions import AuthenticationError, BiliError, InvalidBvidError, NetworkError, NotFoundError, RateLimitError

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# BV ID helpers
# ---------------------------------------------------------------------------

_BVID_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")


def extract_bvid(url_or_bvid: str) -> str:
    """Extract BV ID from a Bilibili URL or return as-is if already a BV ID."""
    match = _BVID_RE.search(url_or_bvid)
    if match:
        return match.group(0)
    raise InvalidBvidError(f"无法提取 BV 号: {url_or_bvid}")


def _map_api_error(action: str, exc: Exception) -> BiliError:
    """Map third-party API exceptions into stable local exception types."""
    if isinstance(exc, BiliError):
        return exc

    if isinstance(exc, (CredentialNoSessdataException, CredentialNoBiliJctException)):
        return AuthenticationError(f"{action}: {exc}")

    if isinstance(exc, ResponseCodeException):
        code = getattr(exc, "code", None)
        # Auth failures
        if code in {-101, -111}:
            return AuthenticationError(f"{action}: {exc}")
        # Not found
        if code in {-404, 62002, 62004}:
            return NotFoundError(f"{action}: {exc}")
        # Rate limit / anti-scraping
        if code in {-412, 412}:
            return RateLimitError(f"{action}: {exc}")
        return BiliError(f"{action}: [{code}] {exc}")

    if isinstance(exc, (NetworkException, ResponseException, aiohttp.ClientError, asyncio.TimeoutError)):
        return NetworkError(f"{action}: {exc}")

    if isinstance(exc, ApiException):
        return BiliError(f"{action}: {exc}")

    return BiliError(f"{action}: {exc}")


async def _call_api(action: str, awaitable):
    """Run an awaitable and normalize API/network/auth errors."""
    try:
        return await awaitable
    except Exception as exc:
        raise _map_api_error(action, exc) from exc


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


async def get_video_info(bvid: str, credential: Credential | None = None) -> dict[str, Any]:
    """Fetch video metadata (title, duration, stats, owner, etc.)."""
    v = video.Video(bvid=bvid, credential=credential)
    return await _call_api("获取视频信息", v.get_info())


def format_subtitle_timeline(
    raw: list[dict[str, Any]] | None,
    output_format: str = "timeline",
) -> str:
    """Format subtitle items with timestamps."""
    if not raw:
        return ""

    if output_format == "srt":
        lines: list[str] = []
        for index, item in enumerate(raw, 1):
            lines.append(str(index))
            lines.append(
                f"{_format_subtitle_srt_time(item.get('from', 0.0))} --> "
                f"{_format_subtitle_srt_time(item.get('to', 0.0))}"
            )
            lines.append(item.get("content", ""))
            lines.append("")
        return "\n".join(lines)

    return "\n".join(
        (
            f"[{_format_subtitle_time(item.get('from', 0.0))} --> "
            f"{_format_subtitle_time(item.get('to', 0.0))}] "
            f"{item.get('content', '')}"
        )
        for item in raw
    )


def _format_subtitle_time(seconds: float) -> str:
    """Format seconds as MM:SS.mmm."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes:02d}:{secs:06.3f}"


def _format_subtitle_srt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SRT output."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")


async def get_video_subtitle(
    bvid: str, credential: Credential | None = None
) -> tuple[str, list]:
    """Fetch video subtitle content.

    Returns (plain_text, raw_subtitle_items).
    An empty tuple element means no subtitle available.
    """
    v = video.Video(bvid=bvid, credential=credential)

    # Get cid from first page
    pages = await _call_api("获取视频分P信息", v.get_pages())
    if not pages:
        logger.warning("No pages found for %s", bvid)
        return "", []

    cid = pages[0].get("cid")
    if not cid:
        logger.warning("No cid found for %s", bvid)
        return "", []

    # Get subtitle list from player info
    player_info = await _call_api("获取播放器信息", v.get_player_info(cid=cid))
    subtitle_info = player_info.get("subtitle", {})

    if not subtitle_info or not subtitle_info.get("subtitles"):
        return "", []

    subtitle_list = subtitle_info["subtitles"]

    # Prefer Chinese subtitles
    subtitle_url = None
    for sub in subtitle_list:
        if "zh" in sub.get("lan", "").lower():
            subtitle_url = sub.get("subtitle_url", "")
            break

    if not subtitle_url and subtitle_list:
        subtitle_url = subtitle_list[0].get("subtitle_url", "")

    if not subtitle_url:
        return "", []

    # Ensure absolute URL
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url

    # Download subtitle JSON
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(subtitle_url) as resp:
                resp.raise_for_status()
                subtitle_data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise NetworkError(f"下载字幕失败: {e}") from e

    if "body" in subtitle_data:
        raw = subtitle_data["body"]
        texts = [item.get("content", "") for item in raw]
        return "\n".join(texts), raw

    return "", []


async def get_video_subtitle_detailed(
    bvid: str, credential: Credential | None = None
) -> dict[str, Any]:
    """Fetch subtitle with an explicit machine-readable reason when unavailable.

    Unlike `get_video_subtitle`, this never collapses "not logged in" and
    "video has no subtitle track" into the same empty result.

    reason ∈ {ok, not_authenticated, no_subtitle_track, no_pages, download_failed, error}
    """
    result: dict[str, Any] = {
        "available": False,
        "reason": "error",
        "message": "",
        "text": "",
        "items": [],
    }

    v = video.Video(bvid=bvid, credential=credential)

    try:
        pages = await _call_api("获取视频分P信息", v.get_pages())
    except BiliError as e:
        result.update(reason="error", message=str(e))
        return result

    if not pages:
        result.update(reason="no_pages", message="未取到分P信息")
        return result

    cid = pages[0].get("cid")
    if not cid:
        result.update(reason="no_pages", message="未取到 cid")
        return result

    try:
        player_info = await _call_api("获取播放器信息", v.get_player_info(cid=cid))
    except AuthenticationError as e:
        result.update(reason="not_authenticated", message=str(e))
        return result
    except BiliError as e:
        result.update(reason="error", message=str(e))
        return result

    subtitle_info = player_info.get("subtitle") or {}
    subtitle_list = subtitle_info.get("subtitles") or []
    if not subtitle_list:
        result.update(reason="no_subtitle_track", message="该视频没有字幕轨")
        return result

    subtitle_url = ""
    for sub in subtitle_list:
        if "zh" in str(sub.get("lan", "")).lower():
            subtitle_url = sub.get("subtitle_url", "")
            break
    if not subtitle_url:
        subtitle_url = subtitle_list[0].get("subtitle_url", "")
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    if not subtitle_url:
        result.update(reason="no_subtitle_track", message="字幕轨缺少下载地址")
        return result

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(subtitle_url) as resp:
                resp.raise_for_status()
                subtitle_data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        result.update(reason="download_failed", message=f"下载字幕失败: {e}")
        return result

    raw = subtitle_data.get("body") or []
    if not raw:
        result.update(reason="no_subtitle_track", message="字幕内容为空")
        return result

    texts = [item.get("content", "") for item in raw]
    result.update(
        available=True,
        reason="ok",
        message="",
        text="\n".join(texts),
        items=raw,
    )
    return result


async def get_video_danmakus(
    bvid: str, limit: int = 100, page_index: int = 0
) -> list[dict[str, Any]]:
    """Fetch danmaku (bullet comments) for a video."""
    v = video.Video(bvid=bvid)
    raw = await _call_api("获取弹幕", v.get_danmakus(page_index=page_index))

    items: list[dict[str, Any]] = []
    for d in raw or []:
        text = getattr(d, "text", "") or ""
        if not text:
            continue
        items.append(
            {
                "time": round(float(getattr(d, "dm_time", 0.0) or 0.0), 2),
                "mode": getattr(d, "mode", None),
                "text": text,
            }
        )
        if len(items) >= limit:
            break
    return items


async def get_video_storyboard(bvid: str, page_index: int = 0) -> dict[str, Any]:
    """Fetch storyboard (videoshot) metadata used for frame extraction.

    Returns the raw payload: image sheets, per-frame timestamps and grid size.
    """
    v = video.Video(bvid=bvid)
    pages = await _call_api("获取视频分P信息", v.get_pages())
    if not pages:
        raise NotFoundError(f"未取到分P信息: {bvid}")

    index = min(max(page_index, 0), len(pages) - 1)
    cid = pages[index].get("cid")
    if not cid:
        raise NotFoundError(f"未取到 cid: {bvid}")

    api = "https://api.bilibili.com/x/player/videoshot"
    params = {"bvid": bvid, "cid": cid, "index": 1}
    headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.bilibili.com"}

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(api, params=params) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        raise NetworkError(f"获取故事板失败: {e}") from e

    if payload.get("code") != 0:
        raise BiliError(f"获取故事板失败: [{payload.get('code')}] {payload.get('message')}")

    data = payload.get("data") or {}
    if not data.get("image"):
        raise NotFoundError("该视频没有故事板（可能过短或未生成）")
    return data


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


async def get_user_info(uid: int, credential: Credential | None = None) -> dict[str, Any]:
    """Fetch user profile information."""
    u = user.User(uid=uid, credential=credential)
    return await _call_api("获取用户信息", u.get_user_info())


async def get_user_relation_info(uid: int, credential: Credential | None = None) -> dict[str, Any]:
    """Fetch user relation stats (follower count, following count)."""
    u = user.User(uid=uid, credential=credential)
    return await _call_api("获取用户关系信息", u.get_relation_info())


async def get_user_videos(
    uid: int, count: int = 10, credential: Credential | None = None
) -> list[dict[str, Any]]:
    """Fetch a user's latest videos.

    Returns list of video dicts (bvid, title, play, length, etc.).
    """
    u = user.User(uid=uid, credential=credential)

    results: list[dict[str, Any]] = []
    page = 1
    per_page = min(count, 50)

    while len(results) < count:
        try:
            data = await _call_api("获取用户视频列表", u.get_videos(ps=per_page, pn=page))
        except BiliError as e:
            if page == 1:
                raise
            logger.warning("Failed to get videos page %d: %s", page, e)
            break

        vlist = data.get("list", {}).get("vlist", [])
        if not vlist:
            break

        for v in vlist:
            results.append(v)
            if len(results) >= count:
                break

        page += 1
        if page > 20:
            break

    return results


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def search_user(keyword: str, page: int = 1) -> list[dict[str, Any]]:
    """Search for users by keyword.

    Returns list of user result dicts.
    """
    res = await _call_api("搜索用户", search.search_by_type(
        keyword=keyword,
        search_type=search.SearchObjectType.USER,
        page=page,
    ))
    return res.get("result", [])


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------


async def get_self_info(credential: Credential) -> dict[str, Any]:
    """Get logged-in user's own info."""
    return await _call_api("获取当前登录用户信息", user.get_self_info(credential))


async def get_favorite_list(credential: Credential) -> list[dict[str, Any]]:
    """List all favorite folders for the logged-in user."""
    me = await get_self_info(credential)
    uid = me.get("mid")
    if uid is None:
        raise BiliError("获取收藏夹列表: 当前用户信息缺少 mid")

    fav_data = await _call_api(
        "获取收藏夹列表",
        favorite_list.get_video_favorite_list(uid=uid, credential=credential),
    )
    return fav_data.get("list", [])


async def get_favorite_videos(
    fav_id: int, credential: Credential, page: int = 1
) -> dict[str, Any]:
    """Get videos in a specific favorite folder.

    Returns the raw response dict with 'medias', 'has_more', etc.
    """
    return await _call_api(
        "获取收藏夹内容",
        favorite_list.get_video_favorite_list_content(
            media_id=fav_id, page=page, credential=credential
        ),
    )


# ---------------------------------------------------------------------------
# Hot & Rank
# ---------------------------------------------------------------------------


async def get_hot_videos(pn: int = 1, ps: int = 20) -> dict[str, Any]:
    """Fetch popular/hot videos."""
    return await _call_api("获取热门视频", hot.get_hot_videos(pn=pn, ps=ps))


_RANK_TYPE_NAMES = {
    "all": "All",
    "bangumi": "Bangumi",
    "guochuang": "Guochuang",
    "documentary": "Documentary",
    "douga": "Douga",
    "music": "Music",
    "dance": "Dance",
    "game": "Game",
    "knowledge": "Knowledge",
    "tech": "Technology",
    "sports": "Sports",
    "car": "Car",
    "life": "Life",
    "food": "Food",
    "animal": "Animal",
    "kichiku": "Kichiku",
    "fashion": "Fashion",
    "ent": "Ent",
    "cinephile": "Cinephile",
    "movie": "Movie",
    "tv": "TV",
    "variety": "Variety",
    "original": "Original",
    "rookie": "Rookie",
}

# Built defensively: upstream enum names drift between releases.
_RANK_TYPES = {
    key: getattr(rank.RankType, attr)
    for key, attr in _RANK_TYPE_NAMES.items()
    if hasattr(rank.RankType, attr)
}
RANK_TYPE_KEYS = tuple(_RANK_TYPES)


def resolve_rank_type(name: str | None):
    """Map a CLI-friendly zone name to the upstream RankType enum."""
    if not name:
        return rank.RankType.All
    key = name.strip().lower()
    if key not in _RANK_TYPES:
        raise InvalidBvidError(
            f"不支持的排行榜分区: {name}（可选: {', '.join(_RANK_TYPES)}）"
        )
    return _RANK_TYPES[key]


async def get_rank_videos(day: int = 3, type_: str | None = None) -> dict[str, Any]:
    """Fetch ranking videos (default: 3-day rank).

    Passing an "all" zone keeps the original call shape (`get_rank(day=...)`),
    which is also what upstream defaults to.
    """
    day_type = rank.RankDayType.THREE_DAY if day == 3 else rank.RankDayType.WEEK
    resolved = resolve_rank_type(type_)
    if resolved is rank.RankType.All:
        return await _call_api("获取排行榜", rank.get_rank(day=day_type))
    return await _call_api("获取排行榜", rank.get_rank(type_=resolved, day=day_type))


# ---------------------------------------------------------------------------
# Video extras
# ---------------------------------------------------------------------------


async def _get_video_comments_direct(
    aid: int,
    bvid: str,
    page: int,
    credential: Credential | None = None,
) -> dict[str, Any]:
    """Fallback direct API call for video comments when SDK returns empty."""
    api_url = "https://api.bilibili.com/x/v2/reply"
    params = {
        "oid": aid,
        "type": 1,
        "pn": page,
        "ps": 20,
        "sort": 2,  # hot/popular
    }
    headers = {
        "User-Agent": _USER_AGENT,
        "Origin": "https://www.bilibili.com",
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "sec-ch-ua": '"Chromium";v="133", "Not(A:Brand";v="99", "Google Chrome";v="133"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }
    if credential and credential.sessdata:
        cookies = [f"SESSDATA={credential.sessdata}"]
        if credential.bili_jct:
            cookies.append(f"bili_jct={credential.bili_jct}")
        headers["Cookie"] = "; ".join(cookies)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(api_url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    if payload.get("code") != 0:
        raise BiliError(
            f"获取视频评论: [{payload.get('code')}] {payload.get('message', 'Unknown error')}"
        )
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


async def get_video_comments(
    bvid: str, page: int = 1, credential: Credential | None = None
) -> dict[str, Any]:
    """Fetch video comments with SDK-first + direct-API fallback strategy."""
    v = video.Video(bvid=bvid, credential=credential)
    info = await _call_api("获取视频信息", v.get_info())
    aid = info.get("aid")
    if aid is None:
        raise BiliError("获取视频评论: 视频信息缺少 aid")

    sdk_result: dict[str, Any] | None = None
    try:
        sdk_result = await _call_api(
            "获取视频评论",
            comment.get_comments(
                oid=aid,
                type_=comment.CommentResourceType.VIDEO,
                page_index=page,
                order=comment.OrderType.LIKE,
                credential=credential,
            ),
        )
    except BiliError as exc:
        logger.warning("SDK comment fetch failed, fallback to direct API: %s", exc)

    if isinstance(sdk_result, dict) and sdk_result.get("replies"):
        return sdk_result

    try:
        direct_result = await _call_api(
            "获取视频评论",
            _get_video_comments_direct(aid=aid, bvid=bvid, page=page, credential=credential),
        )
        if isinstance(direct_result, dict):
            return direct_result
    except BiliError as exc:
        if isinstance(sdk_result, dict) and sdk_result.get("replies"):
            logger.warning("Direct comment fallback failed, return non-empty SDK result: %s", exc)
            return sdk_result
        logger.warning("Direct comment fallback failed after SDK empty/error: %s", exc)
        raise

    if isinstance(sdk_result, dict):
        return sdk_result
    return {}


async def get_video_ai_conclusion(
    bvid: str, credential: Credential | None = None
) -> dict[str, Any]:
    """Fetch AI-generated video summary."""
    v = video.Video(bvid=bvid, credential=credential)
    pages = await _call_api("获取视频分P信息", v.get_pages())
    if not pages:
        return {}
    cid = pages[0].get("cid")
    if not cid:
        return {}
    return await _call_api("获取 AI 总结", v.get_ai_conclusion(cid=cid))


async def get_related_videos(
    bvid: str, credential: Credential | None = None
) -> list[dict[str, Any]]:
    """Fetch related/recommended videos."""
    v = video.Video(bvid=bvid, credential=credential)
    data = await _call_api("获取相关推荐", v.get_related())
    if isinstance(data, list):
        return data
    return []


# ---------------------------------------------------------------------------
# Search (video)
# ---------------------------------------------------------------------------


_VIDEO_ORDER_ALIASES = {
    "totalrank": search.OrderVideo.TOTALRANK,
    "click": search.OrderVideo.CLICK,
    "pubdate": search.OrderVideo.PUBDATE,
    "dm": search.OrderVideo.DM,
    "stow": search.OrderVideo.STOW,
    "scores": search.OrderVideo.SCORES,
}

# The upstream library treats time_range as a *minute* count and buckets it.
_DURATION_MINUTES = {
    "lt10": 5,
    "10-30": 20,
    "30-60": 45,
    "gt60": 90,
}

DEFAULT_SEARCH_PAGE_SIZE = 42


def resolve_video_order(order: str | None):
    """Map a CLI-friendly order name to the upstream enum."""
    if not order:
        return None
    key = order.strip().lower()
    if key not in _VIDEO_ORDER_ALIASES:
        raise InvalidBvidError(
            f"不支持的排序方式: {order}（可选: {', '.join(_VIDEO_ORDER_ALIASES)}）"
        )
    return _VIDEO_ORDER_ALIASES[key]


async def search_video(
    keyword: str,
    page: int = 1,
    *,
    order: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    duration: str | None = None,
    tid: int | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]]:
    """Search for videos by keyword, with optional ordering and filters.

    Keyword-only extras are only forwarded when provided so that the plain
    call shape (`search_video(keyword, page=N)`) stays unchanged.
    """
    params: dict[str, Any] = {
        "keyword": keyword,
        "search_type": search.SearchObjectType.VIDEO,
        "page": page,
    }

    resolved_order = resolve_video_order(order)
    if resolved_order is not None:
        params["order_type"] = resolved_order
    if time_start:
        params["time_start"] = time_start
    if time_end:
        params["time_end"] = time_end
    if duration:
        key = duration.strip().lower()
        if key not in _DURATION_MINUTES:
            raise InvalidBvidError(
                f"不支持的时长筛选: {duration}（可选: {', '.join(_DURATION_MINUTES)}）"
            )
        params["time_range"] = _DURATION_MINUTES[key]
    if tid is not None:
        params["video_zone_type"] = tid
    if page_size:
        params["page_size"] = page_size

    res = await _call_api("搜索视频", search.search_by_type(**params))
    return res.get("result", [])


async def search_video_paged(
    keyword: str,
    count: int = 20,
    page: int = 1,
    *,
    order: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    duration: str | None = None,
    tid: int | None = None,
    page_size: int = DEFAULT_SEARCH_PAGE_SIZE,
    max_pages: int = 10,
    exclude_bvids: set[str] | None = None,
) -> dict[str, Any]:
    """Search videos and page through results until `count` items are collected.

    `exclude_bvids` is applied while paging, so filtered results still let the
    loop walk forward until enough *new* videos are found.

    Returns {"items": [...], "meta": {...}} where meta records whether the
    requested count was actually reached (so callers never silently truncate).
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    known = exclude_bvids or set()
    skipped_seen = 0
    current = page
    pages_fetched = 0
    exhausted = False

    extras: dict[str, Any] = {}
    if order:
        extras["order"] = order
    if time_start:
        extras["time_start"] = time_start
    if time_end:
        extras["time_end"] = time_end
    if duration:
        extras["duration"] = duration
    if tid is not None:
        extras["tid"] = tid
    if page_size and page_size != DEFAULT_SEARCH_PAGE_SIZE:
        extras["page_size"] = page_size

    while len(items) < count and pages_fetched < max_pages:
        batch = await search_video(keyword, page=current, **extras)
        pages_fetched += 1
        if not batch:
            exhausted = True
            break

        for item in batch:
            bvid = item.get("bvid", "")
            if not bvid or bvid in seen:
                continue
            if bvid in known:
                skipped_seen += 1
                continue
            seen.add(bvid)
            items.append(item)
            if len(items) >= count:
                break

        # A short page means this was the last page — stop instead of
        # re-requesting the same tail over and over.
        if len(batch) < page_size:
            exhausted = True
            break

        current += 1

    trimmed = items[:count]
    return {
        "items": trimmed,
        "meta": {
            "requested": count,
            "returned": len(trimmed),
            "pages_fetched": pages_fetched,
            "page_start": page,
            "page_size": page_size,
            "order": order or "totalrank",
            "since": time_start,
            "until": time_end,
            "duration": duration,
            "tid": tid,
            "truncated": len(trimmed) < count,
            "exhausted": exhausted,
            "hit_page_cap": pages_fetched >= max_pages and len(trimmed) < count,
            "skipped_seen": skipped_seen,
        },
    }


# ---------------------------------------------------------------------------
# Following & Toview
# ---------------------------------------------------------------------------


async def get_followings(
    uid: int, pn: int = 1, ps: int = 20, credential: Credential | None = None
) -> dict[str, Any]:
    """Fetch user's following list."""
    u = user.User(uid=uid, credential=credential)
    return await _call_api("获取关注列表", u.get_followings(pn=pn, ps=ps))


async def modify_user_relation(
    uid: int,
    relation: user.RelationType,
    credential: Credential,
) -> dict[str, Any]:
    """Modify relation to a user (subscribe/unsubscribe/block...)."""
    u = user.User(uid=uid, credential=credential)
    return await _call_api("修改用户关系", u.modify_relation(relation=relation))


async def unfollow_user(uid: int, credential: Credential) -> dict[str, Any]:
    """Unfollow a user by UID."""
    return await modify_user_relation(
        uid=uid,
        relation=user.RelationType.UNSUBSCRIBE,
        credential=credential,
    )


async def get_watch_history(
    page: int = 1, count: int = 30, credential: Credential | None = None
) -> dict[str, Any]:
    """Fetch watch history (观看历史)."""
    if credential is None:
        raise AuthenticationError("credential is required for watch history")
    per_page = max(1, min(count, 100))
    return await _call_api(
        "获取观看历史",
        user.get_self_history(page_num=page, per_page_item=per_page, credential=credential),
    )


async def get_toview(credential: Credential) -> dict[str, Any]:
    """Fetch watch-later (稍后再看) list."""
    data = await _call_api("获取稍后再看列表", homepage.get_favorite_list_and_toview(credential))
    if not isinstance(data, list):
        logger.warning("Unexpected toview payload type: %s", type(data).__name__)
        return {"list": [], "count": 0}
    # data is a list; the item with name="稍后再看" contains toview videos
    for item in data:
        if item.get("name") == "稍后再看" or item.get("id") == 2:
            resp = item.get("mediaListResponse", {})
            return {
                "list": resp.get("list", []),
                "count": resp.get("count", 0),
            }
    return {"list": [], "count": 0}


# ---------------------------------------------------------------------------
# Dynamic Feed
# ---------------------------------------------------------------------------


async def get_dynamic_feed(
    offset: str | int | None = "", credential: Credential | None = None
) -> dict[str, Any]:
    """Fetch dynamic feed (动态时间线)."""
    if credential is None:
        raise AuthenticationError("credential is required for dynamic feed")
    if offset in ("", None):
        parsed_offset = None
    elif isinstance(offset, int):
        parsed_offset = offset
    elif isinstance(offset, str):
        try:
            parsed_offset = int(offset)
        except ValueError as e:
            raise BiliError(f"获取动态时间线: offset 非法: {offset}") from e
    else:
        raise BiliError(f"获取动态时间线: offset 类型不支持: {type(offset).__name__}")

    return await _call_api(
        "获取动态时间线",
        dynamic.get_dynamic_page_info(
            credential=credential,
            pn=1,
            offset=parsed_offset,
        ),
    )


async def post_text_dynamic(text: str, credential: Credential) -> dict[str, Any]:
    """Publish a plain-text dynamic."""
    content = text.strip()
    if not content:
        raise BiliError("发布动态: 文本不能为空")
    info = dynamic.BuildDynamic.empty().add_text(content)
    return await _call_api(
        "发布动态",
        dynamic.send_dynamic(info=info, credential=credential),
    )


async def get_user_dynamics(
    uid: int,
    offset: int = 0,
    need_top: bool = False,
    credential: Credential | None = None,
) -> dict[str, Any]:
    """Fetch a user's own dynamics timeline."""
    u = user.User(uid=uid, credential=credential)
    return await _call_api(
        "获取用户动态",
        u.get_dynamics(offset=offset, need_top=need_top),
    )


async def delete_dynamic(dynamic_id: int, credential: Credential) -> dict[str, Any]:
    """Delete a dynamic by dynamic id."""
    d = dynamic.Dynamic(dynamic_id=dynamic_id, credential=credential)
    return await _call_api("删除动态", d.delete())


# ---------------------------------------------------------------------------
# Interactions (like, coin, triple)
# ---------------------------------------------------------------------------


async def like_video(bvid: str, credential: Credential, undo: bool = False) -> dict[str, Any]:
    """Like or unlike a video."""
    v = video.Video(bvid=bvid, credential=credential)
    return await _call_api("点赞视频", v.like(status=not undo))


async def coin_video(bvid: str, credential: Credential, num: int = 1) -> dict[str, Any]:
    """Give coins to a video (1 or 2)."""
    v = video.Video(bvid=bvid, credential=credential)
    return await _call_api("投币", v.pay_coin(num=num))


async def triple_video(bvid: str, credential: Credential) -> dict[str, Any]:
    """Triple (like + coin + favorite) a video."""
    v = video.Video(bvid=bvid, credential=credential)
    return await _call_api("一键三连", v.triple())


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

_DOWNLOAD_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Referer": "https://www.bilibili.com",
}


async def get_audio_url(bvid: str, credential: Credential | None = None) -> str:
    """Get the best audio stream URL for a video.

    Reads the DASH manifest directly instead of going through upstream's
    `detect_best_streams`, which crashes on unauthenticated/no-video-stream
    responses (None entries in its sort key).
    """
    v = video.Video(bvid=bvid, credential=credential)
    download_data = await _call_api("获取下载地址", v.get_download_url(page_index=0))

    dash = download_data.get("dash") or {}
    audios = dash.get("audio") or []
    if audios:
        best = max(audios, key=lambda a: a.get("bandwidth", 0) or 0)
        url = best.get("baseUrl") or best.get("base_url") or ""
        if url:
            return url

    # FLV / MP4 fallback (durl) — audio is muxed into the single file.
    durl = download_data.get("durl") or []
    if durl and isinstance(durl[0], dict) and durl[0].get("url"):
        return durl[0]["url"]

    raise BiliError("无法获取音频流（可能是会员专属或付费视频）")


async def download_audio(audio_url: str, output_path: str) -> int:
    """Download audio stream to a file. Returns bytes written."""
    timeout = aiohttp.ClientTimeout(total=300)
    max_retries = 3

    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(audio_url, headers=_DOWNLOAD_HEADERS) as resp:
                    if resp.status == 200:
                        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                        total_bytes = 0
                        with open(output_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(256 * 1024):
                                if not chunk:
                                    continue
                                f.write(chunk)
                                total_bytes += len(chunk)
                        return total_bytes
                    if attempt < max_retries - 1:
                        logger.warning("Download HTTP %d, retrying...", resp.status)
                        await asyncio.sleep(2)
                    else:
                        raise NetworkError(f"音频下载失败: HTTP {resp.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                logger.warning("Download error: %s, retrying...", e)
                await asyncio.sleep(2)
            else:
                raise NetworkError(f"音频下载失败: {e}") from e

    raise NetworkError("音频下载失败: 重试次数用尽")


def split_audio(input_path: str, output_dir: str, segment_seconds: int = 25) -> list[str]:
    """Split audio into WAV segments using PyAV.

    Returns list of segment file paths.
    Each segment is 16kHz mono PCM s16le WAV — ready for ASR APIs.
    """
    try:
        import av as pyav
    except ImportError:
        raise BiliError(
            "音频切分需要 PyAV 库。请安装: pip install av\n"
            "或: uv add av"
        ) from None

    if segment_seconds <= 0:
        raise BiliError("segment_seconds 必须大于 0")

    os.makedirs(output_dir, exist_ok=True)

    def _write_segment(frames: list, seg_idx: int) -> str:
        seg_path = os.path.join(output_dir, f"seg_{seg_idx:03d}.wav")
        out = None
        try:
            out = pyav.open(seg_path, "w", format="wav")
            out_stream = out.add_stream("pcm_s16le", rate=16000, layout="mono")
            resampler = pyav.AudioResampler(format="s16", layout="mono", rate=16000)
            for fr in frames:
                fr.pts = None
                for resampled in resampler.resample(fr):
                    for pkt in out_stream.encode(resampled):
                        out.mux(pkt)
            for pkt in out_stream.encode():
                out.mux(pkt)
        finally:
            if out is not None:
                out.close()
        return seg_path

    input_container = None
    try:
        input_container = pyav.open(input_path)
        if not input_container.streams.audio:
            raise BiliError("音频解码失败: 无音频流")

        chunk_paths = []
        current_samples = 0
        segment_frames: list = []
        seg_idx = 0
        samples_per_segment = None
        decoded_any = False

        for frame in input_container.decode(audio=0):
            decoded_any = True
            if samples_per_segment is None:
                frame_rate = frame.sample_rate or 16000
                samples_per_segment = segment_seconds * frame_rate

            segment_frames.append(frame)
            current_samples += frame.samples or 0

            if samples_per_segment and current_samples >= samples_per_segment:
                chunk_paths.append(_write_segment(segment_frames, seg_idx))
                seg_idx += 1
                current_samples = 0
                segment_frames = []

        if not decoded_any:
            raise BiliError("音频解码失败: 无帧数据")

        if segment_frames:
            chunk_paths.append(_write_segment(segment_frames, seg_idx))

        return chunk_paths
    finally:
        if input_container is not None:
            input_container.close()
