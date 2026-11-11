from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openpyxl import Workbook, load_workbook

from common.run_context import RunContext
from common.util_prisma import release_log_file
from keywords.keyword_lib import KeyPrisma, execute_command
from software import (
    AdapterNotImplementedError,
    SoftwareRunRequest,
    SoftwareLibrary,
    get_adapter,
    normalize_software_key,
    software_catalog,
)
from software.relap5_output import analyze, extract_value


RESERVED_KEYS = ("neutron_physics", "subchannel", "fuel_performance")


class RegistryTests(unittest.TestCase):
    def test_standard_keys_and_aliases(self):
        self.assertEqual(normalize_software_key("RELAP5"), "relap5")
        self.assertEqual(normalize_software_key("relap5.exe"), "relap5")
        self.assertEqual(normalize_software_key("中子物理软件"), "neutron_physics")
        self.assertEqual(normalize_software_key("子通道"), "subchannel")
        self.assertEqual(normalize_software_key("燃料性能分析软件"), "fuel_performance")
        self.assertEqual(get_adapter("neutronics").key, "neutron_physics")
        self.assertEqual(normalize_software_key("nusolsyslmr.exe"), "nusolsyslmr")
        self.assertEqual(normalize_software_key("NUSOL-SYSLMR"), "nusolsyslmr")

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(Exception) as ctx:
            normalize_software_key("unknown_solver")
        self.assertIn("未知软件类型", str(ctx.exception))

    def test_catalog_exposes_two_available_and_three_reserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library = SoftwareLibrary(Path(temp_dir) / "software-library")
            catalog = {
                item["key"]: item for item in software_catalog(library)
            }
        self.assertEqual(set(catalog), {"relap5", "nusolsyslmr", *RESERVED_KEYS})
        for key in ("relap5", "nusolsyslmr"):
            self.assertEqual(catalog[key]["status"], "available")
            self.assertTrue(catalog[key]["available"])
        for key in RESERVED_KEYS:
            self.assertEqual(catalog[key]["status"], "reserved")
            self.assertFalse(catalog[key]["available"])


class ReservedAdapterTests(unittest.TestCase):
    def _request(self, temp_dir: str) -> SoftwareRunRequest:
        return SoftwareRunRequest(
            case_no="TC-RESERVED",
            run_dir=Path(temp_dir),
            executable="solver.exe",
            software_type="neutron_physics",
            input_name="input.inp",
            output_name="output.txt",
        )

    def test_all_reserved_operations_raise_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            request = self._request(temp_dir)
            for key in RESERVED_KEYS:
                adapter = get_adapter(key)
                with self.subTest(key=key):
                    for operation in (
                        lambda: adapter.prepare(request),
                        lambda: adapter.build_command(request),
                        lambda: adapter.execute(request),
                        lambda: adapter.analyze_output(Path(temp_dir) / "output.txt"),
                        lambda: adapter.extract_value("x", "key"),
                    ):
                        with self.assertRaises(AdapterNotImplementedError) as ctx:
                            operation()
                        self.assertIn("已预留但尚未实现", str(ctx.exception))
                        self.assertIn("不会执行程序", str(ctx.exception))
            self.assertFalse(Path(temp_dir, "output.txt").exists())

    def test_run_software_fails_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for key in RESERVED_KEYS:
                with self.subTest(key=key):
                    ok = KeyPrisma.run_software(
                        "TC-RESERVED", temp_dir, "solver.exe", key,
                        "input.inp", "output.txt", "60",
                    )
                    self.assertFalse(ok)
                    self.assertFalse(Path(temp_dir, "output.txt").exists())

    def test_execute_command_dispatches_reserved_keyword(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            command = [
                "TC-RESERVED", temp_dir, "run_software", "solver.exe",
                "subchannel", "input.inp", "output.txt", "60",
            ]
            self.assertFalse(execute_command(command))


class Relap5CompatibilityTests(unittest.TestCase):
    def test_nusolsyslmr_declares_wizard_defaults_and_runtime(self):
        adapter = get_adapter("nusolsyslmr")
        self.assertEqual(adapter.input_filename, "indta.i")
        self.assertEqual(adapter.output_filename, "outdta")
        self.assertEqual(adapter.timeout_sec, 1800)
        self.assertEqual(
            set(adapter.required_runtime_files),
            {
                "libifcoremdd.dll",
                "libifportmd.dll",
                "libmmd.dll",
                "msvcr120d.dll",
            },
        )

    def test_output_analyzer_and_value_extraction(self):
        text = (
            "0$$$$$$$$ Input processing completed successfully.\n"
            "0MAJOR EDIT !!!time=    0.00000     sec\n"
            "0   Vol.no.  pressure     tempf\n"
            " 100-010000  1.00000E+05  300.0\n"
            "0   Vol.no.  pressure     tempf\n"
            " 100-010000  1.20000E+05  310.0\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "outdta")
            path.write_text(text, encoding="utf-8")
            info = analyze(path)
        self.assertTrue(info["input_ok"])
        self.assertEqual(info["major_edits"], 1)
        self.assertEqual(extract_value(text, "p@100010000"), "1.20000E+05")
        self.assertEqual(extract_value(text, "tempf@100010000"), "310.0")

    def test_requested_input_takes_priority_over_stale_indta(self):
        from software.relap5 import Relap5Adapter

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "indta").write_text("STALE INPUT", encoding="utf-8")
            (run_dir / "input.txt").write_text("FRESH INPUT", encoding="utf-8")
            request = SoftwareRunRequest(
                case_no="TC-INP",
                run_dir=run_dir,
                executable="relap5.exe",
                software_type="relap5",
                input_name="input.txt",
            )
            staged = Relap5Adapter()._stage_input(request)
            self.assertEqual(staged.read_text(encoding="utf-8"), "FRESH INPUT")

    def test_requested_indta_is_used_directly(self):
        from software.relap5 import Relap5Adapter

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "indta").write_text("EXPLICIT INPUT", encoding="utf-8")
            (run_dir / "input.txt").write_text("OTHER INPUT", encoding="utf-8")
            request = SoftwareRunRequest(
                case_no="TC-INP",
                run_dir=run_dir,
                executable="relap5.exe",
                software_type="relap5",
                input_name="indta",
            )
            staged = Relap5Adapter()._stage_input(request)
            self.assertEqual(staged.read_text(encoding="utf-8"), "EXPLICIT INPUT")

    def test_nusolsyslmr_input_deck_is_staged_as_indta(self):
        adapter = get_adapter("nusolsyslmr")
        source = (Path(__file__).resolve().parents[1] / "inputs" /
                  "nusolsyslmr" / "indta.i")
        with tempfile.TemporaryDirectory() as temp_dir:
            request = SoftwareRunRequest(
                case_no="TC-NUSOL",
                run_dir=Path(temp_dir),
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="indta.i",
            )
            staged = adapter._stage_input(request)
            self.assertEqual(staged.name, "indta")
            self.assertEqual(staged.read_bytes(), source.read_bytes())

    def test_specific_property_files_override_common_inputs(self):
        adapter = get_adapter("nusolsyslmr")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            common = root / "inputs" / "tpfh2o"
            specific = root / "inputs" / "nusolsyslmr" / "tpfh2o"
            source_dir = Path(temp_dir) / "unrelated"
            run_dir = Path(temp_dir) / "run"
            common.parent.mkdir(parents=True)
            specific.parent.mkdir(parents=True)
            source_dir.mkdir()
            run_dir.mkdir()
            common.write_bytes(b"COMMON")
            specific.write_bytes(b"NUSOL-SPECIFIC")
            with patch("software.relap5.ROOT", root):
                adapter._copy_property_files(source_dir, run_dir)
            self.assertEqual((run_dir / "tpfh2o").read_bytes(), b"NUSOL-SPECIFIC")

    def test_folder_prep_keeps_software_specific_property_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            inputs = root / "inputs"
            specific = inputs / "nusolsyslmr"
            specific.mkdir(parents=True)
            (inputs / "relap5").mkdir()
            (inputs / "tpfh2o").write_bytes(b"COMMON")
            (inputs / "relap5" / "tpfh2o").write_bytes(b"RELAP5")
            (specific / "tpfh2o").write_bytes(b"NUSOL-SPECIFIC")
            (specific / "indta.i").write_text("INPUT", encoding="utf-8")
            executable = specific / "nusolsyslmr.exe"
            executable.write_bytes(b"MZ")
            run_dir = Path(temp_dir) / "run"
            with patch("keywords.keyword_lib.ROOT", root), \
                 patch("keywords.keyword_lib.resolve_executable", return_value=executable), \
                 patch("keywords.keyword_lib.destination_executable", return_value=run_dir / executable.name), \
                 patch("keywords.keyword_lib.init_log", return_value=MagicMock()):
                ok = KeyPrisma.folder_prep(
                    "TC-NUSOL", run_dir, r"nusolsyslmr\indta.i",
                    r"nusolsyslmr\nusolsyslmr.exe",
                    software_key="nusolsyslmr")
            self.assertTrue(ok)
            self.assertEqual((run_dir / "tpfh2o").read_bytes(), b"NUSOL-SPECIFIC")
            self.assertEqual(
                (run_dir / "indta.i").read_text(encoding="utf-8"),
                "INPUT",
            )

    def test_folder_prep_derives_software_type_from_project_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            inputs = root / "inputs" / "nusolsyslmr"
            inputs.mkdir(parents=True)
            (root / "log").mkdir()
            (inputs / "indta.i").write_text("NUSOL INPUT", encoding="utf-8")
            config_path = root / "config.yaml"
            config_path.write_text(
                "softwareType: nusolsyslmr\n",
                encoding="utf-8",
            )
            context = RunContext(root=root, config_path=config_path)
            run_dir = root / "test_cases" / "case-nusol"
            executable = inputs / "nusolsyslmr.exe"
            executable.write_bytes(b"MZ")

            with patch(
                "keywords.keyword_lib.resolve_executable",
                return_value=executable,
            ) as resolve, patch(
                "keywords.keyword_lib.destination_executable",
                return_value=run_dir / executable.name,
            ), patch(
                "keywords.keyword_lib.init_log",
                return_value=MagicMock(),
            ):
                ok = KeyPrisma.folder_prep(
                    "TC-NUSOL",
                    "case-nusol",
                    r"nusolsyslmr\indta.i",
                    "nusolsyslmr.exe",
                    context=context,
                )

            self.assertTrue(ok)
            self.assertEqual(
                resolve.call_args.kwargs["software_key"],
                "nusolsyslmr",
            )
            self.assertEqual(
                (run_dir / "input.txt").read_text(encoding="utf-8"),
                "NUSOL INPUT",
            )
            self.assertEqual(
                (run_dir / "indta.i").read_text(encoding="utf-8"),
                "NUSOL INPUT",
            )

    def test_folder_prep_project_input_overrides_library_bin_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            specific = root / "inputs" / "nusolsyslmr"
            specific.mkdir(parents=True)
            (specific / "indta.i").write_text("CASE INPUT", encoding="utf-8")
            executable = specific / "nusolsyslmr.exe"
            executable.write_bytes(b"MZ")
            run_dir = root / "test_cases" / "case-01"
            run_dir.mkdir(parents=True)
            (run_dir / "indta.i").write_text("LIBRARY INPUT", encoding="utf-8")
            with patch(
                "keywords.keyword_lib.resolve_executable",
                return_value=executable,
            ), patch(
                "keywords.keyword_lib.destination_executable",
                return_value=run_dir / executable.name,
            ), patch(
                "keywords.keyword_lib.init_log",
                return_value=MagicMock(),
            ):
                ok = KeyPrisma.folder_prep(
                    "TC-NUSOL",
                    "case-01",
                    r"nusolsyslmr\indta.i",
                    "nusolsyslmr.exe",
                    context=RunContext(
                        root=root,
                        config_path=root / "config.yaml",
                    ),
                    software_key="nusolsyslmr",
                )
            self.assertTrue(ok)
            self.assertEqual(
                (run_dir / "indta.i").read_text(encoding="utf-8"),
                "CASE INPUT",
            )
            self.assertEqual(
                (run_dir / "input.txt").read_text(encoding="utf-8"),
                "CASE INPUT",
            )

    def test_missing_dll_returncode_has_actionable_diagnostic(self):
        from software.relap5 import _windows_startup_hint

        message = _windows_startup_hint(0xC0000135)
        self.assertIn("STATUS_DLL_NOT_FOUND", message)
        self.assertIn("MSVCR120D.dll", message)
        self.assertIn("architecture", _windows_startup_hint(0xC000007B))

    def test_nusolsyslmr_missing_runtime_is_rejected_before_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "nusolsyslmr.exe").write_bytes(b"MZ executable")
            request = SoftwareRunRequest(
                case_no="TC-NUSOL",
                run_dir=run_dir,
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="indta.i",
                project_root=run_dir,
            )
            adapter = get_adapter("nusolsyslmr")
            with patch("software.relap5.read_config", return_value={"runMode": "real"}), \
                 patch("software.relap5.subprocess.run") as run:
                with self.assertRaises(Exception) as ctx:
                    adapter.execute(request)
            run.assert_not_called()
            self.assertIn("缺少运行依赖", str(ctx.exception))
            self.assertIn("libifcoremdd.dll", str(ctx.exception))
            self.assertIn("32 位 Intel Fortran", str(ctx.exception))

    def test_nusolsyslmr_project_runtime_overrides_are_copied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            common = root / "inputs"
            specific = common / "nusolsyslmr"
            run_dir = root / "test_cases" / "case-01"
            common.mkdir(parents=True)
            specific.mkdir(parents=True)
            run_dir.mkdir(parents=True)
            executable = Path(temp_dir) / "source" / "nusolsyslmr.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"MZ executable")
            adapter = get_adapter("nusolsyslmr")
            for name in adapter.required_runtime_files:
                (common / name).write_bytes(b"COMMON")
                (specific / name).write_bytes(b"NUSOL-SPECIFIC")
            request = SoftwareRunRequest(
                case_no="TC-NUSOL",
                run_dir=run_dir,
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="indta.i",
                project_root=root,
            )
            with patch(
                "software.relap5.resolve_executable",
                return_value=executable,
            ):
                adapter._prepare_executable(request)
            for name in adapter.required_runtime_files:
                self.assertEqual(
                    (run_dir / name).read_bytes(),
                    b"NUSOL-SPECIFIC",
                )

    def test_nusolsyslmr_bundled_input_does_not_overwrite_requested_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "run"
            library_bin = root / "library" / "nusolsyslmr" / "bin"
            run_dir.mkdir()
            library_bin.mkdir(parents=True)
            executable = library_bin / "nusolsyslmr.exe"
            executable.write_bytes(b"MZ executable")
            (library_bin / "indta.i").write_text("BUNDLED CARD", encoding="utf-8")
            for name in get_adapter("nusolsyslmr").required_runtime_files:
                (library_bin / name).write_bytes(b"DLL")
            requested = run_dir / "indta.i"
            requested.write_text("SHORT CARD", encoding="utf-8")
            request = SoftwareRunRequest(
                case_no="TC-NUSOL",
                run_dir=run_dir,
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="indta.i",
                project_root=root,
            )
            adapter = get_adapter("nusolsyslmr")
            library = MagicMock()
            library.entry_root.return_value = root / "library" / "nusolsyslmr"
            library.resolve_executable.return_value = executable
            with patch("software.relap5.SoftwareLibrary", return_value=library), \
                 patch("software.executables.SoftwareLibrary", return_value=library), \
                 patch("software.relap5.resolve_executable", return_value=executable), \
                 patch("software.executables.destination_executable",
                       wraps=__import__("software.executables", fromlist=["destination_executable"]).destination_executable):
                adapter._prepare_executable(request)
            self.assertEqual(requested.read_text(encoding="utf-8"), "SHORT CARD")

    def test_run_software_relap5_simulate_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("software.relap5.read_config", return_value={"runMode": "simulate"}):
                ok = KeyPrisma.run_software(
                    "TC-R5", temp_dir, "relap5.exe", "relap5",
                    "input.txt", "custom.out", "60",
                )
            output = Path(temp_dir, "custom.out")
            self.assertTrue(ok)
            self.assertTrue(output.is_file())
            self.assertIn("Input processing completed successfully", output.read_text(encoding="utf-8-sig"))

    def test_run_software_relap5_accepts_nested_output_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("software.relap5.read_config", return_value={"runMode": "simulate"}):
                ok = KeyPrisma.run_software(
                    "TC-R5", temp_dir, "relap5.exe", "relap5",
                    "input.txt", "results/case-01/outdta", "60",
                )
            output = Path(temp_dir, "results", "case-01", "outdta")
            self.assertTrue(ok)
            self.assertTrue(output.is_file())
            self.assertIn(
                "Input processing completed successfully",
                output.read_text(encoding="utf-8-sig"),
            )

    def test_run_software_relap5_uses_default_name_for_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("software.relap5.read_config", return_value={"runMode": "simulate"}):
                ok = KeyPrisma.run_software(
                    "TC-R5", temp_dir, "relap5.exe", "relap5",
                    "input.txt", "results/", "60",
                )
            self.assertTrue(ok)
            self.assertTrue(Path(temp_dir, "results", "outdta").is_file())

    def test_run_software_rejects_output_path_outside_run_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = Path(temp_dir).parent / f"{Path(temp_dir).name}-escaped.out"
            try:
                self.assertFalse(KeyPrisma.run_software(
                    "TC-R5", temp_dir, "relap5.exe", "relap5",
                    "input.txt", f"../{outside.name}", "60",
                ))
                self.assertFalse(KeyPrisma.run_software(
                    "TC-R5", temp_dir, "relap5.exe", "relap5",
                    "input.txt", str(Path(temp_dir) / "absolute.out"), "60",
                ))
                self.assertFalse(outside.exists())
            finally:
                outside.unlink(missing_ok=True)

    def test_nusolsyslmr_uses_its_own_timeout_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "runMode": "real",
                "relapTimeoutSec": 1,
                "nusolsyslmrTimeoutSec": 4321,
            }
            request = SoftwareRunRequest(
                case_no="TC-NUSOL",
                run_dir=Path(temp_dir),
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="input.txt",
            )
            adapter = get_adapter("nusolsyslmr")
            fake_source = Path(temp_dir) / "source" / "nusolsyslmr.exe"
            fake_source.parent.mkdir()
            fake_source.write_bytes(b"MZ")
            fake_destination = Path(temp_dir) / "nusolsyslmr.exe"
            with patch("software.relap5.read_config", return_value=config), \
                 patch.object(adapter, "_prepare_executable",
                              return_value=(fake_source, fake_destination)), \
                 patch.object(adapter, "_copy_property_files"), \
                 patch.object(adapter, "_stage_input", return_value=Path(temp_dir) / "indta"), \
                 patch("software.relap5.subprocess.run",
                       side_effect=__import__("subprocess").TimeoutExpired("cmd", 4321)) as run:
                with self.assertRaises(Exception) as ctx:
                    adapter.execute(request)
            self.assertEqual(run.call_args.kwargs["timeout"], 4321)
            self.assertIn(
                f"{adapter.display_name} timeout after 4321s",
                str(ctx.exception),
            )

    def test_missing_dll_is_reported_when_no_output_is_produced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            request = SoftwareRunRequest(
                case_no="TC-NUSOL",
                run_dir=Path(temp_dir),
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="indta.i",
            )
            adapter = get_adapter("nusolsyslmr")
            fake_source = Path(temp_dir) / "source" / "nusolsyslmr.exe"
            fake_source.parent.mkdir()
            fake_source.write_bytes(b"MZ")
            failed = subprocess.CompletedProcess(
                ["nusolsyslmr.exe"], 0xC0000135)
            with patch("software.relap5.read_config", return_value={"runMode": "real"}), \
                 patch.object(adapter, "_prepare_executable",
                              return_value=(fake_source, Path(temp_dir) / "nusolsyslmr.exe")), \
                 patch.object(adapter, "_copy_property_files"), \
                 patch.object(adapter, "_stage_input", return_value=Path(temp_dir) / "indta"), \
                 patch("software.relap5.subprocess.run", return_value=failed):
                with self.assertRaises(Exception) as ctx:
                    adapter.execute(request)
            self.assertIn("returncode=3221225781", str(ctx.exception))
            self.assertIn("STATUS_DLL_NOT_FOUND", str(ctx.exception))
            self.assertIn("MSVCR120D.dll", str(ctx.exception))

    def test_run_software_nusolsyslmr_simulate_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("software.relap5.read_config", return_value={"runMode": "simulate"}):
                ok = KeyPrisma.run_software(
                    "TC-NUSOL", temp_dir, "nusolsyslmr.exe", "nusolsyslmr",
                    "input.txt", "outdta", "60",
                )
            output = Path(temp_dir, "outdta")
            self.assertTrue(ok)
            self.assertTrue(output.is_file())
            self.assertIn(
                "Input processing completed successfully",
                output.read_text(encoding="utf-8-sig"),
            )

    @unittest.skipUnless(
        os.name == "nt" and os.environ.get("PRISMA_REAL_NUSOL") == "1",
        "set PRISMA_REAL_NUSOL=1 to run the NUSOL-SYSLMR executable",
    )
    def test_nusolsyslmr_real_short_case(self):
        template = (
            Path(__file__).resolve().parents[1]
            / "software-library"
            / "nusolsyslmr"
            / "templates"
            / "indta.i"
        )
        deck = template.read_bytes()
        self.assertIn(
            b"201   10.0  1.0e-10   0.005      3     200   2000   2000",
            deck,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            (run_dir / "indta.i").write_bytes(deck)
            request = SoftwareRunRequest(
                case_no="TC-NUSOL-REAL",
                run_dir=run_dir,
                executable="nusolsyslmr.exe",
                software_type="nusolsyslmr",
                input_name="indta.i",
                output_name="outdta",
                timeout=120,
                project_root=run_dir,
            )
            adapter = get_adapter("nusolsyslmr")
            with patch("software.relap5.read_config", return_value={"runMode": "real"}):
                result = adapter.execute(request)

            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.analysis["input_ok"])
            self.assertEqual(result.analysis["diagnostics"], [])
            text = result.output_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("Final time=    10.0000", text)
            self.assertIn("Transient terminated by end of time step cards.", text)
            for key in (
                "p@304010000",
                "tempf@304010000",
                "p@364010000",
                "tempf@364010000",
            ):
                self.assertIsNotNone(adapter.extract_value(text, key))
            self.assertEqual(
                adapter.extract_value(text, "p@304010000"),
                "8.93893E+05",
            )
            self.assertEqual(
                adapter.extract_value(text, "tempf@304010000"),
                "753.142",
            )
            self.assertEqual(
                adapter.extract_value(text, "p@364010000"),
                "7.64535E+06",
            )
            self.assertEqual(
                adapter.extract_value(text, "tempf@364010000"),
                "466.913",
            )

    def test_legacy_run_relap5_delegates_to_adapter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("software.relap5.read_config", return_value={"runMode": "simulate"}):
                ok = KeyPrisma.run_relap5(
                    "TC-R5", temp_dir, "relap5.exe", "input.txt", "outdta", "60")
            self.assertTrue(ok)
            self.assertTrue(Path(temp_dir, "outdta").is_file())

    def test_invalid_timeout_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(KeyPrisma.run_software(
                "TC-R5", temp_dir, "relap5.exe", "relap5", "input.txt", "outdta", "abc"))
            self.assertFalse(KeyPrisma.run_software(
                "TC-R5", temp_dir, "relap5.exe", "relap5", "input.txt", "outdta", "0"))

    def test_empty_input_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(KeyPrisma.run_software(
                "TC-R5", temp_dir, "relap5.exe", "relap5", "", "outdta", "60"))
            self.assertFalse(Path(temp_dir, "outdta").exists())

    def test_unknown_software_type_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertFalse(KeyPrisma.run_software(
                "TC-X", temp_dir, "solver.exe", "unknown", "input.txt", "output.txt", "60"))
            self.assertFalse(Path(temp_dir, "output.txt").exists())


class CheckOutputTests(unittest.TestCase):
    def tearDown(self):
        release_log_file()

    def _write_output(
        self,
        temp_dir: str,
        text: str,
        filename: str = "output.txt",
    ) -> Path:
        path = Path(temp_dir, filename)
        path.write_text(text, encoding="utf-8")
        return path

    def _context(self, temp_dir: str, case_no: str) -> RunContext:
        root = Path(temp_dir)
        config_path = root / "config.yaml"
        config_path.write_text(
            "checkConfigPath: data/test_check.xlsx\n",
            encoding="utf-8",
        )
        check_path = root / "data" / "test_check.xlsx"
        check_path.parent.mkdir()
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["编号", "实际结果", "结论"])
        sheet.append([case_no, None, None])
        workbook.save(check_path)
        workbook.close()
        return RunContext(root=root, config_path=config_path)

    def _assert_result_row(
        self,
        context: RunContext,
        case_no: str,
        actual: str,
        conclusion: str,
    ) -> None:
        workbook = load_workbook(
            context.root / "data" / "test_check.xlsx",
            data_only=True,
        )
        rows = list(workbook.active.iter_rows(values_only=True))
        workbook.close()
        self.assertEqual(rows[1], (case_no, actual, conclusion))
        release_log_file()

    def test_generic_key_value_format(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = self._write_output(temp_dir, "keff: 1.00003\n")
            context = self._context(temp_dir, "TC-GENERIC")
            ok = KeyPrisma.check_output(
                "TC-GENERIC",
                temp_dir,
                output.name,
                "keff",
                "1.00003",
                context=context,
            )
            self.assertTrue(ok)
            self._assert_result_row(
                context,
                "TC-GENERIC",
                "1.00003",
                "通过",
            )

    def test_nested_output_path_can_be_checked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir, "results", "case-01", "output.txt")
            output.parent.mkdir(parents=True)
            output.write_text("keff: 1.00003\n", encoding="utf-8")
            with patch("keywords.keyword_lib.read_config", return_value={}), \
                 patch("keywords.keyword_lib.write_result_to_check"):
                ok = KeyPrisma.check_output(
                    "TC-NESTED",
                    temp_dir,
                    "results/case-01/output.txt",
                    "keff",
                    "1.00003",
                )
            self.assertTrue(ok)
            release_log_file()

    def test_relap5_parser_is_used_when_software_type_omitted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = self._write_output(
                temp_dir,
                "0   Vol.no.  pressure     tempf\n"
                " 100-010000  1.00000E+05  300.0\n",
            )
            context = self._context(temp_dir, "TC-RELAP")
            ok = KeyPrisma.check_output(
                "TC-RELAP",
                temp_dir,
                output.name,
                "p@100010000",
                "1.00000E+05",
                context=context,
            )
            self.assertTrue(ok)
            self._assert_result_row(
                context,
                "TC-RELAP",
                "1.00000E+05",
                "通过",
            )

    def test_nusolsyslmr_uses_relap5_output_parser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = self._write_output(
                temp_dir,
                "0   Vol.no.  pressure     tempf\n"
                " 100-010000  1.00000E+05  300.0\n",
            )
            context = self._context(temp_dir, "TC-NUSOL")
            ok = KeyPrisma.check_output(
                "TC-NUSOL",
                temp_dir,
                output.name,
                "p@100010000",
                "1.00000E+05",
                "nusolsyslmr",
                context=context,
            )
            self.assertTrue(ok)
            self._assert_result_row(
                context,
                "TC-NUSOL",
                "1.00000E+05",
                "通过",
            )

    def test_reserved_software_parser_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = self._write_output(temp_dir, "keff=1.0\n")
            with patch("keywords.keyword_lib.write_result_to_check") as write_result:
                ok = KeyPrisma.check_output(
                    "TC-NP", temp_dir, output.name, "keff", "1.0", "neutron_physics")
            self.assertFalse(ok)
            write_result.assert_not_called()
            release_log_file()


class ApiKeywordSchemaTests(unittest.TestCase):
    def test_run_software_uses_fixed_seven_cells(self):
        from api.server import KEYWORDS
        schemas = {item["name"]: item for item in KEYWORDS}
        schema = schemas["run_software"]
        self.assertEqual({f["index"] for f in schema["fields"]}, {0, 2, 3, 4, 5, 6})
        self.assertTrue(all(0 <= f["index"] <= 6 for f in schema["fields"]))
        check_fields = schemas["check_output"]["fields"]
        self.assertIn(5, {f["index"] for f in check_fields})


if __name__ == "__main__":
    unittest.main()
