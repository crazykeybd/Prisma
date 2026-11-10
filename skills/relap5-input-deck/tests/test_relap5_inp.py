from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
PRISMA_ROOT = REPO_ROOT / "prisma"
CLI = SKILL_ROOT / "scripts" / "relap5_inp.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PRISMA_ROOT))

from common.relap_input_parser import (  # noqa: E402
    apply_relap_input_edit,
    modify_relap_input_file,
    parse_relap_input_data,
    parse_relap_input_file,
    parse_relap_input_text,
    search_relap_input,
)


def write_deck(path: Path, text: str, *, encoding: str = "utf-8", newline: str = "\n") -> None:
    path.write_bytes(text.replace("\n", newline).encode(encoding))


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PRISMA_ROOT"] = str(PRISMA_ROOT)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(cwd or SKILL_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class RelapParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relap-skill-"))
        self.deck = self.tmp / "mini.i"
        shutil.copy2(PRISMA_ROOT / "inputs" / "mini.i", self.deck)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mini_deck_parse(self) -> None:
        parsed = parse_relap_input_file(self.deck)
        self.assertNotIn("error", parsed)
        summary = parsed["summary"]
        self.assertEqual(summary["encoding"], "ASCII")
        self.assertEqual(summary["newline"], "CRLF")
        self.assertEqual(summary["total_cards"], 18)
        self.assertEqual(summary["effective_cards"], 18)
        self.assertEqual(summary["unparsed_lines"], 0)
        self.assertTrue(summary["has_end_card"])
        self.assertTrue(summary["has_hydrodynamic"])
        self.assertEqual(summary["title"], "mini pipe test (relap5 real-run example)")

    def test_spaces_commas_and_quoted_first_field(self) -> None:
        text = (
            "=title\n"
            "0000100,new,stdy-st\n"
            "0000101 'my name', 1.0\n"
            "1000101,1.0,2.0,3.0\n"
            ".RELAP END CARD\n"
        )
        parsed = parse_relap_input_text(text)
        cards = [e for e in parsed["entries"] if e["kind"] == "card"]
        self.assertEqual(cards[0]["fields"], ["new", "stdy-st"])
        self.assertEqual(cards[1]["fields"], ["my name", "1.0"])
        self.assertEqual(cards[1]["target_keyword"], "my name")
        self.assertEqual(cards[2]["target_keyword"], "-")
        self.assertEqual(parsed["summary"]["unparsed_lines"], 0)

    def test_comments_and_implicit_exponents(self) -> None:
        text = (
            "=title\n"
            "* entire comment\n"
            "$ second comment\n"
            "0000100  new  stdy-st  * trailing comment\n"
            "1000101  1.0-3  0.1245+2\n"
            ".RELAP END CARD\n"
        )
        parsed = parse_relap_input_text(text)
        self.assertEqual(parsed["summary"]["comment_lines"], 3)
        self.assertEqual(parsed["summary"]["unparsed_lines"], 0)
        numeric_card = next(e for e in parsed["entries"] if e.get("card_id") == "1000101")
        titled_card = next(e for e in parsed["entries"] if e.get("card_id") == "0000100")
        self.assertEqual(numeric_card["fields"], ["1.0-3", "0.1245+2"])
        self.assertEqual(titled_card["comment"], "* trailing comment")

    def test_continuation_target_and_edit(self) -> None:
        path = self.tmp / "continuation.i"
        original = (
            "=continuation\n"
            "1000101  1.0  2.0\n"
            "+  3.0  4.0\n"
            ".RELAP END CARD\n"
        )
        write_deck(path, original)
        hits = search_relap_input(path.read_text(encoding="ascii"), "1000101")
        target = next(hit["target"] for hit in hits if hit["field"] == 3)
        self.assertEqual(target, "1000101:-:3@2")
        result = modify_relap_input_file(path, target, "7.0", expected_value="3.0")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["applied"])
        self.assertEqual(path.read_text(encoding="ascii").splitlines()[2], "+  7.0  4.0")
        parsed = parse_relap_input_file(path)
        card = next(e for e in parsed["entries"] if e.get("card_id") == "1000101")
        self.assertEqual(card["fields"], ["1.0", "2.0", "7.0", "4.0"])

    def test_duplicate_replacement_and_deletion(self) -> None:
        text = (
            "=dup\n"
            "100  first  1.0\n"
            "100  second  2.0\n"
            "200  gone\n"
            "300  gone\n"
            "300\n"
            ".RELAP END CARD\n"
        )
        parsed = parse_relap_input_text(text)
        cards = {e.get("card_id"): e for e in parsed["entries"] if e["kind"] == "card"}
        self.assertTrue(cards["100"]["effective"])
        self.assertEqual(str(cards["100"]["line_no"]), "3")
        self.assertTrue(next(e for e in parsed["entries"] if e.get("line_no") == 2 and e.get("kind") == "card")["overridden"])
        self.assertFalse(cards["300"]["effective"])
        self.assertTrue(next(e for e in parsed["entries"] if e.get("line_no") == 5 and e.get("kind") == "card")["deleted"])
        self.assertTrue(next(e for e in parsed["entries"] if e.get("line_no") == 6 and e.get("kind") == "card")["deletion"])
        hits = search_relap_input(text, "100")
        self.assertTrue(hits)
        self.assertTrue(all(hit["line_no"] == 3 for hit in hits))
        self.assertFalse(search_relap_input(text, "300"))

    def test_leading_zeroes_are_equivalent(self) -> None:
        text = "=zeroes\n100  new  stdy-st\n.RELAP END CARD\n"
        path = self.tmp / "zeroes.i"
        write_deck(path, text)
        result = modify_relap_input_file(path, "0000100:new:2@2", "transnt")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIn("100  new  transnt", path.read_text(encoding="ascii"))

    def test_last_title_wins(self) -> None:
        parsed = parse_relap_input_text("=first\n=second title\n.RELAP END CARD\n")
        self.assertEqual(parsed["summary"]["title"], "second title")
        self.assertEqual(parsed["summary"]["kinds"]["title"], 2)

    def test_last_card_wins_even_with_continuation(self) -> None:
        text = (
            "=last\n"
            "1000101  1.0\n"
            "+  2.0\n"
            "1000101  9.0  9.0\n"
            ".RELAP END CARD\n"
        )
        parsed = parse_relap_input_text(text)
        card = next(e for e in parsed["entries"] if e.get("card_id") == "1000101" and e.get("kind") == "card" and e.get("effective"))
        self.assertEqual(card["line_no"], 4)
        self.assertEqual(card["fields"], ["9.0", "9.0"])
        hits = search_relap_input(text, "1000101")
        self.assertTrue(hits)
        self.assertTrue(all(hit["line_no"] == 4 for hit in hits))

    def test_invalid_continuation_is_unparsed(self) -> None:
        parsed = parse_relap_input_text(
            "=bad\n1000101  1.0\n\n+  2.0\n.RELAP END CARD\n"
        )
        self.assertEqual(parsed["summary"]["unparsed_lines"], 1)
        self.assertTrue(any("续卡" in e.get("keyword", "") for e in parsed["entries"] if e["kind"] == "unparsed"))

    def test_eighty_column_limit(self) -> None:
        parsed = parse_relap_input_text(
            "=width\n1000101  1.0\n"
            + "1000102  " + "1.0 " * 20 + "\n"
            + ".RELAP END CARD\n"
        )
        self.assertGreaterEqual(parsed["summary"]["ignored_columns"], 1)

    def test_encoding_bom_and_crlf_preserved(self) -> None:
        path = self.tmp / "bom.i"
        text = "=标题\n0000100  new  stdy-st\n.RELAP END CARD\n"
        path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
        before = path.read_bytes()
        result = modify_relap_input_file(path, "0000100:new:2@2", "transnt")
        self.assertTrue(result["ok"], result.get("error"))
        after = path.read_bytes()
        self.assertTrue(after.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(after.count(b"\r\n"), before.count(b"\r\n"))
        self.assertEqual(parse_relap_input_file(path)["summary"]["encoding"], "UTF-8 BOM")

    def test_dry_run_does_not_change_bytes(self) -> None:
        before = self.deck.read_bytes()
        result = modify_relap_input_file(
            self.deck, "0000100:new:2@2", "transnt", apply=False
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertFalse(result["applied"])
        self.assertTrue(result["dry_run"])
        self.assertIn("transnt", result["diff"])
        self.assertEqual(self.deck.read_bytes(), before)

    def test_expected_mismatch_does_not_write(self) -> None:
        before = self.deck.read_bytes()
        result = modify_relap_input_file(
            self.deck, "0000100:new:2@2", "transnt", expected_value="wrong"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(self.deck.read_bytes(), before)

    def test_numeric_field_rejects_text(self) -> None:
        before = self.deck.read_bytes()
        result = modify_relap_input_file(self.deck, "1000101:-:1@10", "not-a-number")
        self.assertFalse(result["ok"])
        self.assertIn("数值", result["error"])
        self.assertEqual(self.deck.read_bytes(), before)

    def test_only_target_field_changes(self) -> None:
        before = self.deck.read_text(encoding="ascii", newline="")
        result = modify_relap_input_file(self.deck, "0000100:new:2@2", "transnt")
        self.assertTrue(result["ok"], result.get("error"))
        after = self.deck.read_text(encoding="ascii", newline="")
        self.assertEqual(before.replace("stdy-st", "transnt", 1), after)
        self.assertEqual(result["line_no"], 2)
        self.assertEqual(len(result["preview"]), 1)

    def test_quote_style_preserved(self) -> None:
        path = self.tmp / "quote.i"
        write_deck(path, "=quote\n101  vol  \"old name\"\n.RELAP END CARD\n")
        result = modify_relap_input_file(path, "101:vol:2@2", "new \"quoted\" name")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertIn('"new ""quoted"" name"', path.read_text(encoding="utf-8"))

    def test_deck_over_80_columns_rejected(self) -> None:
        path = self.tmp / "long.i"
        write_deck(path, "=long\n101  vol  old\n.RELAP END CARD\n")
        result = modify_relap_input_file(path, "101:vol:2@2", "x" * 76)
        self.assertFalse(result["ok"])
        self.assertIn("80", result["error"])

    def test_apply_relap_input_edit_alias(self) -> None:
        result = apply_relap_input_edit(self.deck, "0000100:new:2@2", "transnt")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["applied"])

    def test_parse_data_bytes(self) -> None:
        data = b"=title\r\n0000100  new  stdy-st\r\n.RELAP END CARD\r\n"
        parsed = parse_relap_input_data(data, filename="x.i")
        self.assertEqual(parsed["summary"]["filename"], "x.i")
        self.assertEqual(parsed["summary"]["encoding"], "ASCII")
        self.assertEqual(parsed["summary"]["newline"], "CRLF")


class RelapCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relap-cli-"))
        self.deck = self.tmp / "mini.i"
        shutil.copy2(PRISMA_ROOT / "inputs" / "mini.i", self.deck)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_json(self) -> None:
        proc = run_cli("parse", str(self.deck), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["summary"]["total_cards"], 18)

    def test_search_output_and_json(self) -> None:
        proc = run_cli("search", str(self.deck), "100 new", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload)
        self.assertEqual(payload[0]["target"], "0000100:new:1@2")
        self.assertTrue(any(item["field"] == 2 and item["value"] == "stdy-st" for item in payload))

        plain = run_cli("search", str(self.deck), "stdy-st")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn("0000100:new:2@2", plain.stdout)

    def test_modify_default_dry_run(self) -> None:
        before = self.deck.read_bytes()
        proc = run_cli(
            "modify", str(self.deck), "--target", "0000100:new:2@2",
            "--value", "transnt", "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["applied"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(self.deck.read_bytes(), before)

    def test_modify_apply(self) -> None:
        proc = run_cli(
            "modify", str(self.deck), "--target", "0000100:new:2@2",
            "--value", "transnt", "--expected", "stdy-st", "--apply", "--json",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["applied"])
        self.assertIn("transnt", self.deck.read_text(encoding="ascii"))

    def test_modify_failure_is_nonzero(self) -> None:
        before = self.deck.read_bytes()
        proc = run_cli(
            "modify", str(self.deck), "--target", "0000100:new:2@2",
            "--value", "transnt", "--expected", "wrong", "--json",
        )
        self.assertNotEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.deck.read_bytes(), before)

    def test_parser_found_from_prisma_root(self) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PRISMA_ROOT"] = str(PRISMA_ROOT)
        proc = subprocess.run(
            [sys.executable, str(CLI), "parse", str(self.deck), "--json"],
            cwd=str(self.tmp), env=env, text=True, encoding="utf-8",
            capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["summary"]["effective_cards"], 18)


class PrismaFolderPrepIntegrationTests(unittest.TestCase):
    def test_existing_support_file_is_not_copied_onto_itself(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="relap-folder-prep-"))
        try:
            case_name = "RELAP-SUPPORT-TEST"
            case_dir = tmp / "test_cases" / case_name
            case_dir.mkdir(parents=True)
            (tmp / "inputs").mkdir()
            shutil.copy2(PRISMA_ROOT / "inputs" / "mini.i", tmp / "inputs" / "mini.i")
            shutil.copy2(PRISMA_ROOT / "inputs" / "mini.i", case_dir / "input.txt")
            support = case_dir / "tpfh2o"
            support.write_bytes(b"support-file-is-already-in-run-dir")

            with patch("keywords.keyword_lib.ROOT", tmp), patch(
                "keywords.keyword_lib.init_log", return_value=MagicMock()
            ):
                from keywords.keyword_lib import KeyPrisma

                ok = KeyPrisma.folder_prep(
                    "CASE-1", case_name, "mini.i", "relap5.exe"
                )

            self.assertTrue(ok)
            self.assertEqual(support.read_bytes(), b"support-file-is-already-in-run-dir")

        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PrismaModifyInpIntegrationTests(unittest.TestCase):
    def test_keyprisma_modify_inp_writes_temporary_case(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="relap-prisma-"))
        try:
            case_name = "RELAP-SKILL-TEST"
            case_dir = tmp / "test_cases" / case_name
            case_dir.mkdir(parents=True)
            deck = case_dir / "input.txt"
            shutil.copy2(PRISMA_ROOT / "inputs" / "mini.i", deck)

            buffer = io.StringIO()
            with patch("keywords.keyword_lib.ROOT", tmp), patch(
                "keywords.keyword_lib.init_log", return_value=MagicMock()
            ), redirect_stdout(buffer):
                from keywords.keyword_lib import KeyPrisma

                ok = KeyPrisma.modify_inp(
                    "CASE-1", str(case_name), "input.txt", "0000100:new:2@2", "transnt"
                )
            self.assertTrue(ok)
            self.assertIn("transnt", deck.read_text(encoding="ascii"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
