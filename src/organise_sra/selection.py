"""05 select: 按数据类型/平台/文库布局筛选, 输出可直接交给 kingfisher 的 run 清单 (本工具不下载).

用法:
  organise-sra select <Taxon>                                   # 默认 shallow_WGS,deep_WGS,RNA-Seq; PAIRED; 每组前 3
  organise-sra select <Taxon> --type RNA-Seq --top 1 --tag rna
  organise-sra select <Taxon> --platform ILLUMINA,DNBSEQ --layout ALL
  organise-sra select <Taxon> --per run                         # 旧脚本行为: 按 run 取前 N
分组 = TaxID × BioProject × 数据类型, 按数据量从大到小取 --top 个样本, 并带上样本的全部 run.
输出: selected_<tag>.tsv, run_list_<tag>.txt
"""
import argparse
from pathlib import Path

from .classify import select_samples
from .paths import add_common, data_dir, summary
from .tsv import num, read_tsv, write_tsv


def main(argv):
    p = argparse.ArgumentParser()
    add_common(p)
    p.add_argument("--type", default="shallow_WGS,deep_WGS,RNA-Seq")
    p.add_argument("--platform")
    p.add_argument("--layout", default="PAIRED", choices=["PAIRED", "SINGLE", "ALL"])
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--per", default="sample", choices=["sample", "run"])
    p.add_argument("--tag", default="default")
    a, _ = p.parse_known_args(argv)
    d = data_dir(a)
    types = [x.strip() for x in a.type.split(",") if x.strip()]
    plats = [x.strip() for x in a.platform.split(",")] if a.platform else None
    if a.per == "run":
        cols, table = read_tsv(d / "table.tsv")
        cols = cols + [c for c in ("total_Gb", "runs") if c not in cols]
        sel = select_samples(None, types, plats, a.layout, a.top, per="run", runs=table)
    else:
        cols, samples = read_tsv(d / "samples.tsv")
        sel = select_samples(samples, types, plats, a.layout, a.top)
    runs, seen = [], set()
    for x in sel:
        for r in str(x.get("runs", "")).split(";"):
            if r and r not in seen:
                seen.add(r)
                runs.append(r)
    write_tsv(d / f"selected_{a.tag}.tsv", cols, sel)
    out = d / f"run_list_{a.tag}.txt"
    out.write_text("".join(r + "\n" for r in runs))
    gb = sum(num(x.get("total_Gb")) for x in sel)
    summary("05_select", [f"{a.type} | {a.layout} | 每组前 {a.top} 个{'样本' if a.per == 'sample' else ' run'}",
                          f"{len(sel)} 个{'样本' if a.per == 'sample' else ' run'}, {len(runs)} run, 约 {gb:.1f} Gb",
                          f"-> {out}",
                          f"下载: organise-sra download {a.taxon}" + (f" --tag {a.tag}" if a.tag != "default" else "")])
    return 0

