from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.server import search_generic_input_file as api_search_input_file
from common.input_deck_parser import (
    detect_input_format,
    modify_input_fields,
    modify_input_file,
    parse_field_edit_lines,
    parse_input_data,
    parse_input_file,
    parse_input_text,
    search_input_file,
)


PRISMA_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DECK = PRISMA_ROOT / "inputs" / "CheckSGInput.txt"
LOCUST_DECK = (
    PRISMA_ROOT / "software-library" / "locust" / "bin" / "system1.i"
)

JSON_DECK_TEXT = """{
  "enabled": true,
  "nothing": null,
  "points": [
    {"x": 1.0, "y": [2, 3]},
    {"x": 4.0, "y": [5, 6]}
  ],
  "matrix": [[1, 2], [3, 4]]
}
"""


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

    def test_parse_field_edit_lines_pairs_and_validates_rows(self) -> None:
        self.assertEqual(
            parse_field_edit_lines(
                " L2:1@2 \r\nL2:2@2\r\n",
                "8\r\n9.5",
            ),
            [("L2:1@2", "8"), ("L2:2@2", "9.5")],
        )
        invalid_cases = [
            ("L2:1@2\nL2:2@2", "8"),
            ("", "8"),
            ("L2:1@2\n\nL2:2@2", "8\n9\n10"),
            ("L2:1@2\nL2:2@2", "8\n"),
            ("L2:1@2\nL2:1@2", "8\n9"),
        ]
        for targets, values in invalid_cases:
            with self.subTest(targets=targets, values=values):
                with self.assertRaises(ValueError):
                    parse_field_edit_lines(targets, values)

    def test_multiple_positional_fields_are_modified_atomically(self) -> None:
        dry_run_bytes = self.deck.read_bytes()
        dry_run = modify_input_fields(
            self.deck,
            [("L2:1@2", "8"), ("L2:2@2", "9")],
            apply=False,
        )
        self.assertTrue(dry_run["ok"], dry_run.get("error"))
        self.assertTrue(dry_run["dry_run"])
        self.assertEqual(dry_run["count"], 2)
        self.assertEqual(self.deck.read_bytes(), dry_run_bytes)

        result = modify_input_fields(
            self.deck,
            [("L2:1@2", "8"), ("L2:2@2", "9")],
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["applied"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            [item["target"] for item in result["results"]],
            ["L2:1@2", "L2:2@2"],
        )
        fields = parse_input_file(self.deck)["entries"][1]["fields"]
        self.assertEqual(fields, ["8", "9", "3", "4"])

    def test_batch_failure_leaves_original_file_unchanged(self) -> None:
        before = self.deck.read_bytes()
        result = modify_input_fields(
            self.deck,
            [("L2:1@2", "8"), ("L99:1@99", "1")],
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertIn("第 2 个变量修改失败", result["error"])
        self.assertEqual(self.deck.read_bytes(), before)
        self.assertEqual(list(self.deck.parent.glob(".CheckSGInput.txt.prisma-edit-*.txt")), [])

    def test_single_edit_uses_legacy_path(self) -> None:
        result = modify_input_fields(self.deck, [("L2:2@2", "9")])
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["applied"])
        self.assertNotIn("count", result)
        self.assertEqual(
            parse_input_file(self.deck)["entries"][1]["fields"],
            ["1", "9", "3", "4"],
        )


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

    def test_relap5_continuation_fields_keep_matrix_physical_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-relap-") as temp_dir:
            deck = Path(temp_dir) / "continuation.i"
            deck.write_bytes(
                (
                    "=title\n"
                    "0000100  new  stdy-st\n"
                    "1000101  1.0  2.0\n"
                    "+ 3.0 4.0\n"
                    "+ 5.0\n"
                    ".RELAP END CARD\n"
                ).encode("ascii")
            )

            parsed = parse_input_file(deck)
            card = next(
                entry
                for entry in parsed["entries"]
                if entry.get("kind") == "card" and entry.get("card_id") == "1000101"
            )
            expected_fields = ["1.0", "2.0", "3.0", "4.0", "5.0"]
            self.assertEqual(card["fields"], expected_fields)
            self.assertEqual(card["field_line_nos"], [3, 3, 4, 4, 5])

            result = search_input_file(deck, "L3")
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(
                [item["value"] for item in result["results"]], expected_fields
            )
            self.assertEqual(
                [item["line_no"] for item in result["results"]], [3, 3, 3, 3, 3]
            )
            self.assertEqual(
                [item["field_line_no"] for item in result["results"]],
                [3, 3, 4, 4, 5],
            )
            self.assertTrue(
                all(item["fields"] == expected_fields for item in result["results"])
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

    def test_multiple_relap5_fields_are_modified_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-relap-") as temp_dir:
            deck = Path(temp_dir) / "sample.i"
            deck.write_bytes(
                (
                    "=title\n"
                    "0000100  new  stdy-st\n"
                    "1000101  1.0  2.0  3.0\n"
                    ".RELAP END CARD\n"
                ).encode("ascii")
            )
            result = modify_input_fields(
                deck,
                [("1000101:-:1@3", "7.0"), ("1000101:-:2@3", "8.0")],
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["count"], 2)
            card = next(
                entry
                for entry in parse_input_file(deck)["entries"]
                if entry.get("card_id") == "1000101"
            )
            self.assertEqual(card["fields"], ["7.0", "8.0", "3.0"])

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


class StructuredAndJsonInputTests(unittest.TestCase):
    def test_locust_system_file_flattens_matrix_by_physical_row(self) -> None:
        text = LOCUST_DECK.read_text(encoding="utf-8")
        self.assertEqual(
            detect_input_format(text, filename=LOCUST_DECK.name),
            "structured_text",
        )

        parsed = parse_input_file(LOCUST_DECK)
        self.assertNotIn("error", parsed)
        self.assertEqual(parsed["summary"]["profile"], "structured_text")
        self.assertEqual(parsed["summary"]["total_records"], 192)
        self.assertEqual(parsed["summary"]["record_fields"], 2380)
        self.assertEqual(parsed["summary"]["unparsed_lines"], 0)

        entry = next(
            item
            for item in parsed["entries"]
            if item.get("record_id") == "locust.hydro.p004.vinit"
        )
        self.assertEqual(entry["line_no"], 85)
        self.assertEqual(len(entry["fields"]), 98)
        self.assertEqual(entry["field_line_nos"], [
            line for line in range(85, 99) for _ in range(7)
        ])
        self.assertEqual(
            entry["field_targets"][0]["target"],
            "S85:1@85:locust.hydro.p004.vinit",
        )
        self.assertEqual(
            entry["field_targets"][-1]["target"],
            "S85:98@98:locust.hydro.p004.vinit",
        )

        found = search_input_file(LOCUST_DECK, "p004 vinit")
        self.assertTrue(found["ok"], found.get("error"))
        self.assertEqual(found["count"], 98)
        self.assertEqual(
            [item["field_line_no"] for item in found["results"]],
            [line for line in range(85, 99) for _ in range(7)],
        )
        self.assertTrue(all(item["line_no"] == 85 for item in found["results"]))

        target = found["results"][-1]["target"]
        exact = search_input_file(LOCUST_DECK, target)
        self.assertTrue(exact["ok"], exact.get("error"))
        self.assertEqual(exact["count"], 1)
        self.assertEqual(exact["results"][0]["target"], target)
        self.assertEqual(exact["results"][0]["value"], "14")

    def test_locust_matrix_field_dry_run_and_safe_edit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-locust-") as temp_dir:
            deck = Path(temp_dir) / "system1.i"
            before_bytes = LOCUST_DECK.read_bytes()
            deck.write_bytes(before_bytes)
            target = "S85:2@85:locust.hydro.p004.vinit"

            dry_run = modify_input_file(
                deck,
                target,
                "5.700E+05",
                expected_value="5.765E+05",
                apply=False,
            )
            self.assertTrue(dry_run["ok"], dry_run.get("error"))
            self.assertTrue(dry_run["dry_run"])
            self.assertFalse(dry_run["applied"])
            self.assertEqual(dry_run["profile"], "structured_text")
            self.assertEqual(deck.read_bytes(), before_bytes)

            result = modify_input_file(
                deck,
                target,
                "5.700E+05",
                expected_value="5.765E+05",
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertTrue(result["applied"])
            self.assertFalse(result["dry_run"])

            after_bytes = deck.read_bytes()
            self.assertEqual(
                after_bytes,
                before_bytes.replace(b"5.765E+05", b"5.700E+05", 1),
            )
            updated = parse_input_file(deck)
            entry = next(
                item
                for item in updated["entries"]
                if item.get("record_id") == "locust.hydro.p004.vinit"
            )
            self.assertEqual(entry["fields"][1], "5.700E+05")
            self.assertEqual(len(entry["fields"]), 98)
            self.assertEqual(entry["field_line_nos"][-1], 98)

    def test_json_detection_object_arrays_matrices_and_exact_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-json-") as temp_dir:
            deck = Path(temp_dir) / "sample.json"
            deck.write_bytes(JSON_DECK_TEXT.encode("utf-8"))

            self.assertEqual(
                detect_input_format(JSON_DECK_TEXT, filename="sample.data"),
                "json",
            )
            parsed = parse_input_file(deck)
            self.assertNotIn("error", parsed)
            self.assertEqual(parsed["summary"]["profile"], "json")
            self.assertEqual(parsed["summary"]["record_fields"], 12)
            entries = {
                item["record_id"]: item
                for item in parsed["entries"]
            }
            self.assertEqual(entries["$.enabled"]["fields"], ["true"])
            self.assertEqual(entries["$.nothing"]["fields"], ["null"])
            self.assertEqual(entries["$.points[0].y"]["fields"], ["2", "3"])
            self.assertEqual(
                entries["$.matrix"]["fields"], ["1", "2", "3", "4"]
            )
            self.assertEqual(
                entries["$.points[0].x"]["field_targets"][0]["target"],
                "J5:1@5:$.points[0].x",
            )

            found = search_input_file(deck, "points x")
            self.assertTrue(found["ok"], found.get("error"))
            self.assertEqual(found["count"], 2)
            target = found["results"][0]["target"]

            exact = search_input_file(deck, target)
            self.assertTrue(exact["ok"], exact.get("error"))
            self.assertEqual(exact["count"], 1)
            self.assertEqual(exact["results"][0]["value"], "1.0")

    def test_json_edit_preserves_all_other_formatting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-json-") as temp_dir:
            deck = Path(temp_dir) / "sample.json"
            before = JSON_DECK_TEXT.encode("utf-8")
            deck.write_bytes(before)
            target = "J5:1@5:$.points[0].x"

            result = modify_input_file(
                deck,
                target,
                "9.5",
                expected_value="1.0",
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertTrue(result["applied"])
            self.assertEqual(result["profile"], "json")
            self.assertEqual(
                deck.read_bytes(),
                before.replace(b'"x": 1.0', b'"x": 9.5', 1),
            )

    def test_multiple_locust_matrix_fields_are_modified_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-locust-") as temp_dir:
            deck = Path(temp_dir) / "system1.i"
            deck.write_bytes(LOCUST_DECK.read_bytes())
            parsed = parse_input_file(deck)
            entry = next(
                item
                for item in parsed["entries"]
                if item.get("record_id") == "locust.hydro.p004.vinit"
            )
            first_target = entry["field_targets"][0]["target"]
            last_target = entry["field_targets"][-1]["target"]
            first_value = "5.700E+05" if entry["fields"][0] != "5.700E+05" else "5.800E+05"
            last_value = "15" if entry["fields"][-1] != "15" else "16"

            result = modify_input_fields(
                deck,
                [(first_target, first_value), (last_target, last_value)],
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["count"], 2)
            updated = parse_input_file(deck)
            updated_entry = next(
                item
                for item in updated["entries"]
                if item.get("record_id") == "locust.hydro.p004.vinit"
            )
            self.assertEqual(updated_entry["fields"][0], first_value)
            self.assertEqual(updated_entry["fields"][-1], last_value)
            self.assertEqual(len(updated_entry["fields"]), 98)

    def test_multiple_json_fields_are_modified_in_one_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prisma-json-") as temp_dir:
            deck = Path(temp_dir) / "sample.json"
            deck.write_bytes(JSON_DECK_TEXT.encode("utf-8"))
            entries = {
                item["record_id"]: item
                for item in parse_input_file(deck)["entries"]
            }
            x_target = entries["$.points[0].x"]["field_targets"][0]["target"]
            y_target = entries["$.points[1].y"]["field_targets"][1]["target"]

            result = modify_input_fields(
                deck,
                [(x_target, "9.5"), (y_target, "7")],
            )
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["count"], 2)
            updated = {
                item["record_id"]: item
                for item in parse_input_file(deck)["entries"]
            }
            self.assertEqual(updated["$.points[0].x"]["fields"], ["9.5"])
            self.assertEqual(updated["$.points[1].y"]["fields"], ["5", "7"])
            self.assertIn(b'"matrix": [[1, 2], [3, 4]]', deck.read_bytes())


if __name__ == "__main__":
    unittest.main()
