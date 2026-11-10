# -*- coding: utf-8 -*-
"""Journal tier resolution (JCR percentile / CAS partition / SJR quartile).

Licensing note: JCR and the CAS partition table are commercial data and are NOT
bundled. The bundled seed table only marks journals that are unambiguously in the
top tier. A user-supplied table (``journal_tiers.json`` or a CSV import) always
overrides the seed.

Ranking precedence for the "top N%" decision:

1. ``jif_percentile`` (JCR percentile, 0-100)  -> exact, preferred
2. ``cas_tier`` 1                               -> approximate, marked ``approx``
3. ``jcr_quartile`` Q1 with ``accept_q1``       -> approximate, opt-in only
4. nothing                                     -> ``rank_unknown`` (never silently passes)
"""
from __future__ import annotations

import csv
import difflib
import json
import pathlib
import re
import unicodedata

from .util import ensure_dir, norm_space, read_json, write_json

STOPWORDS = {"the", "of", "and", "for", "on", "in", "official", "journal"}


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)          # drop parenthetical qualifiers
    text = re.sub(r"[:;,.!?'\"/\\]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [t for t in text.split() if t not in STOPWORDS]
    return " ".join(tokens)


def normalize_issn(issn: str) -> str:
    raw = re.sub(r"[^0-9Xx]", "", issn or "").upper()
    return raw if len(raw) == 8 else ""


class JournalTable:
    def __init__(self, entries=None, source: str = "user"):
        self.entries: list[dict] = []
        self.by_title: dict[str, dict] = {}
        self.by_issn: dict[str, dict] = {}
        self.source = source
        for entry in entries or []:
            self.add(entry, source)

    # -- construction -------------------------------------------------
    def add(self, entry: dict, source: str = "user") -> None:
        if not entry or not entry.get("title"):
            return
        item = dict(entry)
        item.setdefault("source", source)
        item["_key"] = normalize_title(item["title"])
        if not item["_key"]:
            return
        self.entries.append(item)
        self.by_title[item["_key"]] = item
        for alias in item.get("aliases") or []:
            key = normalize_title(alias)
            if key:
                self.by_title.setdefault(key, item)
        for issn in item.get("issn") or []:
            key = normalize_issn(issn)
            if key:
                self.by_issn[key] = item

    def merge(self, other: "JournalTable") -> "JournalTable":
        merged = JournalTable(self.entries, self.source)
        for entry in other.entries:
            merged.add(entry, entry.get("source", other.source))
        return merged

    # -- lookup -------------------------------------------------------
    def lookup(self, title: str = "", issn: str = "", fuzzy_cutoff: float = 0.9):
        key = normalize_issn(issn)
        if key and key in self.by_issn:
            return self.by_issn[key], "issn"
        nkey = normalize_title(title)
        if not nkey:
            return None, "none"
        if nkey in self.by_title:
            return self.by_title[nkey], "title"
        matches = difflib.get_close_matches(nkey, list(self.by_title.keys()), n=1, cutoff=fuzzy_cutoff)
        if matches:
            return self.by_title[matches[0]], "fuzzy"
        return None, "none"

    def rank(self, title: str = "", issn: str = "", top_percent: float = 20.0, accept_q1: bool = False) -> dict:
        entry, how = self.lookup(title, issn)
        if not entry:
            return {
                "matched": False,
                "passes": False,
                "basis": "unknown",
                "reason": "journal not found in ranking table; rank unknown",
                "source": None,
                "top_percent_required": top_percent,
            }
        percentile = _to_float(entry.get("jif_percentile"))
        quartile = (entry.get("jcr_quartile") or "").upper() or None
        cas_tier = _to_int(entry.get("cas_tier"))
        threshold = 100.0 - float(top_percent)

        if percentile is not None:
            passes = percentile >= threshold
            return {
                "matched": True,
                "title": entry.get("title"),
                "percentile": round(percentile, 2),
                "quartile": quartile,
                "cas_tier": cas_tier,
                "passes": passes,
                "approx": False,
                "basis": "jif_percentile",
                "match": how,
                "source": entry.get("source"),
                "reason": f"JIF percentile {percentile:.1f} {'>=' if passes else '<'} {threshold:.1f}",
            }
        if cas_tier == 1:
            return {
                "matched": True,
                "title": entry.get("title"),
                "percentile": None,
                "quartile": quartile,
                "cas_tier": cas_tier,
                "passes": True,
                "approx": True,
                "basis": "cas_tier",
                "match": how,
                "source": entry.get("source"),
                "reason": "CAS tier 1 (approximate top-20% proxy; import JCR percentiles for an exact decision)",
            }
        if quartile == "Q1" and accept_q1:
            return {
                "matched": True,
                "title": entry.get("title"),
                "percentile": None,
                "quartile": quartile,
                "cas_tier": cas_tier,
                "passes": True,
                "approx": True,
                "basis": "jcr_quartile",
                "match": how,
                "source": entry.get("source"),
                "reason": "JCR Q1 accepted as a proxy (Q1 is the top 25%, not the top 20%)",
            }
        return {
            "matched": True,
            "title": entry.get("title"),
            "percentile": percentile,
            "quartile": quartile,
            "cas_tier": cas_tier,
            "passes": False,
            "approx": False,
            "basis": "below_threshold",
            "match": how,
            "source": entry.get("source"),
            "reason": "journal is ranked below the requested top percentile",
        }

    def stats(self) -> dict:
        with_pct = sum(1 for e in self.entries if _to_float(e.get("jif_percentile")) is not None)
        cas1 = sum(1 for e in self.entries if _to_int(e.get("cas_tier")) == 1)
        return {"entries": len(self.entries), "with_percentile": with_pct, "cas_tier1": cas1}


def _to_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "."))
    return float(m.group(0)) if m else None


def _to_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else None


# ------------------------------------------------------------------ loaders
def load_seed(path=None) -> JournalTable:
    if path is None:
        path = pathlib.Path(__file__).resolve().parents[2] / "assets" / "journal_tiers.seed.json"
    data = read_json(path)
    return JournalTable(data.get("journals") or [], source="seed")


def load_user(path) -> JournalTable:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    data = read_json(p)
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict) and "journals" in data:
        entries = data["journals"]
    elif isinstance(data, dict):
        # Legacy shape: {"中科院一区": ["Nature", ...], "JCR_Q1": [...]}
        entries = []
        for key, names in data.items():
            if str(key).startswith("_") or not isinstance(names, list):
                continue
            for name in names:
                entry = {"title": name}
                if "一区" in str(key) or "1区" in str(key):
                    entry["cas_tier"] = 1
                if "Q1" in str(key).upper():
                    entry["jcr_quartile"] = "Q1"
                entries.append(entry)
    else:
        raise ValueError("unsupported journal table shape")
    return JournalTable(entries, source="user")


def load_all(user_path=None) -> JournalTable:
    table = load_seed()
    if user_path:
        user = load_user(user_path)
        merged = JournalTable(table.entries, source="seed")
        merged.source = "seed+user"
        for entry in user.entries:
            merged.add(entry, "user")
        return merged
    return table


# ------------------------------------------------------------------ imports
JCR_COLUMNS = {
    "title": ["journal name", "journal", "full journal title", "title", "name"],
    "issn": ["issn", "eissn", "issn/eissn"],
    "jif": ["2024 jif", "2023 jif", "jif", "impact factor", "journal impact factor"],
    "percentile": ["jif percentile", "percentile", "jif percentile (0-100)"],
    "quartile": ["jif quartile", "quartile", "jcr quartile"],
    "category": ["category", "web of science category", "categories"],
}
CAS_COLUMNS = {
    "title": ["期刊名称", "刊名", "journal name", "journal", "title"],
    "issn": ["issn", "issn号"],
    "tier": ["分区", "大类分区", "中科院分区", "tier"],
    "category": ["大类", "学科", "category"],
    "top": ["top", "是否top", "top期刊"],
}
SJR_COLUMNS = {
    "title": ["title", "journal", "source title"],
    "issn": ["issn", "issns"],
    "quartile": ["sjr best quartile", "quartile", "best quartile"],
    "h_index": ["h index", "h-index"],
    "category": ["categories", "category", "subject area"],
}


def _pick(row: dict, candidates: list[str]) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for cand in candidates:
        if cand in lowered and str(lowered[cand]).strip():
            return str(lowered[cand]).strip()
    for key, value in lowered.items():
        for cand in candidates:
            if cand in key and str(value).strip():
                return str(value).strip()
    return ""


def import_csv(path, fmt: str, out_path=None) -> dict:
    fmt = (fmt or "").lower()
    columns = {"jcr": JCR_COLUMNS, "cas": CAS_COLUMNS, "sjr": SJR_COLUMNS, "scimago": SJR_COLUMNS}.get(fmt)
    if not columns:
        raise ValueError("fmt must be one of: jcr, cas, sjr")
    src = pathlib.Path(path)
    with src.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        journals = []
        for row in reader:
            title = _pick(row, columns["title"])
            if not title:
                continue
            entry = {"title": norm_space(title)}
            issn = _pick(row, columns.get("issn", []))
            if issn:
                entry["issn"] = [x for x in re.split(r"[;,/\s]+", issn) if x]
            if fmt == "jcr":
                jif = _to_float(_pick(row, columns["jif"]))
                pct = _to_float(_pick(row, columns["percentile"]))
                quart = _pick(row, columns["quartile"]).upper()
                if jif is not None:
                    entry["jif"] = jif
                if pct is not None:
                    entry["jif_percentile"] = pct
                if quart.startswith("Q"):
                    entry["jcr_quartile"] = quart[:2]
                cat = _pick(row, columns["category"])
                if cat:
                    entry["category"] = cat
            elif fmt == "cas":
                tier_raw = _pick(row, columns["tier"])
                tier = _to_int(re.sub(r"[^0-9]", "", tier_raw or ""))
                if tier is not None:
                    entry["cas_tier"] = tier
                if _pick(row, columns.get("top", [])).lower() in ("是", "yes", "true", "1", "top"):
                    entry["cas_top"] = True
                cat = _pick(row, columns["category"])
                if cat:
                    entry["category"] = cat
            else:
                quart = _pick(row, columns["quartile"]).upper()
                if quart.startswith("Q"):
                    entry["sjr_quartile"] = quart[:2]
                h = _to_int(_pick(row, columns["h_index"]))
                if h is not None:
                    entry["sjr_h_index"] = h
            journals.append(entry)

    payload = {
        "schema_version": "1.0",
        "source": fmt,
        "imported_from": str(src),
        "journals": journals,
    }
    result = {"imported": len(journals), "source": fmt}
    if out_path:
        write_json(out_path, payload)
        result["out"] = str(out_path)
    result["table"] = payload
    return result


def coverage_report(table: JournalTable, names: list[str]) -> dict:
    unknown = []
    for name in names:
        entry, how = table.lookup(title=name)
        if not entry:
            unknown.append(name)
    return {"total": len(names), "unknown": unknown, "coverage": round(1 - len(unknown) / max(1, len(names)), 3)}
