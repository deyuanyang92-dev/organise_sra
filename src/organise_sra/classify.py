"""数据类型派生 (NCBI 无"浅层"字段, 按样本数据量推断) + 样本/物种汇总 + 下载清单筛选. 纯函数, 无网络."""
import json
import math
import re
from pathlib import Path

from .tsv import num

HERE = Path(__file__).resolve().parent
CATEGORY_ORDER = ["shallow_WGS", "deep_WGS", "long_read_WGS", "low_data_WGS", "RNA-Seq", "long_read_RNA", "Hi-C",
                  "RAD-Seq", "AMPLICON", "WGS_metagenomic"]
SAMPLE_INFO = ["experiment_title", "study_title", "geo_loc_name", "lat_lon", "collection_date",
               "specimen_voucher", "isolate", "tissue"]
LINEAGE = ["rank_derived", "phylum", "class", "order", "family", "genus", "species"]


def load_rules(path=None):
    return json.loads(Path(path or HERE / "rules.json").read_text(encoding="utf-8"))


def platform_type(platform, rules):
    p = str(platform or "").upper()
    if p in rules["long_platforms"]:
        return "long"
    if p in rules.get("other_platforms", []) or not p:
        return "other"
    return "short"


def _v(r, name):
    v = r.get(name)
    return "" if v is None else str(v)


def classify_runs(rows, rules):
    """每行加 platform_type_derived / sample_key_derived / sample_total_Gb_derived / category_derived /
    category_rule_derived; 返回新行列表 (不改输入)."""
    meta = {x.upper() for x in rules["metagenomic_sources"]}
    out, keys = [], []
    for r in rows:
        r = dict(r)
        strat, src = _v(r, "LibraryStrategy").upper(), _v(r, "LibrarySource").upper()
        r["platform_type_derived"] = platform_type(_v(r, "Platform"), rules)
        r["sample_key_derived"] = _v(r, "BioSample").strip() or _v(r, "Run")
        # RunInfo 的 BioProject 可能为空 (Polynoidae 51 个 DRR), 用 SRA XML 的 STUDY 补 (同 kingfisher)
        r["BioProject_derived"] = _v(r, "BioProject").strip() or _v(r, "bioproject").strip()
        # LibrarySelection=PCR 不排除: Annelida 实测 WGS+PCR 多为 genome skimming / 基因组数据, 只在 QC 提示
        excluded = strat == "WGS" and src in meta
        gb = num(r.get("bases")) / 1e9
        out.append(r)
        keys.append(((r["sample_key_derived"], strat, r["platform_type_derived"]), gb, excluded))
    total = {}
    for k, gb, excluded in keys:
        total.setdefault(k, []).append(0 if excluded else gb)
    total = {k: math.fsum(v) for k, v in total.items()}
    lo, hi = rules["low_max_gb"], rules["shallow_max_gb"]
    for r, (k, gb, excluded) in zip(out, keys):
        g = round(gb if excluded else total[k], 3)
        r["sample_total_Gb_derived"] = g
        s, t, raw = k[1], k[2], _v(r, "LibraryStrategy")
        if excluded:
            c, w = "WGS_metagenomic", f"WGS & {_v(r, 'LibrarySource').upper()} -> WGS_metagenomic"
        elif s == "WGS" and t == "long":
            c, w = "long_read_WGS", f"WGS & {_v(r, 'Platform')} -> long_read_WGS"
        elif s == "WGS" and t == "short":
            if g < lo:
                c, rng = "low_data_WGS", f"< {lo:g}"
            elif g < hi:
                c, rng = "shallow_WGS", f"in [{lo:g},{hi:g})"
            else:
                c, rng = "deep_WGS", f">= {hi:g}"
            w = f"WGS & {_v(r, 'LibrarySelection').upper()} & short & {g:.2f} Gb {rng} -> {c}"
        elif s == "RNA-SEQ" and t == "long":
            c, w = "long_read_RNA", "RNA-Seq & long -> long_read_RNA"
        else:
            c, w = raw or "unknown", f"LibraryStrategy={raw}"
        r["category_derived"], r["category_rule_derived"] = c, w
    return out


def _join(xs):
    out = []
    for x in xs:
        x = "" if x is None else str(x)
        if x and x != "nan" and x not in out:
            out.append(x)
    return ";".join(out)


def _first(xs):
    for x in xs:
        if x is not None and str(x) not in ("", "nan"):
            return x
    return ""


def _groups(rows, key):
    g = {}
    for r in rows:
        g.setdefault(key(r), []).append(r)
    return g


SAMPLE_COLS = ["sample_key_derived", "category_derived", "TaxID", "ScientificName", "BioProject", "BioSample",
               "n_runs", "total_Gb", "Platform", "LibraryLayout", "runs"]


def summarize_samples(rows, cols):
    """一行一个 样本 × 数据类型 (挑选用). cols = 总表列名 (决定带哪些阶元/样本信息列). -> (cols, rows)"""
    extra = [c for c in LINEAGE + SAMPLE_INFO if c in cols]
    out = []
    for (key, cat), g in _groups(rows, lambda r: (r["sample_key_derived"], r["category_derived"])).items():
        r = {"sample_key_derived": key, "category_derived": cat, "n_runs": len(g),
             "total_Gb": round(math.fsum(num(x.get("bases")) / 1e9 for x in g), 3),
             "runs": _join(x.get("Run") for x in g), "Platform": _join(x.get("Platform") for x in g),
             "LibraryLayout": _join(x.get("LibraryLayout") for x in g)}
        for c in ["TaxID", "ScientificName", "BioSample"] + extra:
            r[c] = _first(x.get(c) for x in g)
        r["BioProject"] = _first(x.get("BioProject_derived") or x.get("BioProject") for x in g)
        out.append(r)
    return SAMPLE_COLS + extra, out


def ordered_categories(cats):
    cats = list(dict.fromkeys(cats))
    return [c for c in CATEGORY_ORDER if c in cats] + sorted(c for c in cats if c not in CATEGORY_ORDER)


def summarize_species(samples, tax_cols, taxonomy):
    """一行一个 TaxID: 阶元 + 各数据类型的样本数与 Gb. -> (cols, rows)"""
    if not samples:
        return ["TaxID", "ScientificName"] + LINEAGE, []
    lin = [c for c in LINEAGE if c in tax_cols] if taxonomy else []
    tax = {}
    for t in taxonomy:
        tax.setdefault(str(t.get("TaxID", "")), t)
    by = _groups(samples, lambda r: re.sub(r"\.0$", "", str(r.get("TaxID", ""))))
    cats = ordered_categories(r["category_derived"] for r in samples)
    out = []
    for tid, g in by.items():
        r = {"TaxID": tid, "ScientificName": g[0].get("ScientificName", "")}
        r.update({c: tax.get(tid, {}).get(c, "") for c in lin})
        for cat in cats:
            gc = [x for x in g if x["category_derived"] == cat]
            r[f"{cat}_samples"] = len(gc)
            r[f"{cat}_Gb"] = round(math.fsum(num(x.get("total_Gb")) for x in gc), 3)
        out.append(r)
    cols = ["TaxID", "ScientificName"] + lin + [f"{c}_{k}" for c in cats for k in ("samples", "Gb")]
    return cols, out


def _keep(rows, types, platforms, layout):
    want = {p.upper() for p in platforms or []}
    out = []
    for r in rows:
        if r.get("category_derived") not in types:
            continue
        if want and not want & set(_v(r, "Platform").upper().split(";")):
            continue
        if layout.upper() != "ALL" and layout.upper() not in _v(r, "LibraryLayout").upper():
            continue
        out.append(r)
    return out


def _top(rows, gb, top):
    n, out = {}, []
    for r in sorted(rows, key=lambda r: -gb(r)):          # sorted 稳定: 同量保持原顺序
        k = (r.get("TaxID"), r.get("BioProject_derived") or r.get("BioProject"), r.get("category_derived"))
        if n.get(k, 0) < top:
            n[k] = n.get(k, 0) + 1
            out.append(r)
    return out


def select_samples(samples, types, platforms, layout, top, per="sample", runs=None):
    """每 TaxID×BioProject×类型 取数据量最大的 top 个样本 (per="run" 时按 run, 需传 runs 表 = 旧脚本行为)."""
    if per == "run":
        r = [dict(x, total_Gb=round(num(x.get("bases")) / 1e9, 3), runs=x.get("Run", ""))
             for x in _keep(runs or [], types, platforms, layout)]
        return _top(r, lambda x: x["total_Gb"], top)
    return _top(_keep(samples, types, platforms, layout), lambda x: num(x.get("total_Gb")), top)
