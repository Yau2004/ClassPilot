"""Preflight the known Windows CJK-path limitation before PostgreSQL setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable


@dataclass(frozen=True)
class PathIssue:
    label: str
    path: Path


def contains_cjk_path_character(value: str | Path) -> bool:
    """Detect Han, Kana, and Hangul characters implicated by the PG payload issue."""
    text = str(value)
    return any(
        0x3400 <= ord(char) <= 0x4DBF
        or 0x4E00 <= ord(char) <= 0x9FFF
        or 0xF900 <= ord(char) <= 0xFAFF
        or 0x20000 <= ord(char) <= 0x3134F
        or 0x3040 <= ord(char) <= 0x30FF
        or 0xAC00 <= ord(char) <= 0xD7AF
        for char in text
    )


def find_unsupported_cjk_paths(paths: Iterable[tuple[str, Path]]) -> list[PathIssue]:
    """Return all paths with CJK script characters; spaces remain supported."""
    return [PathIssue(label, path) for label, path in paths if contains_cjk_path_character(path)]


def format_path_issues(issues: Iterable[PathIssue]) -> str:
    selected = list(issues)
    rows = [f"{issue.label}：{issue.path}" for issue in selected]
    if not rows:
        return ""
    labels = {issue.label for issue in selected}
    install_only = labels == {"ClassPilot 程式安裝位置"}
    if install_only:
        next_step = (
            "請返回安裝位置頁，改選不含中文、日文或韓文的每使用者程式路徑；"
            "空格可保留。"
        )
    else:
        next_step = (
            "若清單包含使用者資料、PostgreSQL executable、資料或暫存位置，"
            "本版沒有安全的替代位置。請取消安裝／設定並等待支援版本；"
            "不要改 Windows 帳戶、搬動資料、使用共用系統位置或放寬權限。"
        )
    return (
        "此版本的 PostgreSQL 初始化不支援含中文、日文或韓文的路徑。"
        "為避免建立不完整資料庫，設定已在初始化前停止。\n\n"
        + "\n".join(rows)
        + "\n\n"
        + next_step
        + "\n路徑含空格可使用；不會刪除既有資料。"
    )
