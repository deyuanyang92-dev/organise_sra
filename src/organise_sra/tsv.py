"""纯标准库的 TSV 读写: 表 = (列名列表, 行 dict 列表), 值一律为字符串 (同 pandas dtype=str, 空值 '')."""
import csv
import sys
from pathlib import Path

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))   # study_abstract 等长字段


def read_tsv(path, required=True):
    """-> (cols, rows). 文件不存在: required 时退出并提示, 否则返回空表."""
    path = Path(path)
    if not path.exists():
        if required:
            raise SystemExit(f"缺少输入 {path.name}: 请先运行生成它的步骤")
        return [], []
    with open(path, encoding="utf-8", newline="") as f:
        r = csv.reader(f, delimiter="\t")
        cols = next(r, [])
        rows = [dict(zip(cols, x + [""] * (len(cols) - len(x)))) for x in r if x]
    return cols, rows


def columns(rows, first=()):
    """各行键的并集, 按首次出现顺序 (first 中的列排最前)."""
    out = dict.fromkeys(first)
    for r in rows:
        out.update(dict.fromkeys(r))
    return list(out)


def write_tsv(path, cols, rows):
    """与 pandas to_csv(sep='\\t', index=False) 相同格式 (QUOTE_MINIMAL, '\\n' 换行)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
    return path


def num(x):
    """'2000000000' -> 2e9; 空/非数字 -> 0.0."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0
