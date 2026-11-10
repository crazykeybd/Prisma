from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from api.server import clear_logfile, project_relative_path, read_results
from common.util_prisma import release_log_file
from project_store import ProjectError, ProjectStore


class ResultTableTests(unittest.TestCase):
    def _workbook(self, root: Path, headers: list[str]) -> Path:
        path = root / "result.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        sheet.append(["TC-01", "case", "input", "1.0", "1.0", "通过"])
        workbook.save(path)
        workbook.close()
        return path

    def test_result_headers_are_normalized_to_expected_and_actual_values(self):
        with tempfile.TemporaryDirectory(prefix="prisma-result-") as temp_dir:
            path = self._workbook(
                Path(temp_dir),
                ["编号", "名称", "输入", "期望结果", "实际结果", "结论"],
            )
            rows = read_results(path)["rows"]

        self.assertEqual(rows[0][3:5], ["期望值", "实际值"])

    def test_missing_expected_and_actual_columns_are_added(self):
        with tempfile.TemporaryDirectory(prefix="prisma-result-") as temp_dir:
            path = self._workbook(
                Path(temp_dir),
                ["编号", "名称", "输入", "结论"],
            )
            rows = read_results(path)["rows"]

        self.assertEqual(
            rows[0],
            ["编号", "名称", "输入", "期望值", "实际值", "结论"],
        )
        self.assertEqual(rows[1][3:5], ["", ""])


class ProjectRelativePathTests(unittest.TestCase):
    def test_returns_project_and_inputs_relative_paths(self):
        with tempfile.TemporaryDirectory(prefix="prisma-path-") as temp_dir:
            store = ProjectStore(Path(temp_dir) / "projects")
            project = store.create("paths", "relap5")
            result = project_relative_path(
                project.root / "inputs" / "CheckSGInput.txt",
                require_input=True,
                project=project,
            )

        self.assertEqual(result["path"], "inputs/CheckSGInput.txt")
        self.assertEqual(result["input_path"], "CheckSGInput.txt")

    def test_rejects_input_card_outside_inputs_directory(self):
        with tempfile.TemporaryDirectory(prefix="prisma-path-") as temp_dir:
            store = ProjectStore(Path(temp_dir) / "projects")
            project = store.create("paths", "relap5")
            outside = project.root / "data" / "test_data.xlsx"
            with self.assertRaisesRegex(ProjectError, "inputs 目录"):
                project_relative_path(
                    outside,
                    require_input=True,
                    project=project,
                )


class ClearLogTests(unittest.TestCase):
    def tearDown(self):
        release_log_file()

    def test_clear_truncates_log_and_reinitializes_file_handler(self):
        with tempfile.TemporaryDirectory(prefix="prisma-log-") as temp_dir:
            try:
                root = Path(temp_dir)
                project = type("Project", (), {"root": root})()
                log_path = root / "log" / "prisma_run.log"
                log_path.parent.mkdir(parents=True)
                log_path.write_text("old log\n", encoding="utf-8")

                result = clear_logfile(project)
                logging.getLogger("prisma").info("new log after clear")
                for handler in logging.getLogger("prisma").handlers:
                    handler.flush()

                text = log_path.read_text(encoding="utf-8")
                self.assertTrue(result["ok"])
                self.assertIn("new log after clear", text)
                self.assertNotIn("old log", text)
            finally:
                release_log_file()


if __name__ == "__main__":
    unittest.main()
