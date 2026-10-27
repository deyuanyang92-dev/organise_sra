"""读用户自己的表 (TSV / CSV / .xlsx 第一个工作表), 取其中一列 run 号. 纯标准库."""
import csv
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ACC = re.compile(r"^[SED]RR\d+$")
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _read_xlsx(path, sheet=None):
    """-> 行列表 (每行为字符串列表). 只读值 (含共享字符串), 不读公式/样式."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = wb.find("m:sheets", _NS)
        names = [s.get("name") for s in sheets]
        i = names.index(sheet) if sheet else 0
        rid = sheets[i].get(f"{{{_NS['r']}}}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rid)
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = ET.fromstring(z.read(target))
    rows = []
    for r in root.find("m:sheetData", _NS):
        vals = {}
        for c in r:
            t, v = c.get("t"), c.find("m:v", _NS)
            if t == "inlineStr":
                x = "".join(e.text or "" for e in c.iter(f"{{{_NS['m']}}}t"))
            elif v is None:
                x = ""
            elif t == "s":
                x = shared[int(v.text)]
            else:
                x = v.text or ""
            vals[_col_index(c.get("r"))] = x
        rows.append([vals.get(j, "") for j in range(max(vals) + 1)] if vals else [])
    return rows


def read_table(path, sheet=None):
    """-> (表头, 数据行). xlsx 按文件头识别; 文本按首行 tab/逗号多少判断分隔符."""
    path = Path(path)
    with open(path, "rb") as f:
        is_zip = f.read(2) == b"PK"
    if is_zip:
        rows = _read_xlsx(path, sheet)
    else:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        first = text.split("\n", 1)[0]
        delim = "\t" if first.count("\t") >= first.count(",") else ","
        rows = list(csv.reader(text.splitlines(), delimiter=delim))
    rows = [r for r in rows if any(x.strip() for x in r)]
    if not rows:
        return [], []
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    return [x.strip() for x in rows[0]], rows[1:]


def tokens(cell):
    """一个单元格可含多个 run: 'SRR1;SRR2' / 'SRR1, SRR2'."""
    return [x.upper() for x in re.split(r"[\s,;]+", cell.strip()) if x]


def pick_column(header, rows, column=None):
    """column: 列名 (不分大小写) 或 1 起的列号; 不给则取 run 号比例最高的列. -> 列下标."""
    if column:
        if str(column).isdigit() and not any(h == str(column) for h in header):
            j = int(column) - 1
            if not 0 <= j < len(header):
                raise SystemExit(f"--column {column} 超出范围 (共 {len(header)} 列)")
            return j
        low = [h.lower() for h in header]
        if str(column).lower() not in low:
            raise SystemExit(f"找不到列 {column!r}; 现有列: {', '.join(header)}")
        return low.index(str(column).lower())
    best, j_best = 0, None
    for j in range(len(header)):
        vals = [r[j] for r in rows if r[j].strip()] + ([header[j]] if ACC.match(header[j].upper()) else [])
        hit = sum(all(ACC.match(t) for t in tokens(v)) for v in vals)
        if hit > best:
            best, j_best = hit, j
    if j_best is None:
        raise SystemExit("表中没有 SRR/ERR/DRR 号; 用 --column 指定列")
    return j_best


def has_header(header):
    """首行本身就是 run 号 (无表头的单列清单)."""
    return not any(ACC.match(t) for h in header for t in tokens(h))


def runs_from_table(path, column=None, sheet=None):
    """-> (合法 run 号去重保序, 非法条目, 表头, 数据行, 列下标)."""
    header, rows = read_table(path, sheet)
    if header and not has_header(header):
        rows = [header] + rows
        header = ["Run"] if len(header) == 1 else [f"col{j + 1}" for j in range(len(header))]
    j = pick_column(header, rows, column)
    seen, bad = [], []
    for r in rows:
        for t in tokens(r[j]):
            if t in seen or t in bad:
                continue
            (seen if ACC.match(t) else bad).append(t)
    return seen, bad, header, rows, j
