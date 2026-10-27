"""06 download: 用 kingfisher 批量下载 fastq, 可断点续跑 (本步骤只调度 kingfisher, 不自己下载).

用法:
  organise-sra download Polynoidae                         # 下载 ./Polynoidae_sra/run_list_default.txt
  organise-sra download Polynoidae --tag rna               # 下载 run_list_rna.txt
  organise-sra download 我的项目 --runs sra_list.txt        # SRR/ERR/DRR 列表文件 (一行一个)
  organise-sra download 我的项目 -r SRR12118866 ERR1739691
  organise-sra download 我的项目 -p PRJNA621514            # BioProject/SRP -> 先查出全部 run, 再逐个下载
  organise-sra download 我的项目 --runs list.txt --check-only   # 只检查本地已下载情况, 不下载
  organise-sra download 我的项目 --runs list.txt --dry-run      # 只打印 kingfisher 命令

断点续跑: 每个 run 下载成功后写 <fastq-dir>/.done/<run>.json; 重跑时有标记的 run 直接跳过.
已有文件但无标记 (旧脚本下的、中途被杀的): 先做完整性检查 (gzip -t, 双端须 _1 _2 成对),
通过则补标记跳过, 不通过则用 kingfisher --force 重下 (删除前写入日志).
输出 (--outdir, 默认 ./<taxon>_sra): fastq/ (--fastq-dir), fastq/logs/<run>.log,
  download_status.tsv, runs_failed.txt (可直接作 --runs 重跑), download_history.tsv (每次尝试追加一行)
"""
import argparse
import datetime
import gzip
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .paths import add_common, data_dir, identity, summary
from .runinfo import ACC, read_runs
from .tsv import write_tsv

# 2026-10-28 本机实测 (SRR12118866, 2×~4.5 MB, 经 http 代理): aws-http 18 s, prefetch 27 s, ena-ftp 102 s,
# ena-ascp 失败 (33001 端口 SSH 连不上). 顺序 = 依次尝试直到成功, 详见 docs/kingfisher.md
DEFAULT_METHODS = ["aws-http", "prefetch", "ena-ftp"]
OLD_METHODS = ["aws-http", "aws-cp", "gcp-cp", "ena-ascp", "ena-ftp", "prefetch"]   # 旧脚本默认
METHODS = ["aws-http", "prefetch", "aws-cp", "gcp-cp", "ena-ascp", "ena-ftp", "ngdc-ascp", "ngdc-http"]
FORMATS = ["sra", "fastq", "fastq.gz", "fasta", "fasta.gz"]
STATUS_COLS = ["Run", "status", "method", "seconds", "size_Gb", "files", "note"]


def find_kingfisher(path=None):
    """-> kingfisher 可执行文件路径. 顺序: --kingfisher, $KINGFISHER, PATH, conda 环境 kingfisher."""
    cands = [path, os.environ.get("KINGFISHER"), shutil.which("kingfisher")]
    for base in ("~/miniforge3", "~/mambaforge", "~/miniconda3", "~/anaconda3", os.environ.get("CONDA_PREFIX", "")):
        if base:
            cands.append(str(Path(base).expanduser() / "envs" / "kingfisher" / "bin" / "kingfisher"))
    for c in cands:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return os.path.abspath(c)
    return None


def child_env(kf):
    """kingfisher 所在 bin 放到 PATH 最前 (fasterq-dump/aria2c/pigz 同环境); 去掉 aria2c 不支持的 socks all_proxy."""
    env = dict(os.environ)
    env["PATH"] = str(Path(kf).parent) + os.pathsep + env.get("PATH", "")
    for k in ("all_proxy", "ALL_PROXY"):
        if env.get(k, "").lower().startswith("socks"):
            env.pop(k)
    return env


def run_files(d, run, formats):
    """d 中属于该 run 的输出文件 (SRR1 不会匹配 SRR10...)."""
    sfx = tuple("." + f for f in formats)
    return sorted(p for p in d.glob(f"{run}[._]*") if p.is_file() and p.name.endswith(sfx)
                  and re.match(rf"^{run}(_[12]|_f1|_r2)?\.", p.name))


def gz_ok(p):
    """gzip 完整性: 优先 pigz/gzip -t, 否则 Python 流式解压."""
    for tool in ("pigz", "gzip"):
        if shutil.which(tool):
            return subprocess.run([tool, "-t", str(p)], capture_output=True).returncode == 0
    try:
        with gzip.open(p, "rb") as f:
            while f.read(1 << 24):
                pass
        return True
    except (OSError, EOFError):
        return False


def check_files(files):
    """-> (完整?, 原因). 规则: 非空; .gz 能完整解压; 有 _1 必须有 _2 (反之亦然)."""
    if not files:
        return False, "无文件"
    names = {p.name for p in files}
    for p in files:
        m = re.search(r"_([12])\.(fast[aq](\.gz)?)$", p.name)
        if m and p.name.replace(f"_{m.group(1)}.", f"_{3 - int(m.group(1))}.") not in names:
            return False, f"{p.name} 缺配对文件"
        if p.stat().st_size == 0:
            return False, f"{p.name} 为空"
        if p.name.endswith(".gz") and not gz_ok(p):
            return False, f"{p.name} gzip 不完整"
    return True, ""


def read_exclude(path):
    """旧脚本 --compared-files: 每行第一列为已下载的 run."""
    if not path or not Path(path).exists():
        return set()
    return {ln.split()[0] for ln in Path(path).read_text(encoding="utf-8-sig").splitlines() if ln.strip()}


def bioproject_runs(eu, accs):
    from .runinfo import fetch
    out = []
    for acc in accs:
        _, rows = fetch(eu, acc)
        out += [r["Run"] for r in rows if r.get("Run") and (acc in (r.get("BioProject"), r.get("SRAStudy")))]
    return list(dict.fromkeys(out))


def build_cmd(kf, run, a, fq, force):
    cmd = [kf, "get", "-r", run, "-m", *a.methods, "-f", *a.formats, "--output-directory", str(fq),
           "--download-threads", str(min(a.download_threads, 16)), "-t", str(a.extraction_threads),
           "--hide-download-progress"]
    if not a.no_md5:
        cmd.append("--check-md5sums")
    if force:
        cmd.append("--force")
    return cmd + a.kingfisher_args.split()


def gb(files):
    return round(sum(p.stat().st_size for p in files) / 1e9, 3)


def download_one(run, a, kf, env, fq, history):
    done = fq / ".done" / f"{run}.json"
    log = fq / "logs" / f"{run}.log"
    row = {"Run": run, "status": "", "method": "", "seconds": "", "size_Gb": "", "files": "", "note": ""}
    if done.exists() and not a.force:
        files = run_files(fq, run, a.formats)
        if files:
            row.update(status="done", files=";".join(p.name for p in files), size_Gb=gb(files), note="已有完成标记")
            return row
    files = run_files(fq, run, a.formats)
    force = a.force
    if files and not a.force:
        ok, why = check_files(files) if not a.no_verify else (True, "")
        if ok:
            write_done(done, run, "existing", files, 0)
            row.update(status="done", files=";".join(p.name for p in files), size_Gb=gb(files),
                       note="已有文件, 完整性检查通过")
            return row
        if a.check_only:
            row.update(status="incomplete", files=";".join(p.name for p in files), note=why)
            return row
        force = True
        history(run, "remove_incomplete", "", 0, f"{why}; kingfisher --force 删除重下: "
                + ";".join(p.name for p in files))
    if a.check_only:
        row.update(status="missing")
        return row
    free = shutil.disk_usage(fq).free / 1e9
    if free < a.min_free_gb:
        row.update(status="skipped", note=f"磁盘剩余 {free:.0f} GB < --min-free-gb {a.min_free_gb}")
        return row
    cmd = build_cmd(kf, run, a, fq, force)
    if a.dry_run:
        row.update(status="dry_run", note=" ".join(cmd))
        return row
    text, sec, why = "", 0, ""
    for attempt in range(1, a.retries + 2):
        t0 = time.time()
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n# {datetime.datetime.now():%F %T} attempt {attempt}: {' '.join(cmd)}\n")
            fh.flush()
            rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env).returncode
        sec = round(time.time() - t0)
        text = log.read_text(encoding="utf-8", errors="replace")
        used = re.findall(r"Method (\S+) worked", text)
        method = used[-1] if used else ""
        files = run_files(fq, run, a.formats)
        ok, why = check_files(files) if rc == 0 else (False, f"kingfisher 退出码 {rc}")
        if ok:
            write_done(done, run, method, files, sec)
            row.update(status="done", method=method, seconds=sec, size_Gb=gb(files),
                       files=";".join(p.name for p in files), note=f"第 {attempt} 次成功" if attempt > 1 else "")
            history(run, "done", method, sec, row["files"])
            return row
        history(run, "failed", method, sec, why)
        cmd = build_cmd(kf, run, a, fq, True)          # 重试时清掉残留文件
    errs = [x for x in text.splitlines() if re.search(r"ERROR|Exception|failed", x)]
    row.update(status="failed", seconds=sec, note=(errs[-1] if errs else why)[:300] + f" (见 logs/{run}.log)")
    return row


def write_done(path, run, method, files, sec):
    path.write_text(json.dumps({"run": run, "method": method, "seconds": sec, "date": datetime.date.today().isoformat(),
                                "files": {p.name: p.stat().st_size for p in files}}, ensure_ascii=False))


def main(argv):
    p = argparse.ArgumentParser(prog="organise-sra download", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common(p)
    src = p.add_argument_group("输入 (任选其一; 都不给则用 <outdir>/run_list_<tag>.txt)")
    src.add_argument("--tag", default="default", help="select 步骤的 --tag [default]")
    src.add_argument("--runs", help="run 列表文件, 一行一个 (同旧脚本 --SRA_lists / --run-identifiers-list)")
    src.add_argument("-r", "--run-identifiers", nargs="+", help="直接给 run 号")
    src.add_argument("-p", "--bioprojects", nargs="+", help="BioProject / SRP 号, 先查出全部 run")
    src.add_argument("--exclude", help="已下载 run 列表 (每行第一列), 跳过 (同旧脚本 --compared-files)")
    kfo = p.add_argument_group("kingfisher 选项")
    kfo.add_argument("-m", "--methods", nargs="+", default=DEFAULT_METHODS, choices=METHODS,
                     help=f"依次尝试的下载方法 [{' '.join(DEFAULT_METHODS)}]; 旧脚本默认: {' '.join(OLD_METHODS)}")
    kfo.add_argument("-f", "--formats", nargs="+", default=["fastq.gz"], choices=FORMATS,
                     help="可接受的输出格式, 给多个时尽量少转换 [fastq.gz]")
    kfo.add_argument("-j", "--jobs", type=int, default=4, help="同时下载的 run 数 [4]")
    kfo.add_argument("--download-threads", type=int, default=8, help="每个 run 的下载连接数, 上限 16 [8]")
    kfo.add_argument("-t", "--extraction-threads", type=int, default=8, help="fasterq-dump/pigz 线程 [8]")
    kfo.add_argument("--no-md5", action="store_true", help="不校验 md5 (默认校验; 同旧脚本 --disable-md5)")
    kfo.add_argument("--kingfisher", help="kingfisher 路径 [自动: PATH 或 ~/miniforge3/envs/kingfisher]")
    kfo.add_argument("--kingfisher-args", default="", help="原样追加给 kingfisher get 的参数, 如 '--allow-paid'")
    run = p.add_argument_group("运行")
    run.add_argument("--fastq-dir", help="下载目录 [<outdir>/fastq]")
    run.add_argument("--retries", type=int, default=1, help="失败后重试次数 [1]")
    run.add_argument("--min-free-gb", type=float, default=50, help="磁盘剩余低于此值不再开始新下载 [50]")
    run.add_argument("--force", action="store_true", help="忽略完成标记与已有文件, 全部重下")
    run.add_argument("--no-verify", action="store_true", help="已有文件不做 gzip 完整性检查, 直接视为完成 (旧脚本行为)")
    run.add_argument("--check-only", action="store_true", help="只检查已下载情况, 不下载 (同旧脚本 --only-check)")
    run.add_argument("--dry-run", action="store_true", help="只打印 kingfisher 命令")
    a, _ = p.parse_known_args(argv)
    d = data_dir(a)

    if a.run_identifiers:
        runs, bad = [x.upper() for x in a.run_identifiers if ACC.match(x.upper())], \
            [x for x in a.run_identifiers if not ACC.match(x.upper())]
        src_desc = "命令行 -r"
    elif a.bioprojects:
        from .eutils import Eutils, EutilsError
        try:
            runs, bad = bioproject_runs(Eutils(*identity(a)), a.bioprojects), []
        except EutilsError as e:
            print(f"[06_download] 查询 BioProject 失败: {e}")
            return 1
        src_desc = "BioProject " + " ".join(a.bioprojects)
    else:
        path = Path(a.runs) if a.runs else d / f"run_list_{a.tag}.txt"
        if not path.exists():
            print(f"[06_download] 找不到 run 清单 {path}: 先运行 organise-sra select, 或用 --runs / -r / -p")
            return 1
        runs, bad = read_runs(path)
        src_desc = str(path)
    runs = list(dict.fromkeys(runs))
    excl = read_exclude(a.exclude)
    todo = [r for r in runs if r not in excl]
    if bad:
        print(f"[06_download] 忽略 {len(bad)} 个非 SRR/ERR/DRR 条目: {' '.join(bad[:5])}")

    kf = None
    if not a.check_only:
        kf = find_kingfisher(a.kingfisher)
        if not kf:
            print("[06_download] 找不到 kingfisher. 安装: mamba create -n kingfisher -c conda-forge -c bioconda "
                  "kingfisher, 或用 --kingfisher 指定路径 (见 docs/kingfisher.md)")
            return 1
    fq = Path(a.fastq_dir) if a.fastq_dir else d / "fastq"
    for sub in ("", ".done", "logs"):
        (fq / sub).mkdir(parents=True, exist_ok=True)
    env = child_env(kf) if kf else None
    hist = d / "download_history.tsv"
    if not hist.exists():
        hist.write_text("time\tRun\tevent\tmethod\tseconds\tnote\n")

    def history(run, event, method, sec, note):
        with open(hist, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%F %T}\t{run}\t{event}\t{method}\t{sec}\t{note}\n")

    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        rows = list(ex.map(lambda r: download_one(r, a, kf, env, fq, history), todo))
    rows += [{"Run": r, "status": "excluded", "note": f"在 {a.exclude} 中"} for r in runs if r in excl]

    write_tsv(d / "download_status.tsv", STATUS_COLS, rows)
    bad_runs = [r["Run"] for r in rows if r["status"] in ("failed", "missing", "incomplete", "skipped")]
    (d / "runs_failed.txt").write_text("".join(x + "\n" for x in bad_runs))
    n = {s: sum(r["status"] == s for r in rows) for s in ("done", "failed", "missing", "incomplete", "skipped",
                                                          "dry_run", "excluded")}
    lines = [f"{src_desc}: {len(runs)} run -> {fq}",
             ", ".join(f"{k} {v}" for k, v in n.items() if v),
             f"状态 -> {d / 'download_status.tsv'}"]
    if a.dry_run:
        lines += [r["note"] for r in rows if r["status"] == "dry_run"][:2]
    if bad_runs:
        lines.append(f"未完成 {len(bad_runs)} 个 -> {d / 'runs_failed.txt'}; 重跑同一命令即可续传")
    summary("06_download", lines)
    return 1 if n["failed"] or n["skipped"] else 0
