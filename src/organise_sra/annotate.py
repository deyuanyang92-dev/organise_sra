"""02 annotate: 照 kingfisher annotate 的做法, 取 efetch db=sra 完整 XML 中的标题、摘要、提交单位与样本全部属性.

用法: organise-sra annotate <Taxon>
      organise-sra annotate <Taxon> --kingfisher kf.tsv   # 改用 kingfisher annotate -a 的结果, 不联网
输入: runinfo.tsv (01)   输出: annotate.tsv (以 Run 为键), annotate_missing.txt
XML 中缺失的 run 超过 1% 时退出码 1.
增量 (01 为增量时): 保留上次 annotate.tsv 中未变的 run, 只重取 runs_changed.txt 与上次缺的 run;
  要重取的超过一半时仍按全量取. --full 强制全量.
"""
import argparse
import csv
import io
import json
import time
from pathlib import Path

from .eutils import Eutils, EutilsError
from .parse import parse_sra_packages
from .paths import add_common, data_dir, identity, summary
from .tsv import columns, read_tsv, write_tsv

ACC_BATCH = 70     # 同 runinfo.ACC_BATCH
XML_PAGE = 100     # 2026-10-28 实测 Polynoidae 525 run, 3 路并行: 每页 500 用 7.8 s, 200 用 5.5 s, 100 用 4.3 s, 50 用 6.0 s
KF_DROP = {"organisation_contact_name", "organisation_contact_email"}      # 同内置: 不取联系人


def fix_mojibake(s):
    """kingfisher/pysradb 用 requests .text 把无 charset 的 UTF-8 按 Latin-1 解码 ('MartÃ­nez'); 能还原则还原."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def read_kingfisher(path):
    """kingfisher annotate 输出 (tsv 或 csv) -> 与内置 annotate 同形的行: run -> Run, 修复乱码, 去联系人."""
    text = Path(path).read_text(encoding="utf-8-sig")
    sep = "\t" if "\t" in text.partition("\n")[0] else ","
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=sep):
        r = {("Run" if k == "run" else k): fix_mojibake(v or "") for k, v in r.items() if k and k not in KF_DROP}
        if r.get("study_links") == "[]":
            r["study_links"] = ""
        rows.append(r)
    return rows


def main(argv, eutils=None):
    p = argparse.ArgumentParser()
    add_common(p)
    p.add_argument("--kingfisher", help="kingfisher annotate -a -f tsv 的输出; 给了就不联网")
    p.add_argument("--full", action="store_true", help="不增量, 全部重取")
    a, _ = p.parse_known_args(argv)
    eu = eutils or Eutils(*identity(a))
    d = data_dir(a)
    runs = [r["Run"] for r in read_tsv(d / "runinfo.tsv")[1] if r.get("Run")]
    mf = d / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    rows, kept = [], {}
    if manifest.get("incremental") and not a.full and not a.kingfisher and (d / "annotate.tsv").exists():
        changed = set((d / "runs_changed.txt").read_text().split()) if (d / "runs_changed.txt").exists() else set()
        want = set(runs)
        kept = {r["Run"]: r for r in read_tsv(d / "annotate.tsv")[1] if r["Run"] in want and r["Run"] not in changed}
        if len(runs) - len(kept) > len(runs) / 2:
            kept = {}
    todo = [x for x in runs if x not in kept]
    try:
        if a.kingfisher:
            rows = read_kingfisher(a.kingfisher)
        elif kept:
            for page in eu.map_parallel(lambda b: eu.efetch_ids("sra", b),
                                        [todo[i:i + ACC_BATCH] for i in range(0, len(todo), ACC_BATCH)]):
                rows += parse_sra_packages(page)
        elif manifest.get("mode") == "taxon" and manifest.get("term"):
            # 类群模式: 复用 01 的检索 (WebEnv 1 小时内有效, 否则重新 esearch), 每页 XML_PAGE, 页间并行
            h = manifest.get("history") or {}
            pages = None
            if h.get("webenv") and time.time() - h.get("time", 0) < 3600:
                try:
                    pages = eu.efetch_history("sra", h["webenv"], h["query_key"], h["count"], page=XML_PAGE)
                except EutilsError:
                    pages = None
            if pages is None:
                r = eu.esearch("sra", manifest["term"])
                pages = eu.efetch_history("sra", r["webenv"], r["query_key"], r["count"], page=XML_PAGE)
            for page in pages:
                rows += parse_sra_packages(page)
        else:
            # 按 run 号直接 efetch XML, 每批 ACC_BATCH 个, 批间并行
            for page in eu.map_parallel(lambda b: eu.efetch_ids("sra", b),
                                        [runs[i:i + ACC_BATCH] for i in range(0, len(runs), ACC_BATCH)]):
                rows += parse_sra_packages(page)
    except EutilsError as e:
        print(f"[02_annotate] 失败: {e}")
        return 1
    want, got = set(runs), {}
    for x in rows:                                  # 新取的优先于沿用的
        if x["Run"] in want and x["Run"] not in got:
            got[x["Run"]] = x
    again = [x for x in todo if x not in got]
    if again and not a.kingfisher and len(again) <= 2000:
        # 补取一次: 2026-10-28 Annelida 增量, 刚修改的 4 个 run 在批量 efetch 中缺失, 单独再取即有 (NCBI 更新中)
        try:
            for page in eu.map_parallel(lambda b: eu.efetch_ids("sra", b),
                                        [again[i:i + ACC_BATCH] for i in range(0, len(again), ACC_BATCH)]):
                for x in parse_sra_packages(page):
                    if x["Run"] in want and x["Run"] not in got:
                        got[x["Run"]] = x
        except EutilsError:
            pass
    got = {**kept, **got}
    df = [got[x] for x in runs if x in got]
    cols = columns(df) if df else ["Run"]
    missing = [x for x in runs if x not in got]
    (d / "annotate_missing.txt").write_text("".join(x + "\n" for x in missing))
    write_tsv(d / "annotate.tsv", cols, df)
    bad = len(runs) and len(missing) / len(runs) > 0.01
    src = f" (来自 kingfisher: {a.kingfisher})" if a.kingfisher else ""
    if kept:
        src += f"; 增量: 沿用 {len(kept)}, 重取 {len(todo)}"
    lines = [f"{len(df)}/{len(runs)} run 取到注释, {len(cols) - 1} 列 (含样本属性){src}", f"-> {d / 'annotate.tsv'}"]
    if missing:
        lines.append(f"{'错误' if bad else '警告'}: {len(missing)} 个 run 无 XML 记录, 见 annotate_missing.txt")
    summary("02_annotate", lines)
    return 1 if bad else 0

