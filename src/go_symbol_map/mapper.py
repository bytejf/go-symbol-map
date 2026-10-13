"""把 diff hunk 归属到它落在的那个符号上。

输入是「一组文件的改动」+ **两个源码读取器**（新版 / 旧版）。库不碰 git、
不读文件系统 —— 源码从哪来由调用方决定，所以它既能配 git，也能配一个
解压出来的源码目录、一个内存字典、一个对象存储。

归属分三步，每步都在处理一种「不能猜」：

1. **按实体边界把 hunk 切开**（:func:`split_by_symbol`）—— 一个 hunk 常常横跨
   好几个函数，不切就没法说它「属于谁」
2. **按改动行找符号**（:func:`_first_owner`）—— 两侧的行号是**两套坐标**，
   `+` 行查新版符号表、`-` 行查旧版
3. **把符号范围换算回新版**（:func:`_head_lines`）—— 纯删除的 hunk 归属取自
   旧版，旧坐标拿去读新文件会读到隔壁代码
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from go_symbol_map.extractor import extract_symbols
from go_symbol_map.models import (
    KIND_FILE,
    FileDiff,
    Hunk,
    MappedHunk,
    SourceReader,
    Symbol,
)

logger = logging.getLogger(__name__)


def map_hunks(
    diffs: list[FileDiff] | tuple[FileDiff, ...],
    *,
    new_source: SourceReader,
    old_source: SourceReader,
) -> list[MappedHunk]:
    """把每个 hunk 归属到包含其改动行的符号。

    先按实体边界把 hunk 拆开（:func:`split_by_symbol`），再逐片归属，最后丢掉
    没有符号的那些 —— 所以**返回的每一片都恰好归属一个符号**，不会出现
    「一片挂着一串符号」或「无归属」这两种含糊状态。

    有新增行时看**新版**侧（新版源码 + 新增行号），纯删除时看**旧版**侧
    （旧版源码 + 删除行号）。结果按文件路径排序、文件内保持 hunk 顺序，
    所以同一输入产生同一输出。

    Args:
        diffs: 待映射的文件改动
        new_source: 新版源码读取器 —— 给路径返回内容，文件不存在返回 ``None``
        old_source: 旧版源码读取器，同上

    Returns:
        :class:`MappedHunk` 列表，按路径、文件内 hunk 顺序排列
    """
    # 同一文件同一侧只算一次：路径 → 符号表
    new_cache: dict[str, list[Symbol]] = {}
    old_cache: dict[str, list[Symbol]] = {}
    results: list[MappedHunk] = []

    # 丢弃**分两类记，别合成一个数**：一类是正常噪声（改动落在所有实体之外），
    # 另一类是「路径对不上」的信号（文件改名后仍拿新路径去查旧版符号表就属于
    # 这类）。合成一个数时，后者的日志会写成前者的理由，排障直接跑偏 ——
    # 说错原因比丢东西本身更难发现
    outside_entity = 0
    no_symbol_table = 0

    for diff in sorted(diffs, key=lambda item: item.path):
        for hunk in diff.hunks:
            parts = split_by_symbol(
                hunk,
                new_symbols=_symbols(new_cache, new_source, diff.new_path),
                old_symbols=_symbols(old_cache, old_source, diff.old_path),
            )
            for part in parts:
                added, removed = changed_lines(part)
                if added:
                    symbols = _symbols(new_cache, new_source, diff.new_path)
                    changed = added
                elif removed:
                    # **旧版侧必须用 `old_path`**：文件改过名时它在旧版树里
                    # 还叫原来的名字，用新名字去查会得到空符号表
                    symbols = _symbols(old_cache, old_source, diff.old_path)
                    changed = removed
                else:
                    no_symbol_table += 1
                    logger.warning("hunk 两侧都没有改动行，无法归属：%s", diff.path)
                    continue

                if not symbols:
                    no_symbol_table += 1
                    # **别猜原因**：空符号表有两个来路 —— 文件在该版本里不存在，
                    # 或者文件在、但里面没有可归属的符号（软链接、只含注释的
                    # `.go`）。报成前一种会把后一种的排查方向带偏
                    logger.warning(
                        "未归类 hunk：%s 在该版本里没有符号表"
                        "（文件不存在，或文件里没有可归属的符号）",
                        diff.path,
                    )
                    continue

                symbol = _first_owner(symbols, changed)
                # 改动只涉及 license 头这类「与声明隔了空行的独立注释」或只涉及
                # 空行 —— 没有可审的实体。它们不是错误，只是不该进下一步
                if symbol is None:
                    outside_entity += 1
                    continue

                results.append(
                    MappedHunk(
                        hunk=part,
                        path=diff.path,
                        symbol=symbol,
                        head_lines=_head_lines(new_cache, new_source, diff.path, symbol),
                    )
                )

    if outside_entity:
        logger.info("丢弃 %d 个切片：改动落在所有实体之外（独立注释或空行）", outside_entity)
    if no_symbol_table:
        logger.warning("丢弃 %d 个切片：该侧查不到符号表，请检查路径", no_symbol_table)
    dropped = outside_entity + no_symbol_table
    logger.info("映射完成：%d 个切片（原始 %d 个）", len(results), len(results) + dropped)
    return results


def split_by_symbol(
    hunk: Hunk,
    new_symbols: list[Symbol] | tuple[Symbol, ...],
    old_symbols: list[Symbol] | tuple[Symbol, ...],
) -> list[Hunk]:
    """按实体边界把 hunk 拆成若干只属于单一符号的切片。

    逐行遍历正文、算出每行的归属，归属变化处开一个新切片。
    **不属于任何实体的行（空行、独立注释）不触发切分**，直接留在当前切片里
    —— 即「空隙行归给相邻切片」，这样每片都恰好对应一个实体。
    （实体之间一定有这种空隙行，所以不能按行号机械对半切。）

    ``+`` 行用**新文件侧**符号表、``-`` 行用**旧文件侧**符号表 —— 两侧行号是
    两套坐标，混用会把删除行的旧行号拿去查新文件的符号表，拆出跨实体的碎片。

    切片的行号范围与正文都是从原 hunk 切出来的，两者拼接回原样：行号范围切分后
    并集等于原范围，正文切片的拼接等于原正文。

    Args:
        hunk: 待拆分的 hunk
        new_symbols: 新文件侧的符号表
        old_symbols: 旧文件侧的符号表

    Returns:
        切片列表；只有一个归属时原样返回 ``[hunk]``
    """
    body = hunk.content.splitlines()
    if not body:
        return [hunk]

    # 第一遍：找出切分点与每段的归属。
    #
    # 按**名字**比较而不是对象身份：新旧两侧符号表是两次提取的结果，同名实体
    # 会是两个不同对象；用身份比较会把「一行改成另一行」（`-旧` + `+新`）
    # 这种**同一个**实体的改动误切成两半。也不能比整个对象：改动会让函数范围
    # 变化，同名实体在新旧两侧的行号本来就可能不同。
    cuts: list[int] = []
    current_name: str | None = None

    old_no, new_no = hunk.old_start, hunk.new_start
    for index, line in enumerate(body):
        head = line[:1]
        if head == "+":
            owner = _owner_at(new_symbols, new_no)
        elif head == "-":
            owner = _owner_at(old_symbols, old_no)
        else:
            # 上下文行不属于任何符号 —— 它两侧占号，但不产生归属
            owner = None
        advance_old, advance_new = _advances(line)
        old_no += advance_old
        new_no += advance_new

        if index == 0:
            cuts.append(0)
        elif owner is not None and current_name is not None and owner.name != current_name:
            cuts.append(index)

        # 空隙行（owner 为 None）不改 current_name，等于归给当前切片
        if owner is not None:
            current_name = owner.name

    if len(cuts) == 1:
        return [hunk]

    # 第二遍：按切分点切出切片，并用原 hunk 的游标算出各自的行号范围
    bounds = [*cuts, len(body)]
    return [_sub_hunk(hunk, body, bounds[i], bounds[i + 1] - 1) for i in range(len(cuts))]


def changed_lines(hunk: Hunk) -> tuple[list[int], list[int]]:
    """取出 hunk 里**真正改动**的行号。

    走正文数，**不用头字段**：``@@ -10,3 +10,7 @@`` 里的 ``7`` 是新文件侧的
    **总行数**（改动 + 上下文），不是改动行数 —— 拿它当改动行用，上下文行会被
    算成改动，连带影响范围界定。只吃 ``-U0`` 的 diff 时两者恰好相等，
    所以这个错在那种输入下不显形。

    Args:
        hunk: 待解析的 hunk

    Returns:
        ``(新增行号, 删除行号)``，均为 1-based、按出现顺序
    """
    old_no, new_no = hunk.old_start, hunk.new_start
    added: list[int] = []
    removed: list[int] = []
    for line in hunk.content.splitlines():
        head = line[:1]
        if head == "+":
            added.append(new_no)
        elif head == "-":
            removed.append(old_no)
        advance_old, advance_new = _advances(line)
        old_no += advance_old
        new_no += advance_new
    return added, removed


def _advances(line: str) -> tuple[bool, bool]:
    """这一行会让旧版 / 新版的计数器各前进几格。

    ``+`` 只推进新版、``-`` 只推进旧版、**上下文行两侧都推**；
    ``\\ No newline at end of file`` 不占号。

    Args:
        line: hunk 正文的一行

    Returns:
        ``(旧版是否前进, 新版是否前进)``
    """
    head = line[:1]
    if head == "+":
        return False, True
    if head == "-":
        return True, False
    if head == " " or line == "":
        return True, True
    return False, False


def _sub_hunk(hunk: Hunk, body: list[str], start: int, end: int) -> Hunk:
    """由原 hunk 的一段正文切出一个切片。

    Args:
        hunk: 原 hunk（提供起始行号）
        body: 原 hunk 的正文行
        start: 该段在正文中的起始下标（含）
        end: 该段在正文中的结束下标（含）

    Returns:
        切片
    """
    before = body[:start]
    segment = body[start : end + 1]
    old_delta = sum(_advances(line)[0] for line in before)
    new_delta = sum(_advances(line)[1] for line in before)
    return Hunk(
        old_start=hunk.old_start + old_delta,
        old_lines=sum(1 for line in segment if line.startswith("-")),
        new_start=hunk.new_start + new_delta,
        new_lines=sum(1 for line in segment if line.startswith("+")),
        content="\n".join(segment),
    )


def _head_lines(
    cache: dict[str, list[Symbol]],
    new_source: SourceReader,
    path: str,
    symbol: Symbol,
) -> tuple[int, int] | None:
    """按名字在**新版本**的符号表里重查归属符号的行号范围。

    不能直接用 ``symbol.start_line / end_line``：纯删除的 hunk 归属取自
    **旧版**符号表，行号与新版本对不上（差值正好等于删除行数），而读源码的人
    拿的是新版本 —— 拿旧坐标去读就是读到隔壁代码。

    Args:
        cache: 新版符号表缓存
        new_source: 新版源码读取器
        path: 文件在新版本里的路径
        symbol: 归属的符号（可能来自旧版符号表）

    Returns:
        ``(起始行, 结束行)``；同名符号不在新版本里（被整体删除或改名）时返回
        ``None`` —— 那时**不给**范围，而不是给一个旧坐标
    """
    for candidate in _symbols(cache, new_source, path):
        if candidate.name == symbol.name:
            return candidate.start_line, candidate.end_line
    logger.debug("符号 %s 不在新版本 %s 中，不给行号范围", symbol.name, path)
    return None


def _owner_at(symbols: list[Symbol] | tuple[Symbol, ...], line_no: int) -> Symbol | None:
    """取包含指定行的符号。

    Args:
        symbols: 文件符号表
        line_no: 行号（1-based）

    Returns:
        命中的符号；没有则 ``None``
    """
    for symbol in symbols:
        if symbol.start_line <= line_no <= symbol.end_line:
            return symbol
    return None


def _first_owner(symbols: list[Symbol] | tuple[Symbol, ...], changed: list[int]) -> Symbol | None:
    """取第一处落在实体里的改动行所属的符号。

    同一片内所有改动行必然落在同一个实体里（见 :func:`split_by_symbol`），
    所以取**第一个有归属的**改动行即可 —— 不能只看第一个改动行：切片开头可能
    先是空隙行（空行、独立注释），后面才是有归属的改动。

    文档注释已经并进实体的行号范围（见 `extractor`），所以注释行直接就能命中。

    Args:
        symbols: 该侧的符号表
        changed: 该侧的改动行号

    Returns:
        归属的符号；所有改动行都落在实体之外时返回 ``None``
    """
    for line_no in changed:
        if (symbol := _owner_at(symbols, line_no)) is not None:
            return symbol
    return None


def _symbols(
    cache: dict[str, list[Symbol]],
    source_of: SourceReader,
    path: str,
) -> list[Symbol]:
    """取某个文件的符号表，带缓存。

    **非 ``.go`` 文件解析不出符号时，合成一个整文件符号**（``kind="file"``，
    行号范围是全文件）—— 否则 `go.mod` 这类文件上的改动会被当成「查不到符号表」
    丢掉，而那正是「结构上压根审不到」。整文件当符号之后，调用方照样能给出
    一个有效的行号范围。

    **只对非 ``.go`` 生效**：``.go`` 文件解析不出符号是个**异常信号** ——
    文件改名后仍拿新路径查旧版符号表会走到这里，软链接、只含注释的 ``.go``
    也会。那条 WARNING 必须留着：无条件合成符号会把这类诊断静默抹掉，
    而它们本来正是最该被人看到的那几个。``.go`` 里没有符号**几乎一定是
    哪里不对**，非 ``.go`` 里没有 Go 符号则是常态。

    Args:
        cache: 缓存字典
        source_of: 该版本的源码读取器
        path: 文件路径

    Returns:
        符号列表；文件在该版本里不存在时为空列表
    """
    if path not in cache:
        source = source_of(path)
        if source is None:
            logger.debug("文件 %s 在该版本中不存在，符号表为空", path)
            cache[path] = []
        else:
            symbols = extract_symbols(source)
            if not symbols and PurePosixPath(path).suffix != ".go":
                symbols = [
                    Symbol(
                        name=PurePosixPath(path).name,
                        kind=KIND_FILE,
                        start_line=1,
                        end_line=max(len(source.splitlines()), 1),
                    )
                ]
            cache[path] = symbols
    return cache[path]
