#!/usr/bin/env python3
"""
平台视频 URL 提取器(headless 浏览器后台模式)。

抖音 / 小红书 没有现成纯 Python 库,通过 Playwright headless=True 后台运行,
对用户完全透明:不弹窗、不需要登录、不需要扫码。

返回字典:
  {
    "platform": "douyin" | "xiaohongshu",
    "title":    视频标题,
    "video_url": 直链 mp4 URL,
    "headers":  curl 下载需要的 HTTP 头(主要是 Referer/User-Agent),
    "duration": 时长秒数(可能 None),
  }
"""
import json
import re
import sys
from typing import Optional

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[ERROR] 缺少依赖: pip install --break-system-packages playwright "
          "&& python3 -m playwright install chromium", file=sys.stderr)
    raise


DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/16.6 Mobile/15E148 Safari/604.1")


# ─── 小红书 ──────────────────────────────────────────────────────

def extract_xiaohongshu(url: str, headless: bool = True) -> dict:
    """从小红书页面提取视频直链。
    PC 端会强制跳转登录页,所以用 mobile UA 绕过。
    """
    captured = []

    def on_response(resp):
        u = resp.url
        ct = resp.headers.get("content-type", "")
        if (".mp4" in u or "video/" in ct) and "xhscdn" in u:
            captured.append(u)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=MOBILE_UA,
            viewport={"width": 390, "height": 844},
            is_mobile=True, has_touch=True, locale="zh-CN",
        )
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        # 先尝试从 __INITIAL_STATE__ 拿(更稳),拿不到再 fallback 到网络流量
        info = page.evaluate(
            """() => {
                const out = { title: document.title };
                try {
                    const state = window.__INITIAL_STATE__;
                    if (!state || !state.note || !state.note.noteDetailMap) return out;
                    const map = state.note.noteDetailMap;
                    for (const k of Object.keys(map)) {
                        if (map[k] && map[k].note) {
                            const n = map[k].note;
                            if (n.type === 'video' && n.video && n.video.media && n.video.media.stream) {
                                const h264 = n.video.media.stream.h264;
                                if (h264 && h264[0]) {
                                    out.state_url = h264[0].masterUrl;
                                    out.duration = Math.round((h264[0].duration || 0) / 1000);
                                    out.title = n.title || out.title;
                                }
                            }
                            break;
                        }
                    }
                } catch (e) { out.err = e.message; }
                return out;
            }"""
        )

        # 触发播放,让网络流量出现
        try:
            page.evaluate("document.querySelector('video') && document.querySelector('video').play()")
        except Exception:
            pass
        page.wait_for_timeout(8000)

        # 没拿到 duration 的话,从 video 元素的 duration 属性兜底
        if not info.get("duration"):
            try:
                v_dur = page.evaluate(
                    "() => { const v = document.querySelector('video'); return v && Number.isFinite(v.duration) ? Math.round(v.duration) : null; }"
                )
                if v_dur:
                    info["duration"] = v_dur
            except Exception:
                pass

        title = info.get("title") or page.title() or "xhs_video"
        title = re.sub(r"\s*-\s*小红书\s*$", "", title).strip()
        browser.close()

    video_url = info.get("state_url") or (captured[0] if captured else None)
    if not video_url:
        raise RuntimeError("小红书:未抓到视频 URL,可能不是视频笔记或页面加载异常")

    return {
        "platform": "xiaohongshu",
        "title": title,
        "video_url": video_url,
        "duration": info.get("duration"),
        "headers": {},  # xhscdn 完全公开
    }


# ─── 抖音 ────────────────────────────────────────────────────────

def _douyin_aweme_id(url: str) -> Optional[str]:
    m = re.search(r"modal_id=(\d+)", url) or re.search(r"/video/(\d+)", url)
    return m.group(1) if m else None


def extract_douyin(url: str, headless: bool = True) -> dict:
    """从抖音页面提取视频直链。
    通过浏览器同源 fetch 调用 detail API(自带 cookie),拿到 play_addr。
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=DESKTOP_UA,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        # 从最终 URL 提取 aweme_id(短链会重定向)
        aweme_id = _douyin_aweme_id(page.url) or _douyin_aweme_id(url)
        if not aweme_id:
            browser.close()
            raise RuntimeError("抖音:无法从 URL 提取 aweme_id")

        api_result = page.evaluate(
            """async (awemeId) => {
                const apiUrl = 'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=' + awemeId + '&device_platform=webapp&aid=6383';
                try {
                    const resp = await fetch(apiUrl, {
                        method: 'GET',
                        credentials: 'include',
                        headers: { 'Accept': 'application/json', 'Referer': 'https://www.douyin.com/' }
                    });
                    const data = await resp.json();
                    if (data && data.aweme_detail && data.aweme_detail.video) {
                        const v = data.aweme_detail.video;
                        const playAddr = v.play_addr || v.play_addr_h264;
                        return {
                            success: true,
                            title: data.aweme_detail.desc || '',
                            video_urls: playAddr ? playAddr.url_list : [],
                            duration_ms: v.duration || 0,
                        };
                    }
                    return { success: false, msg: JSON.stringify(data).substring(0, 300) };
                } catch (e) {
                    return { success: false, error: e.message };
                }
            }""",
            aweme_id,
        )
        page_title = page.title()
        browser.close()

    if not api_result.get("success"):
        raise RuntimeError(f"抖音 detail API 调用失败: {api_result}")
    urls = api_result.get("video_urls") or []
    if not urls:
        raise RuntimeError("抖音:detail API 未返回视频地址")

    # 优先 zjcdn / douyinvod CDN(直链),play API 也可用作 fallback
    video_url = urls[0]
    title = (api_result.get("title") or page_title or "douyin_video").strip()
    title = re.sub(r"\s*-\s*抖音\s*$", "", title).strip()

    return {
        "platform": "douyin",
        "title": title,
        "video_url": video_url,
        "duration": round(api_result.get("duration_ms", 0) / 1000) or None,
        "headers": {
            "Referer": "https://www.douyin.com/",
            "User-Agent": DESKTOP_UA,
        },
    }


# ─── B 站 ────────────────────────────────────────────────────────

def extract_bilibili(url: str, headless: bool = True) -> dict:
    """B 站:从 window.__playinfo__ 提取 dash 流的 video/audio baseUrl。
    返回的两个 URL 需要分别下载后 ffmpeg 合并(audio + video m4s)。
    yt-dlp 412 时由 transcript.py 自动调用作为 fallback。
    """
    JS_PROBE = """() => {
        const out = { has_playinfo: !!window.__playinfo__, current_url: window.location.href, has_video_el: !!document.querySelector('video') };
        const pi = window.__playinfo__ && window.__playinfo__.data;
        if (pi && pi.dash) {
            const dash = pi.dash;
            const pickedVideo =
                dash.video.find(v => v.id === 64) ||
                dash.video.find(v => v.id === 32) ||
                dash.video.find(v => v.id === 16) ||
                dash.video[0];
            const pickedAudio = dash.audio && dash.audio[0];
            if (pickedVideo && pickedAudio) {
                out.video_url = pickedVideo.baseUrl;
                out.audio_url = pickedAudio.baseUrl;
                out.duration = dash.duration;
                out.video_id = pickedVideo.id;
            }
        }
        out.title = document.title.replace(/_哔哩哔哩.*$/, '').replace(/\\s*-\\s*哔哩哔哩.*$/, '').trim();
        return out;
    }"""

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=DESKTOP_UA,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            extra_http_headers={"Referer": "https://www.bilibili.com/"},
        )
        # 反 webdriver 检测
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = ctx.new_page()

        info = None
        for attempt in range(3):
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception as e:
                print(f"# goto attempt {attempt+1} timeout/err: {e}", file=sys.stderr)
            page.wait_for_timeout(2500)
            try:
                page.wait_for_function(
                    "window.__playinfo__ && window.__playinfo__.data && window.__playinfo__.data.dash",
                    timeout=8000,
                )
            except Exception:
                pass
            info = page.evaluate(JS_PROBE)
            if info.get("video_url") and info.get("audio_url"):
                break
            print(f"# attempt {attempt+1}: has_playinfo={info.get('has_playinfo')} url={info.get('current_url')}",
                  file=sys.stderr)

        page_title = page.title()
        browser.close()

    if not info or not info.get("video_url") or not info.get("audio_url"):
        diag = info or {}
        raise RuntimeError(
            f"B 站 __playinfo__ 提取失败 (has_playinfo={diag.get('has_playinfo')}, "
            f"final_url={diag.get('current_url')}). "
            f"可能撞反爬验证,建议手动登录浏览器后再试,或读 FALLBACK.md。"
        )

    return {
        "platform": "bilibili",
        "title": info.get("title") or page_title or "bilibili_video",
        "video_url": info["video_url"],
        "audio_url": info["audio_url"],
        "duration": info.get("duration"),
        "headers": {
            "Referer": "https://www.bilibili.com",
            "User-Agent": DESKTOP_UA,
        },
        "needs_merge": True,
    }


# ─── 公共入口 ──────────────────────────────────────────────────

PLATFORM_EXTRACTORS = {
    "xiaohongshu": extract_xiaohongshu,
    "douyin": extract_douyin,
    "bilibili": extract_bilibili,
}


def detect_platform(url: str) -> Optional[str]:
    u = url.lower()
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    if "douyin.com" in u or "v.douyin.com" in u:
        return "douyin"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    return None


def extract(url: str, headless: bool = True) -> dict:
    platform = detect_platform(url)
    if platform not in PLATFORM_EXTRACTORS:
        raise ValueError(f"不支持的平台: {url}")
    return PLATFORM_EXTRACTORS[platform](url, headless=headless)


# ─── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: platform_extractor.py <url> [--show]", file=sys.stderr)
        sys.exit(2)
    target = sys.argv[1]
    headless = "--show" not in sys.argv[2:]
    result = extract(target, headless=headless)
    print(json.dumps(result, ensure_ascii=False, indent=2))
