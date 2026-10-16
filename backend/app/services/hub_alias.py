"""Hub naming: canonical hub_id generation and alias resolution.

Rules (documented in docs/DATASET_SCHEMA_MAP.md):
1. hub_id = UPPER_SNAKE_CASE of the hub's display name. Letter/digit boundaries are word
   boundaries, so "Mirpur10" and "Mirpur 10" both give MIRPUR_10.
2. Formatting-only variants (case, spacing, punctuation) are the same name:
   "Farm Gate" == "Farmgate", "Sisu Mela" == "Sisumela".
3. Different spellings are merged ONLY through dataset_config/hub_aliases.json:
   `required_aliases` (problem owner) and `approved_aliases` (human-reviewed).
4. Similar spellings (Shahbag / Shahbagh) are reported as proposals, never auto-merged.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from itertools import combinations
from pathlib import Path

from app.services.dataset_manifest import CONFIG_DIR

ALIAS_CONFIG_PATH = CONFIG_DIR / "hub_aliases.json"
PROPOSAL_SIMILARITY = 0.7

_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")


def clean_name(name) -> str:
    """Trim and collapse whitespace. Keeps the original spelling and case."""
    return re.sub(r"\s+", " ", "" if name is None else str(name)).strip()


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _fallback_id(name) -> str:
    # Names with no ASCII letters/digits (e.g. Bangla-only) still get a stable id.
    return "HUB_" + hashlib.sha1(clean_name(name).encode("utf-8")).hexdigest()[:8].upper()


def make_hub_id(name) -> str:
    """'Mirpur 10' -> MIRPUR_10, 'Dhaka Univ.' -> DHAKA_UNIV, 'Mirpur10' -> MIRPUR_10."""
    text = _ascii(clean_name(name)).replace("&", " and ")
    text = _LETTER_DIGIT.sub(" ", text)
    hub_id = _SEPARATORS.sub("_", text).strip("_").upper()
    return hub_id or _fallback_id(name)


def compact_key(name) -> str:
    """Formatting-insensitive key: 'Farm Gate', 'farmgate', 'FARM-GATE' -> 'FARMGATE'."""
    key = re.sub(r"[^A-Z0-9]", "", _ascii(clean_name(name)).upper())
    return key or _fallback_id(name)


def _separator_count(name: str) -> int:
    return len(_SEPARATORS.findall(_LETTER_DIGIT.sub(" ", name.strip())))


def load_alias_config(path: Path | None = None) -> dict:
    p = Path(path) if path else ALIAS_CONFIG_PATH
    if not p.exists():
        return {"required_aliases": [], "approved_aliases": [], "rejected_aliases": [],
                "preferred_display_names": []}
    return json.loads(p.read_text(encoding="utf-8"))


class HubRegistry:
    """Collects every raw hub name, then resolves names to canonical hubs.

    Usage is two-phase so ids never depend on the order names are seen:
    call observe() for every name first, then hub_id()/display_name()/aliases().
    """

    def __init__(self, config: dict | None = None, extra_preferred: list[str] | None = None):
        cfg = load_alias_config() if config is None else config
        self._alias: dict[str, str] = {}
        self.alias_entries: list[dict] = []
        for kind in ("required_aliases", "approved_aliases"):
            for entry in cfg.get(kind, []):
                self._alias[compact_key(entry["alias"])] = compact_key(entry["canonical"])
                self.alias_entries.append({**entry, "kind": kind})
        self._preferred: dict[str, str] = {}
        preferred = [e["canonical"] for e in self.alias_entries]
        preferred += list(cfg.get("preferred_display_names", [])) + list(extra_preferred or [])
        for name in preferred:
            self._preferred.setdefault(compact_key(name), clean_name(name))
        self._rejected = {
            frozenset((compact_key(e["alias"]), compact_key(e["canonical"])))
            for e in cfg.get("rejected_aliases", [])
        }
        self._variants: dict[str, dict[str, set[str]]] = {}
        self._evidence: dict[frozenset, list[str]] = {}

    def add_alias(self, alias: str, canonical: str, source: str, kind: str = "dataset_declared") -> None:
        """Register an alias stated explicitly by a dataset. Call before observe()."""
        a, c = compact_key(alias), compact_key(canonical)
        existing = next((e for e in self.alias_entries if compact_key(e["alias"]) == a), None)
        if existing and compact_key(existing["canonical"]) == c:
            existing.setdefault("confirmed_by", []).append(source)
            return
        self._alias[a] = c
        self._preferred.setdefault(c, clean_name(canonical))
        self.alias_entries.append({"alias": clean_name(alias), "canonical": clean_name(canonical),
                                   "source": source, "kind": kind})

    # -- resolution ---------------------------------------------------------
    def group_key(self, name) -> str:
        key, seen = compact_key(name), set()
        while key in self._alias and key not in seen:
            seen.add(key)
            key = self._alias[key]
        return key

    def same_hub(self, a, b) -> bool:
        return self.group_key(a) == self.group_key(b)

    def observe(self, name, source: str | None = None) -> str | None:
        name = clean_name(name)
        if not name:
            return None
        group = self.group_key(name)
        sources = self._variants.setdefault(group, {}).setdefault(name, set())
        if source:
            sources.add(source)
        return group

    def _display_for_group(self, group: str, fallback: str = "") -> str:
        if group in self._preferred:
            return self._preferred[group]
        variants = self._variants.get(group)
        if not variants:
            return clean_name(fallback)
        # Most word separators first ('Sisu Mela' over 'Sisumela'), then alphabetical.
        return sorted(variants, key=lambda v: (-_separator_count(v), v))[0]

    def display_name(self, name) -> str:
        return self._display_for_group(self.group_key(name), name)

    def hub_id(self, name) -> str | None:
        if not clean_name(name):
            return None
        return make_hub_id(self.display_name(name))

    def groups(self) -> list[str]:
        return sorted(self._variants)

    def group_hub_id(self, group: str) -> str:
        return make_hub_id(self._display_for_group(group))

    def group_display(self, group: str) -> str:
        return self._display_for_group(group)

    def aliases(self, group: str) -> list[str]:
        display = self._display_for_group(group)
        names = set(self._variants.get(group, {}))
        names |= {clean_name(e["alias"]) for e in self.alias_entries if self.group_key(e["alias"]) == group}
        names |= {clean_name(e["canonical"]) for e in self.alias_entries if self.group_key(e["canonical"]) == group}
        names.discard(display)
        return sorted(names)

    def sources(self, group: str) -> list[str]:
        out: set[str] = set()
        for s in self._variants.get(group, {}).values():
            out |= s
        return sorted(out)

    def applied_alias_entries(self) -> list[dict]:
        """Alias config entries whose alias or canonical name was actually observed."""
        observed = {compact_key(v) for g in self._variants.values() for v in g}
        return [e for e in self.alias_entries
                if compact_key(e["alias"]) in observed or compact_key(e["canonical"]) in observed]

    # -- proposals ----------------------------------------------------------
    def add_evidence(self, a, b, text: str) -> None:
        ga, gb = self.group_key(a), self.group_key(b)
        if ga != gb:
            self._evidence.setdefault(frozenset((ga, gb)), []).append(text)

    def proposals(self) -> list[dict]:
        """Similar-looking but unmerged names. Never applied; for human review only."""
        out = []
        for ga, gb in combinations(self.groups(), 2):
            pair = frozenset((ga, gb))
            if pair in self._rejected or re.findall(r"\d+", ga) != re.findall(r"\d+", gb):
                continue
            ratio = difflib.SequenceMatcher(None, ga, gb).ratio()
            evidence = self._evidence.get(pair, [])
            if ratio < PROPOSAL_SIMILARITY and not evidence:
                continue
            if ratio < 0.5:  # junction evidence alone is not enough for unrelated names
                continue
            a, b = self._display_for_group(ga), self._display_for_group(gb)
            out.append({
                "name_a": a, "hub_id_a": make_hub_id(a), "name_b": b, "hub_id_b": make_hub_id(b),
                "similarity": round(ratio, 3), "evidence": evidence, "applied": False,
                "how_to_apply": "add {alias, canonical, evidence} to approved_aliases in "
                                "dataset_config/hub_aliases.json (or to rejected_aliases to silence)",
            })
        return sorted(out, key=lambda p: (-p["similarity"], p["hub_id_a"], p["hub_id_b"]))
