"""genome: 整理全基因组组装可用的 SRA 数据, 按物种汇总并评级, 给出每个物种可直接下载的 run 清单.

用法:
  organise-sra genome Nereididae                  # 科; 也可 Annelida (门) 或 "Perinereis aibuhitensis" (种)
  organise-sra genome Nereididae --genome-size 800          # 所有物种按 800 Mb 估算覆盖度
  organise-sra genome Nereididae --genome-sizes sizes.tsv   # 两列: TaxID 或物种名, 基因组大小 Mb
  organise-sra genome 我的项目 --runs list.txt               # 任意 run 清单
  organise-sra genome Nereididae --refresh                  # 重新联网 (默认复用 <outdir>/table.tsv)
没有 table.tsv 时先自动跑 runinfo/annotate/taxonomy/table (同 organise-sra all 的前四步).

数据角色 (role, 每个 run 一个; 宏基因组、扩增子、RAD 等不算):
  HiFi      PacBio HiFi/CCS: Model=Revio, 或文库名/实验标题/设计含 HiFi/CCS
  PacBio    其余 PacBio 基因组数据 (CLR 或未注明)
  ONT       Oxford Nanopore 基因组数据
  WGS       二代基因组 (WGS/WGA, 非宏基因组; 标题含 Hi-C 的除外)
  HiC       LibraryStrategy=Hi-C / 染色质构象 (Tethered CCC 等), 或标题/文库名含 Hi-C/Omni-C (实测
            Annelida 有 8 个 WGS、3 个 OTHER 实为 Hi-C)
  RNA       二代转录组 (RNA-Seq/ssRNA-seq), 用于注释
  IsoSeq    长读长转录组 (PacBio/ONT 的 RNA-Seq / FL-cDNA)
评级 (tier): A 长读长+Hi-C 均达标 (可做染色体级) > B 长读长达标 > C 二代 WGS 达标 > D 数据不足.
达标: 有基因组大小时按覆盖度 (--long-x 20, --hic-x 10, --short-x 50), 否则按数据量 (--long-gb 15, --hic-gb 10,
--short-gb 30). 基因组大小来源: --genome-sizes > --genome-size > NCBI 已有组装的 total_length.
输出 (<outdir>/genome/): genome_species.tsv, genome_runs.tsv, genome_assemblies.tsv, <Taxon>_genome_<日期>.xlsx,
  run_lists/<物种>_<TaxID>/<role>.txt 与 all.txt, run_list_<role>.txt (全部物种)
"""
import argparse
import json
import re
import shlex
from pathlib import Path

from .eutils import Eutils, EutilsError
from .parse import RANKS, norm_taxid
from .paths import TODAY, add_common, data_dir, identity, report_dir, summary
from .tsv import num, read_tsv, write_tsv
from .xlsx import write_xlsx

ROLES = ["HiFi", "PacBio", "ONT", "WGS", "HiC", "RNA", "IsoSeq"]
LONG = ("HiFi", "PacBio", "ONT")
TEXT_COLS = ("LibraryName", "experiment_title", "design_description", "LibrarySelection")
HIC_RE = re.compile(r"\bhi-?c\b|omni-?c|chromatin conformation|proximity ligation|\b3c\b", re.I)
HIFI_RE = re.compile(r"hifi|\bccs\b|circular consensus", re.I)
SHORT_RNA = {"RNA-SEQ", "SSRNA-SEQ", "FL-CDNA", "EST"}
LEVEL = {"Complete Genome": 4, "Chromosome": 3, "Scaffold": 2, "Contig": 1}


def assign_role(r, meta_sources=("METAGENOMIC", "METATRANSCRIPTOMIC")):
    """-> (role 或 '', 依据). 只看单个 run 的 RunInfo + annotate 文本列."""
    strat = str(r.get("LibraryStrategy", "")).upper()
    src = str(r.get("LibrarySource", "")).upper()
    plat = str(r.get("Platform", "")).upper()
    model = str(r.get("Model", ""))
    text = " ".join(str(r.get(c, "")) for c in TEXT_COLS)
    if src in meta_sources:
        return "", f"LibrarySource={src}"
    long_plat = plat in ("PACBIO_SMRT", "OXFORD_NANOPORE")
    if strat == "HI-C" or "CONFORMATION" in strat:
        return "HiC", f"LibraryStrategy={r.get('LibraryStrategy')}"
    if strat in ("WGS", "WGA", "OTHER") and not long_plat and HIC_RE.search(text):
        return "HiC", f"{r.get('LibraryStrategy')} 但标题/文库名含 Hi-C"
    if strat in SHORT_RNA or src == "TRANSCRIPTOMIC":
        if long_plat:
            return "IsoSeq", f"{r.get('LibraryStrategy')} & {plat}"
        return ("RNA", f"LibraryStrategy={r.get('LibraryStrategy')}") if strat != "EST" else ("", "EST")
    if strat in ("WGS", "WGA") or (strat == "OTHER" and long_plat and src == "GENOMIC"):
        if plat == "PACBIO_SMRT":
            if "revio" in model.lower():
                return "HiFi", "PacBio Revio (仅产 HiFi)"
            if HIFI_RE.search(text):
                return "HiFi", "PacBio, 标题/文库名含 HiFi/CCS"
            return "PacBio", f"PacBio {model} (CLR 或未注明 HiFi)"
        if plat == "OXFORD_NANOPORE":
            return "ONT", f"Nanopore {model}"
        if plat in ("CAPILLARY", ""):
            return "", f"Platform={plat or '空'}"
        return "WGS", f"{r.get('LibraryStrategy')} & {plat}"
    return "", f"LibraryStrategy={r.get('LibraryStrategy')} (组装不用)"


def fetch_assemblies(eu, taxid=None, taxids=()):
    """NCBI Assembly: 有类群 taxid 时一次子树检索, 否则按 TaxID 分批 (每批 30 个, 请求 <1.2 KB). -> 行列表."""
    terms = [f"txid{taxid}[Organism:exp]"] if taxid else \
        [" OR ".join(f"txid{t}[Organism:exp]" for t in taxids[i:i + 30]) for i in range(0, len(taxids), 30)]

    def one(term):
        r = eu.esearch("assembly", term)
        out = []
        for start in range(0, r["count"], 200):
            body = eu.get("esummary.fcgi", {"db": "assembly", "WebEnv": r["webenv"], "query_key": r["query_key"],
                                            "retstart": start, "retmax": 200, "retmode": "json"}, expect="json")
            res = json.loads(body).get("result", {})
            for u in res.get("uids", []):
                x = res[u]
                m = re.search(r'<Stat category="total_length" sequence_tag="all">(\d+)', x.get("meta", ""))
                out.append({"assembly": x.get("assemblyaccession", ""), "name": x.get("assemblyname", ""),
                            "organism": re.sub(r"\s*\(.*\)$", "", x.get("organism", "")),
                            "TaxID": str(x.get("taxid", "")), "species_TaxID": str(x.get("speciestaxid", "")),
                            "level": x.get("assemblystatus", ""),
                            "total_Mb": round(int(m.group(1)) / 1e6, 1) if m else "",
                            "contig_N50_kb": round(num(x.get("contign50")) / 1e3, 1),
                            "scaffold_N50_kb": round(num(x.get("scaffoldn50")) / 1e3, 1),
                            "coverage": x.get("coverage", ""), "release": x.get("asmreleasedate_genbank", "")[:10],
                            "submitter": x.get("submitterorganization", ""),
                            "refseq_category": x.get("refseq_category", ""),
                            "BioProject": ";".join(b.get("bioprojectaccn", "") for b in x.get("gb_bioprojects", []))})
        return out

    rows = []
    for got in eu.map_parallel(one, terms):
        rows += got
    return list({r["assembly"]: r for r in rows}.values())


def best_assembly(asms):
    return max(asms, key=lambda x: (LEVEL.get(x["level"], 0), num(x["contig_N50_kb"]), x["release"]), default=None)


def read_sizes(path):
    """两列表 (TaxID 或物种名, Mb) -> {键: Mb}; 首行非数字视为表头."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        parts = re.split(r"\t|,", line.strip())
        if len(parts) >= 2 and num(parts[1]) > 0:
            out[parts[0].strip()] = num(parts[1])
    return out


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "unknown"


def tier_of(gb, size_mb, a):
    """-> (等级, 说明)."""
    long_gb = sum(gb[r] for r in LONG)
    if size_mb:
        cov = {k: v * 1000 / size_mb for k, v in (("long", long_gb), ("hic", gb["HiC"]), ("short", gb["WGS"]))}
        ok_long, ok_hic, ok_short = cov["long"] >= a.long_x, cov["hic"] >= a.hic_x, cov["short"] >= a.short_x
        why = f"长读长 {cov['long']:.0f}x, Hi-C {cov['hic']:.0f}x, 二代 {cov['short']:.0f}x (基因组 {size_mb:.0f} Mb)"
    else:
        ok_long, ok_hic, ok_short = long_gb >= a.long_gb, gb["HiC"] >= a.hic_gb, gb["WGS"] >= a.short_gb
        why = f"长读长 {long_gb:.1f} Gb, Hi-C {gb['HiC']:.1f} Gb, 二代 {gb['WGS']:.1f} Gb (无基因组大小)"
    if ok_long and ok_hic:
        return "A", why
    if ok_long:
        return "B", why
    if ok_short:
        return "C", why
    return "D", why


def main(argv, eutils=None):
    p = argparse.ArgumentParser(prog="organise-sra genome", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common(p)
    p.add_argument("--refresh", action="store_true", help="忽略已有 table.tsv, 重新跑 01–04")
    p.add_argument("--genome-size", type=float, help="所有物种的基因组大小 (Mb)")
    p.add_argument("--genome-sizes", help="TaxID/物种名 与 基因组大小 (Mb) 两列表")
    p.add_argument("--no-assembly", action="store_true", help="不查询 NCBI 已有组装")
    p.add_argument("--long-gb", type=float, default=15)
    p.add_argument("--hic-gb", type=float, default=10)
    p.add_argument("--short-gb", type=float, default=30)
    p.add_argument("--long-x", type=float, default=20)
    p.add_argument("--hic-x", type=float, default=10)
    p.add_argument("--short-x", type=float, default=50)
    a, _ = p.parse_known_args(argv)
    d = data_dir(a)
    if a.refresh or not (d / "table.tsv").exists():
        from . import annotate, runinfo, table, taxonomy
        for name, mod in (("runinfo", runinfo), ("annotate", annotate), ("taxonomy", taxonomy), ("table", table)):
            rc = mod.main(argv, eutils=eutils) if name != "table" else mod.main(argv)
            if rc and name in ("runinfo", "table"):
                print(f"[genome] {name} 失败 (exit {rc}); 修复后重跑 organise-sra genome {a.taxon}")
                return rc
    _, runs = read_tsv(d / "table.tsv")
    mf = d / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}

    g_rows = []
    for r in runs:
        role, why = assign_role(r)
        if not role:
            continue
        bases, spots = num(r.get("bases")), num(r.get("spots"))
        g_rows.append({"Run": r.get("Run", ""), "role": role, "TaxID": norm_taxid(r.get("TaxID")),
                       "ScientificName": r.get("ScientificName", ""),
                       **{k: r.get(k, "") for k in ("family", "genus", "species")},
                       "BioProject": r.get("BioProject_derived") or r.get("BioProject", ""),
                       "BioSample": r.get("BioSample", ""), "Platform": r.get("Platform", ""),
                       "Model": r.get("Model", ""), "LibraryStrategy": r.get("LibraryStrategy", ""),
                       "LibraryLayout": r.get("LibraryLayout", ""), "Gb": round(bases / 1e9, 3),
                       "avg_len": round(bases / spots) if spots else "", "role_rule": why,
                       "experiment_title": r.get("experiment_title", "")})
    run_cols = ["Run", "role", "TaxID", "ScientificName", "family", "genus", "species", "BioProject", "BioSample",
                "Platform", "Model", "LibraryStrategy", "LibraryLayout", "Gb", "avg_len", "role_rule",
                "experiment_title"]

    taxa = {}
    for r in g_rows:
        taxa.setdefault(r["TaxID"], []).append(r)
    asms = []
    if not a.no_assembly and taxa:
        eu = eutils or Eutils(*identity(a))
        try:
            asms = fetch_assemblies(eu, manifest.get("taxid") if manifest.get("mode") == "taxon" else None,
                                    sorted(taxa))
        except EutilsError as e:
            print(f"[genome] 查询 NCBI Assembly 失败, 跳过: {e}")
    by_tax = {}
    for x in asms:
        for k in {x["TaxID"], x["species_TaxID"]}:
            by_tax.setdefault(k, []).append(x)
    sizes = read_sizes(a.genome_sizes) if a.genome_sizes else {}
    tax_info = {norm_taxid(r.get("TaxID")): r for r in runs}

    sp_rows = []
    out = d / "genome"
    lists = out / "run_lists"
    lists.mkdir(parents=True, exist_ok=True)
    for tid, rs in taxa.items():
        info = tax_info.get(tid, {})
        name = rs[0]["ScientificName"]
        gb = {k: round(sum(num(x["Gb"]) for x in rs if x["role"] == k), 3) for k in ROLES}
        n = {k: len({x["BioSample"] or x["Run"] for x in rs if x["role"] == k}) for k in ROLES}
        asm = by_tax.get(tid, [])
        best = best_assembly(asm)
        size, src = None, ""
        for key in (tid, name):
            if key in sizes:
                size, src = sizes[key], "--genome-sizes"
                break
        if size is None and a.genome_size:
            size, src = a.genome_size, "--genome-size"
        if size is None and best and best["total_Mb"]:
            size, src = float(best["total_Mb"]), f"组装 {best['assembly']}"
        tier, why = tier_of(gb, size, a)
        bp_gb = {}
        for x in rs:
            if x["role"] in LONG + ("HiC",):
                bp_gb[x["BioProject"]] = bp_gb.get(x["BioProject"], 0) + num(x["Gb"])
        row = {"TaxID": tid, "ScientificName": name, "rank": info.get("rank_derived", ""),
               **{k: info.get(k, "") for k in RANKS}, "tier": tier, "tier_reason": why,
               "genome_size_Mb": round(size, 1) if size else "", "genome_size_source": src}
        for k in ROLES:
            row[f"{k}_Gb"], row[f"{k}_samples"] = gb[k], n[k]
            if size and k in LONG + ("WGS", "HiC"):
                row[f"{k}_x"] = round(gb[k] * 1000 / size, 1)
        row.update({"long_read_main_BioProject": max(bp_gb, key=lambda k: bp_gb[k]) if bp_gb else "",
                    "BioProjects": ";".join(sorted({x["BioProject"] for x in rs} - {""})),
                    "n_assemblies": len(asm), "best_assembly": best["assembly"] if best else "",
                    "best_assembly_level": best["level"] if best else "",
                    "best_contig_N50_kb": best["contig_N50_kb"] if best else ""})
        folder = lists / f"{safe(name)}_{tid}"
        folder.mkdir(exist_ok=True)
        for k in ROLES:
            ids = [x["Run"] for x in rs if x["role"] == k]
            f = folder / f"{k}.txt"
            if ids:
                f.write_text("".join(i + "\n" for i in ids))
            elif f.exists():
                f.unlink()
        (folder / "all.txt").write_text("".join(x["Run"] + "\n" for x in rs))
        row["run_list"] = str(folder / "all.txt")
        sp_rows.append(row)
    sp_rows.sort(key=lambda r: (r["tier"], -sum(num(r[f"{k}_Gb"]) for k in ROLES)))
    sp_cols = ["TaxID", "ScientificName", "rank", *RANKS, "tier", "tier_reason", "genome_size_Mb",
               "genome_size_source"]
    for k in ROLES:
        sp_cols += [f"{k}_Gb", f"{k}_samples"] + ([f"{k}_x"] if k in LONG + ("WGS", "HiC") else [])
    sp_cols += ["long_read_main_BioProject", "BioProjects", "n_assemblies", "best_assembly", "best_assembly_level",
                "best_contig_N50_kb", "run_list"]
    for k in ROLES:
        (out / f"run_list_{k}.txt").write_text("".join(x["Run"] + "\n" for x in g_rows if x["role"] == k))
    asm_cols = ["assembly", "name", "organism", "TaxID", "species_TaxID", "level", "total_Mb", "contig_N50_kb",
                "scaffold_N50_kb", "coverage", "release", "submitter", "refseq_category", "BioProject"]
    write_tsv(out / "genome_species.tsv", sp_cols, sp_rows)
    write_tsv(out / "genome_runs.tsv", run_cols, g_rows)
    write_tsv(out / "genome_assemblies.tsv", asm_cols, asms)
    rules = [["tier A", f"长读长 (HiFi+PacBio+ONT) 与 Hi-C 均达标; 覆盖度 ≥{a.long_x:g}x / ≥{a.hic_x:g}x, "
                        f"无基因组大小时 ≥{a.long_gb:g} Gb / ≥{a.hic_gb:g} Gb"],
             ["tier B", "长读长达标"], ["tier C", f"二代 WGS ≥{a.short_x:g}x (无大小时 ≥{a.short_gb:g} Gb)"],
             ["tier D", "数据不足"], ["基因组大小", "--genome-sizes > --genome-size > NCBI 已有组装 total_length"],
             ["role", "见 organise-sra genome -h"], ["下载", f"organise-sra download {a.taxon} --runs <run_list>"]]
    xl = write_xlsx(report_dir(a) / f"{a.taxon}_genome_{TODAY}.xlsx",
                    {"Species": (sp_cols, sp_rows), "Runs": (run_cols, g_rows), "Assemblies": (asm_cols, asms),
                     "Rules": (["item", "rule"], rules)})
    cnt = {t: sum(r["tier"] == t for r in sp_rows) for t in "ABCD"}
    lines = [f"{len(sp_rows)} 个 TaxID 有组装可用数据 ({len(g_rows)} run); A {cnt['A']}, B {cnt['B']}, C {cnt['C']}, "
             f"D {cnt['D']}; NCBI 已有组装 {len(asms)} 个",
             "按角色 run 数: " + ", ".join(f"{k} {sum(x['role'] == k for x in g_rows)}" for k in ROLES),
             f"-> {xl}", f"-> {lists}/<物种>_<TaxID>/all.txt  (organise-sra download {shlex.quote(a.taxon)} --runs ...)"]
    top = [r for r in sp_rows if r["tier"] in "AB"][:3]
    lines += [f"例: {r['ScientificName']} tier {r['tier']}: {r['tier_reason']}" for r in top[:1]]
    summary("genome", lines)
    return 0
