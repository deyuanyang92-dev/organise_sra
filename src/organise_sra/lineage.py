"""lineage: 给一个表 (或 SRR 清单), 在原表后面补上每个 run 的物种名与分类阶元.

用法:
  organise-sra lineage sra_list.txt                       # 一行一个 SRR/ERR/DRR
  organise-sra lineage samples.xlsx --column Run          # 任意 TSV/CSV/xlsx 的一列 (列名或 1 起列号)
  organise-sra lineage samples.tsv                        # 不给 --column: 自动取 run 号最多的列
  organise-sra lineage samples.xlsx --sheet Sheet2 -o out_dir
  organise-sra lineage samples.tsv --full                 # 另跑 annotate + table, 得到完整元数据与 Excel 总表
内部 = runinfo (--runs) + taxonomy 两步, 只需两三次 NCBI 请求/200 run.
输出 (-o, 默认 ./<文件名>_sra): <文件名>_lineage.tsv / .xlsx (原表全部列 + 新列), 以及 runinfo.tsv、taxonomy.tsv 等.
新列: ScientificName, TaxID, rank, phylum, class, order, family, genus, species, BioProject, LibraryStrategy,
      Platform, Gb, lineage_note (查不到/一格多个 run 时说明).
"""
import argparse
from pathlib import Path

from . import runinfo, taxonomy
from .intable import ACC, runs_from_table, tokens
from .parse import RANKS, norm_taxid
from .tsv import num, read_tsv, write_tsv
from .xlsx import write_xlsx

RUN_COLS = ["ScientificName", "TaxID"]
EXTRA = ["BioProject", "LibraryStrategy", "Platform"]


def main(argv, eutils=None):
    p = argparse.ArgumentParser(prog="organise-sra lineage", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("table", help="SRR 清单或 TSV/CSV/xlsx 表")
    p.add_argument("--column", help="run 号所在列: 列名或 1 起列号 [自动识别]")
    p.add_argument("--sheet", help="xlsx 工作表名 [第一个]")
    p.add_argument("-o", "--outdir", help="输出目录 [./<文件名>_sra]")
    p.add_argument("--full", action="store_true", help="另跑 annotate/table, 得到样本属性与 Excel 总表")
    p.add_argument("--email")
    p.add_argument("--api-key")
    a = p.parse_args(argv)
    src = Path(a.table)
    if not src.exists():
        print(f"[lineage] 找不到 {src}")
        return 1
    runs, bad, header, rows, j = runs_from_table(src, a.column, a.sheet)
    if not runs:
        print(f"[lineage] {src} 的列 {header[j] if header else ''} 中没有 SRR/ERR/DRR 号")
        return 1
    stem = src.stem
    out = Path(a.outdir or f"{stem}_sra")
    out.mkdir(parents=True, exist_ok=True)
    lst = out / "lineage_runs.txt"
    lst.write_text("".join(r + "\n" for r in runs))
    common = [stem, "-o", str(out)] + sum(([f"--{k}", v] for k, v in
                                           (("email", a.email), ("api-key", a.api_key)) if v), [])
    rc = runinfo.main(common + ["--runs", str(lst)], eutils=eutils)
    if rc and not (out / "runinfo.tsv").exists():
        return rc
    if taxonomy.main(common, eutils=eutils):
        return 1
    if a.full:
        from . import annotate, table
        annotate.main(common, eutils=eutils)
        table.main(common)

    info = {r["Run"]: r for r in read_tsv(out / "runinfo.tsv")[1]}
    tax = {r["TaxID"]: r for r in read_tsv(out / "taxonomy.tsv", required=False)[1]}
    new = RUN_COLS + ["rank", *RANKS] + EXTRA + ["Gb", "lineage_note"]
    taken = set(header)
    names = {c: (c if c not in taken else f"{c}_sra") for c in new}     # 不覆盖用户原有同名列
    out_rows, found = [], 0
    for r in rows:
        row = dict(zip(header, r))
        ids = [t for t in tokens(r[j]) if ACC.match(t)]
        hits = [info[x] for x in ids if x in info]
        notes = [f"{x} 查不到" for x in ids if x not in info] + [f"{t} 不是 run 号" for t in tokens(r[j])
                                                                  if not ACC.match(t)]
        vals = {c: "" for c in new}
        if hits:
            found += 1

            def join(f, hits=hits):
                return ";".join(dict.fromkeys(f(h) for h in hits if f(h)))

            vals["ScientificName"] = join(lambda h: h.get("ScientificName", ""))
            vals["TaxID"] = join(lambda h: norm_taxid(h.get("TaxID")) or "")
            ts = [tax.get(t, {}) for t in vals["TaxID"].split(";") if t]
            vals["rank"] = ";".join(dict.fromkeys(t.get("rank_derived", "") for t in ts if t.get("rank_derived")))
            for k in RANKS:
                vals[k] = ";".join(dict.fromkeys(t.get(k, "") for t in ts if t.get(k)))
            for k in EXTRA:
                vals[k] = join(lambda h, k=k: h.get(k, ""))
            vals["Gb"] = f"{sum(num(h.get('bases')) for h in hits) / 1e9:.3f}"
            if len(vals["TaxID"].split(";")) > 1:
                notes.append("一格内多个 run 属不同 TaxID")
        vals["lineage_note"] = "; ".join(notes)
        row.update({names[c]: v for c, v in vals.items()})
        out_rows.append(row)
    cols = header + [names[c] for c in new]
    tsv = write_tsv(out / f"{stem}_lineage.tsv", cols, out_rows)
    xl = write_xlsx(out / f"{stem}_lineage.xlsx", {"lineage": (cols, out_rows)})
    print(f"[lineage] {src.name}: 列 '{header[j]}', {len(runs)} 个 run; {found}/{len(rows)} 行取到阶元")
    if bad:
        print(f"   忽略 {len(bad)} 个非 run 号条目, 如 {' '.join(bad[:3])}")
    print(f"   -> {tsv}\n   -> {xl}")
    return 0 if found else 1
