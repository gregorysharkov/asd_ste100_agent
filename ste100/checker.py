"""Check text against the ASD-STE100 writing rules and dictionary.

Standard library only. Entry point: ``check_text(text, mode="auto")``.

The checker is deliberately conservative about what it calls an error:

* error   - a clear violation (not-approved word, semicolon, sentence too long, ...)
* warning - a probable violation that a person (or the agent) must confirm
* info    - something to look at (possessive form, assumed proper noun, ...)

Rules the script can not check are listed in the report under
``manual_review`` so the agent can review them itself.
"""

from __future__ import annotations

import json
import os
import re
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .dictionary import IRREGULAR, Dictionary, Entry, LookupResult, candidate_stems

MODES = ("auto", "procedure", "descriptive")
PROC_MAX_WORDS = 20   # rule 5.1
DESC_MAX_WORDS = 25   # rule 6.3
PARA_MAX_SENTENCES = 6  # rule 6.6

CONDITION_WORDS = {"if", "when", "before", "after", "while", "until", "unless", "during", "to", "for", "with", "as"}
VERB_CONTEXT = {"to", "can", "cannot", "must", "will", "not", "you", "we", "they", "could"}
ING_VERB_CONTEXT = {"before", "after", "by", "for", "without", "while", "when", "of", "in", "on", "from",
                    "is", "are", "was", "were", "be", "been", "am", "start", "starts", "stop", "stops", "continue"}
NOUN_CONTEXT = {"the", "a", "an", "this", "these", "those", "each", "all", "no", "any", "its", "their",
                "your", "his", "her", "our", "of", "some", "many", "more", "other"}
FUNCTION_POS = {"prep", "conj", "pron", "art", "adv", "v"}
BE_FORMS = {"is", "are", "was", "were", "be", "been", "being", "am"}
UNITS = {"mm", "cm", "m", "km", "in", "ft", "kg", "g", "lb", "lbs", "n", "nm", "kpa", "mpa", "pa", "psi",
         "bar", "v", "a", "ma", "w", "kw", "hz", "khz", "mhz", "ghz", "s", "ms", "min", "h", "hr", "hrs",
         "c", "f", "k", "l", "ml", "rpm", "kb", "mb", "gb", "tb", "%", "°c", "°f", "deg", "degrees"}
NUMBER_WORDS = {"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
                "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
                "million", "billion"}
OBJECT_START = {"the", "a", "an", "that", "it", "them", "all", "this", "these", "those", "each", "your"}
SUBJECT_PRONOUNS = {"it", "they", "which", "who", "he", "she", "we", "you", "i"}
BRITISH = {
    "colour": "color", "colours": "colors", "behaviour": "behavior", "centre": "center",
    "centres": "centers", "metre": "meter", "metres": "meters", "litre": "liter", "litres": "liters",
    "fibre": "fiber", "aluminium": "aluminum", "grey": "gray", "tyre": "tire", "tyres": "tires",
    "analyse": "analyze", "analysed": "analyzed", "organise": "organize", "recognise": "recognize",
    "minimise": "minimize", "maximise": "maximize", "labelled": "labeled", "labelling": "labeling",
    "travelled": "traveled", "cancelled": "canceled", "programme": "program", "licence": "license",
    "defence": "defense", "manoeuvre": "maneuver", "catalogue": "catalog", "dialogue": "dialog",
    "favour": "favor", "honour": "honor", "neighbour": "neighbor", "odour": "odor", "vapour": "vapor",
    "mould": "mold", "plough": "plow", "sulphur": "sulfur", "storey": "story", "authorise": "authorize",
    "authorised": "authorized", "initialise": "initialize", "optimise": "optimize", "normalise": "normalize",
    "utilise": "utilize", "standardise": "standardize", "prioritise": "prioritize",
}
LATIN = re.compile(r"(?<![A-Za-z])(e\.g\.|i\.e\.|etc\.?|viz\.|cf\.|et al\.?|vs\.?|per se|a priori|ad hoc)(?![A-Za-z])", re.I)
CONTRACTION = re.compile(r"\b([A-Za-z]+n['’]t|(?:it|that|there|what|here|he|she|who|where)['’]s|[A-Za-z]+['’](?:re|ll|ve|d|m))\b", re.I)
POSSESSIVE = re.compile(r"\b([A-Za-z]+)['’]s\b")
SAFETY_RE = re.compile(r"^\s*(WARNING|CAUTION|DANGER|ATTENTION|IMPORTANT|NOTE)\s*[:\-]\s*", re.I)
LIST_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)]|[a-zA-Z][.)]|\(\w{1,3}\))\s+")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
ABBREV_PROTECT = re.compile(r"\b(No|Fig|Ref|Para|Vol|approx|Mr|Ms|Dr|e\.g|i\.e)\.", re.I)

PARTICIPLES = {"been", "done", "made", "kept", "held", "found", "given", "taken", "put", "set", "cut", "shut",
                "broken", "brought", "bought", "caught", "chosen", "drawn", "fed", "frozen", "hung",
                "known", "laid", "led", "left", "lost", "meant", "paid", "sent", "shaken", "shown",
                "spent", "spun", "split", "spread", "struck", "stuck", "taught", "told", "thrown",
                "understood", "worn", "written", "seen", "gone", "grown", "flown", "fallen", "won"}


@dataclass
class Issue:
    rule: str
    severity: str
    line: int
    message: str
    token: str = ""
    suggestions: List[str] = field(default_factory=list)
    sentence: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["suggestions"]:
            d.pop("suggestions")
        if not d["token"]:
            d.pop("token")
        return d


@dataclass
class Sentence:
    text: str
    line: int
    kind: str          # "body", "heading", "list", "safety", "note"
    block: int         # paragraph id
    safety_word: str = ""
    mode: str = ""     # "procedure" / "descriptive" after classification


@dataclass
class Token:
    text: str
    kind: str          # "word", "number", "paren", "quote", "code"
    start: int
    inner: List["Token"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text -> blocks -> sentences
# ---------------------------------------------------------------------------

def _strip_markdown(line: str) -> str:
    line = re.sub(r"`[^`]*`", " CODE ", line)
    line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"https?://\S+", " URL ", line)
    line = re.sub(r"(\*\*|__|\*|_)(\S.*?\S|\S)\1", r"\2", line)
    line = re.sub(r"<[^>]+>", " ", line)
    return line


def split_sentences(text: str) -> List[Tuple[int, str]]:
    """Split a block into sentences. Returns (char offset, sentence)."""
    protected = ABBREV_PROTECT.sub(lambda m: m.group(0).replace(".", "․"), text)
    protected = LATIN.sub(lambda m: m.group(0).replace(".", "․"), protected)
    out = []
    start = 0
    for m in re.finditer(r"[.!?](?=\s+[\"'(\[]?[A-Z0-9])|[.!?]$|:\s*$", protected):
        end = m.end()
        piece = protected[start:end].strip()
        if piece:
            out.append((start + (len(protected[start:end]) - len(protected[start:end].lstrip())),
                        piece.replace("․", ".")))
        start = end
    tail = protected[start:].strip()
    if tail:
        out.append((start + (len(protected[start:]) - len(protected[start:].lstrip())), tail.replace("․", ".")))
    return out


def parse_document(text: str) -> List[Sentence]:
    lines = text.splitlines()
    blocks: List[Tuple[str, List[Tuple[int, str]], str]] = []  # kind, [(line_no, text)], safety word
    cur: List[Tuple[int, str]] = []
    cur_kind = "body"
    cur_safety = ""
    in_code = False

    def flush():
        nonlocal cur, cur_kind, cur_safety
        if cur:
            blocks.append((cur_kind, cur, cur_safety))
        cur, cur_kind, cur_safety = [], "body", ""

    for no, raw in enumerate(lines, start=1):
        if raw.strip().startswith(("```", "~~~")):
            in_code = not in_code
            flush()
            continue
        if in_code or raw.strip().startswith("|") or raw.strip() in ("---", "***", "___"):
            flush()
            continue
        line = _strip_markdown(raw)
        if not line.strip():
            flush()
            continue
        if HEADING_RE.match(line):
            flush()
            blocks.append(("heading", [(no, HEADING_RE.sub("", line).strip())], ""))
            continue
        m = SAFETY_RE.match(line)
        if m:
            flush()
            word = m.group(1).upper()
            cur_kind = "note" if word == "NOTE" else "safety"
            cur_safety = word
            rest = line[m.end():].strip()
            if rest:
                cur.append((no, rest))
            continue
        if LIST_RE.match(line):
            if cur_kind != "safety" and cur_kind != "note":
                flush()
                cur_kind = "list"
            else:
                flush()
                cur_kind = "list"
            cur.append((no, LIST_RE.sub("", line).strip()))
            continue
        if cur_kind == "list" and not line.startswith((" ", "\t")):
            flush()
        cur.append((no, line.strip()))
    flush()

    sentences: List[Sentence] = []
    for bid, (kind, parts, safety) in enumerate(blocks):
        joined = ""
        offsets: List[int] = []
        line_nos: List[int] = []
        for no, t in parts:
            offsets.append(len(joined))
            line_nos.append(no)
            joined += t + " "
        if kind == "heading":
            sentences.append(Sentence(joined.strip(), line_nos[0], kind, bid))
            continue
        for off, s in split_sentences(joined.strip()):
            idx = max(0, bisect_right(offsets, off) - 1)
            sentences.append(Sentence(s, line_nos[idx], kind, bid, safety_word=safety))
    return sentences


# ---------------------------------------------------------------------------
# Sentence -> tokens, word count (rules 8.4 to 8.7)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"\((?:[^()]|\([^()]*\))*\)"            # parenthesized text (one nesting level)
    r"|\"[^\"]*\"|“[^”]*”"                    # quoted text
    r"|[A-Za-z0-9][A-Za-z0-9'’\-./]*[A-Za-z0-9%]|[A-Za-z0-9]%?"  # words, numbers, identifiers
)


def tokenize(sentence: str) -> List[Token]:
    out: List[Token] = []
    for m in _TOKEN_RE.finditer(sentence):
        t = m.group(0)
        if t.startswith("("):
            out.append(Token(t, "paren", m.start(), tokenize(t[1:-1])))
        elif t[0] in "\"“":
            out.append(Token(t, "quote", m.start()))
        elif t in ("CODE", "URL"):
            out.append(Token(t, "code", m.start()))
        elif re.search(r"\d", t):
            out.append(Token(t, "number", m.start()))
        else:
            out.append(Token(t.rstrip("."), "word", m.start()))
    return out


def word_count(tokens: List[Token]) -> int:
    n = 0
    prev_number = False
    for t in tokens:
        if t.kind == "word" and prev_number and t.text.lower() in UNITS:
            prev_number = False  # "25 mm" is one word (rule 8.6)
            continue
        n += 1
        prev_number = t.kind == "number"
    return n


def flat_words(tokens: List[Token]) -> List[Token]:
    """Words for vocabulary checks, including words inside parentheses."""
    out: List[Token] = []
    for t in tokens:
        if t.kind == "word":
            out.append(t)
        elif t.kind == "paren":
            out.extend(flat_words(t.inner))
    return out


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

def _fmt_alts(entries: List[Entry]) -> List[str]:
    alts: List[str] = []
    for e in entries:
        for a in e.alternatives:
            if a not in alts:
                alts.append(a)
    return alts


def _advice(entries: List[Entry]) -> str:
    notes = [e.meaning for e in entries if not e.approved and e.meaning]
    return (" " + " ".join(notes) + ".") if notes else ""


class Checker:
    def __init__(self, dictionary: Optional[Dictionary] = None):
        self.d = dictionary or Dictionary()
        rules_path = os.path.join(self.d.data_dir, "rules.json")
        self.rules: Dict[str, dict] = {}
        if os.path.exists(rules_path):
            with open(rules_path, encoding="utf-8") as fh:
                self.rules = {r["id"]: r for r in json.load(fh)["rules"]}
        # phrase index: first word -> list of (words, key)
        self.phrases: Dict[str, List[Tuple[List[str], str]]] = {}
        for key in self.d.entries:
            if " " in key and "..." not in key:
                words = key.split()
                self.phrases.setdefault(words[0], []).append((words, key))
        for words in self.d.multiword_names:
            self.phrases.setdefault(words[0], []).append((words, " ".join(words)))
        for v in self.phrases.values():
            v.sort(key=lambda p: len(p[0]), reverse=True)

    # ------------------------------------------------------------ helpers
    def _approved_pos(self, res: LookupResult) -> set:
        pos = {e.pos for e in res.approved_entries}
        if "tn" in pos:
            pos.add("n")
        if "tv" in pos:
            pos.add("v")
        return pos

    def _ing_severity(self, prev: str, nxt: str, first: bool) -> str:
        """An "-ing" word is permitted as (part of) a technical noun ("the bearing",
        "sealing compound"); it is an error when it acts as a verb ("before installing")."""
        if prev in ING_VERB_CONTEXT or first:
            return "error"
        if prev in NOUN_CONTEXT:
            return "info"
        if nxt and nxt not in NOUN_CONTEXT and nxt not in ING_VERB_CONTEXT:
            r = self.d.lookup(nxt)
            if not r.found or any(e.pos in ("n", "tn") for e in r.entries):
                return "info"
        return "warning"

    def is_base_verb(self, word: str) -> bool:
        """Approved verb (or technical verb) in its base form."""
        w = word.lower()
        for e in self.d.entries.get(w, []):
            if e.approved and e.pos in ("v", "tv"):
                return True
        return False

    def _phrase_at(self, words: List[str], i: int) -> Optional[Tuple[int, str]]:
        first = words[i]
        heads = [first] + candidate_stems(first)  # "turned on" -> "turn on"
        if first in self.d.form_index:
            heads += self.d.form_index[first]
        for h in heads:
            for pwords, key in self.phrases.get(h, []):
                n = len(pwords)
                if words[i + 1:i + n] == pwords[1:]:
                    return n, key
        return None

    # ------------------------------------------------------------ main API
    def check(self, text: str, mode: str = "auto") -> dict:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        sentences = parse_document(text)
        issues: List[Issue] = []
        unknown: Dict[str, int] = {}
        total_words = 0

        for s in sentences:
            s.mode = self._classify(s, mode)
            toks = tokenize(s.text)
            wc = word_count(toks)
            if s.kind != "heading":
                total_words += wc
            issues += self._check_words(s, toks, unknown)
            if s.kind == "heading":
                continue
            issues += self._check_punctuation(s)
            issues += self._check_verbs(s, toks)
            issues += self._check_length(s, wc)
            if s.mode == "procedure" and s.kind not in ("note",):
                issues += self._check_procedure(s, toks, explicit=(mode == "procedure"))
            elif s.kind in ("body", "list"):
                issues += self._check_condition_comma(s, toks)
            if s.kind == "note":
                issues += self._check_note(s, toks)
            issues += self._check_noun_clusters(s, toks)

        issues += self._check_paragraphs(sentences)
        issues += self._check_safety(sentences)

        # de-duplicate (same rule, line, token)
        seen = set()
        uniq: List[Issue] = []
        for i in issues:
            k = (i.rule, i.line, i.token.lower(), i.message)
            if k not in seen:
                seen.add(k)
                uniq.append(i)
        uniq.sort(key=lambda i: (i.line, {"error": 0, "warning": 1, "info": 2}[i.severity]))

        counts = {sev: sum(1 for i in uniq if i.severity == sev) for sev in ("error", "warning", "info")}
        used_modes = {s.mode for s in sentences if s.kind != "heading"}
        if any(s.kind == "safety" for s in sentences):
            used_modes.add("safety")
        manual = [
            {"id": r["id"], "check": r["check"], "statement": r["statement"]}
            for r in self.rules.values()
            if r["check"] in ("manual", "partial") and (r["applies_to"] == "all" or r["applies_to"] in used_modes)
        ]
        return {
            "mode": mode,
            "dictionary_loaded": self.d.loaded,
            "summary": {
                "errors": counts["error"], "warnings": counts["warning"], "info": counts["info"],
                "sentences": sum(1 for s in sentences if s.kind != "heading"),
                "words": total_words,
                "passed": counts["error"] == 0,
            },
            "issues": [i.to_dict() for i in uniq],
            "unknown_words": sorted(unknown),
            "manual_review": manual,
        }

    # ------------------------------------------------------------ classify
    def _classify(self, s: Sentence, mode: str) -> str:
        if mode != "auto":
            return mode
        if s.kind in ("heading",):
            return "descriptive"
        words = [t.text.lower() for t in tokenize(s.text) if t.kind == "word"]
        if not words:
            return "descriptive"
        if self.is_base_verb(words[0]) or words[:2] == ["do", "not"]:
            return "procedure"
        if words[0] in CONDITION_WORDS and "," in s.text:
            after = s.text.split(",", 1)[1]
            aw = [t.text.lower() for t in tokenize(after) if t.kind == "word"]
            if aw and (self.is_base_verb(aw[0]) or aw[:2] == ["do", "not"]):
                return "procedure"
        return "descriptive"

    # ------------------------------------------------------------ words
    def _check_words(self, s: Sentence, toks: List[Token], unknown: Dict[str, int]) -> List[Issue]:
        issues: List[Issue] = []
        wtoks = flat_words(toks)
        words = [t.text.lower().replace("’", "'") for t in wtoks]
        letters = re.sub(r"[^A-Za-z]", "", s.text)
        all_caps = bool(letters) and letters.isupper()
        i = 0
        while i < len(words):
            w = words[i]
            raw = wtoks[i].text
            prev = words[i - 1] if i > 0 else ""

            # British spelling (rule 1.14)
            if w in BRITISH:
                issues.append(Issue("1.14", "error", s.line, f'"{raw}" is British spelling. Use American English spelling.',
                                    raw, [BRITISH[w]], s.text))

            # multi-word entries and technical nouns
            ph = self._phrase_at(words, i)
            if ph:
                n, key = ph
                ents = self.d.entries.get(key, [])
                if ents and not any(e.approved for e in ents):
                    phrase = " ".join(t.text for t in wtoks[i:i + n])
                    individually_ok = all(self.d.lookup(x).is_approved for x in words[i:i + n])
                    sev = "warning" if individually_ok else "error"
                    rule = "9.3" if any(e.pos == "v" for e in ents) else "1.1"
                    msg = f'"{phrase}" is not approved in STE.' + _advice(ents)
                    if individually_ok:
                        msg += " (If the words do not belong together here, ignore this.)"
                    issues.append(Issue(rule, sev, s.line, msg, phrase, _fmt_alts(ents), s.text))
                i += n
                continue

            # possessive: check the noun without 's (rule GR-8 handled separately)
            if w.endswith("'s"):
                w = w[:-2]

            if "-" in w and w not in self.d.entries:
                parts = [p for p in w.split("-") if p]
                # hyphenated: each part must be acceptable (rule 8.2); report parts
                p_prev = prev
                for k, p in enumerate(parts):
                    issues += self._check_single(s, p, p, p_prev, i == 0 and k == 0, all_caps, unknown)
                    p_prev = p
                i += 1
                continue

            nxt = words[i + 1] if i + 1 < len(words) else ""
            issues += self._check_single(s, w, raw, prev, i == 0, all_caps, unknown, nxt)
            i += 1
        return issues

    def _verb_role(self, prev: str, nxt: str, first: bool) -> bool:
        """Is the word in a verb position? (start of a sentence = command)"""
        return (first or prev in VERB_CONTEXT or prev in SUBJECT_PRONOUNS
                or (prev in ("and", "or", "then") and nxt in OBJECT_START))

    def _check_single(self, s: Sentence, w: str, raw: str, prev: str, first: bool,
                      all_caps: bool, unknown: Dict[str, int], nxt: str = "") -> List[Issue]:
        if len(w) == 1 and w not in ("a", "i"):
            return []  # single letters are identifiers ("valve A")
        if w in NUMBER_WORDS or w in UNITS or LATIN.fullmatch(raw + ".") or LATIN.fullmatch(raw) \
                or CONTRACTION.fullmatch(raw):
            return []  # numbers and units (rule 8.6); Latin and contractions have their own checks
        res = self.d.lookup(w)
        if not res.found:
            if w.endswith("ing") and len(w) > 5:
                # "-ing" is permitted in a technical noun ("landing gear", "the bearing").
                return [Issue("3.5", self._ing_severity(prev, nxt, first), s.line,
                              f'"{raw}": use the "-ing" form only as a technical noun or in a technical noun. '
                              f"If it is a verb here, rewrite with an approved verb form (\"before you install\", not "
                              f"\"before installing\"). If it is a technical noun, add it to data/technical_names.txt.",
                              raw, [], s.text)]
            if not all_caps and not first and raw[:1].isupper():
                return [Issue("8.6", "info", s.line,
                              f'"{raw}" is not in the dictionary. Assumed to be a proper noun or a technical noun. '
                              f"Add it to data/technical_names.txt if this is correct.", raw, [], s.text)]
            if not all_caps and raw.isupper() and len(raw) <= 6:
                return [Issue("8.6", "info", s.line,
                              f'"{raw}" is assumed to be an abbreviation. Make sure that the reader knows it.',
                              raw, [], s.text)]
            unknown[w] = unknown.get(w, 0) + 1
            return [Issue("1.1", "warning", s.line,
                          f'"{raw}" is not in the STE dictionary. Replace it with an approved word, or, if it is a '
                          f"technical noun or technical verb, add it to data/technical_names.txt or data/technical_verbs.txt.",
                          raw, [], s.text)]

        if not res.is_approved and w.endswith("ing") and res.via == "inflection":
            # "housing", "wiring": the verb is not approved, but the -ing word can be a technical noun.
            sev = self._ing_severity(prev, nxt, first)
            return [Issue("3.5" if sev != "error" else "1.1", sev, s.line,
                          f'"{raw}" comes from a verb that is not approved ({res.headword.upper()}). '
                          f"It is permitted only as a technical noun or in a technical noun." + _advice(res.entries),
                          raw, _fmt_alts(res.entries), s.text)]
        if not res.is_approved:
            # Many words are rejected only as verbs ("cover (v)", "pump (v)") because the
            # noun is a technical noun. Used as a noun, the word is permitted (rules 1.5, 1.6).
            bad_pos = {e.pos for e in res.entries}
            verb_role = self._verb_role(prev, nxt, first)
            looks_inflected_verb = res.via == "inflection" and w.endswith(("ed", "ing"))
            if bad_pos == {"v"} and not verb_role and not looks_inflected_verb:
                return [Issue("1.7", "info", s.line,
                              f'"{raw}" is not approved as a verb. Here it is assumed to be a technical noun, which is '
                              f"permitted. Do not use it as a verb.", raw, _fmt_alts(res.entries), s.text)]
            tn_advice = any("technical noun" in e.meaning.lower() for e in res.entries)
            return [Issue("1.1", "warning" if tn_advice else "error", s.line,
                          f'"{raw}" is not approved in STE.' + _advice(res.entries),
                          raw, _fmt_alts(res.entries), s.text)]

        if not self.d.is_approved_form(w, res):
            forms = []
            for e in res.approved_entries:
                forms += [e.word] + e.forms
            rule = "3.5" if w.endswith("ing") else "1.4"
            sev = self._ing_severity(prev, nxt, first) if rule == "3.5" else "error"
            msg = (f'"{raw}" is not an approved form of {res.headword.upper()}. '
                   f"Approved forms: {', '.join(dict.fromkeys(forms))}.")
            if rule == "3.5":
                msg += ' Use the "-ing" form only in a technical noun.'
            return [Issue(rule, sev, s.line, msg, raw, list(dict.fromkeys(forms)), s.text)]

        # part of speech (rule 1.2): only when the word has both approved and
        # not-approved entries for different parts of speech.
        bad = [e for e in res.entries if not e.approved]
        if bad and res.via in ("exact", "form", "inflection"):
            good_pos = self._approved_pos(res)
            role = None
            if self._verb_role(prev, nxt, first):
                role = "v"
            elif prev in NOUN_CONTEXT:
                role = "n"
            if role and role not in good_pos:
                wrong = [e for e in bad if e.pos == role]
                if wrong:
                    return [Issue("1.2", "error", s.line,
                                  f'"{raw}" looks like a {"verb" if role == "v" else "noun"} here, but '
                                  f"{res.headword.upper()} is approved only as "
                                  f"{', '.join(sorted(good_pos))}." + _advice(wrong),
                                  raw, _fmt_alts(wrong), s.text)]
        return []

    # ------------------------------------------------------------ punctuation etc.
    def _check_punctuation(self, s: Sentence) -> List[Issue]:
        issues = []
        if ";" in s.text:
            issues.append(Issue("8.1", "error", s.line, "Do not use the semicolon. Write two sentences or use a vertical list.",
                                ";", [], s.text))
        for m in CONTRACTION.finditer(s.text):
            issues.append(Issue("4.2", "error", s.line, f'Do not use contractions: "{m.group(0)}".', m.group(0), [], s.text))
        for m in LATIN.finditer(s.text):
            issues.append(Issue("GR-6", "warning", s.line, f'Do not use Latin abbreviations: "{m.group(0)}". '
                                "Use English words (for example, that is, and other).", m.group(0),
                                ["for example", "that is"], s.text))
        for m in POSSESSIVE.finditer(s.text):
            if m.group(1).lower() in ("it", "that", "there", "what", "here", "he", "she", "who", "where"):
                continue
            issues.append(Issue("GR-8", "info", s.line, f'Possessive form "{m.group(0)}". It is permitted, '
                                f'but "the ... of the ..." can be clearer.', m.group(0), [], s.text))
        return issues

    # ------------------------------------------------------------ verbs
    def _is_participle(self, w: str) -> bool:
        if w in PARTICIPLES:
            return True
        if w.endswith("ed") and len(w) > 3:
            return True
        res = self.d.lookup(w)
        return any(w in (f.lower() for f in e.forms[-1:]) for e in res.approved_entries if e.pos == "v")

    def _check_verbs(self, s: Sentence, toks: List[Token]) -> List[Issue]:
        issues: List[Issue] = []
        words = [t.text.lower() for t in toks if t.kind == "word"]
        for i, w in enumerate(words):
            nxt = words[i + 1:i + 4]
            # skip "not" and one adverb ending in -ly
            j = 0
            while j < len(nxt) and (nxt[j] == "not" or (nxt[j].endswith("ly") and len(nxt[j]) > 4)):
                j += 1
            target = nxt[j] if j < len(nxt) else ""
            after = nxt[j + 1] if j + 1 < len(nxt) else ""

            if w in ("has", "have", "had") and target and self._is_participle(target) \
                    and not (prev_is_article(words, i)) and target not in ("no",):
                issues.append(Issue("3.2", "error", s.line,
                                    f'"{w} {target}" is a perfect tense. Use the simple past or simple present tense.',
                                    f"{w} {target}", [], s.text))
            elif w in BE_FORMS and target.endswith("ing") and len(target) > 4:
                r = self.d.lookup(target)
                if not (r.is_approved and self.d.is_approved_form(target, r)):
                    issues.append(Issue("3.2", "error", s.line,
                                        f'"{w} {target}" is a progressive (-ing) tense. Use the simple present or simple past tense.',
                                        f"{w} {target}", [], s.text))
            elif w in BE_FORMS and target and self._is_participle(target):
                by_agent = "by" in words[i + 2:i + 10]
                if by_agent:
                    sev, msg = "error", "Passive voice with a known agent. Use the active voice."
                elif s.mode == "procedure":
                    sev, msg = "info", ("Possible passive voice. Use the active voice in instructions. If the "
                                        "participle is an adjective (make sure that the valve is closed), it is correct.")
                else:
                    sev, msg = "info", ("Possible passive voice. This is permitted in descriptive writing only when "
                                        "the agent is unknown, or when the participle is an adjective (the valve is closed).")
                issues.append(Issue("3.6", sev, s.line, f'"{w} {target}": {msg}', f"{w} {target}", [], s.text))
            if w == "will" and target in ("have", "be") and (after.endswith("ing") or self._is_participle(after)):
                issues.append(Issue("3.4", "error", s.line,
                                    f'"will {target} {after}" is a complex verb construction. Use the simple future tense.',
                                    f"will {target} {after}", [], s.text))
        return issues

    # ------------------------------------------------------------ sentences
    def _check_length(self, s: Sentence, wc: int) -> List[Issue]:
        if s.kind == "heading":
            return []
        if s.mode == "procedure" and wc > PROC_MAX_WORDS:
            return [Issue("5.1", "error", s.line,
                          f"Procedural sentence has {wc} words. Use a maximum of {PROC_MAX_WORDS} words.",
                          "", [], s.text)]
        if s.mode == "descriptive" and wc > DESC_MAX_WORDS:
            return [Issue("6.3", "error", s.line,
                          f"Descriptive sentence has {wc} words. Use a maximum of {DESC_MAX_WORDS} words.",
                          "", [], s.text)]
        return []

    def _check_procedure(self, s: Sentence, toks: List[Token], explicit: bool) -> List[Issue]:
        issues: List[Issue] = []
        words = [t.text.lower() for t in toks if t.kind == "word"]
        if not words:
            return issues
        # rule 5.4: condition first, then a comma
        if words[0] in ("if", "when", "before", "after", "while", "until", "unless") and "," not in s.text:
            issues.append(Issue("5.4", "warning", s.line,
                                "The sentence starts with a condition. Put a comma between the condition and the command.",
                                words[0], [], s.text))
        # rule 5.3: imperative
        if explicit:
            head = words
            if words[0] in CONDITION_WORDS and "," in s.text:
                after = s.text.split(",", 1)[1]
                head = [t.text.lower() for t in tokenize(after) if t.kind == "word"]
            if head and not (self.is_base_verb(head[0]) or head[:2] == ["do", "not"]):
                issues.append(Issue("5.3", "warning", s.line,
                                    f'Write instructions in the imperative (command) form. "{head[0]}" is not an approved verb '
                                    f"in its base form.", head[0], [], s.text))
        # rule 5.2: one instruction per sentence
        for i, w in enumerate(words[1:-1], start=1):
            if w in ("and", "then") and self.is_base_verb(words[i + 1]) and words[i + 1] not in ("make",):
                nxt = words[i + 1]
                approved_noun = any(e.approved and e.pos in ("n", "tn") for e in self.d.entries.get(nxt, []))
                if not approved_noun or words[i - 1] in (",",):
                    issues.append(Issue("5.2", "warning", s.line,
                                        f'"{w} {nxt}" can start a second instruction. Write one instruction in each '
                                        f"sentence, unless the actions occur at the same time.", f"{w} {nxt}", [], s.text))
                    break
        return issues

    def _check_condition_comma(self, s: Sentence, toks: List[Token]) -> List[Issue]:
        words = [t.text.lower() for t in toks if t.kind == "word"]
        if words and words[0] in ("if", "when", "before", "after", "while", "until", "unless") and "," not in s.text \
                and any(self.is_base_verb(w) and words[k - 1] not in SUBJECT_PRONOUNS | NOUN_CONTEXT | BE_FORMS
                        for k, w in enumerate(words[2:], start=2)):
            return [Issue("5.4", "warning", s.line,
                          "If this is an instruction, put a comma between the condition and the command.",
                          words[0], [], s.text)]
        return []

    def _check_note(self, s: Sentence, toks: List[Token]) -> List[Issue]:
        words = [t.text.lower() for t in toks if t.kind == "word"]
        if words and (self.is_base_verb(words[0]) or words[:2] == ["do", "not"]):
            return [Issue("5.5", "warning", s.line,
                          "Write notes only to give information, not instructions. Move this instruction into the procedure.",
                          words[0], [], s.text)]
        return []

    def _check_noun_clusters(self, s: Sentence, toks: List[Token]) -> List[Issue]:
        """Rule 2.1: flag runs of 4+ noun-like words (heuristic)."""
        words = [t.text.lower() for t in toks if t.kind in ("word",)]
        known_names = {" ".join(n) for n in self.d.multiword_names}
        run: List[str] = []
        out: List[Issue] = []

        def nounish(w: str) -> bool:
            if w in BE_FORMS or w in NOUN_CONTEXT or w in VERB_CONTEXT:
                return False
            res = self.d.lookup(w)
            if not res.found:
                return not w.endswith(("ly", "ing"))
            pos = {e.pos for e in res.approved_entries} or {e.pos for e in res.entries}
            return bool(pos) and not (pos & FUNCTION_POS) and pos <= {"n", "adj", "tn"}

        def close():
            if len(run) >= 4:
                phrase = " ".join(run)
                if not any(name in phrase for name in known_names if len(name.split()) >= len(run) - 1):
                    out.append(Issue("2.1", "warning", s.line,
                                     f'"{phrase}" can be a multi-word noun of {len(run)} words. Use a maximum of three '
                                     f"words, or add it to data/technical_names.txt if it is an official technical noun.",
                                     phrase, [], s.text))

        for w in words:
            if nounish(w):
                run.append(w)
            else:
                close()
                run = []
        close()
        return out

    def _check_paragraphs(self, sentences: List[Sentence]) -> List[Issue]:
        by_block: Dict[int, List[Sentence]] = {}
        for s in sentences:
            if s.kind == "body":
                by_block.setdefault(s.block, []).append(s)
        out = []
        for ss in by_block.values():
            if len(ss) > PARA_MAX_SENTENCES and any(x.mode == "descriptive" for x in ss):
                out.append(Issue("6.6", "error", ss[0].line,
                                 f"This paragraph has {len(ss)} sentences. Use a maximum of {PARA_MAX_SENTENCES}.",
                                 "", [], ss[0].text[:80]))
        return out

    def _check_safety(self, sentences: List[Sentence]) -> List[Issue]:
        by_block: Dict[int, List[Sentence]] = {}
        for s in sentences:
            if s.kind == "safety":
                by_block.setdefault(s.block, []).append(s)
        out = []
        for ss in by_block.values():
            first = ss[0]
            word = first.safety_word
            if word not in ("WARNING", "CAUTION"):
                out.append(Issue("7.1", "warning", first.line,
                                 f'"{word}" is not a standard safety word. Use WARNING (risk of injury or death) or '
                                 f"CAUTION (risk of damage to equipment).", word, ["WARNING", "CAUTION"], first.text))
            words = [t.text.lower() for t in tokenize(first.text) if t.kind == "word"]
            ok_start = bool(words) and (
                self.is_base_verb(words[0]) or words[:2] == ["do", "not"] or words[0] in ("if", "when", "before", "after", "while", "until")
            )
            if not ok_start:
                out.append(Issue("7.2", "error", first.line,
                                 "Start a safety instruction with a clear command (for example \"Do not ...\", \"Make sure ...\") or a condition.",
                                 words[0] if words else "", [], first.text))
            if len(ss) < 2:
                out.append(Issue("7.3", "warning", first.line,
                                 "Give an explanation of the risk or possible result after the command "
                                 "(for example \"If you do not obey this precaution, injury can occur.\").",
                                 "", [], first.text))
        return out


def prev_is_article(words: List[str], i: int) -> bool:
    return i > 0 and words[i - 1] in ("the", "a", "an")


_default: Optional[Checker] = None


def check_text(text: str, mode: str = "auto", data_dir: Optional[str] = None) -> dict:
    global _default
    if data_dir:
        return Checker(Dictionary(data_dir)).check(text, mode)
    if _default is None:
        _default = Checker()
    return _default.check(text, mode)
