import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from common.run_context import RunContext
from common.util_prisma import init_log, read_config, read_table, resolve_path
from keywords.keyword_lib import execute_command


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


def execute_testcase(
    file_path=None,
    max_workers=None,
    stop_event=None,
    progress_cb=None,
    project_root=None,
    config_path=None,
):
    root = Path(project_root).resolve() if project_root else None
    cfg_path = Path(config_path).resolve() if config_path else None
    logger = init_log(root / "log" if root else None)
    config = read_config(cfg_path)
    if file_path is None:
        file_path = resolve_path(config.get("dataConfigPath"), root)
    else:
        file_path = resolve_path(file_path, root)
    context = RunContext(root=root, config_path=cfg_path) if root else None
    commands = read_table(file_path)
    if not commands:
        logger.warning("no commands loaded from %s", file_path)
        return {}
    groups = defaultdict(list)
    for command in commands:
        groups[str(command[0])].append(command)
    if max_workers is None:
        configured = int(config.get("maxWorkers", 0) or 0)
        max_workers = configured if configured > 0 else (os.cpu_count() or 1)
    max_workers = max(1, min(max_workers, len(groups)))
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                consumer, cmds, case_no, stop_event, progress_cb, context
            ): case_no
            for case_no, cmds in groups.items()
        }
        done, _ = wait(futures)
    for future in done:
        case_no = futures[future]
        try:
            results[case_no] = bool(future.result())
        except Exception as exc:
            logger.exception("[%s] task failed: %s", case_no, exc)
            results[case_no] = False
    logger.info("summary: %s", results)
    return results


if __name__ == "__main__":
    print(execute_testcase())
