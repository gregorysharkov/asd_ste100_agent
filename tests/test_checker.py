"""Tests for the STE checker. Standard library only:

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ste100.checker import Checker, parse_document, tokenize, word_count  # noqa: E402
from ste100.dictionary import Dictionary  # noqa: E402

D = Dictionary()
C = Checker(D)


def rules_found(text, mode="auto", severity=("error", "warning")):
    report = C.check(text, mode)
    return {(i["rule"], i.get("token", "").lower()) for i in report["issues"] if i["severity"] in severity}


def rule_ids(text, mode="auto", severity=("error", "warning")):
    return {r for r, _ in rules_found(text, mode, severity)}


class DictionaryTest(unittest.TestCase):
    def test_loaded(self):
        self.assertTrue(D.loaded)
        self.assertGreater(len(D.entries), 1900)

    def test_approved_word(self):
        r = D.lookup("adjust")
        self.assertTrue(r.is_approved)
        self.assertIn("ADJUSTED", r.approved_entries[0].forms)

    def test_not_approved_with_alternative(self):
        r = D.lookup("utilize")
        self.assertFalse(r.is_approved)
        self.assertIn("USE (v)", r.entries[0].alternatives)

    def test_inflection(self):
        r = D.lookup("installed")
        self.assertTrue(r.is_approved)
        self.assertTrue(D.is_approved_form("installed", r))
        r = D.lookup("installing")
        self.assertTrue(r.found)
        self.assertFalse(D.is_approved_form("installing", r))

    def test_irregular(self):
        r = D.lookup("made")
        self.assertEqual(r.headword, "make")
        self.assertTrue(D.is_approved_form("made", r))
        r = D.lookup("been")
        self.assertFalse(D.is_approved_form("been", r))  # BE: IS, WAS, ARE, WERE only

    def test_comparative_only_for_adjectives(self):
        self.assertTrue(D.is_approved_form("larger", D.lookup("larger")))
        self.assertNotEqual(D.lookup("pointer").headword, "point")

    def test_multiword_entry(self):
        self.assertTrue(D.lookup("make sure").is_approved)
        self.assertFalse(D.lookup("according to").is_approved)

    def test_technical_noun_from_alternatives(self):
        r = D.lookup("support")
        self.assertTrue(any(e.pos == "tn" for e in r.approved_entries))


class DocumentTest(unittest.TestCase):
    def test_markdown_structure(self):
        text = "# Title\n\n1. Remove the cover.\n2. Install the cover.\n\nThe unit is heavy. It is gray.\n\n```\ncode here; ok\n```\n"
        ss = parse_document(text)
        kinds = [s.kind for s in ss]
        self.assertEqual(kinds, ["heading", "list", "list", "body", "body"])
        self.assertEqual(ss[1].line, 3)

    def test_word_count_rules(self):
        # parentheses (8.5), numbers with units (8.6), hyphenated words (8.7) count as one
        toks = tokenize('Install the bolt (1) with a 25 mm long-life "LOCK" washer.')
        self.assertEqual(word_count(toks), 10)  # Install the bolt (1) with a [25 mm] long-life "LOCK" washer

    def test_safety_block(self):
        ss = parse_document("WARNING: Do not touch the hot unit. Injury can occur.")
        self.assertEqual(ss[0].kind, "safety")
        self.assertEqual(ss[0].safety_word, "WARNING")


class RuleTest(unittest.TestCase):
    def test_not_approved_word(self):
        self.assertIn(("1.1", "utilize"), rules_found("Utilize the tool."))
        self.assertIn(("1.1", "ensure"), rules_found("Ensure that the seal is serviceable."))

    def test_part_of_speech(self):
        self.assertIn(("1.2", "check"), rules_found("Check the seal for damage."))
        self.assertNotIn("1.2", rule_ids("Do a check of the seal."))

    def test_verb_form(self):
        self.assertIn("3.5", rule_ids("Before installing the unit, clean the surface."))
        self.assertIn("3.2", rule_ids("The technician has removed the unit."))
        self.assertIn("3.2", rule_ids("The pump is operating."))

    def test_ing_technical_noun_is_not_an_error(self):
        report = C.check("Install the bearing in the housing.")
        errors = [i for i in report["issues"] if i["severity"] == "error"]
        self.assertEqual(errors, [])

    def test_passive_with_agent(self):
        self.assertIn("3.6", rule_ids("The unit is removed by the operator."))

    def test_phrasal_verb(self):
        self.assertIn("9.3", rule_ids("Carry out the test."))

    def test_semicolon_contraction_latin(self):
        ids = rule_ids("Remove the cover; it isn't necessary, e.g. for the test.")
        self.assertTrue({"8.1", "4.2", "GR-6"} <= ids)

    def test_procedure_length(self):
        long = "Remove " + " ".join(["the cover and the seal"] * 5) + "."
        self.assertIn("5.1", rule_ids(long, mode="procedure"))

    def test_descriptive_length_and_paragraph(self):
        para = " ".join(["The unit is heavy."] * 7)
        self.assertIn("6.6", rule_ids(para, mode="descriptive"))

    def test_condition_comma(self):
        self.assertIn("5.4", rule_ids("If the light is on open the valve.", mode="procedure"))
        self.assertNotIn("5.4", rule_ids("If the light is on, open the door.", mode="procedure"))

    def test_safety_instruction(self):
        self.assertIn("7.2", rule_ids("WARNING: The fluid is hot."))
        self.assertIn("7.3", rule_ids("WARNING: Do not touch the hot unit."))
        self.assertNotIn("7.2", rule_ids("WARNING: Do not touch the hot unit. If you touch it, injury can occur."))

    def test_note_with_instruction(self):
        self.assertIn("5.5", rule_ids("NOTE: Discard the seal."))

    def test_british_spelling(self):
        self.assertIn("1.14", rule_ids("The colour of the unit is gray."))

    def test_clean_text_passes(self):
        text = (
            "1. Remove the four bolts (2) from the cover.\n"
            "2. Make sure that the seal is not damaged.\n"
            "3. If the light comes on, stop the test.\n\n"
            "WARNING: DO NOT TOUCH THE HOT SURFACE. IF YOU DO NOT OBEY THIS PRECAUTION, INJURY CAN OCCUR.\n"
        )
        report = C.check(text)
        self.assertTrue(report["summary"]["passed"], report["issues"])

    def test_technical_names_file(self):
        report = C.check("Install the fuel pump.")  # "fuel pump" is in data/technical_names.txt
        self.assertEqual([i for i in report["issues"] if i["severity"] != "info"], [])


class CliTest(unittest.TestCase):
    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-m", "ste100", *args], cwd=ROOT, input=stdin,
                              capture_output=True, text=True)

    def test_check_json_and_exit_code(self):
        p = self.run_cli("check", "-", "--format", "json", stdin="Utilize the tool.")
        self.assertEqual(p.returncode, 1)
        data = json.loads(p.stdout)
        self.assertFalse(data["summary"]["passed"])
        p = self.run_cli("check", "-", stdin="Remove the cover.")
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_lookup(self):
        p = self.run_cli("lookup", "verify")
        self.assertIn("MAKE SURE", p.stdout)

    def test_rules(self):
        p = self.run_cli("rules", "5.1")
        self.assertIn("20 words", p.stdout)


if __name__ == "__main__":
    unittest.main()
