#!/usr/bin/env python3
"""播客说话人后处理：缝合半截词 → 说话人映射 → 补标点 → 分段 → Markdown。

输入 diarize_asr 产出的 segments(带 Speaker N 标签)，输出说话人区块版逐字稿:

    ### 00:36 – 01:13　主持人 · 曲凯

    正文按语义分段……

说话人姓名从 Shownotes 正则提取(主持人/嘉宾/主播等模式)，
提取不到时回退「说话人 A / 说话人 B」。

CLI: python3 speaker_postprocess.py --json <transcription.json> [--title ...] [--shownotes-file ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from podcast_glossary import (  # noqa: E402
    apply_corrections,
    is_garbage_segment,
    is_intro_segment,
    is_production_segment,
)

CJK = r"[\u4e00-\u9fff]"
PUNCT_RE = re.compile(r"[，,。！？?、；;…\.]+")

QUESTION_MARKERS = re.compile(
    r"(你觉得|你认为|能不能|可不可以|是不是|为什么|怎么|如何|对吗|对吧|可否|能否|么\?|吗\?|呢\?|？)"
)

# 跨说话人只缝合连接词(避免把正常换人误缝)
CROSS_SPEAKER_WORDS = {
    "因为", "所以", "可以", "非常", "然后", "同时", "可能", "其实", "但是",
    "而且", "以及", "不过", "那么", "当然", "如果", "虽然", "还有", "或者",
    "只是", "另外", "就是", "之前", "我是", "对吧",
}

# 同说话人碎句切口：末字+下句首字构成的常见双字词
SAME_SPEAKER_WORDS = CROSS_SPEAKER_WORDS | {
    "我们", "他们", "不是", "没有", "已经", "现在", "自己", "这些", "那些",
    "这个", "那个", "什么", "怎么", "还是", "对于", "关于", "通过", "作为",
    "进行", "开始", "觉得", "认为", "知道", "应该", "需要", "能够", "一样",
    "一种", "一些", "一个", "一下", "特别", "比较", "主要", "包括", "当时",
    "后来", "之后", "目前", "今天", "这边", "公司", "模型", "训练", "数据",
    "创业", "时候", "事情", "方面", "问题", "工作", "方向", "经历", "发展",
    "感谢", "介绍", "简单", "开心", "最近", "相关", "博士", "实习", "重要",
    "朋友", "背景", "典型", "喜欢", "年轻", "毕业", "甚至", "出来", "半年",
    "左右", "实际", "感受", "大厂", "内部", "工程", "流程", "稳定", "以后",
    "肯定", "找到", "平台", "方便", "使用", "不同", "应用", "东西", "发现",
    "每天", "本身", "饱和", "架构", "优化", "资源", "初创", "成本", "一切",
    "基础", "这样", "擅长", "适合", "整个", "合成", "相比", "希望", "精细",
    "环境", "探索", "完成", "任务", "变成", "这种", "落地", "优秀", "实现",
    "相对", "而言", "商业", "模式", "简单", "很多", "回答", "提供", "时候",
}

CLAUSE_MARKERS = (
    "然后", "但是", "所以", "因为", "其实", "而且", "不过", "那么", "另外",
    "只是", "当然", "比如说", "就是说", "包括", "以及", "我觉得", "我认为", "对吧",
)

# 说话人切口把下一句开头粘在上一句末尾的常见前缀
NEXT_TURN_PREFIXES = (
    "我觉得你", "我认为你", "我会觉得其实", "我会觉得", "我觉得其实", "我觉得", "我认为",
)

# Shownotes 中主持人/嘉宾的常见标注模式(含 42章经风格的「导游/N号珍藏」)
HOST_PATTERNS = [
    re.compile(r"(?:主持人|主播|导游)[：:]\s*([^\s，,；;、|【]{2,12})"),
]
GUEST_PATTERNS = [
    re.compile(r"(?:嘉宾|对谈嘉宾|\d+\s*号珍藏)[：:]\s*([^\s，,；;、|【]{2,12})"),
]


# ── 基础文本工具 ─────────────────────────────────────────


def strip_punct(text: str) -> str:
    t = PUNCT_RE.sub("", text or "")
    return re.sub(r"\s+", " ", t).strip()


def space_mixed(text: str) -> str:
    t = re.sub(rf"({CJK})([A-Za-z])", r"\1 \2", text)
    t = re.sub(rf"([A-Za-z0-9])({CJK})", r"\1 \2", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([，。！？、；：])", r"\1", t)
    return t.strip()


def join_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if re.search(r"[A-Za-z0-9]$", left) and re.search(r"^[A-Za-z0-9]", right):
        return f"{left} {right}"
    return left + right


def format_clock(ms: int) -> str:
    s, _ = divmod(ms, 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ── 半截词缝合 ───────────────────────────────────────────


def _last_cjk(text: str) -> str:
    m = re.search(rf"({CJK})$", text)
    return m.group(1) if m else ""


def _first_cjk(text: str) -> str:
    m = re.search(rf"^({CJK})", text)
    return m.group(1) if m else ""


def stitch_pair(prev: str, nxt: str, *, same_speaker: bool) -> tuple[str, str] | None:
    if not prev or not nxt:
        return None
    if not same_speaker:
        for tail in NEXT_TURN_PREFIXES:
            if prev.endswith(tail):
                return prev[: -len(tail)], tail + nxt
        # 语气词被切到下一说话人开头(「三个方面 / 吧人的…」)
        m = re.match(rf"^([吧呢啊嘛])({CJK}.+)", nxt)
        if m and not prev.endswith(m.group(1)):
            return prev + m.group(1), m.group(2)
    a, b = _last_cjk(prev), _first_cjk(nxt)
    if not a or not b:
        return None
    word = a + b
    allowed = SAME_SPEAKER_WORDS if same_speaker else CROSS_SPEAKER_WORDS
    if word not in allowed:
        return None
    return prev[: -len(a)], word + nxt[len(b):]


# ── 标点恢复 ─────────────────────────────────────────────


def _chunk_at_boundary(text: str, size: int = 200) -> list[str]:
    t = text.strip()
    if len(t) <= size:
        return [t] if t else []
    chunks: list[str] = []
    start = 0
    while start < len(t):
        end = min(start + size, len(t))
        if end < len(t):
            window = t[start:end]
            cut = max(window.rfind(" "), window.rfind("的"), window.rfind("了"), window.rfind("是"))
            if cut >= size // 2:
                end = start + cut + 1
        chunks.append(t[start:end])
        start = end
    return chunks


def ct_punc_batch(texts: list[str]) -> list[str] | None:
    """按块送 ct-punc 补标点。模型由 diarize_asr 统一持有,转录后调用不再重复加载。"""
    from diarize_asr import punctuate

    # 摊平成块后一次性送模型，块与原文的对应关系用 owner 记录
    chunks: list[str] = []
    owner: list[int] = []
    for i, text in enumerate(texts):
        raw = (text or "").strip()
        if len(raw) < 4:
            continue
        for chunk in _chunk_at_boundary(raw):
            chunks.append(chunk)
            owner.append(i)

    punced = punctuate(chunks) if chunks else []
    if punced is None:
        print("[WARN] ct-punc 不可用,回退规则标点", file=sys.stderr)
        return None

    joined: list[list[str]] = [[] for _ in texts]
    for idx, chunk_out in zip(owner, punced):
        joined[idx].append(chunk_out)
    return [
        "".join(joined[i]) if joined[i] else (texts[i] or "").strip()
        for i in range(len(texts))
    ]


def rule_punctuate(text: str) -> str:
    t = strip_punct(text)
    if not t:
        return ""
    for m in sorted(CLAUSE_MARKERS, key=len, reverse=True):
        t = re.sub(rf"(?<![，。！？\s])({re.escape(m)})", r"，\1", t)
    t = t.replace("，对吧", "对吧。")
    t = re.sub(r"对吧(?![。？])", "对吧。", t)
    t = re.sub(r"是吧(?![。？])", "是吧。", t)
    t = re.sub(r"(吗)(?=[我你那但所其这])", r"吗？", t)
    t = re.sub(r"(呢)(?=[我你那但所其这])", r"呢？", t)
    t = re.sub(r"，{2,}", "，", t)
    t = re.sub(r"^，+", "", t)
    if "。" not in t and len(t) > 60:
        parts = t.split("，")
        rebuilt: list[str] = []
        buf = ""
        for part in parts:
            if not part:
                continue
            buf = buf + "，" + part if buf else part
            if len(buf) >= 46:
                rebuilt.append(buf.rstrip("，") + "。")
                buf = ""
        if buf:
            rebuilt.append(buf)
        t = "".join(rebuilt)
    if t and t[-1] not in "。！？":
        t += "。"
    t = re.sub(r"。，", "。", t)
    t = re.sub(r"([。！？]){2,}", r"\1", t)
    return space_mixed(t)


def _restore_english_tokens(original: str, punced: str) -> str:
    """ct-punc 会把英文词按子词拆散(Post-training → P ost - training)，
    用原文里的英文 token 逐个还原。"""
    tokens = sorted(
        set(re.findall(r"[A-Za-z0-9][A-Za-z0-9.+\-]*[A-Za-z0-9]", original)),
        key=len,
        reverse=True,
    )
    for tok in tokens:
        if len(tok) < 3 or tok in punced:
            continue
        pattern = r"[\s，、]*".join(re.escape(c) for c in tok)
        punced = re.sub(pattern, tok, punced)
    return punced


def restore_punctuation(texts: list[str], use_model: bool = True) -> list[str]:
    cleaned = [strip_punct(t) for t in texts]
    modeled = ct_punc_batch(cleaned) if use_model else None
    out: list[str] = []
    for i, raw in enumerate(cleaned):
        if modeled and modeled[i].strip():
            t = _restore_english_tokens(raw, modeled[i])
            t = space_mixed(t)
            t = re.sub(r"(?<![，。！？])对吧(?![。？])", "对吧。", t)
            t = re.sub(r"。，", "。", t)
            if t and t[-1] not in "。！？":
                t += "。"
            t = re.sub(r"\.。", "。", t)
            t = re.sub(rf",\s*(?={CJK})", "，", t)
            out.append(t)
        else:
            out.append(rule_punctuate(raw))
    return out


# ── 分段 ─────────────────────────────────────────────────


def _break_long_sentence(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    parts = sentence.split("，")
    if len(parts) == 1:
        return [sentence]
    out: list[str] = []
    buf = ""
    for i, part in enumerate(parts):
        piece = part if i == 0 else "，" + part
        if buf and len(buf) + len(piece) > max_chars:
            if not buf.endswith(("。", "！", "？")):
                buf = buf.rstrip("，") + "。"
            out.append(buf)
            buf = part
        else:
            buf += piece
    if buf:
        out.append(buf)
    return out


def split_readable_paragraphs(text: str, max_chars: int = 140) -> list[str]:
    t = space_mixed(text)
    t = re.sub(r"就。([包是从和而])", r"就\1", t)
    t = t.replace("。包括", "，包括")
    if not t:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])", t) if s.strip()]
    if not sentences:
        return [t]
    exploded: list[str] = []
    for s in sentences:
        exploded.extend(_break_long_sentence(s, max_chars))
    paras: list[str] = []
    buf = ""
    for s in exploded:
        if not buf:
            buf = s
        elif len(buf) + len(s) <= max_chars:
            buf += s
        else:
            paras.append(buf)
            buf = s
    if buf:
        paras.append(buf)
    return paras


# ── 说话人映射 ───────────────────────────────────────────


def parse_speaker_names(shownotes: str) -> tuple[str, str]:
    """从 Shownotes 提取 (主持人名, 嘉宾名)，取不到返回空串。"""
    host = guest = ""
    for pat in HOST_PATTERNS:
        m = pat.search(shownotes or "")
        if m:
            host = m.group(1).strip()
            break
    for pat in GUEST_PATTERNS:
        m = pat.search(shownotes or "")
        if m:
            guest = m.group(1).strip()
            break
    return host, guest


def _talk_time(segments: list[dict]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for seg in segments:
        spk = seg.get("speaker")
        if spk:
            stats[spk] = stats.get(spk, 0) + seg["end_ms"] - seg["start_ms"]
    return stats


def _host_score(text: str, avg_len: float) -> float:
    score = 0.0
    if QUESTION_MARKERS.search(text):
        score += 2.5
    if len(text) < 120:
        score += 1.0
    if avg_len > 0 and len(text) < avg_len * 0.45:
        score += 0.8
    if text.strip().endswith("？"):
        score += 1.0
    return score


def _appearance_order(segments: list[dict], labels: list[str]) -> list[str]:
    seen: list[str] = []
    for seg in segments:
        spk = seg.get("speaker")
        if spk in labels and spk not in seen:
            seen.append(spk)
    return seen + [spk for spk in labels if spk not in seen]


def map_speakers(
    segments: list[dict], host_name: str = "", guest_name: str = ""
) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (speaker_id → 显示名, 显示名 → 角色标签)。"""
    labels = sorted({s.get("speaker") for s in segments if s.get("speaker")})
    if not labels:
        name = host_name or "说话人 A"
        return {}, {name: "主持人" if host_name else ""}

    stats = _talk_time(segments)
    total = sum(stats.values()) or 1
    ordered = sorted(labels, key=lambda x: stats.get(x, 0), reverse=True)

    # 单口:第一说话人 ≥ 90%
    if len(ordered) == 1 or stats.get(ordered[0], 0) / total >= 0.90:
        name = host_name or "说话人 A"
        mapping = {spk: name for spk in labels}
        return mapping, {name: "主持人" if host_name else ""}

    major = [spk for spk in ordered if stats.get(spk, 0) / total >= 0.02][:2]
    if len(major) < 2:
        major = ordered[:2]

    # 没有任何姓名信息:按出场顺序回退「说话人 A/B/...」,不猜角色
    if not host_name and not guest_name:
        mapping: dict[str, str] = {}
        letters = "ABCDEFGH"
        for i, spk in enumerate(_appearance_order(segments, ordered)):
            mapping[spk] = f"说话人 {letters[min(i, len(letters) - 1)]}"
        return mapping, {name: "" for name in mapping.values()}

    # 对谈:默认嘉宾话多、主持人话少;用「段均问句密度」校正
    # (用平均分而非累计分,避免话多的一方靠段数堆高分数)
    guest_spk, host_spk = major[0], major[1]
    score_sum = {spk: 0.0 for spk in major}
    seg_count = {spk: 0 for spk in major}
    avg_len = sum(len(s.get("text", "")) for s in segments) / max(len(segments), 1)
    for seg in segments:
        spk = seg.get("speaker")
        if spk in score_sum:
            score_sum[spk] += _host_score(seg.get("text", ""), avg_len)
            seg_count[spk] += 1
    avg_score = {
        spk: score_sum[spk] / max(seg_count[spk], 1) for spk in major
    }
    if avg_score[guest_spk] > avg_score[host_spk] + 0.5:
        guest_spk, host_spk = host_spk, guest_spk

    host_display = host_name or "说话人 A"
    guest_display = guest_name or "说话人 B"
    mapping = {host_spk: host_display, guest_spk: guest_display}
    roles = {
        host_display: "主持人" if host_name else "",
        guest_display: "嘉宾" if guest_name else "",
    }
    # 三人以上：其余说话人各自保留独立编号，不要合并成一个「其他」
    letters = "CDEFGH"
    extra = 0
    for spk in _appearance_order(segments, ordered):
        if spk in mapping:
            continue
        name = f"说话人 {letters[min(extra, len(letters) - 1)]}"
        mapping[spk] = name
        roles[name] = ""
        extra += 1
    return mapping, roles


# ── 主流程 ───────────────────────────────────────────────


@dataclass
class Turn:
    start_ms: int
    end_ms: int
    speaker: str
    text: str
    role: str = "speech"  # speech | production


def build_turns(
    segments: list[dict], speaker_map: dict[str, str]
) -> tuple[list[Turn], list[tuple[str, str]]]:
    fixes: list[tuple[str, str]] = []
    prelim: list[Turn] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if is_garbage_segment(text):
            continue
        if is_intro_segment(text, seg.get("start_ms", 0)):
            continue
        role = "production" if is_production_segment(text) else "speech"
        spk = seg.get("speaker") or "Speaker 0"
        name = speaker_map.get(spk, spk) if role == "speech" else "制作说明"
        cleaned = strip_punct(text)
        if cleaned.startswith("们"):
            cleaned = "我" + cleaned
        prelim.append(
            Turn(seg["start_ms"], seg["end_ms"], name, cleaned, role)
        )

    speech = [t for t in prelim if t.role == "speech"]
    texts = [t.text for t in speech]
    for i in range(len(texts) - 1):
        same = speech[i].speaker == speech[i + 1].speaker
        moved = stitch_pair(texts[i], texts[i + 1], same_speaker=same)
        if moved:
            texts[i], texts[i + 1] = moved
    rebuilt = [
        Turn(speech[i].start_ms, speech[i].end_ms, speech[i].speaker, texts[i].strip())
        for i in range(len(texts))
        if texts[i].strip()
    ]

    # 合并同说话人相邻段
    merged: list[Turn] = []
    for t in rebuilt:
        if merged and merged[-1].speaker == t.speaker:
            merged[-1] = Turn(
                merged[-1].start_ms, t.end_ms, t.speaker,
                join_text(merged[-1].text, t.text),
            )
        else:
            merged.append(t)

    # 术语纠错 → 补标点 → 再纠错(拼接可能产生新组合)
    for i, t in enumerate(merged):
        corrected, fx = apply_corrections(t.text)
        fixes.extend(fx)
        merged[i] = Turn(t.start_ms, t.end_ms, t.speaker, corrected)
    punctuated = restore_punctuation([t.text for t in merged])
    for i, t in enumerate(merged):
        corrected, fx = apply_corrections(space_mixed(punctuated[i]))
        fixes.extend(fx)
        merged[i] = Turn(t.start_ms, t.end_ms, t.speaker, corrected)

    production = [t for t in prelim if t.role == "production"]
    return merged + production, fixes


def build_markdown(
    *,
    title: str,
    segments: list[dict],
    url: str = "",
    podcast: str = "",
    duration_label: str = "",
    shownotes: str = "",
    host_name: str = "",
    guest_name: str = "",
    generated_at: str = "",
) -> str:
    if not host_name or not guest_name:
        h, g = parse_speaker_names(shownotes)
        host_name = host_name or h
        guest_name = guest_name or g

    speaker_map, roles = map_speakers(segments, host_name, guest_name)
    turns, fixes = build_turns(segments, speaker_map)
    speech = [t for t in turns if t.role == "speech"]
    production = [t for t in turns if t.role == "production"]

    stats: dict[str, int] = {}
    for t in speech:
        stats[t.speaker] = stats.get(t.speaker, 0) + t.end_ms - t.start_ms
    total = sum(stats.values()) or 1

    source = f"播客: {podcast}" if podcast else "播客"
    meta_parts = [source]
    if url:
        meta_parts.append(f"链接: {url}")
    if duration_label:
        meta_parts.append(f"时长 {duration_label}")
    meta_parts.append("引擎: FunASR(paraformer + CAM++ 说话人分离)")
    if generated_at:
        meta_parts.append(f"生成: {generated_at}")

    lines = [f"# {title}", "", "> " + " | ".join(meta_parts), "", "## 说话人", ""]
    for name, ms in sorted(stats.items(), key=lambda x: -x[1]):
        pct = ms / total * 100
        role = roles.get(name, "")
        prefix = f"**{role}** " if role else ""
        lines.append(f"- {prefix}{name}：约 {pct:.0f}% 时长")
    lines.extend(["", "## 逐字稿", ""])

    for t in speech:
        if not t.text.strip():
            continue
        paras = split_readable_paragraphs(t.text)
        if not paras:
            continue
        role = roles.get(t.speaker, "")
        label = f"{role} · {t.speaker}" if role else t.speaker
        lines.append(f"### {format_clock(t.start_ms)} – {format_clock(t.end_ms)}　{label}")
        lines.append("")
        for para in paras:
            if para.strip():
                lines.append(para.strip())
                lines.append("")

    if production:
        lines.extend(["## 制作说明(非对话)", ""])
        for t in production:
            lines.append(f"- [{format_clock(t.start_ms)}] {t.text}")
        lines.append("")

    seen: set[tuple[str, str]] = set()
    unique_fixes = []
    for a, b in fixes:
        if (a, b) not in seen and a != b:
            seen.add((a, b))
            unique_fixes.append((a, b))
    if unique_fixes:
        lines.extend(["## 附:识别修正对照表", "", "**已修正(确信度高)**", ""])
        for wrong, right in unique_fixes[:40]:
            lines.append(f"- {wrong} → {right}")
        if len(unique_fixes) > 40:
            lines.append(f"- … 共 {len(unique_fixes)} 处")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


TRAIL_PUNCT_RE = re.compile(r"([，。！？、；：]+)$")


def _stitch_keeping_punct(items: list[tuple[str, str]]) -> list[str]:
    """对字幕做半截词缝合：ASR 会把「所以」切成上一条结尾的「所」+ 下一条开头的「以」。

    与正文缝合的区别是这里要保留句读，所以先摘下末尾标点，缝完再贴回。
    """
    names = [n for n, _ in items]
    texts = [t for _, t in items]
    for i in range(len(texts) - 1):
        m = TRAIL_PUNCT_RE.search(texts[i])
        tail = m.group(1) if m else ""
        core = texts[i][: len(texts[i]) - len(tail)] if tail else texts[i]
        moved = stitch_pair(core, texts[i + 1], same_speaker=names[i] == names[i + 1])
        if not moved:
            continue
        new_prev, new_next = moved
        texts[i] = (new_prev + tail) if new_prev.strip() else ""
        texts[i + 1] = new_next
    return texts


def _srt_clock(ms: int) -> str:
    s, msec = divmod(max(0, int(ms)), 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d},{msec:03d}"


def build_srt(
    segments: list[dict],
    *,
    shownotes: str = "",
    host_name: str = "",
    guest_name: str = "",
) -> str:
    """带说话人标签的 SRT。用句级 segments 做字幕粒度，说话人标签与 Markdown 一致。

    段内文本沿用 ASR 自带标点(paraformer 链路已过 ct-punc)，不做合并与重排，
    保证字幕与音频时间轴严格对齐。
    """
    if not host_name or not guest_name:
        h, g = parse_speaker_names(shownotes)
        host_name = host_name or h
        guest_name = guest_name or g
    speaker_map, _ = map_speakers(segments, host_name, guest_name)

    kept: list[dict] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if is_garbage_segment(text) or is_intro_segment(text, seg.get("start_ms", 0)):
            continue
        kept.append(seg)
    if not kept:
        return ""

    names = [speaker_map.get(s.get("speaker"), s.get("speaker") or "") for s in kept]
    stitched = _stitch_keeping_punct(
        [(names[i], (kept[i].get("text") or "").strip()) for i in range(len(kept))]
    )

    blocks: list[str] = []
    index = 0
    for seg, name, text in zip(kept, names, stitched):
        corrected, _ = apply_corrections(space_mixed(text))
        corrected = corrected.strip()
        if not corrected:
            continue
        prefix = f"{name}: " if name else ""
        index += 1
        blocks.append(
            f"{index}\n"
            f"{_srt_clock(seg['start_ms'])} --> {_srt_clock(seg['end_ms'])}\n"
            f"{prefix}{corrected}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, type=Path, help="diarize_asr 产出的 transcription.json")
    parser.add_argument("--title", default="播客逐字稿")
    parser.add_argument("--url", default="")
    parser.add_argument("--podcast", default="")
    parser.add_argument("--duration-label", default="")
    parser.add_argument("--shownotes-file", type=Path)
    parser.add_argument("--host", default="")
    parser.add_argument("--guest", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    data = json.loads(args.json.read_text(encoding="utf-8"))
    segments = data.get("segments") or data
    shownotes = ""
    if args.shownotes_file and args.shownotes_file.exists():
        shownotes = args.shownotes_file.read_text(encoding="utf-8")

    from datetime import datetime

    md = build_markdown(
        title=args.title,
        segments=segments,
        url=args.url,
        podcast=args.podcast,
        duration_label=args.duration_label,
        shownotes=shownotes,
        host_name=args.host,
        guest_name=args.guest,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    if args.output:
        args.output.write_text(md, encoding="utf-8")
        print(f"[OK] {args.output}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
