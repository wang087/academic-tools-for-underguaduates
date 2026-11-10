# -*- coding: utf-8 -*-
"""Direction taxonomy: match a Chinese/English research direction to a leaf node
and turn it into a keyword plan.

Matching is deliberately conservative: an unmatched direction returns
``matched: false`` rather than silently picking a wrong field. The caller (the
agent) is then expected to expand keywords with the LLM and continue.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

from .util import norm_space, read_json

PATH_SEPARATORS = re.compile(r"\s*(?:>|›|→|=>|::|\||/|\\|，|,|、|;|；)\s*")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"[\s\u3000]+", " ", text)
    return text.strip(" .;:，。；：、")


def match_norm(text: str) -> str:
    """Normalization used only for matching.

    Chinese users often insert conjunctions ("神经与退行性疾病") that break
    substring matching against the taxonomy ("神经退行性疾病"). Removing the
    conjunctions for comparison makes the two equal without changing display.
    """
    t = norm(text)
    if re.search(r"[\u4e00-\u9fff]", t):
        t = re.sub(r"[与和及或\s]", "", t)
    return t


def node_names(node: dict) -> list[str]:
    names = []
    for key in ("zh", "en"):
        if node.get(key):
            names.append(str(node[key]))
    names.extend(str(x) for x in (node.get("synonyms") or []))
    names.extend(str(x) for x in (node.get("aliases") or []))
    return names


def _similarity(query: str, name: str) -> float:
    if not query or not name:
        return 0.0
    if query == name:
        return 1.0
    # CJK containment: "人体微生物组学" vs "人体微生物组"
    if query in name or name in query:
        ratio = min(len(query), len(name)) / max(len(query), len(name))
        return 0.72 + 0.25 * ratio
    if re.search(r"[\u4e00-\u9fff]", query):
        # Character-level similarity keeps CJK typos/variants matchable
        # ("神经退行性疾病" vs "神经变性疾病").
        ratio = difflib.SequenceMatcher(None, query, name).ratio()
        return ratio * 0.8 if ratio >= 0.55 else 0.0
    # English: token containment helps with "microbiome" vs "human microbiome"
    q_tokens = set(query.split())
    n_tokens = set(name.split())
    if q_tokens and n_tokens:
        overlap = len(q_tokens & n_tokens) / len(q_tokens | n_tokens)
        if overlap:
            return 0.45 + 0.4 * overlap
    return difflib.SequenceMatcher(None, query, name).ratio() * 0.8


def score_node(query: str, node: dict) -> tuple[float, str]:
    q = match_norm(query)
    best = (0.0, "")
    for name in node_names(node):
        n = match_norm(name)
        if not n:
            continue
        if q == n:
            return 1.0, name
        s = _similarity(q, n)
        # Prefer longer names when scores tie (more specific match).
        s += min(len(n), 40) * 1e-4
        if s > best[0]:
            best = (s, name)
    # Keywords break ties between sibling fields: "microbiome" is a primary
    # keyword of Human Microbiome but only "soil microbiome" belongs to Soil
    # Microbiology, so an exact keyword hit should outweigh a loose name match.
    for keyword in node.get("keywords") or []:
        k = norm(str(keyword))
        if not k:
            continue
        if q == k:
            if 0.94 > best[0]:
                best = (0.94, str(keyword))
            break
        if len(k) >= 6 and (k in q or q in k):
            ratio = min(len(k), len(q)) / max(len(k), len(q))
            score = 0.78 + 0.12 * ratio
            if score > best[0]:
                best = (score, str(keyword))
    return best


def load(path) -> dict:
    data = read_json(path)
    if "disciplines" not in data:
        raise ValueError(f"taxonomy missing 'disciplines': {path}")
    return data


def walk(nodes, path=None):
    """Yield (node, path_list, depth)."""
    path = path or []
    for node in nodes:
        here = path + [node.get("zh") or node.get("en") or "?"]
        yield node, here
        yield from walk(node.get("children") or [], here)


def leaves(taxonomy: dict) -> list[tuple[dict, list[str]]]:
    out = []
    for node, path in walk(taxonomy.get("disciplines") or []):
        if not (node.get("children") or []):
            out.append((node, path))
    return out


def _match_children(segments: list[str], nodes: list[dict], path: list[str]):
    if not segments:
        return None
    seg = segments[0]
    for node in nodes:
        score, _name = score_node(seg, node)
        if score < 0.62:
            continue
        here = path + [node.get("zh") or node.get("en")]
        if len(segments) == 1:
            return node, here, score
        found = _match_children(segments[1:], node.get("children") or [], here)
        if found:
            return found
    return None


def match(query: str, taxonomy: dict) -> dict:
    """Return the best matching node plus diagnostics.

    Three strategies, in order:
      1. explicit path ("A > B > C") matched level by level;
      2. exact / alias / synonym equality on any node;
      3. containment or fuzzy match, deepest and longest name wins.
    """
    q = norm_space(query)
    if not q:
        return {"matched": False, "reason": "empty query"}

    segments = [s for s in PATH_SEPARATORS.split(q) if norm(s)]
    if len(segments) > 1:
        found = _match_children(segments, taxonomy.get("disciplines") or [], [])
        if found:
            node, path, score = found
            return {
                "matched": True,
                "match_type": "path",
                "confidence": round(min(0.99, 0.9 + 0.09 * score), 3),
                "matched_segments": segments,
                "path": path,
                "node": node,
            }

    best = None
    for node, path in walk(taxonomy.get("disciplines") or []):
        score, name = score_node(q, node)
        if score <= 0:
            continue
        depth_bonus = len(path) * 0.01
        candidate = (score + depth_bonus, node, path, name)
        if best is None or candidate[0] > best[0]:
            best = candidate

    if not best or best[0] < 0.55:
        return {
            "matched": False,
            "reason": "no taxonomy node above threshold",
            "best_guess": (best[1].get("zh") if best else None),
        }

    score, node, path, name = best
    match_type = "exact" if score >= 0.999 else ("contains" if score >= 0.72 else "fuzzy")
    return {
        "matched": True,
        "match_type": match_type,
        "confidence": round(min(0.99, score), 3),
        "matched_name": name,
        "path": path,
        "node": node,
    }


def _concepts_from_node(node: dict) -> list[dict]:
    """Turn a leaf node into one broad core block plus optional context blocks.

    Direction-level discovery wants recall, not precision: the core block ORs all
    of the node's keywords into a single expression. The concept groups are kept
    as ``role="optional"`` so an agent can promote one (for a narrower question)
    without rewriting the plan. Previously every group was AND-ed, which silently
    turned a direction query into a four-constraint query and starved the review
    pool.
    """
    keywords = [str(x) for x in (node.get("keywords") or [])]
    out: list[dict] = [
        {
            "id": "c1",
            "label": "核心主题（方向级关键词并集）",
            "role": "core",
            "terms": keywords,
            "synonyms": [],
            "broader": [],
            "narrower": [],
            "proximity": 2,
            "exact": True,
        }
    ]
    seen = {k.lower() for k in keywords}
    for idx, (label, terms) in enumerate((node.get("concepts") or {}).items(), start=2):
        clean = []
        for term in terms or []:
            t = str(term).strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                clean.append(t)
        if clean:
            out.append(
                {
                    "id": f"c{idx}",
                    "label": label,
                    "role": "optional",
                    "terms": clean,
                    "synonyms": [],
                    "broader": [],
                    "narrower": [],
                    "proximity": 2,
                    "exact": True,
                }
            )
    return out


def build_plan(query: str, taxonomy: dict, year_from: int | None = None) -> dict:
    hit = match(query, taxonomy)
    if not hit.get("matched"):
        return {
            "schema_version": "litquery-plan/1.0",
            "direction": {"input": query, "matched": False, "reason": hit.get("reason")},
            "english_keywords": [],
            "concepts": [],
            "exclude": [],
            "filters": {},
            "notes": [
                "方向未命中内置词表。请由 agent 依据领域知识补全 concepts，",
                "再执行 query build；不要用空词表直接检索。",
            ],
        }

    node = hit["node"]
    keywords = [str(x) for x in (node.get("keywords") or [])]
    scope_terms = [str(x) for x in (node.get("scope_terms") or [])]
    concepts = _concepts_from_node(node)
    # English synonyms from the node (e.g. "neurodegenerative disease") make the
    # generated query recall better; Chinese synonyms are not searchable in
    # Scopus/WoS and stay in the plan only for display.
    english_synonyms = [
        str(s)
        for s in (node.get("synonyms") or [])
        if re.search(r"[A-Za-z]", str(s)) and str(s).lower() not in {k.lower() for k in keywords}
    ]
    if concepts and english_synonyms:
        merged_synonyms = (concepts[0].get("synonyms") or []) + english_synonyms
        concepts[0]["synonyms"] = list(dict.fromkeys(merged_synonyms))
    notes = [
        "本计划只提供方向级关键词底座，未细分研究方向。",
        "细分方向应在读完权威综述后由 agent 归纳，不得预先写入。",
    ]
    children = node.get("children") or []
    if children:
        # A category such as "免疫与微生物" has no keywords of its own. Use the
        # union of its children as a single broad block instead of inventing a
        # subdivision, and tell the caller that a narrower input is better.
        merged: list[str] = []
        seen: set[str] = set()
        for child in children:
            for kw in child.get("keywords") or []:
                key = str(kw).lower()
                if key not in seen:
                    seen.add(key)
                    merged.append(str(kw))
            for term in child.get("scope_terms") or []:
                if term not in scope_terms:
                    scope_terms.append(term)
        keywords = merged[:40]
        concepts = [
            {
                "id": "c1",
                "label": "核心主题（子方向关键词并集）",
                "role": "core",
                "terms": keywords,
                "synonyms": [],
                "broader": [],
                "narrower": [],
                "proximity": 2,
                "exact": True,
            }
        ]
        notes.append(
            "输入是该大类下的上位方向（含 %d 个子方向），关键词取其并集；如需更精确，请指定具体子方向。"
            % len(children)
        )

    plan = {
        "schema_version": "litquery-plan/1.0",
        "direction": {
            "input": query,
            "matched": True,
            "match_type": hit["match_type"],
            "confidence": hit["confidence"],
            "path": hit["path"],
            "zh": node.get("zh"),
            "en": node.get("en"),
            "synonyms_zh": node.get("synonyms") or [],
        },
        "english_keywords": keywords,
        "scope_terms": scope_terms,
        "concepts": concepts,
        "exclude": [],
        "filters": {
            "year_from": year_from,
            "year_to": None,
            "document_types": ["review"],
            "languages": ["English"],
        },
        "notes": notes,
        "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return plan


def list_tree(taxonomy: dict, contains: str | None = None) -> list[dict]:
    rows = []
    for node, path in walk(taxonomy.get("disciplines") or []):
        if contains and norm(contains) not in norm(" > ".join(path)):
            continue
        rows.append(
            {
                "path": path,
                "zh": node.get("zh"),
                "en": node.get("en"),
                "leaf": not (node.get("children") or []),
                "keywords": len(node.get("keywords") or []),
            }
        )
    return rows


def default_taxonomy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "direction_taxonomy.json"
