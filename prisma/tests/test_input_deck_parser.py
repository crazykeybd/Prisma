from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.server import search_generic_input_file as api_search_input_file
from common.input_deck_parser import (
    detect_input_format,
    modify_input_file,
    parse_input_data,
    parse_input_file,
    parse_input_text,
    search_input_file,
)


PRISMA_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DECK = PRISMA_ROOT / "inputs" / "CheckSGInput.txt"


def positional_line(values: str, description: str) -> str:
    return values.ljust(19) + description


def sample_text() -> str:
    return (
        positional_line("2", "SG数目-蒸发器和过热器总数")
        + "\r\n"
        + positional_line(
            "1 2 3 4",
            "系统中的编号-过热器钠侧，过热器水侧，蒸发器钠侧，蒸发器水侧",
        )
        + "\r\n"
        + positional_line("8", "壁面材料 !过热器 #1 2")
        + "\r\n"
        + "\r\n"
        + "\r\n"
    )


class PositionalInputDeckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prisma-input-")
        self.addCleanup(self.temp_dir.cleanup)
        self.deck = Path(self.temp_dir.name) / "CheckSGInput.txt"
        self.deck.write_bytes(sample_text().encode("gb18030"))

    def test_gb18030_crlf_and_three_trailing_blank_lines(self) -> None:
        parsed = parse_input_file(self.deck)
        self.assertNotIn("error", parsed)
        summary = parsed["summary"]
        self.assertEqual(summary["profile"], "positional_text")
        self.assertEqual(summary["encoding"], "GB18030")
        self.assertEqual(summary["newline"], "CRLF")
        self.assertEqual(summary["total_lines"], 6)
        self.assertEqual(summary["blank_lines"], 3)
        self.assertEqual(summary["total_records"], 3)
        self.assertEqual(summary["record_fields"], 6)
        self.assertEqual(summary["numeric_fields"], 6)
        self.assertEqual(summary["unparsed_lines"], 0)
        self.assertEqual(
            [entry["kind"] for entry in parsed["entries"][-3:]],
            ["blank", "blank", "blank"],
        )

    def test_auto_detection_and_explicit_format_override(self) -> None:
        data = self.deck.read_bytes()
        self.assertEqual(
            detect_input_format(
                data.decode("gb18030"), filename=self.deck.name
            ),
            "positional_text",
        )
        auto = parse_input_data(data, filename=self.deck.name)
        self.assertEqual(auto["summary"]["profile"], "positional_text")

        forced_relap = parse_input_data(
            data, filename=self.deck.name, input_format="relap5"
        )
        self.assertEqual(forced_relap["summary"]["profile"], "relap5")

        relap_text = (
            "=title\n"
            "1000101  1.0  2.0\n"
            ".RELAP END CARD\n"
        )
        forced_positional = parse_input_text(
            relap_text, filename="sample.i", input_format="positional_text"
        )
        self.assertEqual(forced_positional["summary"]["profile"], "positional_text")

    def test_multi_value_search_and_literal_bang_hash_descriptions(self) -> None:
        result = search_input_file(
            self.deck, "系统中的编号", input_format="positional_text"
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["count"], 4)
        self.assertEqual(
            [item["target"] for item in result["results"]],
            ["L2:1@2", "L2:2@2", "L2:3@2", "L2:4@2"],
        )
        self.assertEqual(
            [item["value"] for item in result["results"]], ["1", "2", "3", "4"]
        )

        desc = search_input_file(self.deck, "!过热器")
        self.assertTrue(desc["ok"], desc.get("error"))
        self.assertEqual(desc["count"], 1)
        self.assertEqual(desc["results"][0]["target"], "L3:1@3")
        self.assertIn("#1 2", desc["results"][0]["description"])

        exact_line = search_input_file(REFERENCE_DECK, "L3")
        self.assertTrue(exact_line["ok"], exact_line.get("error"))
        self.assertGreater(exact_line["count"], 0)
        self.assertTrue(
            all(item["line_no"] == 3 for item in exact_line["results"])
        )

        exact_target = search_input_file(REFERENCE_DECK, "L3:2@3")
        self.assertTrue(exact_target["ok"], exact_target.get("error"))
        self.assertEqual(exact_target["count"], 1)
        self.assertEqual(exact_target["results"][0]["target"], "L3:2@3")
        self.assertEqual(exact_target["results"][0]["value"], "100")

    def test_dry_run_does_not_change_bytes(self) -> None:
        before = self.deck.read_bytes()
        result = modify_input_file(
            self.deck,
            "L2:2@2",
            "9",
            expected_value="2",
            apply=False,
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["old_value"], "2")
        self.assertEqual(self.deck.read_bytes(), before)

    def test_safe_write_preserves_width_description_and_newlines(self) -> None:
        before_text = self.deck.read_bytes().decode("gb18030")
        before_parsed = parse_input_file(self.deck)
        before_description = before_parsed["entries"][1]["description"]

        result = modify_input_file(
            self.deck,
            "L2:2@2",
            "9",
            expected_value="2",
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["applied"])
        self.assertFalse(result["dry_run"])

        after_bytes = self.deck.read_bytes()
        after_text = after_bytes.decode("gb18030")
        after_parsed = parse_input_file(self.deck)
        self.assertEqual(after_parsed["summary"]["encoding"], "GB18030")
        self.assertEqual(after_parsed["summary"]["newline"], "CRLF")
        self.assertEqual(after_parsed["entries"][1]["fields"], ["1", "9", "3", "4"])
        self.assertEqual(after_parsed["entries"][1]["description"], before_description)
        self.assertEqual(
            [len(line) for line in before_text.split("\r\n")],
            [len(line) for line in after_text.split("\r\n")],
        )
        self.assertEqual(before_text.count("\r\n"), after_text.count("\r\n"))

    def test_longer_value_uses_inline_padding_without_moving_description(self) -> None:
        before = parse_input_file(self.deck)["entries"][0]
        replacement = "12345678901234567"
        result = modify_input_file(self.deck, "L1:1@1", replacement)
        self.assertTrue(result["ok"], result.get("error"))
        after = parse_input_file(self.deck)["entries"][0]
        self.assertEqual(after["fields"], [replacement])
        self.assertEqual(after["description"], before["description"])
        self.assertEqual(len(after["raw"]), len(before["raw"]))

    def test_numeric_field_rejects_non_numeric_value(self) -> None:
        before = self.deck.read_bytes()
        result = modify_input_file(self.deck, "L3:1@3", "not-a-number")
        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertIn("数值", result["error"])
        self.assertEqual(self.deck.read_bytes(), before)

    def test_reference_checksg_deck_parses_completely(self) -> None:
        parsed = parse_input_file(REFERENCE_DECK)
        self.assertNotIn("error", parsed)
        summary = parsed["summary"]
        self.assertEqual(summary["profile"], "positional_text")
        self.assertEqual(summary["encoding"], "GB18030")
        self.assertEqual(summary["newline"], "CRLF")
        self.assertEqual(summary["total_lines"], 67)
        self.assertEqual(summary["blank_lines"], 3)
        self.assertEqual(summary["total_records"], 64)
        self.assertEqual(summary["record_fields"], 74)
        self.assertEqual(summary["numeric_fields"], 74)
        self.assertEqual(summary["unparsed_lines"], 0)

        result = search_input_file(REFERENCE_DECK, "L64")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertGreater(result["count"], 0)
        self.assertTrue(all(item["line_no"] == 64 for item in result["results"]))


class GenericDispatcherTests(unittest.TestCase):
    def test_relap5_line_search_returns_all_fields_on_multi_value_line(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-relap-") as temp_dir:
            deck = Path(temp_dir) / "multi-value.i"
            fields = ["1.0", "2.0", "3.0", "4.0", "5.0", "6.0"]
            deck.write_bytes(
                (
                    "=title\n"
                    "0000100  new  stdy-st\n"
                    f"1000101  {'  '.join(fields)}\n"
                    ".RELAP END CARD\n"
                ).encode("ascii")
            )

            parsed = parse_input_file(deck)
            self.assertEqual(parsed["summary"]["profile"], "relap5")

            by_line = search_input_file(deck, "L3")
            self.assertTrue(by_line["ok"], by_line.get("error"))
            self.assertEqual(by_line["count"], len(fields))
            self.assertEqual(
                [item["target"] for item in by_line["results"]],
                [f"1000101:-:{index}@3" for index in range(1, len(fields) + 1)],
            )
            self.assertEqual(
                [item["value"] for item in by_line["results"]], fields
            )
            self.assertTrue(
                all(item["fields"] == fields for item in by_line["results"])
            )

            api_by_line = api_search_input_file(str(deck), "L3", 1000, True)
            self.assertTrue(api_by_line["ok"], api_by_line.get("error"))
            self.assertEqual(api_by_line["count"], len(fields))

            by_value = search_input_file(deck, "6.0")
            self.assertTrue(by_value["ok"], by_value.get("error"))
            self.assertEqual(by_value["count"], len(fields))
            self.assertEqual(
                [item["value"] for item in by_value["results"]], fields
            )

    def test_relap5_uses_generic_entry_points(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-relap-") as temp_dir:
            deck = Path(temp_dir) / "sample.i"
            deck.write_bytes(
                (
                    "=title\n"
                    "0000100  new  stdy-st\n"
                    "1000101  1.0  2.0\n"
                    ".RELAP END CARD\n"
                ).encode("ascii")
            )
            parsed = parse_input_file(deck)
            self.assertEqual(parsed["summary"]["profile"], "relap5")

            found = search_input_file(deck, "1000101")
            self.assertTrue(found["ok"], found.get("error"))
            target = next(
                item["target"]
                for item in found["results"]
                if item["field"] == 2
            )
            self.assertEqual(target, "1000101:-:2@3")

            result = modify_input_file(
                deck, target, "7.0", expected_value="2.0"
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["profile"], "relap5")
            updated = parse_input_file(deck)
            card = next(
                entry
                for entry in updated["entries"]
                if entry.get("card_id") == "1000101"
            )
            self.assertEqual(card["fields"], ["1.0", "7.0"])

    def test_api_helper_searches_positional_deck(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-api-") as temp_dir:
            deck = Path(temp_dir) / "CheckSGInput.txt"
            deck.write_bytes(sample_text().encode("gb18030"))
            result = api_search_input_file(
                str(deck), "系统中的编号", 100, True, "positional_text"
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["summary"]["profile"], "positional_text")
            self.assertEqual(result["count"], 4)
            self.assertEqual(result["results"][0]["target"], "L2:1@2")


if __name__ == "__main__":
    unittest.main()
