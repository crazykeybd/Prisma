"""Project discovery and creation for the multi-software test workspace."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from openpyxl import Workbook

from common.util_prisma import ROOT
from software import (
    SoftwareAdapterError,
    SoftwareLibrary,
    get_adapter,
    normalize_software_key,
    software_catalog,
)


LEGACY_PROJECT_ID = "__legacy__"
PROJECT_VERSION = 1
PROJECT_DIRS = ("data", "inputs", "test_cases", "log", "exports")
PROJECT_HEADERS = ["编号", "运行目录", "action", "object", "参数1", "参数2", "参数3", "参数4"]
NUSOLSYSLMR_CHECKS = (
    ("p@304010000", "8.93893E+05"),
    ("tempf@304010000", "753.142"),
    ("p@364010000", "7.64535E+06"),
    ("tempf@364010000", "466.913"),
)
_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


class ProjectError(ValueError):
    """Raised when a project cannot be created, opened, or changed."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    root: Path
    software_key: str
    software_name: str
    created_at: str | None = None
    updated_at: str | None = None
    description: str = ""
    legacy: bool = False

    def to_dict(
        self, software_library: SoftwareLibrary | None = None
    ) -> dict:
        catalog = {
            item["key"]: item
            for item in software_catalog(software_library)
        }
        software = catalog.get(self.software_key, {})
        try:
            display_path = str(self.root.relative_to(ROOT)) or "."
        except ValueError:
            display_path = str(self.root)
        return {
            "id": self.id,
            "name": self.name,
            "path": display_path,
            "absolute_path": str(self.root),
            "software_key": self.software_key,
            "software_name": software.get("display_name", self.software_name),
            "software_status": software.get("status", "unknown"),
            "software_available": bool(software.get("available")),
            "software_program_ready": bool(software.get("program_ready")),
            "software_state": software.get("state", "unknown"),
            "software_version": software.get("version", ""),
            "software_missing_runtime_files": list(
                software.get("missing_runtime_files") or []
            ),
            "software_library_directory": software.get("library_directory", ""),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "legacy": self.legacy,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_project_name(value: str) -> str:
    """Return a safe Windows folder name or raise a user-facing error."""
    name = str(value or "").strip()
    if not name:
        raise ProjectError("项目名称不能为空")
    if len(name) > 60:
        raise ProjectError("项目名称不能超过 60 个字符")
    if name in (".", "..") or name.startswith("."):
        raise ProjectError("项目名称不能以点开头")
    if name.endswith((" ", ".")):
        raise ProjectError("项目名称不能以空格或点结尾")
    if _INVALID_WINDOWS_CHARS.search(name):
        raise ProjectError('项目名称不能包含 < > : " / \\ | ? * 等字符')
    if _WINDOWS_RESERVED.match(name):
        raise ProjectError(f"项目名称不能使用 Windows 保留名: {name}")
    return name


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _project_config(software_key: str, executable: str) -> dict:
    config = {
        "dataConfigPath": "data/test_data.xlsx",
        "checkConfigPath": "data/test_check.xlsx",
        "inputsDir": "inputs",
        "testCasesDir": "test_cases",
        "logDir": "log",
        "executableName": executable,
        "softwareType": software_key,
        "maxWorkers": 0,
        "runMode": "simulate",
        "runArgs": ["{input}"],
    }
    if software_key == "nusolsyslmr":
        config["nusolsyslmrTimeoutSec"] = 1800
    return config


def _create_starter_workbooks(
    root: Path,
    software_key: str,
    executable_name: str,
    input_name: str,
    output_name: str = "output.txt",
) -> None:
    script = Workbook()
    sheet = script.active
    sheet.title = "testcase"
    sheet.append(PROJECT_HEADERS)
    run_dir = "STARTER-001"
    timeout = "1800" if software_key == "nusolsyslmr" else "600"
    rows = [
        [
            "STARTER-001",
            run_dir,
            "folder_prep",
            "STARTER-001",
            input_name,
            executable_name,
            None,
            None,
        ],
        [
            None,
            run_dir,
            "run_software",
            executable_name,
            software_key,
            input_name,
            output_name,
            timeout,
        ],
    ]
    if software_key == "nusolsyslmr":
        rows.extend(
            [
                None,
                run_dir,
                "check_output",
                output_name,
                key,
                expected,
                software_key,
                None,
            ]
            for key, expected in NUSOLSYSLMR_CHECKS
        )
    for row in rows:
        sheet.append(row)
    sheet.merge_cells(start_row=2, start_column=1, end_row=len(rows) + 1, end_column=1)
    script.save(root / "data" / "test_data.xlsx")
    script.close()

    check = Workbook()
    result_sheet = check.active
    result_sheet.title = "testcheck"
    result_sheet.append(["编号", "实际结果", "结论"])
    result_sheet.append(["STARTER-001", None, None])
    check.save(root / "data" / "test_check.xlsx")
    check.close()


class ProjectStore:
    """Manage the on-disk project catalog and the active project."""

    def __init__(
        self,
        projects_root: Path | None = None,
        software_library: SoftwareLibrary | None = None,
        workspace_root: Path | None = None,
    ):
        self.projects_root = Path(projects_root or (ROOT / "projects")).resolve()
        self.software_library = software_library or SoftwareLibrary()
        self.workspace_root = Path(workspace_root or ROOT).resolve()

    @property
    def active_file(self) -> Path:
        return self.projects_root / ".active"

    def legacy_project(self) -> Project:
        root = self.workspace_root
        metadata: dict = {}
        metadata_path = root / "project.json"
        if metadata_path.is_file():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    metadata = payload
            except (OSError, json.JSONDecodeError):
                metadata = {}
        software_key = str(metadata.get("software_key") or "relap5")
        try:
            adapter = get_adapter(software_key, self.software_library)
        except SoftwareAdapterError:
            adapter = get_adapter("relap5", self.software_library)
        return Project(
            id=LEGACY_PROJECT_ID,
            name=str(metadata.get("name") or "旧版工作区"),
            root=root,
            software_key=adapter.key,
            software_name=adapter.display_name,
            created_at=metadata.get("created_at"),
            updated_at=metadata.get("updated_at"),
            description=str(
                metadata.get("description")
                or "兼容现有 data、inputs、test_cases 和 log 数据。"
            ),
            legacy=True,
        )

    def _metadata_path(self, root: Path) -> Path:
        return root / "project.json"

    def _read_metadata(self, root: Path) -> dict:
        metadata_path = self._metadata_path(root)
        if not metadata_path.is_file():
            raise ProjectError(f"项目缺少 project.json: {root.name}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"项目元数据无法读取: {root.name}") from exc
        if not isinstance(metadata, dict):
            raise ProjectError(f"项目元数据格式无效: {root.name}")
        return metadata

    def _load(self, root: Path) -> Project:
        metadata = self._read_metadata(root)
        software_key = str(metadata.get("software_key") or "")
        try:
            adapter = get_adapter(software_key, self.software_library)
        except SoftwareAdapterError as exc:
            raise ProjectError(f"项目对应的软件未注册: {software_key}") from exc
        return Project(
            id=root.name,
            name=str(metadata.get("name") or root.name),
            root=root,
            software_key=adapter.key,
            software_name=adapter.display_name,
            created_at=metadata.get("created_at"),
            updated_at=metadata.get("updated_at"),
            description=str(metadata.get("description") or ""),
        )

    def _validate_openable_project(self, root: Path) -> Project:
        project = self._load(root)
        config_path = root / "config.yaml"
        if not config_path.is_file():
            raise ProjectError(f"项目缺少 config.yaml: {root.name}")
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProjectError(f"项目配置无法读取: {root.name}") from exc
        if not isinstance(config, dict):
            raise ProjectError(f"项目配置格式无效: {root.name}")
        configured_software = str(
            config.get("softwareType") or config.get("software_type") or ""
        ).strip()
        if not configured_software:
            raise ProjectError(f"项目配置缺少 softwareType: {root.name}")
        try:
            config_adapter = get_adapter(configured_software, self.software_library)
        except SoftwareAdapterError as exc:
            raise ProjectError(
                f"项目配置对应的软件未注册: {configured_software}"
            ) from exc
        if config_adapter.key != project.software_key:
            raise ProjectError("project.json 与 config.yaml 中的被测软件不一致")
        if config_adapter.status != "available":
            raise ProjectError(
                f"{config_adapter.display_name} 尚未实现，不能打开该项目"
            )
        return project

    def projects(self) -> list[Project]:
        if not self.projects_root.is_dir():
            return []
        projects = []
        for root in self.projects_root.iterdir():
            if not root.is_dir() or root.name.startswith("."):
                continue
            try:
                projects.append(self._load(root))
            except ProjectError:
                continue
        return sorted(
            projects,
            key=lambda project: (project.updated_at or "", project.name.casefold()),
            reverse=True,
        )

    def active(self) -> Project:
        if self.active_file.is_file():
            active_id = self.active_file.read_text(encoding="utf-8").strip()
            if active_id == LEGACY_PROJECT_ID:
                return self.legacy_project()
            if active_id:
                try:
                    return self.get(active_id)
                except ProjectError:
                    pass
        return self.legacy_project()

    def set_active(self, project_id: str) -> Project:
        project = self.get(project_id)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.active_file.write_text(project.id + "\n", encoding="utf-8")
        return project

    def get(self, project_id: str) -> Project:
        if project_id == LEGACY_PROJECT_ID:
            return self.legacy_project()
        safe_id = validate_project_name(project_id)
        root = (self.projects_root / safe_id).resolve()
        self._assert_inside_projects(root)
        if not root.is_dir():
            raise ProjectError(f"项目不存在: {project_id}")
        return self._load(root)

    def create(
        self,
        name: str,
        software_key: str,
        description: str = "",
    ) -> Project:
        project_name = validate_project_name(name)
        try:
            adapter = get_adapter(software_key, self.software_library)
        except SoftwareAdapterError as exc:
            raise ProjectError(str(exc)) from exc
        if not adapter.status == "available":
            raise ProjectError(f"{adapter.display_name} 尚未实现，不能创建可运行项目")

        self.projects_root.mkdir(parents=True, exist_ok=True)
        root = (self.projects_root / project_name).resolve()
        self._assert_inside_projects(root)
        if root.exists():
            raise ProjectError(f"同名项目已存在: {project_name}")

        created = False
        try:
            root.mkdir()
            created = True
            for directory in PROJECT_DIRS:
                (root / directory).mkdir()

            assets = self.software_library.describe(adapter)
            executable_name = (
                assets.get("executable_name")
                or getattr(adapter, "executable_name", "")
                or "solver.exe"
            )
            config = _project_config(adapter.key, executable_name)
            (root / "config.yaml").write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            template_files = self.software_library.template_files(adapter.key)
            template_root = self.software_library.entry_root(adapter.key) / "templates"
            if template_files:
                for source in template_files:
                    relative = source.relative_to(template_root)
                    destination = root / "inputs" / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            elif adapter.key == "relap5":
                # 兼容旧工作区：RELAP5 仍附带历史输入卡。
                source_inputs = ROOT / "inputs"
                for filename in ("CheckSGInput.txt", "input_A1.txt"):
                    source = source_inputs / filename
                    if source.is_file():
                        shutil.copy2(source, root / "inputs" / filename)

            input_names = [path.name for path in template_files]
            declared_inputs = [
                str(name)
                for name in (getattr(adapter, "input_filenames", ()) or ())
                if str(name).strip()
            ]
            default_input = declared_inputs[0] if declared_inputs else str(
                getattr(adapter, "input_filename", "") or "input.txt"
            )
            starter_input = next(
                (
                    name for name in input_names
                    if name.casefold() == "checksginput.txt"
                ),
                input_names[0]
                if input_names
                else (default_input if adapter.key != "relap5" else "CheckSGInput.txt"),
            )
            # 用户软件：为每张声明的输入卡建立占位文件，多输入卡软件也能开箱即用。
            existing_inputs = {name.casefold() for name in input_names}
            for name in declared_inputs:
                if name.casefold() in existing_inputs:
                    continue
                placeholder = root / "inputs" / name
                placeholder.parent.mkdir(parents=True, exist_ok=True)
                placeholder.touch()
                existing_inputs.add(name.casefold())
                input_names.append(name)
            default_output = str(
                getattr(adapter, "output_filename", "") or "output.txt"
            )
            _create_starter_workbooks(
                root,
                adapter.key,
                str(config["executableName"]),
                starter_input,
                default_output,
            )

            now = _utc_now()
            metadata = {
                "version": PROJECT_VERSION,
                "name": project_name,
                "software_key": adapter.key,
                "created_at": now,
                "updated_at": now,
                "description": str(description or "").strip(),
            }
            _write_json(self._metadata_path(root), metadata)
        except Exception:
            if created and root.is_dir():
                shutil.rmtree(root)
            raise
        return self.set_active(project_name)

    def update_metadata(
        self,
        project_id: str,
        name: str,
        description: str = "",
        software_key: str | None = None,
    ) -> Project:
        legacy = project_id == LEGACY_PROJECT_ID
        project = self.get(project_id)
        display_name = validate_project_name(name)
        clean_description = str(description or "").strip()
        if len(clean_description) > 200:
            raise ProjectError("项目说明不能超过 200 个字符")

        target_software_key = project.software_key
        target_adapter = None
        if software_key is not None:
            try:
                target_adapter = get_adapter(str(software_key), self.software_library)
            except SoftwareAdapterError as exc:
                raise ProjectError(str(exc)) from exc
            if target_adapter.status != "available":
                raise ProjectError(
                    f"{target_adapter.display_name} 尚未实现，不能设为项目的被测软件"
                )
            target_software_key = target_adapter.key

        if legacy:
            metadata_path = self.workspace_root / "project.json"
            config_path = self.workspace_root / "config" / "config.yaml"
            if metadata_path.is_file():
                try:
                    metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise ProjectError("旧版工作区元数据无法读取") from exc
                if not isinstance(metadata, dict):
                    raise ProjectError("旧版工作区元数据格式无效")
            else:
                metadata = {}
        else:
            metadata = self._read_metadata(project.root)
            metadata_path = self._metadata_path(project.root)
            config_path = project.root / "config.yaml"
        metadata_before = (
            metadata_path.read_bytes() if metadata_path.is_file() else None
        )
        config_before = config_path.read_bytes() if config_path.is_file() else None

        if target_software_key != project.software_key:
            if not config_path.is_file():
                label = "旧版工作区" if legacy else project.root.name
                raise ProjectError(f"{label}缺少 config.yaml")
            try:
                config = yaml.safe_load(config_before.decode("utf-8"))
            except (UnicodeDecodeError, yaml.YAMLError) as exc:
                raise ProjectError(f"项目配置无法读取: {project.root.name}") from exc
            if not isinstance(config, dict):
                raise ProjectError(f"项目配置格式无效: {project.root.name}")
            assets = self.software_library.describe(target_adapter)
            executable_name = (
                assets.get("executable_name")
                or getattr(target_adapter, "executable_name", "")
                or "solver.exe"
            )
            config["softwareType"] = target_software_key
            config["executableName"] = executable_name
            if target_software_key == "nusolsyslmr":
                config.setdefault("nusolsyslmrTimeoutSec", 1800)
            config_text = yaml.safe_dump(
                config, allow_unicode=True, sort_keys=False
            )
        else:
            config_text = None

        metadata["version"] = PROJECT_VERSION
        metadata["name"] = display_name
        metadata["description"] = clean_description
        metadata["software_key"] = target_software_key
        metadata["created_at"] = str(
            metadata.get("created_at") or _utc_now()
        )
        metadata["updated_at"] = _utc_now()
        try:
            if config_text is not None:
                temporary = config_path.with_suffix(".yaml.tmp")
                temporary.write_text(config_text, encoding="utf-8")
                temporary.replace(config_path)
            _write_json(metadata_path, metadata)
        except Exception:
            if config_before is not None:
                config_path.write_bytes(config_before)
            if metadata_before is None:
                metadata_path.unlink(missing_ok=True)
            else:
                metadata_path.write_bytes(metadata_before)
            raise
        return self.get(project.id)

    def open_existing(self, path: str | Path) -> Project:
        """Activate an internal project or import an external project copy."""
        if not str(path or "").strip():
            raise ProjectError("请选择已有项目")
        source = Path(path).expanduser().resolve()
        if source.is_file():
            if source.name.casefold() != "project.json":
                raise ProjectError("请选择项目文件夹或其中的 project.json")
            source = source.parent
        if not source.is_dir():
            raise ProjectError(f"项目文件夹不存在: {source}")
        self._validate_openable_project(source)

        try:
            source.relative_to(self.projects_root)
            if source.parent != self.projects_root:
                raise ProjectError("请选择项目库中的项目文件夹，或选择外部项目根目录")
            return self.set_active(source.name)
        except ValueError:
            pass

        project_name = validate_project_name(source.name)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        destination = (self.projects_root / project_name).resolve()
        self._assert_inside_projects(destination)
        if destination.exists():
            raise ProjectError(f"同名项目已存在: {project_name}")

        staging = (self.projects_root / f".import-{uuid4().hex}").resolve()
        self._assert_inside_projects(staging)
        try:
            shutil.copytree(source, staging)
            self._validate_openable_project(staging)
            staging.rename(destination)
        except Exception:
            if staging.is_dir():
                shutil.rmtree(staging)
            raise
        return self.set_active(destination.name)

    def delete(self, project_id: str) -> None:
        if project_id == LEGACY_PROJECT_ID:
            raise ProjectError("旧版工作区不能通过项目管理删除")
        project = self.get(project_id)
        active = self.active()
        self._assert_inside_projects(project.root)
        if project.root == self.projects_root:
            raise ProjectError("不能删除项目根目录")
        shutil.rmtree(project.root)
        if active.id == project.id:
            self.set_active(LEGACY_PROJECT_ID)

    def resolve_member(
        self,
        project: Project,
        value: str | Path,
        *,
        allowed_directories: tuple[str, ...] | None = None,
    ) -> Path:
        candidate = Path(str(value or ".").replace("\\", "/"))
        if not candidate.is_absolute():
            candidate = project.root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(project.root)
        except ValueError as exc:
            raise ProjectError("路径必须位于当前项目内") from exc
        if allowed_directories:
            allowed_roots = [(project.root / name).resolve() for name in allowed_directories]
            if not any(
                candidate == allowed_root or allowed_root in candidate.parents
                for allowed_root in allowed_roots
            ):
                raise ProjectError("该文件类型只能放在项目的测试目录中")
        return candidate

    def _assert_inside_projects(self, path: Path) -> None:
        try:
            path.relative_to(self.projects_root)
        except ValueError as exc:
            raise ProjectError("项目路径越界") from exc

