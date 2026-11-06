"""Build data/dictionary.json from the ASD-STE100 PDF.

This is a one-time build step. It needs ``pdfplumber`` (pip install pdfplumber).
The checker itself does not need it: commit the generated JSON and the offline
machine only needs the standard library.

How Part 2 is laid out (Issue 9):

    Word (part of speech) | Approved meaning/ALTERNATIVES | STE EXAMPLE | Non-STE example

* UPPERCASE word in column 1  -> approved word. Verbs and adjectives list
  their approved forms after the part of speech: "ABSORB (v), ABSORBS, ABSORBED, ABSORBED".
* lowercase word in column 1  -> not approved. Column 2 gives the approved
  alternatives in UPPERCASE with their part of speech: "INCREASE (v)".
* An approved word can also list alternatives for meanings it does not have
  ("For other meanings, use: APPROXIMATELY (adv)").

We only keep columns 1 and 2. The examples are not needed by the checker.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Dict, List, Optional

POS_RE = r"(?:v|n|adj|adv|prep|conj|pron|art|prefix|TN|TV|tn|tv)"
ALT_RE = re.compile(r"((?:[A-Z0-9][A-Z0-9'\-]*)(?:\s+[A-Z0-9][A-Z0-9'\-]*)*)\s*\((" + POS_RE + r")\)")
FOOTER_RE = re.compile(r"(Part 2 - Dictionary|Part 2 – Dictionary|^Issue 9|^\d{4}-\d{2}-\d{2}$|ASD.STE100 Simplified|^Page \d)")
DICT_PAGE_RE = re.compile(r"Page 2-1-[A-Z]\d+|2-1-[A-Z]\d+$", re.M)


def _rows(page, tol: float = 3.0):
    """Group words into visual rows. Returns list of (top, [words])."""
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    rows: List[List[dict]] = []
    for w in words:
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _columns(page) -> Optional[List[float]]:
    """x boundaries of columns 2, 3, 4 from the table header."""
    words = page.extract_words()
    x_alt = next((w["x0"] for w in words if w["text"] == "ALTERNATIVES"), None)
    x_ste = next((w["x0"] for w in words if w["text"] == "STE"
                  and any(v["text"] == "EXAMPLE" and abs(v["top"] - w["top"]) < 3 for v in words)), None)
    x_non = next((w["x0"] for w in words if w["text"] == "Non-STE"), None)
    if None in (x_alt, x_ste, x_non):
        return None
    header_top = next(w["top"] for w in words if w["text"] == "ALTERNATIVES")
    return [x_alt - 10, x_ste - 6, x_non - 6, header_top]


NOTE_RE = re.compile(r"^(No other|forms of this|forms\.?$|verb forms|of this adjective|adjective\.?$)", re.I)
HAS_POS_RE = re.compile(r"\((?:" + POS_RE + r")(?:[,/ ]+(?:" + POS_RE + r"))*\)")


def _is_upper(text: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", text)
    return bool(letters) and letters.upper() == letters


def _starts_new_entry(rows: List[tuple], i: int, current: Optional[dict]) -> bool:
    """Decide if the column-1 text of rows[i] opens a new dictionary entry.

    The PDF is not consistent with commas between verb forms, so:
    * a hyphen at the end of the previous line joins the word ("ELECTROMAG-" + "NETIC");
    * a line that starts with "(" continues the entry (wrapped part of speech
      or comparative forms like "(LARGER, LARGEST)");
    * a line with its own "(pos)" starts a new entry, and so does a line whose
      next column-1 line is a lone "(pos)" (a headword that wrapped);
    * an UPPERCASE line directly below a verb or adjective line, that looks
      like an inflection of it, is a form ("PROTRUDE (v)" / "PROTRUDES");
    * otherwise it starts a new entry when the current one is complete.
    """
    c1 = rows[i][1]
    if current is None:
        return True
    head = current["col1"].strip()
    if head.endswith("-"):
        return False
    if c1.startswith("("):
        return False
    if head.count("(") > head.count(")"):  # "process (in the" / "process of) (prep)"
        return False
    prev_had_c1 = i > 0 and bool(rows[i - 1][1])
    if "(" not in head and prev_had_c1:  # headword wrapped: "DOWNSTREAM" / "OF (prep)"
        return False
    if HAS_POS_RE.search(c1):
        return True
    if c1.endswith("-"):  # "COUNTERCLOCK-" / "WISE (adv)"
        return True
    nxt = next((r[1] for r in rows[i + 1:i + 3] if r[1]), "")
    if nxt.startswith("(") and HAS_POS_RE.match(nxt):
        return True
    if not current["complete"]:
        return False
    headword = head.split("(")[0].strip().upper()
    first = c1.split(",")[0].split()[0].upper() if c1.split() else ""
    if (_is_upper(c1) and prev_had_c1 and re.search(r"\((?:v|adj)\)", head)
            and first != headword and first[:3] == headword[:3]):
        return False
    return True


def _norm_pos(p: str) -> str:
    p = p.lower()
    return {"tn": "tn", "tv": "tv"}.get(p, p)


def headword_key(head: str) -> str:
    """Lookup key for a headword. 'process (in the process of)' -> 'in the process of',
    'provided (that)' -> 'provided that'."""
    h = head.lower().strip()
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", h)
    if m:
        outer, inner = m.group(1).strip(), m.group(2).strip()
        if outer and any(w.startswith(outer) for w in inner.split()):
            return inner
        return f"{outer} {inner}".strip()
    return h


def parse_word_cell(text: str) -> Optional[dict]:
    """Parse column 1, for example 'ABSORB (v), ABSORBS, ABSORBED, ABSORBED'."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    matches = list(HAS_POS_RE.finditer(text))
    if matches:
        m = matches[0]
        head, pos_raw, rest = text[: m.start()].strip(" ,"), m.group(0)[1:-1], text[m.end():]
        pos_list = [_norm_pos(p.strip()) for p in re.split(r"[,/ ]+", pos_raw) if p.strip()]
    else:  # a few entries (for example "FOR EXAMPLE") have no part of speech
        head, pos_list, rest = text, [""], ""
    rest = re.sub(r"\balso\b", "", rest.replace("(", " ").replace(")", " "))
    forms: List[str] = []
    for piece in rest.split(","):
        piece = piece.strip()
        if not piece:
            continue
        # Forms without commas ("PROTRUDES PROTRUDED"); keep multi-word verbs ("MAKES SURE").
        if len(head.split()) == 1 and len(piece.split()) > 1:
            forms.extend(piece.split())
        else:
            forms.append(piece)
    return {"word": head, "pos": pos_list, "approved": _is_upper(re.sub(r"\(.*?\)", "", head) or head), "forms": forms}


def parse_meaning_cell(text: str, approved: bool) -> dict:
    text = re.sub(r"\s+", " ", text).strip()
    alts = []
    for m in ALT_RE.finditer(text):
        alt = f"{m.group(1).strip()} ({m.group(2)})"
        if alt not in alts:
            alts.append(alt)
    if not approved and not alts:
        # Some alternatives have no part of speech ("beside (prep)  ADJACENT TO").
        for run in re.findall(r"\b[A-Z][A-Z0-9'\-]*(?:\s+[A-Z][A-Z0-9'\-]*)*\b", text):
            if len(run) > 1 and run not in alts:
                alts.append(run)
    if approved:
        meaning = re.split(r"For other meanings, use:", text)[0]
        meaning = ALT_RE.sub("", meaning).strip(" :;,")
    else:
        # Not-approved words sometimes carry advice instead of (or next to) an
        # alternative: "Use an accurate verb.", "To replace this word, use a different construction."
        meaning = ALT_RE.sub("", text)
        for a in alts:
            meaning = meaning.replace(a, "")
        meaning = re.sub(r"\s+", " ", meaning).strip(" :;,.")
        if not re.search(r"[a-z]{3}", meaning):
            meaning = ""
    return {"meaning": meaning, "alternatives": alts}


def extract(pdf_path: str) -> dict:
    import pdfplumber  # build-time dependency only

    rows: List[tuple] = []  # (page, col1, col2)
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if "ALTERNATIVES" not in text or not DICT_PAGE_RE.search(text):
                continue
            cols = _columns(page)
            if not cols:
                continue
            x2, x3, _x4, header_top = cols
            for row in _rows(page):
                if row[0]["top"] <= header_top + 12:  # table header (2 lines)
                    continue
                line = " ".join(w["text"] for w in row).strip()
                if FOOTER_RE.search(line):
                    continue
                c1 = " ".join(w["text"] for w in row if w["x0"] < x2).strip()
                c2 = " ".join(w["text"] for w in row if x2 <= w["x0"] < x3).strip()
                if NOTE_RE.match(c1):  # "No other verb forms." etc. in column 1
                    c1 = ""
                rows.append((pno, c1, c2))

    raw_entries: List[dict] = []
    current: Optional[dict] = None
    for i, (pno, c1, c2) in enumerate(rows):
        if c1 and _starts_new_entry(rows, i, current):
            if current:
                raw_entries.append(current)
            current = {"col1": "", "col2": "", "page": pno, "complete": False}
        if current is None:
            continue
        if c1:
            if current["col1"].endswith("-"):
                current["col1"] = current["col1"][:-1] + c1
            else:
                current["col1"] = (current["col1"] + " " + c1).strip()
            t = current["col1"]
            current["complete"] = bool(re.search(r"\([^)]*\)", t)) and not t.endswith(",")
        if c2:
            current["col2"] += " " + c2
    if current:
        raw_entries.append(current)

    entries: Dict[str, List[dict]] = {}
    problems = []
    for raw in raw_entries:
        cell = parse_word_cell(raw["col1"])
        if not cell:
            problems.append({"page": raw["page"], "text": raw["col1"].strip()})
            continue
        if cell["pos"] == [""]:
            problems.append({"page": raw["page"], "text": raw["col1"].strip(), "note": "no part of speech"})
        info = parse_meaning_cell(raw["col2"], cell["approved"])
        for pos in cell["pos"]:
            key = headword_key(cell["word"])
            keys = [key] + ([k.strip() for k in key.split(" or ")] if " or " in key else [])
            for k in keys:
                entries.setdefault(k, []).append({
                    "word": cell["word"],
                    "pos": pos,
                    "approved": cell["approved"],
                    "forms": cell["forms"],
                    "meaning": info["meaning"],
                    "alternatives": info["alternatives"],
                    "page": raw["page"],
                })

    return {
        "meta": {
            "source": "ASD-STE100 Issue 9 (2025-01-15), Part 2 - Dictionary",
            "generated_by": "python -m ste100 extract",
            "headwords": len(entries),
            "entries": sum(len(v) for v in entries.values()),
            "approved": sum(1 for v in entries.values() for e in v if e["approved"]),
            "parse_problems": problems,
        },
        "entries": dict(sorted(entries.items())),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Extract the STE dictionary from the PDF")
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None, help="output JSON (default data/dictionary.json)")
    args = ap.parse_args(argv)
    from .dictionary import DEFAULT_DATA_DIR
    import os

    out = args.out or os.path.join(DEFAULT_DATA_DIR, "dictionary.json")
    data = extract(args.pdf)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    m = data["meta"]
    print(f"Wrote {out}: {m['headwords']} headwords, {m['entries']} entries, "
          f"{m['approved']} approved, {len(m['parse_problems'])} parse problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
