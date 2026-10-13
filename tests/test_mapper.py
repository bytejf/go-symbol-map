"""hunk → 符号映射的单元测试。

集成部分**不用真 git 仓库**：`map_hunks` 只认两个源码回调，所以喂两个
字典就够了 —— 比现场建仓库快，也把「归属逻辑」与「怎么拿到源码」隔开了。
"""

from __future__ import annotations

from go_symbol_map import (
    KIND_FILE,
    FileDiff,
    Hunk,
    SourceReader,
    Symbol,
    changed_lines,
    map_hunks,
    split_by_symbol,
)

# ------------------------------------------------------------------ 工具


def make_hunk(
    old_start: int,
    old_lines: int,
    new_start: int,
    new_lines: int,
    content: str = "",
) -> Hunk:
    """构造测试用 Hunk。

    Args:
        old_start: 旧文件起始行
        old_lines: 旧文件行数
        new_start: 新文件起始行
        new_lines: 新文件行数
        content: hunk 正文（不含 ``@@`` 行）

    Returns:
        Hunk
    """
    return Hunk(
        old_start=old_start,
        old_lines=old_lines,
        new_start=new_start,
        new_lines=new_lines,
        content=content,
    )


def reader(files: dict[str, str]) -> SourceReader:
    """把 ``{路径: 内容}`` 包成一个源码读取器。

    Args:
        files: 该版本的「文件系统」

    Returns:
        读取器；查不到的路径返回 ``None``（模拟「这个版本里没有这个文件」）
    """
    return files.get


def make_diff(
    hunks: list[Hunk],
    *,
    path: str = "a.go",
    old_path: str | None = None,
    is_new: bool = False,
    is_deleted: bool = False,
) -> FileDiff:
    """构造测试用 FileDiff。

    Args:
        hunks: hunk 列表
        path: 规范路径（新路径）
        old_path: 旧路径；默认与 ``path`` 相同
        is_new: 是否新增文件
        is_deleted: 是否删除文件

    Returns:
        FileDiff
    """
    return FileDiff(
        old_path=old_path if old_path is not None else path,
        new_path=path,
        hunks=tuple(hunks),
        is_new=is_new,
        is_deleted=is_deleted,
    )


# -------------------------------------------------------- changed_lines


def test_changed_lines_for_modification() -> None:
    """增删混合：改动行按各自的起点数。"""
    hunk = make_hunk(11, 1, 13, 2, content="\n".join(["-\told()", "+\tnew()", "+\tmore()"]))

    added, removed = changed_lines(hunk)

    assert added == [13, 14]
    assert removed == [11]


def test_changed_lines_pure_addition_has_no_removed() -> None:
    """纯新增（旧文件侧行数为 0）。"""
    hunk = make_hunk(14, 0, 16, 1, content="+\tnew()")

    assert changed_lines(hunk) == ([16], [])


def test_changed_lines_pure_deletion_has_no_added() -> None:
    """纯删除（新文件侧行数为 0）。"""
    hunk = make_hunk(10, 3, 9, 0, content="\n".join(["-\ta()", "-\tb()", "-\tc()"]))

    assert changed_lines(hunk) == ([], [10, 11, 12])


def test_changed_lines_skips_context_lines() -> None:
    """**上下文行不算改动行。**

    `@@ -10,2 +10,2 @@` 里的 ``2`` 是那一侧的**总行数**（改动 + 上下文），
    不是改动行数。照头字段取区间会把上下文行算进来 —— 而只吃 ``-U0`` 的 diff
    时两者恰好相等，所以这个错在那种输入下完全不显形。
    """
    hunk = make_hunk(10, 2, 10, 2, content="\n".join([" \tctx()", "-\told()", "+\tnew()"]))

    added, removed = changed_lines(hunk)

    assert added == [11], "上下文行占掉了第 10 行"
    assert removed == [11]


def test_changed_lines_ignores_no_newline_marker() -> None:
    r"""`\ No newline at end of file` 不占号、也不算改动。"""
    hunk = make_hunk(10, 1, 10, 2, content="-\ta()\n\\ No newline at end of file\n+\tb()")

    assert changed_lines(hunk) == ([10], [10])


# ------------------------------------------------------ split_by_symbol


def test_split_returns_same_hunk_when_single_symbol() -> None:
    """只属于一个符号时不拆（原样返回同一个对象）。"""
    symbols = [Symbol("A", "function", 3, 5)]
    hunk = make_hunk(0, 0, 3, 3, "\n".join(["+\tx := 1", "+\t_ = x", "+}"]))

    assert split_by_symbol(hunk, symbols, symbols) == [hunk]


def test_split_new_file_by_entity_boundary() -> None:
    """新增文件的 hunk 按实体边界拆开，**空隙行归给相邻切片**。"""
    symbols = [Symbol("package", "package", 1, 1), Symbol("A", "function", 3, 5)]
    hunk = make_hunk(
        0, 0, 1, 5, "\n".join(["+package main", "+", "+func A() {", "+\tprintln(1)", "+}"])
    )

    pieces = split_by_symbol(hunk, symbols, symbols)

    assert len(pieces) == 2
    # 切片 1：package + 紧跟的空行（空行不属于任何符号，归给相邻切片）
    assert changed_lines(pieces[0]) == ([1, 2], [])
    # 切片 2：函数体
    assert changed_lines(pieces[1]) == ([3, 4, 5], [])


def test_split_preserves_lines_and_content() -> None:
    """拆分不丢内容：行号并集还原、正文拼接还原。"""
    symbols = [Symbol("package", "package", 1, 1), Symbol("A", "function", 3, 5)]
    hunk = make_hunk(
        0, 0, 1, 5, "\n".join(["+package main", "+", "+func A() {", "+\tprintln(1)", "+}"])
    )

    pieces = split_by_symbol(hunk, symbols, symbols)

    added = [line for piece in pieces for line in changed_lines(piece)[0]]
    removed = [line for piece in pieces for line in changed_lines(piece)[1]]
    assert sorted(added) == changed_lines(hunk)[0]
    assert sorted(removed) == changed_lines(hunk)[1]
    assert "\n".join(piece.content for piece in pieces) == hunk.content


def test_split_mixed_add_and_remove() -> None:
    """增删混合的 hunk 也能按实体边界拆开。"""
    # 旧文件：函数 A（1-2）；新文件：函数 A（1-2）+ 函数 B（4-5）
    symbols = [Symbol("A", "function", 1, 2), Symbol("B", "function", 4, 5)]
    hunk = make_hunk(
        1, 2, 1, 4, "\n".join(["-line1", "-line2", "+line1", "+line2", "+", "+func B() {}"])
    )

    pieces = split_by_symbol(hunk, symbols, symbols)

    assert len(pieces) == 2
    assert changed_lines(pieces[0]) == ([1, 2, 3], [1, 2])
    assert changed_lines(pieces[1]) == ([4], [])


def test_split_matches_symbols_by_name_not_identity() -> None:
    """同名符号在新旧两侧是**两个不同对象**，不能按身份比较。

    用身份比较会把「一行改成另一行」（`-旧` + `+新`，同一个实体）误切成两半 ——
    两侧符号表是两次提取的结果，同名实体天然不是同一个对象。
    """
    new_symbols = [Symbol("A", "function", 1, 4)]
    old_symbols = [Symbol("A", "function", 1, 2)]
    hunk = make_hunk(1, 2, 1, 4, "\n".join(["-old1", "-old2", "+new1", "+new2"]))

    assert split_by_symbol(hunk, new_symbols, old_symbols) == [hunk]


# ------------------------------------------------------------ map_hunks

SERVICE = """package main

func A() {
\treturn
}

func B() {
\treturn
}
"""


def test_map_attributes_to_the_changed_function() -> None:
    """改动落在 B 的函数体里 → 归属 B。"""
    hunk = make_hunk(8, 1, 8, 1, "-\treturn\n+\treturn nil")
    files = {"a.go": SERVICE}

    mapped = map_hunks([make_diff([hunk])], new_source=reader(files), old_source=reader(files))

    assert [item.symbol.name for item in mapped] == ["B"]
    assert mapped[0].path == "a.go"
    assert mapped[0].head_lines == (7, 9)


def test_map_keeps_the_doc_comment_with_its_function() -> None:
    """新增「文档注释 + 函数」时，注释与函数归到同一片。"""
    new = SERVICE + "\n// Extra 的说明。\nfunc Extra() {}\n"
    old = SERVICE
    hunk = make_hunk(10, 0, 10, 2, "\n".join(["+\t// Extra 的说明。", "+func Extra() {}"]))
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert [item.symbol.name for item in mapped] == ["Extra"]


def test_map_keeps_a_piece_whose_first_changed_line_has_no_owner() -> None:
    """**回归用例**：切片首个改动行没有归属时，整片不能被误丢。

    新增「空行 + 文档注释 + 函数」时，**新增的空行在 diff 里是一个 `+` 行**、
    会进改动行，而它不属于任何符号。归属判定若只看第一个改动行，整个新函数会被
    判成「无归属」丢掉 —— 改动的实体凭空消失，**且不报错**。
    必须取第一个**有归属**的改动行。

    （注意别把它写成上下文行 `""` —— 那就复现不出这个场景了，本用例是照着
    真实 diff 的形状来的。）
    """
    old = SERVICE
    new = SERVICE + "\n// Extra 的说明。\nfunc Extra() {}\n"
    hunk = make_hunk(10, 0, 10, 3, "\n".join(["+", "+// Extra 的说明。", "+func Extra() {}"]))
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert [item.symbol.name for item in mapped] == ["Extra"]
    added, _ = changed_lines(mapped[0].hunk)
    assert added == [10, 11, 12], "空行也是一个 + 行，它跟着这一片一起走"


def test_map_drops_a_piece_that_is_only_a_blank_line() -> None:
    """整片只有空行 → 没有可归属的实体，丢弃（这是**正确**的丢弃）。"""
    old = SERVICE
    new = SERVICE + "\n"
    hunk = make_hunk(9, 0, 10, 1, "+")
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert mapped == []


def test_map_attributes_top_level_change_to_the_const_block() -> None:
    """包级常量块也是可归属的符号。"""
    old = "package main\n\nconst (\n\tA = 1\n)\n"
    new = "package main\n\nconst (\n\tA = 1\n\tB = 2\n)\n"
    hunk = make_hunk(0, 0, 5, 1, "+\tB = 2")
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert [(item.symbol.name, item.symbol.kind) for item in mapped] == [("const", "const")]


def test_map_drops_an_orphan_comment() -> None:
    """改动只涉及「与声明隔了空行的独立注释」→ 无归属，丢弃。

    它不属于任何实体，没有可归属的目标 —— 这不是错误，只是不该进下一步。
    """
    old = SERVICE
    new = "package main\n\nfunc A() {}\n\n// 独立说明\n\nfunc B() {}\n"
    hunk = make_hunk(0, 0, 5, 1, "+// 独立说明")
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert mapped == []


def test_map_pure_deletion_uses_the_old_sources() -> None:
    """纯删除的 hunk 归属取自**旧版**符号表，行号也是旧版的坐标。"""
    old = "package main\n\nfunc Gone() {\n\treturn\n}\n"
    new = "package main\n"
    hunk = make_hunk(3, 3, 2, 0, "\n".join(["-\tfunc Gone() {", "-\t\treturn", "-\t}"]))
    diff = make_diff([hunk], is_deleted=False)

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert [item.symbol.name for item in mapped] == ["Gone"]
    # Gone 在新版里已经不存在 —— 不给范围，而不是给一个旧坐标
    assert mapped[0].head_lines is None


def test_map_rename_plus_pure_deletion_uses_the_old_path() -> None:
    """**改名 + 纯删除**必须用旧路径查旧版符号表。

    用新名字去查会得到空符号表，整个 hunk 被当成「无归属」丢掉 —— 而它其实是
    一处真实的删除。这个 bug 靠真实数据对账才发现。
    """
    old = "package main\n\nfunc Gone() {\n\treturn\n}\n"
    hunk = make_hunk(3, 3, 2, 0, "\n".join(["-\tfunc Gone() {", "-\t\treturn", "-\t}"]))
    diff = make_diff([hunk], path="new_name.go", old_path="old_name.go")

    mapped = map_hunks(
        [diff],
        new_source=reader({}),  # 新名字下什么都没有
        old_source=reader({"old_name.go": old}),
    )

    assert [item.symbol.name for item in mapped] == ["Gone"]
    assert mapped[0].path == "new_name.go", "路径取规范路径（新版）"


def test_map_head_lines_track_the_surviving_symbol() -> None:
    """归属来自旧版时，`head_lines` 要按名字在**新版**里重查。

    纯删除的 hunk 归属取自旧版符号表，旧坐标直接拿去读新文件会读到隔壁代码。
    这里新版里同名函数往后挪了 5 行，`head_lines` 必须给出**新的**位置。
    """
    old = "package main\n\nfunc Kept() {\n\treturn\n}\n"
    new = "package main\n\n\n\n\n\nfunc Kept() {\n\treturn\n}\n"
    hunk = make_hunk(3, 3, 2, 0, "\n".join(["-\tfunc Kept() {", "-\t\treturn", "-\t}"]))
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({"a.go": old}))

    assert mapped[0].symbol.start_line == 3, "归属符号取自旧版"
    assert mapped[0].head_lines == (7, 9), "head_lines 是新版的坐标"


def test_map_gives_a_non_go_file_a_whole_file_symbol() -> None:
    """非 ``.go`` 文件解析不出符号时，**合成一个整文件符号**。

    否则这类文件上的改动会被当成「查不到符号表」丢掉 —— 而那正是「结构上
    压根审不到」。
    """
    hunk = make_hunk(0, 0, 1, 1, "+go 1.22")
    diff = make_diff([hunk], path="go.mod", old_path="/dev/null", is_new=True)

    mapped = map_hunks([diff], new_source=reader({"go.mod": "go 1.22\n"}), old_source=reader({}))

    assert [(item.symbol.name, item.symbol.kind) for item in mapped] == [("go.mod", KIND_FILE)]
    assert mapped[0].head_lines == (1, 1)


def test_map_drops_a_go_file_without_symbols() -> None:
    """``.go`` 文件解析不出符号**不合成**整文件符号 —— 那是「路径对不上」的信号。

    无条件合成会把那条 WARNING 静默抹掉，而它正是排查改名问题的入口。
    """
    hunk = make_hunk(0, 0, 1, 1, "+// 只有注释")
    diff = make_diff([hunk], path="x.go", old_path="/dev/null", is_new=True)

    mapped = map_hunks([diff], new_source=reader({"x.go": "// 只有注释\n"}), old_source=reader({}))

    assert mapped == []


def test_map_drops_a_missing_file() -> None:
    """文件在两个版本里都读不到 → 丢弃，不抛异常。"""
    hunk = make_hunk(10, 1, 10, 1, "-\told\n+\tnew")
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({}), old_source=reader({}))

    assert mapped == []


def test_map_empty_input() -> None:
    """没有 diff 就是空结果。"""
    assert map_hunks([], new_source=reader({}), old_source=reader({})) == []


def test_map_result_order_is_deterministic() -> None:
    """结果按路径排序 —— 同一输入必须产生同一顺序的输出。"""
    hunk = make_hunk(8, 1, 8, 1, "-\treturn\n+\treturn nil")
    files = {"b.go": SERVICE, "a.go": SERVICE}
    diffs = [make_diff([hunk], path="b.go"), make_diff([hunk], path="a.go")]

    mapped = map_hunks(diffs, new_source=reader(files), old_source=reader(files))

    assert [item.path for item in mapped] == ["a.go", "b.go"]


def test_map_keeps_every_line_across_pieces() -> None:
    """拆分之后，改动行一条都不能少（上界的守卫）。"""
    new = "package main\n\nfunc A() {}\n\nfunc B() {}\n"
    hunk = make_hunk(
        0, 0, 1, 5, "\n".join(["+package main", "+", "+func A() {}", "+", "+func B() {}"])
    )
    diff = make_diff([hunk])

    mapped = map_hunks([diff], new_source=reader({"a.go": new}), old_source=reader({}))

    added = [line for item in mapped for line in changed_lines(item.hunk)[0]]
    assert sorted(added) == changed_lines(hunk)[0]
    assert [item.symbol.name for item in mapped] == ["package", "A", "B"]


def test_source_reader_is_only_asked_for_paths_it_can_answer() -> None:
    """源码回调只吃路径 —— 库不碰 git、不碰文件系统，拿到什么由调用方决定。"""
    seen: list[str] = []

    def spy(path: str) -> str | None:
        seen.append(path)
        return SERVICE if path == "a.go" else None

    hunk = make_hunk(8, 1, 8, 1, "-\treturn\n+\treturn nil")
    mapped = map_hunks([make_diff([hunk])], new_source=spy, old_source=spy)

    assert set(seen) == {"a.go"}
    assert [item.symbol.name for item in mapped] == ["B"]
