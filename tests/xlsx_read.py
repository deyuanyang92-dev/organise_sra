"""测试用的极简 .xlsx 读取 (标准库): 工作表名、单元格值/类型、表头样式、冻结、筛选、列宽."""
import re
import xml.etree.ElementTree as ET
import zipfile

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


class Book:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        wb = ET.fromstring(self.z.read("xl/workbook.xml"))
        self.sheetnames = [s.get("name") for s in wb.find("m:sheets", NS)]
        st = ET.fromstring(self.z.read("xl/styles.xml"))
        fonts = st.find("m:fonts", NS)
        fills = st.find("m:fills", NS)
        self.xf = []
        for x in st.find("m:cellXfs", NS):
            font = fonts[int(x.get("fontId", 0))]
            fg = fills[int(x.get("fillId", 0))].find("m:patternFill/m:fgColor", NS)
            self.xf.append({"bold": font.find("m:b", NS) is not None, "fill": fg.get("rgb") if fg is not None else None})

    def sheet(self, name):
        root = ET.fromstring(self.z.read(f"xl/worksheets/sheet{self.sheetnames.index(name) + 1}.xml"))
        rows = []
        for r in root.find("m:sheetData", NS):
            vals = {}
            for c in r:
                j, t = _col(c.get("r")), c.get("t")
                if t == "inlineStr":
                    v = ("s", "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t")))
                else:
                    x = c.find("m:v", NS).text
                    v = ("n", float(x) if "." in x or "e" in x.lower() else int(x))
                vals[j] = (*v, self.xf[int(c.get("s", 0))])
            rows.append(vals)
        pane = root.find("m:sheetViews/m:sheetView/m:pane", NS)
        af = root.find("m:autoFilter", NS)
        widths = [float(c.get("width")) for c in root.find("m:cols", NS)] if root.find("m:cols", NS) is not None else []
        return {"rows": rows, "freeze": pane.get("topLeftCell") if pane is not None else None,
                "filter": af.get("ref") if af is not None else None, "widths": widths}

    def values(self, name):
        """-> [[值...]] (按列补 None)."""
        rows = self.sheet(name)["rows"]
        w = max((max(r) + 1 for r in rows if r), default=0)
        return [[r[j][1] if j in r else None for j in range(w)] for r in rows]
