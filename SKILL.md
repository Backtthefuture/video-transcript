---
name: 视频文案提取
description: |
  视频/播客逐字稿提取专家(FunASR 本地转录,无需 API Key)。视频走 SenseVoice-Small(CPU 高速,自带标点);播客/访谈走 paraformer + CAM++ **说话人分离**链路,输出「说话人区块版」逐字稿(主持人/嘉宾自动识别 + 补标点 + 语义分段)。支持 微信视频号 / 抖音 / 小红书 / B站 / YouTube / 小宇宙播客 / 本地视频音频。**视频核心交付为「整理优化版」**(补标点+合并碎句+修正识别错误+语义化小标题+对照表),原始逐字稿仅作内部素材与对照存档,不向用户全文展示。全程在用户电脑后台运行(headless),不弹窗、不要求登录视频网站,离线零成本。
  触发场景:
  - 用户说"出文案"、"视频文案"、"提取文案"、"文案提取"、"视频文案提取"
  - 用户说"出逐字稿"、"提取逐字稿"、"转文字"、"视频转文字"
  - 用户说"听写视频"、"视频字幕"
  - 用户说"主持稿"、"出主持稿"
  - 用户说"播客转文字"、"播客逐字稿"、"区分说话人"、"说话人分离"
  - 用户使用 /video-transcript 命令
  - **用户贴一个视频链接(微信视频号/抖音/小红书/B站/YouTube)→ 直接开始转录,不询问意图**
  - **用户贴一个播客/音频链接(小宇宙 xiaoyuzhoufm.com/episode/、喜马拉雅 ximalaya.com/sound/、Apple Podcasts)→ 自动走说话人分离链路**
  - **用户贴微博/知乎/西瓜视频/AcFun 等其他链接 → 也直接试转录(yt-dlp 兜底),不要先反问**
  - 用户给本地视频/音频文件路径(mp4/mp3/m4a/wav 等)→ 直接转录
  - 只有用户明确说"只下载"、"保存MP4"、"下载视频不用转录"时,才走纯下载流程
  - 已知不支持:Spotify(DRM)、快手 — 脚本会打印具体原因和替代做法,照着转达即可
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
user-invocable: true
---

# 视频文案提取专家

> 输入链接 → 解析一次 → 直链提音频(+模型预热并行) → FunASR → 机器预整理 → LLM 只出 patch → 主持稿/整理优化版

## 阶段 0 · 定位 skill 根目录(第一件事)

```bash
VT_HOME="$(
  for d in "$HOME/.workbuddy/skills/video-transcript" \
           "$HOME/.agents/skills/video-transcript" \
           "$HOME/.Codex/skills/video-transcript" \
           "$HOME/.codex/skills/video-transcript" \
           "$HOME/.claude/skills/video-transcript" \
           "$(pwd)/.Codex/skills/video-transcript" \
           "$(pwd)/.claude/skills/video-transcript" \
           "$(pwd)/skills/video-transcript" \
           "$HOME/.Codex/plugins/video-transcript/video-transcript" \
           "$HOME/.claude/plugins/video-transcript/video-transcript"; do
    [ -f "$d/SKILL.md" ] && echo "$d" && break
  done
)"
export VT_HOME
echo "VT_HOME=$VT_HOME"
```

如果输出为空,让用户给出路径后 `export VT_HOME=<路径>`。
之后**所有命令**都通过 `"$VT_HOME/scripts/transcript.py"`,不要硬编码路径。

优先用带 funasr 的解释器:

```bash
VT_PY="${VT_PY:-$HOME/.workbuddy/binaries/python/envs/default/bin/python}"
[ -x "$VT_PY" ] || VT_PY="/opt/anaconda3/bin/python3.12"
[ -x "$VT_PY" ] || VT_PY="python3"
```

## 阶段 1 · 意图分流(贴链接 = 直接转录,不问)

**默认规则:用户贴视频/播客链接 → 直接进入转录,不询问。**

| 用户行为 | 处理 |
|---|---|
| 贴视频链接,无其他说明 | **直接转录,不问** |
| 贴播客/音频链接(小宇宙/喜马拉雅/Apple Podcasts) | **直接转录,自动带说话人分离** |
| 贴链接 + 说「文案/逐字稿/主持稿/转文字」 | 直接转录 |
| 明确说「只下载」「保存MP4」「不用转录」 | 走 `video-download` |
| 只说「处理视频」但没附链接 | 问用户要链接 |

平台支持分三档,不确定的链接**直接试,不要预先劝退**:

| 档位 | 平台 | 说明 |
|---|---|---|
| 专门解析 | B站(含 b23.tv)、抖音、小红书、YouTube、微信视频号、小宇宙单集 | 最稳 |
| 播客链路 | 小宇宙单集、喜马拉雅单集、Apple Podcasts | 自动说话人分离 |
| yt-dlp 兜底 | 微博、知乎、西瓜视频、AcFun 等 | 能跑,默认走视频链路;要区分说话人加 `--speakers` |
| 不支持 | Spotify(DRM)、快手 | 脚本给出原因+替代做法 |

常见误贴:小宇宙**节目主页**(`/podcast/`)和喜马拉雅**专辑页**(`/album/`)都不是单集页,
脚本会明确提示改用单集链接 —— 把提示原样转达给用户,别自己瞎猜别的原因。

仅下载时定位 `video-download` 后跑 `download_video.py "<URL>" --json`,不要再进入转录。

## 阶段 2 · 依赖体检(首次/可疑时)

已验证过且环境没变化的,**跳过体检**。首次/换电脑/报错才跑:

```bash
"$VT_PY" "$VT_HOME/scripts/transcript.py" --doctor
```

有 ✗ 项就跑 `bash "$VT_HOME/install.sh"`。全 ✓ 才进入阶段 3。

## 阶段 3 · 一条命令跑完下载+转录+预整理

用户给了链接就立刻跑,不要先单独 probe,不要再调一次 download:

```bash
"$VT_PY" "$VT_HOME/scripts/transcript.py" "<URL或本地路径>"
```

可选:

- `--force` 忽略同 URL 缓存
- `--keep-video` 额外保存完整 MP4(默认只提音频)
- `--no-daemon` 不用常驻模型(默认会自动拉起 FunASR daemon)

脚本会自动:

0. **缓存** — 同一 URL 已有预整理稿则秒回,你直接进入阶段 4
1. **解析一次** — 视频号优先 HTTP(元宝 Cookie),失败才开一次浏览器;B 站/抖音/小红书探测时缓存直链
2. **并行** — 后台预热 FunASR daemon,同时 ffmpeg 直链提 16k wav(不下完整 MP4)
3. **转录** — 长视频按 ≤5 分钟切块;有 daemon 则顺序流式写出,无 daemon 则最多 2 进程并行
4. **预整理** — 机器完成切段/合并碎句/候选标题,写出 `*_预整理.md` + `*_polish_brief.json`

stderr 会先打 📊 评估表。**立刻复述给用户**(标题/时长/预估耗时),不要等全部跑完。

长视频还会写 `$VT_HOME/outputs/.partial/<hash>/chunk_XX.md` 和 `progress.json`。
转录还没结束时,你可以读已经完成的 chunk,**边转边改标题/纠错**,最后再合并进一份 patch。

完成后 stderr 有 `----- VT_OUTPUTS -----` 一行 JSON,里面有:

- `preorganized_path` — 预整理稿(**你的主输入**)
- `polish_brief_path` — 增量润色任务书
- `transcript_path` — 原始逐字稿(对照存档,不要在对话里全文展示)
- `stream_dir` — 分块流式目录

## 阶段 4 · 你(agent)必须做的事:只出 patch,不要重写全文

核心交付仍是「整理优化版 / 主持稿」。但机器已经做完分段和合并,**禁止**再把全文抄进 `content.json`,也**禁止**在对话里把同一篇稿子重写两遍。

### 正确流程(必须按此执行)

1. 读 `preorganized_path` 全文(以文件为准,stdout 可能截断)
2. 读 `polish_brief_path`
3. **只写一份很小的 `patch.json`**,字段:
   - `title`: 可选,润色后的大标题
   - `headings`: 与章节顺序对齐的语义化小标题(可只改需要改的)
   - `fixes`: `[{"from":"原词","to":"修正词","confidence":"high|low"}]`（`high` 会自动替换全文对应词）
   - `paragraph_edits`: 仅当整段结构都要改时才给 `{"section":1,"para":0,"replace":"..."}`
4. 渲染(一次即可):

```bash
"$VT_PY" "$VT_HOME/scripts/make_optimized.py" \
  --from-md "<preorganized_path>" \
  --patch "<patch.json>" \
  --filename "YYYY-MM-DD_标题30字内_整理优化版" \
  --output-dir "$VT_HOME/outputs"
```

5. 读取生成的 `*_整理优化版.md`,**在对话里完整输出整理优化版全文**(纯 Markdown,不要用代码块包裹)
6. 需要预览时 `present_files` 只传整理优化版 `.html`(第一位) + `.md`
7. 末尾附一行落盘路径

### 章节很多时的并行润色

`polish_brief.json` 里 `sections` **超过 4 个**时:

- 按 4 章一组拆成多个小 patch(只要 `headings` 切片 + 该段 `fixes` / `paragraph_edits`)
- 可以并行想、但最后必须合成**一个** `patch.json` 再跑 `make_optimized.py`
- 仍然不要输出多份全文

### 绝对不要做

- ❌ 把原始无标点逐字稿贴进对话
- ❌ `--dump-template` 再把全文填进 `content.json`(旧流程已废弃)
- ❌ 只展示前几段、总结或改写观点
- ❌ 缓存命中后还重新下载/转录(除非用户说「重跑」/`--force`)

### 缓存命中

脚本打印 `[OK] 缓存命中` 时:直接用已有 `预整理.md` 做 patch → 渲染整理优化版。用户明确要求重跑才加 `--force`。

## 播客/说话人分离模式

**触发**:小宇宙 episode 链接自动启用;其他输入(本地音频/任意 URL)加 `--speakers` 强制启用。

```bash
# 小宇宙链接:自动说话人分离,无需额外参数
"$VT_PY" "$VT_HOME/scripts/transcript.py" "https://www.xiaoyuzhoufm.com/episode/xxxx"

# 本地音频/其他来源:强制说话人分离,可手动指定人名
"$VT_PY" "$VT_HOME/scripts/transcript.py" 访谈.m4a --speakers --host 张三 --guest 李四

# 只重跑后处理(调版式/改人名),复用已有转录,不重跑 ASR、不重新下载
"$VT_PY" "$VT_HOME/scripts/transcript.py" <同一输入> --reformat --host 张三 --guest 李四
```

**版式不满意/人名认错时用 `--reformat`,不要用 `--force`。** `--force` 会连十几分钟的 ASR 一起重跑；
`--reformat` 复用 `outputs/.partial/<hash>/transcription.json`,1 小时单集约 1 分钟出新版。

小宇宙以外的播客平台(喜马拉雅/Apple Podcasts)没有音频直链,自动用 yt-dlp 取音频,
拿不到 Shownotes,所以说话人会回退成「说话人 A/B」,想要真名就手动传 `--host` / `--guest`。

**产物**:`*_逐字稿.md`(说话人区块,成品)、`*_逐字稿.srt`(带说话人前缀、句级时间轴,可直接压字幕)、
`*_outputs.json`、`.partial/<hash>/transcription.json`(原始转录,`--reformat` 的输入)。

**转录期间**每 30 秒打一行 `[转录中] 已跑 x 分,约 y%,预计还需 z 分`。
进度是按音频时长估的,不是真实完成度;转录本身是一次不可中断的推理,
中途失败只能重跑(**不切块是有意的**:CAM++ 的说话人编号只在单次推理内一致,切块会让同一个人在不同块里换编号)。

ASR 一落盘就删掉临时 wav(1 小时单集约 115MB);要留音频排查加 `--keep-audio`。

与视频链路的区别:

| | 视频链路 | 播客链路 |
|---|---|---|
| 引擎 | SenseVoice-Small(快,~6x 实时) | paraformer + CAM++(慢,约音频时长 25%,1 小时单集约 15 分钟) |
| 说话人 | 无 | 自动分离 + 主持人/嘉宾映射 |
| 输出 | `*_预整理.md`(需 agent patch 润色) | `*_逐字稿.md`(**成品,直接交付,不走 patch 流程**)+ `*_逐字稿.srt` |
| 首次模型 | SenseVoice 234M | paraformer/CAM++/VAD/punc 约 1GB |

自动化处理:小宇宙页 `__NEXT_DATA__` 解析标题/音频直链/Shownotes → 从 Shownotes 提取主持人/嘉宾姓名(取不到回退「说话人 A/B」) → 半截词缝合 → ct-punc 补标点 → 语义分段 → 通用 AI 术语纠错。

输出版式(说话人区块):

```markdown
## 说话人
- **主持人** 曲凯:约 30% 时长
- **嘉宾** 孟繁青:约 70% 时长

## 逐字稿
### 00:22 – 00:30　主持人 · 曲凯
因为这块也很热嘛,所以今天很开心请到…
```

播客专属词表扩展:在 `$VT_HOME/.podcast_glossary.json` 写 `[["错误词","修正词"], ...]`,会叠加在内置通用 AI 术语表之上。

agent 拿到播客 `*_逐字稿.md` 后:**直接在对话里输出全文**(或按用户要求摘要),不要再跑 `make_optimized.py`。

## 阶段 5 · 异常处理

| 场景 | 处理 |
|---|---|
| `--doctor` 报缺依赖 | `bash "$VT_HOME/install.sh"` |
| funasr 未安装 | `pip install funasr torchaudio` |
| 首次运行联网失败 | 首次需下载 SenseVoice-Small(约 234M) |
| 播客模式首次很慢 | 首次自动下载 paraformer/CAM++/VAD/punc 模型(约 1GB),之后走本地缓存 |
| 播客版式/人名要改 | 用 `--reformat`(秒级),别用 `--force`(会重跑 ASR) |
| 播客转录中途中断 | 只能重跑,ASR 不支持续跑(切块会打乱说话人编号);已完成的单集看 `.partial/<hash>/transcription.json` |
| 某节目专有名词老是错 | 写 `$VT_HOME/.podcast_glossary.json`: `[["错词","对词"]]`,优先于内置词表 |
| 小宇宙解析失败 | 页面结构变化;可先下载音频再 `--speakers` 转本地文件 |
| 抖音图文笔记 | 提示仅支持视频 |
| 平台前端改版 | 看 `$VT_HOME/FALLBACK.md` |
| 视频号缺登录态 | `"$VT_PY" "$VT_HOME/scripts/sph_resolver.py" --login` |
| 视频号公共 Worker 1042 | 已默认走元宝 HTTP,无需处理 |
| 要保留 MP4 | 给脚本加 `--keep-video`,或走 `video-download` |

视频号解析默认 `yuanbao-login`。`sph_resolver.py` 先抽 Cookie 走 HTTP,失败才开一次浏览器。

```bash
"$VT_PY" "$VT_HOME/scripts/sph_resolver.py" --check
"$VT_PY" "$VT_HOME/scripts/sph_resolver.py" --login
"$VT_PY" "$VT_HOME/scripts/asr_daemon.py" --status
```

## 命令行选项

| 参数 | 说明 |
|---|---|
| `input` | 视频 URL 或本地路径 |
| `--title` | 覆盖标题 |
| `--no-save` | 不落盘 |
| `--output-dir` | 改保存路径 |
| `--doctor` | 体检 |
| `--force` / `--no-cache` | 忽略同 URL 缓存 |
| `--keep-video` | 额外保存 MP4 |
| `--no-daemon` | 不使用常驻模型 |
| `--speakers` | 强制说话人分离模式(小宇宙链接自动启用) |
| `--host` / `--guest` | 说话人分离模式手动指定主持人/嘉宾姓名 |
| `--reformat` | 复用已有转录只重跑后处理(调版式/改人名,不重跑 ASR) |
| `--keep-audio` | 播客模式转录后保留临时 wav(默认清理) |

## Notes

- 视频引擎 FunASR SenseVoice-Small:中文 CER 7.81%,模型 234M,CPU 约 6x 实时
- 播客引擎 paraformer-zh + fsmn-vad + ct-punc + CAM++:带说话人分离,约 0.15x 实时
- 视频号不再 probe+download 各解析一遍;默认也不下完整视频
- FunASR daemon 常驻后,后续任务跳过 15~30s 模型加载;空闲 30 分钟自动退出
- 时间戳是段落级,用于章节定位
- 预估耗时:`时长/8 + 15s`(直链音频 + 已预热模型)
- 热词:`$VT_HOME/.env` 里 `FUNASR_HOTWORD=词1 词2`
- 全程离线转录,不需要 API Key
