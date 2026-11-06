---
agent: STE100 Writer
description: Rewrite the selected text or file in ASD-STE100 Simplified Technical English and verify it with the checker.
argument-hint: Optional - procedure, descriptive or auto
---

Rewrite the text below (or the current selection or file) in ASD-STE100 Simplified Technical English.

Mode: ${input:mode:procedure, descriptive or auto}

Follow the workflow in your agent instructions:
1. Look up every word you are not sure about with `python -m ste100 lookup`.
2. Save the draft, then run `python -m ste100 check <file> --mode <mode> --format json`.
3. Fix all errors. Decide about each warning. Repeat until there are no errors.
4. Review the rules in `manual_review` yourself.

Keep all technical meaning, values, units, identifiers and references exactly the same.

Text:

${selection}
