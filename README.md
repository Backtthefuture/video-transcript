# 视频文案提取（video-transcript）Skill

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

把 微信视频号 / B站 / 抖音 / 小红书 / YouTube / 本地视频 自动转成**逐字稿文案**（保留口语词、网络梗、停顿）。

全程在你电脑**本地离线**跑：下载 → 提音频 → **FunASR（SenseVoice-Small）**转录。不需要任何 API Key、不上传视频、不弹窗、不要登录视频网站（模型首次下载后全离线）。

---

## ⚡ 一键安装（macOS 推荐）

复制下面这一行，粘贴到终端回车，全程跟着提示按回车就行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Backtthefuture/video-transcript/main/bootstrap.sh)
```

引导脚本会自动：
1. 把 skill 拉到 `~/.claude/skills/video-transcript/`（优先 `npx skills add`，回退 git clone，再回退 tarball）
2. 检查/安装 ffmpeg（必要时连 Homebrew 一起装）
3. 装 `yt-dlp` + `playwright` + Chromium 浏览器引擎（~300MB）
4. 安装 FunASR 转录引擎（SenseVoice-Small，约 234M）
5. **自动安装配套 skill `video-download`**（微信视频号下载必需，默认 `public-worker` 解析，无需 Cookie）
6. 跑 `--doctor` 自检

完成后在 Claude Code / Codex 里就能用 `/video-transcript <视频链接>`。

如果你只想保存 MP4、不需要逐字稿，先说明"只下载"，agent 会改走 `video-download`，不进入转录流程。只贴链接或说"处理这个视频"但没说清结果时，agent 应先问你是仅下载还是提取逐字稿。

### 标准两步安装

```bash
# 1. 拉 skill 文件
npx skills add Backtthefuture/video-transcript -a claude-code -g -y

# 2. 装系统依赖 + FunASR 转录引擎
bash ~/.claude/skills/video-transcript/install.sh
```

### 老手手动 4 步

```bash
brew install ffmpeg
pip3 install --break-system-packages -r ~/.claude/skills/video-transcript/requirements.txt
python3 -m playwright install chromium
pip3 install --break-system-packages funasr torchaudio
```

---

## 🚀 用法

### 在 Claude Code / Codex 里

```
/video-transcript <视频 URL>
```

支持：
- B 站：`https://www.bilibili.com/video/BVxxx`
- 抖音：`https://www.douyin.com/video/xxx` 或 `https://v.douyin.com/xxx`
- 小红书：`https://www.xiaohongshu.com/discovery/item/xxx` 或短链 `xhslink.com/xxx`
- YouTube：`https://youtube.com/watch?v=xxx`
- 微信视频号：`https://weixin.qq.com/sph/xxx` 或 `channels.weixin.qq.com/...`
- 本地文件：`/path/to/video.mp4`

微信视频号转录依赖配套 skill `video-download`（已由 install.sh 自动安装，默认 `public-worker` 解析，无需 Cookie）。若只下载视频号视频，请直接走 `video-download`。

#### 微信视频号：元宝登录态解析（推荐）

公共 Worker（`sph.litao.workers.dev`）已失效（返回微信错误码 1042），`download_video.py` 会自动回退到**元宝登录态解析**：复用 `~/.workbuddy/credentials/yuanbao_state.json` 的腾讯元宝持久化登录态，走腾讯官方接口，不导出 Cookie、不依赖第三方服务。

- install.sh 第 7 步会引导**微信扫码一次**建立登录态
- 手动维护：

```bash
python3 ~/.workbuddy/skills/video-transcript/scripts/sph_resolver.py --login   # 扫码建立/更新登录态
python3 ~/.workbuddy/skills/video-transcript/scripts/sph_resolver.py --check   # 检查登录态
python3 ~/.workbuddy/skills/video-transcript/scripts/sph_resolver.py "<链接>"   # 直接解析(输出 JSON)
```

### 终端直接跑

```bash
python3 ~/.claude/skills/video-transcript/scripts/transcript.py "<URL>"
```

### 实际体验

跑命令时会看到：
```
═══════════════════════════════════════════════════════
  📊 视频探测
───────────────────────────────────────────────────────
  平台:      B 站
  标题:      在浙江和安徽之间，一座10万人的城市消失了
  时长:      17分12秒
  分段:      3 段(每段 ≤ 6 分钟)
  预估耗时:  3分20秒 ~ 5分25秒
═══════════════════════════════════════════════════════
```
然后自动跑 下载 → 提音频 → 转录 → 分段，全程无人值守。

---

## 📝 输出

逐字稿默认**两个去处**：
1. **stdout**：完整 Markdown 直接打印（适合直接展示，或 `| pbcopy`）
2. **落盘**：`~/.claude/skills/video-transcript/outputs/<日期>_<标题>_transcript.md`

格式示例：
```markdown
# 视频标题

> 时长 5:32 | 来源: <URL> | 引擎: FunASR(SenseVoice-Small)

## 1. 引入话题 [00:00 - 00:42]
大家好，今天我们要聊的是...

## 2. 核心观点 [00:42 - 02:15]
那么我的看法是这样的，首先...
```

特性：
- **严格逐字**：保留语气词（"呃""啊""那"）、口语、网络梗，不总结改写
- **中文标点**：SenseVoice 自带逗号/句号/问号，无需后处理补标点
- **语义分段**：口述话题转折处优先切段（"第 N 个/点"等标记），无标记按 60s 兜底
- **段落级时间戳**：`[MM:SS - MM:SS]` 方便定位
- **一键目录**：段落 > 3 时自动生成"标题 + 起始时间戳"导航
- **关键词**：全文高频词提取，放头部
- **长视频自动分段**：按口述话题切段，规避长音频聚类失真

> **整理优化版**：agent 默认还会在逐字稿基础上整理一版「整理优化版」（补标点、合并碎句、修正识别错误、语义化小标题 + 识别修正对照表），作为对话与预览的核心交付。

---

## 🛠 命令行选项

| 参数 | 说明 |
|---|---|
| `input` | 视频 URL 或本地文件路径（必需，`--doctor` 时不需要） |
| `--title` | 视频标题（默认用探测到的标题） |
| `--no-save` | 不写 .md 文件（默认会保存） |
| `--output-dir` | 改输出目录 |
| `--doctor` | 体检模式：检查所有依赖+配置 |

---

## 🩺 故障排查

```bash
python3 ~/.claude/skills/video-transcript/scripts/transcript.py --doctor
```

会逐项检查：ffmpeg / ffprobe / Python / yt-dlp / playwright / chromium / video-download（视频号可选依赖）/ funasr / 模型，缺啥说啥。

### 常见问题

| 现象 | 处理 |
|---|---|
| `--doctor` 报缺依赖 | `bash ~/.claude/skills/video-transcript/install.sh` |
| funasr 未安装 | `pip install funasr torchaudio` |
| 首次运行联网失败 | 首次需联网下载模型（约 234M）；国内可重试或检查网络 |
| 抖音/小红书抓不到视频 | 平台前端可能改版，参考 `FALLBACK.md` 手动方案 |
| 微信视频号提示找不到 `video-download` | 重跑 `bash ~/.claude/skills/video-transcript/install.sh` 自动安装配套 skill，或手动 `npx skills add Backtthefuture/video-download` |
| B 站 yt-dlp 报 412 | 正常，已自动 fallback 到 headless 浏览器，不用管 |
| 抖音图文笔记（note 链接） | 不支持图文，仅支持视频 |
| `playwright` 报 chromium 找不到 | `python3 -m playwright install chromium` |
| Chromium 下载失败 | 国内网络问题；可设代理或重试 |

---

## 🏗 架构

```
~/.claude/skills/video-transcript/
├── SKILL.md                      ← Agent 入口文档（触发词 + 工作流）
├── README.md                     ← (本文件)
├── FALLBACK.md                   ← 抓取失效时的人工兜底
├── install.sh                    ← 一键安装向导
├── bootstrap.sh                  ← 一行命令入口（三档兜底拉 skill + 跑 install.sh）
├── requirements.txt              ← Python 依赖列表
├── .env                          ← 用户私有配置（可选热词），.gitignore
├── .gitignore
├── outputs/                      ← 逐字稿落盘目录
└── scripts/
    ├── transcript.py             ← 主流程（--doctor 体检 / probe / 下载 / 转录 / 分段）
    ├── make_optimized.py         ← 生成「整理优化版」md + html
    └── platform_extractor.py     ← 抖音/小红书/B 站 headless 直链抓取
```

流程：
```
探测元信息(headless,拿 title+duration+直链 URL)
  ↓
打印评估表(平台/标题/时长/分段/预估耗时)
  ↓
下载(复用探测拿到的直链,不重启浏览器;微信视频号先桥接 video-download)
  ↓
ffmpeg 提 16k 单声道 wav
  ↓
FunASR SenseVoice-Small 本地转录(自带中文标点,CPU 约 6x 实时)
  ↓
按句切句 + 字数比例估算时间戳 + 话题转折分段
  ↓
stdout + 落盘 outputs/
```

---

## 📦 手动安装（不想用 install.sh）

```bash
# 0. 把 skill 拷贝到 ~/.claude/skills/video-transcript/

# 1. ffmpeg
brew install ffmpeg

# 2. Python 包
pip install --break-system-packages -r ~/.claude/skills/video-transcript/requirements.txt

# 3. Chromium
python3 -m playwright install chromium

# 4. FunASR 转录引擎
pip install --break-system-packages funasr torchaudio

# 5. 体检
python3 ~/.claude/skills/video-transcript/scripts/transcript.py --doctor
```

---

## 🔒 隐私

- **全程本地离线**：视频只在你电脑上处理，不上传任何视频或音频到第三方
- 不需要任何 API Key，零成本
- 可选热词配置在本地 `.env`（权限 600），`.gitignore` 已屏蔽
- 首次使用需联网下载 FunASR 模型（约 234M，ModelScope），之后全离线

---

## 🤝 反馈

平台前端改版导致抓取失效是常态。遇到失败：
1. 跑 `--doctor`
2. 看 `FALLBACK.md` 手动绕开
3. 提 issue 附上 URL + 报错日志

---

## 📄 License

[MIT](LICENSE) — 随便用，欢迎 Star ⭐ 和 PR。
