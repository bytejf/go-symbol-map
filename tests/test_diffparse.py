"""`parse_unified_diff` 的单元测试。

切文件块只有两种错法，都很难发现：**少切**（上一个文件的正文混进下一个）
与**多切**（文件头被当成正文）。两边都钉住。
"""

from __future__ import annotations

from go_symbol_map import DEV_NULL, parse_unified_diff

FULL = """\
diff --git a/pkg/store.go b/pkg/store.go
index 1a2b3c4..5d6e7f8 100644
--- a/pkg/store.go
+++ b/pkg/store.go
@@ -10,3 +10,4 @@ func Load() {
 \tkey := "x"
-\told()
+\tnew()
+\tmore()
@@ -40,2 +41,0 @@ func Save() {
-\tdead()
-\tcode()
diff --git a/go.mod b/go.mod
index aaa..bbb 100644
--- a/go.mod
+++ b/go.mod
@@ -1,2 +1,3 @@
 module x
+go 1.22
"""

NEW_FILE = """\
diff --git a/new.go b/new.go
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/new.go
@@ -0,0 +1,2 @@
+package main
+
"""

DELETED_FILE = """\
diff --git a/gone.go b/gone.go
deleted file mode 100644
index 1234567..0000000
--- a/gone.go
+++ /dev/null
@@ -1,2 +0,0 @@
-package main
-
"""

BINARY = """\
diff --git a/logo.png b/logo.png
index 1234567..89abcde 100644
Binary files a/logo.png and b/logo.png differ
"""

MODE_ONLY = """\
diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
"""


def test_splits_by_file() -> None:
    """一个 patch 切成多个 FileDiff，顺序按出现顺序。"""
    diffs = parse_unified_diff(FULL)

    assert [d.path for d in diffs] == ["pkg/store.go", "go.mod"]


def test_strips_the_git_path_prefix() -> None:
    """``a/`` / ``b/`` 前缀要去掉 —— 下游拿这个路径去读文件。"""
    first, second = parse_unified_diff(FULL)

    assert (first.old_path, first.new_path) == ("pkg/store.go", "pkg/store.go")
    assert (second.old_path, second.new_path) == ("go.mod", "go.mod")


def test_multiple_hunks_per_file() -> None:
    """一个文件里的多个 hunk 都要在，且不串到下一个文件。"""
    first, second = parse_unified_diff(FULL)

    assert len(first.hunks) == 2
    assert len(second.hunks) == 1
    assert first.hunks[0].content.splitlines() == [
        ' \tkey := "x"',
        "-\told()",
        "+\tnew()",
        "+\tmore()",
    ]
    assert first.hunks[1].content.splitlines() == ["-\tdead()", "-\tcode()"]


def test_header_numbers() -> None:
    """四个数字按 git 的写法读；行数省略时为 1。"""
    first, second = parse_unified_diff(FULL)

    a, b = first.hunks
    assert (a.old_start, a.old_lines, a.new_start, a.new_lines) == (10, 3, 10, 4)
    assert (b.old_start, b.old_lines, b.new_start, b.new_lines) == (40, 2, 41, 0)
    assert second.hunks[0].new_lines == 3


def test_new_file_marks_is_new_and_starts_at_zero() -> None:
    """新增文件：``old_path`` 是 ``/dev/null``，旧侧起点是 0 不是 1。"""
    (diff,) = parse_unified_diff(NEW_FILE)

    assert diff.is_new is True
    assert diff.is_deleted is False
    assert diff.old_path == DEV_NULL
    assert diff.new_path == "new.go"
    assert (diff.hunks[0].old_start, diff.hunks[0].old_lines) == (0, 0)


def test_deleted_file_marks_is_deleted() -> None:
    """删除文件：``new_path`` 是 ``/dev/null``，新侧行数为 0。"""
    (diff,) = parse_unified_diff(DELETED_FILE)

    assert diff.is_deleted is True
    assert diff.is_new is False
    assert diff.new_path == DEV_NULL
    assert diff.path == "gone.go", "规范路径取旧路径"
    assert diff.hunks[0].new_lines == 0


def test_binary_block_yields_nothing() -> None:
    """二进制改动没有 ``---`` / ``+++``，也不产出 FileDiff。

    「文件变了但这里看不到」是设计行为：二进制改动没有任何行可以归属。
    """
    assert parse_unified_diff(BINARY) == []


def test_mode_only_change_yields_nothing() -> None:
    """纯模式变更同理 —— 没有行可归属。"""
    assert parse_unified_diff(MODE_ONLY) == []


def test_hunk_header_context_after_the_at_signs_is_ignored() -> None:
    """``@@`` 后面跟函数名之类的上下文，不参与解析。"""
    (diff,) = parse_unified_diff(
        "diff --git a/x.go b/x.go\n--- a/x.go\n+++ b/x.go\n@@ -1 +1 @@ func Load() {\n-a\n+b\n"
    )

    assert diff.hunks[0].content.splitlines() == ["-a", "+b"]


def test_non_body_line_closes_the_hunk() -> None:
    """碰到非正文行就收口，后续内容不再并进这个 hunk。"""
    text = (
        "diff --git a/x.go b/x.go\n--- a/x.go\n+++ b/x.go\n"
        "@@ -1 +1 @@\n-a\n+b\n"
        "diff --git a/y.go b/y.go\n--- a/y.go\n+++ b/y.go\n@@ -9 +9 @@\n-q\n+r\n"
    )

    first, second = parse_unified_diff(text)

    assert first.hunks[0].content.splitlines() == ["-a", "+b"]
    assert second.hunks[0].content.splitlines() == ["-q", "+r"]


def test_empty_input_yields_nothing() -> None:
    """空输入与「只有文件头」都返回空列表，不抛异常。"""
    assert parse_unified_diff("") == []
    assert parse_unified_diff("diff --git a/x b/x\nindex 1..2 100644\n") == []
