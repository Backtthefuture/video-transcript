# video-transcript｜视频文案提取

给视频、播客链接或本地媒体，获取可审阅的文字稿。本版使用夸克网盘官方 Skill 的文件问答能力取稿，再由 Agent 检查原文并做有依据的轻校对。

**准确性边界：**云端返回正文不等于逐字准确或全文完整。没有逐句听校时，应标明完整性与准确率未验证。本版不运行本地 FunASR，不提供自动说话人分离、句级时间轴或 SRT。

## 工作流程

1. 登记单条或批次输入，为每个文件建立固定 asset_id。
2. 链接由 [video-download](https://github.com/Backtthefuture/video-download) 下载；本地文件按 SHA256 核对；已在夸克的文件可凭完整 FID 直接选定。
3. 新媒体上传夸克后，通过完整搜索结果核对文件名、大小和上传时间，唯一匹配才继续。
4. 每个 FID 单独请求正文。原始回答、尝试记录和状态都持久保存；分析未完成时按记录时间恢复。
5. 检查正文是否过短、缺来源或只是摘要。合格后生成预整理稿；Agent 再用 patch 轻校对，保留原文。

完整的 Agent 操作约定见 [SKILL.md](SKILL.md)，批次、状态和恢复说明见 [quark-workflow.md](references/quark-workflow.md)。

## 安装

支持 Python 3.9+ 的 macOS。需要 Node.js、ffmpeg/ffprobe、配套 video-download Skill，以及**另行安装并授权的夸克网盘官方 Skill（quarkclouddrive）**。本仓库不包含夸克 CLI、账号凭据或媒体文件。请从你使用的 Agent/Skill 平台安装 quarkclouddrive，并按其 SKILL.md 完成安装和登录。

将本 Skill 装进全局 skills 目录。例如 Codex：

    VIDEO_TRANSCRIPT_TARGET="$HOME/.codex/skills/video-transcript" \
      bash <(curl -fsSL https://raw.githubusercontent.com/Backtthefuture/video-transcript/main/bootstrap.sh)

默认目标是 ~/.claude/skills/video-transcript。bootstrap 只同步本仓库文件并执行本地依赖检查；已有 .env、outputs 和个人词表会保留。若需要下载依赖，在安装目录运行：

    bash ~/.codex/skills/video-transcript/install.sh
    python3 ~/.codex/skills/video-transcript/scripts/transcript.py --doctor

install.sh 可安装 video-download、yt-dlp 和 Playwright/Chromium；需要预先准备 Python、Node.js、ffmpeg/ffprobe。夸克 Skill 仍需单独安装与授权。--doctor 只检查工具和依赖位置，**不代表云端授权或取稿链路成功**。

如果已有其他安装根，可分别设置 VT_PY、VIDEO_DOWNLOAD_HOME、QUARK_SKILL_HOME；运行时始终以当前 SKILL.md 所在目录为 VT_HOME。

## 使用与恢复

Agent 应将用户本轮原始请求写成临时 UTF-8 文件，并传入 --session-input-file。单条示例：

    python3 ~/.codex/skills/video-transcript/scripts/transcript.py \
      "/path/to/video.mp4" --session-input-file "/path/to/request.txt" --wait 1800

批次使用 --batch inputs.json。程序返回 batch_manifest 后，可以查看状态或续跑：

    python3 ~/.codex/skills/video-transcript/scripts/transcript.py \
      --resume "/path/to/manifest.json" --status
    python3 ~/.codex/skills/video-transcript/scripts/transcript.py \
      --resume "/path/to/manifest.json" \
      --session-input-file "/path/to/request.txt" --wait 1800

--wait 是本次进程的复查预算，不是云端预计完成时间。程序结束后不会自动唤醒。text_received 仅表示正文取回；response_needs_review、mapping_needs_review、upload_outcome_unknown 和 auth_required 都需要按 [恢复说明](references/quark-workflow.md) 处理。

## 隐私与旧版

所选媒体会上传至用户授权的夸克网盘；原始回答和状态保存在本机 outputs。程序不自动删除本地或云端文件。本仓库忽略 .env 和 outputs，不应把账号配置、Cookie、私人媒体或逐字稿提交到 GitHub。

此前的本地 FunASR 版说明保留在 [历史 README](docs/legacy-README.md)。旧脚本留在 Git 历史与仓库中供已有用户参考，不是当前 SKILL.md 的运行入口。
