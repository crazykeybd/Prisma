from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml
from openpyxl import load_workbook

from common.util_prisma import release_log_file
from project_store import (
    LEGACY_PROJECT_ID,
    PROJECT_DIRS,
    ProjectError,
    ProjectStore,
    validate_project_name,
)
from run_prisma.executor import execute_testcase


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace_root = self.base / "workspace"
        self.workspace_root.mkdir()
        self.store = ProjectStore(
            self.base / "projects",
            workspace_root=self.workspace_root,
        )

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def test_create_builds_complete_project_workspace(self):
        project = self.store.create(
            "RELAP5 基准",
            "relap5",
            "回归测试项目",
        )

        self.assertEqual(self.store.active().id, project.id)
        for name in PROJECT_DIRS:
            self.assertTrue((project.root / name).is_dir(), name)
        self.assertTrue((project.root / "project.json").is_file())
        self.assertTrue((project.root / "config.yaml").is_file())
        self.assertTrue((project.root / "data" / "test_data.xlsx").is_file())
        self.assertTrue((project.root / "data" / "test_check.xlsx").is_file())
        self.assertTrue((project.root / "inputs" / "mini.i").is_file())

        config = yaml.safe_load(
            (project.root / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["softwareType"], "relap5")
        self.assertEqual(config["runMode"], "simulate")
        self.assertEqual(
            project.to_dict()["software_status"],
            "available",
        )

    def test_duplicate_and_invalid_names_are_rejected(self):
        self.store.create("same-name", "relap5")
        with self.assertRaisesRegex(ProjectError, "同名项目已存在"):
            self.store.create("same-name", "relap5")

        for name in ("", "CON", "aux.txt", ".hidden", "bad/name", "bad."):
            with self.subTest(name=name):
                with self.assertRaises(ProjectError):
                    validate_project_name(name)

    def test_reserved_software_cannot_create_runnable_project(self):
        with self.assertRaisesRegex(ProjectError, "尚未实现"):
            self.store.create("中子物理项目", "neutron_physics")

    def test_activation_and_path_boundary(self):
        first = self.store.create("first", "relap5")
        second = self.store.create("second", "nusolsyslmr")
        self.assertEqual(self.store.active().id, second.id)

        self.store.set_active(first.id)
        self.assertEqual(self.store.active().id, first.id)
        self.assertEqual(
            self.store.resolve_member(first, "data/test_data.xlsx"),
            (first.root / "data" / "test_data.xlsx").resolve(),
        )
        self.assertEqual(
            self.store.resolve_member(first, r"data\test_data.xlsx"),
            (first.root / "data" / "test_data.xlsx").resolve(),
        )
        with self.assertRaisesRegex(ProjectError, "当前项目内"):
            self.store.resolve_member(first, "../outside.txt")
        with self.assertRaisesRegex(ProjectError, "当前项目内"):
            self.store.resolve_member(first, Path(self.temp_dir.name) / "outside.txt")

    def test_save_project_metadata_persists_name_and_description(self):
        project = self.store.create("before-save", "relap5", "旧说明")
        metadata_path = project.root / "project.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["updated_at"] = "2000-01-01T00:00:00+00:00"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        saved = self.store.update_metadata(
            project.id,
            "after-save",
            "新的项目说明",
        )

        self.assertEqual(saved.id, project.id)
        self.assertEqual(saved.name, "after-save")
        self.assertEqual(saved.description, "新的项目说明")
        self.assertNotEqual(saved.updated_at, "2000-01-01T00:00:00+00:00")
        self.assertEqual(self.store.get(project.id).name, "after-save")

        legacy = self.store.update_metadata(
            LEGACY_PROJECT_ID,
            "legacy-saved",
            "旧版工作区也可以保存",
        )
        self.assertEqual(legacy.id, LEGACY_PROJECT_ID)
        self.assertEqual(legacy.name, "legacy-saved")
        self.assertEqual(legacy.description, "旧版工作区也可以保存")
        self.assertTrue((self.workspace_root / "project.json").is_file())

    def test_save_legacy_project_can_change_software_and_config(self):
        config_path = self.workspace_root / "config" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            yaml.safe_dump(
                {
                    "softwareType": "relap5",
                    "executableName": "relap5.exe",
                    "runMode": "simulate",
                }
            ),
            encoding="utf-8",
        )

        saved = self.store.update_metadata(
            LEGACY_PROJECT_ID,
            "legacy-nusol",
            "切换旧版工作区软件",
            software_key="nusolsyslmr",
        )

        self.assertEqual(saved.id, LEGACY_PROJECT_ID)
        self.assertEqual(saved.software_key, "nusolsyslmr")
        metadata = json.loads(
            (self.workspace_root / "project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["software_key"], "nusolsyslmr")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["softwareType"], "nusolsyslmr")
        self.assertEqual(config["executableName"], "nusolsyslmr.exe")
        self.assertEqual(config["nusolsyslmrTimeoutSec"], 1800)

    def test_save_project_metadata_can_change_software_in_place(self):
        project = self.store.create("before-software-change", "relap5", "旧说明")
        original_root = project.root

        saved = self.store.update_metadata(
            project.id,
            "after-software-change",
            "切换软件",
            software_key="nusolsyslmr",
        )

        self.assertEqual(saved.id, project.id)
        self.assertEqual(saved.root, original_root)
        self.assertEqual(saved.software_key, "nusolsyslmr")
        metadata = json.loads(
            (original_root / "project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["software_key"], "nusolsyslmr")
        config = yaml.safe_load(
            (original_root / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["softwareType"], "nusolsyslmr")
        self.assertEqual(config["executableName"], "nusolsyslmr.exe")
        self.assertEqual(config["nusolsyslmrTimeoutSec"], 1800)

    def test_save_project_rejects_unavailable_software_without_changing_files(self):
        project = self.store.create("reserved-switch", "relap5")
        metadata_path = project.root / "project.json"
        config_path = project.root / "config.yaml"
        metadata_before = metadata_path.read_bytes()
        config_before = config_path.read_bytes()

        with self.assertRaisesRegex(ProjectError, "尚未实现"):
            self.store.update_metadata(
                project.id,
                "reserved-switch-renamed",
                "不应写入",
                software_key="neutron_physics",
            )

        self.assertEqual(metadata_path.read_bytes(), metadata_before)
        self.assertEqual(config_path.read_bytes(), config_before)

    def test_open_internal_project_is_idempotent(self):
        project = self.store.create("internal-open", "relap5")

        opened = self.store.open_existing(project.root)

        self.assertEqual(opened.id, project.id)
        self.assertEqual(self.store.active().id, project.id)
        self.assertEqual(len(self.store.projects()), 1)

    def test_open_external_project_imports_and_activates_copy(self):
        external_store = ProjectStore(
            Path(self.temp_dir.name) / "external-projects"
        )
        source = external_store.create("外部回归项目", "relap5", "可导入")

        opened = self.store.open_existing(source.root)

        self.assertEqual(opened.id, source.id)
        self.assertEqual(self.store.active().id, source.id)
        self.assertNotEqual(opened.root, source.root)
        self.assertTrue((opened.root / "project.json").is_file())
        self.assertTrue((opened.root / "config.yaml").is_file())
        self.assertTrue(
            (opened.root / "data" / "test_data.xlsx").is_file()
        )
        self.assertTrue(source.root.is_dir())

    def test_open_external_project_accepts_manifest_path(self):
        external_store = ProjectStore(
            Path(self.temp_dir.name) / "external-projects"
        )
        source = external_store.create("manifest-open", "relap5")

        opened = self.store.open_existing(source.root / "project.json")

        self.assertEqual(opened.id, source.id)
        self.assertEqual(self.store.active().id, source.id)
        self.assertNotEqual(opened.root, source.root)

    def test_open_existing_rejects_non_manifest_file(self):
        external_store = ProjectStore(
            Path(self.temp_dir.name) / "external-projects"
        )
        source = external_store.create("wrong-file", "relap5")

        with self.assertRaisesRegex(ProjectError, "project.json"):
            self.store.open_existing(source.root / "config.yaml")

    def test_open_external_project_rejects_conflicts_and_invalid_projects(self):
        external_store = ProjectStore(
            Path(self.temp_dir.name) / "external-projects"
        )
        source = external_store.create("same-name", "relap5")
        self.store.create("same-name", "relap5")
        with self.assertRaisesRegex(ProjectError, "同名项目已存在"):
            self.store.open_existing(source.root)

        missing_config = external_store.create("missing-config", "relap5")
        (missing_config.root / "config.yaml").unlink()
        with self.assertRaisesRegex(ProjectError, "缺少 config.yaml"):
            self.store.open_existing(missing_config.root)

        missing_metadata = external_store.create("missing-metadata", "relap5")
        (missing_metadata.root / "project.json").unlink()
        with self.assertRaisesRegex(ProjectError, "缺少 project.json"):
            self.store.open_existing(missing_metadata.root)

        reserved = external_store.create("reserved-software", "relap5")
        metadata_path = reserved.root / "project.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["software_key"] = "neutron_physics"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        config_path = reserved.root / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["softwareType"] = "neutron_physics"
        config_path.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProjectError, "尚未实现"):
            self.store.open_existing(reserved.root)

    def test_delete_active_project_returns_to_legacy_workspace(self):
        project = self.store.create("temporary", "relap5")
        self.store.delete(project.id)
        self.assertFalse(project.root.exists())
        self.assertEqual(self.store.active().id, LEGACY_PROJECT_ID)
        with self.assertRaisesRegex(ProjectError, "旧版工作区"):
            self.store.delete(LEGACY_PROJECT_ID)

    def test_project_run_uses_project_log_and_result_files(self):
        project = self.store.create("run-project", "relap5")
        results = execute_testcase(
            file_path=str(project.root / "data" / "test_data.xlsx"),
            max_workers=1,
            project_root=project.root,
            config_path=project.root / "config.yaml",
        )

        self.assertTrue(results)
        self.assertTrue(
            (project.root / "log" / "prisma_run.log").is_file()
        )
        self.assertTrue(
            (
                project.root
                / "test_cases"
                / "STARTER-001"
                / "outdta"
            ).is_file()
        )
        self.assertFalse((project.root / "log" / "outside.log").exists())

    def test_nusolsyslmr_starter_project_runs_end_to_end_in_simulate_mode(self):
        project = self.store.create("NUSOL 短时回归", "nusolsyslmr")
        self.assertTrue((project.root / "inputs" / "indta.i").is_file())
        config = yaml.safe_load(
            (project.root / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["nusolsyslmrTimeoutSec"], 1800)

        workbook = load_workbook(
            project.root / "data" / "test_data.xlsx",
            data_only=True,
        )
        rows = [
            [None if value is None else str(value) for value in row]
            for row in workbook["testcase"].iter_rows(values_only=True)
        ]
        workbook.close()
        self.assertEqual(len(rows), 7)
        self.assertEqual(
            [row[2] for row in rows[1:]],
            [
                "folder_prep",
                "run_software",
                "check_output",
                "check_output",
                "check_output",
                "check_output",
            ],
        )
        self.assertEqual(rows[2][5:8], ["indta.i", "outdta", "1800"])
        self.assertEqual(
            [row[4:7] for row in rows[3:]],
            [
                ["p@304010000", "8.93893E+05", "nusolsyslmr"],
                ["tempf@304010000", "753.142", "nusolsyslmr"],
                ["p@364010000", "7.64535E+06", "nusolsyslmr"],
                ["tempf@364010000", "466.913", "nusolsyslmr"],
            ],
        )

        results = execute_testcase(
            file_path=str(project.root / "data" / "test_data.xlsx"),
            max_workers=1,
            project_root=project.root,
            config_path=project.root / "config.yaml",
        )

        self.assertEqual(results, {"STARTER-001": True})
        output = project.root / "test_cases" / "STARTER-001" / "outdta"
        self.assertTrue(output.is_file())
        self.assertIn(
            "Input processing completed successfully",
            output.read_text(encoding="utf-8-sig"),
        )
        self.assertEqual(
            (
                project.root
                / "test_cases"
                / "STARTER-001"
                / "indta.i"
            ).read_bytes(),
            (project.root / "inputs" / "indta.i").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
