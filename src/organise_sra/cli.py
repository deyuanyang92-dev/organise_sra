"""organise-sra 命令行入口.

  organise-sra all Polynoidae                        # 类群名: 01–05 全流程
  organise-sra all 我的项目 --runs list.txt           # SRR/ERR/DRR 列表
  organise-sra all 我的项目 --runinfo SraRunInfo.csv  # 网页导出的 RunInfo
  organise-sra select Polynoidae --type RNA-Seq --top 1 --tag rna   # 只换筛选条件
  organise-sra download Polynoidae                   # 用 kingfisher 下载 run_list_default.txt, 可断点续跑
  organise-sra download 我的项目 --runs list.txt      # 任意 run 列表 (也可 -r SRR.. / -p PRJNA..)
  organise-sra <步骤> -h                              # 各步骤参数

步骤: runinfo(01) annotate(02) taxonomy(03) table(04) select(05); 每步只读上一步的 TSV、只写自己的,
失败的步骤修复后可单独重跑. all = 01–05 (不下载); download(06) 单独运行, 调用 kingfisher get.
"""
import importlib
import sys

from . import __version__

STEPS = [("runinfo", "runinfo"), ("annotate", "annotate"), ("taxonomy", "taxonomy"),
         ("table", "table"), ("select", "selection")]
EXTRA = [("download", "download")]                 # 不在 all 里: 下载要单独确认
ALIAS = {f"{i:02d}": name for i, (name, _) in enumerate(STEPS + EXTRA, start=1)}


def run_step(name, argv):
    mod = importlib.import_module(f".{dict(STEPS + EXTRA)[name]}", __package__)
    return mod.main(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"organise-sra {__version__}")
        return 0
    cmd, rest = ALIAS.get(argv[0], argv[0]), argv[1:]
    if cmd == "all":
        if not rest or rest[0].startswith("-"):
            raise SystemExit("用法: organise-sra all <类群名或项目名> [选项]")
        for name, _ in STEPS:
            rc = run_step(name, rest)
            if rc:
                print(f"[{name}] 失败 (exit {rc}); 修复后可单独重跑: organise-sra {name} {rest[0]}")
                return rc
        return 0
    if cmd not in dict(STEPS + EXTRA):
        raise SystemExit(f"未知命令 {argv[0]}; 可用: all, " + ", ".join(n for n, _ in STEPS + EXTRA))
    return run_step(cmd, rest)
