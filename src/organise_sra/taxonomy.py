"""03 taxonomy: TaxID -> 门/纲/目/科/属/种 (NCBI Taxonomy, 不依赖 ete3 本地库).

用法: organise-sra taxonomy <Taxon>
输入: runinfo.tsv   输出: taxonomy.tsv (以 TaxID 为键), taxonomy_missing.txt
"""
import argparse
from pathlib import Path

from .paths import add_common, data_dir, identity, summary
from .eutils import Eutils, EutilsError
from .parse import RANKS, norm_taxid, parse_taxonomy_xml
from .tsv import read_tsv, write_tsv

BATCH = 200


def main(argv, eutils=None):
    p = argparse.ArgumentParser()
    add_common(p)
    a, _ = p.parse_known_args(argv)
    eu = eutils or Eutils(*identity(a))
    d = data_dir(a)
    ids = []
    for r in read_tsv(d / "runinfo.tsv")[1]:
        x = norm_taxid(r.get("TaxID"))
        if x and x not in ids:
            ids.append(x)
    rows = []
    try:
        for i in range(0, len(ids), BATCH):
            rows += parse_taxonomy_xml(eu.efetch_ids("taxonomy", ids[i:i + BATCH]))
    except EutilsError as e:
        print(f"[03_taxonomy] 失败: {e}")
        return 1
    cols = ["TaxID", "rank_derived", *RANKS]
    want, got = set(ids), {}
    for r in rows:
        if r["TaxID"] in want and r["TaxID"] not in got:
            got[r["TaxID"]] = r
    df = list(got.values())
    missing = [x for x in ids if x not in got]
    (d / "taxonomy_missing.txt").write_text("".join(x + "\n" for x in missing))
    write_tsv(d / "taxonomy.tsv", cols, df)
    lines = [f"{len(df)}/{len(ids)} 个 TaxID 取到阶元; 科 {len({r['family'] for r in df} - {''})} 个, "
             f"种级 {sum(r['rank_derived'] == 'species' for r in df)} 个", f"-> {d / 'taxonomy.tsv'}"]
    if missing:
        lines.append(f"警告: {len(missing)} 个 TaxID 查不到, 见 taxonomy_missing.txt")
    summary("03_taxonomy", lines)
    return 0

