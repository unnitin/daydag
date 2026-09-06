#!/usr/bin/env python3
"""Block real identifiers from entering a public repo.

Runs as a pre-commit hook and in CI. The repo is public; .env is the only
place real IDs are allowed to live, and it is gitignored.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("slack user id", re.compile(r"\bU0[0-9A-Z]{8,}\b")),
    ("slack channel id", re.compile(r"\bC0[0-9A-Z]{8,}\b")),
    ("slack dm id", re.compile(r"\bD0[0-9A-Z]{8,}\b")),
    ("google file id", re.compile(r"\b1[A-Za-z0-9_-]{24,}\b")),
    # A truncated id still leaks a prefix, e.g. `${GDOC_VP_EXPECTATIONS}\u2026`.
    ("truncated google file id", re.compile(r"\b1[A-Za-z0-9_-]{9,}\u2026")),
    ("uuid (notion/atlassian)", re.compile(r"\b[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")),
    ("databricks workspace id", re.compile(r"\b\d{16}\b")),
    ("internal email", re.compile(r"\b[\w.%+-]+@createmusicgroup\.com\b")),
    ("home path", re.compile(r"/Users/[a-z0-9]+/")),
    ("slack token", re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}")),
    ("github token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

# Placeholders and public constants that look like the real thing but aren't.
ALLOW = re.compile(
    r"^(U0{9}|C0{9}|D0{9}|0{16}|0{8}(-0{4}){3}-0{12}|1?0{24,})$"
    r"|USERID|CHANNELID|CHANGEME|example\.com|gemini-notes@google\.com"
)
# The scanner's own fixtures must contain pattern-shaped values. They are
# synthetic - verified by tests/test_scan_secrets.py itself.
SKIP_FILES = {".env.example", "scripts/scan_secrets.py", "tests/test_scan_secrets.py"}
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".gif", ".pdf"}


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Return (line_no, label, matched_value) for every disallowed identifier."""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pat in PATTERNS:
            for m in pat.finditer(line):
                if not ALLOW.search(m.group(0)):
                    out.append((lineno, label, m.group(0)))
    return out


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    if str(path) in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
        return []
    try:
        return scan_text(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []


def main(argv: list[str]) -> int:
    findings = [(p, *f) for p in map(Path, argv) for f in scan_file(p)]
    for path, lineno, label, value in findings:
        print(f"{path}:{lineno}: real {label} -> {value!r}", file=sys.stderr)
    if findings:
        print(
            f"\n{len(findings)} real identifier(s) blocked. This repo is public.\n"
            "Move the value into .env and reference it as ${VAR_NAME}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
