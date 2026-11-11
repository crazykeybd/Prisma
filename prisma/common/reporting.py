"""Generate project-level JSON, Excel, HTML and JUnit test reports."""

from __future__ import annotations

import html
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common.results import (
    ARTIFACT_DIR_NAME,
    CaseExecution,
    ReportPaths,
    SCHEMA_VERSION,
    compare_points,
    read_json,
    read_jsonl,
    safe_token,
    utc_now,
    write_json_atomic,
)


_HEADER_FILL = PatternFill("solid", fgColor="1D4E89")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_PASS_FILL = PatternFill("solid", fgColor="D9EAD3")
_FAIL_FILL = PatternFill("solid", fgColor="F4CCCC")
_ERROR_FILL = PatternFill("solid", fgColor="FCE5CD")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _number(value):
    if value is None or isinstance(value, bool):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return number


def _baseline_path(project_root: Path, software_key: str, case_no: str) -> Path:
    return (
        project_root
        / "baselines"
        / safe_token(software_key, "software")
        / f"{safe_token(case_no)}.json"
    )


def _save_baseline(
    project_root: Path,
    software: dict,
    case: dict,
    script_path: Path,
) -> dict:
    points = []
    for check in case.get("checks") or []:
        point = check.get("point")
        if not isinstance(point, dict) or not check.get("passed"):
            continue
        points.append(
            {
                **point,
                "metadata": {
                    **(point.get("metadata") or {}),
                    "check_key": check.get("key", ""),
                },
            }
        )
    path = _baseline_path(
        project_root, str(software.get("key") or ""), case["case_no"]
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "case_no": case["case_no"],
        "created_at": utc_now(),
        "software": software,
        "source": {
            "script": _relative(script_path, project_root),
            "output_files": [
                item
                for item in (case.get("run") or {}).get("output_files", [])
            ],
            "run": {
                key: (case.get("run") or {}).get(key)
                for key in (
                    "run_id",
                    "started_at",
                    "finished_at",
                    "returncode",
                    "command",
                    "input_sha256",
                    "output_sha256",
                )
            },
        },
        "points": points,
    }
    write_json_atomic(path, payload)
    index_path = project_root / "baselines" / "index.json"
    index = read_json(index_path, default={}) or {}
    entries = index.get("entries") if isinstance(index, dict) else None
    if not isinstance(entries, list):
        entries = []
    entries = [
        item
        for item in entries
        if not (
            isinstance(item, dict)
            and item.get("software_key") == software.get("key")
            and item.get("case_no") == case["case_no"]
        )
    ]
    entries.append(
        {
            "software_key": software.get("key", ""),
            "case_no": case["case_no"],
            "path": _relative(path, project_root),
            "software_version": software.get("version", ""),
            "created_at": payload["created_at"],
        }
    )
    write_json_atomic(
        index_path,
        {"schema_version": SCHEMA_VERSION, "entries": entries},
    )
    return payload


def _attach_baseline_comparison(
    case: dict,
    baseline: dict | None,
) -> None:
    if not baseline:
        case["baseline"] = {"status": "not_available", "points": 0}
        return
    points = baseline.get("points") or []
    by_key = {
        str((point.get("metadata") or {}).get("check_key") or ""): point
        for point in points
        if isinstance(point, dict)
    }
    matched = 0
    for check in case.get("checks") or []:
        point = check.get("point")
        expected = by_key.get(str(check.get("key") or ""))
        if not isinstance(point, dict) or not isinstance(expected, dict):
            continue
        matched += 1
        comparison = compare_points(
            [point],
            [expected],
            rtol=float(check.get("rtol") or 1e-4),
            atol=float(check.get("atol") or 1e-6),
        )
        check["baseline_comparison"] = comparison
    case["baseline"] = {
        "status": "used",
        "points": len(points),
        "matched": matched,
        "created_at": baseline.get("created_at", ""),
        "software": baseline.get("software") or {},
        "source": baseline.get("source") or {},
    }


def _group_metrics(checks: Iterable[dict], scope: str) -> list[dict]:
    groups: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"current": [], "expected": []}
    )
    for check in checks:
        comparison_key = "comparison" if scope == "expected" else "baseline_comparison"
        comparison = check.get(comparison_key) or {}
        point = check.get("point")
        expected = (
            check.get("expected_point")
            if scope == "expected"
            else None
        )
        if scope == "baseline":
            baseline_point = None
            for item in comparison.get("points") or []:
                if item.get("key") == check.get("key"):
                    baseline_point = {
                        "value": item.get("baseline"),
                        "parameter": item.get("parameter", ""),
                        "coordinate": item.get("coordinate", ""),
                        "unit": item.get("unit", ""),
                        "metadata": {"key": check.get("key", "")},
                    }
                    break
            expected = baseline_point
        if not isinstance(point, dict) or not isinstance(expected, dict):
            continue
        key = (
            str(point.get("parameter") or ""),
            str(point.get("unit") or ""),
            str(point.get("data_type") or ""),
        )
        groups[key]["current"].append(point)
        groups[key]["expected"].append(expected)

    metrics: list[dict] = []
    for (parameter, unit, data_type), values in sorted(groups.items()):
        if not values["current"] or not values["expected"]:
            continue
        result = compare_points(values["current"], values["expected"])
        metrics.append(
            {
                "scope": scope,
                "parameter": parameter,
                "unit": unit,
                "data_type": data_type,
                **result,
            }
        )
    return metrics


def build_report_data(
    *,
    project_root: Path,
    run_id: str,
    case_outcomes: dict[str, CaseExecution],
    case_run_dirs: dict[str, Path],
    script_path: Path,
    software: dict,
    mode: str,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    report_directory: Path,
    fatal_error: str = "",
    exit_code: int | None = None,
) -> dict:
    cases: list[dict] = []
    for case_no, outcome in case_outcomes.items():
        run_dir = Path(
            outcome.run_dir
            or case_run_dirs.get(case_no)
            or (project_root / "test_cases" / safe_token(case_no))
        )
        artifact = run_dir / ARTIFACT_DIR_NAME
        run = read_json(artifact / "run.json", default={}) or {}
        checks = read_jsonl(artifact / "checks.jsonl")
        execution = read_json(artifact / "execution.json", default={}) or {}
        baseline_path = _baseline_path(
            project_root, str(software.get("key") or ""), case_no
        )
        baseline = read_json(baseline_path, default=None)

        case = {
            "case_no": case_no,
            "status": "passed" if outcome.passed else (
                "skipped" if outcome.stopped else "failed"
            ),
            "passed": bool(outcome.passed),
            "failure_type": outcome.failure_type,
            "failed_step": outcome.failed_step,
            "failed_action": outcome.failed_action,
            "error_message": outcome.error_message,
            "commands_total": outcome.commands_total,
            "commands_completed": outcome.commands_completed,
            "run_dir": _relative(run_dir, project_root),
            "run": run,
            "execution": execution,
            "checks": checks,
            "metrics": _group_metrics(checks, "expected"),
        }
        _attach_baseline_comparison(case, baseline)
        case["metrics"].extend(_group_metrics(checks, "baseline"))

        if outcome.passed and checks and not baseline:
            baseline = _save_baseline(project_root, software, case, script_path)
            case["baseline"] = {
                "status": "created",
                "points": len(baseline.get("points") or []),
                "created_at": baseline.get("created_at", ""),
                "software": baseline.get("software") or {},
            }
        cases.append(case)

    check_records = [
        check for case in cases for check in (case.get("checks") or [])
    ]
    passed_cases = sum(1 for case in cases if case["passed"])
    skipped_cases = sum(1 for case in cases if case["status"] == "skipped")
    execution_failed = sum(
        1
        for case in cases
        if case["failure_type"] == "execution" or case["status"] == "skipped"
    )
    comparison_failed = sum(
        1 for case in cases if case["failure_type"] == "comparison"
    )
    failed_checks = sum(1 for check in check_records if not check.get("passed"))
    total_anomalies = sum(
        int((check.get("comparison") or {}).get("anomaly_count") or 0)
        for check in check_records
    )
    computed_exit_code = (
        2
        if fatal_error or execution_failed
        else 1
        if comparison_failed or failed_cases(cases)
        else 0
    )
    if exit_code is None:
        exit_code = computed_exit_code
    summary = {
        "total_cases": len(cases),
        "passed_cases": passed_cases,
        "failed_cases": len(cases) - passed_cases - skipped_cases,
        "skipped_cases": skipped_cases,
        "execution_failed_cases": execution_failed,
        "comparison_failed_cases": comparison_failed,
        "total_checks": len(check_records),
        "failed_checks": failed_checks,
        "anomalies": total_anomalies,
        "exit_code": int(exit_code),
        "fatal_error": str(fatal_error or ""),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "run_id": run_id,
        "project": {
            "root": str(project_root),
            "name": project_root.name,
        },
        "script": _relative(script_path, project_root),
        "software": software,
        "mode": mode,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(float(duration_seconds), 6),
        "summary": summary,
        "cases": cases,
        "artifacts": {
            "directory": str(report_directory),
            "results_json": "results.json",
            "excel": "results.xlsx",
            "html": "report.html",
            "junit": "junit.xml",
            "charts": {
                "heatmap": "charts/heatmap.svg",
                "trend": "charts/trend.svg",
                "distribution": "charts/distribution.svg",
            },
        },
    }


def failed_cases(cases: Iterable[dict]) -> bool:
    return any(not case.get("passed") for case in cases)


def _svg_document(width: int, height: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        'role="img">'
        '<style>text{font-family:Segoe UI,Arial,sans-serif;fill:#1f2937}'
        '.axis{stroke:#94a3b8;stroke-width:1}.grid{stroke:#e2e8f0;'
        'stroke-width:1}.muted{fill:#64748b;font-size:12px}</style>'
        '<rect width="100%" height="100%" fill="#ffffff"/>'
        f"{body}</svg>"
    )


def _heatmap_svg(cases: list[dict]) -> str:
    keys: list[str] = []
    for case in cases:
        for check in case.get("checks") or []:
            key = str(check.get("key") or "")
            if key and key not in keys:
                keys.append(key)
    row_height = 34
    cell_width = max(120, min(220, 900 // max(1, len(keys))))
    left = 180
    top = 65
    width = max(640, left + max(1, len(keys)) * cell_width + 30)
    height = max(180, top + max(1, len(cases)) * row_height + 30)
    body = [
        '<text x="24" y="28" font-size="18" font-weight="600">'
        "检查项热力图</text>",
        '<text x="24" y="48" class="muted">颜色按相对误差分级，红色表示超限</text>',
    ]
    for column, key in enumerate(keys):
        x = left + column * cell_width + 8
        body.append(
            f'<text x="{x}" y="{top - 12}" class="muted">{html.escape(key)}</text>'
        )
    for row, case in enumerate(cases):
        y = top + row * row_height
        body.append(
            f'<text x="24" y="{y + 21}" font-size="13">'
            f"{html.escape(str(case.get('case_no') or ''))}</text>"
        )
        checks = {str(item.get("key") or ""): item for item in case.get("checks") or []}
        for column, key in enumerate(keys):
            x = left + column * cell_width
            check = checks.get(key)
            if not check:
                color = "#f1f5f9"
                label = "-"
            elif not check.get("passed"):
                color = "#ef8a80"
                label = f"{(check.get('comparison') or {}).get('max_relative_error') or 0:.3g}"
            else:
                relative = (check.get("comparison") or {}).get("max_relative_error")
                ratio = min(1.0, float(relative or 0) / 1e-4)
                red = int(214 + 25 * ratio)
                green = int(237 - 60 * ratio)
                blue = int(218 - 70 * ratio)
                color = f"#{red:02x}{green:02x}{blue:02x}"
                label = f"{float(relative or 0):.3g}"
            body.append(
                f'<rect x="{x}" y="{y}" width="{cell_width - 8}" '
                f'height="{row_height - 5}" rx="3" fill="{color}"/>'
            )
            body.append(
                f'<text x="{x + (cell_width - 8) / 2}" y="{y + 19}" '
                f'text-anchor="middle" font-size="12">{html.escape(label)}</text>'
            )
    if not keys:
        body.append(
            f'<text x="{left}" y="{top + 20}" class="muted">没有检查结果</text>'
        )
    return _svg_document(width, height, "".join(body))


def _trend_svg(cases: list[dict]) -> str:
    width, height = 920, 380
    left, right, top, bottom = 70, 30, 60, 60
    plot_width = width - left - right
    plot_height = height - top - bottom
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    max_error = 1e-9
    for case_index, case in enumerate(cases):
        for check in case.get("checks") or []:
            comparison = check.get("comparison") or {}
            relative = comparison.get("max_relative_error")
            if relative is None:
                continue
            value = float(relative)
            series[str(check.get("key") or "")].append((case_index, value))
            max_error = max(max_error, value)
    max_error *= 1.12
    body = [
        '<text x="24" y="28" font-size="18" font-weight="600">'
        "相对误差趋势</text>",
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" '
        f'y2="{top + plot_height}"/>',
        f'<line class="axis" x1="{left}" y1="{top + plot_height}" '
        f'x2="{left + plot_width}" y2="{top + plot_height}"/>',
    ]
    for step in range(5):
        ratio = step / 4
        y = top + plot_height * (1 - ratio)
        body.append(
            f'<line class="grid" x1="{left}" y1="{y}" '
            f'x2="{left + plot_width}" y2="{y}"/>'
        )
        body.append(
            f'<text x="{left - 10}" y="{y + 4}" text-anchor="end" '
            f'class="muted">{max_error * ratio:.2e}</text>'
        )
    colors = ("#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2")
    for index, case in enumerate(cases):
        x = left + (plot_width * (index + 0.5) / max(1, len(cases)))
        body.append(
            f'<text x="{x}" y="{top + plot_height + 22}" '
            f'text-anchor="middle" class="muted">{html.escape(case["case_no"])}</text>'
        )
    for series_index, (key, points) in enumerate(sorted(series.items())):
        color = colors[series_index % len(colors)]
        rendered = []
        for case_index, value in points:
            x = left + plot_width * (case_index + 0.5) / max(1, len(cases))
            y = top + plot_height * (1 - value / max_error)
            rendered.append(f"{x:.1f},{y:.1f}")
        body.append(
            f'<polyline points="{" ".join(rendered)}" fill="none" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        for point in rendered:
            x, y = point.split(",")
            body.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{color}"/>')
        legend_y = top + 18 + series_index * 19
        body.append(
            f'<line x1="{left + plot_width - 190}" y1="{legend_y}" '
            f'x2="{left + plot_width - 165}" y2="{legend_y}" '
            f'stroke="{color}" stroke-width="3"/>'
        )
        body.append(
            f'<text x="{left + plot_width - 158}" y="{legend_y + 4}" '
            f'font-size="12">{html.escape(key)}</text>'
        )
    if not series:
        body.append(
            f'<text x="{left + 20}" y="{top + 30}" class="muted">'
            "没有可绘制的数值检查</text>"
        )
    return _svg_document(width, height, "".join(body))


def _distribution_svg(cases: list[dict]) -> str:
    values = [
        float((check.get("comparison") or {}).get("max_relative_error"))
        for case in cases
        for check in case.get("checks") or []
        if (check.get("comparison") or {}).get("max_relative_error") is not None
    ]
    width, height = 760, 340
    left, right, top, bottom = 70, 30, 60, 55
    plot_width = width - left - right
    plot_height = height - top - bottom
    body = [
        '<text x="24" y="28" font-size="18" font-weight="600">'
        "相对误差分布</text>",
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" '
        f'y2="{top + plot_height}"/>',
        f'<line class="axis" x1="{left}" y1="{top + plot_height}" '
        f'x2="{left + plot_width}" y2="{top + plot_height}"/>',
    ]
    if values:
        maximum = max(values) or 1.0
        bins = [0.0] * 10
        for value in values:
            position = min(9, int((value / maximum) * 10))
            bins[position] += 1
        highest = max(bins) or 1
        bar_width = plot_width / len(bins)
        for index, count in enumerate(bins):
            bar_height = plot_height * count / highest
            x = left + index * bar_width + 5
            y = top + plot_height - bar_height
            body.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 10:.1f}" '
                f'height="{bar_height:.1f}" rx="2" fill="#4f7cac"/>'
            )
            body.append(
                f'<text x="{x + (bar_width - 10) / 2:.1f}" '
                f'y="{top + plot_height + 20}" text-anchor="middle" '
                f'class="muted">{maximum * index / 10:.1e}</text>'
            )
    else:
        body.append(
            f'<text x="{left + 20}" y="{top + 30}" class="muted">'
            "没有可绘制的数值检查</text>"
        )
    return _svg_document(width, height, "".join(body))


def write_charts(data: dict, chart_dir: Path) -> dict[str, Path]:
    chart_dir.mkdir(parents=True, exist_ok=True)
    charts = {
        "heatmap": chart_dir / "heatmap.svg",
        "trend": chart_dir / "trend.svg",
        "distribution": chart_dir / "distribution.svg",
    }
    cases = data.get("cases") or []
    charts["heatmap"].write_text(_heatmap_svg(cases), encoding="utf-8")
    charts["trend"].write_text(_trend_svg(cases), encoding="utf-8")
    charts["distribution"].write_text(_distribution_svg(cases), encoding="utf-8")
    return charts


def _style_sheet(sheet, widths: dict[int, int] | None = None) -> None:
    for cell in sheet[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in (widths or {}).items():
        sheet.column_dimensions[get_column_letter(column)].width = width


def write_excel(data: dict, path: Path) -> Path:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["项目", "值"])
    summary_rows = (
        ("run_id", data.get("run_id")),
        ("项目", (data.get("project") or {}).get("name")),
        ("剧本", data.get("script")),
        ("软件", (data.get("software") or {}).get("display_name")),
        ("版本", (data.get("software") or {}).get("version")),
        ("运行模式", data.get("mode")),
        ("开始时间", data.get("started_at")),
        ("结束时间", data.get("finished_at")),
        ("耗时（秒）", data.get("duration_seconds")),
    )
    for key, value in summary_rows:
        summary.append([key, value])
    for key, value in (data.get("summary") or {}).items():
        summary.append([key, value])
    _style_sheet(summary, {1: 28, 2: 88})

    cases_sheet = workbook.create_sheet("Cases")
    cases_sheet.append(
        [
            "用例",
            "状态",
            "失败类型",
            "失败步骤",
            "失败动作",
            "检查数",
            "异常数",
            "最大相对误差",
            "运行目录",
            "错误信息",
        ]
    )
    for case in data.get("cases") or []:
        checks = case.get("checks") or []
        max_relative = max(
            (
                float((check.get("comparison") or {}).get("max_relative_error"))
                for check in checks
                if (check.get("comparison") or {}).get("max_relative_error")
                is not None
            ),
            default=None,
        )
        cases_sheet.append(
            [
                case.get("case_no"),
                case.get("status"),
                case.get("failure_type"),
                case.get("failed_step"),
                case.get("failed_action"),
                len(checks),
                sum(
                    int((check.get("comparison") or {}).get("anomaly_count") or 0)
                    for check in checks
                ),
                max_relative,
                case.get("run_dir"),
                case.get("error_message"),
            ]
        )
    _style_sheet(
        cases_sheet,
        {1: 22, 2: 12, 3: 16, 4: 12, 5: 18, 9: 34, 10: 70},
    )

    checks_sheet = workbook.create_sheet("Checks")
    checks_sheet.append(
        [
            "用例",
            "检查键",
            "参数",
            "坐标",
            "单位",
            "数据类型",
            "期望值",
            "实际值",
            "状态",
            "绝对误差",
            "相对误差",
            "MSE",
            "RMSE",
            "Pearson",
            "K-S",
            "异常点数",
            "错误信息",
        ]
    )
    for case in data.get("cases") or []:
        for check in case.get("checks") or []:
            comparison = check.get("comparison") or {}
            point = check.get("point") or {}
            checks_sheet.append(
                [
                    case.get("case_no"),
                    check.get("key"),
                    point.get("parameter"),
                    point.get("coordinate"),
                    point.get("unit"),
                    point.get("data_type"),
                    check.get("expected_value"),
                    check.get("actual_value"),
                    check.get("status"),
                    comparison.get("max_abs_error"),
                    comparison.get("max_relative_error"),
                    comparison.get("mse"),
                    comparison.get("rmse"),
                    comparison.get("pearson"),
                    comparison.get("ks_statistic"),
                    comparison.get("anomaly_count"),
                    check.get("error"),
                ]
            )
    _style_sheet(
        checks_sheet,
        {
            1: 22,
            2: 22,
            7: 20,
            8: 20,
            11: 18,
            17: 60,
        },
    )

    metrics_sheet = workbook.create_sheet("Metrics")
    metrics_sheet.append(
        [
            "用例",
            "范围",
            "参数",
            "单位",
            "点数",
            "MSE",
            "RMSE",
            "MAE",
            "最大绝对误差",
            "最大相对误差",
            "平均有符号误差",
            "系统性偏差",
            "偏差方向占比",
            "Pearson",
            "K-S 统计量",
            "K-S p 值",
            "异常区间",
        ]
    )
    for case in data.get("cases") or []:
        for metric in case.get("metrics") or []:
            intervals = ",".join(
                f"{item.get('start')}-{item.get('end')}"
                for item in metric.get("anomaly_intervals") or []
            )
            metrics_sheet.append(
                [
                    case.get("case_no"),
                    metric.get("scope"),
                    metric.get("parameter"),
                    metric.get("unit"),
                    metric.get("count"),
                    metric.get("mse"),
                    metric.get("rmse"),
                    metric.get("mae"),
                    metric.get("max_abs_error"),
                    metric.get("max_relative_error"),
                    metric.get("mean_signed_error"),
                    metric.get("systematic_bias"),
                    metric.get("systematic_bias_ratio"),
                    metric.get("pearson"),
                    metric.get("ks_statistic"),
                    metric.get("ks_p_value"),
                    intervals,
                ]
            )
    _style_sheet(metrics_sheet, {1: 22, 2: 14, 3: 18, 17: 24})

    anomalies_sheet = workbook.create_sheet("Anomalies")
    anomalies_sheet.append(
        [
            "用例",
            "检查键",
            "序号",
            "参数",
            "坐标",
            "单位",
            "基线值",
            "实际值",
            "有符号误差",
            "相对误差",
            "容差",
        ]
    )
    for case in data.get("cases") or []:
        for check in case.get("checks") or []:
            for item in (check.get("comparison") or {}).get("anomalies") or []:
                anomalies_sheet.append(
                    [
                        case.get("case_no"),
                        check.get("key"),
                        item.get("index"),
                        item.get("parameter"),
                        item.get("coordinate"),
                        item.get("unit"),
                        item.get("baseline"),
                        item.get("actual"),
                        item.get("signed_error"),
                        item.get("relative_error"),
                        item.get("tolerance"),
                    ]
                )
    _style_sheet(anomalies_sheet, {1: 22, 2: 22})

    trace_sheet = workbook.create_sheet("Trace")
    trace_sheet.append(
        [
            "用例",
            "状态",
            "软件",
            "版本",
            "模式",
            "开始时间",
            "结束时间",
            "耗时（秒）",
            "返回码",
            "命令行",
            "程序 SHA256",
            "输入 SHA256",
            "输出 SHA256",
            "主机",
            "输出文件",
            "屏幕输出摘要",
            "错误",
        ]
    )
    for case in data.get("cases") or []:
        run = case.get("run") or {}
        trace_sheet.append(
            [
                case.get("case_no"),
                run.get("status"),
                run.get("software_type"),
                run.get("software_version"),
                run.get("mode"),
                run.get("started_at"),
                run.get("finished_at"),
                run.get("duration_seconds"),
                run.get("returncode"),
                " ".join(str(item) for item in run.get("command") or []),
                run.get("executable_sha256"),
                run.get("input_sha256"),
                run.get("output_sha256"),
                run.get("host"),
                "；".join(
                    str(item.get("path") or "")
                    for item in run.get("output_files") or []
                ),
                run.get("screen_excerpt"),
                run.get("error"),
            ]
        )
    _style_sheet(trace_sheet, {1: 22, 3: 18, 10: 65, 16: 70, 17: 45})

    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=2):
            status = next(
                (
                    str(cell.value)
                    for cell in row
                    if cell.value in ("passed", "通过", "failed", "不通过")
                ),
                "",
            )
            if not status:
                continue
            fill = _PASS_FILL if status in ("passed", "通过") else _FAIL_FILL
            for cell in row:
                cell.fill = fill
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()
    return path


def write_junit(data: dict, path: Path) -> Path:
    cases = data.get("cases") or []
    total = 0
    failures = 0
    errors = 0
    skipped = 0
    root = ET.Element("testsuites")
    for case in cases:
        suite = ET.SubElement(
            root,
            "testsuite",
            {
                "name": str(case.get("case_no") or ""),
                "timestamp": data.get("started_at", ""),
                "time": f"{float((case.get('run') or {}).get('duration_seconds') or 0):.6f}",
            },
        )
        suite_props = ET.SubElement(suite, "properties")
        ET.SubElement(
            suite_props,
            "property",
            {"name": "prisma.run_id", "value": str(data.get("run_id") or "")},
        )
        ET.SubElement(
            suite_props,
            "property",
            {"name": "prisma.run_dir", "value": str(case.get("run_dir") or "")},
        )
        checks = case.get("checks") or []
        if not checks:
            checks = [{"key": "run", "status": case.get("status"), "passed": case.get("passed")}]
        suite_tests = 0
        suite_failures = 0
        suite_errors = 0
        suite_skipped = 0
        for check in checks:
            suite_tests += 1
            total += 1
            testcase = ET.SubElement(
                suite,
                "testcase",
                {
                    "classname": str(case.get("case_no") or ""),
                    "name": str(check.get("key") or "run"),
                    "time": f"{float((case.get('run') or {}).get('duration_seconds') or 0):.6f}",
                },
            )
            properties = ET.SubElement(testcase, "properties")
            for name, value in (
                ("prisma.key", check.get("key")),
                ("prisma.output", check.get("output_file")),
                ("prisma.expected", check.get("expected_value")),
                ("prisma.actual", check.get("actual_value")),
                ("prisma.artifact", case.get("run_dir")),
                ("prisma.attachment", data.get("artifacts", {}).get("html")),
            ):
                ET.SubElement(
                    properties,
                    "property",
                    {"name": name, "value": "" if value is None else str(value)},
                )
            if case.get("status") == "skipped":
                suite_skipped += 1
                skipped += 1
                ET.SubElement(testcase, "skipped", {"message": case.get("error_message") or "stopped"})
            elif check.get("status") == "error" or case.get("failure_type") == "execution":
                suite_errors += 1
                errors += 1
                message = (
                    check.get("error")
                    or case.get("error_message")
                    or "execution failed"
                )
                ET.SubElement(
                    testcase,
                    "error",
                    {"type": "ExecutionError", "message": str(message)},
                ).text = str(message)
            elif not check.get("passed"):
                suite_failures += 1
                failures += 1
                comparison = check.get("comparison") or {}
                message = (
                    f"expected={check.get('expected_value')} "
                    f"actual={check.get('actual_value')} "
                    f"max_relative_error={comparison.get('max_relative_error')}"
                )
                ET.SubElement(
                    testcase,
                    "failure",
                    {"type": "ComparisonFailure", "message": message},
                ).text = message
            stdout = ET.SubElement(testcase, "system-out")
            run = case.get("run") or {}
            stdout.text = "\n".join(
                (
                    f"case={case.get('case_no')}",
                    f"command={' '.join(str(item) for item in run.get('command') or [])}",
                    f"returncode={run.get('returncode')}",
                    f"output={run.get('output_sha256')}",
                    f"baseline={case.get('baseline', {}).get('status')}",
                )
            )
            stderr = ET.SubElement(testcase, "system-err")
            stderr.text = str(
                check.get("error")
                or case.get("error_message")
                or run.get("screen_excerpt")
                or ""
            )
        suite.set("tests", str(suite_tests))
        suite.set("failures", str(suite_failures))
        suite.set("errors", str(suite_errors))
        suite.set("skipped", str(suite_skipped))
    root.set("name", f"Prisma {data.get('run_id')}")
    root.set("tests", str(total))
    root.set("failures", str(failures))
    root.set("errors", str(errors))
    root.set("skipped", str(skipped))
    root.set("time", f"{float(data.get('duration_seconds') or 0):.6f}")
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _html_table(headers: list[str], rows: list[list]) -> str:
    head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
    body = []
    for row in rows:
        body.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape('' if value is None else str(value))}</td>"
                for value in row
            )
            + "</tr>"
        )
    return (
        f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def write_html(data: dict, path: Path, charts: dict[str, Path]) -> Path:
    summary = data.get("summary") or {}
    case_rows = [
        [
            case.get("case_no"),
            case.get("status"),
            case.get("failure_type"),
            case.get("failed_action"),
            case.get("failed_step"),
            len(case.get("checks") or []),
            sum(
                int((check.get("comparison") or {}).get("anomaly_count") or 0)
                for check in case.get("checks") or []
            ),
        ]
        for case in data.get("cases") or []
    ]
    check_rows = []
    for case in data.get("cases") or []:
        for check in case.get("checks") or []:
            comparison = check.get("comparison") or {}
            check_rows.append(
                [
                    case.get("case_no"),
                    check.get("key"),
                    (check.get("point") or {}).get("unit"),
                    check.get("expected_value"),
                    check.get("actual_value"),
                    comparison.get("max_relative_error"),
                    comparison.get("mse"),
                    comparison.get("ks_statistic"),
                    check.get("status"),
                ]
            )
    anomaly_rows = [
        [
            case.get("case_no"),
            check.get("key"),
            item.get("index"),
            item.get("coordinate"),
            item.get("baseline"),
            item.get("actual"),
            item.get("relative_error"),
            item.get("tolerance"),
        ]
        for case in data.get("cases") or []
        for check in case.get("checks") or []
        for item in (check.get("comparison") or {}).get("anomalies") or []
    ]
    trace_rows = []
    for case in data.get("cases") or []:
        run = case.get("run") or {}
        trace_rows.append(
            [
                case.get("case_no"),
                run.get("status"),
                run.get("software_version"),
                run.get("mode"),
                " ".join(str(item) for item in run.get("command") or []),
                run.get("executable_sha256"),
                run.get("input_sha256"),
                run.get("output_sha256"),
                run.get("duration_seconds"),
                run.get("host"),
            ]
        )
    chart_sections = []
    for key, title in (
        ("heatmap", "检查热力图"),
        ("trend", "误差趋势"),
        ("distribution", "误差分布"),
    ):
        chart = charts.get(key)
        if chart and chart.is_file():
            chart_sections.append(
                f'<section><h2>{html.escape(title)}</h2>'
                f'<div class="chart">{chart.read_text(encoding="utf-8")}</div></section>'
            )
    software = data.get("software") or {}
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prisma 测试报告 {html.escape(str(data.get("run_id") or ""))}</title>
<style>
:root{{--ink:#172033;--muted:#5d6b82;--line:#d8e0ea;--pass:#0f6b3d;
--fail:#a92727;--error:#a34b00;--paper:#f6f8fb;--panel:#fff;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);
font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif}}main{{max-width:1280px;
margin:0 auto;padding:32px 28px 60px}}header{{border-bottom:3px solid var(--ink);
padding-bottom:18px;margin-bottom:24px}}h1{{font-size:30px;margin:0 0 6px}}
h2{{font-size:19px;margin:30px 0 12px}}p{{margin:4px 0;color:var(--muted)}}
.stats{{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;
margin:20px 0}}.stat{{background:var(--panel);border:1px solid var(--line);
padding:12px}}.stat b{{display:block;font-size:24px}}.stat span{{color:var(--muted)}}
.table-wrap{{overflow:auto;border:1px solid var(--line);background:var(--panel)}}
table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{padding:8px 10px;
border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{background:#eaf0f6;font-weight:600;position:sticky;top:0}}tr:last-child td{{border:0}}
.chart{{background:var(--panel);border:1px solid var(--line);padding:12px;overflow:auto}}
.chart svg{{max-width:100%;height:auto}}.mono{{font-family:Consolas,monospace;
font-size:12px;word-break:break-all}}.passed{{color:var(--pass);font-weight:600}}
.failed{{color:var(--fail);font-weight:600}}.error{{color:var(--error);font-weight:600}}
footer{{margin-top:40px;color:var(--muted);font-size:12px}}
@media(max-width:840px){{main{{padding:20px 14px}}.stats{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body><main>
<header><h1>Prisma 自动化测试报告</h1>
<p>Run ID: {html.escape(str(data.get("run_id") or ""))}</p>
<p>项目: {html.escape(str((data.get("project") or {}).get("name") or ""))} ·
软件: {html.escape(str(software.get("display_name") or software.get("key") or ""))} ·
版本: {html.escape(str(software.get("version") or ""))} ·
模式: {html.escape(str(data.get("mode") or ""))}</p>
<p>剧本: {html.escape(str(data.get("script") or ""))} ·
{html.escape(str(data.get("started_at") or ""))} 至
{html.escape(str(data.get("finished_at") or ""))}</p></header>
<div class="stats">
<div class="stat"><b>{summary.get("total_cases", 0)}</b><span>用例总数</span></div>
<div class="stat"><b>{summary.get("passed_cases", 0)}</b><span>通过</span></div>
<div class="stat"><b>{summary.get("failed_cases", 0)}</b><span>失败</span></div>
<div class="stat"><b>{summary.get("total_checks", 0)}</b><span>检查项</span></div>
<div class="stat"><b>{summary.get("anomalies", 0)}</b><span>异常点</span></div>
<div class="stat"><b>{summary.get("exit_code", 0)}</b><span>CLI 退出码</span></div>
</div>
<section><h2>用例结果</h2>
{_html_table(["用例", "状态", "失败类型", "失败动作", "步骤", "检查数", "异常点"], case_rows)}
</section>
{"".join(chart_sections)}
<section><h2>检查与统计</h2>
{_html_table(["用例", "检查键", "单位", "期望值", "实际值", "最大相对误差", "MSE", "K-S", "状态"], check_rows)}
</section>
<section><h2>异常点</h2>
{_html_table(["用例", "检查键", "序号", "坐标", "基线值", "实际值", "相对误差", "容差"], anomaly_rows)}
</section>
<section><h2>运行追溯</h2>
{_html_table(["用例", "状态", "软件版本", "模式", "命令行", "程序 SHA256", "输入 SHA256", "输出 SHA256", "耗时（秒）", "主机"], trace_rows)}
</section>
<footer>Prisma schema {SCHEMA_VERSION} · 报告由标准库与 openpyxl 生成，可离线打开。</footer>
</main></body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path


def write_reports(
    *,
    project_root: Path,
    run_id: str,
    case_outcomes: dict[str, CaseExecution],
    case_run_dirs: dict[str, Path],
    script_path: Path,
    software: dict,
    mode: str,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    fatal_error: str = "",
    exit_code: int | None = None,
) -> ReportPaths:
    project_root = Path(project_root).resolve()
    report_dir = project_root / "exports" / "reports" / run_id
    chart_dir = report_dir / "charts"
    data = build_report_data(
        project_root=project_root,
        run_id=run_id,
        case_outcomes=case_outcomes,
        case_run_dirs=case_run_dirs,
        script_path=Path(script_path),
        software=software,
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        report_directory=report_dir,
        fatal_error=fatal_error,
        exit_code=exit_code,
    )
    charts = write_charts(data, chart_dir)
    paths = ReportPaths(
        run_id=run_id,
        directory=report_dir,
        run_json=report_dir / "run.json",
        results_json=report_dir / "results.json",
        excel=report_dir / "results.xlsx",
        html=report_dir / "report.html",
        junit=report_dir / "junit.xml",
        charts=charts,
    )
    data["artifacts"] = {
        "directory": str(report_dir),
        "run_json": str(paths.run_json),
        "results_json": str(paths.results_json),
        "excel": str(paths.excel),
        "html": str(paths.html),
        "junit": str(paths.junit),
        "charts": {key: str(path) for key, path in charts.items()},
    }
    write_json_atomic(paths.results_json, data)
    write_excel(data, paths.excel)
    write_junit(data, paths.junit)
    write_html(data, paths.html, charts)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "project": data["project"],
        "script": data["script"],
        "software": data["software"],
        "mode": data["mode"],
        "started_at": data["started_at"],
        "finished_at": data["finished_at"],
        "duration_seconds": data["duration_seconds"],
        "summary": data["summary"],
        "artifacts": data["artifacts"],
    }
    write_json_atomic(paths.run_json, manifest)
    return paths
