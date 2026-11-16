from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common.version_compare import (
    UNLABELED_VERSION,
    VersionCompareError,
    compare_reports,
    discover_reports,
)


def _write_report(
    project_root: Path,
    run_id: str,
    *,
    recorded_run_id: str | None = None,
    version: str = "1.0",
    finished_at: str = "2026-01-01T00:00:00+00:00",
    cases: list[dict] | None = None,
) -> None:
    report_dir = project_root / "exports" / "reports" / run_id
    report_dir.mkdir(parents=True)
    cases = cases or []
    checks = [
        check
        for case in cases
        for check in case.get("checks", [])
    ]
    summary = {
        "total_cases": len(cases),
        "passed_cases": sum(1 for case in cases if case.get("passed")),
        "failed_cases": sum(1 for case in cases if not case.get("passed")),
        "skipped_cases": 0,
        "total_checks": len(checks),
        "failed_checks": sum(1 for check in checks if not check.get("passed")),
        "anomalies": sum(
            int((check.get("comparison") or {}).get("anomaly_count") or 0)
            for check in checks
        ),
        "exit_code": 0 if all(case.get("passed") for case in cases) else 1,
    }
    results = {
        "run_id": recorded_run_id or run_id,
        "software": {
            "key": "demo",
            "display_name": "Demo Solver",
            "version": version,
        },
        "mode": "simulate",
        "started_at": finished_at,
        "finished_at": finished_at,
        "duration_seconds": 1.5,
        "summary": summary,
        "cases": cases,
    }
    manifest = {
        "run_id": run_id,
        "software": results["software"],
        "mode": "simulate",
        "started_at": finished_at,
        "finished_at": finished_at,
        "duration_seconds": 1.5,
        "summary": summary,
    }
    (report_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False),
        encoding="utf-8",
    )
    (report_dir / "run.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )


def _case(
    case_no: str,
    *,
    passed: bool,
    checks: list[dict] | None = None,
    error_message: str = "",
) -> dict:
    return {
        "case_no": case_no,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "failure_type": "" if passed else "comparison",
        "error_message": error_message,
        "commands_total": 2,
        "checks": checks or [],
    }


def _check(
    key: str,
    *,
    actual: float,
    expected: float,
    passed: bool,
    error: str = "",
) -> dict:
    return {
        "key": key,
        "expected_value": expected,
        "actual_value": actual,
        "passed": passed,
        "status": "passed" if passed else "failed",
        "error": error,
        "comparison": {"anomaly_count": 0 if passed else 1},
    }


class ReportDiscoveryTests(unittest.TestCase):
    def test_lists_versions_newest_first_and_uses_fallback_version(self):
        with tempfile.TemporaryDirectory(prefix="prisma-compare-list-") as temp_dir:
            root = Path(temp_dir)
            _write_report(
                root,
                "run-old",
                version="1.0",
                finished_at="2026-01-01T00:00:00+00:00",
                cases=[_case("A", passed=True)],
            )
            _write_report(
                root,
                "run-new",
                version="2.0",
                finished_at="2026-02-01T00:00:00+00:00",
                cases=[_case("A", passed=True)],
            )
            _write_report(
                root,
                "run-unlabeled",
                version="",
                finished_at="2025-12-01T00:00:00+00:00",
                cases=[_case("A", passed=True)],
            )

            reports = discover_reports(root)

        self.assertEqual(
            [report["run_id"] for report in reports],
            ["run-new", "run-old", "run-unlabeled"],
        )
        self.assertEqual(reports[0]["version"], "2.0")
        self.assertEqual(reports[-1]["version"], UNLABELED_VERSION)
        self.assertIn("2.0 · run-new", reports[0]["label"])

    def test_uses_report_directory_as_run_id_when_snapshot_id_differs(self):
        with tempfile.TemporaryDirectory(prefix="prisma-compare-run-id-") as temp_dir:
            root = Path(temp_dir)
            _write_report(
                root,
                "run-directory",
                recorded_run_id="stale-id",
                cases=[_case("A", passed=True)],
            )

            reports = discover_reports(root)
            result = compare_reports(root, "run-directory", "run-directory")

        self.assertEqual(reports[0]["run_id"], "run-directory")
        self.assertEqual(result["baseline"]["run_id"], "run-directory")


class ReportComparisonTests(unittest.TestCase):
    def test_compares_case_and_check_transitions(self):
        with tempfile.TemporaryDirectory(prefix="prisma-compare-") as temp_dir:
            root = Path(temp_dir)
            _write_report(
                root,
                "run-v1",
                version="1.0",
                cases=[
                    _case(
                        "A",
                        passed=True,
                        checks=[
                            _check("pressure", actual=10.0, expected=10.0, passed=True),
                            _check("temp", actual=20.0, expected=20.0, passed=True),
                        ],
                    ),
                    _case(
                        "B",
                        passed=False,
                        checks=[
                            _check("flow", actual=4.0, expected=5.0, passed=False)
                        ],
                    ),
                    _case(
                        "C",
                        passed=True,
                        checks=[
                            _check("level", actual=3.0, expected=3.0, passed=True)
                        ],
                    ),
                ],
            )
            _write_report(
                root,
                "run-v2",
                version="2.0",
                cases=[
                    _case(
                        "A",
                        passed=False,
                        checks=[
                            _check("pressure", actual=12.0, expected=10.0, passed=False),
                            _check("temp", actual=20.0, expected=20.0, passed=True),
                        ],
                    ),
                    _case(
                        "B",
                        passed=True,
                        checks=[
                            _check("flow", actual=5.0, expected=5.0, passed=True)
                        ],
                    ),
                    _case(
                        "D",
                        passed=True,
                        checks=[
                            _check("power", actual=8.0, expected=8.0, passed=True)
                        ],
                    ),
                ],
            )

            result = compare_reports(root, "run-v1", "run-v2")

        cases = {item["case_no"]: item for item in result["case_changes"]}
        self.assertEqual(cases["A"]["change_type"], "regressed")
        self.assertEqual(cases["B"]["change_type"], "fixed")
        self.assertEqual(cases["C"]["change_type"], "removed")
        self.assertEqual(cases["D"]["change_type"], "added")
        self.assertEqual(result["counts"]["cases"]["regressed"], 1)
        self.assertEqual(result["counts"]["cases"]["fixed"], 1)

        checks = {
            (item["case_no"], item["key"]): item
            for item in result["check_changes"]
        }
        pressure = checks[("A", "pressure")]
        self.assertEqual(pressure["change_type"], "regressed")
        self.assertAlmostEqual(pressure["absolute_change"], 2.0)
        self.assertAlmostEqual(pressure["relative_change"], 0.2)
        self.assertEqual(checks[("A", "temp")]["change_type"], "unchanged")
        self.assertEqual(checks[("B", "flow")]["change_type"], "fixed")
        self.assertEqual(checks[("C", "level")]["change_type"], "removed")
        self.assertEqual(checks[("D", "power")]["change_type"], "added")
        self.assertEqual(result["summary"]["passed_cases"]["baseline"], 2)
        self.assertEqual(result["summary"]["passed_cases"]["latest"], 2)

    def test_duplicate_check_keys_are_compared_by_occurrence(self):
        with tempfile.TemporaryDirectory(prefix="prisma-compare-dupe-") as temp_dir:
            root = Path(temp_dir)
            _write_report(
                root,
                "run-v1",
                cases=[
                    _case(
                        "A",
                        passed=True,
                        checks=[
                            _check("pressure", actual=10.0, expected=10.0, passed=True),
                            _check("pressure", actual=11.0, expected=11.0, passed=True),
                        ],
                    )
                ],
            )
            _write_report(
                root,
                "run-v2",
                cases=[
                    _case(
                        "A",
                        passed=True,
                        checks=[
                            _check("pressure", actual=10.0, expected=10.0, passed=True),
                            _check("pressure", actual=12.0, expected=11.0, passed=True),
                        ],
                    )
                ],
            )

            result = compare_reports(root, "run-v1", "run-v2")

        pressure_changes = [
            item
            for item in result["check_changes"]
            if item["key"] == "pressure"
        ]
        self.assertEqual(len(pressure_changes), 2)
        self.assertEqual(pressure_changes[0]["change_type"], "unchanged")
        self.assertEqual(pressure_changes[1]["change_type"], "changed")
        self.assertEqual(pressure_changes[1]["occurrence"], 2)

    def test_changed_check_error_is_reported_as_changed(self):
        with tempfile.TemporaryDirectory(prefix="prisma-compare-check-error-") as temp_dir:
            root = Path(temp_dir)
            _write_report(
                root,
                "run-v1",
                cases=[
                    _case(
                        "A",
                        passed=False,
                        checks=[
                            _check(
                                "pressure",
                                actual=10.0,
                                expected=10.0,
                                passed=False,
                                error="case-sensitive lookup failed",
                            )
                        ],
                    )
                ],
            )
            _write_report(
                root,
                "run-v2",
                cases=[
                    _case(
                        "A",
                        passed=False,
                        checks=[
                            _check(
                                "pressure",
                                actual=10.0,
                                expected=10.0,
                                passed=False,
                                error="case-insensitive lookup failed",
                            )
                        ],
                    )
                ],
            )

            result = compare_reports(root, "run-v1", "run-v2")

        self.assertEqual(result["check_changes"][0]["change_type"], "changed")

    def test_rejects_invalid_run_ids_and_path_traversal(self):
        with tempfile.TemporaryDirectory(prefix="prisma-compare-safe-") as temp_dir:
            root = Path(temp_dir)
            _write_report(root, "run-v1", cases=[_case("A", passed=True)])

            with self.assertRaisesRegex(VersionCompareError, "运行编号格式无效"):
                compare_reports(root, "../run-v1", "run-v1")
            with self.assertRaisesRegex(VersionCompareError, "运行编号格式无效"):
                compare_reports(root, "run-v1", "C:\\outside")
            with self.assertRaisesRegex(VersionCompareError, "报告不存在"):
                compare_reports(root, "missing", "run-v1")


if __name__ == "__main__":
    unittest.main()
