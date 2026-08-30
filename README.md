# video-transcript

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

把视频和播客转成可引用的逐字稿。ASR 转录在你电脑上运行，不需要 API Key；链接解析和首次模型下载需要联网。

- **视频**：B 站 / 抖音 / 小红书 / YouTube / 微信视频号
- **播客**：小宇宙 / 喜马拉雅 / Apple Podcasts → 自动分开说话人
- **本地文件**：`mp4` / `m4a` / `mp3` / `wav` 同样能转

> 这个 Skill 是 [黄叔开源系列](https://github.com/Backtthefuture/huangshu) 之一。想系统学 Agent、少一个人摸索，看文末 [加入社群](#加入黄叔和唯庸的-agent-实战社群)。

---

## 贴链接，你拿到什么

转写在你电脑上跑。B 站 / 抖音 / 小红书 / YouTube 给你带小标题的整理稿。播客再多一份能压字幕的 SRT。微信视频号首次使用需要在本机打开腾讯元宝并扫码一次，不需要手工复制 Cookie。

视频号可以选三种交付。三种都是**他怎么说的**，不会改写成概述。

| 你怎么写 | 你拿到什么 |
|---|---|
| 什么都不写，或写「逐字稿」 | **默认。** 对话里直接给你整理过的口语稿。不做 PDF |
| 文字PDF（以前叫「文字版本」） | 同一份口语稿，排成羊皮纸长文 PDF。文件名用视频原标题 |
| 截图PDF（以前叫「截图版本」） | 同一份口语稿，再配上视频里的关键画面。文件名用视频原标题 |

贴视频号链接，什么都不写，我就把逐字稿直接发你。想要羊皮纸长文就加「文字PDF」；想要边看讲边看屏幕就加「截图PDF」。

PDF 用 [Kami](https://github.com/tw93/Kami) 羊皮纸长文排。文件名用视频原标题（去掉话题标签）。

只想保存 MP4、不要逐字稿，请说明「只下载」，会改走配套的 [`video-download`](https://github.com/Backtthefuture/video-download)。

---

## 目录

- [贴链接，你拿到什么](#贴链接你拿到什么)
- [两条链路](#两条链路)
- [支持的平台](#支持的平台)
- [安装](#安装)
- [用法](#用法)
- [输出长什么样](#输出长什么样)
- [常用参数](#常用参数)
- [故障排查](#故障排查)
- [隐私](#隐私)
- [加入社群](#加入黄叔和唯庸的-agent-实战社群)

---

## 两条链路

贴链接后，脚本自己判断走哪条。一般不用先选模式。

| | 视频 | 播客 / 访谈 |
|---|---|---|
| 何时启用 | 视频链接、本地视频 | 小宇宙 / 喜马拉雅 / Apple 单集；或加 `--speakers` |
| 引擎 | SenseVoice-Small，大约 6 倍实时 | paraformer + CAM++，大约音频时长的 25% |
| 说话人 | 不区分 | 自动分离，映射成主持人 / 嘉宾 |
| 给你的东西 | 预整理稿，再按平台整理一版。视频号默认把口语稿发在对话里 | `*_逐字稿.md` + `*_逐字稿.srt`，直接能用 |
| 首次模型 | 约 234MB | 约 1GB（之后走本地缓存） |

---

## 支持的平台

| 档位 | 平台 | 说明 |
|---|---|---|
| 专门解析 | B 站（含 b23.tv）、抖音、小红书、YouTube、微信视频号、小宇宙单集 | 最稳 |
| 播客链路 | 小宇宙、喜马拉雅、Apple Podcasts 的**单集页** | 自动说话人分离 |
| yt-dlp 兜底 | 微博、知乎、西瓜、AcFun 等 | 先当视频转；要区分说话人就加 `--speakers` |
| 暂不支持 | Spotify（DRM）、快手 | 脚本会说明原因和替代做法 |

注意：小宇宙节目主页（`/podcast/`）和喜马拉雅专辑页（`/album/`）不是单集，贴进去会被明确提示。请用单集链接。

---

## 安装

macOS 复制这一行到终端，回车，跟着提示走：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Backtthefuture/video-transcript/main/bootstrap.sh)
```

它会：把 Skill 装到 `~/.claude/skills/video-transcript/`，检查 ffmpeg，安装 `yt-dlp` / Playwright / Chromium，装 FunASR，并把配套的 `video-download` 放在同一个 skills 根目录。重复执行会从 GitHub main 更新程序文件，同时保留 `.env`、`outputs/` 和本地词表。

要安装到其他 Agent 目录，可显式指定目标，避免机器上出现多份互相遮蔽的副本：

```bash
VIDEO_TRANSCRIPT_TARGET="$HOME/.agents/skills/video-transcript" \
  bash <(curl -fsSL https://raw.githubusercontent.com/Backtthefuture/video-transcript/main/bootstrap.sh)
```

装完后在 Claude Code / Codex 里就可以：

```
/video-transcript <视频或播客链接>
```

<details>
<summary>已经会装 Skill 的人：两步装</summary>

```bash
npx skills add Backtthefuture/video-transcript -a claude-code -g -y
bash ~/.claude/skills/video-transcript/install.sh
```

</details>

<details>
<summary>手动安装（不想跑向导）</summary>

```bash
brew install ffmpeg
pip3 install --break-system-packages -r ~/.claude/skills/video-transcript/requirements.txt
python3 -m playwright install chromium
pip3 install --break-system-packages funasr torchaudio
python3 ~/.claude/skills/video-transcript/scripts/transcript.py --doctor
```

</details>

---

## 用法

### 在对话里

把链接丢给 agent，或显式调用：

```
/video-transcript https://www.bilibili.com/video/BVxxx
/video-transcript https://www.xiaoyuzhoufm.com/episode/xxxx
```

视频号三种写法：

```
/video-transcript https://weixin.qq.com/sph/xxxx
/video-transcript https://weixin.qq.com/sph/xxxx 文字PDF
/video-transcript https://weixin.qq.com/sph/xxxx 截图PDF
```

第一行什么都不加，就是逐字稿。以前说的「文字版本 / 截图版本 / 逐字稿版本」也还能用。

### 在终端里

```bash
# 视频
python3 ~/.claude/skills/video-transcript/scripts/transcript.py "<URL>"

# 播客 / 本地访谈：区分说话人
python3 ~/.claude/skills/video-transcript/scripts/transcript.py 访谈.m4a --speakers --host 张三 --guest 李四

# 只改版式或人名，不重跑识别（秒级）
python3 ~/.claude/skills/video-transcript/scripts/transcript.py "<同一输入>" --reformat --host 张三 --guest 李四
```

版式或人名不对，用 `--reformat`。`--force` 会把十几分钟的识别一起重跑。

<details>
<summary>微信视频号怎么解析</summary>

公开安装不再使用 `public-worker`：该地址当前需要额外服务器凭据，普通用户会收到 HTTP 401。`install.sh` 第 7 步会引导微信扫码一次，之后复用本机腾讯元宝登录态；不会把 Cookie 发给第三方 Worker。

之后自己维护：

```bash
python3 ~/.claude/skills/video-transcript/scripts/sph_resolver.py --login
python3 ~/.claude/skills/video-transcript/scripts/sph_resolver.py --check
python3 ~/.claude/skills/video-transcript/scripts/transcript.py --doctor-live "<公开视频号链接>"
```

`--check` 只验证认证，不代表任意链接都能拿到视频流。`--doctor-live` 才会验证“认证 → 分享链接解析 → 视频详情 → 媒体流”，且不会下载或转录。

只下载、不转录，请直接用 [`video-download`](https://github.com/Backtthefuture/video-download)。

</details>

---

## 输出长什么样

视频稿带小标题和时间范围：

```markdown
# 视频标题

> 时长 5:32 | 来源: <URL> | 引擎: FunASR(SenseVoice-Small)

## 1. 引入话题 [00:00 - 00:42]
大家好，今天我们要聊的是...

## 2. 核心观点 [00:42 - 02:15]
那么我的看法是这样的，首先...
```

视频号默认把这样的口语稿直接发在对话里。要 PDF 时，正文还是这些话，只是换成羊皮纸长文；截图PDF 再在对应句子旁边插上视频画面。

播客稿按说话人分块，并额外给一条可直接压字幕的 SRT：

```markdown
## 曲凯 · 00:22
所以今天很开心请到 Evolving 的联创孟繁青，先给大家简单介绍一下。

## 孟繁青 · 00:30
非常感谢曲老师邀请。我是孟繁青，同时也是 Evolving 这边的联创。
```

文件默认写在 `~/.claude/skills/video-transcript/outputs/`。

---

## 常用参数

| 参数 | 说明 |
|---|---|
| `input` | 链接或本地路径（`--doctor` 时不需要） |
| `--title` | 覆盖自动探测到的标题 |
| `--output-dir` | 改输出目录 |
| `--speakers` | 强制说话人分离（播客单集会自动启用） |
| `--host` / `--guest` | 指定主持人 / 嘉宾姓名 |
| `--reformat` | 复用已有识别结果，只重跑后处理 |
| `--keep-audio` | 播客模式保留临时 wav（默认转录完就删） |
| `--keep-video` | 额外留一份 MP4（截图PDF 需要） |
| `--force` | 忽略缓存，整条链路重跑 |
| `--doctor` | 检查依赖，缺什么说什么 |
| `--doctor-live <视频号链接>` | 检查依赖并真实验证视频号解析，不下载/转录 |

---

## 故障排查

```bash
python3 ~/.claude/skills/video-transcript/scripts/transcript.py --doctor
```

| 现象 | 处理 |
|---|---|
| `--doctor` 报缺依赖 | 重跑 `bash ~/.claude/skills/video-transcript/install.sh` |
| funasr 未安装 | `pip install funasr torchaudio` |
| 首次很慢 / 联网失败 | 视频模型约 234MB，播客模型约 1GB；模型可离线复用，网络链接仍需联网 |
| 抖音 / 小红书抓不到 | 平台改版常见，看 [FALLBACK.md](FALLBACK.md) |
| 视频号找不到 `video-download` | 重跑 `install.sh`，或 `npx skills add Backtthefuture/video-download` |
| `WECHAT_AUTH_REQUIRED` / `WECHAT_AUTH_EXPIRED` | 运行 `scripts/sph_resolver.py --login`，扫码后重试 |
| `WECHAT_PARSE_EMPTY` / `WECHAT_PARSE_TOKEN_MISSING` | 登录已通过，但该链接没有可用解析结果；检查链接/内容权限，必要时上传 MP4/MOV |
| `WECHAT_FEED_FAILED` / `WECHAT_STREAM_EMPTY` | 视频详情阶段没有媒体流；保留完整错误码后提 Issue，或改传本地文件 |
| 公共 Worker 返回 401 / 1042 | 不要继续请求隐私授权；公开发行只用 `yuanbao-login` |
| B 站 yt-dlp 报 412 | 会自动改走无头浏览器，可忽略 |
| 抖音图文笔记 | 只支持视频，不支持图文 |
| Chromium 找不到 / 下载失败 | `python3 -m playwright install chromium`，国内网络可设代理后重试 |
| 播客人名或版式要改 | 用 `--reformat`，不要用 `--force` |

抓取失败时：先 `--doctor`，再看 [FALLBACK.md](FALLBACK.md)，仍不行就 [提 Issue](https://github.com/Backtthefuture/video-transcript/issues)（附上链接和报错）。

---

## 隐私

ASR 推理和逐字稿文件在你电脑上处理，不上传到第三方，也不需要 API Key。处理网络链接时，分享链接及必要请求会发送给原平台；视频号默认只访问腾讯域名，并复用本机元宝登录态。可选热词写在本地 `.env`（已加入 `.gitignore`）。

第一次使用需要联网下载模型（ModelScope），之后模型可离线复用；处理网络链接时仍需访问对应平台。

---

## 加入黄叔和唯庸的 Agent 实战社群

这个 Skill 能独立用。如果你还想把 Agent 从「会跑一条命令」推到「能落地项目」，不必一个人试。

社群里有：

- **课程**：零基础智能体视频课，从入门到实战
- **案例**：可复用的项目拆解
- **挑战**：带着任务练，而不是只看
- **直播**：跟黄叔、唯庸同步最新打法

微信扫下图小店码即可查看并加入。

<p align="center">
  <img src="docs/agent-community.png" alt="黄叔和唯庸的 Agent 实战社群：微信扫码加入" width="360">
</p>

<p align="center"><sub>黄叔和唯庸的 Agent 实战社群 · 不用一个人摸索 Agent</sub></p>

---

## License

[MIT](LICENSE)。随便用，也欢迎 Star 和 PR。
