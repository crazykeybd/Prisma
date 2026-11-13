"""多输入卡软件支持：定义校验、命令行占位符、运行目录暂存。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from common.run_context import RunContext
from common.util_prisma import release_log_file
from keywords.keyword_lib import KeyPrisma, execute_command
from project_store import ProjectStore
from software import (
    GenericCommandAdapter,
    SoftwareAdapterError,
    SoftwareLibrary,
    SoftwareLibraryError,
    SoftwareRunRequest,
    get_adapter,
    software_catalog,
)


MULTI = {
    "display_name": "MULTI-CARD",
    "software_key": "multi_card",
    "description": "需要主输入卡与辅助数据表的求解器",
    "input_filenames": ["case.inp", "table.dat"],
    "output_filename": "case.out",
    "arguments": "-i {input1} -t {input2} -o {output}",
    "timeout_sec": 60,
}


def write_pe(path: Path, payload: bytes = b"MZ multi-card executable") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class MultiInputDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def test_multiple_cards_are_stored_and_exposed(self):
        self.library.create_custom_definition(MULTI)

        manifest = json.loads(
            (self.library.entry_root("multi_card") / "software.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["input_filenames"], ["case.inp", "table.dat"])
        self.assertEqual(manifest["input_filename"], "case.inp")

        item = next(
            entry
            for entry in software_catalog(self.library)
            if entry["key"] == "multi_card"
        )
        self.assertEqual(item["input_filenames"], ["case.inp", "table.dat"])
        self.assertEqual(item["input_filename"], "case.inp")

    def test_text_and_list_forms_are_equivalent(self):
        self.library.create_custom_definition(
            {
                **MULTI,
                "software_key": "text_cards",
                "input_filenames": "case.inp, table.dat；extra.tbl",
                "arguments": "{input}",
            }
        )
        adapter = get_adapter("text_cards", self.library)
        self.assertEqual(
            list(adapter.input_filenames),
            ["case.inp", "table.dat", "extra.tbl"],
        )

    def test_duplicate_cards_are_collapsed(self):
        adapter = GenericCommandAdapter(
            key="dup",
            display_name="DUP",
            input_filenames=["Case.inp", "case.inp", "table.dat"],
        )
        self.assertEqual(list(adapter.input_filenames), ["Case.inp", "table.dat"])

    def test_invalid_card_configuration_is_rejected(self):
        cases = [
            ({**MULTI, "input_filenames": ["a/case.inp"]}, "输入文件名"),
            ({**MULTI, "input_filenames": [f"card{i}.inp" for i in range(13)]}, "最多"),
            ({**MULTI, "input_filenames": ["case.out"]}, "同名"),
            ({**MULTI, "input_filenames": []}, None),
        ]
        for payload, message in cases:
            with self.subTest(payload=payload):
                if message is None:
                    # 空列表回退到默认输入卡，不应报错。
                    self.library.create_custom_definition(
                        {**payload, "software_key": "blank_cards"}
                    )
                    adapter = get_adapter("blank_cards", self.library)
                    self.assertEqual(list(adapter.input_filenames), ["input.txt"])
                    self.library.remove_custom_definition("blank_cards")
                    continue
                with self.assertRaisesRegex(SoftwareLibraryError, message):
                    self.library.create_custom_definition(payload)


class MultiInputCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.library.create_custom_definition(MULTI)
        self.adapter = get_adapter("multi_card", self.library)
        self.project = self.base / "project"
        self.run_dir = self.project / "test_cases" / "case-1"
        self.run_dir.mkdir(parents=True)
        (self.project / "inputs").mkdir()
        (self.project / "inputs" / "case.inp").write_text("MAIN\n", encoding="utf-8")
        (self.project / "inputs" / "table.dat").write_text("T1 1.0\n", encoding="utf-8")
        self.config = self.base / "config.yaml"

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def _request(
        self,
        mode: str = "real",
        input_name: str = "case.inp",
        output_name: str = "case.out",
    ):
        self.config.write_text(yaml.safe_dump({"runMode": mode}), encoding="utf-8")
        return SoftwareRunRequest(
            case_no="TC-MULTI",
            run_dir=self.run_dir,
            executable="solver.exe",
            software_type="multi_card",
            input_name=input_name,
            output_name=output_name,
            project_root=self.project,
            config_path=self.config,
        )

    def test_indexed_and_collective_placeholders(self):
        adapter = get_adapter("multi_card", self.library)
        adapter.arguments = ("-i", "{input1}", "-t", "{input2}", "-o", "{output}")
        command = adapter.build_command(self._request("simulate"), "solver.exe")
        self.assertEqual(
            command,
            ["solver.exe", "-i", "case.inp", "-t", "table.dat", "-o", "case.out"],
        )

        adapter.arguments = ("{inputs}", "-o", "{output}")
        self.assertEqual(
            adapter.build_command(self._request("simulate"), "solver.exe"),
            ["solver.exe", "case.inp table.dat", "-o", "case.out"],
        )

    def test_undefined_indexed_placeholder_is_skipped(self):
        adapter = get_adapter("multi_card", self.library)
        adapter.arguments = ("-i", "{input1}", "-x", "{input5}")
        self.assertEqual(
            adapter.build_command(self._request("simulate"), "solver.exe"),
            ["solver.exe", "-i", "case.inp", "-x"],
        )

    def test_plain_input_placeholder_is_the_primary_card(self):
        adapter = get_adapter("multi_card", self.library)
        adapter.arguments = ("{input}", "-o", "{output}")
        self.assertEqual(
            adapter.build_command(self._request("simulate"), "solver.exe"),
            ["solver.exe", "case.inp", "-o", "case.out"],
        )

    def test_explicit_input_replaces_registered_primary_and_keeps_auxiliary(self):
        adapter = get_adapter("multi_card", self.library)
        request = self._request("simulate", input_name="custom.inp")
        self.assertEqual(adapter.input_names(request), ["custom.inp", "table.dat"])

    def test_explicit_input_reorders_a_registered_auxiliary_card(self):
        adapter = get_adapter("multi_card", self.library)
        request = self._request("simulate", input_name="table.dat")
        self.assertEqual(adapter.input_names(request), ["table.dat", "case.inp"])

    def test_explicit_single_input_replaces_registered_primary(self):
        adapter = GenericCommandAdapter(
            key="single",
            display_name="SINGLE",
            input_filenames=["input.i"],
        )
        request = self._request("simulate", input_name="system1.i")
        self.assertEqual(adapter.input_names(request), ["system1.i"])

    def test_real_run_stages_every_card_into_run_dir(self):
        source = write_pe(self.base / "solver" / "solver.exe")
        self.library.import_source(get_adapter("multi_card", self.library), source)
        calls = []

        def fake_run(command, cwd=None, **kwargs):
            calls.append((command, Path(cwd)))
            Path(cwd, "case.out").write_text("result: 1\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        with patch("software.custom.subprocess.run", side_effect=fake_run):
            result = self.adapter.execute(self._request("real"))

        self.assertEqual(calls[0][0][1:], ["-i", "case.inp", "-t", "table.dat", "-o", "case.out"])
        self.assertEqual(
            (self.run_dir / "case.inp").read_text(encoding="utf-8"), "MAIN\n"
        )
        self.assertEqual(
            (self.run_dir / "table.dat").read_text(encoding="utf-8"), "T1 1.0\n"
        )
        self.assertEqual(result.output_path, self.run_dir / "case.out")

    def test_real_run_accepts_nested_output_path(self):
        source = write_pe(self.base / "solver" / "solver.exe")
        self.library.import_source(get_adapter("multi_card", self.library), source)
        output_name = "results/case-01/case.out"
        calls = []

        def fake_run(command, cwd=None, **kwargs):
            calls.append(command)
            output = Path(cwd, output_name)
            self.assertTrue(output.parent.is_dir())
            output.write_text("result: nested\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        with patch("software.custom.subprocess.run", side_effect=fake_run):
            result = self.adapter.execute(
                self._request("real", output_name=output_name)
            )

        self.assertEqual(
            calls[0][1:],
            ["-i", "case.inp", "-t", "table.dat", "-o", output_name],
        )
        self.assertEqual(
            result.output_path,
            self.run_dir.joinpath("results", "case-01", "case.out").resolve(),
        )
        self.assertTrue(result.output_path.is_file())

    def test_output_directory_uses_registered_default_name(self):
        result = self.adapter.execute(
            self._request("simulate", output_name="results/")
        )
        self.assertEqual(
            result.output_path,
            self.run_dir.joinpath("results", "case.out").resolve(),
        )
        self.assertTrue(result.output_path.is_file())

    def test_output_path_cannot_leave_run_directory(self):
        with self.assertRaisesRegex(SoftwareAdapterError, r"不能包含 \.\."):
            self.adapter.execute(
                self._request("simulate", output_name="../case.out")
            )

    def test_missing_secondary_card_is_reported(self):
        (self.project / "inputs" / "table.dat").unlink()
        with self.assertRaisesRegex(SoftwareAdapterError, "table.dat"):
            self.adapter.prepare(self._request("real"))


class MultiInputProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.library.create_custom_definition(MULTI)
        self.store = ProjectStore(self.base / "projects", software_library=self.library)

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def test_project_creates_placeholder_for_every_card(self):
        project = self.store.create("多输入卡项目", "multi_card")
        for name in ("case.inp", "table.dat"):
            self.assertTrue(
                (project.root / "inputs" / name).is_file(), f"缺少占位文件 {name}"
            )


class FolderPrepMultiCardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.library.create_custom_definition(MULTI)
        self.project = self.base / "project"
        (self.project / "inputs").mkdir(parents=True)
        (self.project / "test_cases").mkdir()
        (self.project / "inputs" / "case.inp").write_text("MAIN\n", encoding="utf-8")
        (self.project / "inputs" / "table.dat").write_text("T1 1.0\n", encoding="utf-8")
        self.context = RunContext(
            root=self.project.resolve(), config_path=self.project / "config.yaml"
        )
        self.adapter = get_adapter("multi_card", self.library)

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def test_folder_prep_copies_all_declared_cards(self):
        with patch("keywords.keyword_lib.get_adapter", return_value=self.adapter):
            ok = KeyPrisma.folder_prep(
                "TC-PREP",
                "case-1",
                "case.inp",
                "",
                context=self.context,
                software_key="multi_card",
            )

        self.assertTrue(ok)
        run_dir = self.project / "test_cases" / "case-1"
        self.assertTrue((run_dir / "input.txt").is_file())
        self.assertEqual((run_dir / "case.inp").read_text(encoding="utf-8"), "MAIN\n")
        self.assertEqual(
            (run_dir / "table.dat").read_text(encoding="utf-8"), "T1 1.0\n"
        )

    def test_explicit_unregistered_card_replaces_registered_primary(self):
        (self.project / "inputs" / "custom.inp").write_text(
            "CUSTOM\n", encoding="utf-8"
        )
        logger = MagicMock()
        with patch(
            "keywords.keyword_lib.get_adapter", return_value=self.adapter
        ), patch("keywords.keyword_lib.init_log", return_value=logger):
            ok = KeyPrisma.folder_prep(
                "TC-PREP",
                "case-1",
                "custom.inp",
                "",
                context=self.context,
                software_key="multi_card",
            )

        self.assertTrue(ok)
        run_dir = self.project / "test_cases" / "case-1"
        self.assertEqual(
            (run_dir / "custom.inp").read_text(encoding="utf-8"), "CUSTOM\n"
        )
        self.assertTrue((run_dir / "table.dat").is_file())
        self.assertFalse((run_dir / "case.inp").exists())
        warning_messages = [
            str(call.args[0]) for call in logger.warning.call_args_list
        ]
        self.assertNotIn(
            "declared input card not found: %s",
            warning_messages,
        )


SUPPORT_DEFINITION = {
    **MULTI,
    "software_key": "support_solver",
    "display_name": "SUPPORT-SOLVER",
    "support_files": ["runtime/*.dll", "properties/tpfh2o"],
}


class FolderPrepSupportFileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.library = SoftwareLibrary(self.base / "software-library")
        self.library.create_custom_definition(SUPPORT_DEFINITION)
        self.adapter = get_adapter("support_solver", self.library)
        self.project = self.base / "project"
        (self.project / "inputs" / "support_solver" / "runtime").mkdir(
            parents=True
        )
        (self.project / "inputs" / "support_solver" / "properties").mkdir(
            parents=True
        )
        (self.project / "test_cases").mkdir()
        (self.project / "inputs" / "case.inp").write_text(
            "MAIN\n", encoding="utf-8"
        )
        (self.project / "inputs" / "table.dat").write_text(
            "TABLE\n", encoding="utf-8"
        )
        (self.project / "inputs" / "support_solver" / "tpfh2o").write_bytes(
            b"LEGACY"
        )
        (self.project / "inputs" / "support_solver" / "solver.exe").write_bytes(
            b"MZ support executable"
        )
        (self.project / "inputs" / "support_solver" / "properties" /
         "tpfh2o").write_bytes(b"PROJECT-PROPERTY")
        self.library.entry_root("support_solver").mkdir(
            parents=True, exist_ok=True
        )
        bundled_bin = self.library.entry_root("support_solver") / "bin"
        bundled_runtime = bundled_bin / "runtime"
        bundled_runtime.mkdir(parents=True)
        (bundled_runtime / "core.dll").write_bytes(b"PACKAGED")
        (bundled_runtime / "only-packaged.dll").write_bytes(b"PACKAGED-ONLY")
        default_only = bundled_bin / "default-only"
        default_only.mkdir()
        (default_only / "plugin.dat").write_bytes(b"DEFAULT-BIN")
        project_runtime = (
            self.project / "inputs" / "support_solver" / "runtime"
        )
        (project_runtime / "core.dll").write_bytes(b"PROJECT")
        self.context = RunContext(
            root=self.project.resolve(), config_path=self.project / "config.yaml"
        )

    def tearDown(self):
        release_log_file()
        self.temp_dir.cleanup()

    def _prep(self, selection=""):
        with patch(
            "keywords.keyword_lib.get_adapter", return_value=self.adapter
        ), patch(
            "keywords.keyword_lib.init_log", return_value=MagicMock()
        ):
            return KeyPrisma.folder_prep(
                "TC-SUPPORT",
                "case-1",
                "case.inp",
                "solver.exe",
                context=self.context,
                support_files=selection,
                software_key="support_solver",
            )

    def test_blank_selection_uses_configured_dependencies(self):
        self.assertTrue(self._prep())
        run_dir = self.project / "test_cases" / "case-1"

        self.assertTrue((run_dir / "solver.exe").is_file())
        self.assertEqual(
            (run_dir / "runtime" / "core.dll").read_bytes(), b"PROJECT"
        )
        self.assertTrue((run_dir / "runtime" / "only-packaged.dll").is_file())
        self.assertEqual(
            (run_dir / "properties" / "tpfh2o").read_bytes(),
            b"PROJECT-PROPERTY",
        )
        self.assertEqual(
            (run_dir / "default-only" / "plugin.dat").read_bytes(),
            b"DEFAULT-BIN",
        )
        self.assertFalse((run_dir / "tpfh2o").exists())

    def test_all_selection_copies_every_configured_dependency(self):
        self.assertTrue(self._prep("全部"))
        run_dir = self.project / "test_cases" / "case-1"

        self.assertTrue((run_dir / "solver.exe").is_file())
        self.assertTrue((run_dir / "runtime" / "core.dll").is_file())
        self.assertTrue((run_dir / "runtime" / "only-packaged.dll").is_file())
        self.assertTrue((run_dir / "properties" / "tpfh2o").is_file())
        self.assertTrue((run_dir / "default-only" / "plugin.dat").is_file())

    def test_explicit_pattern_only_copies_selected_dependency(self):
        self.assertTrue(self._prep("runtime/core.dll"))
        run_dir = self.project / "test_cases" / "case-1"

        self.assertTrue((run_dir / "runtime" / "core.dll").is_file())
        self.assertFalse((run_dir / "runtime" / "only-packaged.dll").exists())
        self.assertFalse((run_dir / "properties" / "tpfh2o").exists())
        self.assertFalse((run_dir / "default-only" / "plugin.dat").exists())

    def test_none_disables_configured_and_legacy_dependencies(self):
        self.assertTrue(self._prep("无"))
        run_dir = self.project / "test_cases" / "case-1"

        self.assertFalse((run_dir / "runtime" / "core.dll").exists())
        self.assertFalse((run_dir / "default-only" / "plugin.dat").exists())
        self.assertFalse((run_dir / "tpfh2o").exists())

    def test_parent_directory_dependency_is_rejected(self):
        self.assertFalse(self._prep("../runtime.dll"))
        self.assertFalse((self.project / "test_cases" / "case-1").exists())

    def test_execute_command_passes_optional_dependency_column(self):
        command = [
            "TC-SUPPORT",
            str(self.project / "test_cases" / "case-1"),
            "folder_prep",
            "TC-SUPPORT",
            "case.inp",
            "solver.exe",
            "runtime/*.dll",
            "ignored-extra-column",
        ]
        with patch(
            "keywords.keyword_lib.init_log", return_value=MagicMock()
        ), patch.object(
            KeyPrisma, "folder_prep", return_value=True
        ) as folder_prep, patch.object(
            KeyPrisma, "_software_key", return_value="support_solver"
        ):
            self.assertTrue(execute_command(command))

        folder_prep.assert_called_once_with(
            "TC-SUPPORT",
            str(self.project / "test_cases" / "case-1"),
            "case.inp",
            "solver.exe",
            context=None,
            support_files="runtime/*.dll",
            software_key="support_solver",
        )


class MultiInputApiTests(unittest.TestCase):
    """通过真实 HTTP 接口登记多输入卡软件。"""

    def setUp(self):
        import http.client
        from http.server import ThreadingHTTPServer

        from api import server

        self.http_client = http.client
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

    def post(self, path: str, payload: dict):
        connection = self.http_client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=15
        )
        try:
            connection.request(
                "POST",
                path,
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
        finally:
            connection.close()

    def test_create_multi_card_software_and_project(self):
        status, data = self.post("/api/software/create", MULTI)
        self.assertEqual(status, 200, data)
        self.assertEqual(
            data["definition"]["input_filenames"], ["case.inp", "table.dat"]
        )
        self.assertEqual(
            data["software"]["input_filenames"], ["case.inp", "table.dat"]
        )

        status, data = self.post(
            "/api/projects/create",
            {"name": "多输入卡项目", "software_key": "multi_card"},
        )
        self.assertEqual(status, 200, data)
        root = Path(data["current"]["absolute_path"])
        for name in ("case.inp", "table.dat"):
            self.assertTrue((root / "inputs" / name).is_file())


if __name__ == "__main__":
    unittest.main()
