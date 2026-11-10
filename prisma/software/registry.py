"""软件适配器注册表。

新增软件时只需实现适配器协议并在此注册；执行器和 HTTP API 都从
这里获取可用软件清单，避免两份软件清单逐渐不一致。
"""

from __future__ import annotations

import re

from software.base import SoftwareAdapterError
from software.library import SoftwareLibrary, SoftwareLibraryError
from software.nusolsyslmr import NusolsyslmrAdapter
from software.relap5 import Relap5Adapter
from software.reserved import ReservedSoftwareAdapter


def _normalize(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


_RELAP_ALIASES = ("relap", "relap5", "relap5.exe", "relap-5", "relap_5")
_NUSOLSYSLMR_ALIASES = (
    "nusolsyslmr", "nusolsyslmr.exe", "nusol_syslmr", "nusol_syslmr.exe",
    "nusol-syslmr", "nusol-syslmr.exe",
)
_NEUTRON_ALIASES = (
    "neutron_physics", "neutronics", "neutron", "中子物理", "中子物理软件",
    "neutron_physics_software",
)
_SUBCHANNEL_ALIASES = ("subchannel", "sub_channel", "子通道", "子通道软件")
_FUEL_ALIASES = (
    "fuel_performance", "fuel", "fuel_performance_analysis", "燃料性能",
    "燃料性能分析", "燃料性能分析软件",
)

_ADAPTERS = (
    Relap5Adapter(),
    NusolsyslmrAdapter(),
    ReservedSoftwareAdapter(
        "neutron_physics",
        "中子物理软件",
        "中子物理/反应堆物理分析软件（预留接口）",
    ),
    ReservedSoftwareAdapter(
        "subchannel",
        "子通道软件",
        "子通道热工水力分析软件（预留接口）",
    ),
    ReservedSoftwareAdapter(
        "fuel_performance",
        "燃料性能分析软件",
        "燃料性能/燃料棒行为分析软件（预留接口）",
    ),
)

_ALIASES = {
    **{alias: "relap5" for alias in _RELAP_ALIASES},
    **{alias: "nusolsyslmr" for alias in _NUSOLSYSLMR_ALIASES},
    **{alias: "neutron_physics" for alias in _NEUTRON_ALIASES},
    **{alias: "subchannel" for alias in _SUBCHANNEL_ALIASES},
    **{alias: "fuel_performance" for alias in _FUEL_ALIASES},
}


def _custom_adapters(library: SoftwareLibrary | None = None):
    """读取软件库中用户新增的软件定义（读取失败时不影响内置软件）。"""
    library = library or SoftwareLibrary()
    try:
        return tuple(library.custom_adapters())
    except (SoftwareLibraryError, OSError):
        return ()


def normalize_software_key(
    value: str, library: SoftwareLibrary | None = None
) -> str:
    """将标准键、英文别名、中文名称或用户软件标识转换为标准键。"""
    raw = str(value or "").strip()
    if not raw:
        raise SoftwareAdapterError("软件类型不能为空")
    normalized = _normalize(raw)
    for candidate in (raw.lower(), normalized):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    custom = _custom_adapters(library)
    for candidate in (raw.lower(), normalized):
        if any(adapter.key == candidate for adapter in custom):
            return candidate
    # 保留未登记错误信息中的原始名称，便于用户发现拼写问题。
    known = [adapter.key for adapter in (*_ADAPTERS, *custom)]
    raise SoftwareAdapterError(
        f"未知软件类型: {raw}；可选值: {', '.join(known)}"
    )


def get_adapter(
    software_type: str, library: SoftwareLibrary | None = None
):
    key = normalize_software_key(software_type, library)
    for adapter in (*_ADAPTERS, *_custom_adapters(library)):
        if adapter.key == key:
            return adapter
    raise SoftwareAdapterError(f"未知软件类型: {software_type}")


def all_adapters(library: SoftwareLibrary | None = None):
    return (*_ADAPTERS, *_custom_adapters(library))


def software_catalog(library: SoftwareLibrary | None = None) -> list[dict]:
    """返回适配器、软件库状态和程序可用性合并后的软件清单。"""
    library = library or SoftwareLibrary()
    catalog = []
    for adapter in all_adapters(library):
        try:
            assets = library.describe(adapter)
        except SoftwareLibraryError as exc:
            assets = {
                "library_directory": str(library.entry_root(adapter.key)),
                "installed": False,
                "program_ready": False,
                "state": "invalid",
                "version": "",
                "executable_path": "",
                "executable_name": str(
                    getattr(adapter, "executable_name", "") or ""
                ),
                "template_count": 0,
                "file_count": 0,
                "total_size": 0,
                "imported_at": None,
                "manifest_error": str(exc),
            }
        catalog.append({
            "key": adapter.key,
            "display_name": adapter.display_name,
            "status": adapter.status,
            "description": adapter.description,
            "available": adapter.status == "available",
            "executable_name": getattr(adapter, "executable_name", ""),
            "user_defined": bool(getattr(adapter, "user_defined", False)),
            "adapter_kind": "user" if getattr(adapter, "user_defined", False) else "builtin",
            "arguments": list(getattr(adapter, "arguments", ()) or ()),
            "input_filename": str(getattr(adapter, "input_filename", "") or ""),
            "input_filenames": [
                str(name)
                for name in (getattr(adapter, "input_filenames", ()) or ())
            ],
            "support_files": [
                str(name)
                for name in (getattr(adapter, "support_files", ()) or ())
            ],
            "output_filename": str(getattr(adapter, "output_filename", "") or ""),
            "timeout_sec": int(getattr(adapter, "timeout_sec", 0) or 0),
            **assets,
        })
    return catalog
