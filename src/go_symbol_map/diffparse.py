"""把 unified diff 文本切成 :class:`~go_symbol_map.models.FileDiff`。

一个便利模块，不是核心 —— 核心只认对象（`mapper.map_hunks`）。已经有自己的
diff 表示时，直接构造 :class:`FileDiff` / :class:`Hunk` 即可，不必经过这里。

只认 ``git diff`` 那套写法：``diff --git a/x b/x`` 起一个新文件块，
``@@`` 起一个新 hunk，文件路径取自 ``---`` / ``+++`` 两行。

**没有 ``---`` / ``+++`` 的块不会产出**：二进制改动与纯模式变更（``old mode`` /
``new mode``）git 都不写这两行，而它们本来也没有任何行可以归属。所以
「文件变了但这里看不到」是设计行为，不是漏解析。
"""

from __future__ import annotations

import re

from go_symbol_map.models import FileDiff, Hunk

#: 未变更侧的路径占位符 —— git 在新增 / 删除文件时写的就是它
DEV_NULL = "/dev/null"

#: ``diff --git a/x b/x``
_GIT_HEADER_RE = re.compile(r"^diff --git (\S+) (\S+)$")
#: ``@@ -old_start[,old_lines] +new_start[,new_lines] @@``（后接可选上下文）
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
#: ``--- a/x`` / ``+++ b/x``；已经删掉文件时是 ``/dev/null``
_OLD_PATH_RE = re.compile(r"^--- (\S+)")
_NEW_PATH_RE = re.compile(r"^\+\+\+ (\S+)")
#: hunk 正文的行首记号；``\`` 是「文件末尾没有换行」那一行的记号
_BODY_PREFIXES = ("+", "-", " ", "\\")


def parse_unified_diff(text: str) -> list[FileDiff]:
    """把一段 unified diff 文本切成按文件分组的 :class:`FileDiff`。

    Args:
        text: ``git diff`` 风格的文本，可以含多个文件

    Returns:
        文件改动列表，按出现顺序。没有可解析的文件块时返回空列表
    """
    diffs: list[FileDiff] = []
    old_path = new_path = ""
    is_new = is_deleted = False
    hunks: list[Hunk] = []
    in_header = False

    def flush_file() -> None:
        """把攒着的这个文件块收成一个 FileDiff。"""
        if old_path or new_path:
            diffs.append(
                FileDiff(
                    old_path=_strip_prefix(old_path),
                    new_path=_strip_prefix(new_path),
                    hunks=tuple(hunks),
                    is_new=is_new,
                    is_deleted=is_deleted,
                )
            )

    def reset_file() -> None:
        nonlocal old_path, new_path, is_new, is_deleted, hunks
        old_path = new_path = ""
        is_new = is_deleted = False
        hunks = []

    for line in text.splitlines():
        if _GIT_HEADER_RE.match(line) is not None:
            flush_file()
            reset_file()
            in_header = True
            continue

        if in_header:
            if (matched := _OLD_PATH_RE.match(line)) is not None:
                old_path = matched.group(1)
                continue
            if (matched := _NEW_PATH_RE.match(line)) is not None:
                new_path = matched.group(1)
                is_new = old_path == DEV_NULL
                is_deleted = new_path == DEV_NULL
                in_header = False
                continue
            continue  # index / mode / similarity index 之类的头行，不关心

        if (matched := _HUNK_HEADER_RE.match(line)) is not None:
            hunks.append(
                Hunk(
                    old_start=int(matched.group(1)),
                    # 行数省略时表示 1 行（git 的惯例）
                    old_lines=int(matched.group(2) or 1),
                    new_start=int(matched.group(3)),
                    new_lines=int(matched.group(4) or 1),
                    content="",
                )
            )
            continue

        if hunks and (line == "" or line[:1] in _BODY_PREFIXES):
            last = hunks[-1]
            hunks[-1] = Hunk(
                old_start=last.old_start,
                old_lines=last.old_lines,
                new_start=last.new_start,
                new_lines=last.new_lines,
                content=f"{last.content}\n{line}" if last.content else line,
            )
            continue

        # 既不是头、也不是正文：收口，丢掉这一段直到下一个 hunk
        if hunks:
            flush_file()
            reset_file()

    flush_file()
    return diffs


def _strip_prefix(path: str) -> str:
    """去掉 git 加的 ``a/`` / ``b/`` 前缀。

    Args:
        path: ``---`` / ``+++`` 行里的路径

    Returns:
        去掉前缀的路径；``/dev/null`` 原样返回
    """
    if path == DEV_NULL:
        return path
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path
