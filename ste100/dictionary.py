"""Dictionary lookup for ASD-STE100.

The dictionary lives in data/dictionary.json (generated from the standard's PDF
by ``python -m ste100 extract``). Project-specific technical names and
technical verbs live in data/technical_names.txt and data/technical_verbs.txt.

JSON layout::

    {
      "meta": {"source": "...", "issue": "9", "entry_count": 123},
      "entries": {
        "adjust": [
          {"word": "ADJUST", "pos": "v", "approved": true,
           "forms": ["ADJUSTS", "ADJUSTED"], "meaning": "...",
           "alternatives": []}
        ],
        "utilize": [
          {"word": "utilize", "pos": "v", "approved": false,
           "forms": [], "meaning": "", "alternatives": ["USE (v)"]}
        ]
      }
    }

Keys of "entries" are lower case headwords. One headword can have several
entries (for example an approved noun and a not-approved verb).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(PACKAGE_DIR), "data")

POS_NAMES = {
    "v": "verb",
    "n": "noun",
    "adj": "adjective",
    "adv": "adverb",
    "prep": "preposition",
    "conj": "conjunction",
    "pron": "pronoun",
    "art": "article",
    "tn": "technical name",
    "tv": "technical verb",
}


@dataclass
class Entry:
    word: str
    pos: str
    approved: bool
    forms: List[str] = field(default_factory=list)
    meaning: str = ""
    alternatives: List[str] = field(default_factory=list)
    source: str = "dictionary"

    @property
    def headword(self) -> str:
        return self.word.lower()

    def to_dict(self) -> dict:
        return {
            "word": self.word,
            "pos": self.pos,
            "pos_name": POS_NAMES.get(self.pos, self.pos),
            "approved": self.approved,
            "forms": self.forms,
            "meaning": self.meaning,
            "alternatives": self.alternatives,
            "source": self.source,
        }


@dataclass
class LookupResult:
    token: str
    headword: Optional[str]
    entries: List[Entry]
    via: str  # "exact", "form", "inflection", "technical", "none"

    @property
    def found(self) -> bool:
        return bool(self.entries)

    @property
    def approved_entries(self) -> List[Entry]:
        return [e for e in self.entries if e.approved]

    @property
    def is_approved(self) -> bool:
        return bool(self.approved_entries)

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "headword": self.headword,
            "found": self.found,
            "approved": self.is_approved,
            "via": self.via,
            "entries": [e.to_dict() for e in self.entries],
        }


# Irregular forms that suffix stripping can not recover.
IRREGULAR = {
    "is": "be", "are": "be", "was": "be", "were": "be", "been": "be", "am": "be",
    "has": "have", "had": "have",
    "does": "do", "did": "do", "done": "do",
    "went": "go", "gone": "go", "goes": "go",
    "made": "make", "kept": "keep", "held": "hold", "found": "find",
    "got": "get", "gotten": "get", "gave": "give", "given": "give",
    "took": "take", "taken": "take", "put": "put", "set": "set", "cut": "cut",
    "let": "let", "shut": "shut", "fell": "fall", "fallen": "fall",
    "broke": "break", "broken": "break", "brought": "bring", "bought": "buy",
    "caught": "catch", "chose": "choose", "chosen": "choose", "came": "come",
    "drew": "draw", "drawn": "draw", "fed": "feed", "felt": "feel",
    "fit": "fit", "flew": "fly", "flown": "fly", "froze": "freeze",
    "frozen": "freeze", "grew": "grow", "grown": "grow", "hung": "hang",
    "knew": "know", "known": "know", "laid": "lay", "led": "lead", "left": "leave",
    "lent": "lend", "lit": "light", "lost": "lose", "meant": "mean", "met": "meet",
    "paid": "pay", "ran": "run", "rang": "ring", "rose": "rise", "risen": "rise",
    "said": "say", "saw": "see", "seen": "see", "sent": "send", "shook": "shake",
    "shaken": "shake", "shot": "shoot", "showed": "show", "shown": "show",
    "spent": "spend", "spun": "spin", "split": "split", "spread": "spread",
    "stood": "stand", "struck": "strike", "stuck": "stick", "swung": "swing",
    "taught": "teach", "told": "tell", "thought": "think", "threw": "throw",
    "thrown": "throw", "understood": "understand", "wore": "wear", "worn": "wear",
    "won": "win", "wound": "wind", "wrote": "write", "written": "write",
    "children": "child", "men": "man", "women": "woman", "feet": "foot",
    "teeth": "tooth", "data": "data", "better": "good", "best": "good",
    "worse": "bad", "worst": "bad", "less": "little", "least": "little",
    "more": "more", "most": "most", "further": "far", "farther": "far",
}

VOWELS = set("aeiou")


def candidate_stems(token: str) -> List[str]:
    """Possible dictionary headwords for an inflected token, best guess first."""
    return [s for s, _ in candidate_pairs(token)]


def candidate_pairs(token: str) -> List[tuple]:
    """(stem, suffix) pairs for an inflected token, best guess first."""
    t = token.lower()
    out: List[tuple] = []
    suffix = {"v": ""}

    def add(s: str) -> None:
        if s and s != t and s not in [o[0] for o in out] and len(s) >= 2:
            out.append((s, suffix["v"]))

    if t in IRREGULAR:
        suffix["v"] = "irregular"
        add(IRREGULAR[t])
    suffix["v"] = "s"
    if t.endswith("'s"):
        add(t[:-2])
    if t.endswith("ies") and len(t) > 4:
        add(t[:-3] + "y")
    if t.endswith("es") and len(t) > 3:
        add(t[:-2])
    if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
        add(t[:-1])
    for suf in ("ed", "ing", "er", "est"):
        suffix["v"] = suf
        if t.endswith(suf) and len(t) > len(suf) + 2:
            base = t[: -len(suf)]
            add(base)
            add(base + "e")
            if len(base) > 2 and base[-1] == base[-2] and base[-1] not in VOWELS:
                add(base[:-1])  # stopped -> stop
            if base.endswith("i"):
                add(base[:-1] + "y")  # applied -> apply, easier -> easy
    suffix["v"] = "ly"
    if t.endswith("ly") and len(t) > 4:
        add(t[:-2])
        if t.endswith("ily"):
            add(t[:-3] + "y")
    return out


def _read_term_file(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    terms = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                terms.append(line)
    return terms


class Dictionary:
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or os.environ.get("STE100_DATA_DIR") or DEFAULT_DATA_DIR
        self.meta: dict = {}
        self.entries: Dict[str, List[Entry]] = {}
        self.form_index: Dict[str, List[str]] = {}
        self.technical_names: List[str] = []
        self.technical_verbs: List[str] = []
        self.multiword_names: List[List[str]] = []
        self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        path = os.path.join(self.data_dir, "dictionary.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.meta = raw.get("meta", {})
            for head, items in raw.get("entries", {}).items():
                for item in items:
                    self._add(Entry(
                        word=item["word"],
                        pos=item.get("pos", ""),
                        approved=bool(item.get("approved")),
                        forms=list(item.get("forms", [])),
                        meaning=item.get("meaning", ""),
                        alternatives=list(item.get("alternatives", [])),
                    ), key=head)

        # Technical nouns that the dictionary itself names in its alternatives,
        # for example "support (v) -> use SUPPORT (TN)". These words are permitted as nouns.
        tn_names = set()
        for items in list(self.entries.values()):
            for e in items:
                for alt in e.alternatives:
                    m = re.match(r"^(.+?)\s*\(TN\)$", alt.strip(), re.I)
                    if m:
                        tn_names.add(m.group(1).strip())
        for name in sorted(tn_names):
            key = name.lower()
            if not any(e.approved and e.pos in ("n", "tn") for e in self.entries.get(key, [])):
                self._add(Entry(word=name, pos="tn", approved=True, source="dictionary (TN)",
                                meaning="Technical noun named as an alternative in the dictionary"))

        self.technical_names = _read_term_file(os.path.join(self.data_dir, "technical_names.txt"))
        self.technical_verbs = _read_term_file(os.path.join(self.data_dir, "technical_verbs.txt"))
        for name in self.technical_names:
            words = name.lower().split()
            if len(words) > 1:
                self.multiword_names.append(words)
            else:
                self._add(Entry(word=name, pos="tn", approved=True, source="technical_names.txt"))
        for verb in self.technical_verbs:
            self._add(Entry(word=verb, pos="tv", approved=True, source="technical_verbs.txt"))
        self.multiword_names.sort(key=len, reverse=True)

    def _add(self, entry: Entry, key: Optional[str] = None) -> None:
        key = (key or entry.headword).lower()
        self.entries.setdefault(key, []).append(entry)
        if entry.approved:
            for form in entry.forms:
                f = form.lower()
                if f != key:
                    self.form_index.setdefault(f, [])
                    if key not in self.form_index[f]:
                        self.form_index[f].append(key)

    @property
    def loaded(self) -> bool:
        return any(e.source == "dictionary" for v in self.entries.values() for e in v)

    # ---------------------------------------------------------------- lookup
    def lookup(self, token: str) -> LookupResult:
        t = token.lower().strip()
        if t in self.entries:
            return LookupResult(token, t, self.entries[t], "exact")
        if t in self.form_index:
            heads = self.form_index[t]
            ents = [e for h in heads for e in self.entries.get(h, [])]
            return LookupResult(token, heads[0], ents, "form")
        for stem, suf in candidate_pairs(t):
            if stem in self.entries:
                ents = self.entries[stem]
                # "-er"/"-est" are comparatives only for adjectives ("pointer" is not POINT + er);
                # "-ly" adverbs only come from adjectives.
                if suf in ("er", "est", "ly") and not any(e.pos == "adj" for e in ents):
                    continue
                return LookupResult(token, stem, ents, "inflection")
        return LookupResult(token, None, [], "none")

    def is_approved_form(self, token: str, result: LookupResult) -> bool:
        """True if the token itself is a form the dictionary approves."""
        t = token.lower()
        if result.via in ("exact", "form"):
            return result.is_approved
        # Reached through suffix stripping: only approved if the dictionary
        # lists this exact form. Technical names/verbs accept regular inflection.
        for e in result.approved_entries:
            if e.pos in ("tn", "tv"):
                return True
            if t in (f.lower() for f in e.forms):
                return True
            h = e.headword
            if e.pos == "n" and t in (h + "s", h + "es", h[:-1] + "ies"):
                return True  # plural of an approved noun
        return False

    def search(self, prefix: str, limit: int = 20) -> List[str]:
        p = prefix.lower()
        return sorted(k for k in self.entries if k.startswith(p))[:limit]

    def approved_words(self, pos: Optional[str] = None) -> Iterable[Entry]:
        for items in self.entries.values():
            for e in items:
                if e.approved and (pos is None or e.pos == pos):
                    yield e


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def words_in(text: str) -> List[str]:
    return _WORD_RE.findall(text)
