"""04 table: 按 Run 合并 RunInfo + annotate + 阶元, 派生数据类型, 输出总表 TSV 与 Excel.

用法: organise-sra table <Taxon> [--rules rules.json]
输入: runinfo.tsv (必需), annotate.tsv, taxonomy.tsv, manifest.json (可缺)
输出: table.tsv, samples.tsv, species_summary.tsv, <Taxon>_SRA_<date>.xlsx (--report-dir)
输入三表与规则都没变 (table.stamp.json 记录其 sha1) 且输出还在时跳过, 沿用上次的 Excel; --full 强制重做.
"""
import hashlib
import argparse
import json
from collections import Counter
from pathlib import Path

from .classify import (classify_runs, load_rules, ordered_categories,
                      summarize_samples, summarize_species)
from .paths import add_common, data_dir, report_dir, summary, TODAY
from .parse import norm_taxid
from .tsv import read_tsv, write_tsv
from .xlsx import write_xlsx

CAT_SHEETS = ["shallow_WGS", "deep_WGS", "long_read_WGS", "RNA-Seq", "Hi-C"]
QC_CATS = ["WGS_metagenomic", "low_data_WGS"]
DERIVED = ["platform_type_derived", "sample_key_derived", "BioProject_derived", "sample_total_Gb_derived", "category_derived",
           "category_rule_derived"]


def lines_of(path):
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def _rename_clash(cols, taken, suffix):
    """与已有列重名的列加后缀 (同 pandas merge suffixes=("", suffix))."""
    return {c: (c + suffix if c in taken else c) for c in cols}


def merge(runinfo, annotate, taxonomy):
    """(cols, rows) ×3 -> (cols, rows); 按 Run / 规范化 TaxID 左连接, 行数不变, 缺值 ''."""
    cols, rows = list(runinfo[0]), [dict(r) for r in runinfo[1]]
    a_cols, a_rows = annotate
    t_cols, t_rows = taxonomy
    if a_rows and t_rows:       # 样本属性与阶元同名 (如 species) -> 属性改名 sample_<名>, 阶元保留原名
        clash = {c: f"sample_{c}" for c in t_cols if c != "TaxID" and c in a_cols}
        a_cols = [clash.get(c, c) for c in a_cols]
        a_rows = [{clash.get(k, k): v for k, v in r.items()} for r in a_rows]
    if a_rows:
        new = _rename_clash([c for c in a_cols if c != "Run"], set(cols), "_annotate")
        by = {}
        for r in a_rows:
            by.setdefault(r.get("Run"), r)
        for r in rows:
            a = by.get(r.get("Run"), {})
            r.update({n: a.get(c, "") for c, n in new.items()})
        cols += list(new.values())
    if t_rows:
        new = _rename_clash([c for c in t_cols if c != "TaxID"], set(cols), "_taxonomy")
        by = {}
        for r in t_rows:
            by.setdefault(r.get("TaxID"), r)
        for r in rows:
            t = by.get(norm_taxid(r.get("TaxID", "")), {})
            r.update({n: t.get(c, "") for c, n in new.items()})
        cols += list(new.values())
    for r in rows:
        for c in cols:
            if r.get(c) is None:
                r[c] = ""
    assert len(rows) == len(runinfo[1]), "merge changed row count"
    return cols, rows


def qc_table(cols, t, d, manifest, rules):
    PCR = {x.upper() for x in rules["pcr_selections"]}
    rows = [{"issue": r["category_derived"], "Run": r["Run"], "detail": r["category_rule_derived"]}
            for r in t if r.get("category_derived") in QC_CATS]
    if "LibrarySelection" in cols:
        rows += [{"issue": "WGS_selection_PCR", "Run": r["Run"],
                  "detail": f"LibrarySelection=PCR, classified as {r['category_derived']}"}
                 for r in t if str(r.get("LibraryStrategy", "")).upper() == "WGS"
                 and str(r.get("LibrarySelection", "")).upper() in PCR]
    if "BioProject" in cols:
        rows += [{"issue": "empty_BioProject", "Run": r["Run"],
                  "detail": f"BioProject_derived={r['BioProject_derived']} (from SRA XML)" if r.get("BioProject_derived") else ""}
                 for r in t if r["BioProject"] == ""]
    rows += [{"issue": "run_not_found", "Run": x, "detail": "not in NCBI SRA"} for x in lines_of(d / "runs_not_found.txt")]
    rows += [{"issue": "annotate_missing", "Run": x, "detail": "no SRA XML"} for x in lines_of(d / "annotate_missing.txt")]
    rows += [{"issue": "taxonomy_missing", "Run": "", "detail": f"TaxID {x}"} for x in lines_of(d / "taxonomy_missing.txt")]
    rows += [{"issue": "manifest", "Run": "", "detail": x} for x in manifest.get("problems", [])]
    return ["issue", "Run", "detail"], rows


def fingerprint(d, rules, xlsx_dir):
    h = hashlib.sha1(json.dumps([rules, str(xlsx_dir)], sort_keys=True).encode())
    for n in ("runinfo.tsv", "annotate.tsv", "taxonomy.tsv"):
        f = d / n
        if f.exists():
            with open(f, "rb") as x:
                for b in iter(lambda: x.read(1 << 20), b""):
                    h.update(b)
        h.update(n.encode())
    return h.hexdigest()


def main(argv):
    p = argparse.ArgumentParser()
    add_common(p)
    p.add_argument("--rules")
    p.add_argument("--full", action="store_true", help="输入没变也重做")
    a, _ = p.parse_known_args(argv)
    d = data_dir(a)
    stamp = d / "table.stamp.json"
    fp = fingerprint(d, load_rules(a.rules), report_dir(a))
    try:
        old = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if not a.full and old.get("sha1") == fp and Path(old.get("xlsx", "")).exists() and (d / "table.tsv").exists():
        summary("04_table", ["输入与上次相同, 跳过 (要重做加 --full)", f"-> {old['xlsx']}"])
        return 0
    runinfo = read_tsv(d / "runinfo.tsv")
    annotate = read_tsv(d / "annotate.tsv", required=False)
    taxonomy = read_tsv(d / "taxonomy.tsv", required=False)
    mf = d / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    rules = load_rules(a.rules)

    if runinfo[1]:
        cols, t = merge(runinfo, annotate, taxonomy)
        t = classify_runs(t, rules)
        cols += [c for c in DERIVED if c not in cols]
    else:
        cols, t = runinfo[0] + ["category_derived", "category_rule_derived"], []
    s_cols, samples = summarize_samples(t, cols)
    sp_cols, species = summarize_species(samples, *taxonomy)
    write_tsv(d / "table.tsv", cols, t)
    write_tsv(d / "samples.tsv", s_cols, samples)
    write_tsv(d / "species_summary.tsv", sp_cols, species)

    sheets = {"Summary": (sp_cols, species), "Samples": (s_cols, samples)}
    for c in CAT_SHEETS:
        sheets[c] = (s_cols, [r for r in samples if r["category_derived"] == c])
    sheets["Other"] = (s_cols, [r for r in samples if r["category_derived"] not in CAT_SHEETS])
    sheets["Runs"] = (cols, t)
    sheets["QC"] = qc_table(cols, t, d, manifest, rules)
    q = [("taxon", a.taxon)] + [(k, manifest.get(k, "")) for k in ("mode", "taxid", "term", "date", "esearch_count",
                                                                     "experiments")]
    q += [("runs", len(t)), ("rules", json.dumps(rules, ensure_ascii=False)), ("table_date", TODAY)]
    sheets["Query"] = (["key", "value"], [[k, "" if v is None else str(v)] for k, v in q])
    for name, (c, r) in sheets.items():                 # keep empty sheets present with headers
        if not c:
            sheets[name] = (["(empty)"], [])
    path = write_xlsx(report_dir(a) / f"{a.taxon}_SRA_{TODAY}.xlsx", sheets)
    stamp.write_text(json.dumps({"sha1": fp, "xlsx": str(path), "date": TODAY}), encoding="utf-8")

    counts = Counter(r["category_derived"] for r in t)
    top = ", ".join(f"{c} {counts[c]}" for c in ordered_categories(counts)[:6])
    summary("04_table", [f"{len(t)} run / {len(samples)} 样本×类型 / {len(species)} 个 TaxID",
                         f"run 数: {top or '无'}", f"QC {len(sheets['QC'][1])} 条", f"-> {path}"])
    return 0

