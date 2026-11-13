"""Output parsing helpers for LOCUST 1.4 transient result files."""

from __future__ import annotations

import re


_KEY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*@\s*(\d{9})\s*$")
_VOLUME_SECTION = re.compile(r"^\s*===>\s*volume information\s*:?\s*$", re.I)
_SECTION = re.compile(r"^\s*===>")
_VOLUME_HEADER = re.compile(r"^\s*vol\.no\.\s+(.+?)\s*$", re.I)
_VOLUME_ROW = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def volume_number_from_key(key: str):
    """Return the LOCUST volume number for a pressure key."""
    match = _KEY.match(str(key or ""))
    if not match:
        return None
    variable, point = match.groups()
    if variable.casefold() not in {"p", "pressure"} or point[5:] != "0000":
        return None
    component = int(point[:3])
    volume = int(point[3:5])
    if component < 1 or volume < 1:
        return None
    return component * 100 + volume


def extract_value(text: str, key: str):
    """Extract the pressure from the last matching LOCUST volume table."""
    target = volume_number_from_key(key)
    if target is None:
        return None

    active = False
    columns = []
    value = None
    for line in str(text or "").splitlines():
        if _VOLUME_SECTION.match(line):
            active = True
            columns = []
            continue
        if _SECTION.match(line):
            active = False
            columns = []
            continue
        if not active:
            continue

        header = _VOLUME_HEADER.match(line)
        if header:
            columns = [item.casefold() for item in header.group(1).split()]
            continue
        if "pressure" not in columns:
            continue

        row = _VOLUME_ROW.match(line)
        if not row or int(row.group(1)) != target:
            continue
        tokens = row.group(2).split()
        index = columns.index("pressure")
        if index < len(tokens):
            value = tokens[index]
    return value
