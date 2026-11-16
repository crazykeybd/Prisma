"""Compare immutable report snapshots from different software versions."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UNLABELED_VERSION = "未标注版本"
REPORTS_RELATIVE_PATH = Path("exports") / "reports"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class VersionCompareError(ValueError):
    """Raised when report snapshots cannot be discovered or compared."""


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any, default: float = 0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _count(value: Any, default: int = 0) -> int:
    return int(_number(value, default))


def _json_read(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _reports_root(project_root: Path) -> Path:
    return (Path(project_root).resolve() / REPORTS_RELATIVE_PATH).resolve()


def _run_directory(project_root: Path, run_id: str) -> tuple[Path, Path]:
    run_id = _text(run_id)
    if not run_id or not _RUN_ID_RE.fullmatch(run_id):
        raise VersionCompareError("运行编号格式无效")
    reports_root = _reports_root(project_root)
    candidate = (reports_root / run_id).resolve()
    if candidate.parent != reports_root:
        raise VersionCompareError("报告路径必须位于当前项目的报告目录内")
    if not candidate.is_dir():
        raise VersionCompareError(f"报告不存在: {run_id}")
    return reports_root, candidate


def _parse_timestamp(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _known_passed(case: dict) -> bool:
    if isinstance(case.get("passed"), bool):
        return case["passed"]
    return _text(case.get("status")).lower() == "passed"


def _normalized_summary(data: dict, manifest: dict, cases: list) -> dict:
    source = _as_dict(data.get("summary")) or _as_dict(manifest.get("summary"))
    checks = [
        check
        for case in cases
        for check in _as_list(case.get("checks"))
        if isinstance(check, dict)
    ]
    passed_cases = sum(1 for case in cases if _known_passed(case))
    total_cases = _count(source.get("total_cases"), len(cases))
    total_checks = _count(source.get("total_checks"), len(checks))
    failed_checks = _count(
        source.get("failed_checks"),
        sum(1 for check in checks if not _known_passed(check)),
    )
    anomalies = _count(
        source.get("anomalies"),
        sum(
            _count(_as_dict(check.get("comparison")).get("anomaly_count"))
            for check in checks
        ),
    )
    return {
        "total_cases": total_cases,
        "passed_cases": _count(source.get("passed_cases"), passed_cases),
        "failed_cases": _count(
            source.get("failed_cases"),
            max(0, total_cases - passed_cases),
        ),
        "skipped_cases": _count(source.get("skipped_cases")),
        "execution_failed_cases": _count(
            source.get("execution_failed_cases")
        ),
        "comparison_failed_cases": _count(
            source.get("comparison_failed_cases")
        ),
        "total_checks": total_checks,
        "failed_checks": failed_checks,
        "anomalies": anomalies,
        "exit_code": _count(source.get("exit_code")),
        "fatal_error": _text(source.get("fatal_error")),
    }


def _report_summary(
    project_root: Path,
    run_dir: Path,
    data: dict,
    manifest: dict,
) -> dict:
    merged = manifest or data
    software = _as_dict(data.get("software")) or _as_dict(manifest.get("software"))
    cases = [case for case in _as_list(data.get("cases")) if isinstance(case, dict)]
    version = _text(software.get("version")) or UNLABELED_VERSION
    # The directory name is the stable identifier accepted by the compare API.
    # Older snapshots may contain a stale or inconsistent run_id in their JSON.
    run_id = run_dir.name
    summary = _normalized_summary(data, manifest, cases)
    finished_at = (
        _text(data.get("finished_at"))
        or _text(manifest.get("finished_at"))
    )
    started_at = (
        _text(data.get("started_at"))
        or _text(manifest.get("started_at"))
    )
    duration = _number(
        data.get("duration_seconds", manifest.get("duration_seconds"))
    )
    return {
        "run_id": run_id,
        "version": version,
        "version_label": version,
        "label": f"{version} · {run_id}",
        "software": software,
        "software_name": _text(
            software.get("display_name") or software.get("key")
        ),
        "mode": _text(data.get("mode")) or _text(merged.get("mode")),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(duration, 6),
        "summary": summary,
        "directory": _relative(run_dir, project_root),
        "results_json": _relative(run_dir / "results.json", project_root),
        "report_html": _relative(run_dir / "report.html", project_root),
    }


def _read_snapshot(project_root: Path, run_id: str) -> tuple[dict, dict, dict, Path]:
    _, run_dir = _run_directory(project_root, run_id)
    data = _json_read(run_dir / "results.json")
    if data is None:
        raise VersionCompareError(f"报告缺少可读取的结果快照: {run_id}")
    manifest = _json_read(run_dir / "run.json") or {}
    return data, manifest, _report_summary(project_root, run_dir, data, manifest), run_dir


def discover_reports(project_root: Path) -> list[dict]:
    """Return comparable report snapshots, newest first."""

    reports_root = _reports_root(project_root)
    if not reports_root.is_dir():
        return []

    reports = []
    for run_dir in reports_root.iterdir():
        if not run_dir.is_dir() or not _RUN_ID_RE.fullmatch(run_dir.name):
            continue
        data = _json_read(run_dir / "results.json")
        if data is None:
            continue
        manifest = _json_read(run_dir / "run.json") or {}
        reports.append(_report_summary(project_root, run_dir, data, manifest))

    def sort_key(report: dict) -> tuple[float, str]:
        timestamp = _parse_timestamp(
            report.get("finished_at") or report.get("started_at")
        )
        return (
            timestamp if timestamp is not None else 0,
            _text(report.get("run_id")),
        )

    reports.sort(key=sort_key, reverse=True)
    return reports


def _cases_by_number(data: dict) -> tuple[dict[str, dict], list[str]]:
    cases: dict[str, dict] = {}
    order: list[str] = []
    for index, raw_case in enumerate(_as_list(data.get("cases"))):
        if not isinstance(raw_case, dict):
            continue
        case_no = _text(raw_case.get("case_no")) or f"case-{index + 1}"
        if case_no in cases:
            continue
        case = dict(raw_case)
        case["case_no"] = case_no
        case["passed"] = _known_passed(raw_case)
        cases[case_no] = case
        order.append(case_no)
    return cases, order


def _checks_by_identity(
    cases: dict[str, dict],
    order: list[str],
) -> tuple[dict[tuple[str, str, int], dict], list[tuple[str, str, int]]]:
    checks: dict[tuple[str, str, int], dict] = {}
    identities: list[tuple[str, str, int]] = []
    for case_no in order:
        case = cases[case_no]
        occurrences: Counter[str] = Counter()
        for index, raw_check in enumerate(_as_list(case.get("checks"))):
            if not isinstance(raw_check, dict):
                continue
            key = _text(raw_check.get("key"))
            if key:
                occurrences[key] += 1
                occurrence = occurrences[key]
                identity = (case_no, key, occurrence)
            else:
                occurrence = index + 1
                identity = (case_no, "", occurrence)
            if identity in checks:
                continue
            check = dict(raw_check)
            check["key"] = key
            check["display_key"] = key or f"未命名检查项 {occurrence}"
            check["passed"] = _known_passed(raw_check)
            checks[identity] = check
            identities.append(identity)
    return checks, identities


def _status_label(case: dict | None) -> str:
    if case is None:
        return "缺失"
    status = _text(case.get("status")).lower()
    if _known_passed(case):
        return "通过"
    if status == "skipped":
        return "跳过"
    return "失败"


def _case_change_type(before: dict, after: dict) -> str:
    before_passed = _known_passed(before)
    after_passed = _known_passed(after)
    if before_passed and not after_passed:
        return "regressed"
    if not before_passed and after_passed:
        return "fixed"
    before_status = _text(before.get("status")).lower()
    after_status = _text(after.get("status")).lower()
    detail_fields = (
        "failure_type",
        "failed_step",
        "failed_action",
        "error_message",
    )
    if before_status != after_status or any(
        before.get(field) != after.get(field) for field in detail_fields
    ):
        return "changed"
    return "unchanged"


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _values_equal(before: Any, after: Any) -> bool:
    before_number = _numeric_value(before)
    after_number = _numeric_value(after)
    if before_number is not None and after_number is not None:
        return before_number == after_number
    return before == after


def _check_change_type(before: dict, after: dict) -> str:
    before_passed = _known_passed(before)
    after_passed = _known_passed(after)
    if before_passed and not after_passed:
        return "regressed"
    if not before_passed and after_passed:
        return "fixed"
    if (
        not _values_equal(before.get("actual_value"), after.get("actual_value"))
        or not _values_equal(
            before.get("expected_value"), after.get("expected_value")
        )
        or _text(before.get("status")) != _text(after.get("status"))
        or _text(before.get("error")) != _text(after.get("error"))
    ):
        return "changed"
    return "unchanged"


def _check_payload(
    identity: tuple[str, str, int],
    before: dict | None,
    after: dict | None,
) -> dict:
    case_no, key, occurrence = identity
    if before is None:
        change_type = "added"
    elif after is None:
        change_type = "removed"
    else:
        change_type = _check_change_type(before, after)
    before_value = before.get("actual_value") if before else None
    after_value = after.get("actual_value") if after else None
    before_number = _numeric_value(before_value)
    after_number = _numeric_value(after_value)
    absolute_change = None
    relative_change = None
    if before_number is not None and after_number is not None:
        absolute_change = after_number - before_number
        if before_number:
            relative_change = absolute_change / abs(before_number)
        elif after_number == 0:
            relative_change = 0.0
    source = after or before or {}
    return {
        "case_no": case_no,
        "key": key,
        "display_key": _text(source.get("display_key"))
        or key
        or f"未命名检查项 {occurrence}",
        "occurrence": occurrence,
        "before": before,
        "after": after,
        "before_passed": _known_passed(before) if before else None,
        "after_passed": _known_passed(after) if after else None,
        "before_actual_value": before_value,
        "after_actual_value": after_value,
        "before_expected_value": before.get("expected_value") if before else None,
        "after_expected_value": after.get("expected_value") if after else None,
        "absolute_change": absolute_change,
        "relative_change": relative_change,
        "change_type": change_type,
    }


def _comparison_metric(before: int | float, after: int | float) -> dict:
    return {
        "baseline": before,
        "latest": after,
        "delta": after - before,
    }


def compare_reports(
    project_root: Path,
    baseline_run_id: str,
    latest_run_id: str,
) -> dict:
    """Compare two report snapshots by case number and check key."""

    baseline_data, baseline_manifest, baseline, _ = _read_snapshot(
        project_root, baseline_run_id
    )
    latest_data, latest_manifest, latest, _ = _read_snapshot(
        project_root, latest_run_id
    )
    before_cases, before_order = _cases_by_number(baseline_data)
    after_cases, after_order = _cases_by_number(latest_data)
    case_order = list(before_order)
    case_order.extend(case_no for case_no in after_order if case_no not in before_cases)

    case_changes = []
    case_counts: Counter[str] = Counter()
    for case_no in case_order:
        before_case = before_cases.get(case_no)
        after_case = after_cases.get(case_no)
        if before_case is None:
            change_type = "added"
        elif after_case is None:
            change_type = "removed"
        else:
            change_type = _case_change_type(before_case, after_case)
        case_counts[change_type] += 1
        source = after_case or before_case or {}
        case_changes.append(
            {
                "case_no": case_no,
                "before": before_case,
                "after": after_case,
                "before_status": _status_label(before_case),
                "after_status": _status_label(after_case),
                "before_passed": (
                    _known_passed(before_case) if before_case else None
                ),
                "after_passed": _known_passed(after_case) if after_case else None,
                "before_failure_type": _text(
                    (before_case or {}).get("failure_type")
                ),
                "after_failure_type": _text(
                    (after_case or {}).get("failure_type")
                ),
                "before_error": _text((before_case or {}).get("error_message")),
                "after_error": _text((after_case or {}).get("error_message")),
                "commands_total": _count(source.get("commands_total")),
                "change_type": change_type,
            }
        )

    before_checks, before_identities = _checks_by_identity(
        before_cases, before_order
    )
    after_checks, after_identities = _checks_by_identity(
        after_cases, after_order
    )
    check_identities = list(before_identities)
    check_identities.extend(
        identity for identity in after_identities if identity not in before_checks
    )
    check_changes = []
    check_counts: Counter[str] = Counter()
    checks_by_case: dict[str, list[str]] = {}
    for identity in check_identities:
        payload = _check_payload(
            identity,
            before_checks.get(identity),
            after_checks.get(identity),
        )
        check_changes.append(payload)
        check_counts[payload["change_type"]] += 1
        if payload["change_type"] != "unchanged":
            checks_by_case.setdefault(payload["case_no"], []).append(
                payload["change_type"]
            )
    for case_change in case_changes:
        case_change["changed_checks"] = len(
            checks_by_case.get(case_change["case_no"], [])
        )
        case_change["regressed_checks"] = checks_by_case.get(
            case_change["case_no"], []
        ).count("regressed")
        case_change["fixed_checks"] = checks_by_case.get(
            case_change["case_no"], []
        ).count("fixed")

    before_summary = baseline["summary"]
    after_summary = latest["summary"]
    summary_keys = (
        "total_cases",
        "passed_cases",
        "failed_cases",
        "skipped_cases",
        "total_checks",
        "failed_checks",
        "anomalies",
        "exit_code",
    )
    metrics = {
        key: _comparison_metric(before_summary[key], after_summary[key])
        for key in summary_keys
    }
    metrics["duration_seconds"] = _comparison_metric(
        baseline["duration_seconds"],
        latest["duration_seconds"],
    )

    return {
        "baseline": baseline,
        "latest": latest,
        "summary": metrics,
        "counts": {
            "cases": {
                key: case_counts[key]
                for key in (
                    "regressed",
                    "fixed",
                    "changed",
                    "added",
                    "removed",
                    "unchanged",
                )
            },
            "checks": {
                key: check_counts[key]
                for key in (
                    "regressed",
                    "fixed",
                    "changed",
                    "added",
                    "removed",
                    "unchanged",
                )
            },
        },
        "case_changes": case_changes,
        "check_changes": check_changes,
    }
