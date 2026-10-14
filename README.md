# go-symbol-map

[![CI](https://github.com/bytejf/go-symbol-map/actions/workflows/ci.yml/badge.svg)](https://github.com/bytejf/go-symbol-map/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

**把 Go 文件里的 diff hunk 归属到它落在的那个函数上。** 走 tree-sitter 的 AST，不走正则。

```python
from go_symbol_map import map_hunks, parse_unified_diff

diffs = parse_unified_diff(patch_text)
mapped = map_hunks(diffs, new_source=read_head, old_source=read_base)

mapped[0].symbol.name  # '(*Service).Run'
mapped[0].head_lines  # (245, 268)
```

---

## 为什么需要这个

知道「这个 diff 改了哪些行」很容易，知道「**这个改动落在哪个函数里**」不容易 ——
而后者才是大多数下游用途真正需要的：覆盖面报告要按函数聚合、审查意见要挂在函数上、
变更影响分析要从函数出发、CI 缓存要按函数切分。

朴素做法是拿行号区间去和函数区间求交，但 diff 的世界里有四处容易做错：

**1. 一个 hunk 常常横跨好几个符号。** 新增一个大段、或者两个相邻函数同时被改，
git 会把它合成一个 hunk。不切开就没法说它「属于谁」。

**2. 两侧的行号是两套坐标。** `+` 行的行号属于新版本、`-` 行的属于旧版本。
拿旧行号去查新文件的符号表，会拆出跨实体的碎片。

**3. 纯删除的 hunk 在新版本里没有任何位置。** 它只能看旧版本的源码 —— 但结论
（那个函数现在叫什么、还在不在）又必须换算回新版本。

**4. 注释在语法树里不属于任何符号。** 文档注释、行尾注释、license 头各有各的归属
规则，错一处就会让整段代码的归属消失。

`go-symbol-map` 把这四件事都处理掉，并且保证一个结构性质：
**返回的每一片都恰好归属一个符号** —— 不存在「一片挂着一串符号」或「没有符号」
这两种含糊状态。

## 安装

```bash
pip install go-symbol-map
```

依赖只有 [`tree-sitter`](https://pypi.org/project/tree-sitter/) 与
[`tree-sitter-go`](https://pypi.org/project/tree-sitter-go/)。

> **`tree-sitter` 有上界（`<0.26`），是必需的**：0.26 运行时配 `tree-sitter-go`
> 0.25.0 的 grammar 在原生层不兼容，解析稍大的真实 Go 文件会 **SIGSEGV**
> （`tree-sitter-go` 至今停在 0.25.0）。等它发 0.26 配套版之后再去掉上界。
> 仓库里有一条几十 KB、含中文注释的回归用例专门走这条路径。

## 用法

### 从 diff 文本开始

`parse_unified_diff` 吃 `git diff` 风格的文本，产出按文件分组的 `FileDiff`：

```python
import subprocess
from go_symbol_map import map_hunks, parse_unified_diff


def read(path: str, rev: str) -> str | None:
    """读某个版本下的文件内容；不存在时返回 None。"""
    result = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


patch = subprocess.run(
    ["git", "diff", "-U0", "main", "HEAD"], capture_output=True, text=True
).stdout

mapped = map_hunks(
    parse_unified_diff(patch),
    new_source=lambda path: read(path, "HEAD"),
    old_source=lambda path: read(path, "main"),
)
```

**源码从哪来由你决定。** 库不碰 git、不碰文件系统 —— 两个回调可以接 `git show`、
可以接一个解压出来的目录、可以接内存字典、也可以接对象存储。所以它不需要
`pygit2` 那种原生依赖。

### 已经有自己的 diff 表示时

直接构造对象，不经 `parse_unified_diff`：

```python
from go_symbol_map import FileDiff, Hunk, map_hunks

diff = FileDiff(
    old_path="pkg/store.go",
    new_path="pkg/store.go",
    hunks=(
        Hunk(
            old_start=8,
            old_lines=1,
            new_start=8,
            new_lines=2,
            content="-\treturn nil\n+\treturn nil, nil\n+\t// 新增说明",
        ),
    ),
)

mapped = map_hunks([diff], new_source=read_head, old_source=read_base)
```

### 只想要符号表

`extract_symbols` 是纯函数，不涉及 diff：

```python
from go_symbol_map import extract_symbols

source = """package main

func Load(path string) error {
\treturn nil
}
"""
for symbol in extract_symbols(source):
    print(symbol.name, symbol.kind, symbol.start_line, symbol.end_line)
# package package 1 1
# Load function 3 5
```

`kind` 与 `start_line` / `end_line` 都是**从 1 开始、闭区间**，行号是该源码文件里的坐标。

### 结果怎么读

```python
for item in mapped:
    item.path  # 文件路径（删除文件取旧路径）
    item.symbol  # 归属的符号 —— 恒为一个
    item.hunk  # 切片本身，可能已从原 hunk 按实体边界切开
    item.head_lines  # symbol 在**新版本**里的行号范围；新版没有同名符号时为 None
```

| 字段 | 要注意的 |
|---|---|
| `item.hunk` | 可能比原 hunk 小。行号范围切分后并集等于原范围，正文拼接等于原正文 —— 两条都有用例钉住 |
| `item.symbol` | 纯删除的 hunk 归属取自**旧版**符号表，所以它的行号是旧版坐标 |
| `item.head_lines` | 就是这个原因存在的：按名字在新版里重查一遍。**拿 `symbol.start_line` 去读新文件会读到隔壁代码** |

## 设计要点

**1. 按实体边界切，空隙行归给相邻切片。**
逐行算归属，归属变化处开一个新切片；**不属于任何实体的行（空行、独立注释）
不触发切分**。实体之间一定有这种空隙行，所以不能按行号机械对半切 ——
那样切出来的碎片谁也不属于。

**2. 同名符号按「名字」比较，不按对象身份。**
新旧两侧的符号表是**两次独立提取**的结果，同一个函数天然是两个不同对象。
按身份比较会把「一行改成另一行」（`-旧` + `+新`，同一个实体）误切成两半。
也不能比整个对象：改动会让函数范围变化，同名实体两侧的行号本来就可能不同。

**3. 行号推进靠正文，不靠头字段。**
`@@ -10,2 +10,2 @@` 里的 `2` 是那一侧的**总行数**（改动 + 上下文），
不是改动行数。`+` 只推进新版计数、`-` 只推进旧版、**上下文行两侧都推进**。
只吃 `-U0` 的 diff 时头字段恰好等于改动行，所以照头字段取区间的写法在那种
输入下完全不显形 —— 换一种 diff 来源就全错，且没有任何信号。

**4. 注释按 Go 的文档注释约定并入实体。**
连续注释合成块（不限 `//` 还是 `/* */`），块末行 +1 == 实体首行则并入（文档注释），
否则块首行 -1 == 实体末行则并入（后置说明），都不满足就保持独立（license 头）。
**行尾注释不参与合并** —— 它与所在声明同行号、本来就在范围内，而若并入上方
的注释块，会把块的末行推到声明之后，文档注释就再也并不进去了。

**5. `.go` 文件解析不出符号时丢弃，非 `.go` 合成整文件符号。**
`.go` 解析不出符号是「**路径对不上**」的信号（文件改名后仍拿新路径查旧版符号表），
无条件合成符号会把这条诊断静默抹掉。而 `go.mod` 这类文件没有 Go 符号可归，
不给符号就会被当成「查不到符号表」丢掉 —— 那属于「结构上无法处理」。

## 边界

- **只支持 Go。** 符号提取写死了 Go 的节点类型。其它语言要么加 grammar，
  要么把 `extract_symbols` 换掉（`split_by_symbol` 与 `map_hunks` 都是语言无关的）。
- **只提取顶层声明。** 不进入函数体与类型体 —— 所以闭包、结构体字段不会被
  误当作符号。
- **不读 git、不碰文件系统。** 源码从哪来由调用方给。
- **不做模糊匹配。** 符号归属要么落在实体的行号范围里，要么就是没有。

## 开发

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
```

## License

[MIT](LICENSE)
