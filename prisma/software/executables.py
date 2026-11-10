"""被测软件可执行文件的查找与运行文件复制。"""

from __future__ import annotations

import shutil
from pathlib import Path

from common.util_prisma import ROOT, resolve_path
from software.library import (
    SoftwareLibrary,
    SoftwareLibraryError,
    is_supported_executable,
    is_windows_executable,
    iter_runtime_bundle_files,
)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_executable(
    object_name,
    run_dir,
    software_key: str = "relap5",
    project_root: Path | None = None,
    library: SoftwareLibrary | None = None,
):
    """Find a runnable executable without accepting placeholder files.

    RELAP5 keeps the historical fallback to ``inputs/relap5`` so old test
    scripts continue to work. Other adapters only search their own software
    directory and the common ``inputs`` directory.
    """
    name = str(object_name or "").strip()
    if not name:
        return None
    root = Path(project_root).resolve() if project_root else ROOT
    library = library or SoftwareLibrary()
    run_dir = resolve_path(run_dir, root / "test_cases")
    software_dir = root / "inputs" / software_key
    candidates = [run_dir / name, software_dir / name, root / "inputs" / name]
    if software_key == "relap5":
        candidates.extend([
            software_dir / "relap5.exe",
            root / "inputs" / "relap5.exe",
        ])
    seen = set()
    for candidate in candidates:
        if project_root is not None and not _inside(root, candidate):
            continue
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if is_supported_executable(candidate):
            return candidate
    library_executable = library.resolve_executable(
        software_key, preferred_name=name
    )
    if library_executable:
        return library_executable
    if root != ROOT:
        for candidate in (
            ROOT / "inputs" / software_key / name,
            ROOT / "inputs" / name,
        ):
            if is_supported_executable(candidate):
                return candidate
    return None


def destination_executable(
    source: Path,
    run_dir,
    software_key: str = "relap5",
    project_root: Path | None = None,
    library: SoftwareLibrary | None = None,
    *,
    copy_runtime: bool = True,
) -> Path:
    """Copy the executable and, when requested, sibling runtime files."""
    root = Path(project_root).resolve() if project_root else ROOT
    run_dir = resolve_path(run_dir, root / "test_cases")
    if project_root is not None and not _inside(root, run_dir):
        raise ValueError("运行目录必须位于当前项目内")
    source = Path(source).resolve()
    source_root = source.parent
    library = library or SoftwareLibrary()
    try:
        library_root = library.entry_root(software_key)
    except SoftwareLibraryError:
        library_root = None
    if library_root and _inside(library_root, source):
        bundled_root = library_root / "bin"
        if _inside(bundled_root, source):
            source_root = bundled_root
    if copy_runtime:
        for runtime_file in iter_runtime_bundle_files(source_root):
            relative = runtime_file.relative_to(source_root)
            destination = run_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.resolve() == runtime_file.resolve():
                continue
            shutil.copy2(runtime_file, destination)
    else:
        destination = run_dir / source.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() != source.resolve():
            shutil.copy2(source, destination)
    destination = run_dir / source.relative_to(source_root)
    if not is_supported_executable(destination):
        raise ValueError("复制后的可执行程序无效")
    return destination
