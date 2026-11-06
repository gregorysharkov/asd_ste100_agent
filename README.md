# ASD-STE100 agent for VS Code Copilot

A Copilot custom agent that writes in **ASD-STE100 Simplified Technical English (Issue 9)**,
plus an offline checker that it uses to verify every draft.

The STE dictionary (about 2,000 entries) and the writing rules are too large to put in a
prompt. So the agent does not memorize them. It **looks words up** and **runs the checker**,
and only the short results go into its context.

```
 you ──► STE100 Writer agent ──► python -m ste100 lookup / check ──► data/dictionary.json
                ▲                                                    data/rules.json
                └────────── errors, warnings, alternatives ◄──────── data/technical_*.txt
```

Everything runs **offline with the Python standard library** (Python 3.8+). No pip, no internet, no MCP server.

## What is in the repo

| Path | What it is |
|---|---|
| `.github/agents/ste100-writer.agent.md` | The custom agent: workflow, commands, how to react to each finding |
| `.github/prompts/ste-rewrite.prompt.md` | `/ste-rewrite` – rewrite a selection or file in STE |
| `.github/prompts/ste-check.prompt.md` | `/ste-check` – review a file, explain the problems, no edits |
| `.vscode/settings.json` | Auto-approves only the `python -m ste100 …` terminal commands |
| `ste100/` | The checker package (standard library only) |
| `data/dictionary.json` | The STE dictionary, extracted from Part 2 of the standard |
| `data/rules.json` | All writing rules (1.1–9.4, GR-1–GR-8) with check level and hints |
| `data/technical_names.txt` | **Your** technical nouns (rules 1.5, 1.8, 1.11) |
| `data/technical_verbs.txt` | **Your** technical verbs (rule 1.12) |
| `tests/` | Unit tests (`python -m unittest discover -s tests`) |

## Set up on the offline machine

1. Copy or clone the repository. Nothing to install.
2. Open the folder in VS Code. Copilot finds the agent in `.github/agents/` automatically.
3. In Copilot Chat, select **STE100 Writer** in the agent list.
4. Check that Python works: `python -m ste100 lookup verify`.
5. Replace the examples in `data/technical_names.txt` and `data/technical_verbs.txt` with
   the approved terms of your company or subject field. This is the most important
   setup step: STE expects each project to define its own technical nouns.

If the auto-approve setting name in `.vscode/settings.json` is different in your VS Code
version, search for "terminal auto approve" in Settings. Without it, Copilot asks before each
check, which is safe but slower.

## The commands

```bash
python -m ste100 check draft.md --mode procedure          # human-readable report
python -m ste100 check draft.md --mode auto --format json # what the agent reads
python -m ste100 lookup ensure installing housing         # entries, forms, alternatives
python -m ste100 suggest rem --pos v                      # approved verbs starting with "rem"
python -m ste100 rules 3.5 5.1                            # rule text and hints
python -m ste100 rules --manual --applies-to procedure    # rules the agent must review itself
```

`check` exits with 0 when there are no errors and 1 when there are errors
(`--strict` also fails on warnings), so you can also use it in CI or a pre-commit hook.

Example output:

```
draft.md:3: ERROR [rule 1.1] "Utilize" is not approved in STE.  -> use: USE (v)
draft.md:4: ERROR [rule 3.5] "installing" is not an approved form of INSTALL. ...
draft.md:5: ERROR [rule 1.2] "Check" looks like a verb here, but CHECK is approved only as n.  -> use: MAKE SURE (v), MEASURE (v)
draft.md:8: ERROR [rule 3.2] "has been" is a perfect tense. Use the simple past or simple present tense.
```

## What the checker checks

Each rule in `data/rules.json` has a check level:

* **auto** – the script checks it fully: approved words (1.1), verb and adjective forms (1.4, 3.1),
  "-ing" forms (3.5), sentence length in procedures and descriptions (5.1, 6.3), paragraph length (6.6),
  semicolons (8.1), word count rules (8.4–8.7), Latin abbreviations (GR-6).
* **partial** – the script finds the common violations, the agent reviews the rest: part of speech (1.2),
  multi-word nouns (2.1), tenses and auxiliaries (3.2, 3.4), passive voice (3.6), contractions (4.2),
  one instruction per sentence (5.2), imperative (5.3), condition + comma (5.4), notes (5.5),
  safety instructions (7.1–7.3), phrasal verbs (9.3), American spelling (1.14).
* **manual** – the script can not check it (approved meaning 1.3, one topic per paragraph 6.5, …).
  Every report lists these under `manual_review`, and the agent reviews them itself.

Severity: **error** = clear violation, **warning** = probable violation to confirm,
**info** = something to look at (hidden unless `--info`).

### How accurate it is

Measured on the standard's own examples (every dictionary entry has an STE example and a non-STE example):

| | Sentences | Flagged with at least one error |
|---|---|---|
| Approved STE examples | 2,981 | **4.1 %** (false alarms) |
| Non-STE examples | 1,939 | **86.6 %** (caught) |

Most false alarms are words that STE expects each project to define as technical nouns
("port", "upper drag strut"). Add those to `data/technical_names.txt` and they go away.
Most misses are non-STE examples that use approved words but a non-STE sentence structure;
the agent's manual review covers those.

## Rebuild the dictionary (new issue of the standard)

This is the only step that needs a third-party package, and you do it once, on any machine:

```bash
pip install pdfplumber
python -m ste100 extract ASD-STE100_ISSUE9.pdf   # writes data/dictionary.json
python -m unittest discover -s tests
```

The extractor reads the 4-column table of Part 2 by column position: UPPERCASE words are
approved, lowercase words are not, and column 2 gives meanings and approved alternatives.
Approved verb and adjective forms come from column 1 ("ABSORB (v), ABSORBS, ABSORBED, ABSORBED").
Technical nouns that the dictionary names as alternatives ("SUPPORT (TN)") are also accepted.

## Copyright

ASD-STE100 is © ASD. `data/dictionary.json` and the rule statements in `data/rules.json`
are derived from the standard. Keep this repository private, or remove those files before
you publish it. The PDF itself is in `.gitignore`.
