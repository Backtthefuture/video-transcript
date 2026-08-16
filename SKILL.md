---
name: 视频文案提取
description: |
  视频文案提取专家(FunASR SenseVoice-Small 中文转录,CPU 高速,自带标点,无需 API Key)。支持 微信视频号 / 抖音 / 小红书 / B站 / YouTube / 本地视频,把视频人声一键转成"分段小标题 + 段落级时间戳"的逐字稿文案。**核心交付为「整理优化版」**(补标点+合并碎句+修正识别错误+语义化小标题+对照表),原始逐字稿仅作内部素材与对照存档,不向用户全文展示。全程在用户电脑后台运行(headless),不弹窗、不要求登录视频网站,离线零成本。
  触发场景:
  - 用户说"出文案"、"视频文案"、"提取文案"、"文案提取"、"视频文案提取"
  - 用户说"出逐字稿"、"提取逐字稿"、"转文字"、"视频转文字"
  - 用户说"听写视频"、"视频字幕"
  - 用户使用 /video-transcript 命令
  - **用户贴一个视频链接(微信视频号/抖音/小红书/B站/YouTube)→ 直接开始转录,不询问意图**
  - 只有用户明确说"只下载"、"保存MP4"、"下载视频不用转录"时,才走纯下载流程
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
user-invocable: true
---

# 视频文案提取专家

> 输入视频链接或本地路径 → 自动探测 → 后台下载 → 提音频 → FunASR 转录 → 严格逐字稿(stdout + 落盘)

## 阶段 0 · 定位 skill 根目录(第一件事)

被触发时你必须先定位自己,跑这段:

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

如果输出为空,说明 skill 安装位置非标准。让用户给出路径,然后手工 `export VT_HOME=<路径>`。
之后**所有命令**都通过 `"$VT_HOME/scripts/transcript.py"`,不要用相对路径或绝对路径硬编码。

## 阶段 1 · 意图分流(贴链接 = 直接转录,不问)

**默认规则:用户贴视频链接 → 直接进入转录流程,不需要询问。**

只有在以下情况才需要询问或分流:
- 用户明确说"只下载"、"下载视频"、"保存MP4"、"不用转录" → 走纯下载,调用 `video-download`
- 用户只说"处理这个视频"但**没有附链接** → 问用户要链接

| 用户行为 | 处理 |
|---|---|
| 贴视频链接(视频号/抖音/小红书/B站/YouTube),无其他说明 | **直接进入转录流程(阶段 3),不问** |
| 贴链接 + 说"文案/逐字稿/转文字" | 直接进入转录流程 |
| 明确说"只下载"、"保存MP4"、"不用转录" | 走纯下载(见下方) |
| 只说"处理视频"但没附链接 | 问用户要链接 |

仅下载时,定位并调用 `video-download`:

```bash
VD_HOME="$(
  for d in "$HOME/.workbuddy/skills/video-download" \
           "$HOME/.agents/skills/video-download" \
           "$HOME/.Codex/skills/video-download" \
           "$HOME/.codex/skills/video-download" \
           "$HOME/.claude/skills/video-download" \
           "$(pwd)/skills/video-download"; do
    [ -f "$d/SKILL.md" ] && echo "$d" && break
  done
)"
export VD_HOME
echo "VD_HOME=$VD_HOME"
python3 "$VD_HOME/scripts/download_video.py" "<URL或本地路径>" --json
```

仅下载完成后,按 `video-download` 的验收标准返回 durable MP4 路径、metadata 路径、时长、分辨率、视频/音频编码和文件大小;不要再进入转录流程。

## 阶段 2 · 依赖体检(首次/可疑时)

> **已验证过且环境没变化的,跳过体检直接跑转录。** 体检只在首次使用、换电脑、报错时做。

第一次跑或者遇到报错时,先做体检:

```bash
python3 "$VT_HOME/scripts/transcript.py" --doctor
```

如果有 ✗ 项,告诉用户:
- 缺 ffmpeg / playwright / chromium / API Key 等 → 跑一键安装向导:
  ```bash
  bash "$VT_HOME/install.sh"
  ```
- 体检全 ✓ 才进入阶段 3。

## 阶段 3 · 触发方式与执行

> 运行环境提示:WorkBuddy 中请用隔离 venv 的 python 执行(已装 funasr/playwright):
> `/Users/superhuang/.workbuddy/binaries/python/envs/default/bin/python "$VT_HOME/scripts/transcript.py"`

**用户给了视频链接(URL 或本地路径)就直接跑,不需要等确认:**

```bash
python3 "$VT_HOME/scripts/transcript.py" "<URL或本地路径>"
```

唯一引擎:**FunASR SenseVoice-Small**(中文最优,CPU 高速,自带标点)。

支持的输入:
- B 站:`https://www.bilibili.com/video/BVxxx` 或 `b23.tv/xxx` 短链
- 抖音:`https://www.douyin.com/video/xxx`、`v.douyin.com/xxx`、`douyin.com/jingxuan?modal_id=xxx`
- 小红书:`xiaohongshu.com/discovery/item/xxx`、`xiaohongshu.com/explore/xxx`、`xhslink.com/xxx`
- YouTube:`youtube.com/watch?v=xxx`、`youtu.be/xxx`
- 微信视频号:`https://weixin.qq.com/sph/xxx`、`channels.weixin.qq.com/finder-preview/pages/sph?id=xxx`
- 本地视频文件路径

脚本自动:
0. **探测** — 启动 headless 浏览器,拿标题、时长、视频/音频直链;打印 📊 评估表 + 预估耗时
1. **下载** — 复用探测拿到的直链(不重启浏览器);B 站走 dash 流(分别下载 video/audio + ffmpeg 合并);其他平台直接 mp4
   - 微信视频号会先调用 `video-download` 下载为本地 MP4,再进入转录流程;解析方式由 `video-download/.env` 的 `WECHAT_RESOLVER` 决定
2. **提音频** — ffmpeg 提取 16k 单声道 wav(输入已是 wav 则跳过)
3. **转录** — FunASR SenseVoice-Small,CPU 高速(约 6x 实时),自带中文标点;按句号切句 + 字数比例估算时间戳
4. **分段** — 口述话题转折检测("第 N 个/点"等标记优先切段) + 60s 兜底,输出段落级时间戳

### ⚠️ 你(agent)必须做的三件事

**(1) 评估表复述 — 给用户等待预期**

脚本启动后,stderr 第一段就会打印 📊 视频探测评估表。**你必须立刻把它复述给用户**,告诉他:
- 视频标题、时长、分段数
- **预估耗时**(给用户一个等待预期,这点最重要)

不要等转录跑完才说,**先复述评估表 → 再继续等待转录**。如果用户看不到时长和耗时预估,会以为程序卡死。

例:
> 视频探测完成:
> - 平台:B 站 / 标题:《xxx》
> - 时长 17 分 12 秒,会切成 3 段独立转录
> - **预估耗时 3 分 20 秒 ~ 5 分 25 秒**,正在跑,稍等...

**(2) 转录完成后,直接在对话中输出「整理优化版」全文**

核心交付物是**整理优化版**。脚本会把原始逐字稿打印到 stdout 并落盘 `*_transcript.md`,但**原始稿不在对话里全文展示**——它仅作为整理素材与对照存档。

**正确做法(必须按此执行)**:
1. 读取落盘的 `*_transcript.md`(stdout 可能截断,一律以文件为准)
2. 整理出优化版(见第 (3) 条)
3. **直接在对话回复中完整输出整理优化版全文**(纯 Markdown,不要用代码块包裹)
4. 同时生成 .md + .html 文件落盘
5. 调用 `present_files` 打开 .html 预览 + .md 附件
6. 末尾附一行说明文件落盘路径

**「直接输出全文」= 整理优化版的每个章节、每个段落、对照表,全部写在你的回复正文里,不能只展示前几段,不能只给文件不给内容。用户要的是打开对话就能直接读完的完整文案。**

错误做法:
- ❌ 在对话里把原始无标点逐字稿逐行贴出(用户明确不想要)
- ❌ 只展示整理优化版前几段就省略
- ❌ 总结/精简整理优化版内容(要逐段展示)

**(3) 必须同时产出「整理优化版」(默认,勿等用户要求)**

转录完成后,**默认自动**整理一版 `*_整理优化版.md` + `.html`,不要等用户提。用户曾因缺此版本反馈"格式和之前不一样"——这是默认交付标准,不是可选项。

做法:读取落盘的 `*_transcript.md` 全文(注意:stdout 可能截断,一律以落盘文件为准),基于逐字稿做四件事:
0. **分段对齐(先做,决定章节结构)** — 章节边界必须以**视频口述结构**为准,不能沿用脚本 60s 机械切分:
   - 口述编号开头的论述(如"第一个问题""第四个点""第五个就是""还有一个问题")必须**完整成节**,不得被切成两节;
   - 相邻段落属同一话题论述的,须**合并回一节**(例:"第五个就是…奢华无边界"与其延续"个人成功→佣金预判→重排场轻实操"同属一个问题,须同一节);
   - 口述中非编号的"药方/建议/补充"内容可单列小节,但同一话题内不要人为分叉;
   - 时间戳为段落级,相邻节时间范围**允许重叠**(如实反映口述边界,不强求对齐到秒)。
1. **补标点断句** — FunASR 自带标点但仍有口语碎句,按语义补全句读、修正为通顺表达
2. **合并碎句** — 按语义合并为自然段落(每段 1-3 个自然段),替换"每句一行"的碎行
3. **修正识别错误** — 专名/术语/人名/成语按上下文纠错(例:内飞泰→内啡肽、普利高金→普里高津、Errorglass→Ira Glass、补梦我→补梦网);不确定的标 `〔?〕`,不要硬猜
4. **语义化小标题** — 为每章起有信息量的标题(例:"一厘米的切口,挖一千米深"),替代机器截断标题;标题可带编号(如"问题五:奢华堆砌无边界,个人光环消耗信任"),与口述编号对应

文末必须附 **「识别修正对照表」**:分「已修正(确信度高)」和「存疑(〔?〕标注,建议对照原视频核对)」两栏,逐条列出 原词 → 修正词。**保留原话原意,不总结、不增删观点。**

生成方式:用通用渲染脚本 `$VT_HOME/scripts/make_optimized.py`:
1. `--dump-template` 输出 content.json 骨架
2. 按骨架填入整理好的 sections(语义标题/时间戳/补标点段落)与 fixes(对照表),保存为 content.json
3. `python3 "$VT_HOME/scripts/make_optimized.py" --content content.json` → 自动生成 `.md` + `.html`(样式与旧版整理优化版一致:工具栏复制/下载按钮 + 目录 + 语义章节 + 对照表)
若不便用脚本,也可按同样结构手工写 .md/.html。

输出命名:`YYYY-MM-DD_标题(≤30字)_整理优化版.md/.html`,放在 `$VT_HOME/outputs/`,与原始 `_transcript.md` 并存作为对照。

## 阶段 4 · 输出去处

脚本会**三个去处同时输出**(均为原始逐字稿,作为整理素材与对照存档):
1. **stdout 直出**原始 Markdown 全文 — 仅作 agent 整理时的读取来源,**不在对话里全文展示**
2. **.md 落盘**到 `$VT_HOME/outputs/YYYY-MM-DD_<标题30字>_transcript.md` — 原始稿,源码存档
3. **.html 预览版**同步生成到同目录 — 原始稿预览

> **展示优先级**:对话与预览面板呈现的核心是**整理优化版**(见阶段 3 第 (3) 条);原始稿文件保留作对照,一般不主动展开。

**取消落盘**:`--no-save`
**改保存路径**:`--output-dir <path`

**整理优化版落盘**:`$VT_HOME/outputs/YYYY-MM-DD_标题(≤30字)_整理优化版.md/.html`,与原始 `_transcript.md` 并存。渲染脚本见 `$VT_HOME/scripts/make_optimized.py`。

### Agent 呈现规范(WorkBuddy 中)

- **对话里**:直接发**整理优化版** markdown 全文(不要用 ` ```markdown ... ``` ` 代码块包裹,否则用户看到的是 raw 源码);原始逐字稿不展示全文
- **预览面板**:用 `present_files` 传**整理优化版** `.html` 文件,让 WorkBuddy 自动在**右侧结果区/内置浏览器**打开预览;整理优化版 `.md` 作为产物卡片附件;原始 `_transcript.*` 文件不主动呈现(可留档)
- **不要贴裸 http 链接**:聊天正文里的普通链接点击会走系统默认浏览器(标准行为,无 in-app 协议);告知用户"预览已在右侧结果区打开",需要重开时点右侧「产物」卡片或「浏览器」视图即可
- **整理优化版(默认必出)**:见阶段 3 第 (3) 条,每次转录**必须**产出 `*_整理优化版.md/.html` 并作为唯一核心交付;原转录版仅作对照存档。`present_files` 只传整理优化版(html 第一位优先预览)

### 评估表样例

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

### 输出格式样例(funasr 引擎,2026-08 新版)

```markdown
# 视频标题

> 来源: 微信视频号 | 链接: <用户提供的原视频URL> | 时长 12:06 | 引擎: FunASR(SenseVoice-Small) | 生成: 2026-08-08 18:48
> 关键词: 内容 · 观众 · 创作

## 目录

1. 卖豪宅的杰森今天给我发了 [00:00]
2. 但在我看到的是他这个账号出现 [01:02]

## 1. 卖豪宅的杰森今天给我发了 [00:00 - 01:02]

卖豪宅的杰森今天给我发了一个邀请函，他说他的新公司要搬了，然后邀请我去参加他的聚会。我拒绝了，因为我从来不参加任何聚会。

## 2. 但在我看到的是他这个账号出现 [01:02 - 01:51]

但是我看到的是，他这个账号出现了巨大的问题。我一个个讲一下，优点我就不说了，今天就说问题吧。
```

特性(funasr 引擎):
- **口述话题转折切分**:识别"第 N 个/点""还有一个问题""接下来""再说一遍"等口述标记,在话题转折处**优先切段**,避免"第五个问题"这类完整论述被拦腰切断;无标记时保持 60s 节奏(规则见 `scripts/transcript.py` 的 `_is_topic_marker`)
- **自带中文标点**:SenseVoice 输出自带逗号/句号/问号,无需后处理补标点
- **语义小标题**:规则法生成(去口语弱词 + 首句截断 ≤14 字,太短拼关键词),替代"第 N 段"
- **一键目录**:段落 > 3 时自动在头部生成"标题 + 起始时间戳"导航
- **句级切分 + 时间戳**:按句号切句,按字数比例估算每句时间戳(基于 wav 总时长)
- **元信息头**:来源平台 / 时长 / 引擎 / 生成时间,便于存档追溯
- **关键词**:全文 jieba 高频词提取,放头部
- **文件命名**:`YYYY-MM-DD_标题(≤30字)_transcript.md`,不再超长
- **段落级时间戳**:每段开头标 `[MM:SS - MM:SS]`,定位方便
- **降级**:无 jieba 时标题回退为首句截断;无任何文字时标注"(未识别到语音)"
- **逐字转录**:保留口语词("呃""那""啊""就是")、网络梗、停顿,不总结、不改写
- **无人声段落**:用 `_(此处无人声,XX秒)_` 标注

## 阶段 5 · 异常处理

| 场景 | 处理 |
|---|---|
| `--doctor` 报缺依赖 | 跑 `bash "$VT_HOME/install.sh"` |
| funasr 未安装 | `pip install funasr torchaudio` |
| 首次运行联网失败 | 首次需联网下载模型(约 234M,远小于 Whisper 1.5GB);国内可重试或检查网络 |
| 返回整段文本无分段 | 预期行为:脚本按句号切句 + 字数比例估算时间戳;整理优化版阶段按口述编号最终对齐 |
| 抖音图文笔记(note 链接) | 提示用户不支持图文,仅支持视频 |
| 平台前端改版导致抓取失败 | 看 `$VT_HOME/FALLBACK.md` 走人工兜底 |
| 微信视频号提示缺少 video-download | 重跑 `bash "$VT_HOME/install.sh"` 自动装配套 skill,或手动 `npx skills add Backtthefuture/video-download -a claude-code -g -y` |
| 微信视频号提示缺少 SPH_COOKIE/YUANBAO_COOKIE | 该提示只在 `WECHAT_RESOLVER=cookie` 时出现;默认已改为 `yuanbao-login`,无需配 Cookie |
| 微信视频号公共 Worker 失效(错误码 1042) | 已自动回退到**元宝登录态解析**(推荐),无需处理;见下方说明 |

视频号解析方式由 `video-download/.env` 决定。**默认即元宝登录态(`yuanbao-login`),开箱即用**。如需显式配置:

```env
# 默认(推荐):复用本地元宝登录态,免 Cookie、免第三方
WECHAT_RESOLVER=yuanbao-login
# 备选:公共 Worker(失效时自动回退元宝)
# WECHAT_RESOLVER=public-worker
# 备选:手动 Cookie
# WECHAT_RESOLVER=cookie
```

也可临时显式覆盖:

```bash
VIDEO_DOWNLOAD_WECHAT_RESOLVER=public-worker python3 "$VT_HOME/scripts/transcript.py" "https://weixin.qq.com/sph/xxx"
```

### 微信视频号:元宝登录态解析(默认)

公共 Worker(`sph.litao.workers.dev`)已失效(返回微信错误码 1042)。`download_video.py` 现在**默认走元宝登录态解析**(`WECHAT_RESOLVER=yuanbao-login`):复用 `~/.workbuddy/credentials/yuanbao_state.json` 的持久化登录态,走腾讯官方接口,不导出 Cookie、不依赖第三方服务;配置为 `public-worker` 时失败也会自动回退到这条链路。

安装时(`install.sh`)会自动引导扫码建立登录态;也可手动维护:

```bash
# 建立/更新登录态(弹出浏览器,微信扫码一次)
python3 "$VT_HOME/scripts/sph_resolver.py" --login

# 检查登录态是否有效
python3 "$VT_HOME/scripts/sph_resolver.py" --check

# 直接解析视频号链接(输出 JSON,含 direct_url)
python3 "$VT_HOME/scripts/sph_resolver.py" "https://weixin.qq.com/sph/xxx"
```

登录态有效期与微信授权一致,过期后重新 `--login` 扫码即可。

## 命令行选项

| 参数 | 说明 |
|---|---|
| `input` | 视频 URL 或本地文件路径(必需,`--doctor` 时可省) |
| `--title` | 视频标题(默认用探测到的) |
| `--no-save` | 不落盘 .md(默认会保存到 `$VT_HOME/outputs/`) |
| `--output-dir` | 改保存路径 |
| `--doctor` | 体检模式:检查依赖+配置 |

## Notes

- **唯一引擎 FunASR(SenseVoice-Small)**:中文 CER 7.81%(vs Whisper 20%)、模型仅 234M(vs 1.5GB)、CPU 约 6x 实时(M4 实测 12:56 音频纯转录 76s);**自带中文标点**(逗号/句号/问号);按句号切句 + 字数比例估算时间戳,口述话题转折标记同样适用
- 时间戳精度为段落级(不是词级/句级),用于章节定位
- 默认 stdout 直接输出原始 Markdown 全文(仅作 agent 整理的读取来源,**不在对话里展示**);**同时**落盘;对话/预览呈现的是整理优化版
- 预估耗时模型(基于实测):`时长/6 + 60s`(含提音频与模型加载),给 ±20% 范围
- 可选热词:在 `$VT_HOME/.env` 配 `FUNASR_HOTWORD=词1 词2` 可提升专有名词识别率
- 全部离线运行,不需要任何 API Key
