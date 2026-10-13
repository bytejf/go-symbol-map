"""把 Go 文件里的 diff hunk 归属到它落在的那个函数 / 类型 / 声明块上。

>>> from go_symbol_map import extract_symbols, parse_unified_diff, map_hunks
>>> symbols = extract_symbols(source)            # 纯函数：源码 → 符号
>>> diffs = parse_unified_diff(patch_text)       # 文本 → FileDiff
>>> mapped = map_hunks(diffs, new_source=..., old_source=...)
>>> mapped[0].symbol.name
'(*Service).Run'
"""

from go_symbol_map.diffparse import DEV_NULL, parse_unified_diff
from go_symbol_map.extractor import extract_symbols
from go_symbol_map.mapper import changed_lines, map_hunks, split_by_symbol
from go_symbol_map.models import (
    KIND_CONST,
    KIND_FILE,
    KIND_FUNCTION,
    KIND_IMPORT,
    KIND_INTERFACE,
    KIND_METHOD,
    KIND_PACKAGE,
    KIND_STRUCT,
    KIND_TYPE,
    KIND_VAR,
    FileDiff,
    Hunk,
    MappedHunk,
    SourceReader,
    Symbol,
)

__all__ = [
    "DEV_NULL",
    "KIND_CONST",
    "KIND_FILE",
    "KIND_FUNCTION",
    "KIND_IMPORT",
    "KIND_INTERFACE",
    "KIND_METHOD",
    "KIND_PACKAGE",
    "KIND_STRUCT",
    "KIND_TYPE",
    "KIND_VAR",
    "FileDiff",
    "Hunk",
    "MappedHunk",
    "SourceReader",
    "Symbol",
    "changed_lines",
    "extract_symbols",
    "map_hunks",
    "parse_unified_diff",
    "split_by_symbol",
]
