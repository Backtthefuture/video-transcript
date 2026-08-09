# 浏览器 Fallback 下载方案

当 yt-dlp 因反爬（412/403）下载失败时，直接切换浏览器模式。**不要重试 yt-dlp。**

## B站

```bash
# 1. 打开视频页面（可能需要两次 navigate 过验证）
browser navigate "https://www.bilibili.com/video/BVxxxxxxxxxx"
# 如遇 412 验证页，再次 navigate 同一 URL

# 2. 提取视频/音频流地址
browser console exec "
const pi = window.__playinfo__.data.dash;
const video = pi.video.find(v => v.id === 64) || pi.video.find(v => v.id === 32) || pi.video[0];
const audio = pi.audio[0];
JSON.stringify({ video_url: video.baseUrl, audio_url: audio.baseUrl, duration: pi.duration });
"

# 3. curl 下载（必须带 Referer，否则 403）
curl -o /tmp/video-transcript/video_stream.m4s \
  -H "Referer: https://www.bilibili.com" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "<video_url>"

curl -o /tmp/video-transcript/audio_stream.m4s \
  -H "Referer: https://www.bilibili.com" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "<audio_url>"

# 4. ffmpeg 合并视频+音频
ffmpeg -y -i /tmp/video-transcript/video_stream.m4s \
  -i /tmp/video-transcript/audio_stream.m4s \
  -c copy -movflags +faststart /tmp/video-transcript/video.mp4

browser close
```

**要点：**
- `video.id` 画质映射：120=4K, 112=1080P+, 80=1080P, 64=720P, 32=480P, 16=360P，优先取 720P
- curl **必须带** `Referer: https://www.bilibili.com`，否则 403
- 下载的是 m4s 分片格式，需要 ffmpeg 合并

## YouTube

```bash
browser navigate "https://www.youtube.com/watch?v=xxxxxxxxxxx"
# 如遇 consent 页面：
browser act "Click the Accept/Agree button if visible"

# 优先尝试 formats（音视频合一）
browser console exec "
const pr = ytInitialPlayerResponse.streamingData;
const fmt = pr.formats.find(f => f.height <= 720) || pr.formats[0];
JSON.stringify({ url: fmt.url, quality: fmt.qualityLabel, mimeType: fmt.mimeType });
"

curl -o /tmp/video-transcript/video.mp4 -L "<url>"

# 如仅有 adaptiveFormats（DASH），需分别下载音视频再合并
browser console exec "
const pr = ytInitialPlayerResponse.streamingData;
const audio = pr.adaptiveFormats.find(f => f.mimeType.startsWith('audio/mp4'));
const video = pr.adaptiveFormats.find(f => f.mimeType.startsWith('video/mp4') && f.height <= 720);
JSON.stringify({ video_url: video.url, audio_url: audio.url });
"
# 分别 curl 下载后 ffmpeg 合并

browser close
```

**要点：**
- URL 可能有时效性，需尽快下载
- 优先 `formats`（合一），仅有 `adaptiveFormats` 时才分别下载合并
- 如 `ytInitialPlayerResponse` 不存在，尝试 `document.querySelector('video').src`

## 小红书

小红书**不使用 yt-dlp**，直接浏览器方案。支持 URL：
- `xiaohongshu.com/explore/[id]`（需 xsec_token）
- `xiaohongshu.com/discovery/item/[id]`
- `xhslink.com/[code]` 短链接（自动重定向）

```bash
# 短链接直接打开（自动 302 重定向到完整 URL）
browser navigate "https://xhslink.com/[share-code]"
# 或完整链接
browser navigate "https://www.xiaohongshu.com/explore/[id]?xsec_token=xxx"

# 提取视频信息
browser console exec "
var state = window.__INITIAL_STATE__;
var noteKey = Object.keys(state.note.noteDetailMap)[0];
var note = state.note.noteDetailMap[noteKey].note;
if (note.type !== 'video') {
    JSON.stringify({error: 'not_video', type: note.type, title: note.title});
} else {
    var h264 = note.video.media.stream.h264;
    var best = h264[0];
    JSON.stringify({
        title: note.title,
        video_url: best.masterUrl,
        width: best.width,
        height: best.height,
        size_mb: (best.size / 1024 / 1024).toFixed(1),
        duration_s: (best.duration / 1000).toFixed(0)
    });
}
"

# CDN 无需认证头，直接下载
curl -o /tmp/video-transcript/video.mp4 -L "<video_url>"

browser close
```

**要点：**
- 优先取 h264（兼容性最好），`h264[0].masterUrl`
- CDN（`sns-video-*.xhscdn.com`）**完全公开**，无需 Referer 或 Cookie
- 音视频合一 MP4，**不需要 ffmpeg 合并**
- `note.type !== 'video'` 说明是图文笔记，提示用户
- 如 explore 链接 404，可能缺 `xsec_token`，用短链接替代

## 抖音

抖音**不使用 yt-dlp**（DouyinIE 提取器已失效），直接浏览器方案。支持 URL：
- `douyin.com/video/[id]` 完整链接
- `v.douyin.com/[code]` 短链接（自动 302 重定向）
- 分享文本中提取的 URL

**不支持：** `douyin.com/note/[id]` 图文笔记

**核心原理：** 抖音视频通过 MSE (Media Source Extensions) 流式加载，页面中只有 `blob:` URL。但在浏览器上下文中（同源），可以直接调用抖音内部 API 获取视频 CDN 地址，无需复杂的拦截器。

```bash
# 1. 导航到抖音页面（获取必要的 cookies：ttwid 等）
browser navigate "https://www.douyin.com/video/[id]"
# 短链接也可以直接打开，会自动 302 重定向：
# browser navigate "https://v.douyin.com/[code]"

# 等待页面加载（获取 cookies 即可，不需要等视频播放）
sleep 5

# 2. 从 URL 中提取 aweme_id（视频 ID）
#    - douyin.com/video/[id] → id 就是 aweme_id
#    - 短链接重定向后检查 window.location.href 获取最终 URL
browser console exec "window.location.href"

# 3. 直接调用抖音 API 获取视频信息（同源请求，自动带 cookies）
browser console exec "
var awemeId = '[从URL提取的视频ID]';
var apiUrl = 'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=' + awemeId + '&device_platform=webapp&aid=6383';

fetch(apiUrl, {
  method: 'GET',
  credentials: 'include',
  headers: { 'Accept': 'application/json', 'Referer': 'https://www.douyin.com/' }
}).then(function(resp) { return resp.json(); })
.then(function(data) {
  if (data.aweme_detail && data.aweme_detail.video) {
    var video = data.aweme_detail.video;
    var playAddr = video.play_addr || video.play_addr_h264;
    window._douyinVideoData = {
      success: true,
      title: data.aweme_detail.desc || '',
      video_urls: playAddr ? playAddr.url_list : [],
      duration: video.duration || 0,
      width: (playAddr && playAddr.width) || 0,
      height: (playAddr && playAddr.height) || 0
    };
  } else {
    window._douyinVideoData = { success: false, msg: JSON.stringify(data).substring(0, 300) };
  }
  document.title = 'API_DONE';
}).catch(function(e) {
  window._douyinVideoData = { success: false, error: e.message };
  document.title = 'API_DONE';
});
'fetching...';
"

# 等待 API 响应（通常 1-2 秒）
sleep 3

# 4. 获取结果
browser console exec "JSON.stringify(window._douyinVideoData)"
# 返回: { success: true, title: "...", video_urls: ["https://..."], ... }

# 5. 优先使用 url_list 中最后一个地址（douyin aweme play 格式，最稳定）
#    或使用第一个 CDN 地址（v5-dy-o.zjcdn.com 等）
curl -o /tmp/video-transcript/video.mp4 -L \
  -H "Referer: https://www.douyin.com/" \
  -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "<video_url>"

browser close
```

**要点：**
- 直接 API 调用比 fetch 拦截器**更简单可靠**（无需在导航前注入，不怕页面跳转清除 JS 上下文）
- API 地址：`/aweme/v1/web/aweme/detail/?aweme_id=[ID]&device_platform=webapp&aid=6383`
- 必须从浏览器上下文调用（同源 + cookies），直接 curl 会被拦截
- `url_list` 通常有 3 个地址：2 个 CDN（`.zjcdn.com`）+ 1 个 play API，任取一个即可
- curl **必须带** `Referer: https://www.douyin.com/`
- CDN URL 有时效性（约几小时），需尽快下载
- 如果 API 返回空或错误，可能视频需要登录才能查看，需提示用户
- `douyin.com/note/[id]` 是图文笔记，不支持视频下载，需提示用户

## Fallback 后续流程

浏览器下载完成后，视频在 `/tmp/video-transcript/video.mp4`（或你指定的路径）：

```bash
python3 ~/.claude/skills/video-transcript/scripts/transcript.py \
  /tmp/video-transcript/video.mp4 \
  --title "从浏览器页面标题获取的视频标题"
```

> 注：本 skill 输出目录默认 `./outputs/`，且只产出 Markdown 逐字稿（无 HTML 报告）。
