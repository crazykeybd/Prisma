"""为尚未接入的求解器保留统一适配器接口。"""

from __future__ import annotations

from software.base import AdapterNotImplementedError, SoftwareRunRequest, SoftwareRunResult


class ReservedSoftwareAdapter:
    """已登记但尚无具体求解器实现的软件适配器。"""

    def __init__(self, key: str, display_name: str, description: str):
        self.key = key
        self.display_name = display_name
        self.description = description
        self.status = "reserved"
        self.executable_name = ""

    def _not_implemented(self) -> None:
        raise AdapterNotImplementedError(
            f"{self.display_name} 适配器已预留但尚未实现；"
            "当前不会执行程序，也不会生成或伪造测试结果"
        )

    def prepare(self, request: SoftwareRunRequest) -> None:
        self._not_implemented()

    def build_command(self, request: SoftwareRunRequest) -> list[str]:
        self._not_implemented()
        return []

    def execute(self, request: SoftwareRunRequest) -> SoftwareRunResult:
        self._not_implemented()
        raise AssertionError("unreachable")

    def analyze_output(self, output_path) -> dict:
        self._not_implemented()
        return {}

    def extract_value(self, text: str, key: str):
        self._not_implemented()
        return None
