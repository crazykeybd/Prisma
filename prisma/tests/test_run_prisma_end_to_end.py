from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from openpyxl import Workbook, load_workbook

from api import server
from common.results import ExecutionSummary
from common.util_prisma import release_log_file
from project_store import ProjectStore
from run_prisma.__main__ import main
from run_prisma.executor import execute_testcase
from software import SoftwareLibrary, get_adapter


CASE_NO = "TC-NUSOL"
RUN_DIR = "NUSOL-CASE-001"
SUCCESS_TEXT = "Input processing completed successfully"
SUCCESS_REGEX = f"({SUCCESS_TEXT})"


def write_project(
    root: Path,
    *,
    expected: str = SUCCESS_TEXT,
    action: str = "run_software",
) -> Path:
    """Create a minimal NUSOL project without requiring the real solver."""
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    script = data_dir / "test_data.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "testcase"
    sheet.append(
        [
            "编号",
            "运行目录",
            "action",
            "object",
            "参数1",
            "参数2",
            "参数3",
            "参数4",
        ]
    )
    sheet.append(
        [
            CASE_NO,
            RUN_DIR,
            action,
            "nusolsyslmr.exe",
            "nusolsyslmr",
            "indta.i",
            "outdta",
            "1800",
        ]
    )
    if action == "run_software":
        sheet.append(
            [
                None,
                RUN_DIR,
                "check_output",
                "outdta",
                SUCCESS_REGEX,
                expected,
                "nusolsyslmr",
                None,
            ]
        )
        sheet.merge_cells(start_row=2, start_column=1, end_row=3, end_column=1)
    workbook.save(script)
    workbook.close()

    check = data_dir / "test_check.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["编号", "期望值", "实际值", "偏差", "结论"])
    sheet.append([CASE_NO, None, None, None, None])
    workbook.save(check)
    workbook.close()

    config = {
        "dataConfigPath": "data/test_data.xlsx",
        "checkConfigPath": "data/test_check.xlsx",
        "inputsDir": "inputs",
        "testCasesDir": "test_cases",
        "logDir": "log",
        "executableName": "nusolsyslmr.exe",
        "softwareType": "nusolsyslmr",
        "maxWorkers": 1,
        "runMode": "simulate",
        "nusolsyslmrTimeoutSec": 1800,
    }
    (root / "config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return script


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prisma-e2e-")
        self.root = Path(self.temp_dir.name) / "project"

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def _main(self) -> tuple[int, dict, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--project", str(self.root)])
        raw = output.getvalue()
        return exit_code, json.loads(raw), raw

    def test_success_is_single_json_document_and_writes_all_artifacts(self):
        write_project(self.root)

        exit_code, payload, raw = self._main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["results"], {CASE_NO: True})
        self.assertFalse(payload["execution_failed"])
        self.assertFalse(payload["comparison_failed"])
        self.assertEqual(raw.strip()[0], "{")
        self.assertEqual(raw.strip()[-1], "}")

        reports = payload["reports"]
        expected_files = {
            "run_json",
            "results_json",
            "excel",
            "html",
            "junit",
        }
        for key in expected_files:
            path = Path(reports[key])
            self.assertTrue(path.is_file(), key)
            self.assertGreater(path.stat().st_size, 0, key)
        self.assertEqual(
            set(reports["charts"]),
            {"heatmap", "trend", "distribution"},
        )
        for path in reports["charts"].values():
            self.assertTrue(Path(path).is_file())
            self.assertIn("<svg", Path(path).read_text(encoding="utf-8"))

        html = Path(reports["html"]).read_text(encoding="utf-8")
        self.assertIn(get_adapter("nusolsyslmr").display_name, html)
        self.assertIn(CASE_NO, html)
        junit = Path(reports["junit"]).read_text(encoding="utf-8")
        self.assertIn("<testsuite", junit)
        self.assertIn(f'name="{CASE_NO}"', junit)

        workbook = load_workbook(reports["excel"], data_only=True)
        check_rows = list(workbook["Checks"].iter_rows(values_only=True))
        workbook.close()
        self.assertEqual(check_rows[0][6:9], ("期望值", "实际值", "偏差"))
        self.assertEqual(
            check_rows[1][6:9],
            (SUCCESS_TEXT, SUCCESS_TEXT, "一致"),
        )

        baseline = (
            self.root
            / "baselines"
            / "nusolsyslmr"
            / f"{CASE_NO}.json"
        )
        self.assertTrue(baseline.is_file())
        baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertTrue(baseline_data["points"])
        self.assertTrue((self.root / "baselines" / "index.json").is_file())

        workbook = load_workbook(
            self.root / "data" / "test_check.xlsx",
            data_only=True,
        )
        result_rows = list(workbook.active.iter_rows(values_only=True))
        workbook.close()
        self.assertEqual(
            result_rows[1],
            (
                CASE_NO,
                SUCCESS_TEXT,
                SUCCESS_TEXT,
                "一致",
                "通过",
            ),
        )

    def test_case_run_dir_is_cleared_before_execution(self):
        write_project(self.root)
        run_dir = self.root / "test_cases" / RUN_DIR
        old_results = run_dir / "old-results" / "old.dat"
        old_results.parent.mkdir(parents=True)
        old_results.write_text("stale result", encoding="utf-8")
        old_screen = run_dir / "old-screen.txt"
        old_screen.write_text("stale screen", encoding="utf-8")

        exit_code, _, _ = self._main()

        self.assertEqual(exit_code, 0)
        self.assertFalse(old_results.exists())
        self.assertFalse(old_screen.exists())
        self.assertTrue((run_dir / "outdta").is_file())

    def test_comparison_failure_returns_one_and_keeps_reports(self):
        write_project(self.root, expected="Input processing did not complete")

        exit_code, payload, _ = self._main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["exit_code"], 1)
        self.assertTrue(payload["comparison_failed"])
        self.assertFalse(payload["execution_failed"])
        outcome = payload["outcomes"][CASE_NO]
        self.assertEqual(outcome["failure_type"], "comparison")
        self.assertEqual(outcome["failed_action"], "check_output")
        self.assertTrue(Path(payload["reports"]["html"]).is_file())

    def test_step_execution_failure_returns_two(self):
        write_project(self.root, action="unsupported_action")

        exit_code, payload, _ = self._main()

        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["exit_code"], 2)
        self.assertTrue(payload["execution_failed"])
        self.assertFalse(payload["comparison_failed"])
        outcome = payload["outcomes"][CASE_NO]
        self.assertEqual(outcome["failure_type"], "execution")
        self.assertEqual(outcome["failed_action"], "unsupported_action")
        self.assertTrue(Path(payload["reports"]["junit"]).is_file())

    def test_preexisting_stop_event_is_recorded_as_stopped(self):
        write_project(self.root)
        stop_event = threading.Event()
        stop_event.set()

        with redirect_stdout(io.StringIO()):
            summary = execute_testcase(
                project_root=self.root,
                stop_event=stop_event,
                return_summary=True,
            )

        self.assertIsInstance(summary, ExecutionSummary)
        self.assertEqual(summary.exit_code, 2)
        self.assertTrue(summary.execution_failed)
        outcome = summary.outcomes[CASE_NO]
        self.assertTrue(outcome.stopped)
        self.assertEqual(outcome.commands_completed, 0)
        self.assertIn("用户请求停止执行", outcome.error_message)
        self.assertTrue(summary.reports)
        self.assertTrue(summary.reports.html.is_file())


class ApiRunStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prisma-api-run-")
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.store = ProjectStore(
            self.base / "projects",
            software_library=self.library,
        )
        self.project = self.store.create("NUSOL API", "nusolsyslmr")
        write_project(self.project.root)
        self.state = {
            "running": False,
            "stop_event": threading.Event(),
            "order": [],
            "steps": {},
            "summary": None,
            "project_id": None,
            "run_id": None,
            "reports": None,
            "report_directory": None,
            "html_report": None,
            "exit_code": None,
            "execution_summary": None,
        }
        self.patches = [
            patch.object(server, "SOFTWARE_LIBRARY", self.library),
            patch.object(server, "STORE", self.store),
            patch.object(server, "STATE", self.state),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        release_log_file()
        self.temp_dir.cleanup()

    def _wait_for_run(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while self.state["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(
            self.state["running"],
            "API background run did not finish before timeout",
        )

    def test_start_run_populates_status_and_report_contract(self):
        ok, message = server.start_run(
            "data/test_data.xlsx",
            "data/test_check.xlsx",
            "",
            "simulate",
            1,
        )
        self.assertTrue(ok, message)
        self._wait_for_run()

        self.assertEqual(self.state["summary"], {CASE_NO: True})
        self.assertEqual(self.state["exit_code"], 0)
        self.assertIsNotNone(self.state["execution_summary"])
        self.assertTrue(Path(self.state["html_report"]).is_file())
        self.assertEqual(
            Path(self.state["reports"]["directory"]).name,
            self.state["run_id"],
        )

        handler = object.__new__(server.Handler)
        handler.path = "/api/status"
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        server.Handler.do_GET(handler)
        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))

        self.assertFalse(payload["running"])
        self.assertEqual(payload["summary"], {CASE_NO: True})
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["html_report"], self.state["html_report"])
        self.assertIn("results_json", payload["reports"])
        self.assertIn("junit", payload["reports"])
        self.assertIn("charts", payload["reports"])

    def test_status_distinguishes_comparison_failure_from_interruption(self):
        write_project(
            self.project.root,
            expected="Input processing did not complete",
        )

        ok, message = server.start_run(
            "data/test_data.xlsx",
            "data/test_check.xlsx",
            "",
            "simulate",
            1,
        )
        self.assertTrue(ok, message)
        self._wait_for_run()

        self.assertEqual(self.state["summary"], {CASE_NO: False})
        self.assertEqual(self.state["exit_code"], 1)
        summary = self.state["execution_summary"]
        self.assertTrue(summary["comparison_failed"])
        self.assertFalse(summary["execution_failed"])
        outcome = summary["outcomes"][CASE_NO]
        self.assertEqual(outcome["failure_type"], "comparison")
        self.assertEqual(outcome["failed_action"], "check_output")
        self.assertFalse(outcome["stopped"])


if __name__ == "__main__":
    unittest.main()
