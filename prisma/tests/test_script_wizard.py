"""测试剧本向导：编号、运行目录、参数矩阵展开与默认值。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.script_wizard import (
    MAX_CASES,
    ScriptWizardError,
    build_cases,
    case_numbers,
    normalize_spec,
    run_dir_for,
    wizard_defaults,
)
from software import SoftwareLibrary


class FakeProject:
    """最小项目对象：向导只需要 root 与 software_key。"""

    def __init__(self, root, software_key="relap5"):
        self.root = Path(root)
        self.software_key = software_key


class WizardBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.project = FakeProject(self.base / "projects" / "demo")
        (self.project.root / "inputs").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def spec(self, **overrides):
        data = {
            "software_key": "relap5",
            "count": 1,
            "start": 1,
            "prefix": "TC",
            "run_dir_template": "test_cases/{case}",
        }
        data.update(overrides)
        return data


class CaseNumberTests(unittest.TestCase):
    def test_pads_to_two_digits(self):
        self.assertEqual(case_numbers("TC", 3, 1), ["TC-01", "TC-02", "TC-03"])

    def test_widens_for_large_start(self):
        self.assertEqual(case_numbers("ABC", 3, 9), ["ABC-09", "ABC-10", "ABC-11"])
        self.assertEqual(case_numbers("X", 1, 100), ["X-100"])

    def test_defaults_when_prefix_and_count_blank(self):
        self.assertEqual(case_numbers("", None, None), ["TC-01"])

    def test_rejects_bad_prefix_and_count(self):
        for prefix in ("A/B", "A:B", "A*B", 'A"B', "A|B"):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(ScriptWizardError, "前缀"):
                    case_numbers(prefix, 1, 1)
        with self.assertRaisesRegex(ScriptWizardError, "用例数量"):
            case_numbers("TC", 0, 1)
        with self.assertRaisesRegex(ScriptWizardError, "用例数量"):
            case_numbers("TC", MAX_CASES + 1, 1)
        with self.assertRaisesRegex(ScriptWizardError, "起始编号"):
            case_numbers("TC", 1, 0)


class RunDirTests(unittest.TestCase):
    def test_substitutes_placeholders(self):
        self.assertEqual(
            run_dir_for("test_cases/{case}", "TC-01"), "test_cases/TC-01"
        )
        self.assertEqual(run_dir_for(r"cases\{CASE}\run", "TC-02"), "cases/TC-02/run")
        self.assertEqual(run_dir_for("out/{index}", "TC-03"), "out/TC-03")

    def test_default_template(self):
        self.assertEqual(run_dir_for("", "TC-01"), "test_cases/TC-01")

    def test_rejects_absolute_and_parent_paths(self):
        for template in ("D:/tmp/{case}", "/tmp/{case}", "../{case}"):
            with self.subTest(template=template):
                with self.assertRaises(ScriptWizardError):
                    run_dir_for(template, "TC-01")


class NormalizeSpecTests(WizardBase):
    def test_fills_adapter_defaults(self):
        data = normalize_spec(self.spec(), self.project, self.library)
        self.assertEqual(data["software_key"], "relap5")
        self.assertTrue(data["executable"])
        self.assertTrue(data["input_card"])
        self.assertTrue(data["output"])
        self.assertGreater(data["timeout"], 0)
        self.assertEqual(data["case_numbers"], ["TC-01"])

    def test_multi_case_requires_placeholder(self):
        with self.assertRaisesRegex(ScriptWizardError, r"\{case\}"):
            normalize_spec(
                self.spec(count=2, run_dir_template="test_cases/fixed"),
                self.project,
                self.library,
            )

    def test_single_case_allows_fixed_dir(self):
        data = normalize_spec(
            self.spec(run_dir_template="test_cases/fixed"),
            self.project,
            self.library,
        )
        self.assertEqual(data["run_dir_template"], "test_cases/fixed")

    def test_unknown_software(self):
        with self.assertRaisesRegex(ScriptWizardError, "未知软件"):
            normalize_spec(self.spec(software_key="nope"), self.project, self.library)

    def test_bad_timeout(self):
        with self.assertRaisesRegex(ScriptWizardError, "超时"):
            normalize_spec(self.spec(timeout=0), self.project, self.library)

    def test_variable_without_target_is_rejected(self):
        with self.assertRaisesRegex(ScriptWizardError, "缺少变量定位"):
            normalize_spec(
                self.spec(variables=[{"values": ["1.0"]}]),
                self.project,
                self.library,
            )

    def test_matrix_pads_and_drops_empty_rows(self):
        data = normalize_spec(
            self.spec(
                count=3,
                variables=[
                    {"target": "L12:3@45", "values": ["1.0"]},
                    {"target": "L12:4@45", "values": ["", ""]},
                    {"values": [""]},
                ],
                checks=[{"target": "p@100010000", "values": ["15.5"]}],
            ),
            self.project,
            self.library,
        )
        self.assertEqual([v["target"] for v in data["variables"]], ["L12:3@45"])
        self.assertEqual(data["variables"][0]["values"], ["1.0", "", ""])
        self.assertEqual(data["checks"][0]["values"], ["15.5", "", ""])


class BuildCasesTests(WizardBase):
    def test_row_layout(self):
        cases = build_cases(
            self.spec(
                count=1,
                executable="relap5.exe",
                software_key="relap5",
                input_card="CheckSGInput.txt",
                output="outdta",
                timeout=600,
                variables=[{"target": "L12:3@45", "values": ["1.0"]}],
                checks=[{"keyword": "p@100010000", "values": ["15.5"]}],
            ),
            self.project,
            self.library,
        )
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case["no"], "TC-01")
        self.assertEqual(
            case["rows"],
            [
                ["test_cases/TC-01", "folder_prep", "TC-01", "CheckSGInput.txt",
                 "relap5.exe", "", ""],
                ["test_cases/TC-01", "modify_inp", "CheckSGInput.txt", "L12:3@45",
                 "1.0", "", ""],
                ["test_cases/TC-01", "run_software", "relap5.exe", "relap5",
                 "CheckSGInput.txt", "outdta", "600"],
                ["test_cases/TC-01", "check_output", "outdta", "p@100010000",
                 "15.5", "relap5", ""],
            ],
        )

    def test_blank_values_skip_rows(self):
        cases = build_cases(
            self.spec(
                count=3,
                input_card="CheckSGInput.txt",
                output="outdta",
                timeout=600,
                variables=[{"target": "L12:3@45", "values": ["1.0", "", "3.0"]}],
                checks=[{"keyword": "p@100010000", "values": ["15.5", "16.0", ""]}],
            ),
            self.project,
            self.library,
        )
        actions = [[row[1] for row in case["rows"]] for case in cases]
        self.assertEqual(
            actions,
            [
                ["folder_prep", "modify_inp", "run_software", "check_output"],
                ["folder_prep", "run_software", "check_output"],
                ["folder_prep", "modify_inp", "run_software"],
            ],
        )
        self.assertEqual(
            [case["rows"][0][0] for case in cases],
            ["test_cases/TC-01", "test_cases/TC-02", "test_cases/TC-03"],
        )

    def test_every_row_has_seven_cells(self):
        cases = build_cases(self.spec(count=2), self.project, self.library)
        for case in cases:
            for row in case["rows"]:
                self.assertEqual(len(row), 7)

    def test_custom_card_field_overrides_default(self):
        cases = build_cases(
            self.spec(
                variables=[
                    {"target": "table.1", "card": "table.dat", "values": ["2"]}
                ]
            ),
            self.project,
            self.library,
        )
        self.assertEqual(cases[0]["rows"][1][2], "table.dat")


class WizardDefaultsTests(WizardBase):
    def test_returns_expected_fields(self):
        (self.project.root / "inputs" / "case.inp").write_text("x", encoding="utf-8")
        sub = self.project.root / "inputs" / "relap5"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "extra.i").write_text("x", encoding="utf-8")

        data = wizard_defaults(self.project, self.library)

        for key in (
            "software_key", "software_name", "software_options", "executable",
            "input_card", "inputs", "output", "timeout", "prefix", "count",
            "start", "run_dir_template", "script_path", "case_numbers",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["software_key"], "relap5")
        self.assertIn("case.inp", data["inputs"])
        self.assertIn("relap5/extra.i", data["inputs"])
        self.assertIn(data["input_card"], data["inputs"])
        self.assertEqual(data["case_numbers"], case_numbers(data["prefix"], data["count"], data["start"]))
        self.assertEqual(
            [item["key"] for item in data["software_options"]][:1], ["relap5"]
        )
        self.assertTrue(data["script_path"].endswith(".xlsx"))

    def test_falls_back_when_project_has_no_inputs(self):
        data = wizard_defaults(self.project, self.library)
        self.assertTrue(data["input_card"])
        self.assertIsInstance(data["inputs"], list)


if __name__ == "__main__":
    unittest.main()
