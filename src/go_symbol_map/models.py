"""数据模型与常量。

库不碰 git、不碰文件系统 —— 源码从哪来由调用方决定（见 `mapper.map_hunks`
的两个 source 回调）。所以这里只有四个纯数据类。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# ------------------------------------------------------------------ 符号类型
#
# `Symbol.kind` 的取值。前七个由 `extractor.extract_symbols` 从 AST 上提取，
# `KIND_FILE` 是合成出来的（见 `mapper` 的整文件符号）。

KIND_PACKAGE = "package"
KIND_FUNCTION = "function"
KIND_METHOD = "method"
KIND_IMPORT = "import"
KIND_CONST = "const"
KIND_VAR = "var"
KIND_STRUCT = "struct"
KIND_INTERFACE = "interface"
KIND_TYPE = "type"
#: 非 Go 文件解析不出符号时，整个文件合成的那个符号
KIND_FILE = "file"

#: 一个源码读取器：给路径，返回该版本下的文件内容；文件不存在时返回 ``None``
SourceReader = Callable[[str], "str | None"]


@dataclass(frozen=True)
class Symbol:
    """源码里的一个可归属实体。

    Attributes:
        name: 函数 / 方法名；方法形如 ``(*Service).Run``。顶层声明块（import /
            const / var / package）的 name 与 kind 同名
        kind: 取值见 ``KIND_*``
        start_line: 起始行（1-based，含）。**文档注释已经并进来了** ——
            见 `extractor.extract_symbols`
        end_line: 结束行（1-based，含）
    """

    name: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Hunk:
    """一个 diff hunk。

    Attributes:
        old_start: 旧文件起始行号（1-based）
        old_lines: 旧文件行数；``0`` 表示该侧没有内容（纯新增）
        new_start: 新文件起始行号（1-based）
        new_lines: 新文件行数；``0`` 表示该侧没有内容（纯删除）
        content: hunk 正文，逐行带 ``+`` / ``-`` / 空格前缀，**不含** ``@@`` 头
    """

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    content: str


@dataclass(frozen=True)
class FileDiff:
    """一个文件的改动。

    Attributes:
        old_path: 旧路径；新增文件用 ``/dev/null`` 之类的占位
        new_path: 新路径；删除文件用占位
        hunks: 该文件的 hunk 列表
        is_new: 是否新增文件
        is_deleted: 是否删除文件
    """

    old_path: str
    new_path: str
    hunks: tuple[Hunk, ...]
    is_new: bool = False
    is_deleted: bool = False

    @property
    def path(self) -> str:
        """本改动的规范路径：删除文件取旧路径，其余取新路径。"""
        return self.old_path if self.is_deleted else self.new_path


@dataclass(frozen=True)
class MappedHunk:
    """一个**只属于单一符号**的 hunk 切片。

    Attributes:
        hunk: 切片本身。原 hunk 跨多个实体时会被 `mapper.split_by_symbol`
            按实体边界切开，所以它可能比原 hunk 小（行号范围更窄、正文更短）
        path: 文件路径 —— 与 :attr:`FileDiff.path` 同一套约定
        symbol: 归属的符号。**恒为一个、非可选**：无归属的切片在映射阶段
            就被丢掉了，所以不存在「一个切片挂着几个符号」或「没有符号」这两种
            含糊状态
        head_lines: :attr:`symbol` 在**新版本**里的行号范围 ``(起始, 结束)``。
            归属可能取自旧版符号表（纯删除的 hunk 就是），那时的行号是旧版的
            坐标，直接拿去读新文件会读到隔壁代码。查不到同名符号
            （被整体删除或改名）时为 ``None``
    """

    hunk: Hunk
    path: str
    symbol: Symbol
    head_lines: tuple[int, int] | None
