"""03 taxonomy: TaxID -> 门/纲/目/科/属/种 (NCBI Taxonomy, 不依赖 ete3 本地库).

用法: organise-sra taxonomy <Taxon> [--no-cache]
输入: runinfo.tsv   输出: taxonomy.tsv (以 TaxID 为键), taxonomy_missing.txt
本地缓存: ~/.cache/organise_sra/taxonomy.tsv (或 $ORGANISE_SRA_CACHE/), 90 天内查过的 TaxID 不再联网;
未命中的每 100 个一批, 批间并行.
"""
import argparse
import datetime
import os
from pathlib import Path

from .eutils import Eutils, EutilsError
from .parse import RANKS, norm_taxid, parse_taxonomy_xml
from .paths import add_common, data_dir, identity, summary
from .tsv import read_tsv, write_tsv

BATCH = 100        # 100 个 TaxID ≈ 0.8 KB, 直连不超 eutils.DIRECT_MAX (200 个会被转去代理, 慢 3–4 s)
CACHE_DAYS = 90


def cache_path():
    return Path(os.environ.get("ORGANISE_SRA_CACHE") or Path.home() / ".cache" / "organise_sra") / "taxonomy.tsv"


def load_cache():
    """-> {TaxID: 行}; 超过 CACHE_DAYS 的条目丢弃 (NCBI 分类会更新)."""
    old = (datetime.date.today() - datetime.timedelta(days=CACHE_DAYS)).isoformat()
    return {r["TaxID"]: r for r in read_tsv(cache_path(), required=False)[1] if r.get("fetched", "") >= old}


def save_cache(cache):
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    write_tsv(tmp, ["TaxID", "rank_derived", *RANKS, "fetched"], list(cache.values()))
    tmp.replace(p)                                  # 原子替换, 并行运行时不留半个文件


def main(argv, eutils=None):
    p = argparse.ArgumentParser()
    add_common(p)
    p.add_argument("--no-cache", action="store_true", help="不用本地缓存, 全部重新查询")
    a, _ = p.parse_known_args(argv)
    eu = eutils or Eutils(*identity(a))
    d = data_dir(a)
    ids = []
    for r in read_tsv(d / "runinfo.tsv")[1]:
        x = norm_taxid(r.get("TaxID"))
        if x and x not in ids:
            ids.append(x)
    cache = {} if a.no_cache else load_cache()
    todo = [x for x in ids if x not in cache]
    rows = [cache[x] for x in ids if x in cache]
    try:
        for page in eu.map_parallel(lambda b: eu.efetch_ids("taxonomy", b),
                                    [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]):
            rows += parse_taxonomy_xml(page)
    except EutilsError as e:
        print(f"[03_taxonomy] 失败: {e}")
        return 1
    if todo:
        today = datetime.date.today().isoformat()
        full = load_cache()
        full.update({r["TaxID"]: dict(r, fetched=today) for r in rows if r["TaxID"] not in cache})
        try:
            save_cache(full)
        except OSError as e:
            print(f"   (缓存写入失败, 不影响结果: {e})")
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
             f"种级 {sum(r['rank_derived'] == 'species' for r in df)} 个; 缓存命中 {len(ids) - len(todo)}, "
             f"联网 {len(todo)}", f"-> {d / 'taxonomy.tsv'}"]
    if missing:
        lines.append(f"警告: {len(missing)} 个 TaxID 查不到, 见 taxonomy_missing.txt")
    summary("03_taxonomy", lines)
    return 0

