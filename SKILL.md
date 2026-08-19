---
name: 视频文案提取
description: |
  视频文案提取专家(FunASR SenseVoice-Small 中文转录,CPU 高速,自带标点,无需 API Key)。支持 微信视频号 / 抖音 / 小红书 / B站 / YouTube / 本地视频,把视频人声一键转成"分段小标题 + 段落级时间戳"的逐字稿文案。**核心交付为「整理优化版」**(补标点+合并碎句+修正识别错误+语义化小标题+对照表),原始逐字稿仅作内部素材与对照存档,不向用户全文展示。全程在用户电脑后台运行(headless),不弹窗、不要求登录视频网站,离线零成本。
  触发场景:
  - 用户说"出文案"、"视频文案"、"提取文案"、"文案提取"、"视频文案提取"
  - 用户说"出逐字稿"、"提取逐字稿"、"转文字"、"视频转文字"
  - 用户说"听写视频"、"视频字幕"
  - 用户说"主持稿"、"出主持稿"
  - 用户使用 /video-transcript 命令
  - **用户贴一个视频链接(微信视频号/抖音/小红书/B站/YouTube)→ 直接开始转录,不询问意图**
  - 只有用户明确说"只下载"、"保存MP4"、"下载视频不用转录"时,才走纯下载流程
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

**默认规则:用户贴视频链接 → 直接进入转录,不询问。**

| 用户行为 | 处理 |
|---|---|
| 贴视频链接,无其他说明 | **直接转录,不问** |
| 贴链接 + 说「文案/逐字稿/主持稿/转文字」 | 直接转录 |
| 明确说「只下载」「保存MP4」「不用转录」 | 走 `video-download` |
| 只说「处理视频」但没附链接 | 问用户要链接 |

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

## 阶段 5 · 异常处理

| 场景 | 处理 |
|---|---|
| `--doctor` 报缺依赖 | `bash "$VT_HOME/install.sh"` |
| funasr 未安装 | `pip install funasr torchaudio` |
| 首次运行联网失败 | 首次需下载 SenseVoice-Small(约 234M) |
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

## Notes

- 唯一引擎 FunASR SenseVoice-Small:中文 CER 7.81%,模型 234M,CPU 约 6x 实时
- 视频号不再 probe+download 各解析一遍;默认也不下完整视频
- FunASR daemon 常驻后,后续任务跳过 15~30s 模型加载;空闲 30 分钟自动退出
- 时间戳是段落级,用于章节定位
- 预估耗时:`时长/8 + 15s`(直链音频 + 已预热模型)
- 热词:`$VT_HOME/.env` 里 `FUNASR_HOTWORD=词1 词2`
- 全程离线转录,不需要 API Key
