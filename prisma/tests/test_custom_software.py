from __future__ import annotations

import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from common.util_prisma import release_log_file
from project_store import ProjectError, ProjectStore
from software import (
    GenericCommandAdapter,
    SoftwareAdapterError,
    SoftwareLibrary,
    SoftwareLibraryError,
    SoftwareRunRequest,
    get_adapter,
    software_catalog,
)


def write_pe(path: Path, payload: bytes = b"MZ custom executable") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


DEFINITION = {
    "display_name": "MY-SOLVER",
    "software_key": "my_solver",
    "description": "用户新增的命令行求解器",
    "arguments": "{input} -o {output}",
    "input_filename": "case.inp",
    "support_files": ["runtime/*.dll", r"properties\tpfh2o"],
    "output_filename": "case.out",
    "timeout_sec": 120,
}


class CustomDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def test_registered_software_appears_in_catalog(self):
        self.library.create_custom_definition(DEFINITION)

        catalog = {item["key"]: item for item in software_catalog(self.library)}

        item = catalog["my_solver"]
        self.assertEqual(item["display_name"], "MY-SOLVER")
        self.assertTrue(item["available"])
        self.assertTrue(item["user_defined"])
        self.assertEqual(item["state"], "not_installed")
        self.assertEqual(item["input_filename"], "case.inp")
        self.assertEqual(item["output_filename"], "case.out")
        self.assertEqual(item["arguments"], ["{input}", "-o", "{output}"])
        self.assertEqual(
            item["support_files"],
            ["runtime/*.dll", "properties/tpfh2o"],
        )
        self.assertEqual(item["timeout_sec"], 120)
        self.assertEqual(get_adapter("MY-SOLVER", self.library).key, "my_solver")
        self.assertEqual(get_adapter("my_solver", self.library).key, "my_solver")

    def test_definition_survives_new_library_instance(self):
        self.library.create_custom_definition(DEFINITION)
        manifest = json.loads(
            (self.library.entry_root("my_solver") / "software.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["adapter"], "generic_cli")

        reopened = SoftwareLibrary(self.library.root)
        adapters = {adapter.key: adapter for adapter in reopened.custom_adapters()}
        self.assertEqual(adapters["my_solver"].display_name, "MY-SOLVER")
        self.assertEqual(adapters["my_solver"].arguments, ("{input}", "-o", "{output}"))
        self.assertEqual(
            adapters["my_solver"].support_files,
            ("runtime/*.dll", "properties/tpfh2o"),
        )

    def test_invalid_definitions_are_rejected(self):
        cases = [
            ({**DEFINITION, "software_key": "Bad Key"}, "软件标识"),
            ({**DEFINITION, "software_key": "relap5"}, "内置软件"),
            ({**DEFINITION, "software_key": ""}, "软件标识"),
            ({**DEFINITION, "display_name": "  "}, "显示名称"),
            ({**DEFINITION, "input_filename": "a/case.inp"}, "输入文件名"),
            ({**DEFINITION, "output_filename": "case/out.txt"}, "输出文件名"),
            ({**DEFINITION, "arguments": "{input} {unknown}"}, "占位符"),
            ({**DEFINITION, "timeout_sec": 0}, "超时"),
            ({**DEFINITION, "support_files": [r"C:\runtime.dll"]}, "运行依赖"),
            ({**DEFINITION, "support_files": ["../runtime.dll"]}, "运行依赖"),
        ]
        for payload, message in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(SoftwareLibraryError, message):
                    self.library.create_custom_definition(payload)

    def test_re_registration_updates_definition_and_keeps_program(self):
        self.library.create_custom_definition(DEFINITION)
        source = write_pe(self.base / "solver" / "solver.exe")
        self.library.import_source(
            get_adapter("my_solver", self.library), source, "1.0"
        )

        self.library.create_custom_definition(
            {**DEFINITION, "display_name": "MY-SOLVER 2"}
        )

        manifest = json.loads(
            (self.library.entry_root("my_solver") / "software.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["display_name"], "MY-SOLVER 2")
        self.assertEqual(manifest["executable"], "bin/solver.exe")
        item = next(
            entry for entry in software_catalog(self.library)
            if entry["key"] == "my_solver"
        )
        self.assertEqual(item["state"], "ready")
        self.assertEqual(item["version"], "1.0")

    def test_update_definition_changes_config_and_keeps_program_and_templates(self):
        self.library.create_custom_definition(DEFINITION)
        template = (
            self.library.entry_root("my_solver") / "templates" / "case.tpl"
        )
        template.parent.mkdir(parents=True)
        template.write_text("template\n", encoding="utf-8")
        source = write_pe(self.base / "solver" / "solver.exe")
        self.library.import_source(
            get_adapter("my_solver", self.library), source, "1.0"
        )

        updated = self.library.update_custom_definition(
            "my_solver",
            {
                "software_key": "my_solver",
                "display_name": "MY-SOLVER EDIT",
                "description": "修改后的软件说明",
                "version": "2.0",
                "input_filenames": ["main.inp", "auxiliary.dat"],
                "input_filename": "main.inp",
                "support_files": ["runtime/*.dll", "data/*.dat"],
                "output_filename": "main.out",
                "arguments": "-i {input1} -a {input2} -o {output}",
                "timeout_sec": 240,
            },
        )

        self.assertEqual(updated["software_key"], "my_solver")
        self.assertEqual(updated["display_name"], "MY-SOLVER EDIT")
        self.assertEqual(updated["executable"], "bin/solver.exe")
        self.assertEqual(updated["templates"], ["templates/case.tpl"])
        self.assertTrue(
            (self.library.entry_root("my_solver") / "bin" / "solver.exe").is_file()
        )
        self.assertTrue(template.is_file())
        item = next(
            entry for entry in software_catalog(self.library)
            if entry["key"] == "my_solver"
        )
        self.assertEqual(item["version"], "2.0")
        self.assertEqual(item["arguments"], ["-i", "{input1}", "-a", "{input2}", "-o", "{output}"])
        self.assertEqual(item["input_filenames"], ["main.inp", "auxiliary.dat"])
        self.assertEqual(item["timeout_sec"], 240)

    def test_update_definition_rejects_software_key_change(self):
        self.library.create_custom_definition(DEFINITION)

        with self.assertRaisesRegex(SoftwareLibraryError, "不能修改"):
            self.library.update_custom_definition(
                "my_solver",
                {**DEFINITION, "software_key": "renamed_solver"},
            )

    def test_custom_software_cannot_shadow_builtin_key(self):
        with self.assertRaisesRegex(SoftwareLibraryError, "内置软件"):
            self.library.create_custom_definition(
                {**DEFINITION, "software_key": "nusolsyslmr"}
            )

    def test_removing_custom_software(self):
        self.library.create_custom_definition(DEFINITION)
        self.library.remove_custom_definition("my_solver")
        self.assertFalse(self.library.entry_root("my_solver").exists())
        with self.assertRaisesRegex(SoftwareAdapterError, "未知软件类型"):
            get_adapter("my_solver", self.library)
        with self.assertRaisesRegex(SoftwareLibraryError, "内置软件"):
            self.library.remove_custom_definition("relap5")


class CustomProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.library.create_custom_definition(DEFINITION)
        self.store = ProjectStore(self.base / "projects", software_library=self.library)

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def test_project_can_use_custom_software(self):
        project = self.store.create("自定义软件项目", "my_solver", "回归")

        config = yaml.safe_load(
            (project.root / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["softwareType"], "my_solver")
        self.assertTrue((project.root / "inputs" / "case.inp").is_file())
        payload = project.to_dict(self.library)
        self.assertTrue(payload["software_available"])
        self.assertEqual(payload["software_name"], "MY-SOLVER")
        self.assertEqual(self.store.active().id, project.id)

    def test_project_cannot_use_unregistered_software(self):
        with self.assertRaisesRegex(ProjectError, "未知软件类型"):
            self.store.create("坏项目", "not_registered")

    def test_existing_project_reopens_after_library_reload(self):
        project = self.store.create("重开项目", "my_solver")
        reopened = ProjectStore(
            self.base / "projects", software_library=SoftwareLibrary(self.library.root)
        )
        self.assertEqual(reopened.get(project.id).software_key, "my_solver")


class GenericAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.library.create_custom_definition(DEFINITION)
        self.adapter = get_adapter("my_solver", self.library)
        self.project = self.base / "project"
        self.run_dir = self.project / "test_cases" / "case-1"
        self.run_dir.mkdir(parents=True)
        (self.project / "inputs").mkdir()
        (self.project / "inputs" / "case.inp").write_text("SET 1\n", encoding="utf-8")
        self.config = self.base / "config.yaml"

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def _request(self, mode: str = "real") -> SoftwareRunRequest:
        self.config.write_text(
            yaml.safe_dump({"runMode": mode}), encoding="utf-8"
        )
        return SoftwareRunRequest(
            case_no="TC-CUSTOM",
            run_dir=self.run_dir,
            executable="solver.exe",
            software_type="my_solver",
            input_name="case.inp",
            output_name="case.out",
            project_root=self.project,
            config_path=self.config,
        )

    def test_build_command_expands_placeholders(self):
        command = self.adapter.build_command(
            self._request("simulate"), "solver.exe"
        )
        self.assertEqual(command, ["solver.exe", "case.inp", "-o", "case.out"])

    def test_simulate_mode_writes_output_without_program(self):
        result = self.adapter.execute(self._request("simulate"))
        self.assertEqual(result.output_path, self.run_dir / "case.out")
        self.assertIn("status: simulate", result.output_path.read_text(encoding="utf-8"))

    def test_real_run_passes_arguments_and_analyses_output(self):
        source = write_pe(self.base / "solver" / "solver.exe")
        self.library.import_source(
            get_adapter("my_solver", self.library), source
        )
        calls = []

        def fake_run(command, cwd=None, **kwargs):
            calls.append((command, cwd))
            Path(cwd, "case.out").write_text(
                "pressure: 15.5\ntemperature = 300\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0)

        with patch("software.custom.subprocess.run", side_effect=fake_run):
            result = self.adapter.execute(self._request("real"))

        self.assertEqual(Path(calls[0][0][0]).name, "solver.exe")
        self.assertEqual(calls[0][0][1:], ["case.inp", "-o", "case.out"])
        self.assertEqual(Path(calls[0][1]), self.run_dir)
        self.assertEqual(
            result.output_path.read_text(encoding="utf-8"),
            "pressure: 15.5\ntemperature = 300\n",
        )
        self.assertEqual(result.analysis["pairs"], 2)
        self.assertEqual(
            self.adapter.extract_value(
                result.output_path.read_text(encoding="utf-8"), "PRESSURE"
            ),
            "15.5",
        )
        self.assertTrue((self.run_dir / "screen.txt").is_file())
        self.assertTrue((self.run_dir / "solver.exe").is_file())

    def test_missing_output_is_reported(self):
        source = write_pe(self.base / "solver" / "solver.exe")
        self.library.import_source(
            get_adapter("my_solver", self.library), source
        )
        with patch(
            "software.custom.subprocess.run",
            return_value=subprocess.CompletedProcess(["solver.exe"], 3),
        ):
            with self.assertRaisesRegex(Exception, "未生成输出文件"):
                self.adapter.execute(self._request("real"))

    def test_missing_program_is_reported(self):
        with self.assertRaisesRegex(Exception, "未找到 MY-SOLVER 的可运行程序"):
            self.adapter.execute(self._request("real"))

    def test_missing_input_is_reported(self):
        request = self._request("real")
        (self.project / "inputs" / "case.inp").unlink()
        with self.assertRaisesRegex(Exception, "未找到输入文件"):
            self.adapter.prepare(request)


class SoftwareApiTests(unittest.TestCase):
    """通过真实的 HTTP 接口验证新增/删除软件流程。"""

    def setUp(self):
        from http.server import ThreadingHTTPServer

        from api import server

        self.server = server
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.store = ProjectStore(
            self.base / "projects", software_library=self.library
        )
        self._patches = [
            patch.object(server, "SOFTWARE_LIBRARY", self.library),
            patch.object(server, "STORE", self.store),
        ]
        for active in self._patches:
            active.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        for active in self._patches:
            active.stop()
        release_log_file()
        self.temp_dir.cleanup()

    def request(self, method: str, path: str, payload=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=15
        )
        body = json.dumps(payload) if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = json.loads(response.read().decode("utf-8") or "{}")
            return response.status, data
        finally:
            connection.close()

    def test_create_project_and_delete_flow(self):
        status, data = self.request("POST", "/api/software/create", DEFINITION)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["definition"]["software_key"], "my_solver")
        self.assertFalse(data["software"]["program_ready"])

        status, data = self.request(
            "POST", "/api/software/create", {**DEFINITION, "software_key": "relap5"}
        )
        self.assertEqual(status, 400)
        self.assertIn("内置软件", data["error"])

        status, data = self.request(
            "POST",
            "/api/projects/create",
            {"name": "自定义软件项目", "software_key": "my_solver"},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["current"]["software_key"], "my_solver")

        status, data = self.request(
            "POST", "/api/software/delete", {"software_key": "my_solver"}
        )
        self.assertEqual(status, 400)
        self.assertIn("仍在使用", data["error"])

        status, data = self.request(
            "POST",
            "/api/projects/delete",
            {"project_id": "自定义软件项目", "confirm_name": "自定义软件项目"},
        )
        self.assertEqual(status, 200, data)

        status, data = self.request(
            "POST", "/api/software/delete", {"software_key": "my_solver"}
        )
        self.assertEqual(status, 200, data)
        keys = [item["key"] for item in data["software"]]
        self.assertNotIn("my_solver", keys)

    def test_update_software_and_project_software_via_api(self):
        status, data = self.request("POST", "/api/software/create", DEFINITION)
        self.assertEqual(status, 200, data)

        status, data = self.request(
            "POST",
            "/api/software/update",
            {
                **DEFINITION,
                "display_name": "MY-SOLVER EDIT",
                "version": "2.0",
                "input_filenames": ["main.inp", "auxiliary.dat"],
                "input_filename": "main.inp",
                "output_filename": "main.out",
                "arguments": "-i {input1} -a {input2} -o {output}",
                "timeout_sec": 240,
            },
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["definition"]["display_name"], "MY-SOLVER EDIT")
        self.assertEqual(data["software"]["version"], "2.0")
        self.assertEqual(
            data["software"]["input_filenames"], ["main.inp", "auxiliary.dat"]
        )

        status, data = self.request(
            "POST",
            "/api/software/update",
            {**DEFINITION, "software_key": "renamed_solver"},
        )
        self.assertEqual(status, 400)
        self.assertIn("未知软件类型", data["error"])

        status, data = self.request(
            "POST",
            "/api/software/update",
            {
                **DEFINITION,
                "software_key": "relap5",
                "display_name": "RELAP5 EDIT",
                "version": "4.0",
                "input_filenames": ["main.inp", "auxiliary.dat"],
                "input_filename": "main.inp",
                "output_filename": "main.out",
                "arguments": "-i {input1} -a {input2} -o {output}",
                "timeout_sec": 3600,
            },
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["definition"]["software_key"], "relap5")
        self.assertEqual(data["definition"]["display_name"], "RELAP5 EDIT")
        self.assertEqual(data["software"]["display_name"], "RELAP5 EDIT")
        self.assertEqual(
            data["software"]["input_filenames"],
            ["main.inp", "auxiliary.dat"],
        )
        self.assertEqual(data["software"]["timeout_sec"], 3600)

        with patch.dict(self.server.STATE, {"running": True}):
            status, data = self.request(
                "POST", "/api/software/update", DEFINITION
            )
        self.assertEqual(status, 400)
        self.assertIn("运行期间", data["error"])

        status, data = self.request(
            "POST",
            "/api/projects/create",
            {"name": "切换软件项目", "software_key": "relap5"},
        )
        self.assertEqual(status, 200, data)
        project_id = data["current"]["id"]
        original_root = Path(data["current"]["absolute_path"])

        status, data = self.request(
            "POST",
            "/api/projects/save",
            {
                "project_id": project_id,
                "name": "切换软件项目-已修改",
                "description": "项目配置可编辑",
                "software_key": "my_solver",
            },
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["current"]["software_key"], "my_solver")
        self.assertEqual(data["current"]["name"], "切换软件项目-已修改")
        self.assertEqual(Path(data["current"]["absolute_path"]), original_root)
        config = yaml.safe_load(
            (original_root / "config.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["softwareType"], "my_solver")


if __name__ == "__main__":
    unittest.main()
