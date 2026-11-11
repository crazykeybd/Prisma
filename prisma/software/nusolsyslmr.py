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
    input_filename = "indta.i"
    input_filenames = ("indta.i",)
    support_files = ()
    arguments = ("-i", "{input}", "-o", "{output}")
    output_filename = "outdta"
    timeout_sec = 1800
    simulate_output = (
        "0$$$$$$$$ Input processing completed successfully.\n"
        "0MAJOR EDIT !!!time=    0.00000     sec\n"
        "p@304010000: 8.93893E+05\n"
        "tempf@304010000: 753.142\n"
        "p@364010000: 7.64535E+06\n"
        "tempf@364010000: 466.913\n"
    )
    required_runtime_files = (
        "libifcoremdd.dll",
        "libifportmd.dll",
        "libmmd.dll",
        "msvcr120d.dll",
    )
    runtime_help = (
        "需要 32 位 Intel Fortran 2013 Debug 运行库和 MSVCR120D.dll；"
        "请将这些文件放入软件库 bin/，或放入项目 inputs/nusolsyslmr/。"
    )
