"""01 RunInfo: 得到 NCBI SraRunInfo 表 (原列名、原值), 三种输入任选其一.

用法:
  organise-sra runinfo Polynoidae                      # 类群名 -> txid<id>[Organism:exp], 分页取全
  organise-sra runinfo Polynoidae --query 'WGS[Strategy]'   # 追加 Entrez 子句
  organise-sra runinfo <项目名> --runs list.txt          # SRR/ERR/DRR 列表 (一行一个)
  organise-sra runinfo <项目名> --runs samples.xlsx --column Run   # 任意表格中的一列
  organise-sra runinfo <项目名> --runinfo SraRunInfo.csv  # 网页下载的 RunInfo
输出 (--outdir, 默认 ./<taxon>_sra): runinfo.tsv, manifest.json, (runs_not_found.txt)
核对: 类群模式下 唯一 Experiment 数 必须 = esearch Count (db=sra 按 experiment 计数).
"""
import argparse
import json
import re
import time
from pathlib import Path

from .eutils import Eutils, EutilsError, resolve_taxid
from .parse import parse_runinfo
from .paths import TODAY, add_common, data_dir, identity, summary
from .tsv import columns, write_tsv

BATCH = 200
ACC_BATCH = 70         # --runs: efetch 直接给 run 号, 每批 70 个 (~1 KB, 直连不超 eutils.DIRECT_MAX), 批间并行
REQUIRED = ["Run", "LibraryStrategy", "bases", "TaxID"]
ACC = re.compile(r"^[SED]RR\d+$")


def fetch(eu, term, r=None):
    """-> (esearch Count, RunInfo 行). r: 已有的 esearch 结果 (复用其 WebEnv, 省一次请求)."""
    r = r or eu.esearch("sra", term)
    rows = []
    for page in eu.efetch_history("sra", r["webenv"], r["query_key"], r["count"], rettype="runinfo", retmode="text"):
        rows += parse_runinfo(page)
    return r["count"], rows


def read_runs(path, column=None, sheet=None):
    """-> (合法 run 号, 非法条目); 空白/逗号分隔, 转大写, 去重保序.
    给 column (列名或 1 起列号) 或文件是 .xlsx 时, 按表读取该列 (不给 column 则自动找 run 号最多的列)."""
    with open(path, "rb") as f:
        is_xlsx = f.read(2) == b"PK"
    if column or sheet or is_xlsx:
        from .intable import runs_from_table
        return runs_from_table(path, column, sheet)[:2]
    seen, bad = [], []
    for acc in re.split(r"[\s,;]+", Path(path).read_text(encoding="utf-8-sig")):
        acc = acc.strip().upper()
        if not acc or acc in seen or acc in bad:
            continue
        (seen if ACC.match(acc) else bad).append(acc)
    return seen, bad


def main(argv, eutils=None):
    p = argparse.ArgumentParser()
    add_common(p)
    p.add_argument("--taxid")
    p.add_argument("--query")
    p.add_argument("--runs", help="run 列表; 或任意 TSV/CSV/xlsx 表, 配合 --column")
    p.add_argument("--column", help="--runs 为表格时, run 号所在列 (列名或 1 起列号); xlsx 不给则自动识别")
    p.add_argument("--sheet", help="--runs 为 xlsx 时的工作表名 [第一个]")
    p.add_argument("--runinfo")
    p.add_argument("--max-experiments", type=int, default=200000)
    a, _ = p.parse_known_args(argv)
    eu = eutils or Eutils(*identity(a))
    out = data_dir(a)
    m = {"taxon": a.taxon, "date": TODAY, "taxid": "", "term": "", "esearch_count": None,
         "not_found": 0, "problems": []}
    hard = []                                       # 使结果不可信的问题 -> ok=false, 退出码 1
    (out / "runs_not_found.txt").write_text("")     # 清掉上次 --runs 留下的清单
    try:
        if a.runinfo:
            m["mode"] = "runinfo"
            rows = parse_runinfo(Path(a.runinfo).read_text(encoding="utf-8-sig", errors="replace"))
            lack = [c for c in REQUIRED if not rows or c not in rows[0]]
            if lack:
                print(f"[01_runinfo] {a.runinfo} 不是 NCBI SraRunInfo 表 (0 行或缺列 {', '.join(lack)}); "
                      "请用网页 Send to → File → RunInfo 导出的 csv")
                return 1
        elif a.runs:
            m["mode"] = "runs"
            want, bad = read_runs(a.runs, a.column, a.sheet)
            rows = []
            # 按 run 号直接 efetch (每批 1 次请求; 旧做法 esearch accn OR + efetch 要 2 次且请求体大)
            batches = [want[i:i + ACC_BATCH] for i in range(0, len(want), ACC_BATCH)]
            for page in eu.map_parallel(lambda b: eu.efetch_ids("sra", b, rettype="runinfo", retmode="text"),
                                        batches):
                rows += parse_runinfo(page)
            total = None
            wanted = set(want)
            rows = [r for r in rows if r["Run"] in wanted]
            got_runs = {r["Run"] for r in rows}
            missing = [x for x in want if x not in got_runs] + bad
            m["esearch_count"], m["not_found"] = total, len(missing)
            (out / "runs_not_found.txt").write_text("".join(x + "\n" for x in missing))
            if missing:
                m["problems"].append(f"{len(missing)} 个 run 查不到或不是 SRR/ERR/DRR 号, 见 runs_not_found.txt")
        else:
            m["mode"] = "taxon"
            m["taxid"] = a.taxid or resolve_taxid(eu, a.taxon)
            term = f"txid{m['taxid']}[Organism:exp]" + (f" AND ({a.query})" if a.query else "")
            m["term"] = term
            r = eu.esearch("sra", term)
            n = m["esearch_count"] = r["count"]
            if n > a.max_experiments:
                print(f"[01_runinfo] {term} 有 {n} 条 experiment, 超过 --max-experiments {a.max_experiments}; "
                      f"确认要全部获取请加大该值")
                return 2
            n, rows = fetch(eu, term, r)
            m["history"] = {"webenv": r["webenv"], "query_key": r["query_key"], "count": n,
                            "time": time.time()}            # 02 annotate 复用, 省一次 esearch
    except EutilsError as e:
        print(f"[01_runinfo] 失败: {e}")
        return 1

    seen, df = set(), []
    for r in rows:                                  # 同一 run 出现多次 (分页重叠) 只留第一条
        if r["Run"] not in seen:
            seen.add(r["Run"])
            df.append(r)
    cols = columns(df) if df else ["Run", "Experiment"]
    m["runs"] = len(df)
    m["experiments"] = len({r.get("Experiment") or "" for r in df} - {""}) if "Experiment" in cols else 0
    m["empty_bioproject"] = sum(not r.get("BioProject") for r in df) if "BioProject" in cols else 0
    if m["mode"] == "taxon" and m["experiments"] != m["esearch_count"]:
        hard.append(f"唯一 Experiment {m['experiments']} != esearch Count {m['esearch_count']}")
    m["problems"] = hard + m["problems"]
    m["ok"] = not hard
    write_tsv(out / "runinfo.tsv", cols, df)
    (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"{m['mode']}: {m['term'] or a.runs or a.runinfo}",
             f"{m['experiments']} experiment / {m['runs']} run; BioProject 为空 {m['empty_bioproject']}",
             f"-> {out / 'runinfo.tsv'}"]
    lines += [f"警告: {x}" for x in m["problems"]]
    summary("01_runinfo", lines)
    return 0 if m["ok"] else 1

