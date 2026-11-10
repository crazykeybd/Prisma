"""RELAP5 输出分析工具（保持与旧关键词模块的公开函数兼容）。"""

from __future__ import annotations

import re


def analyze(outdta_path) -> dict:
    """Scan a RELAP5 outdta file and summarize success markers and diagnostics."""
    input_ok = False
    major_edits = 0
    diagnostics = []
    diag_pattern = re.compile(r"^\s*\d*\*{6,}\s*\S")
    with open(outdta_path, "r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            text = line.strip()
            if "Input processing completed successfully" in text:
                input_ok = True
            elif "Errors detected during input processing" in text:
                input_ok = False
                diagnostics.append(text)
            elif text.startswith("0MAJOR EDIT"):
                major_edits += 1
            elif diag_pattern.match(line) and "****" in text:
                diagnostics.append(text)
            elif "error number" in text.lower() or "severe (" in text.lower():
                diagnostics.append(text)
    return {"input_ok": input_ok, "major_edits": major_edits, "diagnostics": diagnostics}


def extract_value(text: str, key: str):
    """Extract a RELAP5 value from a ``var@volume`` key or regex group."""
    match = re.match(r"^([A-Za-z]+)@(\d{9})$", key.strip())
    if match:
        variable, volume = match.group(1).lower(), match.group(2)
        target = {"p": "pressure"}.get(variable, variable)
        volume_pattern = f"{volume[:3]}-{volume[3:7]}{volume[7:]}"
        line_pattern = re.compile(rf"^\s*{re.escape(volume_pattern)}\s+(.+)$")
        header_pattern = re.compile(r"^\s*0?\s*Vol\.no\.\s+(.*)$")
        columns = None
        value = None
        for line in text.splitlines():
            header_match = header_pattern.match(line)
            if header_match:
                columns = header_match.group(1).split()
                continue
            if columns is None or target not in columns:
                continue
            line_match = line_pattern.match(line)
            if line_match:
                index = columns.index(target)
                tokens = line_match.group(1).split()
                if index < len(tokens):
                    value = tokens[index]
        return value
    try:
        result = re.search(key, text, re.M)
    except re.error:
        return None
    if result and result.groups():
        return result.group(1)
    return None
