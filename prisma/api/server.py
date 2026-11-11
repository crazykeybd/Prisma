"""多软件自动化测试平台 HTTP API（纯标准库 + openpyxl，供 Tauri 前端调用）。"""

import argparse
import json
import logging
import re
import shutil
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from openpyxl import Workbook, load_workbook

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.input_deck_parser import (
    FORMAT_PROFILES,
    parse_input_file as parse_generic_input_file,
    parse_input_text as parse_generic_input_text,
    search_input_file as _search_input_file,
)
from common.run_context import RunContext
from common.relap_input_parser import parse_relap_input_file, parse_relap_input_text
from common.results import ExecutionSummary, utc_now
from common.util_prisma import (
    CONFIG_PATH,
    ROOT,
    init_log,
    read_config,
    release_log_file,
    resolve_path,
)
from project_store import LEGACY_PROJECT_ID, Project, ProjectError, ProjectStore
from run_prisma.executor import execute_testcase
from software import (
    SoftwareAdapterError,
    SoftwareLibrary,
    SoftwareLibraryError,
    get_adapter,
    software_catalog,
)
from software.executables import resolve_executable
from api.script_wizard import (
    ScriptWizardError,
    build_cases,
    wizard_defaults,
)
from keywords.keyword_store import (
    BUILTIN_KEYWORDS,
    COMMAND_IMPL,
    IMPL_CHOICES,
    KeywordError,
    all_keywords,
    delete_custom as delete_custom_keyword,
    find_custom as find_custom_keyword,
    save_custom as save_custom_keyword,
)

LOCK = threading.Lock()
SOFTWARE_LIBRARY = SoftwareLibrary()
STORE = ProjectStore(software_library=SOFTWARE_LIBRARY)
STATE = {
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
LOGS = deque(maxlen=2000)
LOG_SEQ = 0
RESULT_VALUE_HEADERS = {
    "期望值": ("期望值", "期望结果"),
    "实际值": ("实际值", "实际结果"),
}

# 每个字段的 index 指向 read_script/save_script 返回的七个数据单元格：
# 0=运行目录, 1=action, 2=object, 3..6=参数 1..4。
# 内置关键词定义在 keywords/keyword_store.py，项目自定义关键词追加在其后。
KEYWORDS = BUILTIN_KEYWORDS

HEADERS = ["编号", "运行目录", "action", "object", "参数1", "参数2", "参数3", "参数4"]


class BufferLogHandler(logging.Handler):
    def emit(self, record):
        global LOG_SEQ
        LOG_SEQ += 1
        LOGS.append({"seq": LOG_SEQ, "text": self.format(record)})


def _rel(p, base=None):
    p = Path(p).resolve()
    try:
        return str(p.relative_to((base or ROOT).resolve())) or "."
    except ValueError:
        return str(p)


def _active_scope():
    project = STORE.active()
    config_path = (
        CONFIG_PATH if project.legacy else project.root / "config.yaml"
    ).resolve()
    return project, project.root.resolve(), config_path


def _project_config_path(project: Project) -> Path:
    return CONFIG_PATH if project.legacy else project.root / "config.yaml"


def _resolve_member(project: Project, value, fallback=None) -> Path:
    raw = value if value not in (None, "") else fallback
    if raw in (None, ""):
        raise ProjectError("文件路径不能为空")
    if project.legacy:
        return resolve_path(raw, project.root)
    return STORE.resolve_member(project, raw)


def _project_rel(project: Project, value) -> str:
    return _rel(value, project.root)


def project_relative_path(path, require_input=False, project=None) -> dict:
    project = project or STORE.active()
    candidate = _resolve_member(project, path)
    try:
        relative_path = candidate.relative_to(project.root.resolve()).as_posix()
    except ValueError as exc:
        raise ProjectError("路径必须位于当前项目内") from exc
    if not candidate.is_file():
        raise ProjectError(f"文件不存在: {path}")
    inputs_root = (project.root / "inputs").resolve()
    try:
        input_path = candidate.relative_to(inputs_root).as_posix()
    except ValueError:
        input_path = ""
    if require_input and not input_path:
        raise ProjectError("主输入卡必须选择当前项目 inputs 目录中的文件")
    return {
        "ok": True,
        "path": relative_path,
        "input_path": input_path,
    }


def _project_payload(project: Project) -> dict:
    return project.to_dict(STORE.software_library)


def _projects_payload() -> dict:
    active = STORE.active()
    projects = [STORE.legacy_project(), *STORE.projects()]
    return {
        "current": _project_payload(active),
        "projects": [
            {
                **_project_payload(project),
                "current": project.id == active.id,
            }
            for project in projects
        ],
        "projects_root": _rel(STORE.projects_root),
    }


def _software_payload() -> dict:
    SOFTWARE_LIBRARY.ensure_root()
    return {
        "software": software_catalog(SOFTWARE_LIBRARY),
        "library_root": _rel(SOFTWARE_LIBRARY.root),
        "library_root_absolute": str(SOFTWARE_LIBRARY.root),
    }


def _reset_run_state(project: Project) -> None:
    if STATE["running"]:
        raise ProjectError("任务运行期间不能切换或删除项目")
    STATE["order"] = []
    STATE["steps"] = {}
    STATE["summary"] = None
    STATE["project_id"] = project.id
    STATE["run_id"] = None
    STATE["reports"] = None
    STATE["report_directory"] = None
    STATE["html_report"] = None
    STATE["exit_code"] = None
    STATE["execution_summary"] = None


def _assert_project_management_available(action: str) -> None:
    if STATE["running"]:
        raise ProjectError(f"任务运行期间不能{action}项目")


def _load_cases(script):
    commands = read_table_safe(script)
    totals = {}
    order = []
    for cmd in commands:
        case_no = str(cmd[0])
        if case_no not in totals:
            totals[case_no] = 0
            order.append(case_no)
        totals[case_no] += 1
    return order, totals


def read_table_safe(script):
    from common.util_prisma import read_table
    return read_table(script)



# ---------- Input-deck parsing helpers ----------


def _parse_generic_input(path, input_format="", display_root=None):
    p = resolve_path(path)
    if not p or not p.is_file():
        return {"error": f"输入卡不存在: {path}"}
    return parse_generic_input_file(
        p, display_path=_rel(p, display_root), input_format=input_format or None
    )


def search_generic_input_file(
    path, query="", limit=200, search_all=True, input_format="", display_root=None
):
    p = resolve_path(path)
    if not p or not p.is_file():
        return {"ok": False, "error": f"输入卡不存在: {path}"}
    return _search_input_file(
        p,
        query,
        limit,
        search_all=search_all,
        input_format=input_format or None,
        display_path=_rel(p, display_root),
    )

def parse_relap_input(path, display_root=None):
    """Parse a RELAP5 input deck while preserving every non-empty line."""
    p = resolve_path(path)
    if not p or not p.is_file():
        return {"error": f"输入卡不存在: {path}"}
    return parse_relap_input_file(p, display_path=_rel(p, display_root))


def search_relap_input_file(
    path, query="", limit=200, search_all=False, display_root=None
):
    """Search parsed card fields and return stable in-place edit targets."""
    p = resolve_path(path)
    if not p or not p.is_file():
        return {"ok": False, "error": f"输入卡不存在: {path}"}
    parsed = parse_relap_input_file(p, display_path=_rel(p, display_root))
    if parsed.get("error"):
        return {"ok": False, "error": parsed["error"]}
    terms = [part.lower() for part in str(query or "").strip().split() if part]
    max_results = max(1, min(int(limit or 200), 1000))
    results = []
    for entry in parsed.get("entries", []):
        if entry.get("kind") != "card" or not entry.get("effective"):
            continue
        fields = entry.get("fields") or []
        haystack = " ".join([str(entry.get("line_no", "")), str(entry.get("card_id") or ""), str(entry.get("keyword") or ""), str(entry.get("raw") or ""), *fields]).lower()
        if terms and not all(term in haystack for term in terms):
            continue
        token_indexes = range(len(fields)) if search_all else ([0] if fields else [])
        for index in token_indexes:
            value = fields[index]
            target_keyword = entry.get("target_keyword") or entry.get("keyword") or "-"
            results.append({
                "target": f"{entry.get('card_id')}:{target_keyword}:{index + 1}@{entry.get('line_no')}",
                "line_no": entry.get("line_no"), "card_id": entry.get("card_id"),
                "field_line_no": (
                    (entry.get("field_line_nos") or [])[index]
                    if index < len(entry.get("field_line_nos") or [])
                    else entry.get("line_no")
                ),
                "keyword": entry.get("keyword"), "target_keyword": target_keyword,
                "field": index + 1, "value": value,
                "raw": entry.get("raw", ""), "format": entry.get("format", ""),
                "category": entry.get("category", ""), "path": _rel(p, display_root),
                "field_count": len(fields), "fields": fields,
            })
            if len(results) >= max_results:
                return {"ok": True, "path": _rel(p, display_root), "filename": p.name, "count": len(results), "results": results, "summary": parsed.get("summary") or {}}
    return {"ok": True, "path": _rel(p, display_root), "filename": p.name, "count": len(results), "results": results, "summary": parsed.get("summary") or {}}



def parse_relap_output(path, max_lines=2000, display_root=None):
    """Parse RELAP5 output text into sections and key lines."""
    p = resolve_path(path)
    if not p or not p.is_file():
        return {"error": f"输出文件不存在: {path}"}
    sections = {
        "input_listing": False,
        "processing_info": False,
        "input_success": False,
        "major_edit_count": 0,
        "hydrodynamic_edit": False,
        "heat_structure_edit": False,
        "errors": [],
    }
    lines_out = []
    for i, raw in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.rstrip("\n")
        if len(lines_out) < max_lines:
            lines_out.append(line)
        s = line.strip()
        if not s:
            continue
        lower = s.lower()
        if "listing of input data" in lower:
            sections["input_listing"] = True
        if "input processing completed successfully" in lower:
            sections["input_success"] = True
        if "major edit" in lower:
            sections["major_edit_count"] += 1
        if "edit of hydrodynamic components after input processing" in lower:
            sections["hydrodynamic_edit"] = True
        if "edit of heat structures after input processing" in lower:
            sections["heat_structure_edit"] = True
        if "error" in lower or "fatal" in lower or "severe (" in lower:
            sections["errors"].append(line)
        elif re.match(r"^\s*\d*\*{6,}\s*\S", s) and "Input processing completed" not in s:
            sections["errors"].append(line)
    summary = {
        "path": _rel(p, display_root),
        "has_input_listing": sections["input_listing"],
        "input_success": sections["input_success"],
        "major_edit_count": sections["major_edit_count"],
        "has_hydrodynamic_edit": sections["hydrodynamic_edit"],
        "has_heat_structure_edit": sections["heat_structure_edit"],
        "error_count": len(sections["errors"]),
    }
    return {"summary": summary, "errors": sections["errors"], "lines": lines_out}

# ---------- script editor ----------

def read_script(path, display_root=None):
    p = resolve_path(path)
    if not p.is_file():
        return {"error": f"文件不存在: {p}"}
    wb = load_workbook(p, data_only=True)
    name = next((n for n in wb.sheetnames if n.lower() == "testcase"), None)
    if not name:
        wb.close()
        return {"error": "缺少名为 testcase 的 sheet"}
    ws = wb[name]
    cases = []
    cur = None
    for row in ws.iter_rows(min_row=2):
        values = [c.value for c in row]
        if not any(v is not None and str(v).strip() != "" for v in values):
            continue
        no = "" if values[0] is None else str(values[0]).strip()
        cells = []
        for v in values[1:9]:
            cells.append("" if v is None else str(v).strip())
        while len(cells) < 7:
            cells.append("")
        if no or cur is None:
            cur = {"no": no, "rows": []}
            cases.append(cur)
        cur["rows"].append(cells[:7])
    wb.close()
    return {"path": _rel(p, display_root), "cases": cases}


def save_script(path, cases, display_root=None):
    p = resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "testcase"
    ws.append(HEADERS)
    r = 2
    for case in cases:
        start = r
        rows = case.get("rows") or []
        if not rows:
            continue
        for row in rows:
            ws.cell(r, 1, case.get("no", "") if r == start else None)
            for i in range(7):
                v = row[i] if i < len(row) else ""
                ws.cell(r, i + 2, "" if v is None else str(v))
            r += 1
        if r - 1 > start:
            ws.merge_cells(start_row=start, start_column=1, end_row=r - 1, end_column=1)
    wb.save(p)
    wb.close()
    return {"ok": True, "path": _rel(p, display_root)}


def read_results(path, display_root=None):
    p = resolve_path(path)
    if not p.is_file():
        return {"error": f"文件不存在: {p}"}
    wb = load_workbook(p, data_only=True, read_only=True)
    ws = wb.active
    rows = [[("" if c.value is None else str(c.value)) for c in row] for row in ws.iter_rows()]
    wb.close()
    if not rows:
        rows = [["期望值", "实际值"]]
    else:
        header = [str(value).strip() for value in rows[0]]
        while header and not header[-1]:
            header.pop()
        for row in rows[1:]:
            del row[len(header):]
        for canonical, aliases in RESULT_VALUE_HEADERS.items():
            index = next(
                (i for i, value in enumerate(header) if value in aliases),
                None,
            )
            if index is not None:
                header[index] = canonical
                continue
            insert_at = len(header)
            if "结论" in header:
                insert_at = header.index("结论")
            header.insert(insert_at, canonical)
            for row in rows[1:]:
                row.insert(insert_at, "")
        rows[0] = header
    return {"path": _rel(p, display_root), "rows": rows}


def list_files(rel_path, boundary=None):
    boundary = Path(boundary or ROOT).resolve()
    base = resolve_path(rel_path or ".", boundary)
    try:
        base.relative_to(boundary)
    except ValueError as exc:
        raise ProjectError("目录必须位于当前项目内") from exc
    if not base.is_dir():
        return {"error": f"目录不存在: {base}"}
    entries = []
    try:
        for item in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith("."):
                continue
            size = item.stat().st_size if item.is_file() else None
            entries.append({"name": item.name, "dir": item.is_dir(), "size": size})
    except PermissionError:
        return {"error": "无权限访问该目录"}
    return {"path": _rel(base, boundary),
            "parent": _rel(base.parent, boundary) if base != boundary else None,
            "entries": entries}


def read_logfile(lines=300, project=None):
    project = project or STORE.active()
    p = project.root / "log" / "prisma_run.log"
    if not p.is_file():
        return {"text": ""}
    data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"text": "\n".join(data[-int(lines):])}


def clear_logfile(project=None):
    global LOG_SEQ
    project = project or STORE.active()
    path = project.root / "log" / "prisma_run.log"
    with LOCK:
        release_log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        init_log(path.parent)
        LOGS.clear()
        LOG_SEQ = 0
    return {"ok": True, "text": ""}


# ---------- run control ----------

def start_run(script, check, exe, mode, workers):
    with LOCK:
        if STATE["running"]:
            return False, "已有任务在运行"
        project, root, config_path = _active_scope()
        script_path = _resolve_member(project, script)
        check_path = _resolve_member(project, check)
        if not script_path.is_file():
            return False, f"测试剧本不存在: {_project_rel(project, script_path)}"
        if not check_path.is_file():
            return False, f"结果表不存在: {_project_rel(project, check_path)}"
        if exe:
            exe_path = Path(exe)
            if exe_path.is_file():
                inputs_dir = root / "inputs"
                inputs_dir.mkdir(parents=True, exist_ok=True)
                target = STORE.resolve_member(
                    project, inputs_dir / exe_path.name
                ) if not project.legacy else inputs_dir / exe_path.name
                if not target.exists():
                    shutil.copy2(exe_path, target)

        cfg = read_config(config_path)
        cfg["dataConfigPath"] = _project_rel(project, script_path)
        cfg["checkConfigPath"] = _project_rel(project, check_path)
        cfg["runMode"] = str(mode or "simulate")
        cfg["maxWorkers"] = int(workers or 0)
        if exe and Path(exe).is_file():
            cfg["executableName"] = Path(exe).name
        config_path.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

        order, totals = _load_cases(script_path)
        STATE["running"] = True
        STATE["stop_event"].clear()
        STATE["order"] = order
        STATE["steps"] = {c: {"index": 0, "total": totals[c], "ok": True, "done": False}
                          for c in order}
        STATE["summary"] = None
        STATE["project_id"] = project.id
        STATE["run_id"] = None
        STATE["reports"] = None
        STATE["report_directory"] = None
        STATE["html_report"] = None
        STATE["exit_code"] = None
        STATE["execution_summary"] = None
        init_log(root / "log")

    def progress(case_no, index, total, ok):
        step = STATE["steps"].get(case_no)
        if step is not None:
            step["index"] = index
            step["total"] = total
            step["ok"] = bool(ok)
            step["done"] = (not ok) or index >= total

    def worker():
        started_at = utc_now()
        try:
            summary = execute_testcase(
                file_path=str(script_path), max_workers=int(workers or 0),
                stop_event=STATE["stop_event"], progress_cb=progress,
                project_root=root,
                config_path=config_path,
                return_summary=True,
            )
        except Exception as exc:
            logging.getLogger("prisma").exception("run failed: %s", exc)
            finished_at = utc_now()
            summary = ExecutionSummary(
                results={},
                outcomes={},
                run_id=f"api-error-{finished_at}",
                started_at=started_at,
                finished_at=finished_at,
                fatal_error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            reports = summary.reports.to_dict() if summary.reports else None
            STATE["summary"] = dict(summary.results)
            STATE["run_id"] = summary.run_id
            STATE["reports"] = reports
            STATE["report_directory"] = (
                reports.get("directory") if reports else None
            )
            STATE["html_report"] = reports.get("html") if reports else None
            STATE["exit_code"] = summary.exit_code
            STATE["execution_summary"] = summary.to_dict()
            STATE["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True, "started"


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self._send("ok")

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        q = lambda k: qs.get(k, [""])[0]
        try:
            project, root, config_path = _active_scope()
            if url.path == "/api/config":
                cfg = read_config(config_path)
                executable_name = str(
                    cfg.get("executableName")
                    or getattr(
                        get_adapter(
                            str(cfg.get("softwareType") or "relap5"),
                            SOFTWARE_LIBRARY,
                        ),
                        "executable_name",
                        "",
                    )
                    or ""
                )
                exe = resolve_executable(
                    executable_name,
                    root / "test_cases",
                    str(cfg.get("softwareType") or "relap5"),
                    project_root=root,
                    library=SOFTWARE_LIBRARY,
                )
                self._send({"config": cfg, "exe": str(exe) if exe else ""})
            elif url.path == "/api/projects":
                self._send(_projects_payload())
            elif url.path == "/api/cases":
                script = q("script") or str(
                    read_config(config_path).get("dataConfigPath") or ""
                )
                self._send_script_cases(script, project)
            elif url.path == "/api/script":
                if not q("path"):
                    raise ProjectError("剧本路径不能为空")
                self._send(read_script(
                    _resolve_member(project, q("path")), project.root
                ))
            elif url.path == "/api/results":
                result_path = q("path") or str(
                    read_config(config_path).get("checkConfigPath") or ""
                )
                self._send(read_results(
                    _resolve_member(project, result_path), project.root
                ))
            elif url.path == "/api/keywords":
                self._send({
                    "keywords": all_keywords(project.root),
                    "software": software_catalog(SOFTWARE_LIBRARY),
                    "builtin_actions": list(IMPL_CHOICES),
                    "command_impl": COMMAND_IMPL,
                    "keyword_file": "keywords.json",
                })
            elif url.path == "/api/script/wizard":
                self._send(wizard_defaults(project, SOFTWARE_LIBRARY))
            elif url.path == "/api/software":
                self._send(_software_payload())
            elif url.path == "/api/files":
                self._send(list_files(q("path"), project.root))
            elif url.path == "/api/logfile":
                self._send(read_logfile(q("lines") or 300, project))
            elif url.path == "/api/status":
                self._send({"running": STATE["running"], "order": STATE["order"],
                            "steps": STATE["steps"], "summary": STATE["summary"],
                            "project_id": STATE["project_id"],
                            "run_id": STATE["run_id"],
                            "reports": STATE["reports"],
                            "report_directory": STATE["report_directory"],
                            "html_report": STATE["html_report"],
                            "exit_code": STATE["exit_code"],
                            "execution_summary": STATE["execution_summary"]})
            elif url.path == "/api/input/formats":
                self._send({"formats": list(FORMAT_PROFILES.values())})
            elif url.path == "/api/input/search":
                self._send(search_generic_input_file(
                    _resolve_member(project, q("path")),
                    q("q"),
                    int(q("limit") or 200),
                    q("all") not in ("0", "false"),
                    q("format") or q("profile"),
                    display_root=project.root,
                ))
            elif url.path == "/api/input":
                self._send(_parse_generic_input(
                    _resolve_member(project, q("path")),
                    q("format") or q("profile"),
                    display_root=project.root,
                ))
            elif url.path == "/api/relap/input/search":
                self._send(search_relap_input_file(
                    _resolve_member(project, q("path")),
                    q("q"),
                    int(q("limit") or 200),
                    q("all") in ("1", "true"),
                    display_root=project.root,
                ))
            elif url.path == "/api/relap/input":
                self._send(parse_relap_input(
                    _resolve_member(project, q("path")), project.root
                ))
            elif url.path == "/api/relap/output":
                self._send(parse_relap_output(
                    _resolve_member(project, q("path")),
                    int(q("lines") or 2000),
                    project.root,
                ))
            elif url.path == "/api/logs":
                after = int(q("after") or 0)
                self._send({"lines": [l for l in LOGS if l["seq"] > after]})
            else:
                self._send({"error": "not found"}, 404)
        except ProjectError as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": str(exc)}, 500)

    def _send_script_cases(self, script, project):
        if not script:
            self._send({"error": "script required"}, 400)
            return
        try:
            order, totals = _load_cases(_resolve_member(project, script))
            self._send({"cases": [{"no": c, "total": totals[c]} for c in order]})
        except ProjectError as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": str(exc)}, 400)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/projects/create":
                _assert_project_management_available("新建")
                project = STORE.create(
                    data.get("name"),
                    data.get("software_key"),
                    data.get("description") or "",
                )
                _reset_run_state(project)
                self._send({"ok": True, "current": _project_payload(project)})
            elif path == "/api/projects/activate":
                project_id = str(data.get("project_id") or "")
                project = STORE.get(project_id)
                _reset_run_state(project)
                project = STORE.set_active(project_id)
                self._send({"ok": True, "current": _project_payload(project)})
            elif path == "/api/projects/open":
                _assert_project_management_available("打开")
                project = STORE.open_existing(data.get("path"))
                _reset_run_state(project)
                self._send({"ok": True, "current": _project_payload(project)})
            elif path == "/api/projects/save":
                _assert_project_management_available("保存")
                project = STORE.update_metadata(
                    str(data.get("project_id") or ""),
                    data.get("name"),
                    data.get("description") or "",
                    data.get("software_key"),
                )
                self._send({
                    "ok": True,
                    "project": _project_payload(project),
                    "current": _project_payload(STORE.active()),
                })
            elif path == "/api/projects/delete":
                project_id = str(data.get("project_id") or "")
                project = STORE.get(project_id)
                confirm_name = str(data.get("confirm_name") or "").strip()
                if confirm_name != project.name:
                    raise ProjectError("请输入完整项目名称以确认删除")
                if STATE["running"] and STATE["project_id"] == project.id:
                    raise ProjectError("任务运行期间不能删除当前项目")
                release_log_file()
                STORE.delete(project_id)
                current = STORE.active()
                _reset_run_state(current)
                self._send({"ok": True, "current": _project_payload(current)})
            elif path == "/api/software/import":
                if STATE["running"]:
                    raise ProjectError("任务运行期间不能导入或更新被测软件")
                source_path = str(data.get("path") or "").strip()
                if not source_path:
                    raise ProjectError("请选择要导入的程序或专用程序文件夹")
                try:
                    adapter = get_adapter(
                        str(data.get("software_key") or ""), SOFTWARE_LIBRARY
                    )
                except SoftwareAdapterError as exc:
                    raise ProjectError(str(exc)) from exc
                if adapter.status != "available":
                    raise ProjectError(
                        f"{adapter.display_name} 尚未实现，不能导入为可运行软件"
                    )
                try:
                    imported = SOFTWARE_LIBRARY.import_source(
                        adapter,
                        source_path,
                        data.get("version") or "",
                    )
                except SoftwareLibraryError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({
                    "ok": True,
                    "software": imported,
                    **_software_payload(),
                })
            elif path == "/api/software/create":
                if STATE["running"]:
                    raise ProjectError("任务运行期间不能新增被测软件")
                try:
                    definition = SOFTWARE_LIBRARY.create_custom_definition(data)
                except SoftwareLibraryError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({
                    "ok": True,
                    "definition": definition,
                    "software": next(
                        (
                            item for item in software_catalog(SOFTWARE_LIBRARY)
                            if item["key"] == definition["software_key"]
                        ),
                        None,
                    ),
                })
            elif path == "/api/software/update":
                if STATE["running"]:
                    raise ProjectError("任务运行期间不能修改被测软件配置")
                software_key = str(data.get("software_key") or "").strip()
                try:
                    adapter = get_adapter(software_key, SOFTWARE_LIBRARY)
                except SoftwareAdapterError as exc:
                    raise ProjectError(str(exc)) from exc
                try:
                    definition = SOFTWARE_LIBRARY.update_definition(adapter, data)
                except SoftwareLibraryError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({
                    "ok": True,
                    "definition": definition,
                    "software": next(
                        (
                            item for item in software_catalog(SOFTWARE_LIBRARY)
                            if item["key"] == definition["software_key"]
                        ),
                        None,
                    ),
                })
            elif path == "/api/software/delete":
                if STATE["running"]:
                    raise ProjectError("任务运行期间不能删除被测软件")
                software_key = str(data.get("software_key") or "").strip()
                try:
                    adapter = get_adapter(software_key, SOFTWARE_LIBRARY)
                except SoftwareAdapterError as exc:
                    raise ProjectError(str(exc)) from exc
                if not getattr(adapter, "user_defined", False):
                    raise ProjectError("内置软件不能删除")
                in_use = [
                    project.name
                    for project in STORE.projects()
                    if project.software_key == adapter.key
                ]
                if in_use:
                    raise ProjectError(
                        "以下项目仍在使用该软件，请先删除项目: "
                        + "、".join(in_use)
                    )
                try:
                    SOFTWARE_LIBRARY.remove_custom_definition(adapter.key)
                except SoftwareLibraryError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({"ok": True, **_software_payload()})
            elif path == "/api/stop":
                STATE["stop_event"].set()
                self._send({"ok": True})
            elif path == "/api/logs/clear":
                if STATE["running"]:
                    raise ProjectError("任务运行期间不能清空日志")
                project, _, _ = _active_scope()
                self._send(clear_logfile(project))
            elif path == "/api/run":
                ok, msg = start_run(
                    data.get("script"), data.get("check"), data.get("exe"),
                    data.get("mode"), data.get("workers"))
                self._send({"ok": ok, "message": msg}, 200 if ok else 400)
            elif path == "/api/path/project-relative":
                project, _, _ = _active_scope()
                self._send(project_relative_path(
                    data.get("path"),
                    require_input=bool(data.get("require_input")),
                    project=project,
                ))
            elif path == "/api/relap/input/parse":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    self._send({"error": "请输入 RELAP5 输入卡内容"}, 400)
                    return
                filename = str(data.get("filename") or "pasted.i").strip() or "pasted.i"
                self._send(parse_relap_input_text(text, filename=filename))
            elif path == "/api/input/parse":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    self._send({"error": "请输入输入卡内容"}, 400)
                    return
                filename = (
                    str(data.get("filename") or "pasted.txt").strip()
                    or "pasted.txt"
                )
                self._send(parse_generic_input_text(
                    text,
                    filename=filename,
                    input_format=data.get("format") or data.get("profile"),
                ))
            elif path == "/api/script/save":
                project, _, _ = _active_scope()
                script_path = _resolve_member(project, data.get("path"))
                self._send(save_script(
                    script_path, data.get("cases") or [], project.root
                ))
            elif path == "/api/script/wizard/preview":
                project, _, _ = _active_scope()
                try:
                    cases = build_cases(
                        data.get("spec") or {}, project, SOFTWARE_LIBRARY
                    )
                except ScriptWizardError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({"ok": True, "cases": cases})
            elif path == "/api/script/wizard/create":
                project, root, _ = _active_scope()
                script_path = _resolve_member(
                    project, data.get("path") or "data/test_data.xlsx"
                )
                try:
                    cases = build_cases(
                        data.get("spec") or {}, project, SOFTWARE_LIBRARY
                    )
                except ScriptWizardError as exc:
                    raise ProjectError(str(exc)) from exc
                if script_path.exists() and not data.get("overwrite"):
                    raise ProjectError(
                        "剧本文件已存在: "
                        + _project_rel(project, script_path)
                        + "；请确认覆盖或换一个文件名"
                    )
                script_path.parent.mkdir(parents=True, exist_ok=True)
                result = save_script(script_path, cases, root)
                self._send({**result, "cases": cases})
            elif path == "/api/script/saveas":
                project, root, _ = _active_scope()
                src = _resolve_member(project, data.get("path"))
                dst = root / "data" / "test_data.xlsx"
                if not project.legacy:
                    STORE.resolve_member(project, dst)
                shutil.copy2(src, dst)
                self._send({"ok": True, "path": _project_rel(project, dst)})
            elif path == "/api/keywords/save":
                project, root, _ = _active_scope()
                try:
                    keyword = save_custom_keyword(
                        root,
                        data.get("keyword") or {},
                        original_name=data.get("original_name") or "",
                    )
                except KeywordError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({
                    "ok": True,
                    "keyword": keyword,
                    "keywords": all_keywords(root),
                    "path": _project_rel(project, root / "keywords.json"),
                })
            elif path == "/api/keywords/delete":
                project, root, _ = _active_scope()
                try:
                    delete_custom_keyword(root, data.get("name") or "")
                except KeywordError as exc:
                    raise ProjectError(str(exc)) from exc
                self._send({
                    "ok": True,
                    "keywords": all_keywords(root),
                    "path": _project_rel(project, root / "keywords.json"),
                })
            elif path == "/api/config/save":
                project, _, config_path = _active_scope()
                cfg = data.get("config") or {}
                if not isinstance(cfg, dict):
                    raise ProjectError("配置必须是键值对象")
                config_path.write_text(
                    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
                self._send({"ok": True, "current": _project_payload(project)})
            else:
                self._send({"error": "not found"}, 404)
        except ProjectError as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    init_log()
    logger = logging.getLogger("prisma")
    handler = BufferLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"API server on http://127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
