#!/usr/bin/env python3
"""
微信视频号解析器(复用元宝登录态)
用法:
  python3 sph_resolver.py <视频号分享链接>
  python3 sph_resolver.py --check          # 检查登录态是否有效
  python3 sph_resolver.py --login          # 手动打开浏览器扫码登录并保存登录态

流程: 打开腾讯元宝(复用/获取登录态) → 官方接口 get_parse_result 拿 playable_url → get_feed_info 拿视频直链 → 输出 JSON
"""
import asyncio
import json
import os
import sys
import time
import random
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.expanduser("~/.workbuddy/credentials/yuanbao_state.json")
AGENT_ID = "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1"
PYTHON = sys.executable

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
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


async def run_browser(headless, storage_state, url, js_code, arg=None):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--start-maximized"] if not headless else [],
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            storage_state=storage_state,
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(6000)
        result = await page.evaluate(js_code, arg) if arg is not None else await page.evaluate(js_code)
        state = None
        if ctx.storage_state:
            try:
                state = await ctx.storage_state()
            except Exception:
                state = None
        await browser.close()
        return result, state


def check_login_state():
    state = load_state()
    result, _ = asyncio.run(run_browser(True, state, "https://yuanbao.tencent.com/", LOGIN_JS))
    return result


def do_login():
    """打开可见浏览器,等待扫码,保存登录态"""
    result, state = asyncio.run(run_browser(False, None, "https://yuanbao.tencent.com/", LOGIN_JS))
    if result.get("loggedIn"):
        # 已登录,直接保存
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False)
        os.chmod(STATE_FILE, 0o600)
        print(f"[OK] 登录态已保存到 {STATE_FILE}")
        return True
    return False


def parse_share_url(share_url):
    state = load_state()
    if not state:
        print("[提示] 无登录态,先执行 --login 扫码登录", file=sys.stderr)
        return None
    # 先检查登录态
    result, _ = asyncio.run(run_browser(True, state, "https://yuanbao.tencent.com/", LOGIN_JS))
    if not result.get("loggedIn"):
        print("[提示] 登录态已过期,需重新执行 --login 扫码登录", file=sys.stderr)
        return None
    # 解析
    result, _ = asyncio.run(
        run_browser(True, state, "https://yuanbao.tencent.com/", PARSE_JS, {"shareUrl": share_url, "agentId": AGENT_ID})
    )
    if not result.get("ok"):
        print(f"[错误] 解析失败: {result.get('code')} {result.get('body', '')[:200]}", file=sys.stderr)
        return None
    return json.loads(result["body"])


def fetch_feed_info(token, eid):
    """用 token+eid 获取视频直链"""
    def gen_rid():
        return f"{int(time.time()):x}-" + "".join(random.choice("0123456789abcdef") for _ in range(8))

    rid = gen_rid()
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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    req = urllib.request.Request(api_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
    return json.loads(body), None


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
        ok = do_login()
        return 0 if ok else 1

    share_url = args[0]
    parse_result = parse_share_url(share_url)
    if not parse_result:
        return 1

    data = parse_result.get("data") or {}
    playable_url = data.get("playable_url", "")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(playable_url).query)
    token = (qs.get("token") or [""])[0]
    eid = (qs.get("eid") or [""])[0]
    if not token or not eid:
        print("[错误] playable_url 缺少 token/eid", file=sys.stderr)
        return 1

    feed, err = fetch_feed_info(token, eid)
    if err or not feed:
        print(f"[错误] get_feed_info 失败: {err}", file=sys.stderr)
        return 1

    feed_data = feed.get("data") or {}
    feed_info = feed_data.get("feedInfo") or {}
    author_info = feed_data.get("authorInfo") or {}
    h264 = (feed_info.get("h264VideoInfo") or {}).get("videoUrl") or ""
    h265 = (feed_info.get("h265VideoInfo") or {}).get("videoUrl") or ""
    base = feed_info.get("videoUrl") or ""

    output = {
        "platform": "wechat_channels",
        "title": f"{author_info.get('nickname','')}-{feed_info.get('description','')}",
        "author": author_info.get("nickname", ""),
        "description": feed_info.get("description", ""),
        "source_url": share_url,
        "direct_url": h264 or base or h265,
        "stats": {
            "fav": feed_info.get("favCountFmt"),
            "like": feed_info.get("likeCountFmt"),
            "forward": feed_info.get("forwardCountFmt"),
            "comment": feed_info.get("commentCountFmt"),
        },
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
