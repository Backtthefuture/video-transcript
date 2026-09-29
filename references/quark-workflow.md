# 夸克云端运行与恢复

## 文件布局

`outputs/.quark-v1/items/<asset_id>/state.json` 是单条权威记录；每次更新用原子替换，任务锁防止同一文件被同时处理。
`attempts/<唯一编号>/response.jsonl` 与 raw.md 保留每次结果；预整理和润色稿不覆盖原始返回。
`outputs/.quark-v1/batches/<batch_id>/manifest.json` 保存批次成员与会话 ID；status.json 是快照，不是状态权威。
跨批次在同一个 output-dir 内复用条目；不同 output-dir 之间没有全局去重。URL 内容发生变化不会自动发现，须以新本地文件的哈希重新登记；不要把 --force 解释成重新下载。
输入本地同内容文件以 SHA256 去重，原路径另存 sources；文件名仅用于展示。远端自定义 content_hash 不当作本地 SHA256。

## 自行上传后的入口

先按夸克 Skill 的 search --stdout-only 或指定目录 browse --all，读取完整 Artifact。只选择用户指定范围的音视频，禁止根据前五条预览批量操作。
把确定的条目写为 JSON 数组：

```json
[
  {"input":"quark:<完整FID>","filename":"实际文件名.mp4","size":1234567,"duration":60,"parent_fid":"<指定目录FID>","title":"展示标题","source_url":"可选原始链接"}
]
```

filename、size 必须来自返回值；duration 缺失可省略，不能猜。运行时再次按官方 FID 尾串身份规则和元数据核对，调用使用最新完整 FID。长文件名搜索会按接口 50 字符限制取前缀，但最终仍比较完整名称。
同名不同 FID 保持独立；候选歧义时保留待确认，不取第一条。该入口只读已选定文件，不再上传。

## 参数

- --output-dir：持久产物根目录；跨会话恢复时沿用原目录。
- --quark-home / QUARK_SKILL_HOME：指定官方夸克 Skill；不需要复制 token。
- --download-home / VIDEO_DOWNLOAD_HOME：指定下载器。
- --session-input-file：本轮原始用户请求，用于官方 CLI 追踪；不要附加额外私密材料。
- --wait N：当前进程最长复查预算，默认 0 为单轮；每次长请求仍受单独超时约束。
- --resume manifest：恢复整个批次；--status 只读本地记录。
- --retry-errors：修复问题后明确重试。已发出的未知上传仍只对账，不盲目重传。
- --force / --no-cache：重新提问，不重传；可疑新回答不会覆盖已有可用原文。
- --reformat：不联网，从原文重新生成预整理，模型随后重新写 patch。
- --accept-response "检查说明"：仅单条任务、人工读过候选全文后，接纳 response_needs_review 的文本用于预整理；保留疑点且不升级为完整性已验证。
- --keep-video / --keep-audio / --no-daemon：旧参数兼容；媒体保留，无本地模型。
- --speakers / --host / --guest：不支持，明确报错；--no-save 不支持，防串稿必须保存状态。

## 失败处理

- 上传调用超时：已保存发起记录。恢复后只搜索并核对，匹配成功再读取。没有可确定候选时停下；不能换一个任务 ID 重新上传来绕过。
- 账号未授权：按照夸克 auth.md 登录；不读配置中的凭据值。登录后恢复同一批次并 --retry-errors。
- 分析未完成：不是固定等 24 小时，也不能把查询间隔当作转写耗时；记录最后未就绪和首次可读时间。
- 空答案、摘要、无来源或明显过短：保存候选，人工检查；必要时 --force 用同一 FID 重新提问，始终保留旧原文。
- 正文缺段：模型不能补全。报告 partial，保留原文；获得新证据或用户要求后再采取其他方案。
- 缺少远端哈希：仅报告已做元数据映射，不声称云端内容哈希比对。

本模块只通过官方 CLI 调用，不提供未公开的转写/字幕接口。官方环境检查可能更新其 CLI，失败时停止，不绕过。


## 调用方契约（2026-09-19）

stdout 输出完整 JSON，包含 schema_version、batch_manifest、items 和 counts；stderr 是进度，不作为路径权威。单条成功时保留顶层路径兼容字段。`--status` 与 input/--batch 联用可纯本地登记资产；不会运行夸克命令。适配器先登记再保存 job.json，随后仅用 --resume 恢复。

caller_client.run_job 每次执行一轮；wait/队列由调用方管理。成功缓存不再问答，人工待审不因路径存在而升级。复制媒体在使用前核对哈希，默认不清理。media-repairs 中字节不同的候选不会替换原资产。
