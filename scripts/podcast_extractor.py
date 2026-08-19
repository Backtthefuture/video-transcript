#!/usr/bin/env python3
"""小宇宙播客单集解析：episode URL → 标题 / 音频直链 / Shownotes。

解析策略:
1. 首选 episode 页 HTML 里的 __NEXT_DATA__ JSON(不依赖会失效的 BUILD_ID)
2. 兜底 og:audio / og:title meta 标签

CLI: python3 podcast_extractor.py <episode_url>  → 输出 JSON
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sys
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

EPISODE_RE = re.compile(r"xiaoyuzhoufm\.com/episode/([0-9a-fA-F]+)")


def is_xiaoyuzhou_episode(url: str) -> bool:
    return bool(EPISODE_RE.search(url or ""))


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_html(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html_mod.unescape(text or ""))
    return re.sub(r"\s+", " ", t).strip()


def _meta_content(html_text: str, prop: str) -> str:
    m = re.search(
        rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]+)"',
        html_text,
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content="([^"]+)"[^>]+(?:property|name)="{re.escape(prop)}"',
            html_text,
        )
    return html_mod.unescape(m.group(1)) if m else ""


def _parse_next_data(html_text: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.S
    )
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    props = data.get("props") or {}
    page_props = props.get("pageProps") or {}
    return page_props.get("episode") or {}


def extract_episode(url: str) -> dict:
    """返回 {eid, title, podcast, audio_url, duration_sec, shownotes_text, url}."""
    m = EPISODE_RE.search(url or "")
    eid = m.group(1) if m else ""
    clean_url = f"https://www.xiaoyuzhoufm.com/episode/{eid}" if eid else url

    html_text = _fetch(clean_url)
    info: dict = {
        "eid": eid,
        "title": "",
        "podcast": "",
        "audio_url": "",
        "duration_sec": 0,
        "shownotes_text": "",
        "url": clean_url,
    }

    ep = _parse_next_data(html_text)
    if ep:
        info["title"] = str(ep.get("title") or "")
        info["duration_sec"] = int(ep.get("duration") or 0)
        info["shownotes_text"] = strip_html(ep.get("shownotes") or "")[:4000]
        media = ep.get("media") or {}
        source = media.get("source") or {}
        info["audio_url"] = str(source.get("url") or media.get("url") or "")
        podcast = ep.get("podcast") or {}
        info["podcast"] = str(podcast.get("title") or "")

    if not info["audio_url"]:
        info["audio_url"] = _meta_content(html_text, "og:audio")
    if not info["title"]:
        title = _meta_content(html_text, "og:title")
        info["title"] = re.sub(r"\s*[|｜].*?小宇宙.*$", "", title).strip()
    if not info["shownotes_text"]:
        info["shownotes_text"] = _meta_content(html_text, "og:description")[:4000]

    if not info["audio_url"]:
        raise RuntimeError("未解析到音频直链(页面结构可能已变化)")
    return info


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: podcast_extractor.py <xiaoyuzhou_episode_url>", file=sys.stderr)
        return 2
    try:
        info = extract_episode(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 - CLI surface
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
