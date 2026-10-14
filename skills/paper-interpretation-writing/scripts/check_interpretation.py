#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check a Chinese paper-interpretation draft against the writing module.

Deterministic and local-only by default: it reads text files, makes no network
calls, and never scores prose quality. What it can do mechanically is catch the
failure modes that are *checkable* -- empty filler, speculation that leaked into
a conclusion section, an abbreviation used without its first-mention gloss,
missing closing values, a draft that drifted out of its tier's budget or into
content that belongs in a higher tier, and -- given ``--source`` -- numbers that
do not appear in the paper at all. The last one is the highest-value check here:
a fabricated number is the single most damaging defect an interpretation can
carry, and it is invisible to every other kind of review.

What it cannot do is judge whether a *claim* is faithful to the paper, or
whether several tiers of the same paper agree with each other. Verb calibration,
mechanism accuracy and cross-tier consistency still need a human (or a model)
reading the source. The report says so, rather than implying a clean run means
the draft is correct.

``--model`` adds an optional second-opinion layer (still local, no network): a
fine-tuned Qwen3-0.6B compliance classifier (``deai`` adapter, measured 99.1%
on its held-out set) re-reads every sentence and catches synonym variants the
string rules miss. Its findings are **warnings only** -- they never change the
exit code, so the delivery gate keeps its deterministic behaviour. See
``scripts/small_model.py`` in the pipeline repository for setup and
``scripts/selftest_small_model.py`` for a migration check.

Usage:
    python check_interpretation.py draft.md --tier L1
    python check_interpretation.py draft.md --tier L3 --source paper.txt
    python check_interpretation.py draft.md --tier L2 --json --strict
    python check_interpretation.py draft.md --tier L2 --model
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

#: Phrases that carry no information. Any hit is an error: the template's first
#: principle is that an interpretation reconstructs an argument, and these are
#: exactly the places where an argument was replaced by a gesture at one.
FILLER_PHRASES: tuple[str, ...] = (
    "采用了多种方法",
    "采用了多种先进方法",
    "运用了多种手段",
    "进行了深入研究",
    "进行了系统研究",
    "取得了显著成果",
    "取得了重要成果",
    "具有重要意义",
    "具有重要的理论意义",
    "众所周知",
    "引起了广泛关注",
    "受到广泛关注",
    "为进一步研究奠定了基础",
    "填补了国内空白",
    "达到了国际先进水平",
)

#: Markers that a sentence is describing a hope rather than a result. Legitimate
#: in an open-questions section; a defect inside a conclusion section.
SPECULATION_MARKERS: tuple[str, ...] = (
    "有望",
    "未来可",
    "未来将",
    "将进一步",
    "或将",
    "预计",
    "亟待",
    "尚需",
)

#: Verbs strong enough that pairing them with a speculation marker in one
#: sentence is a contradiction, not a nuance.
STRONG_VERBS: tuple[str, ...] = (
    "证明了",
    "证实了",
    "确定了",
    "揭示了",
    "鉴定出",
    "阐明了",
)

#: Section headings that mean "this is a verified conclusion".
CONCLUSION_HEADINGS: tuple[str, ...] = (
    "已验证",
    "verified_conclusions",
    "主要结论",
    "结论",
    "发现",
)

#: Section headings that mean "hopes and limits belong here".
OPEN_HEADINGS: tuple[str, ...] = (
    "待验证",
    "open_questions",
    "局限",
    "展望",
    "未解决",
    "存疑",
    "讨论",
)

#: Closing-value phrases. An interpretation closes on both a cognitive value and
#: an applied value; the module calls this the double landing. "Applied" is read
#: broadly on purpose: a basic-science paper's export is usually a model, a
#: resource or a method rather than a drug target, and a checker that only
#: recognised therapy words would report a correct closing as missing one.
COGNITIVE_VALUE_MARKERS: tuple[str, ...] = (
    "提供了新的视角",
    "提供了新的分子解释",
    "为理解",
    "提供了新的线索",
    "提供了新的解释",
    "丰富了",
)

APPLICATION_VALUE_MARKERS: tuple[str, ...] = (
    "潜在的新靶点",
    "潜在靶点",
    "新的靶点",
    "潜在的治疗",
    "治疗策略",
    "临床",
    "潜在应用",
    "潜在途径",
    "动物模型",
    "疾病模型",
    "研究平台",
    "实验范式",
    "可验证的动物模型",
)

#: Abbreviations so universal that demanding a gloss would be noise.
GLOSS_EXEMPT: frozenset[str] = frozenset(
    {
        "DNA", "RNA", "PCR", "CDNA", "MRNA", "TRNA", "AI", "ML", "USA", "UK",
        "EU", "URL", "DOI", "HTML", "JSON", "CSV", "PDF", "PPT", "OK", "ID",
        "IT", "ATM", "HIV", "COVID", "WHO", "MATH", "GPU", "CPU", "API",
    }
)

#: Nucleotide letters, plus the ambiguity codes that do not collide with
#: ordinary acronyms. `UCAU` and `YCAY` are sequence motifs, not abbreviations
#: -- there is no longer form to gloss them with. `B`, `D`, `H`, `V` and `N` are
#: deliberately excluded: they are ambiguity codes too, but including them would
#: swallow real acronyms such as BACH.
_NUCLEOTIDE_LETTERS = frozenset("ACGTURYSWKM")

#: ...and even a full house of nucleotide letters is not enough on its own: in
#: the IUPAC alphabet `GWAS` is spelled G-W-A-S, and it is emphatically an
#: abbreviation. Requiring a `U` or a `Y` separates the two, because sequence
#: motifs in practice carry an RNA uracil or a pyrimidine, while ordinary
#: acronyms almost never do.
_NUCLEOTIDE_MARKERS = frozenset("UY")


def _is_sequence_motif(token: str) -> bool:
    upper = token.upper()
    return (
        len(upper) >= 3
        and set(upper) <= _NUCLEOTIDE_LETTERS
        and not set(upper).isdisjoint(_NUCLEOTIDE_MARKERS)
    )

#: Journals whose name in a lede satisfies the "which venue" element.
JOURNALS: tuple[str, ...] = (
    "Nature", "Science", "Cell", "NEJM", "New England Journal of Medicine",
    "Lancet", "JAMA", "PNAS", "BMJ", "Nature Medicine", "Nature Genetics",
    "Nature Methods", "Nature Biotechnology", "Immunity", "Cancer Cell",
)

#: Novelty claims that need the source to actually say "first".
NOVELTY_CLAIMS: tuple[str, ...] = ("首次", "开创性", "史无前例", "重大突破")

CJK_NUMERAL_CONVERSION = re.compile(
    r"(?:近|约|逾|超过|多达)?[一二三四五六七八九十百千]+[万亿]"
    r"|[一二三四五六七八九十百千]+[万亿](?:人|个|名|篇|倍|条|种)"
)

# Explicit ASCII lookarounds, not `\b`: in Python's `str` regexes a CJK
# character counts as a word character, so `用GWAS` has no boundary between the
# two and `\bGWAS\b` silently fails to match the commonest case of all.
_UPPER_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,9}(?![A-Za-z0-9])")
_MIXED_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][a-z][A-Z][A-Za-z0-9]*(?![A-Za-z0-9])"
)
_GLOSS_BEFORE = re.compile(r"[（(][^）)]{0,40}$")
# Lookarounds matter: without them `BACH2` yields the number 2 and `rs1010474`
# yields 1010474. Those are identifiers, not quantities, and checking them
# against the source produces noise that buries the real finding.
_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")
#: The same thing without lookarounds, for indexing the source only.
_NUMBER_LOOSE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{4}[-/]\d{1,2}[-/]\d{1,2}"
)
_URL = re.compile(r"https?://\S+")
_DOI = re.compile(r"\b10\.\d{4,}/\S+")
_INLINE_CODE = re.compile(r"`[^`]*`")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")

# PDF text extraction inserts letter-spacing inside numbers: a paper that prints
# `p = 0.046` extracts as `p =0 . 0 4 6`, and `13.1` as `1 3 . 1i n`. Without
# repair, every such number reads as a fabrication -- which is the one finding
# this checker must never get wrong, because a report that cries wolf gets
# ignored exactly when it matters. Two passes, decimal point preserved.
_SPACED_DECIMAL = re.compile(r"(?<=\d)\s*\.\s*(?=\d)")
_SPACED_DIGITS = re.compile(r"(?<=\d)\s+(?=\d)")

#: PDFs set these as single glyphs. `ﬁrst` never matches a search for `first`,
#: so a genuine priority claim in the source would go unrecognised.
_LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "ft", "\ufb06": "st",
}

_NOVELTY_SUPPORT = re.compile(
    r"for the first time|the first to\b|first demonstration|first report"
    r"|首次|unprecedented",
    re.I,
)


def normalise_ligatures(text: str) -> str:
    """Expand the fi/fl/ff ligatures PDF extraction leaves behind."""
    for glyph, expansion in _LIGATURES.items():
        if glyph in text:
            text = text.replace(glyph, expansion)
    return text


def mask_code_spans(text: str) -> str:
    """Blank out inline code, preserving length and line structure.

    Prose checks must tell *use* from *mention*. A checklist that says
    "never write `首次` without support" is doing its job, not making a
    priority claim, and flagging it teaches the reader to distrust the report.
    Equal-length blanking keeps offsets and line numbers valid for reporting.
    """
    return _INLINE_CODE.sub(lambda match: " " * len(match.group(0)), text)


def collapse_spaced_digits(text: str) -> str:
    """Rejoin digits that PDF extraction split apart.

    The digit-decimal-digit pass runs first so ``0 . 0 4 6`` becomes ``0.046``
    rather than ``0046``. Collapsing only ever *merges* tokens, so on the source
    side it widens the set of accepted numbers -- it can turn a true fabrication
    into a miss, but it cannot invent one. On the draft side hand-written prose
    essentially never separates two numbers with bare whitespace.
    """
    return _SPACED_DIGITS.sub("", _SPACED_DECIMAL.sub(".", text))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def strip_noise(text: str) -> str:
    """Remove URLs, DOIs, inline code, link targets, and publication dates.

    Their digits are identifiers or metadata, not findings -- checking them
    against the source would produce a stream of meaningless mismatches. A lede
    legitimately carries the publication date, and a manuscript body is not
    where that date lives.

    Digit-collapsing runs first so that a number split by PDF letter-spacing is
    rejoined before anything else looks at it.
    """
    text = normalise_ligatures(collapse_spaced_digits(text))
    text = _MD_LINK.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _DOI.sub(" ", text)
    text = _DATE.sub(" ", text)
    return text


def number_tokens(text: str, pattern: re.Pattern[str] = _NUMBER) -> dict[str, int]:
    """Normalised numeric token -> offset of first occurrence.

    Commas are stripped so ``28,279`` and ``28279`` compare equal, which is the
    comparison that matters: a digit changed is a fabrication, a thousands
    separator removed is not.
    """
    found: dict[str, int] = {}
    for match in pattern.finditer(text):
        normalised = match.group(0).replace(",", "")
        found.setdefault(normalised, match.start())
    return found


def source_index(source: str) -> set[str]:
    """Every number a source could be said to contain, indexed permissively.

    Asymmetric on purpose. The draft is scanned strictly, because a digit glued
    to a letter there is an identifier (BACH2, rs1010474) and reporting it would
    be noise. The source is scanned loosely and in both raw and
    digit-collapsed form, because extraction damages it in ways the draft never
    suffers: letter-spacing splits ``0.046`` into ``0 . 0 4 6``, words glue onto
    numbers (``13.1in``), and collapsing can fuse a footer's ``16:1542 1`` into
    ``15421``.

    A false positive here costs the checker its credibility -- a report that
    cries wolf is ignored exactly when a real fabrication appears -- so the
    source side errs towards accepting. It can let a fabrication through; it
    cannot invent one.
    """
    return set(number_tokens(strip_noise(source), _NUMBER_LOOSE)) | set(
        number_tokens(source, _NUMBER_LOOSE)
    )


def sentences(text: str) -> list[tuple[int, str]]:
    """(line number, sentence) pairs, splitting on Chinese and Latin stops."""
    out: list[tuple[int, str]] = []
    line = 1
    for block in text.splitlines():
        for piece in re.split(r"(?<=[。！？；!?;])", block):
            stripped = piece.strip()
            if stripped:
                out.append((line, stripped))
        line += 1
    return out


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) pairs for a Markdown document; one unnamed section if not.

    Section identity is what makes the speculation rule usable: 展望 is correct
    under "待验证的问题" and wrong under "已验证的结论", and only the heading
    distinguishes them.
    """
    headings = list(re.finditer(r"^#{1,6}[ \t]*(.+)$", text, re.M))
    if not headings:
        return [("", text)]
    out: list[tuple[str, str]] = []
    if headings[0].start() > 0:
        out.append(("", text[: headings[0].start()]))
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        out.append((match.group(1).strip(), text[match.end():end]))
    return out


def sections_with_offsets(text: str) -> list[tuple[str, str, int]]:
    """Like ``sections`` but also returns each body's start offset in ``text``."""
    headings = list(re.finditer(r"^#{1,6}[ \t]*(.+)$", text, re.M))
    if not headings:
        return [("", text, 0)]
    out: list[tuple[str, str, int]] = []
    if headings[0].start() > 0:
        out.append(("", text[: headings[0].start()], 0))
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        out.append((match.group(1).strip(), text[match.end():end], match.end()))
    return out


def heading_kind(heading: str) -> str:
    lowered = heading.lower()
    for marker in OPEN_HEADINGS:
        if marker.lower() in lowered:
            return "open"
    for marker in CONCLUSION_HEADINGS:
        if marker.lower() in lowered:
            return "conclusion"
    return "neutral"


def window(text: str, focus: str, width: int = 34) -> str:
    """A short excerpt centred on `focus`, so the report shows the offence.

    An interpretation's paragraphs run long, and quoting the whole line buries
    the phrase that tripped the rule under a screenful of prose.
    """
    text = text.strip()
    if not focus:
        return text[: width * 2]
    position = text.find(focus)
    if position < 0:
        return text[: width * 2]
    start = max(0, position - width)
    end = min(len(text), position + len(focus) + width)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def finding(code: str, severity: str, message: str, line: int | None = None,
            excerpt: str = "", focus: str = "") -> dict:
    item = {"code": code, "severity": severity, "message": message}
    if line is not None:
        item["line"] = line
    if excerpt:
        item["excerpt"] = window(excerpt, focus)
    return item


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_filler(text: str) -> list[dict]:
    """Empty phrases that stand where an argument should be."""
    problems = []
    for number, line in enumerate(mask_code_spans(text).splitlines(), 1):
        for phrase in FILLER_PHRASES:
            if phrase in line:
                problems.append(
                    finding(
                        "EMPTY_FILLER",
                        "error",
                        f"空话「{phrase}」：换成具体的缺口或结果",
                        number,
                        line,
                        phrase,
                    )
                )
    return problems


def check_speculation_leak(text: str) -> list[dict]:
    """Speculation inside a section that claims verified conclusions."""
    problems = []
    text = mask_code_spans(text)
    blocks = sections(text)
    has_open = any(heading_kind(h) == "open" for h, _ in blocks)

    for heading, body in blocks:
        kind = heading_kind(heading)
        for number, line in enumerate(body.splitlines(), 1):
            markers = [m for m in SPECULATION_MARKERS if m in line]
            if not markers:
                continue
            if kind == "open":
                continue
            if kind == "conclusion":
                problems.append(
                    finding(
                        "SPECULATION_IN_CONCLUSION",
                        "error",
                        f"结论段出现展望词「{markers[0]}」：移到待验证问题",
                        number,
                        line,
                        markers[0],
                    )
                )
                continue
            # No headings at all, or a neutral section: only flag a genuine
            # contradiction, so a legitimate outlook paragraph is not punished.
            if not has_open:
                strong = [v for v in STRONG_VERBS if v in line]
                if strong:
                    problems.append(
                        finding(
                            "SPECULATION_WITH_STRONG_VERB",
                            "warning",
                            f"同一句里既断言「{strong[0]}」又写「{markers[0]}」："
                            "二者强度矛盾，拆开或降级",
                            number,
                            line,
                            markers[0],
                        )
                    )
    return problems


def check_numbers(text: str, source: str | None, check_years: bool = False,
                  check_all_numbers: bool = False) -> list[dict]:
    """Numbers in the draft that do not appear in the source.

    Two defaults keep the signal clean:

    * **Years are skipped** -- a lede carries the publication date, which is
      metadata rather than a finding. ``--check-years`` includes them.
    * **Single digits are skipped** -- they are overwhelmingly figure panels,
      gene names and list ordinals. ``--check-all-numbers`` includes them.

    What is left is the population that matters: sample sizes, counts, effect
    sizes, percentages, p-values.
    """
    problems = []
    cleaned = strip_noise(text)
    draft_numbers = number_tokens(cleaned)

    for number, line in enumerate(cleaned.splitlines(), 1):
        for match in CJK_NUMERAL_CONVERSION.finditer(line):
            problems.append(
                finding(
                    "NUMBER_CONVERSION_SUSPECT",
                    "error",
                    f"「{match.group(0)}」疑似把原文数字换算成中文概数；"
                    "数值必须原样引用",
                    number,
                    line,
                    match.group(0),
                )
            )

    if source is None:
        return problems

    source_numbers = source_index(source)
    for value, offset in sorted(draft_numbers.items(), key=lambda kv: kv[1]):
        if not check_years and _YEAR.match(value):
            continue
        if not check_all_numbers and len(value.replace(".", "")) < 2:
            continue
        if value in source_numbers:
            continue
        line = cleaned.count("\n", 0, offset) + 1
        problems.append(
            finding(
                "NUMBER_NOT_IN_SOURCE",
                "error",
                f"数字 {value} 在原文中找不到；核对是否臆造或换算",
                line,
            )
        )
    return problems


def check_novelty(text: str, source: str | None) -> list[dict]:
    """Novelty claims are only allowed when the source makes them.

    Deliberately narrow. A bare ``first`` appears all over a paper's methods
    ("the first 24 hours"), so only explicit priority constructions count --
    otherwise the check would wave through exactly the overclaim it exists to
    catch.

    The check cannot tell use from mention in ordinary prose: a sentence about
    the word itself still reads as a claim. Wrapping a quoted phrase in
    backticks is what marks it as a mention -- see `mask_code_spans`.
    """
    if source is None:
        return []
    supported = bool(_NOVELTY_SUPPORT.search(normalise_ligatures(source)))
    problems = []
    for number, line in enumerate(mask_code_spans(text).splitlines(), 1):
        for claim in NOVELTY_CLAIMS:
            if claim in line and not supported:
                problems.append(
                    finding(
                        "UNSUPPORTED_NOVELTY",
                        "error",
                        f"「{claim}」在原文中找不到依据；可改为「此前未被报道」",
                        number,
                        line,
                        claim,
                    )
                )
    return problems


def check_term_gloss(text: str) -> list[dict]:
    """Every abbreviation gets its full form once, at first mention.

    Only abbreviations are checked -- a Chinese term needs no gloss. Three
    exclusions keep this usable rather than noisy:

    * a token containing a digit is a gene or protein symbol (BACH2, NRF2,
      BCL11A, HBG1). Gene symbols are conventionally written bare in Chinese
      prose; demanding a gloss for them would be wrong, not strict.
    * a small allowlist of abbreviations too universal to gloss (DNA, PCR).
    * a run of nucleotide letters is a sequence motif (UCAU, YCAY), which has
      no longer form to expand into.

    What remains is exactly the population that does need a gloss in a
    cross-disciplinary digest: GWAS, HbF, SHM, MERFISH, ATAC, CLIP.
    """
    problems = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in (_UPPER_TOKEN, _MIXED_TOKEN):
            for match in pattern.finditer(line):
                token = match.group(0)
                if token.upper() in GLOSS_EXEMPT or token in seen:
                    continue
                if any(character.isdigit() for character in token):
                    continue
                if _is_sequence_motif(token):
                    continue
                seen.add(token)
                before = line[: match.start()]
                if _GLOSS_BEFORE.search(before):
                    continue
                problems.append(
                    finding(
                        "TERM_WITHOUT_GLOSS",
                        "warning",
                        f"「{token}」首次出现未见中英对照；"
                        f"写成「中文全称（{token}）」",
                        number,
                        line,
                        token,
                    )
                )
    return problems


def check_closing_values(text: str) -> list[dict]:
    """An R3 interpretation closes on both a cognitive and an applied value."""
    problems = []
    text = mask_code_spans(text)
    has_cognitive = any(m in text for m in COGNITIVE_VALUE_MARKERS)
    has_application = any(m in text for m in APPLICATION_VALUE_MARKERS)
    if not has_cognitive:
        problems.append(
            finding(
                "MISSING_COGNITIVE_VALUE",
                "warning",
                "缺认知落点：补「为理解…提供了新的视角」一类句子",
            )
        )
    if not has_application:
        problems.append(
            finding(
                "MISSING_APPLICATION_VALUE",
                "warning",
                "缺应用落点：补「为…提供了潜在的新靶点」一类句子",
            )
        )
    return problems


def check_lede_elements(text: str) -> list[dict]:
    """R3's lede carries date, venue, original title, and institution."""
    problems = []
    text = mask_code_spans(text)
    has_date = bool(re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", text))
    has_journal = any(journal in text for journal in JOURNALS)
    has_institution = bool(
        re.search(r"大学|研究院|研究所|学院|医院|实验室|中心", text)
    )
    has_original_title = bool(
        re.search(r"[A-Za-z][A-Za-z\-']+(?:\s+[A-Za-z][A-Za-z\-']+){4,}", text)
    )

    for present, code, message in (
        (has_date, "LEDE_MISSING_DATE", "导语段缺发表日期"),
        (has_journal, "LEDE_MISSING_JOURNAL", "导语段缺期刊名"),
        (has_institution, "LEDE_MISSING_INSTITUTION", "导语段缺机构"),
        (
            has_original_title,
            "LEDE_MISSING_ORIGINAL_TITLE",
            "导语段缺英文原题——它是读者回查原文的入口",
        ),
    ):
        if not present:
            problems.append(finding(code, "warning", message))
    return problems


#: Tier -> the checks that apply. L1 is a table cell, so the lede and
#: closing-value rules would misfire on it; L2 is a card, which has a closing
#: but no five-element lede.
TIER_CHECKS = {
    "L1": ("filler", "speculation", "numbers", "novelty", "gloss", "tier"),
    "L2": (
        "filler", "speculation", "numbers", "novelty", "gloss", "closing", "tier",
    ),
    "L3": (
        "filler", "speculation", "numbers", "novelty", "gloss", "closing",
        "lede", "tier",
    ),
    "all": ("filler", "speculation", "numbers", "novelty", "gloss"),
}

#: Ceiling on Chinese characters per tier. Generous -- the point is to catch an
#: L1 that was written by abbreviating an L3, not to police a paragraph's length.
TIER_CJK_MAX = {"L1": 500, "L2": 1100, "L3": 2600}

_CJK = re.compile(r"[\u4e00-\u9fff]")

#: Content that belongs in a higher tier. L1 is consumed as table cells and
#: list items, so an error bar or a figure reference there costs the whole
#: table its comparability rather than just adding a detail.
TIER_FORBIDDEN: dict[str, tuple[tuple[re.Pattern[str], str, str], ...]] = {
    "L1": (
        (re.compile(r"±"), "误差项", "L1 不写误差项，移到 L2/L3"),
        (
            re.compile(r"置信区间|95%\s*(?:CI|置信)|p\s*[=＝]\s*0?\.\d"),
            "统计量",
            "L1 只留结论数字，不写 p 值与置信区间",
        ),
        (
            re.compile(r"(?<![A-Za-z])[Nn]\s*[=＝]\s*\d"),
            "样本量",
            "L1 不写样本量",
        ),
        (
            re.compile(r"图\s*\d|表\s*\d|Fig\.?\s*\d|Supplementary|补充图"),
            "图表引用",
            "L1 不引图表",
        ),
        (
            re.compile(
                r"凝胶迁移|免疫染色|免疫印迹|免疫组化|重采样|流式|共聚焦"
                r"|qPCR|Western|CLIP\s*峰|转录组测序",
                re.I,
            ),
            "实验步骤",
            "L1 不写实验步骤，移到 L2/L3",
        ),
    ),
}


def count_cjk(text: str) -> int:
    """Chinese characters, ignoring code spans."""
    return len(_CJK.findall(mask_code_spans(text)))


def check_tier(text: str, tier: str) -> list[dict]:
    """Enforce the tier's budget and the content that belongs higher up.

    The L1 rules are the ones that matter. An L1 produced by shortening an L3
    keeps long sentences, error bars and figure references -- and a table built
    from those cells is unreadable row by row, which is the only thing a table
    is for.
    """
    if tier not in TIER_CJK_MAX:
        return []
    problems: list[dict] = []

    length = count_cjk(text)
    ceiling = TIER_CJK_MAX[tier]
    if length > ceiling:
        problems.append(
            finding(
                "TIER_TOO_LONG",
                "warning",
                f"{tier} 档 {length} 个汉字，超出该档上限 {ceiling}；"
                "考虑升档或删减",
            )
        )

    for pattern, label, message in TIER_FORBIDDEN.get(tier, ()):
        for number, line in enumerate(mask_code_spans(text).splitlines(), 1):
            match = pattern.search(line)
            if match:
                problems.append(
                    finding(
                        f"TIER_FORBIDS_{label}",
                        "error",
                        f"{message}（命中「{match.group(0).strip()}」）",
                        number,
                        line,
                        match.group(0).strip(),
                    )
                )
    return problems


# --------------------------------------------------------------------------
# Optional model layer (deai adapter) -- warnings only, never errors
# --------------------------------------------------------------------------

#: deai label -> (code suffix, human hint). Codes are emitted as ``MODEL_*`` so
#: they can never collide with the deterministic rule codes above.
_MODEL_LABEL_HINTS = {
    "EMPTY_FILLER": "空话套话（规则黑名单外的同义变体）：换成具体的缺口或结果",
    "SPECULATION_LEAK": "结论语境出现展望/推测措辞：移到待验证问题",
    "STRONG_VERB_OVERREACH": "动词强度越级（如「证明了/证实了」超出证据强度）：降级措辞",
    "L1_FORBIDDEN": "疑似 L1 档禁写内容（误差项/样本量/图表引用/实验步骤）",
    "NUMBER_CONVERSION": "疑似把原文数字换算或概数化：数值必须原样引用",
    "UNSUPPORTED_NOVELTY": "疑似无依据的新颖性断言（首次/重大突破）",
    "TERM_NO_GLOSS": "疑似术语首现缺中英对照",
}


def model_findings(text: str, tier: str = "all") -> tuple[list[dict], dict]:
    """Sentence-level second opinion from the deai adapter.

    Two deliberate constraints keep the layer trustworthy:

    * **Warnings only.** The 99.1% held-out accuracy is real but it was
      measured on injected violations; on live drafts the model can still
      misfire, and an error here would block delivery through a gate whose
      contract is deterministic. A human reads warnings; nothing else changes.
    * **Context exemption.** ``SPECULATION_LEAK`` and ``L1_FORBIDDEN`` are
      context-dependent labels (speculation is legitimate under 待验证/展望;
      L1-forbidden content is fine in L2/L3). Sentences inside open sections
      are exempted from ``SPECULATION_LEAK``, mirroring the rule layer, and
      ``L1_FORBIDDEN`` is only reported when ``tier`` is L1 or ``all``.

    Raises ``RuntimeError`` when the model layer was explicitly requested but
    is unavailable -- an explicit switch must fail loudly, never degrade
    silently (the pipeline's standing convention).
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent.parent.parent / "scripts",   # 流程1/skills/<skill>/scripts -> 流程1/scripts
        here,
    ]
    small_model = None
    for cand in candidates:
        if (cand / "small_model.py").is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            import small_model as _sm
            small_model = _sm
            break
    if small_model is None:
        raise RuntimeError(
            "找不到 scripts/small_model.py —— deai 模型层未随流程部署。")
    try:
        model = small_model.SmallModel()
    except small_model.SmallModelError as error:
        raise RuntimeError(str(error)) from error

    # (absolute line, sentence, section kind), in document order. Line numbers
    # come from each section body's real offset in the text -- accumulating
    # body newline counts would drift, because heading lines are not part of
    # any body.
    entries: list[tuple[int, str, str]] = []
    for heading, body, offset in sections_with_offsets(text):
        kind = heading_kind(heading)
        base = text.count("\n", 0, offset)
        for relative, sentence in sentences(body):
            entries.append((base + relative, sentence, kind))

    todo = [(n, s, k) for n, s, k in entries if mask_code_spans(s).strip()]
    labels = model.deai_classify([s for _, s, _ in todo])

    problems: list[dict] = []
    unparsed = 0
    for (number, sentence, kind), label in zip(todo, labels):
        if label is None:
            unparsed += 1
            continue
        if label == "OK":
            continue
        if label == "SPECULATION_LEAK" and kind == "open":
            continue
        if label == "L1_FORBIDDEN" and tier not in ("L1", "all"):
            continue
        hint = _MODEL_LABEL_HINTS.get(label, "模型判为违规")
        problems.append(
            finding(
                f"MODEL_{label}",
                "warning",
                f"[deai 模型二审] {hint}",
                number,
                sentence,
            )
        )
    stats = {
        "sentences_checked": len(todo),
        "model_warnings": len(problems),
        "unparsed_outputs": unparsed,
        "severity_policy": "warning-only（不进 error_count，不影响退出码）",
    }
    return problems, stats


def check(text: str, source: str | None = None, tier: str = "all",
          check_years: bool = False, check_all_numbers: bool = False,
          use_model: bool = False) -> dict:
    """Run the applicable checks and return a machine-readable report."""
    if tier not in TIER_CHECKS:
        tier = "all"
    selected = TIER_CHECKS[tier]
    findings: list[dict] = []

    if "filler" in selected:
        findings += check_filler(text)
    if "speculation" in selected:
        findings += check_speculation_leak(text)
    if "numbers" in selected:
        findings += check_numbers(text, source, check_years, check_all_numbers)
    if "novelty" in selected:
        findings += check_novelty(text, source)
    if "gloss" in selected:
        findings += check_term_gloss(text)
    if "closing" in selected:
        findings += check_closing_values(text)
    if "lede" in selected:
        findings += check_lede_elements(text)
    if "tier" in selected:
        findings += check_tier(text, tier)

    model_stats = None
    if use_model:
        model_problems, model_stats = model_findings(text, tier=tier)
        findings += model_problems

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    return {
        "valid": not errors,
        "tier": tier,
        "cjk_chars": count_cjk(text),
        "source_checked": source is not None,
        "model_layer": model_stats,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "findings": findings,
        "notice": (
            "本报告只覆盖可机械判定的规则，不是文笔评分。"
            "语气校准（动词强度是否与原文对齐）、机制是否忠实于原文，"
            "以及档间一致性（L1 ⊂ L2 ⊂ L3 且语气同级）"
            "仍需回原文与跨档人工核对——校验器能发现「这个数字原文里没有」，"
            "但发现不了「这句话原文里没有」。"
        ),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_interpretation.py",
        description=(
            "校验中文论文解读草稿是否符合解读写作模板：空话、越界推测、"
            "术语缺中英对照、缺收尾落点，以及在给出 --source 时逐个核对"
            "数字是否真的在原文里。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 = 通过；1 = 有 error（或 --strict 下有 warning）；"
            "2 = 用法或读取错误。"
        ),
    )
    parser.add_argument("draft", help="待校验的解读草稿（Markdown 或纯文本）")
    parser.add_argument(
        "--source",
        help="原文文本（论文正文或摘要）。给出后逐个核对数字出处，"
             "并检查「首次」一类新颖性断言是否有依据。",
    )
    parser.add_argument(
        "--tier",
        choices=("L1", "L2", "L3", "all"),
        default="all",
        help="档位：L1 另查篇幅上限与该档禁写项（误差项/统计量/样本量/图表/实验步骤）；"
             "L3 另查导语五要素与收尾落点（默认 all，只做通用检查）",
    )
    parser.add_argument(
        "--json", action="store_true", help="输出机器可读的 JSON 报告"
    )
    parser.add_argument(
        "--strict", action="store_true", help="有 warning 也返回非零退出码"
    )
    parser.add_argument(
        "--check-years",
        action="store_true",
        help="把 4 位年份也纳入数字核对（默认跳过，因为发表日期属元数据）",
    )
    parser.add_argument(
        "--check-all-numbers",
        action="store_true",
        help="把个位数也纳入数字核对（默认跳过，因为多为图号、基因名与序号）",
    )
    parser.add_argument(
        "--encoding", default="utf-8", help="输入文件编码（默认 utf-8）"
    )
    parser.add_argument(
        "--model", action="store_true",
        help="加开 deai 模型二审（本地 Qwen3-0.6B 适配器，见 scripts/small_model.py）："
             "逐句判别规则黑名单外的同义变体，只出 warning，不影响退出码。"
             "也可用环境变量 CHECK_INTERPRETATION_MODEL=1 开启。"
             "依赖 torch/transformers/peft；不可用时**报错退出**，不静默降级。",
    )
    return parser


def _read(path: Path, encoding: str) -> str:
    return path.read_text(encoding=encoding, errors="strict")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    draft_path = Path(args.draft)
    if not draft_path.is_file():
        print(f"错误：找不到草稿文件 {draft_path}", file=sys.stderr)
        return 2
    try:
        draft = _read(draft_path, args.encoding)
    except (OSError, UnicodeDecodeError) as error:
        print(f"错误：无法读取 {draft_path}（{error}）", file=sys.stderr)
        return 2

    source = None
    if args.source:
        source_path = Path(args.source)
        if not source_path.is_file():
            print(f"错误：找不到原文文件 {source_path}", file=sys.stderr)
            return 2
        try:
            source = _read(source_path, args.encoding)
        except (OSError, UnicodeDecodeError) as error:
            print(f"错误：无法读取 {source_path}（{error}）", file=sys.stderr)
            return 2

    use_model = bool(args.model or
                     (os.environ.get("CHECK_INTERPRETATION_MODEL") or "").strip()
                     in ("1", "true", "yes", "on"))

    try:
        report = check(
            draft,
            source=source,
            tier=args.tier,
            check_years=args.check_years,
            check_all_numbers=args.check_all_numbers,
            use_model=use_model,
        )
    except RuntimeError as error:
        print(f"错误：--model 已显式开启但模型层不可用，按约定不静默降级。\n{error}",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["findings"]:
            location = f":{item['line']}" if "line" in item else ""
            print(f"[{item['severity'].upper()}] {item['code']}{location} "
                  f"{item['message']}")
            if item.get("excerpt"):
                print(f"        > {item['excerpt']}")
        print()
        model_note = ""
        if report.get("model_layer"):
            ms = report["model_layer"]
            model_note = (f"，deai 模型二审 {ms['sentences_checked']} 句"
                          f" → {ms['model_warnings']} 条 warning")
        print(f"error {report['error_count']} / warning {report['warning_count']}"
              f"（档位 {report['tier']}，{report['cjk_chars']} 个汉字，"
              f"原文核对 {'开' if report['source_checked'] else '关'}{model_note}）")
        print(report["notice"])
        if not report["findings"]:
            print("\n未发现可机械判定的问题。仍需回原文核对语气与机制。")

    if report["error_count"]:
        return 1
    if args.strict and report["warning_count"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
