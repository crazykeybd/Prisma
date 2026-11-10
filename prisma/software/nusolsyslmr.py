"""NUSOL-SYSLMR 运行适配器。"""

from __future__ import annotations

from software.relap5 import Relap5Adapter


class NusolsyslmrAdapter(Relap5Adapter):
    """NUSOL-SYSLMR 的 RELAP5 兼容适配器。

    程序使用与 RELAP5 相同的 ``-i indta -o outdta`` 命令行及输入/输出
    格式，因此直接复用 RELAP5 的执行、分析和变量提取逻辑。
    """

    key = "nusolsyslmr"
    display_name = "NUSOL-SYSLMR"
    status = "available"
    description = "NUSOL-SYSLMR 系统分析程序（RELAP5 兼容输入/输出）"
    aliases = ("nusolsyslmr", "nusolsyslmr.exe", "nusol_syslmr")
    executable_name = "nusolsyslmr.exe"
    timeout_config_key = "nusolsyslmrTimeoutSec"
