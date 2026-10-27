"""各步骤共用: 命令行公共参数、输出目录、日期、NCBI 身份、终端摘要."""
import datetime
from pathlib import Path

from .eutils import load_identity

TODAY = datetime.date.today().isoformat()


def add_common(p):
    p.add_argument("taxon", help="类群名 (如 Polynoidae); --runs/--runinfo 模式下作项目名")
    p.add_argument("-o", "--outdir", help="输出目录, 默认 ./<taxon>_sra")
    p.add_argument("--report-dir", help="Excel 输出目录, 默认同 --outdir")
    p.add_argument("--email", help="NCBI 联系邮箱 (也可用 NCBI_EMAIL)")
    p.add_argument("--api-key", help="NCBI API key, 有 key 快 3 倍 (也可用 NCBI_API_KEY)")
    p.add_argument("--ncbi-proxy", choices=["auto", "direct", "env"],
                   help="连 NCBI: auto 先试直连 (默认), direct 不走代理, env 用 http(s)_proxy (也可 ORGANISE_SRA_PROXY)")


def data_dir(a):
    d = Path(a.outdir or f"{a.taxon}_sra")
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_dir(a):
    if not a.report_dir:
        return data_dir(a)
    d = Path(a.report_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def identity(a):
    if getattr(a, "ncbi_proxy", None):
        import os
        os.environ["ORGANISE_SRA_PROXY"] = a.ncbi_proxy
    return load_identity(a.email, a.api_key)


def summary(step, lines):
    """每个步骤结束时打印的简短摘要 (≤5 行)."""
    print(f"[{step}] " + lines[0])
    for x in lines[1:5]:
        print("   " + x)
