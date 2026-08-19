#!/usr/bin/env python3
"""把 FunASR 碎句整理成接近「整理优化版」的预整理稿,并生成 LLM 增量润色 brief。"""
from __future__ import annotations

import json
import re
from collections import Counter

STOPWORDS = set("""的 了 是 我 你 他 她 它 我们 你们 他们 咱们 这个 那个 一个 什么 怎么 就是 然后 但是 因为 所以 如果 而且 自己 现在 已经 非常 真的 觉得 认为 知道 时候 大家 一些 这种 那样 其实 还是 可以 没有 不是 应该 所有 以及 或者 等等 对于 关于 通过 从 到 在 有 和 与 就 都 也 很 太 更 最 不 没 吗 呢 吧 啊 呀 哦 嗯 那 这 让 把 被 给 向 为 之 其 说 讲 看 要 会 能 着 过 得 地 个 又 再 还 才 只 但 而 或 及 若""".split())

_WEAK_LEAD = ("我觉得", "我认为", "就是说", "就是说呢", "就是", "然后", "那么", "其实", "所以", "这个", "那个")
TOPIC_MARKER_RE = re.compile(
    r"第[一二三四五六七八九十百\d]+[个点]"
    r"|还有一个问题|另外一个|接下来|再说一遍|总结一下"
    r"|最后[一句话讲]?"
)
_QWORDS = ("什么", "怎么", "为什么", "哪里", "哪儿", "谁", "吗", "呢", "么", "咋", "如何", "是不是")
_CLAUSE_RE = re.compile(r"(?=(?:然后|但是|但|所以|因为|就是|如果|要是|而且|其实|不过|因此))")
SUSPECT_RE = re.compile(
    r"[A-Za-z]{3,}|"
    r"[\u4e00-\u9fff]{2,6}(?=泰|津|斯|兹|顿)|"
    r"[一-龥]{1,3}(?=玻璃|高金|飞泰)"
)


def fmt_mmss(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def split_sentences(text):
    parts = re.split(r"(?<=[。！？…!?])", text or "")
    return [p.strip() for p in parts if p.strip()]


def add_sentence_punct(text):
    t = (text or "").rstrip()
    if not t:
        return text
    if t[-1] in "。！？…，、；：,.!?;:":
        return t
    if any(w in t for w in _QWORDS):
        return t + "？"
    return t + "。"


def split_clauses(text):
    parts = [p for p in _CLAUSE_RE.split(text or "") if p]
    merged = []
    for part in parts:
        if merged and len(merged[-1]) < 12:
            merged[-1] += part
        else:
            merged.append(part)
    return [p for p in merged if p.strip()]


def extract_keywords(text, top_n=2):
    try:
        import jieba
    except ImportError:
        return []
    words = [
        w for w in jieba.cut(text or "")
        if len(w) >= 2 and w not in STOPWORDS and not w.isdigit()
    ]
    return [w for w, _ in Counter(words).most_common(top_n)]


def _clean_lead(s):
    for w in _WEAK_LEAD:
        if s.startswith(w) and len(s) > len(w) + 4:
            return s[len(w):]
    return s


def gen_section_title(text, keywords=None):
    sents = split_sentences(text)
    base = ""
    for s in sents:
        t = _clean_lead(s).strip(" ，。！？…:：")
        if len(t) >= 6:
            base = t
            break
    if not base:
        base = (sents[0][:14] if sents else "") or (text or "")[:14]
    base = base[:14]
    keywords = keywords or extract_keywords(text, 2)
    if keywords and len(base) < 8:
        kw = " · ".join(keywords[:2])
        return f"{base}｜{kw}"[:20] if base else kw
    return base or "段落"


def _is_topic_marker(tx, lookahead=20):
    return bool(TOPIC_MARKER_RE.search((tx or "")[:lookahead]))


def cluster_segments(segments, max_span=60):
    paras = []
    cur_start = cur_end = None
    cur_texts = []
    for s in segments or []:
        st = float(s.get("start", 0))
        en = float(s.get("end", 0))
        tx = (s.get("text") or "").strip()
        if not tx:
            continue
        if cur_start is None:
            cur_start, cur_end, cur_texts = st, en, [tx]
        elif _is_topic_marker(tx) and len(cur_texts) >= 2:
            paras.append((cur_start, cur_end, cur_texts))
            cur_start, cur_end, cur_texts = st, en, [tx]
        elif (st - cur_start) >= max_span and len(cur_texts) >= 2:
            paras.append((cur_start, cur_end, cur_texts))
            cur_start, cur_end, cur_texts = st, en, [tx]
        else:
            cur_end = en
            cur_texts.append(tx)
    if cur_start is not None:
        paras.append((cur_start, cur_end, cur_texts))
    return paras


def sentences_from_texts(texts):
    lines = []
    for tx in texts:
        sents = split_sentences(tx)
        if sents:
            lines.extend(sents)
        else:
            for clause in split_clauses(tx):
                lines.append(add_sentence_punct(clause))
    return lines


def merge_paragraphs(sentences, target_chars=90, max_sents=3):
    paras = []
    buf = []
    size = 0
    for sent in sentences:
        buf.append(sent)
        size += len(sent)
        if size >= target_chars or len(buf) >= max_sents:
            paras.append("".join(buf))
            buf, size = [], 0
    if buf:
        paras.append("".join(buf))
    return paras or [""]


def detect_suspects(text, limit=12):
    hits = []
    seen = set()
    for m in SUSPECT_RE.finditer(text or ""):
        token = m.group(0).strip()
        if len(token) < 2 or token in seen or token.lower() in STOPWORDS:
            continue
        if token.isascii() and token.isalpha() and token.islower() and len(token) < 5:
            continue
        seen.add(token)
        hits.append(token)
        if len(hits) >= limit:
            break
    return hits


def build_sections(segments):
    sections = []
    for start, end, texts in cluster_segments(segments):
        sentences = sentences_from_texts(texts)
        paras = merge_paragraphs(sentences)
        joined = "".join(paras)
        heading = gen_section_title(joined, extract_keywords(joined, 2))
        sections.append({
            "heading": heading,
            "start": fmt_mmss(start),
            "end": fmt_mmss(end),
            "start_sec": int(start),
            "end_sec": int(end),
            "paras": paras,
        })
    return sections


def render_preorganized_md(title, meta, sections):
    source = meta.get("source") or meta.get("platform_zh") or "视频"
    url = meta.get("url") or ""
    duration = meta.get("duration_label") or "?"
    transcribed_at = meta.get("transcribed_at") or ""
    lines = [
        f"# {title}\n",
        (
            f"> 来源: {source} | 链接: {url} | 时长 {duration} | "
            f"转录: FunASR(SenseVoice-Small) {transcribed_at} | 整理: 机器预整理"
        ),
        "> 说明: 机器已完成分段、合并碎句、候选标题；请只做专名纠错与标题润色，不要重写观点。文末对照表待补。\n",
        "## 目录\n",
    ]
    for i, sec in enumerate(sections, 1):
        lines.append(f"{i}. {sec['heading']} [{sec['start']}]")
    lines.append("")
    for i, sec in enumerate(sections, 1):
        lines.append(f"## {i}. {sec['heading']} [{sec['start']} - {sec['end']}]\n")
        for para in sec["paras"]:
            lines.append(para + "\n")
    lines.extend([
        "---\n",
        "## 附：识别修正对照表（整理时改动）\n",
        "**已修正（确信度高）**：\n- （待 LLM 增量补全）\n",
        "**存疑（〔?〕标注，建议对照原视频核对）**：\n- （待 LLM 增量补全）\n",
    ])
    return "\n".join(lines).rstrip() + "\n"


def build_polish_brief(title, sections, suspects=None):
    suspects = suspects or detect_suspects("".join(p for s in sections for p in s.get("paras") or []))
    return {
        "mode": "patch_only",
        "instruction": (
            "不要重写全文，不要输出主持稿正文。"
            "只根据预整理稿做增量润色，输出一个 JSON patch。"
            "保留原话原意，不总结、不增删观点。"
        ),
        "title_current": title,
        "sections": [
            {
                "index": i,
                "heading": sec["heading"],
                "start": sec["start"],
                "end": sec["end"],
                "excerpt": (sec["paras"][0] if sec.get("paras") else "")[:80],
            }
            for i, sec in enumerate(sections, 1)
        ],
        "suspects": suspects,
        "patch_schema": {
            "title": "可选,润色后的大标题",
            "headings": ["与章节顺序对齐的语义化小标题,可只改需要改的"],
            "fixes": [{"from": "原词", "to": "修正词", "confidence": "high|low"}],
            "paragraph_edits": [
                {"section": 1, "para": 0, "replace": "仅当该段有识别错误时才提供整段替换"}
            ],
        },
    }


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path
