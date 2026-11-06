---
agent: STE100 Writer
description: Check a file against ASD-STE100 and explain each problem, without changing the file.
---

Check the current file (${file}) against ASD-STE100. Do not edit it.

1. Run `python -m ste100 check "${file}" --mode auto --format json --info`.
2. Group the findings by line. For each finding, give the rule, the problem in plain words,
   and a corrected STE version of the sentence.
3. List the unknown words, and say which ones look like real technical nouns for this
   subject field and which ones must be replaced.
4. List the rules from `manual_review` where you see a possible problem, and why.
