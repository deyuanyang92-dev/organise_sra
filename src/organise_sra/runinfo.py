"""01 RunInfo: 得到 NCBI SraRunInfo 表 (原列名、原值), 三种输入任选其一.

用法:
  organise-sra runinfo Polynoidae                      # 类群名 -> txid<id>[Organism:exp], 分页取全
  organise-sra runinfo Polynoidae --query 'WGS[Strategy]'   # 追加 Entrez 子句
  organise-sra runinfo <项目名> --runs list.txt          # SRR/ERR/DRR 列表 (一行一个)
  organise-sra runinfo <项目名> --runs samples.xlsx --column Run   # 任意表格中的一列
  organise-sra runinfo <项目名> --runinfo SraRunInfo.csv  # 网页下载的 RunInfo
输出 (--outdir, 默认 ./<taxon>_sra): runinfo.tsv, manifest.json, sra_uids.txt, runs_new.txt, runs_changed.txt,
      update_history.tsv (每次新增/修改/删除的 run), (runs_not_found.txt)
核对: 类群模式下 唯一 Experiment 数 必须 = esearch Count (db=sra 按 experiment 计数).

增量更新 (默认): 同一输出目录已有上次结果时, 只取变化的部分 ——
  类群模式: 列出全部 UID (每 1 万个 ~1 s) 与上次比对得新增/删除, 再查 [MDAT] 修改日期 >= 上次日期-3 天的
  experiment; 只重取这些的 RunInfo, 02 annotate 也只重取这些 run 的 XML.
  --runs 模式: 已有的 run 不再取, 只取清单里新加的.
  距上次全量超过 --full-days (默认 30) 天自动全量; --full 强制全量 (= 0.4.0 及以前的做法).
"""
import argparse
import datetime
import json
import re
import time
from pathlib import Path

from .eutils import Eutils, EutilsError, resolve_taxid
from .parse import parse_runinfo
from .paths import TODAY, add_common, data_dir, identity, summary
from .tsv import columns, read_tsv, write_tsv

BATCH = 200
UID_BATCH = 100        # 增量: 按 UID efetch RunInfo, 每批 100 个 (~900 B)
OVERLAP_DAYS = 3       # 增量: 修改日期回看上次日期前 3 天 (MDAT 只精确到日, 且 NCBI 索引有延迟)
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


def previous(out, mode, key, full_days):
    """上次同模式、同检索式的成功结果 -> (manifest, 行); 不能增量时 None."""
    try:
        old = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        rows = read_tsv(out / "runinfo.tsv", required=False)[1]
    except (OSError, ValueError):
        return None
    if old.get("mode") != mode or old.get("key") != key or not old.get("ok") or not rows:
        return None
    try:
        age = (datetime.date.fromisoformat(TODAY) - datetime.date.fromisoformat(old["last_full"])).days
    except (KeyError, TypeError, ValueError):
        return None
    return (old, rows) if age <= full_days else None


def update_taxon(eu, term, old, old_rows, old_uids):
    """增量: -> (当前全部 UID, 合并后的行, 重取的 run, 删除的行, 说明) 或 None (UID 对不上, 需全量)."""
    cur = eu.esearch_ids("sra", term)
    since = (datetime.date.fromisoformat(old["date"]) - datetime.timedelta(days=OVERLAP_DAYS)).strftime("%Y/%m/%d")
    changed = set(eu.esearch_ids("sra", f"({term}) AND {since}:3000[MDAT]"))
    have, now = set(old_uids), set(cur)
    new = [u for u in cur if u not in have]
    mod = [u for u in cur if u in have and u in changed]
    gone = [u for u in old_uids if u not in now]
    fetched = []
    todo = new + mod
    for page in eu.map_parallel(lambda b: eu.efetch_ids("sra", b, rettype="runinfo", retmode="text"),
                                [todo[i:i + UID_BATCH] for i in range(0, len(todo), UID_BATCH)]):
        fetched += parse_runinfo(page)
    drop = {r.get("Experiment") for r in fetched}
    if gone:
        exp = eu.sra_experiments(gone)
        if len(exp) < len(gone):           # 撤回的记录 esummary 也查不到 -> 不知道删哪些行, 全量
            return None
        drop |= set(exp.values())
    keep = [r for r in old_rows if r.get("Experiment") not in drop]
    removed = [r for r in old_rows if r.get("Experiment") in drop]
    note = {"since": since, "new_experiments": len(new), "modified_experiments": len(mod),
            "removed_experiments": len(gone)}
    return cur, fetched + keep, [r["Run"] for r in fetched], removed, note


def log_changes(out, added, changed, removed, reason):
    """update_history.tsv 追加: 日期, 动作, Run, Experiment, 原因 (删除的行在此留底)."""
    f = out / "update_history.tsv"
    new = not f.exists()
    with open(f, "a", encoding="utf-8") as h:
        if new:
            h.write("date\taction\tRun\tExperiment\treason\n")
        for act, rows in (("added", added), ("updated", changed), ("removed", removed)):
            for r in rows:
                h.write(f"{TODAY}\t{act}\t{r.get('Run', '')}\t{r.get('Experiment', '')}\t"
                        f"{reason if act == 'removed' else ''}\n")


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
    p.add_argument("--full", action="store_true", help="不增量, 全部重取 (0.4.0 及以前的行为)")
    p.add_argument("--full-days", type=int, default=30, help="距上次全量超过这么多天自动全量 [30]")
    a, _ = p.parse_known_args(argv)
    eu = eutils or Eutils(*identity(a))
    out = data_dir(a)
    m = {"taxon": a.taxon, "date": TODAY, "taxid": "", "term": "", "esearch_count": None,
         "not_found": 0, "problems": []}
    hard = []                                       # 使结果不可信的问题 -> ok=false, 退出码 1
    prev, refetched, removed, uids, t0 = None, None, [], None, time.time()
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
            m["key"] = "runs"
            prev = None if a.full else previous(out, "runs", "runs", a.full_days)
            rows = [r for r in prev[1] if r["Run"] in set(want)] if prev else []
            have = {r["Run"] for r in rows}
            todo = [x for x in want if x not in have]
            # 按 run 号直接 efetch (每批 1 次请求; 旧做法 esearch accn OR + efetch 要 2 次且请求体大)
            batches = [todo[i:i + ACC_BATCH] for i in range(0, len(todo), ACC_BATCH)]
            for page in eu.map_parallel(lambda b: eu.efetch_ids("sra", b, rettype="runinfo", retmode="text"),
                                        batches):
                rows += parse_runinfo(page)
            total = None
            wanted = set(want)
            rows = [r for r in rows if r["Run"] in wanted]
            if prev:
                refetched = [r["Run"] for r in rows if r["Run"] not in have]
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
            m["term"] = m["key"] = term
            prev = None if a.full else previous(out, "taxon", term, a.full_days)
            old_uids = (out / "sra_uids.txt").read_text().split() if (out / "sra_uids.txt").exists() else []
            res = update_taxon(eu, term, prev[0], prev[1], old_uids) if prev and old_uids else None
            if res:
                uids, rows, refetched, removed, m["update"] = res
                m["esearch_count"] = len(uids)
            else:
                prev = None
        if m["mode"] == "taxon" and not prev:
            term = m["term"]
            uids = eu.esearch_ids("sra", term)
            r = eu.esearch("sra", term)
            n = m["esearch_count"] = r["count"]
            if n > a.max_experiments:
                print(f"[01_runinfo] {term} 有 {n} 条 experiment, 超过 --max-experiments {a.max_experiments}; "
                      f"确认要全部获取请加大该值")
                return 2
            n, rows = fetch(eu, term, r)
            m["history"] = {"webenv": r["webenv"], "query_key": r["query_key"], "count": n,
                            "time": time.time()}            # 02 annotate 复用, 省一次 esearch
            if len(uids) != n:                              # UID 列表与 history 不同步 (中途有新提交): 下次全量
                uids = []
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
        if prev:                                    # 增量合并后对不上 -> 自动全量重取一次
            print(f"[01_runinfo] 增量结果 Experiment {m['experiments']} != {m['esearch_count']}, 改为全量")
            return main(list(argv) + ["--full"], eutils=eu)
        hard.append(f"唯一 Experiment {m['experiments']} != esearch Count {m['esearch_count']}")
    m["problems"] = hard + m["problems"]
    m["ok"] = not hard
    m["incremental"] = bool(prev)
    m["last_full"] = prev[0]["last_full"] if prev else TODAY
    try:
        same = json.loads((out / "manifest.json").read_text(encoding="utf-8")).get("key") == m.get("key")
    except (OSError, ValueError):
        same = False
    old_rows = prev[1] if prev else read_tsv(out / "runinfo.tsv", required=False)[1] if same else []
    old_runs = {r["Run"] for r in old_rows}
    added = [r for r in df if r["Run"] not in old_runs]
    if not prev:                                    # 全量: 与上次文件比对, 删除的也记录
        now = {r["Run"] for r in df}
        removed = [r for r in old_rows if r["Run"] not in now] if m["mode"] == "taxon" else []
        refetched = None
    upd = [r for r in df if refetched and r["Run"] in set(refetched) and r["Run"] in old_runs]
    removed = [r for r in removed if r["Run"] not in {x["Run"] for x in df}]
    m["new_runs"], m["updated_runs"], m["removed_runs"] = len(added), len(upd), len(removed)
    if old_rows:
        log_changes(out, added, upd, removed, "NCBI 检索不再返回 (撤回/改类群)")
    (out / "runs_new.txt").write_text("".join(r["Run"] + "\n" for r in added))
    # 02 annotate 据此只重取这些 run 的 XML; 全量时为空文件且 incremental=false
    (out / "runs_changed.txt").write_text("".join(x + "\n" for x in (refetched or [])))
    if m["mode"] == "taxon":
        (out / "sra_uids.txt").write_text("".join(u + "\n" for u in (uids or [])))
    write_tsv(out / "runinfo.tsv", cols, df)
    (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    how = (f"增量 (上次 {prev[0]['date']}): 新增 {len(added)}、更新 {len(upd)}、删除 {len(removed)} run"
           if prev else "全量")
    lines = [f"{m['mode']}: {m['term'] or a.runs or a.runinfo}",
             f"{m['experiments']} experiment / {m['runs']} run; BioProject 为空 {m['empty_bioproject']}",
             f"{how}; {time.time() - t0:.1f} s" + ("; 新 run 见 runs_new.txt" if added and old_rows else ""),
             f"-> {out / 'runinfo.tsv'}"]
    lines += [f"警告: {x}" for x in m["problems"]]
    summary("01_runinfo", lines)
    return 0 if m["ok"] else 1

