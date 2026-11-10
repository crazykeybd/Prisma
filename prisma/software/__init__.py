"""可扩展的被测软件适配器包。

RELAP5 与 RELAP5 兼容的 NUSOL-SYSLMR 已实现；中子物理、子通道和
燃料性能分析软件已登记预留接口；用户还可以在软件库中自行登记
通用命令行软件（``generic_cli`` 适配器）。
"""

from software.base import (
    AdapterNotImplementedError,
    SoftwareAdapterError,
    SoftwareRunRequest,
    SoftwareRunResult,
    normalize_output_path,
    resolve_output_path,
)
from software.custom import GenericCommandAdapter
from software.library import (
    SoftwareLibrary,
    SoftwareLibraryError,
    normalize_support_files,
)
from software.registry import all_adapters, get_adapter, normalize_software_key, software_catalog

__all__ = [
    "AdapterNotImplementedError",
    "GenericCommandAdapter",
    "SoftwareAdapterError",
    "SoftwareRunRequest",
    "SoftwareRunResult",
    "SoftwareLibrary",
    "SoftwareLibraryError",
    "all_adapters",
    "get_adapter",
    "normalize_output_path",
    "normalize_support_files",
    "normalize_software_key",
    "resolve_output_path",
    "software_catalog",
]
