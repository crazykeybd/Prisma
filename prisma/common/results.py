"""Unified result models, comparison metrics and trace helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import socket
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
ARTIFACT_DIR_NAME = ".prisma"
_JSONL_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: str | Path | None) -> str:
    """Return the SHA256 of a file, or an empty string when it is absent."""
    if path is None:
        return ""
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(
    path: str | Path | None,
    *,
    base: str | Path | None = None,
    role: str = "",
) -> dict | None:
    """Describe an existing artifact with a stable relative path and hash."""
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    resolved = candidate.resolve()
    display_path = str(resolved)
    if base is not None:
        try:
            display_path = resolved.relative_to(Path(base).resolve()).as_posix()
        except (OSError, ValueError):
            pass
    try:
        size = resolved.stat().st_size
    except OSError:
        size = 0
    return {
        "role": str(role or ""),
        "path": display_path,
        "absolute_path": str(resolved),
        "size": size,
        "sha256": sha256_file(resolved),
    }


def screen_excerpt(path: str | Path | None, limit: int = 8000) -> str:
    """Return the tail of a text console log without failing the test run."""
    if path is None:
        return ""
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    try:
        text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max(1, int(limit)) :]


def write_json_atomic(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def append_jsonl(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _JSONL_LOCK:
        with target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
    return target


def read_jsonl(path: str | Path) -> list[dict]:
    target = Path(path)
    if not target.is_file():
        return []
    records: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def artifact_dir(run_dir: str | Path) -> Path:
    return Path(run_dir) / ARTIFACT_DIR_NAME


def reset_case_artifacts(run_dir: str | Path) -> Path:
    """Start a clean evidence set while retaining prior run history."""
    target = artifact_dir(run_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("run.json", "checks.jsonl", "execution.json"):
        (target / name).unlink(missing_ok=True)
    return target


def write_run_trace(run_dir: str | Path, trace: dict) -> Path:
    target = artifact_dir(run_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": utc_now(),
        **trace,
    }
    write_json_atomic(target / "run.json", payload)
    append_jsonl(target / "runs.jsonl", payload)
    return target / "run.json"


def write_check_record(run_dir: str | Path, record: dict) -> Path:
    target = artifact_dir(run_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": utc_now(),
        **record,
    }
    append_jsonl(target / "checks.jsonl", payload)
    return target / "checks.jsonl"


def write_execution_record(run_dir: str | Path, record: dict) -> Path:
    target = artifact_dir(run_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": utc_now(),
        **record,
    }
    write_json_atomic(target / "execution.json", payload)
    return target / "execution.json"


def latest_check_status(run_dir: str | Path) -> str:
    records = read_jsonl(artifact_dir(run_dir) / "checks.jsonl")
    return str(records[-1].get("status") or "") if records else ""


def environment_summary() -> dict:
    """Collect non-secret host/runtime information used for reproducibility."""
    names = (
        "COMPUTERNAME",
        "USERNAME",
        "USERDOMAIN",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "COMSPEC",
        "PYTHONPATH",
    )
    selected = {
        name: str(os.environ.get(name) or "")
        for name in names
        if str(os.environ.get(name) or "").strip()
    }
    return {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "variables": selected,
    }


_PARAMETER_UNITS = {
    "p": "Pa",
    "pressure": "Pa",
    "tempf": "degF",
    "tempg": "K",
    "mflowj": "kg/s",
    "mflow": "kg/s",
    "rho": "kg/m3",
    "voidf": "1",
    "voidg": "1",
}


def result_point(
    value: Any,
    *,
    parameter: str = "",
    coordinate: str = "",
    unit: str = "",
    data_type: str = "",
    metadata: dict | None = None,
) -> dict:
    text = "" if value is None else str(value).strip()
    if not data_type:
        try:
            float(text)
            data_type = "number"
        except (TypeError, ValueError):
            data_type = "string"
    return {
        "value": value,
        "parameter": str(parameter or ""),
        "coordinate": str(coordinate or ""),
        "unit": str(unit or ""),
        "data_type": data_type,
        "metadata": dict(metadata or {}),
    }


def point_from_key(value: Any, key: str, metadata: dict | None = None) -> dict:
    text = str(key or "").strip()
    parameter, separator, coordinate = text.partition("@")
    if not separator:
        coordinate = ""
    parameter = parameter.strip()
    return result_point(
        value,
        parameter=parameter,
        coordinate=coordinate.strip(),
        unit=_PARAMETER_UNITS.get(parameter.casefold(), ""),
        metadata={"key": text, **(metadata or {})},
    )


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _point_identity(point: dict, index: int) -> str:
    metadata = point.get("metadata") or {}
    key = str(metadata.get("key") or "").strip()
    parameter = str(point.get("parameter") or "").strip()
    coordinate = str(point.get("coordinate") or "").strip()
    if key:
        return key
    if parameter or coordinate:
        return f"{parameter}@{coordinate}"
    return f"#{index}"


def align_points(
    current: Sequence[dict],
    baseline: Sequence[dict],
) -> list[tuple[str, dict, dict | None]]:
    """Align points by result key/coordinate, falling back to series order."""
    baseline_by_key: dict[str, list[dict]] = {}
    for index, point in enumerate(baseline):
        baseline_by_key.setdefault(_point_identity(point, index), []).append(point)
    aligned: list[tuple[str, dict, dict | None]] = []
    consumed: dict[str, int] = {}
    for index, point in enumerate(current):
        key = _point_identity(point, index)
        bucket = baseline_by_key.get(key, [])
        position = consumed.get(key, 0)
        counterpart = bucket[position] if position < len(bucket) else None
        consumed[key] = position + 1
        aligned.append((key, point, counterpart))
    return aligned


def _ks_two_sample(first: Sequence[float], second: Sequence[float]) -> tuple[float, float]:
    if not first or not second:
        return 0.0, 1.0
    a = sorted(first)
    b = sorted(second)
    values = sorted(set(a + b))
    count_a = 0
    count_b = 0
    position_a = 0
    position_b = 0
    statistic = 0.0
    for value in values:
        # Count all ties in one ECDF step so the statistic follows the
        # two-sample Kolmogorov-Smirnov definition.
        while position_a < len(a) and a[position_a] <= value:
            count_a += 1
            position_a += 1
        while position_b < len(b) and b[position_b] <= value:
            count_b += 1
            position_b += 1
        statistic = max(statistic, abs(count_a / len(a) - count_b / len(b)))
    effective = len(a) * len(b) / (len(a) + len(b))
    if effective <= 0:
        return statistic, 1.0
    scaled = (math.sqrt(effective) + 0.12 + 0.11 / math.sqrt(effective)) * statistic
    if scaled <= 0:
        return 0.0, 1.0
    p_value = 2.0 * sum(
        ((-1) ** (index - 1)) * math.exp(-2.0 * index * index * scaled * scaled)
        for index in range(1, 101)
    )
    return statistic, max(0.0, min(1.0, p_value))


def _pearson(values: Sequence[tuple[float, float]]) -> float | None:
    if len(values) < 2:
        return None
    mean_a = sum(item[0] for item in values) / len(values)
    mean_b = sum(item[1] for item in values) / len(values)
    numerator = sum(
        (first - mean_a) * (second - mean_b) for first, second in values
    )
    variance_a = sum((first - mean_a) ** 2 for first, _ in values)
    variance_b = sum((second - mean_b) ** 2 for _, second in values)
    denominator = math.sqrt(variance_a * variance_b)
    if denominator <= 0:
        return None
    return max(-1.0, min(1.0, numerator / denominator))


def _relative_error(actual: float, expected: float) -> float:
    denominator = max(abs(expected), 1e-300)
    return abs(actual - expected) / denominator


def compare_points(
    current: Sequence[dict],
    baseline: Sequence[dict],
    *,
    rtol: float = 1e-4,
    atol: float = 1e-6,
) -> dict:
    """Compare two point series and return metrics, anomalies and intervals."""
    aligned = align_points(current, baseline)
    positions: list[dict] = []
    xy: list[tuple[float, float]] = []
    current_numbers: list[float] = []
    baseline_numbers: list[float] = []
    missing = 0
    matched = 0
    for index, (key, actual_point, expected_point) in enumerate(aligned, start=1):
        actual_raw = actual_point.get("value")
        expected_raw = expected_point.get("value") if expected_point else None
        actual_number = _as_number(actual_point.get("value"))
        expected_number = _as_number(expected_raw)

        if actual_raw is None or expected_raw is None:
            missing += 1
            positions.append(
                {
                    "index": index,
                    "key": key,
                    "parameter": actual_point.get("parameter", ""),
                    "coordinate": actual_point.get("coordinate", ""),
                    "unit": actual_point.get("unit", ""),
                    "actual": actual_raw,
                    "baseline": expected_raw,
                    "comparison_kind": "missing",
                    "anomaly": True,
                }
            )
            continue

        matched += 1
        if actual_number is not None and expected_number is not None:
            difference = actual_number - expected_number
            tolerance = max(float(atol), float(rtol) * abs(expected_number))
            failed = abs(difference) > tolerance
            relative = _relative_error(actual_number, expected_number)
            current_numbers.append(actual_number)
            baseline_numbers.append(expected_number)
            xy.append((actual_number, expected_number))
            positions.append(
                {
                    "index": index,
                    "key": key,
                    "parameter": actual_point.get("parameter", ""),
                    "coordinate": actual_point.get("coordinate", ""),
                    "unit": actual_point.get("unit", ""),
                    "actual": actual_number,
                    "baseline": expected_number,
                    "comparison_kind": "number",
                    "absolute_error": abs(difference),
                    "signed_error": difference,
                    "relative_error": relative,
                    "tolerance": tolerance,
                    "anomaly": failed,
                }
            )
            continue

        actual_text = str(actual_raw).strip()
        expected_text = str(expected_raw).strip()
        failed = actual_text != expected_text
        positions.append(
            {
                "index": index,
                "key": key,
                "parameter": actual_point.get("parameter", ""),
                "coordinate": actual_point.get("coordinate", ""),
                "unit": actual_point.get("unit", ""),
                "actual": actual_text,
                "baseline": expected_text,
                "comparison_kind": "string",
                "exact_match": not failed,
                "anomaly": failed,
            }
        )

    numeric_positions = [
        item for item in positions if item.get("comparison_kind") == "number"
    ]
    errors = [abs(item["signed_error"]) for item in numeric_positions]
    outliers = [item for item in positions if item["anomaly"]]
    intervals: list[dict] = []
    for item in outliers:
        index = int(item["index"])
        if intervals and index == int(intervals[-1]["end"]) + 1:
            intervals[-1]["end"] = index
            intervals[-1]["length"] += 1
            intervals[-1]["keys"].append(item["key"])
        else:
            intervals.append(
                {
                    "start": index,
                    "end": index,
                    "length": 1,
                    "keys": [item["key"]],
                }
            )

    count = matched
    numeric_count = len(numeric_positions)
    signed_relative = [
        item["signed_error"] / max(abs(item["baseline"]), 1e-300)
        for item in numeric_positions
    ]
    positive = sum(1 for value in signed_relative if value > 0)
    negative = sum(1 for value in signed_relative if value < 0)
    ks_statistic, ks_p_value = _ks_two_sample(current_numbers, baseline_numbers)
    return {
        "count": count,
        "numeric_count": numeric_count,
        "missing": missing,
        "passed": missing == 0 and not outliers,
        "mse": sum(value * value for value in errors) / numeric_count
        if numeric_count
        else None,
        "rmse": math.sqrt(sum(value * value for value in errors) / numeric_count)
        if numeric_count
        else None,
        "mae": sum(errors) / numeric_count if numeric_count else None,
        "max_abs_error": max(errors) if errors else None,
        "max_relative_error": max(
            (item["relative_error"] for item in numeric_positions), default=None
        ),
        "mean_signed_error": (
            sum(item["signed_error"] for item in numeric_positions) / numeric_count
            if numeric_count
            else None
        ),
        "systematic_bias": (
            sum(signed_relative) / numeric_count if numeric_count else None
        ),
        "systematic_bias_ratio": (
            max(positive, negative) / numeric_count if numeric_count else None
        ),
        "pearson": _pearson(xy),
        "ks_statistic": ks_statistic,
        "ks_p_value": ks_p_value,
        "anomaly_count": len(outliers),
        "anomalies": outliers,
        "anomaly_intervals": intervals,
        "points": positions,
        "rtol": float(rtol),
        "atol": float(atol),
    }


def safe_token(value: str, fallback: str = "case") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return text[:120] or fallback


@dataclass
class CaseExecution:
    case_no: str
    passed: bool
    run_dir: str = ""
    failure_type: str = "none"
    failed_step: int | None = None
    failed_action: str = ""
    error_message: str = ""
    commands_total: int = 0
    commands_completed: int = 0
    stopped: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReportPaths:
    run_id: str
    directory: Path
    run_json: Path
    results_json: Path
    excel: Path
    html: Path
    junit: Path
    charts: dict[str, Path] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "directory": str(self.directory),
            "run_json": str(self.run_json),
            "results_json": str(self.results_json),
            "excel": str(self.excel),
            "html": str(self.html),
            "junit": str(self.junit),
            "charts": {key: str(value) for key, value in self.charts.items()},
        }


@dataclass
class ExecutionSummary:
    results: dict[str, bool]
    outcomes: dict[str, CaseExecution]
    run_id: str
    reports: ReportPaths | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    fatal_error: str = ""
    report_error: str = ""

    @property
    def execution_failed(self) -> bool:
        return any(
            item.failure_type == "execution" or item.stopped
            for item in self.outcomes.values()
        )

    @property
    def comparison_failed(self) -> bool:
        return any(item.failure_type == "comparison" for item in self.outcomes.values())

    @property
    def exit_code(self) -> int:
        if self.fatal_error or self.report_error or self.execution_failed:
            return 2
        if self.comparison_failed or not all(self.results.values()):
            return 1
        return 0

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "results": dict(self.results),
            "outcomes": {
                case_no: outcome.to_dict()
                for case_no, outcome in self.outcomes.items()
            },
            "run_id": self.run_id,
            "reports": self.reports.to_dict() if self.reports else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "execution_failed": self.execution_failed,
            "comparison_failed": self.comparison_failed,
            "fatal_error": self.fatal_error,
            "report_error": self.report_error,
        }
