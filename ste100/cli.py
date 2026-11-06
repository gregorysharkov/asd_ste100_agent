"""Command line interface.

    python -m ste100 check FILE [--mode auto|procedure|descriptive] [--format text|json]
    python -m ste100 check -  < draft.md          (read from stdin)
    python -m ste100 lookup WORD [WORD ...]       (dictionary entries and alternatives)
    python -m ste100 suggest WORD                 (approved words that start the same way)
    python -m ste100 rules [ID ...] [--manual]    (rule texts; --manual = rules the agent must review)
    python -m ste100 extract PDF                  (rebuild data/dictionary.json, needs pdfplumber)

Exit codes for "check": 0 = no errors, 1 = errors found (or warnings with
--strict), 2 = usage problem.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from typing import List, Optional

from .checker import MODES, Checker
from .dictionary import POS_NAMES, Dictionary


def _out(obj, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(obj)


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def format_report(report: dict, path: str, show_info: bool) -> str:
    s = report["summary"]
    lines = []
    if not report["dictionary_loaded"]:
        lines.append("!! data/dictionary.json is missing: only the rule checks ran. Run `python -m ste100 extract <pdf>`.")
    for i in report["issues"]:
        if i["severity"] == "info" and not show_info:
            continue
        tag = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}[i["severity"]]
        line = f"{path}:{i['line']}: {tag} [rule {i['rule']}] {i['message']}"
        if i.get("suggestions"):
            line += f"  -> use: {', '.join(i['suggestions'][:6])}"
        lines.append(line)
    if report["unknown_words"]:
        lines.append("")
        lines.append("Unknown words (replace, or add to data/technical_names.txt / technical_verbs.txt): "
                     + ", ".join(report["unknown_words"]))
    lines.append("")
    lines.append(f"{s['errors']} errors, {s['warnings']} warnings, {s['info']} info - "
                 f"{s['sentences']} sentences, {s['words']} words. "
                 + ("PASSED (no errors)" if s["passed"] else "FAILED"))
    lines.append("Rules to review yourself: " + ", ".join(r["id"] for r in report["manual_review"]))
    return "\n".join(lines)


def cmd_check(args) -> int:
    checker = Checker(Dictionary(args.data_dir) if args.data_dir else None)
    worst = 0
    reports = []
    for path in args.files:
        report = checker.check(_read(path), mode=args.mode)
        report["file"] = path
        if args.format == "json":
            if not args.verbose:
                report["manual_review"] = [r["id"] for r in report["manual_review"]]
            if not args.info:
                report["issues"] = [i for i in report["issues"] if i["severity"] != "info"]
            reports.append(report)
        else:
            print(format_report(report, path, args.info))
        s = report["summary"]
        if s["errors"] or (args.strict and s["warnings"]):
            worst = 1
    if args.format == "json":
        _out(reports[0] if len(reports) == 1 else reports, "json")
    return worst


def cmd_lookup(args) -> int:
    d = Dictionary(args.data_dir) if args.data_dir else Dictionary()
    results = []
    for word in args.words:
        res = d.lookup(word)
        r = res.to_dict()
        if res.found:
            r["approved_form"] = d.is_approved_form(word, res)
        results.append(r)
    if args.format == "json":
        _out(results if len(results) > 1 else results[0], "json")
        return 0
    for r in results:
        if not r["found"]:
            print(f"{r['token']}: NOT IN DICTIONARY. Replace it, or, if it is a technical noun/verb, "
                  f"add it to data/technical_names.txt or data/technical_verbs.txt.")
            continue
        note = "" if r["via"] == "exact" else f" (form of {r['headword'].upper()})"
        print(f"{r['token']}{note}:")
        for e in r["entries"]:
            status = "APPROVED" if e["approved"] else "not approved"
            line = f"  {e['word']} ({POS_NAMES.get(e['pos'], e['pos'])}) - {status}"
            if e["forms"]:
                line += f"; forms: {', '.join(e['forms'])}"
            if e["meaning"]:
                line += f"; {'meaning' if e['approved'] else 'advice'}: {e['meaning']}"
            if e["alternatives"]:
                line += f"; use: {', '.join(e['alternatives'])}"
            print(line)
        if r["found"] and r["approved"] and not r.get("approved_form", True):
            print(f"  !! '{r['token']}' itself is not an approved form.")
    return 0


def cmd_suggest(args) -> int:
    d = Dictionary(args.data_dir) if args.data_dir else Dictionary()
    words = [k for k in d.search(args.prefix, limit=200)
             if any(e.approved for e in d.entries[k])
             and (not args.pos or any(e.approved and e.pos == args.pos for e in d.entries[k]))]
    print(", ".join(w.upper() for w in words[: args.limit]) or "(no approved words)")
    return 0


def cmd_rules(args) -> int:
    checker = Checker()
    rules = list(checker.rules.values())
    if args.ids:
        rules = [r for r in rules if r["id"] in args.ids]
    if args.manual:
        rules = [r for r in rules if r["check"] in ("manual", "partial")]
    if args.applies_to:
        rules = [r for r in rules if r["applies_to"] in ("all", args.applies_to)]
    if args.format == "json":
        _out(rules, "json")
        return 0
    for r in rules:
        print(f"Rule {r['id']} [{r['applies_to']}, {r['check']}]: {r['statement']}")
        if r.get("agent_hint"):
            print(f"    hint: {r['agent_hint']}")
    return 0


def cmd_extract(args) -> int:
    from .extract import main as extract_main
    argv = [args.pdf] + (["--out", args.out] if args.out else [])
    return extract_main(argv)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ste100", description="ASD-STE100 checker (offline)")
    ap.add_argument("--data-dir", default=None, help="folder with dictionary.json, rules.json and term lists")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="check one or more text/markdown files")
    c.add_argument("files", nargs="+", help="files to check, or - for stdin")
    c.add_argument("--mode", choices=MODES, default="auto",
                   help="procedure = 20-word limit and imperative checks; descriptive = 25-word limit; "
                        "auto = decide for each sentence (default)")
    c.add_argument("--format", choices=("text", "json"), default="text")
    c.add_argument("--info", action="store_true", help="also show info-level findings")
    c.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    c.add_argument("--verbose", action="store_true", help="JSON: include full text of rules to review")
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("lookup", help="look up words in the dictionary")
    l.add_argument("words", nargs="+")
    l.add_argument("--format", choices=("text", "json"), default="text")
    l.set_defaults(func=cmd_lookup)

    g = sub.add_parser("suggest", help="list approved words that start with a prefix")
    g.add_argument("prefix")
    g.add_argument("--pos", choices=sorted(POS_NAMES), default=None)
    g.add_argument("--limit", type=int, default=40)
    g.set_defaults(func=cmd_suggest)

    r = sub.add_parser("rules", help="show writing rules")
    r.add_argument("ids", nargs="*", help="rule ids, for example 3.6 5.1 GR-6")
    r.add_argument("--manual", action="store_true", help="only rules the script can not fully check")
    r.add_argument("--applies-to", choices=("procedure", "descriptive", "safety"), default=None)
    r.add_argument("--format", choices=("text", "json"), default="text")
    r.set_defaults(func=cmd_rules)

    e = sub.add_parser("extract", help="rebuild data/dictionary.json from the STE PDF (needs pdfplumber)")
    e.add_argument("pdf")
    e.add_argument("--out", default=None)
    e.set_defaults(func=cmd_extract)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    if args.cmd in ("lookup", "suggest", "rules", "extract") and not hasattr(args, "data_dir"):
        args.data_dir = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
