#!/usr/bin/env python3
"""
微信视频号解析器(复用元宝登录态)

用法:
  python3 sph_resolver.py <视频号分享链接>
  python3 sph_resolver.py --check          # 检查登录态是否有效
  python3 sph_resolver.py --login          # 打开浏览器扫码登录并保存登录态

优先从 ~/.workbuddy/credentials/yuanbao_state.json 抽 Cookie 走纯 HTTP
(getuserinfo → get_parse_result → get_feed_info),约 2~5 秒。
HTTP 失败才回退到**单次** headless 浏览器(登录检查+解析合并,等 $webApi 就绪,不再固定睡 6 秒)。
"""
import asyncio
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.expanduser("~/.workbuddy/credentials/yuanbao_state.json")
AGENT_ID = "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1"
YUANBAO_ORIGIN = "https://yuanbao.tencent.com"
DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()


def log(msg):
    print(msg, file=sys.stderr)


class WechatResolverError(RuntimeError):
    """带稳定错误码和阶段的视频号解析错误。"""

    def __init__(self, code, stage, message):
        self.code = code
        self.stage = stage
        self.message = message
        super().__init__(f"[{code}] {message}")

    def as_dict(self):
        return {
            "ok": False,
            "code": self.code,
            "stage": self.stage,
            "error": self.message,
        }


def resolver_error(code, stage, message):
    raise WechatResolverError(code, stage, message)


LOGIN_JS = r"""
() => {
  const webApi = window.$webApi;
  if (!webApi?.getYbCommonHeaders || !webApi?.setContextualRequestHeaders) {
    return {ready: false, loggedIn: false};
  }
  return new Promise(async (resolve) => {
    try {
      const request = {
        url: "/api/getuserinfo",
        headers: {
          ...webApi.getYbCommonHeaders(),
          "Accept": "application/json, text/plain, */*",
          "X-Requested-With": "XMLHttpRequest"
        }
      };
      await webApi.setContextualRequestHeaders(request);
      const response = await fetch(request.url, {
        method: "GET", credentials: "include", headers: request.headers
      });
      resolve({ready: true, loggedIn: response.ok, status: response.status});
    } catch (e) {
      resolve({ready: true, loggedIn: false, error: String(e)});
    }
  });
}
"""

PARSE_JS = r"""
async ({shareUrl, agentId}) => {
  const webApi = window.$webApi;
  const baseHeaders = {
    ...webApi.getYbCommonHeaders(),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest"
  };
  const userRequest = { url: "/api/getuserinfo", headers: { ...baseHeaders } };
  await webApi.setContextualRequestHeaders(userRequest);
  const userResponse = await fetch(userRequest.url, {
    method: "GET", credentials: "include", headers: userRequest.headers
  });
  const userText = await userResponse.text();
  if (!userResponse.ok) return { ok: false, code: "LOGIN_REQUIRED", body: userText.slice(0, 200) };
  let userInfo = {};
  try { userInfo = JSON.parse(userText); } catch (_) {}
  const userData = userInfo?.data && typeof userInfo.data === "object" ? userInfo.data : userInfo;
  const userId = userData?.userId || userData?.userid || userData?.user_id || userData?.id || "";

  const parseRequest = {
    url: "/api/weixin/get_parse_result",
    headers: { ...baseHeaders, "X-AgentID": agentId }
  };
  await webApi.setContextualRequestHeaders(parseRequest);
  if (userId) {
    parseRequest.headers["T-UserID"] = String(userId);
    parseRequest.headers["X-ID"] = String(userId);
  }
  const response = await fetch(parseRequest.url, {
    method: "POST",
    credentials: "include",
    headers: parseRequest.headers,
    body: JSON.stringify({ type: "video_channel_url", url: shareUrl, scene: 1 })
  });
  const body = await response.text();
  return { ok: response.status === 200, status: response.status, body: body };
}
"""


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.chmod(STATE_FILE, 0o600)


def cookie_header_from_state(state):
    if not state:
        return ""
    parts = []
    seen = set()
    for cookie in state.get("cookies") or []:
        domain = cookie.get("domain") or ""
        if "tencent.com" not in domain and "yuanbao" not in domain:
            continue
        name = cookie.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        parts.append(f"{name}={cookie.get('value') or ''}")
    return "; ".join(parts)


def _http_json(url, method="GET", payload=None, headers=None, timeout=20):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=SSL_CONTEXT),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:240]}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络请求失败: {exc.reason}") from None
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise RuntimeError(f"接口返回不是 JSON: {body[:200]}") from None
    return parsed, status


def _yuanbao_headers(cookie, extra=None):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": YUANBAO_ORIGIN,
        "Referer": f"{YUANBAO_ORIGIN}/chat/{AGENT_ID}",
        "User-Agent": DESKTOP_UA,
        "X-Requested-With": "XMLHttpRequest",
        "X-Source": "web",
        "X-Platform": "mac",
        "Cookie": cookie,
    }
    if extra:
        headers.update(extra)
    return headers


def _pick_user_id(user_info):
    data = user_info.get("data") if isinstance(user_info.get("data"), dict) else user_info
    for key in ("userId", "userid", "user_id", "id"):
        val = data.get(key) if isinstance(data, dict) else None
        if val:
            return str(val)
    return ""


def http_get_userinfo(cookie):
    parsed, status = _http_json(
        f"{YUANBAO_ORIGIN}/api/getuserinfo",
        method="GET",
        headers=_yuanbao_headers(cookie),
    )
    if status != 200:
        raise RuntimeError(f"getuserinfo 失败: HTTP {status}")
    return parsed


def http_parse_share_url(share_url, cookie, user_id=""):
    extra = {
        "Content-Type": "application/json",
        "X-AgentID": AGENT_ID,
    }
    if user_id:
        extra["T-UserID"] = user_id
        extra["X-ID"] = user_id
    parsed, status = _http_json(
        f"{YUANBAO_ORIGIN}/api/weixin/get_parse_result",
        method="POST",
        payload={"type": "video_channel_url", "url": share_url, "scene": 1},
        headers=_yuanbao_headers(cookie, extra),
    )
    if status != 200:
        raise RuntimeError(f"get_parse_result 失败: HTTP {status}")
    if parsed.get("code") not in (None, 0):
        resolver_error(
            "WECHAT_PARSE_FAILED",
            "parse",
            f"元宝解析失败: {parsed.get('msg') or parsed.get('message') or parsed.get('code')}",
        )
    data = parsed.get("data") or {}
    if not data.get("playable_url") and not data.get("wx_export_id"):
        resolver_error(
            "WECHAT_PARSE_EMPTY",
            "parse",
            "元宝已响应，但没有返回 playable_url 或 wx_export_id；可能是链接失效、内容权限受限或页面接口发生变化",
        )
    return data


def generate_rid():
    return f"{int(time.time()):x}-" + "".join(random.choice("0123456789abcdef") for _ in range(8))


def fetch_feed_info(token, eid):
    rid = generate_rid()
    api_url = (
        "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
        f"?_rid={rid}&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
    )
    referer = (
        "https://channels.weixin.qq.com/finder-preview/pages/feed"
        f"?entry_card_type=48&comment_scene=39&appid=0"
        f"&token={urllib.parse.quote(token)}"
        f"&entry_scene=0&eid={urllib.parse.quote(eid)}"
    )
    payload = json.dumps({"baseReq": {"generalToken": token}, "exportId": eid}).encode("utf-8")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://channels.weixin.qq.com",
        "Referer": referer,
        "User-Agent": DESKTOP_UA,
    }
    req = urllib.request.Request(api_url, data=payload, headers=headers, method="POST")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=SSL_CONTEXT),
    )
    try:
        with opener.open(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
    except urllib.error.URLError as exc:
        return None, f"网络请求失败: {exc.reason}"
    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        return None, "get_feed_info 返回不是 JSON"


def _coerce_duration(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0
    if num > 10000:
        num = num / 1000.0
    if 1 <= num <= 36000:
        return int(round(num))
    return 0


def extract_duration(feed):
    data = (feed or {}).get("data") or {}
    feed_info = data.get("feedInfo") or {}
    for blob in (
        feed_info,
        feed_info.get("h264VideoInfo") or {},
        feed_info.get("h265VideoInfo") or {},
        data,
    ):
        if not isinstance(blob, dict):
            continue
        for key in ("videoPlayLen", "videoDuration", "duration", "playLen", "videoLen"):
            dur = _coerce_duration(blob.get(key))
            if dur:
                return dur
    return 0


def token_eid_from_parse(parsed):
    playable = parsed.get("playable_url") or ""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(playable).query)
    token = (qs.get("token") or [""])[0]
    eid = (qs.get("eid") or qs.get("exportId") or [""])[0] or (parsed.get("wx_export_id") or "")
    return token, eid


def profile_from_feed(share_url, feed, resolver="yuanbao-http"):
    data = (feed or {}).get("data") or {}
    feed_info = data.get("feedInfo") or {}
    author_info = data.get("authorInfo") or {}
    h264 = ((feed_info.get("h264VideoInfo") or {}).get("videoUrl") or "").strip()
    h265 = ((feed_info.get("h265VideoInfo") or {}).get("videoUrl") or "").strip()
    base = (feed_info.get("videoUrl") or "").strip()
    direct = h264 or base or h265
    author = author_info.get("nickname") or ""
    desc = feed_info.get("description") or ""
    title = f"{author}-{desc}" if author else (desc or "weixin_channels_video")
    return {
        "platform": "wechat_channels",
        "title": title,
        "author": author,
        "description": desc,
        "source_url": share_url,
        "direct_url": direct,
        "duration": extract_duration(feed),
        "resolver": resolver,
        "stats": {
            "fav": feed_info.get("favCountFmt"),
            "like": feed_info.get("likeCountFmt"),
            "forward": feed_info.get("forwardCountFmt"),
            "comment": feed_info.get("commentCountFmt"),
        },
    }


def resolve_via_http(share_url, state):
    cookie = cookie_header_from_state(state)
    if not cookie:
        resolver_error(
            "WECHAT_AUTH_STATE_INVALID",
            "auth",
            "元宝登录态里没有可用 Cookie，请重新执行 sph_resolver.py --login",
        )
    if "hy_token" not in cookie and "hy_user" not in cookie:
        resolver_error(
            "WECHAT_AUTH_STATE_INVALID",
            "auth",
            "元宝登录态缺少必要字段，请重新执行 sph_resolver.py --login",
        )
    user_info = http_get_userinfo(cookie)
    user_id = _pick_user_id(user_info)
    parsed = http_parse_share_url(share_url, cookie, user_id)
    token, eid = token_eid_from_parse(parsed)
    if not token or not eid:
        resolver_error(
            "WECHAT_PARSE_TOKEN_MISSING",
            "parse",
            "元宝返回结果缺少 token/eid，无法继续请求视频详情",
        )
    feed, err = fetch_feed_info(token, eid)
    if err or not feed:
        resolver_error(
            "WECHAT_FEED_FAILED",
            "feed",
            f"视频号详情请求失败: {err or '空响应'}",
        )
    profile = profile_from_feed(share_url, feed, resolver="yuanbao-http")
    if not profile.get("direct_url"):
        resolver_error(
            "WECHAT_STREAM_EMPTY",
            "stream",
            "视频号详情已返回，但没有可下载视频流；可能是内容权限、链接状态或接口字段变化",
        )
    return profile


async def _wait_webapi(page, timeout_ms=10000):
    try:
        await page.wait_for_function(
            "() => !!(window.$webApi && window.$webApi.getYbCommonHeaders "
            "&& window.$webApi.setContextualRequestHeaders)",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def run_browser_session(headless, storage_state, share_url=None, login_only=False, wait_login=False):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--start-maximized"] if not headless else [],
        )
        ctx = await browser.new_context(
            user_agent=DESKTOP_UA,
            viewport={"width": 1280, "height": 900},
            storage_state=storage_state,
        )
        page = await ctx.new_page()
        await page.goto(f"{YUANBAO_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
        ready = await _wait_webapi(page, 10000)
        if not ready:
            await page.wait_for_timeout(2000)

        login = await page.evaluate(LOGIN_JS)
        if wait_login and not login.get("loggedIn"):
            for _ in range(24):
                await page.wait_for_timeout(5000)
                login = await page.evaluate(LOGIN_JS)
                if login.get("loggedIn"):
                    break

        parse_result = None
        if (not login_only) and share_url and login.get("loggedIn"):
            parse_result = await page.evaluate(
                PARSE_JS, {"shareUrl": share_url, "agentId": AGENT_ID}
            )

        state = None
        try:
            state = await ctx.storage_state()
        except Exception:
            state = None
        await browser.close()
        return {"login": login, "parse": parse_result, "state": state}


def check_login_state():
    state = load_state()
    if not state:
        return {
            "ready": True,
            "loggedIn": False,
            "via": "local",
            "authOnly": True,
            "code": "WECHAT_AUTH_REQUIRED",
            "message": "未找到元宝登录态，首次使用视频号需执行 sph_resolver.py --login",
        }
    cookie = cookie_header_from_state(state)
    if cookie:
        try:
            info = http_get_userinfo(cookie)
            user_id = _pick_user_id(info)
            if user_id:
                return {
                    "ready": True,
                    "loggedIn": True,
                    "via": "http",
                    "authOnly": True,
                    "userId": user_id,
                }
        except Exception as exc:
            log(f"[INFO] HTTP 登录检查失败,回退浏览器: {exc}")
    result = asyncio.run(run_browser_session(True, state, login_only=True))
    login = result.get("login") or {}
    login["via"] = "browser"
    login["authOnly"] = True
    if not login.get("loggedIn"):
        login.setdefault("code", "WECHAT_AUTH_EXPIRED")
    return login


def do_login():
    result = asyncio.run(
        run_browser_session(False, None, login_only=True, wait_login=True)
    )
    login = result.get("login") or {}
    state = result.get("state")
    if login.get("loggedIn") and state:
        save_state(state)
        log(f"[OK] 登录态已保存到 {STATE_FILE}")
        return True
    log("[错误] 未检测到登录,请扫码后重试")
    return False


def resolve_via_browser(share_url, state):
    result = asyncio.run(run_browser_session(True, state, share_url=share_url))
    login = result.get("login") or {}
    if result.get("state"):
        try:
            save_state(result["state"])
        except Exception:
            pass
    if not login.get("loggedIn"):
        resolver_error(
            "WECHAT_AUTH_EXPIRED",
            "auth",
            "元宝登录态已失效，需要重新执行 sph_resolver.py --login 扫码",
        )
    parsed_wrap = result.get("parse") or {}
    if not parsed_wrap.get("ok"):
        resolver_error(
            "WECHAT_PARSE_FAILED",
            "parse",
            f"元宝浏览器解析失败: {parsed_wrap.get('code')} {(parsed_wrap.get('body') or '')[:200]}",
        )
    parsed = json.loads(parsed_wrap["body"])
    data = parsed.get("data") or parsed
    token, eid = token_eid_from_parse(data)
    if not token or not eid:
        resolver_error(
            "WECHAT_PARSE_TOKEN_MISSING",
            "parse",
            "元宝返回结果缺少 token/eid，无法继续请求视频详情",
        )
    feed, err = fetch_feed_info(token, eid)
    if err or not feed:
        resolver_error(
            "WECHAT_FEED_FAILED",
            "feed",
            f"视频号详情请求失败: {err or '空响应'}",
        )
    profile = profile_from_feed(share_url, feed, resolver="yuanbao-browser")
    if not profile.get("direct_url"):
        resolver_error(
            "WECHAT_STREAM_EMPTY",
            "stream",
            "视频号详情已返回，但没有可下载视频流；可能是内容权限、链接状态或接口字段变化",
        )
    return profile


def resolve_wechat(share_url, prefer_http=True):
    """解析视频号分享链接,返回含 direct_url/title/duration 的 profile。"""
    state = load_state()
    if not state:
        resolver_error(
            "WECHAT_AUTH_REQUIRED",
            "auth",
            "未找到元宝登录态，先执行 sph_resolver.py --login 扫码登录",
        )
    http_error = None
    if prefer_http:
        try:
            profile = resolve_via_http(share_url, state)
            log("[OK] 视频号 HTTP 解析成功")
            return profile
        except Exception as exc:
            http_error = exc
            log(f"[WARN] HTTP 解析失败({exc}),回退单次浏览器会话")
    try:
        return resolve_via_browser(share_url, state)
    except WechatResolverError:
        raise
    except Exception as browser_exc:
        if isinstance(http_error, WechatResolverError):
            raise WechatResolverError(
                http_error.code,
                http_error.stage,
                f"{http_error.message}；浏览器兜底也失败: {browser_exc}",
            ) from browser_exc
        resolver_error(
            "WECHAT_BROWSER_FALLBACK_FAILED",
            "browser",
            f"元宝浏览器兜底失败: {browser_exc}",
        )


def parse_share_url(share_url):
    """兼容旧 CLI:失败返回 None。"""
    try:
        profile = resolve_wechat(share_url)
    except Exception as exc:
        log(f"[错误] {exc}")
        return None
    return {"data": {"playable_url": "", "profile": profile}, "profile": profile}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    if args[0] == "--check":
        result = check_login_state()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("loggedIn") else 1

    if args[0] == "--login":
        return 0 if do_login() else 1

    probe_mode = args[0] == "--probe"
    if probe_mode and len(args) < 2:
        print("用法: sph_resolver.py --probe <视频号分享链接>", file=sys.stderr)
        return 2
    share_url = args[1] if probe_mode else args[0]
    try:
        profile = resolve_wechat(share_url)
    except Exception as exc:
        log(f"[错误] {exc}")
        if isinstance(exc, WechatResolverError):
            print(json.dumps(exc.as_dict(), ensure_ascii=False))
        else:
            print(json.dumps({"ok": False, "code": "WECHAT_UNKNOWN", "stage": "unknown", "error": str(exc)}, ensure_ascii=False))
        return 1
    if probe_mode:
        profile = {"ok": True, **profile}
    print(json.dumps(profile, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
