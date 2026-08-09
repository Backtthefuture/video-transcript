#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「整理优化版」md + html（通用渲染器，video-transcript skill 配套模板）

用法:
  python make_optimized.py --content content.json [--output-dir DIR]

content.json 结构:
{
  "title": "高敏感+低能量才是内容创作的圣体（整理优化版）",
  "source": "微信视频号",
  "url": "https://weixin.qq.com/sph/xxx",
  "duration": "21:32",
  "transcribed_at": "2026-08-07 16:38",
  "filename": "2026-08-07_妙高山下的老明-高敏感+低能量才是内容创作的圣体_整理优化版",
  "sections": [
    {"heading": "开篇：低能量+高敏感=创作圣体", "start": "00:00", "end": "01:00",
     "paras": ["段落1", "段落2"]}
  ],
  "fixes": "已修正(确信度高)：\n- ...\n\n存疑(〔?〕标注)：\n- ..."
}

工作流（agent 必须遵守）:
1. 读取落盘 *_transcript.md 全文（stdout 可能截断，以文件为准）
2. 逐段：补标点断句 + 合并碎句 + 修正识别错误（不确定标〔?〕）+ 语义化小标题
3. 整理为 content.json（可让脚本 --dump-template 先出骨架）
4. 跑本脚本 → 生成 .md + .html（工具栏复制/下载 + 目录 + 对照表，样式与旧版一致）
5. present_files 呈现时 .html 放第一位
"""
import argparse, json, html, os, sys, datetime

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")

def esc(s):
    return html.escape(s, quote=True)

def build_md(c):
    lines = []
    lines.append(f"# {c['title']}\n")
    lines.append(f"> 来源: {c.get('source','视频')} | 链接: {c['url']} | 时长 {c.get('duration','?')} | 转录: FunASR(SenseVoice-Small) {c.get('transcribed_at','?')} | 整理: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("> 说明: 在逐字稿基础上补标点、合并碎句、修正识别错误，保留原话原意；个别存疑处标〔?〕，详见文末对照表\n")
    lines.append("## 目录\n")
    for i, s in enumerate(c["sections"], 1):
        lines.append(f"{i}. {s['heading']} [{s['start']}]")
    lines.append("")
    for i, s in enumerate(c["sections"], 1):
        lines.append(f"## {i}. {s['heading']} [{s['start']} - {s['end']}]\n")
        for p in s["paras"]:
            lines.append(p + "\n")
    lines.append("---\n")
    lines.append("## 附：识别修正对照表（整理时改动）\n")
    lines.append(c.get("fixes", "") + "\n")
    return "\n".join(lines).rstrip() + "\n"

def build_html(c, md_text, fn_md):
    art = []
    art.append(f"<h1>{esc(c['title'])}</h1>")
    art.append("<blockquote>")
    art.append(f"<p>来源: {esc(c.get('source','视频'))} | 链接: {esc(c['url'])} | 时长 {c.get('duration','?')} | 转录: FunASR(SenseVoice-Small) {c.get('transcribed_at','?')} | 整理: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    art.append("说明: 在逐字稿基础上补标点、合并碎句、修正识别错误，保留原话原意；个别存疑处标〔?〕，详见文末对照表</p>")
    art.append("</blockquote>")
    art.append("<h2>目录</h2><ol>")
    for i, s in enumerate(c["sections"], 1):
        art.append(f"<li>{esc(s['heading'])} [{s['start']}]</li>")
    art.append("</ol>")
    for i, s in enumerate(c["sections"], 1):
        art.append(f"<h2>{i}. {esc(s['heading'])} [{s['start']} - {s['end']}]</h2>")
        for p in s["paras"]:
            art.append(f"<p>{esc(p)}</p>")
    art.append("<hr>")
    art.append("<h2>附：识别修正对照表（整理时改动）</h2>")
    for para in c.get("fixes", "").split("\n\n"):
        if para.strip():
            art.append(f"<p>{esc(para).replace(chr(10), '<br>')}</p>")

    article_html = "\n".join(art)
    md_json = json.dumps(md_text, ensure_ascii=True)
    fn_json = json.dumps(fn_md, ensure_ascii=True)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(c['title'])}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; background: #f7f7f5; color: #1f2328; line-height: 1.75; }}
  .toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 10px; justify-content: center; padding: 14px; background: rgba(247,247,245,.96); backdrop-filter: blur(6px); border-bottom: 1px solid #e6e6e3; }}
  .toolbar button {{ border: 1px solid #d0d0cc; background: #fff; color: #1f2328; border-radius: 8px; padding: 8px 18px; font-size: 14px; cursor: pointer; transition: all .15s; }}
  .toolbar button:hover {{ background: #f0f0ee; border-color: #b8b8b3; }}
  .toolbar button:active {{ transform: translateY(1px); }}
  .toolbar button.primary {{ background: #1f2328; color: #fff; border-color: #1f2328; }}
  .toolbar button.primary:hover {{ background: #33383f; }}
  article {{ max-width: 760px; margin: 0 auto; padding: 36px 28px 80px; background: #fff; min-height: 100vh; }}
  article h1 {{ font-size: 26px; font-weight: 600; line-height: 1.4; margin: 0 0 12px; }}
  article h2 {{ font-size: 19px; font-weight: 600; margin: 36px 0 10px; padding-top: 24px; border-top: 1px solid #eee; }}
  article h2:first-of-type {{ border-top: none; padding-top: 0; }}
  article p {{ margin: 12px 0; font-size: 16px; }}
  article blockquote {{ margin: 14px 0; padding: 10px 16px; border-left: 3px solid #d0d0cc; background: #fafaf8; color: #57606a; font-size: 14px; border-radius: 0 8px 8px 0; }}
  article blockquote p {{ margin: 4px 0; font-size: 14px; }}
  article ol {{ margin: 12px 0 12px 26px; }}
  article li {{ margin: 4px 0; font-size: 15px; }}
  article hr {{ border: none; border-top: 1px solid #eee; margin: 28px 0; }}
  @media (max-width: 640px) {{ article {{ padding: 24px 18px 60px; }} article h1 {{ font-size: 22px; }} article h2 {{ font-size: 17px; }} article p {{ font-size: 15px; }} }}
</style>
</head>
<body>
<div class="toolbar">
  <button class="primary" onclick="copyMd()">复制全文</button>
  <button onclick="downloadMd()">下载 .md</button>
</div>
<article>
{article_html}
</article>
<script>
const MD = {md_json};
const FN = {fn_json};
async function copyMd(){{
  try {{
    await navigator.clipboard.writeText(MD);
    flash("已复制全文");
  }} catch (e) {{
    const ta = document.createElement("textarea");
    ta.value = MD; document.body.appendChild(ta); ta.select();
    try {{ document.execCommand("copy"); flash("已复制全文"); }}
    catch (e2) {{ flash("复制失败,请手动选择复制"); }}
    document.body.removeChild(ta);
  }}
}}
function downloadMd(){{
  const blob = new Blob([MD], {{ type: "text/markdown" }});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = FN; a.click();
  URL.revokeObjectURL(a.href);
}}
function flash(msg){{
  const d = document.createElement("div");
  d.textContent = msg;
  d.style.cssText = "position:fixed;left:50%;top:64px;transform:translateX(-50%);background:#1f2328;color:#fff;padding:8px 18px;border-radius:8px;font-size:13px;z-index:99;";
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 1600);
}}
</script>
</body>
</html>
"""

def main():
    ap = argparse.ArgumentParser(description="生成整理优化版 md+html")
    ap.add_argument("--content", help="content.json 路径")
    ap.add_argument("--output-dir", default=DEFAULT_OUT)
    ap.add_argument("--dump-template", action="store_true", help="输出 content.json 骨架模板")
    args = ap.parse_args()

    if args.dump_template:
        tpl = {
            "title": "标题（整理优化版）",
            "source": "微信视频号",
            "url": "https://weixin.qq.com/sph/xxx",
            "duration": "12:06",
            "transcribed_at": "2026-08-07 15:58",
            "filename": "2026-08-07_标题30字内_整理优化版",
            "sections": [
                {"heading": "语义化小标题", "start": "00:00", "end": "01:00", "paras": ["补标点后的完整段落", "第二段"]}
            ],
            "fixes": "**已修正（确信度高）**：\n- 原词 → 修正词 ｜ ...\n\n**存疑（〔?〕标注，建议对照原视频核对）**：\n- ...",
        }
        print(json.dumps(tpl, ensure_ascii=False, indent=2))
        return

    if not args.content:
        ap.error("需要 --content content.json（或 --dump-template 看骨架）")

    with open(args.content, encoding="utf-8") as f:
        c = json.load(f)

    out_dir = os.path.abspath(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)
    fn_md = c["filename"] + ".md"
    fn_html = c["filename"] + ".html"

    md_text = build_md(c)
    page = build_html(c, md_text, fn_md)

    with open(os.path.join(out_dir, fn_md), "w", encoding="utf-8") as f:
        f.write(md_text)
    with open(os.path.join(out_dir, fn_html), "w", encoding="utf-8") as f:
        f.write(page)

    print("OK ->", os.path.join(out_dir, fn_md))
    print("OK ->", os.path.join(out_dir, fn_html))
    print("MD chars:", len(md_text))

if __name__ == "__main__":
    main()
