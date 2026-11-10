"""RELAP5 运行适配器。"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from common.util_prisma import ROOT, read_config, resolve_path
from software.base import (
    SoftwareAdapterError,
    SoftwareRunRequest,
    SoftwareRunResult,
    resolve_output_path,
)
from software.executables import destination_executable, resolve_executable
from software.library import SoftwareLibrary
from software.relap5_output import analyze, extract_value


def _windows_startup_hint(returncode: int | None) -> str:
    """Return an actionable hint for common Windows startup failures."""
    if returncode is None:
        return ""
    code = int(returncode) & 0xFFFFFFFF
    hints = {
        0xC0000135: (
            "Windows STATUS_DLL_NOT_FOUND: a required runtime DLL is missing. "
            "Check the Intel Fortran runtime (irc_msg.dll, libifcoremdd.dll, "
            "libifportMD.dll, libmmd.dll) and MSVCR120D.dll."
        ),
        0xC000007B: (
            "Windows STATUS_INVALID_IMAGE_FORMAT: a DLL has the wrong CPU "
            "architecture (32-bit/64-bit) or is corrupt."
        ),
    }
    return hints.get(code, "")


class Relap5Adapter:
    """RELAP5 经典命令行求解器的适配器。

    运行目录内的输入卡会被暂存为 ``indta``，程序以
    ``relap5.exe -i indta -o outdta`` 执行。``tpf*`` 物性文件会从
    程序所在目录及 ``inputs/relap5`` 复制到运行目录。
    """

    key = "relap5"
    display_name = "RELAP5"
    status = "available"
    description = "RELAP5 热工水力系统分析程序"
    aliases = ("relap", "relap5.exe")
    executable_name = "relap5.exe"
    timeout_config_key = "relapTimeoutSec"
    requires_input_ok = True

    def analyze_output(self, output_path) -> dict:
        return analyze(output_path)

    def extract_value(self, text: str, key: str):
        return extract_value(text, key)

    @staticmethod
    def _project_root(request: SoftwareRunRequest) -> Path:
        return Path(request.project_root).resolve() if request.project_root else ROOT

    @staticmethod
    def _safe_run_dir(request: SoftwareRunRequest) -> Path:
        root = Relap5Adapter._project_root(request)
        run_dir = resolve_path(request.run_dir, root / "test_cases")
        if request.project_root is not None:
            try:
                run_dir.relative_to(root)
            except ValueError as exc:
                raise SoftwareAdapterError("运行目录必须位于当前项目内") from exc
        return run_dir

    def _prepare_executable(self, request: SoftwareRunRequest) -> tuple[Path, Path]:
        root = self._project_root(request)
        executable = resolve_executable(
            request.executable, request.run_dir, self.key, project_root=root
        )
        if executable is None:
            raise SoftwareAdapterError(
                f"no valid {self.display_name} executable found for {request.executable}; "
                "zero-byte, non-PE and non-ELF placeholders are ignored"
            )
        destination = destination_executable(
            executable, request.run_dir, self.key, project_root=root
        )
        return executable, destination

    def _copy_property_files(
        self, source_dir: Path, run_dir: Path, project_root: Path | None = None
    ) -> None:
        logger = logging.getLogger("prisma")
        # Copy from generic to specific so the program's bundled property
        # tables always win over the shared RELAP5 files.
        root = Path(project_root).resolve() if project_root else ROOT
        support_dirs = [root / "inputs", root / "inputs" / self.key, source_dir]
        for support_dir in dict.fromkeys(support_dirs):
            if not support_dir.is_dir():
                continue
            for support_file in support_dir.glob("tpf*"):
                if not support_file.is_file():
                    continue
                destination = run_dir / support_file.name
                if destination.resolve() == support_file.resolve():
                    continue
                try:
                    # 与旧 folder_prep 一致：每次运行刷新物性文件。
                    shutil.copy2(support_file, destination)
                except PermissionError as exc:
                    if destination.is_file() and destination.stat().st_size > 0:
                        logger.warning(
                            "support file busy, keeping existing %s: %s",
                            destination, exc,
                        )
                        continue
                    raise

    def _stage_input(self, request: SoftwareRunRequest) -> Path:
        run_dir = self._safe_run_dir(request)
        root = self._project_root(request)
        requested_name = str(request.input_name or "").strip()
        if not requested_name or Path(requested_name).name != requested_name:
            raise SoftwareAdapterError("输入卡名必须是文件名，不能包含目录")
        common_fallbacks = [
            run_dir / "input.txt",
            root / "inputs" / "input.txt",
            root / "inputs" / self.key / "input.txt",
            run_dir / "indta",
            root / "inputs" / "indta",
            root / "inputs" / self.key / "indta",
        ]
        library_templates = SoftwareLibrary().template_files(self.key)
        library_named = [
            path for path in library_templates if path.name.casefold() == requested_name.casefold()
        ]
        library_defaults = [
            path for path in library_templates
            if path.name.casefold() in ("input.txt", "indta")
        ]
        if requested_name.lower() == "indta":
            input_candidates = [
                run_dir / "indta",
                root / "inputs" / "indta",
                root / "inputs" / self.key / "indta",
                *common_fallbacks,
                *library_named,
                *library_defaults,
            ]
        else:
            # 显式输入必须优先，否则修改后的 input.txt 会被上次运行遗留的
            # indta 覆盖，导致测试继续使用旧输入卡。
            input_candidates = [
                run_dir / requested_name,
                root / "inputs" / requested_name,
                root / "inputs" / self.key / requested_name,
                *common_fallbacks,
                *library_named,
                *library_defaults,
            ]
        input_candidates = list(dict.fromkeys(input_candidates))
        input_source = next((candidate for candidate in input_candidates if candidate.is_file()), None)
        if input_source is None:
            raise SoftwareAdapterError(
                f"input deck not found (tried {[str(c) for c in input_candidates]})"
            )
        indta = run_dir / "indta"
        if input_source.resolve() != indta.resolve():
            shutil.copy2(input_source, indta)
        return indta

    def execute(self, request: SoftwareRunRequest) -> SoftwareRunResult:
        logger = logging.getLogger("prisma")
        root = self._project_root(request)
        run_dir = self._safe_run_dir(request)
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = resolve_output_path(
            run_dir, request.output_name, "outdta", label="输出文件名"
        )
        config = read_config(request.config_path)
        mode = str(config.get("runMode", "simulate")).lower()
        if mode != "real":
            output_path.write_text(
                "0$$$$$$$$ Input processing completed successfully.\n"
                "0MAJOR EDIT !!!time=    0.00000     sec\n",
                encoding="utf-8",
            )
            logger.info("[%s] %s(simulate) -> %s",
                        request.case_no, self.key, output_path)
            return SoftwareRunResult(output_path=output_path, analysis={"input_ok": True})

        executable_source, executable = self._prepare_executable(request)
        logger.info("[%s] %s executable=%s",
                    request.case_no, self.key, executable)
        self._copy_property_files(executable_source.parent, run_dir, project_root=root)
        self._stage_input(request)

        # 该 exe 拒绝覆盖已有 outdta；重启文件也一并清理，确保每次从干净状态开始。
        for stale_path in (
            run_dir / "outdta",
            run_dir / "rstplt",
            run_dir / "stripf",
            output_path,
        ):
            if stale_path.is_file():
                stale_path.unlink()

        timeout_sec = int(request.timeout or config.get(self.timeout_config_key, 1800) or 1800)
        try:
            with (run_dir / "screen.txt").open("w", encoding="utf-8", errors="replace") as screen:
                process = subprocess.run(
                    [str(executable), "-i", "indta", "-o", "outdta"],
                    cwd=run_dir,
                    stdout=screen,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_sec,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise SoftwareAdapterError(
                f"{self.display_name} timeout after {timeout_sec}s") from exc
        except OSError as exc:
            raise SoftwareAdapterError(
                f"{self.display_name} failed to start: {exc}") from exc

        outdta = run_dir / "outdta"
        if not outdta.is_file():
            hint = _windows_startup_hint(process.returncode)
            detail = f"; {hint}" if hint else ""
            raise SoftwareAdapterError(
                f"{self.display_name} produced no outdta "
                f"(returncode={process.returncode}{detail}); see screen.txt"
            )
        info = self.analyze_output(outdta)
        for diagnostic in info["diagnostics"][:20]:
            logger.warning("[%s] outdta: %s", request.case_no, diagnostic)
        logger.info(
            "[%s] %s returncode=%s, input_ok=%s, major_edits=%s, diagnostics=%s -> %s",
            request.case_no, self.key, process.returncode,
            info["input_ok"], info["major_edits"],
            len(info["diagnostics"]), outdta,
        )
        if outdta.resolve() != output_path.resolve():
            shutil.copy2(outdta, output_path)
        return SoftwareRunResult(
            output_path=output_path,
            returncode=process.returncode,
            analysis=info,
        )
