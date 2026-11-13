from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from common.reporting import write_reports
from common.results import (
    CaseExecution,
    ExecutionSummary,
    latest_check_status,
    read_json,
    read_jsonl,
    reset_case_artifacts,
    safe_token,
    utc_now,
    write_execution_record,
)
from common.run_context import RunContext
from common.util_prisma import (
    CONFIG_PATH,
    ROOT,
    init_log,
    read_config,
    read_table,
    resolve_path,
)
from keywords.keyword_lib import execute_command
from software import SoftwareLibrary, get_adapter


def consumer(
    commands, case_no=None, stop_event=None, progress_cb=None, context=None
):
    total = len(commands)
    for index, command in enumerate(commands, start=1):
        if stop_event is not None and stop_event.is_set():
            return False
        ok = execute_command(command, context=context)
        if progress_cb is not None:
            progress_cb(case_no or str(command[0]), index, total, bool(ok))
        if not ok:
            return False
    return True


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _case_run_dir(context: RunContext, commands: list[list[str]], case_no: str):
    value = commands[0][1] if commands and len(commands[0]) > 1 else case_no
    try:
        return context.resolve(
            value,
            context.root / "test_cases",
            label="运行目录",
        )
    except Exception:
        return (
            context.root
            / "test_cases"
            / safe_token(str(value or case_no))
        ).resolve()


def _reset_case_run_dir(run_dir: Path, project_root: Path) -> None:
    cases_root = (Path(project_root).resolve() / "test_cases").resolve()
    target = Path(run_dir).resolve()
    if target == cases_root or cases_root not in target.parents:
        raise ValueError("运行目录必须是 test_cases 下的用例子目录")
    if target.exists():
        if not target.is_dir():
            raise ValueError("运行目录路径不是文件夹")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def _failure_detail(run_dir: Path) -> str:
    checks = read_jsonl(run_dir / ".prisma" / "checks.jsonl")
    if checks:
        error = str(checks[-1].get("error") or "").strip()
        if error:
            return error
        comparison = checks[-1].get("comparison") or {}
        if comparison:
            relative = comparison.get("max_relative_error")
            if relative is not None:
                return f"结果对比不通过，最大相对误差 {relative}"
    run = read_json(run_dir / ".prisma" / "run.json", default={}) or {}
    return str(run.get("error") or "").strip()


def _write_case_execution(
    run_dir: Path,
    *,
    run_id: str,
    case_no: str,
    passed: bool,
    failure_type: str,
    failed_step: int | None,
    failed_action: str,
    error_message: str,
    commands_total: int,
    commands_completed: int,
    stopped: bool,
    started_at: str,
    started_clock: float,
) -> None:
    write_execution_record(
        run_dir,
        {
            "run_id": run_id,
            "case_no": case_no,
            "status": "passed" if passed else ("skipped" if stopped else "failed"),
            "passed": bool(passed),
            "failure_type": failure_type,
            "failed_step": failed_step,
            "failed_action": failed_action,
            "error_message": error_message,
            "commands_total": commands_total,
            "commands_completed": commands_completed,
            "stopped": stopped,
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_seconds": round(
                max(0.0, time.monotonic() - started_clock), 6
            ),
        },
    )


def _execute_case(
    commands,
    case_no,
    run_id,
    stop_event=None,
    progress_cb=None,
    context=None,
):
    started_at = utc_now()
    started_clock = time.monotonic()
    total = len(commands)
    completed = 0
    run_dir = _case_run_dir(context, commands, case_no)

    try:
        _reset_case_run_dir(run_dir, context.root if context else ROOT)
        reset_case_artifacts(run_dir)
    except Exception as exc:
        _write_case_execution(
            run_dir,
            run_id=run_id,
            case_no=case_no,
            passed=False,
            failure_type="execution",
            failed_step=1,
            failed_action="",
            error_message=f"无法初始化运行目录: {exc}",
            commands_total=total,
            commands_completed=0,
            stopped=False,
            started_at=started_at,
            started_clock=started_clock,
        )
        return CaseExecution(
            case_no=case_no,
            passed=False,
            run_dir=str(run_dir),
            failure_type="execution",
            failed_step=1,
            error_message=f"无法初始化运行目录: {exc}",
            commands_total=total,
        )

    for index, command in enumerate(commands, start=1):
        action = str(command[2]) if len(command) > 2 else ""
        if stop_event is not None and stop_event.is_set():
            error_message = "用户请求停止执行"
            _write_case_execution(
                run_dir,
                run_id=run_id,
                case_no=case_no,
                passed=False,
                failure_type="execution",
                failed_step=index,
                failed_action=action,
                error_message=error_message,
                commands_total=total,
                commands_completed=completed,
                stopped=True,
                started_at=started_at,
                started_clock=started_clock,
            )
            return CaseExecution(
                case_no=case_no,
                passed=False,
                run_dir=str(run_dir),
                failure_type="execution",
                failed_step=index,
                failed_action=action,
                error_message=error_message,
                commands_total=total,
                commands_completed=completed,
                stopped=True,
            )

        try:
            ok = bool(execute_command(command, context=context))
        except Exception as exc:
            ok = False
            error_message = f"{type(exc).__name__}: {exc}"
        else:
            error_message = ""

        if not ok:
            status = latest_check_status(run_dir)
            failure_type = "comparison" if status == "failed" else "execution"
            detail = error_message or _failure_detail(run_dir)
            if not detail:
                detail = (
                    "期望值对比不通过"
                    if failure_type == "comparison"
                    else "步骤执行失败"
                )
            _write_case_execution(
                run_dir,
                run_id=run_id,
                case_no=case_no,
                passed=False,
                failure_type=failure_type,
                failed_step=index,
                failed_action=action,
                error_message=detail,
                commands_total=total,
                commands_completed=completed,
                stopped=False,
                started_at=started_at,
                started_clock=started_clock,
            )
            if progress_cb is not None:
                progress_cb(case_no, index, total, False)
            return CaseExecution(
                case_no=case_no,
                passed=False,
                run_dir=str(run_dir),
                failure_type=failure_type,
                failed_step=index,
                failed_action=action,
                error_message=detail,
                commands_total=total,
                commands_completed=completed,
            )

        completed += 1
        if progress_cb is not None:
            progress_cb(case_no, index, total, True)

    _write_case_execution(
        run_dir,
        run_id=run_id,
        case_no=case_no,
        passed=True,
        failure_type="none",
        failed_step=None,
        failed_action="",
        error_message="",
        commands_total=total,
        commands_completed=completed,
        stopped=False,
        started_at=started_at,
        started_clock=started_clock,
    )
    return CaseExecution(
        case_no=case_no,
        passed=True,
        run_dir=str(run_dir),
        commands_total=total,
        commands_completed=completed,
    )


def _software_details(config: dict) -> dict:
    software_key = str(config.get("softwareType") or "relap5").strip() or "relap5"
    try:
        adapter = get_adapter(software_key)
    except Exception:
        return {
            "key": software_key,
            "display_name": software_key,
            "version": "",
        }
    try:
        assets = SoftwareLibrary().describe(adapter)
    except Exception:
        assets = {}
    return {
        "key": adapter.key,
        "display_name": getattr(adapter, "display_name", adapter.key),
        "version": str(assets.get("version") or ""),
        "executable": str(
            assets.get("executable_name")
            or getattr(adapter, "executable_name", "")
            or ""
        ),
        "input_filename": str(
            getattr(adapter, "input_filename", "") or ""
        ),
        "output_filename": str(
            getattr(adapter, "output_filename", "") or ""
        ),
        "timeout_sec": int(getattr(adapter, "timeout_sec", 0) or 0),
        "library_directory": str(assets.get("library_directory") or ""),
        "program_ready": bool(assets.get("program_ready")),
    }


def execute_testcase(
    file_path=None,
    max_workers=None,
    stop_event=None,
    progress_cb=None,
    project_root=None,
    config_path=None,
    *,
    return_summary=False,
):
    root = Path(project_root).expanduser().resolve() if project_root else ROOT.resolve()
    if config_path:
        cfg_path = Path(config_path).expanduser()
        if not cfg_path.is_absolute():
            cfg_path = root / cfg_path
        cfg_path = cfg_path.resolve()
    elif (root / "config.yaml").is_file():
        cfg_path = (root / "config.yaml").resolve()
    else:
        cfg_path = CONFIG_PATH.resolve()

    logger = init_log(root / "log")
    context = RunContext(root=root, config_path=cfg_path)
    run_id = _new_run_id()
    started_at = utc_now()
    started_clock = time.monotonic()
    config = {}
    outcomes = {}
    case_run_dirs = {}
    fatal_error = ""
    report_error = ""
    script_path = root / "data" / "test_data.xlsx"

    try:
        config = read_config(cfg_path)
        requested_script = (
            file_path
            if file_path is not None
            else config.get("dataConfigPath")
        )
        if requested_script in (None, ""):
            raise ValueError("配置缺少 dataConfigPath，且未显式指定剧本")
        script_path = resolve_path(requested_script, root)
        commands = read_table(script_path)
        if not commands:
            raise ValueError(f"剧本没有可执行命令: {script_path}")

        groups = {}
        for command in commands:
            if not command:
                raise ValueError("剧本包含空命令行")
            groups.setdefault(str(command[0]), []).append(command)

        for case_no, case_commands in groups.items():
            case_run_dirs[case_no] = _case_run_dir(
                context, case_commands, case_no
            )

        if max_workers is None:
            configured = int(config.get("maxWorkers", 0) or 0)
            max_workers = configured if configured > 0 else (os.cpu_count() or 1)
        max_workers = max(1, min(int(max_workers), len(groups)))

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _execute_case,
                    case_commands,
                    case_no,
                    run_id,
                    stop_event,
                    progress_cb,
                    context,
                ): case_no
                for case_no, case_commands in groups.items()
            }
            wait(futures)
            for future in futures:
                case_no = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:
                    logger.exception("[%s] task failed: %s", case_no, exc)
                    run_dir = case_run_dirs[case_no]
                    outcome = CaseExecution(
                        case_no=case_no,
                        passed=False,
                        run_dir=str(run_dir),
                        failure_type="execution",
                        failed_step=1,
                        error_message=f"{type(exc).__name__}: {exc}",
                        commands_total=len(groups[case_no]),
                    )
                    try:
                        reset_case_artifacts(run_dir)
                        _write_case_execution(
                            run_dir,
                            run_id=run_id,
                            case_no=case_no,
                            passed=False,
                            failure_type="execution",
                            failed_step=outcome.failed_step,
                            failed_action="",
                            error_message=outcome.error_message,
                            commands_total=outcome.commands_total,
                            commands_completed=0,
                            stopped=False,
                            started_at=started_at,
                            started_clock=started_clock,
                        )
                    except Exception:
                        logger.exception(
                            "[%s] cannot write task failure evidence", case_no
                        )
                outcomes[case_no] = outcome
    except Exception as exc:
        fatal_error = f"{type(exc).__name__}: {exc}"
        logger.exception("test run failed before completion: %s", exc)

    results = {
        case_no: bool(outcome.passed)
        for case_no, outcome in outcomes.items()
    }
    finished_at = utc_now()
    duration_seconds = round(max(0.0, time.monotonic() - started_clock), 6)
    execution_failed = any(
        item.failure_type == "execution" or item.stopped
        for item in outcomes.values()
    )
    comparison_failed = any(
        item.failure_type == "comparison" for item in outcomes.values()
    )
    exit_code = (
        2
        if fatal_error or execution_failed
        else 1
        if comparison_failed or not all(results.values())
        else 0
    )
    software = _software_details(config)
    mode = str(config.get("runMode") or "simulate")
    reports = None
    try:
        reports = write_reports(
            project_root=root,
            run_id=run_id,
            case_outcomes=outcomes,
            case_run_dirs=case_run_dirs,
            script_path=script_path,
            software=software,
            mode=mode,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            fatal_error=fatal_error,
            exit_code=exit_code,
        )
    except Exception as exc:
        report_error = f"{type(exc).__name__}: {exc}"
        logger.exception("cannot generate reports: %s", exc)

    summary = ExecutionSummary(
        results=results,
        outcomes=outcomes,
        run_id=run_id,
        reports=reports,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        fatal_error=fatal_error,
        report_error=report_error,
    )
    logger.info(
        "summary: exit_code=%s results=%s run_id=%s",
        summary.exit_code,
        results,
        run_id,
    )
    return summary if return_summary else summary.results


if __name__ == "__main__":
    print(execute_testcase())
