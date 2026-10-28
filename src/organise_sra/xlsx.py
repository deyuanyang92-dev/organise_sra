"""纯标准库的 .xlsx 写出 (Office Open XML = zip + XML), 取代 xlsxwriter.

格式同原 xlsxwriter 版: 粗体表头、_derived 表头黄底 (#FFF2CC)、冻结首行、自动筛选、列宽 (8–50)。
文本一律写成内联字符串 (不会被 Excel 当成公式/网址/数字); int/float 写成数值。
逐行流式写入 zip, Annelida 1.5 万行 × 400 列不占大内存。
"""
import datetime
import re
import zipfile
from xml.sax.saxutils import escape

MAX_CELL = 32767                                    # Excel 单元格字符上限
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")   # XML 1.0 不允许的字符

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{NS}">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def col_letter(j):
    s = ""
    j += 1
    while j:
        j, r = divmod(j - 1, 26)
        s = chr(65 + r) + s
    return s


def _text(v):
    return escape(_BAD.sub("", str(v))[:MAX_CELL])


def _cell(ref, v, style=0):
    st = f' s="{style}"' if style else ""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{_text(v)}</t></is></c>'
    if v != v or v in (float("inf"), float("-inf")):
        return ""
    return f'<c r="{ref}"{st}><v>{v!r}</v></c>'


def _sheet(zf, i, cols, rows):
    """rows: 可迭代的值列表 (与 cols 对齐); None / '' 不写."""
    rows = list(rows)
    last = col_letter(max(len(cols) - 1, 0))
    widths = []
    for j, c in enumerate(cols):
        sample = [len(str(r[j])) for r in rows[:300] if r[j] is not None]
        w = min(max(8, max([len(c)] + sample) + 2), 50)
        widths.append(int((w * 7 + 5) / 7 * 256) / 256)       # 字符数 -> Excel 存储宽度 (同 xlsxwriter, 7 px 字宽 + 5 px 边距)
    with zf.open(f"xl/worksheets/sheet{i}.xml", "w") as f:
        head = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS}" xmlns:r="{NS_R}">'
                f'<dimension ref="A1:{last}{len(rows) + 1}"/>'
                '<sheetViews><sheetView workbookViewId="0"'
                + (' tabSelected="1"' if i == 1 else "")
                + '><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
                '<sheetFormatPr defaultRowHeight="15"/><cols>'
                + "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>' for j, w in enumerate(widths))
                + '</cols><sheetData><row r="1">'
                + "".join(_cell(f"{col_letter(j)}1", c, 2 if c.endswith("_derived") else 1) for j, c in enumerate(cols))
                + "</row>")
        f.write(head.encode("utf-8"))
        buf = []
        letters = [col_letter(j) for j in range(len(cols))]
        for n, r in enumerate(rows, start=2):
            cells = "".join(_cell(f"{letters[j]}{n}", v) for j, v in enumerate(r) if v is not None and v != "")
            buf.append(f'<row r="{n}">{cells}</row>')
            if len(buf) >= 500:
                f.write("".join(buf).encode("utf-8"))
                buf = []
        tail = "".join(buf) + f'</sheetData><autoFilter ref="A1:{last}{max(len(rows) + 1, 2)}"/></worksheet>'
        f.write(tail.encode("utf-8"))
    return f"$A$1:${last}${max(len(rows) + 1, 2)}"


def write_xlsx(path, sheets):
    """sheets: {工作表名: (cols, rows)}, rows 为 dict 或与 cols 对齐的 list. 目标文件被占用时另存为 <name>_HHMM.xlsx."""
    try:
        open(path, "ab").close()
    except PermissionError:
        path = path.with_name(f"{path.stem}_{datetime.datetime.now():%H%M}{path.suffix}")
        print(f"   (原文件被占用, 另存为 {path.name})")
    names = [n[:31] for n in sheets]
    # compresslevel 1: Annelida 1.5M 格 压缩 1.2 s -> 0.4 s, 文件大 ~15%
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.sheet.main+xml"/>'
                    f'<Override PartName="/xl/styles.xml" ContentType="{CT}.styles+xml"/>'
                    + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{CT}.worksheet+xml"/>'
                              for i in range(1, len(names) + 1)) + "</Types>")
        zf.writestr("_rels/.rels",
                    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG_R}">'
                    f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/styles.xml", STYLES)
        refs = []
        for i, (cols, rows) in enumerate(sheets.values(), start=1):
            cols = [str(c) for c in cols]
            vals = ([r.get(c) for c in cols] if isinstance(r, dict) else list(r) for r in rows)
            refs.append(_sheet(zf, i, cols, vals))
        zf.writestr("xl/_rels/workbook.xml.rels",
                    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="{PKG_R}">'
                    + "".join(f'<Relationship Id="rId{i}" Type="{NS_R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                              for i in range(1, len(names) + 1))
                    + f'<Relationship Id="rId{len(names) + 1}" Type="{NS_R}/styles" Target="styles.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml",
                    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{NS}" xmlns:r="{NS_R}">'
                    '<bookViews><workbookView/></bookViews><sheets>'
                    + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                              for i, n in enumerate(names, start=1))
                    + "</sheets><definedNames>"
                    + "".join(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
                              f"'{escape(n.replace(chr(39), chr(39) * 2))}'!{ref}</definedName>"
                              for i, (n, ref) in enumerate(zip(names, refs)))
                    + "</definedNames></workbook>")
    return path
