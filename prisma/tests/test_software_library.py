from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.util_prisma import release_log_file
from project_store import ProjectStore
from software import SoftwareLibrary, SoftwareLibraryError, get_adapter, software_catalog
from software.executables import destination_executable, resolve_executable
from software.library import executable_name_variants, is_linux_executable


def write_pe(path: Path, payload: bytes = b"MZ test executable") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def write_elf(path: Path, payload: bytes = b"\x7fELF test executable") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class SoftwareLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.relap5 = get_adapter("relap5")

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def _import_relap5(self, source: Path, version: str = "") -> dict:
        return self.library.import_source(self.relap5, source, version)

    def _install_nusolsyslmr(self, runtime_files=()) -> Path:
        entry = self.library.entry_root("nusolsyslmr")
        executable = write_pe(entry / "bin" / "nusolsyslmr.exe")
        for name in runtime_files:
            (entry / "bin" / name).write_bytes(b"DLL")
        (entry / "software.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "software_key": "nusolsyslmr",
                    "display_name": "NUSOL-SYSLMR",
                    "version": "NUSOLSYS-LMVer:1.1.1",
                    "executable": "bin/nusolsyslmr.exe",
                    "templates": [],
                }
            ),
            encoding="utf-8",
        )
        return executable

    def test_empty_library_reports_not_installed(self):
        catalog = {item["key"]: item for item in software_catalog(self.library)}

        relap5 = catalog["relap5"]
        self.assertFalse(relap5["installed"])
        self.assertFalse(relap5["program_ready"])
        self.assertEqual(relap5["state"], "not_installed")
        self.assertEqual(catalog["neutron_physics"]["state"], "reserved")

    def test_nusolsyslmr_reports_missing_runtime_dependencies(self):
        adapter = get_adapter("nusolsyslmr")
        self._install_nusolsyslmr()

        described = self.library.describe(adapter)

        self.assertEqual(described["state"], "missing_runtime")
        self.assertFalse(described["program_ready"])
        self.assertFalse(described["runtime_ready"])
        self.assertEqual(
            described["missing_runtime_files"],
            list(adapter.required_runtime_files),
        )

    def test_nusolsyslmr_is_ready_when_runtime_dependencies_exist(self):
        adapter = get_adapter("nusolsyslmr")
        self._install_nusolsyslmr(adapter.required_runtime_files)

        described = self.library.describe(adapter)

        self.assertEqual(described["state"], "ready")
        self.assertTrue(described["program_ready"])
        self.assertTrue(described["runtime_ready"])
        self.assertEqual(described["missing_runtime_files"], [])
        self.assertEqual(
            described["version"],
            "NUSOLSYS-LMVer:1.1.1",
        )

    def test_import_copies_runtime_bundle_and_filters_generated_files(self):
        source = self.base / "RELAP5"
        executable = write_pe(source / "relap5.exe")
        (source / "tpfh2o").write_bytes(b"property table")
        (source / "runtime").mkdir()
        (source / "runtime" / "ifcore.dll").write_bytes(b"DLL")
        (source / "notes.xlsx").write_bytes(b"spreadsheet")
        (source / "README.md").write_text("notes", encoding="utf-8")
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "cache.pyc").write_bytes(b"cache")

        imported = self._import_relap5(executable, "3.3")

        entry = self.library.entry_root("relap5")
        self.assertEqual(imported["state"], "ready")
        self.assertTrue(imported["program_ready"])
        self.assertEqual(imported["version"], "3.3")
        self.assertEqual(
            self.library.resolve_executable("relap5", "relap5.exe"),
            entry / "bin" / "relap5.exe",
        )
        self.assertTrue((entry / "bin" / "tpfh2o").is_file())
        self.assertTrue((entry / "bin" / "runtime" / "ifcore.dll").is_file())
        self.assertFalse((entry / "bin" / "notes.xlsx").exists())
        self.assertFalse((entry / "bin" / "README.md").exists())
        self.assertFalse((entry / "bin" / "__pycache__").exists())

    def test_program_update_preserves_templates(self):
        source = self.base / "RELAP5"
        executable = write_pe(source / "relap5.exe")
        self._import_relap5(executable, "1.0")
        template = self.library.entry_root("relap5") / "templates" / "input.i"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text("TEMPLATE", encoding="utf-8")

        imported = self._import_relap5(executable, "2.0")

        self.assertEqual(imported["version"], "2.0")
        self.assertEqual(template.read_text(encoding="utf-8"), "TEMPLATE")
        self.assertEqual(
            self.library.template_files("relap5"),
            [template],
        )

    def test_invalid_and_ambiguous_program_sources_are_rejected(self):
        with self.assertRaisesRegex(SoftwareLibraryError, "请选择"):
            self.library.import_source(self.relap5, "")
        with self.assertRaisesRegex(SoftwareLibraryError, "请选择"):
            self.library.import_source(self.relap5, None)

        empty = write_pe(self.base / "empty" / "relap5.exe", b"")
        with self.assertRaisesRegex(SoftwareLibraryError, "有效"):
            self._import_relap5(empty)

        wrong_type = self.base / "wrong" / "relap5.txt"
        wrong_type.parent.mkdir()
        wrong_type.write_text("MZ", encoding="utf-8")
        with self.assertRaisesRegex(SoftwareLibraryError, ".exe"):
            self._import_relap5(wrong_type)

        ambiguous = self.base / "ambiguous"
        write_pe(ambiguous / "first.exe")
        write_pe(ambiguous / "second.exe")
        with self.assertRaisesRegex(SoftwareLibraryError, "多个"):
            self._import_relap5(ambiguous)

    def test_linux_elf_import_accepts_exe_less_program_name(self):
        source = self.base / "RELAP5-linux"
        executable = write_elf(source / "relap5")

        with patch("software.library.is_supported_executable", return_value=True):
            imported = self._import_relap5(executable, "linux")

        manifest = json.loads(
            (
                self.library.entry_root("relap5") / "software.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(imported["state"], "ready")
        self.assertEqual(manifest["executable"], "bin/relap5")

    def test_linux_elf_without_execute_permission_is_rejected(self):
        source = write_elf(self.base / "RELAP5-linux" / "relap5")

        with patch("software.library.os.access", return_value=False):
            self.assertFalse(is_linux_executable(source))
        with patch("software.library.os.access", return_value=True):
            self.assertTrue(is_linux_executable(source))

    def test_executable_name_variants_cover_windows_and_linux_names(self):
        self.assertEqual(
            executable_name_variants("relap5.exe", "relap5"),
            ["relap5.exe", "relap5"],
        )

    def test_manifest_cannot_resolve_outside_the_software_entry(self):
        entry = self.library.entry_root("relap5")
        entry.mkdir(parents=True)
        outside = write_pe(self.library.root / "outside.exe")
        (entry / "software.json").write_text(
            json.dumps({"executable": "../outside.exe"}),
            encoding="utf-8",
        )

        self.assertIsNone(
            self.library.resolve_executable("relap5", "relap5.exe")
        )
        self.assertTrue(outside.is_file())
        self.assertEqual(
            self.library.describe(self.relap5)["state"],
            "missing_program",
        )

    def test_project_creation_uses_library_templates_and_status(self):
        source = self.base / "RELAP5"
        executable = write_pe(source / "relap5.exe")
        self._import_relap5(executable)
        template = self.library.entry_root("relap5") / "templates" / "custom.i"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text("CUSTOM DECK", encoding="utf-8")

        store = ProjectStore(
            self.base / "projects",
            software_library=self.library,
        )
        project = store.create("Library project", "relap5")

        self.assertEqual(
            (project.root / "inputs" / "custom.i").read_text(encoding="utf-8"),
            "CUSTOM DECK",
        )
        payload = project.to_dict(self.library)
        self.assertTrue(payload["software_program_ready"])
        self.assertEqual(payload["software_version"], "")

    def test_all_builtin_and_reserved_software_definitions_are_editable(self):
        for key in (
            "relap5",
            "nusolsyslmr",
            "neutron_physics",
            "subchannel",
            "fuel_performance",
        ):
            with self.subTest(key=key):
                adapter = get_adapter(key, self.library)
                entry = self.library.entry_root(key)
                template = entry / "templates" / "case.tpl"
                template.parent.mkdir(parents=True, exist_ok=True)
                template.write_text("TEMPLATE", encoding="utf-8")
                executable = None
                if adapter.executable_name:
                    executable = write_pe(entry / "bin" / adapter.executable_name)
                    if key == "nusolsyslmr":
                        for name in adapter.required_runtime_files:
                            (entry / "bin" / name).write_bytes(b"DLL")

                updated = self.library.update_definition(
                    adapter,
                    {
                        "software_key": key,
                        "display_name": f"{key}-edited",
                        "description": f"{key} 可编辑说明",
                        "version": "9.8.7",
                        "arguments": ["--input", "{input}", "--output", "{output}"],
                        "input_filenames": ["main.inp", "auxiliary.dat"],
                        "support_files": ["runtime/*.dll"],
                        "output_filename": "result.out",
                        "timeout_sec": 4321,
                    },
                )

                configured = get_adapter(key, self.library)
                catalog = {
                    item["key"]: item
                    for item in software_catalog(self.library)
                }[key]
                self.assertEqual(updated["display_name"], f"{key}-edited")
                self.assertEqual(configured.display_name, f"{key}-edited")
                self.assertEqual(
                    configured.arguments,
                    ("--input", "{input}", "--output", "{output}"),
                )
                self.assertEqual(
                    configured.input_filenames, ("main.inp", "auxiliary.dat")
                )
                self.assertEqual(configured.support_files, ("runtime/*.dll",))
                self.assertEqual(configured.output_filename, "result.out")
                self.assertEqual(configured.timeout_sec, 4321)
                self.assertEqual(catalog["display_name"], f"{key}-edited")
                self.assertEqual(catalog["version"], "9.8.7")
                self.assertEqual(catalog["timeout_sec"], 4321)
                self.assertTrue(template.is_file())
                if executable is not None:
                    self.assertTrue(executable.is_file())
                    self.assertEqual(
                        catalog["executable_name"],
                        adapter.executable_name,
                    )

    def test_reserved_software_stays_reserved_after_update(self):
        adapter = get_adapter("neutron_physics", self.library)
        self.library.update_definition(
            adapter,
            {
                "display_name": "中子物理-已配置",
                "description": "仍是预留接口",
                "timeout_sec": 900,
            },
        )

        configured = get_adapter("neutron_physics", self.library)
        catalog = {
            item["key"]: item for item in software_catalog(self.library)
        }["neutron_physics"]

        self.assertEqual(configured.display_name, "中子物理-已配置")
        self.assertEqual(configured.status, "reserved")
        self.assertFalse(catalog["available"])
        self.assertEqual(catalog["state"], "reserved")

    def test_project_executable_wins_before_library_fallback(self):
        source = self.base / "RELAP5"
        library_executable = write_pe(source / "relap5.exe")
        self._import_relap5(library_executable)
        project = self.base / "project"
        run_dir = project / "test_cases" / "case-1"
        run_dir.mkdir(parents=True)
        local_executable = write_pe(project / "inputs" / "relap5" / "relap5.exe")

        with patch(
            "software.executables.SoftwareLibrary",
            return_value=self.library,
        ):
            resolved = resolve_executable(
                "relap5.exe",
                run_dir,
                "relap5",
                project_root=project,
            )
            local_executable.unlink()
            fallback = resolve_executable(
                "relap5.exe",
                run_dir,
                "relap5",
                project_root=project,
            )

        self.assertEqual(resolved, local_executable)
        self.assertEqual(
            fallback,
            self.library.entry_root("relap5") / "bin" / "relap5.exe",
        )

    def test_destination_copy_skips_unrelated_project_files(self):
        project = self.base / "project"
        source = project / "inputs" / "relap5"
        run_dir = project / "test_cases" / "case-1"
        executable = write_pe(source / "relap5.exe")
        (source / "tpfh2o").write_bytes(b"PROPERTY")
        (source / "runtime.dll").write_bytes(b"DLL")
        (source / "test_data.xlsx").write_bytes(b"DATA")
        (source / "notes.md").write_text("notes", encoding="utf-8")
        run_dir.mkdir(parents=True)

        copied = destination_executable(
            executable,
            run_dir,
            "relap5",
            project_root=project,
        )

        self.assertEqual(copied, run_dir / "relap5.exe")
        self.assertTrue((run_dir / "tpfh2o").is_file())
        self.assertTrue((run_dir / "runtime.dll").is_file())
        self.assertFalse((run_dir / "test_data.xlsx").exists())
        self.assertFalse((run_dir / "notes.md").exists())

    def test_api_payload_uses_the_configured_library(self):
        from api import server

        with patch.object(server, "SOFTWARE_LIBRARY", self.library):
            payload = server._software_payload()

        self.assertEqual(
            Path(payload["library_root_absolute"]),
            self.library.root,
        )
        self.assertEqual(len(payload["software"]), 5)


if __name__ == "__main__":
    unittest.main()
