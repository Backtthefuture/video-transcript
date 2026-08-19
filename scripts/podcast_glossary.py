"""通用 AI/科技播客术语纠错词典 + 片头/垃圾段过滤。

只收录跨播客通用的 ASR 误识别修正；单个播客的专属词
(人名、栏目名、该节目高频产品名)放到 skill 根目录 `.podcast_glossary.json`:
    [["错误词", "修正词"], ...]
会在运行时自动加载并优先匹配。

收录标准(避免误伤正常表达):
- 纯 ASCII 条目按词边界匹配，所以 "infa" 不会命中 "infant"
- 本身是常用词的一律不收(如 minus/质朴/办起)，这类只能进节目专属词表
"""

from __future__ import annotations

import json
import os
import re

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRA_GLOSSARY_FILE = os.path.join(SKILL_DIR, ".podcast_glossary.json")

# (错误模式, 修正)——按长度降序匹配，避免短词误伤
PHRASE_CORRECTIONS: list[tuple[str, str]] = [
    # GPT 系
    ("GBT 四 o", "GPT-4o"),
    ("GBT 四 O", "GPT-4o"),
    ("GBT4o", "GPT-4o"),
    ("GBT 三点五", "GPT-3.5"),
    ("GBT 3.5", "GPT-3.5"),
    ("GBT", "GPT"),
    # DeepSeek
    ("deep sik", "DeepSeek"),
    ("deep sick", "DeepSeek"),
    ("dev seek", "DeepSeek"),
    ("dev sig", "DeepSeek"),
    ("deep sig", "DeepSeek"),
    ("Deep Sig", "DeepSeek"),
    ("deep seek", "DeepSeek"),
    ("deepseek r1", "DeepSeek R1"),
    ("DeepSeekrone", "DeepSeek R1"),
    ("DeepSeekR1", "DeepSeek R1"),
    ("DeepSeek R1", "DeepSeek R1"),
    # Claude / OpenAI 系
    ("open cloud", "OpenClaw"),
    ("Open Cloud", "OpenClaw"),
    ("open claw", "OpenClaw"),
    ("cloud code", "Claude Code"),
    ("ClaudeCode", "Claude Code"),
    ("GPTWorkspace", "GPT Workspace"),
    ("open AI", "OpenAI"),
    ("open i", "OpenAI"),
    ("s thropic", "Anthropic"),
    ("anthropic", "Anthropic"),
    # 训练术语
    ("post traunier", "Post-training"),
    ("poststrining", "Post-training"),
    ("portraining", "Post-training"),
    ("portrain", "Post-training"),
    ("posttraining", "Post-training"),
    ("posttrain", "Post-training"),
    ("post training", "Post-training"),
    ("Post training", "Post-training"),
    ("post train", "Post-training"),
    ("Post-traininging", "Post-training"),
    ("prehition", "Pre-training"),
    ("pretraining", "Pre-training"),
    ("pretraing", "Pre-training"),
    ("pertraining", "Pre-training"),
    ("pre training", "Pre-training"),
    ("Pre training", "Pre-training"),
    ("pre train", "Pre-training"),
    ("per training", "Pre-training"),
    ("Pre-trainingdata", "Pre-training data"),
    ("Pre-traininging", "Pre-training"),
    # 公司/产品
    ("mimax", "MiniMax"),
    ("MIMXX", "MiniMax"),
    ("this Manus", "Manus"),
    ("thisManus", "Manus"),
    ("jenspark", "Genspark"),
    ("genspark", "Genspark"),
    ("carrot AI", "Character.AI"),
    ("CharacterAI", "Character.AI"),
    ("kimi", "Kimi"),
    ("Kimi lner", "Kimi Linear"),
    ("混员", "混元"),
    ("workbody", "WorkBuddy"),
    # 评测/工程术语
    ("benmark", "Benchmark"),
    ("办曲", "Benchmark"),
    ("办企", "Benchmark"),
    ("半形", "Benchmark"),
    ("band 曲", "Benchmark"),
    ("batch 曲", "Benchmark"),
    ("AgentBenchmark", "Agent Benchmark"),
    ("genskating", "scaling"),
    ("commodify", "commoditize"),
    ("rubbase", "rubrics"),
    ("harineys engineering", "harness engineering"),
    ("infa", "Infra"),
    ("uresearch", "researcher"),
    ("resulch", "research"),
    ("ailab", "AI Lab"),
    ("foucs", "focus"),
    # 中文常见误识别
    ("创神", "创始人"),
    ("床投", "创投"),
    ("博客媒体", "播客媒体"),
    ("训谋型", "训模型"),
    ("训机模", "训基模"),
    ("巨身", "具身"),
    ("旧霸", "巨无霸"),
    ("核成数据", "合成数据"),
    ("模婴厂", "模型厂"),
    ("长征任务", "长程任务"),
    ("自制制能体", "自治智能体"),
    ("熬in", "all in"),
    ("欧 in", "all in"),
    ("欧，in", "all in"),
    ("梭嗨", "梭哈"),
    ("NI 时代", "AI 时代"),
]

# 小宇宙片头 bumper / 平台口播(出现在开头 25 秒内则过滤)
INTRO_PATTERNS = re.compile(
    r"(这上面(也在|的)?|在小宇宙|听播客[，,]?上小宇宙|清晨洗漱|做家务|通勤路上|眼睛好累|一个人旅行|放空大脑)",
    re.I,
)
# 制作说明类(片头片尾曲、后期说明)
PRODUCTION_PATTERNS = re.compile(r"(片头曲|片尾曲|片尾音乐|本节目由|后期制作|剪辑说明)", re.I)
GARBAGE_ONLY = re.compile(r"^[\s，,。！？?、；;…\.]+$")

_extra_loaded: list[tuple[str, str]] | None = None


def _load_extra() -> list[tuple[str, str]]:
    global _extra_loaded
    if _extra_loaded is not None:
        return _extra_loaded
    _extra_loaded = []
    if os.path.exists(EXTRA_GLOSSARY_FILE):
        try:
            data = json.loads(open(EXTRA_GLOSSARY_FILE, encoding="utf-8").read())
            if isinstance(data, list):
                _extra_loaded = [
                    (str(pair[0]), str(pair[1])) for pair in data if len(pair) >= 2
                ]
        except (json.JSONDecodeError, OSError, TypeError, IndexError):
            _extra_loaded = []
    return _extra_loaded


ASCII_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+\-]*")
_pattern_cache: dict[str, re.Pattern[str]] = {}


def _pattern_for(wrong: str) -> re.Pattern[str]:
    """纯 ASCII 词按词边界匹配(infa 不命中 infant)；含中文的直接匹配。"""
    cached = _pattern_cache.get(wrong)
    if cached is None:
        body = re.escape(wrong)
        if ASCII_TERM_RE.fullmatch(wrong):
            body = rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"
        cached = re.compile(body)
        _pattern_cache[wrong] = cached
    return cached


def apply_corrections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """返回 (修正后的文本, [(原词, 修正词), ...])。"""
    fixes: list[tuple[str, str]] = []
    out = text
    table = _load_extra() + PHRASE_CORRECTIONS
    for wrong, right in sorted(table, key=lambda x: len(x[0]), reverse=True):
        if not wrong or wrong == right:
            continue
        new, n = _pattern_for(wrong).subn(right, out)
        if n and new != out:
            fixes.append((wrong, right))
            out = new
    return out, fixes


def is_intro_segment(text: str, start_ms: int) -> bool:
    return start_ms < 25000 and bool(INTRO_PATTERNS.search(text))


def is_production_segment(text: str) -> bool:
    return bool(PRODUCTION_PATTERNS.search(text))


def is_garbage_segment(text: str) -> bool:
    t = (text or "").strip()
    if not t or GARBAGE_ONLY.match(t):
        return True
    if len(t) < 4 and re.fullmatch(r"[\W\d]+", t):
        return True
    return False
