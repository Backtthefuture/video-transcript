#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「整理优化版」md + html

用法:
  python make_optimized.py --from-md 预整理.md [--patch patch.json] [--output-dir DIR]
  python make_optimized.py --content content.json [--output-dir DIR]

优先 --from-md: agent 只写一次 markdown(或只出小 patch),脚本渲染 html,不再让 LLM 把全文抄进 content.json。
"""
import argparse
import datetime
import html
import json
import os
import re
import sys

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs")

SEC_HEADER_RE = re.compile(
    r"^##\s*(\d+)[\.、\)\s]\s*(.*?)\s*\[\s*(\d{1,2}:\d{2})\s*[-–~]\s*(\d{1,2}:\d{2})\s*\]\s*$"
)


def esc(s):
    return html.escape(s or "", quote=True)


def build_md(c):
    lines = []
    lines.append(f"# {c['title']}\n")
    lines.append(
        f"> 来源: {c.get('source','视频')} | 链接: {c.get('url','')} | 时长 {c.get('duration','?')} | "
        f"转录: FunASR(SenseVoice-Small) {c.get('transcribed_at','?')} | "
        f"整理: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    lines.append("> 说明: 在逐字稿基础上补标点、合并碎句、修正识别错误，保留原话原意；个别存疑处标〔?〕，详见文末对照表\n")
    lines.append("## 目录\n")
    for i, s in enumerate(c["sections"], 1):
        lines.append(f"{i}. {s['heading']} [{s['start']}]")
    lines.append("")
    for i, s in enumerate(c["sections"], 1):
        lines.append(f"## {i}. {s['heading']} [{s['start']} - {s['end']}]\n")
        for p in s.get("paras") or []:
            lines.append(p + "\n")
    lines.append("---\n")
    lines.append("## 附：识别修正对照表（整理时改动）\n")
    lines.append((c.get("fixes") or "") + "\n")
    return "\n".join(lines).rstrip() + "\n"


def build_html(c, md_text, fn_md):
    art = []
    art.append(f"<h1>{esc(c['title'])}</h1>")
    art.append("<blockquote>")
    art.append(
        f"<p>来源: {esc(c.get('source','视频'))} | 链接: {esc(c.get('url',''))} | 时长 {c.get('duration','?')} | "
        f"转录: FunASR(SenseVoice-Small) {c.get('transcribed_at','?')} | "
        f"整理: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    art.append("说明: 在逐字稿基础上补标点、合并碎句、修正识别错误，保留原话原意；个别存疑处标〔?〕，详见文末对照表</p>")
    art.append("</blockquote>")
    art.append("<h2>目录</h2><ol>")
    for i, s in enumerate(c["sections"], 1):
        art.append(f"<li>{esc(s['heading'])} [{s['start']}]</li>")
    art.append("</ol>")
    for i, s in enumerate(c["sections"], 1):
        art.append(f"<h2>{i}. {esc(s['heading'])} [{s['start']} - {s['end']}]</h2>")
        for p in s.get("paras") or []:
            art.append(f"<p>{esc(p)}</p>")
    art.append("<hr>")
    art.append("<h2>附：识别修正对照表（整理时改动）</h2>")
    for para in (c.get("fixes") or "").split("\n\n"):
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


def _parse_meta_line(line):
    meta = {}
    text = line.lstrip("> ").strip()
    for part in text.split("|"):
        part = part.strip()
        if part.startswith("来源:"):
            meta["source"] = part.split(":", 1)[1].strip()
        elif part.startswith("链接:"):
            meta["url"] = part.split(":", 1)[1].strip()
        elif part.startswith("时长"):
            meta["duration"] = part.replace("时长", "", 1).strip()
        elif "转录:" in part:
            rest = part.split("转录:", 1)[1].strip()
            meta["transcribed_at"] = rest.replace("FunASR(SenseVoice-Small)", "").strip()
    return meta


def parse_optimized_md(md_text):
    lines = (md_text or "").splitlines()
    title = "整理优化版"
    meta = {}
    sections = []
    fixes_lines = []
    in_fixes = False
    current = None
    para_buf = []

    def flush_para():
        nonlocal para_buf, current
        if current is not None and para_buf:
            text = "\n".join(para_buf).strip()
            if text:
                current["paras"].append(text)
            para_buf = []

    def flush_section():
        nonlocal current
        flush_para()
        if current is not None:
            sections.append(current)
            current = None

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            continue
        if line.startswith(">") and not current and not in_fixes:
            parsed = _parse_meta_line(line)
            meta.update(parsed)
            continue
        if line.startswith("## 目录") or line.startswith("##目录"):
            continue
        if line.startswith("## 附") or "识别修正对照表" in line:
            flush_section()
            in_fixes = True
            continue
        if in_fixes:
            if line.strip() == "---":
                continue
            fixes_lines.append(line)
            continue
        m = SEC_HEADER_RE.match(line)
        if m:
            flush_section()
            current = {
                "heading": m.group(2).strip(),
                "start": m.group(3),
                "end": m.group(4),
                "paras": [],
            }
            continue
        if line.startswith("## "):
            continue
        if current is None:
            continue
        if line.strip() == "---":
            flush_section()
            continue
        if not line.strip():
            flush_para()
        else:
            para_buf.append(line)

    flush_section()
    fixes = "\n".join(fixes_lines).strip()
    return {
        "title": title,
        "source": meta.get("source", "视频"),
        "url": meta.get("url", ""),
        "duration": meta.get("duration", "?"),
        "transcribed_at": meta.get("transcribed_at", ""),
        "sections": sections,
        "fixes": fixes,
    }


def _fixes_from_patch(patch, existing=""):
    items = patch.get("fixes")
    if not items:
        return existing
    if isinstance(items, str):
        return items
    high, low = [], []
    for item in items:
        if isinstance(item, str):
            high.append(f"- {item}")
            continue
        src = item.get("from") or item.get("original") or ""
        dst = item.get("to") or item.get("fixed") or ""
        line = f"- {src} → {dst}" if src and dst else f"- {src or dst}"
        if (item.get("confidence") or "high") == "low":
            low.append(line)
        else:
            high.append(line)
    parts = []
    parts.append("**已修正（确信度高）**：")
    parts.extend(high or ["- （无）"])
    parts.append("")
    parts.append("**存疑（〔?〕标注，建议对照原视频核对）**：")
    parts.extend(low or ["- （无）"])
    return "\n".join(parts)


def _apply_term_fixes(content, patch):
    items = patch.get("fixes") or []
    if not isinstance(items, list):
        return
    replacements = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if (item.get("confidence") or "high") == "low":
            continue
        src = item.get("from") or item.get("original") or ""
        dst = item.get("to") or item.get("fixed") or ""
        if src and dst and src != dst:
            replacements.append((src, dst))
    if not replacements:
        return
    for sec in content.get("sections") or []:
        paras = sec.get("paras") or []
        sec["paras"] = [ _replace_all(p, replacements) for p in paras ]
        if sec.get("heading"):
            sec["heading"] = _replace_all(sec["heading"], replacements)
    if content.get("title"):
        content["title"] = _replace_all(content["title"], replacements)


def _replace_all(text, replacements):
    out = text or ""
    for src, dst in replacements:
        out = out.replace(src, dst)
    return out


def apply_patch(content, patch):
    if not patch:
        return content
    if patch.get("title"):
        content["title"] = patch["title"]
    headings = patch.get("headings") or []
    for i, heading in enumerate(headings):
        if heading and i < len(content["sections"]):
            content["sections"][i]["heading"] = heading
    _apply_term_fixes(content, patch)
    for edit in patch.get("paragraph_edits") or []:
        try:
            si = int(edit.get("section") or 1) - 1
            pi = int(edit.get("para") or 0)
            repl = edit.get("replace")
        except (TypeError, ValueError):
            continue
        if repl is None:
            continue
        if 0 <= si < len(content["sections"]):
            paras = content["sections"][si].setdefault("paras", [])
            while len(paras) <= pi:
                paras.append("")
            paras[pi] = repl
    content["fixes"] = _fixes_from_patch(patch, content.get("fixes") or "")
    return content


def default_filename(title):
    safe = re.sub(r'[\\/:*?"<>|]', "_", title or "整理优化版").strip()
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    return f"{date}_{safe[:30]}_整理优化版"


def write_outputs(content, out_dir, filename=None):
    os.makedirs(out_dir, exist_ok=True)
    filename = filename or content.get("filename") or default_filename(content.get("title"))
    if filename.endswith(".md"):
        filename = filename[:-3]
    fn_md = filename + ".md"
    fn_html = filename + ".html"
    md_text = build_md(content)
    page = build_html(content, md_text, fn_md)
    md_path = os.path.join(out_dir, fn_md)
    html_path = os.path.join(out_dir, fn_html)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)
    return md_path, html_path, md_text


def main():
    ap = argparse.ArgumentParser(description="生成整理优化版 md+html")
    ap.add_argument("--content", help="content.json 路径(旧流程,不推荐)")
    ap.add_argument("--from-md", dest="from_md", help="预整理/整理优化版 markdown")
    ap.add_argument("--patch", help="LLM 增量 patch.json,配合 --from-md")
    ap.add_argument("--filename", default=None, help="输出文件名(不含扩展名)")
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

    if args.from_md:
        with open(args.from_md, encoding="utf-8") as f:
            content = parse_optimized_md(f.read())
        if args.patch:
            with open(args.patch, encoding="utf-8") as f:
                patch = json.load(f)
            content = apply_patch(content, patch)
        if args.filename:
            content["filename"] = args.filename
        md_path, html_path, md_text = write_outputs(content, os.path.abspath(args.output_dir), args.filename)
        print("OK ->", md_path)
        print("OK ->", html_path)
        print("MD chars:", len(md_text))
        return

    if not args.content:
        ap.error("需要 --from-md 预整理.md(推荐) 或 --content content.json")

    with open(args.content, encoding="utf-8") as f:
        c = json.load(f)

    md_path, html_path, md_text = write_outputs(c, os.path.abspath(args.output_dir), c.get("filename"))
    print("OK ->", md_path)
    print("OK ->", html_path)
    print("MD chars:", len(md_text))


if __name__ == "__main__":
    main()
