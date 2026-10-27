"""02 annotate: 照 kingfisher annotate 的做法, 取 efetch db=sra 完整 XML 中的标题、摘要、提交单位与样本全部属性.

用法: organise-sra annotate <Taxon>
      organise-sra annotate <Taxon> --kingfisher kf.tsv   # 改用 kingfisher annotate -a 的结果, 不联网
输入: runinfo.tsv (01)   输出: annotate.tsv (以 Run 为键), annotate_missing.txt
XML 中缺失的 run 超过 1% 时退出码 1.
"""
import argparse
import csv
import io
import json
from pathlib import Path

from .paths import add_common, data_dir, identity, summary
from .eutils import Eutils, EutilsError
from .parse import parse_sra_packages
from .tsv import columns, read_tsv, write_tsv

BATCH = 200
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
    a, _ = p.parse_known_args(argv)
    eu = eutils or Eutils(*identity(a))
    d = data_dir(a)
    runs = [r["Run"] for r in read_tsv(d / "runinfo.tsv")[1] if r.get("Run")]
    mf = d / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    rows = []
    try:
        if a.kingfisher:
            rows = read_kingfisher(a.kingfisher)
        elif manifest.get("mode") == "taxon" and manifest.get("term"):
            # 类群模式: 复用 01 的检索式, 每页 500 (比按 accession 分批快约一倍)
            r = eu.esearch("sra", manifest["term"])
            for page in eu.efetch_history("sra", r["webenv"], r["query_key"], r["count"], page=500):
                rows += parse_sra_packages(page)
        else:
            for i in range(0, len(runs), BATCH):
                r = eu.esearch("sra", " OR ".join(f"{x}[accn]" for x in runs[i:i + BATCH]))
                for page in eu.efetch_history("sra", r["webenv"], r["query_key"], r["count"], page=BATCH):
                    rows += parse_sra_packages(page)
    except EutilsError as e:
        print(f"[02_annotate] 失败: {e}")
        return 1
    want, got = set(runs), {}
    for x in rows:
        if x["Run"] in want and x["Run"] not in got:
            got[x["Run"]] = x
    df = list(got.values())
    cols = columns(df) if df else ["Run"]
    missing = [x for x in runs if x not in got]
    (d / "annotate_missing.txt").write_text("".join(x + "\n" for x in missing))
    write_tsv(d / "annotate.tsv", cols, df)
    bad = len(runs) and len(missing) / len(runs) > 0.01
    src = f" (来自 kingfisher: {a.kingfisher})" if a.kingfisher else ""
    lines = [f"{len(df)}/{len(runs)} run 取到注释, {len(cols) - 1} 列 (含样本属性){src}", f"-> {d / 'annotate.tsv'}"]
    if missing:
        lines.append(f"{'错误' if bad else '警告'}: {len(missing)} 个 run 无 XML 记录, 见 annotate_missing.txt")
    summary("02_annotate", lines)
    return 1 if bad else 0

