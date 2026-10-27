"""下载依赖的外部软件: 找 kingfisher 所在 conda 环境, 检查各下载方法要用的程序, 自动选可用方法.

  organise-sra tools                          # 检查 (默认 conda 环境 kingfisher)
  organise-sra tools --conda-env kf2          # 指定 conda 环境名
  organise-sra tools --kingfisher /path/to/env_or_bin/kingfisher
  organise-sra tools --no-probe               # 不试 ascp 下载
  organise-sra tools --reprobe                # 重新试 ascp (如刚打开代理 TUN 模式)
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

CONDA_BASES = ("~/miniforge3", "~/mambaforge", "~/miniconda3", "~/anaconda3", "/opt/conda")
ASCP_HOST = ("fasp.sra.ebi.ac.uk", 33001)          # ENA 官方 Aspera 端点
# 2026-10-28 实测: kingfisher 自带的 asperaweb_id_dsa.openssh 被 EBI 拒绝 (Permission denied);
# Aspera Transfer SDK (ascli config ascp install) 的 aspera_bypass_rsa.pem 可通过认证
ASCP_KEYS = ("~/.aspera/sdk/aspera_bypass_rsa.pem", "~/.aspera/connect/etc/aspera_bypass_rsa.pem",
             "~/.aspera/connect/etc/asperaweb_id_dsa.openssh")
# 方法 -> 需要的程序 (任一组满足即可)
NEEDS = {"ena-ascp": [["ascp"]], "ena-ftp": [["curl"], ["aria2c"]],
         "aws-http": [["aria2c", "fasterq-dump"], ["curl", "fasterq-dump"]],
         "prefetch": [["prefetch", "fasterq-dump"]], "aws-cp": [["aws", "fasterq-dump"]]}
AUTO_ORDER = ["ena-ascp", "aws-http", "prefetch", "ena-ftp"]     # 默认尝试顺序 (ascp 能用时最先)


def conda_bases():
    out = []
    for k in ("CONDA_EXE", "MAMBA_EXE"):
        if os.environ.get(k):
            out.append(Path(os.environ[k]).resolve().parent.parent)
    if os.environ.get("CONDA_PREFIX"):
        p = Path(os.environ["CONDA_PREFIX"])
        out.append(p.parent.parent if p.parent.name == "envs" else p)
    out += [Path(b).expanduser() for b in CONDA_BASES]
    return list(dict.fromkeys(out))


def env_prefix(name):
    """conda 环境名 -> 目录 (找不到返回 None). 名字含 / 时当作路径."""
    if "/" in name:
        p = Path(name).expanduser()
        return p if p.is_dir() else None
    for b in conda_bases():
        p = b / "envs" / name
        if (p / "bin").is_dir():
            return p
    return None


def find_kingfisher(path=None, conda_env=None):
    """-> kingfisher 可执行文件. 顺序: --kingfisher (文件或环境目录), --conda-env, $KINGFISHER, 当前 PATH,
    各 conda 安装下名为 kingfisher 的环境."""
    cands = []
    if path:
        p = Path(path).expanduser()
        cands.append(p / "bin" / "kingfisher" if p.is_dir() else p)
    if conda_env:
        pre = env_prefix(conda_env)
        if pre is None:
            raise SystemExit(f"找不到 conda 环境 {conda_env!r} (查找于 {', '.join(str(b) for b in conda_bases())})")
        cands.append(pre / "bin" / "kingfisher")
    if path or conda_env:                       # 明确指定时不再回退到别处
        c = cands[-1]
        if c.is_file() and os.access(c, os.X_OK):
            return str(c.absolute())
        raise SystemExit(f"{c} 不存在或不可执行")
    cands += [os.environ.get("KINGFISHER"), shutil.which("kingfisher")]
    cands += [b / "envs" / "kingfisher" / "bin" / "kingfisher" for b in conda_bases()]
    for c in cands:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(Path(c).absolute())
    return None


def child_env(kf):
    """相当于 conda activate: 环境 bin 放到 PATH 最前, 设 CONDA_PREFIX; ~/.aspera/sdk 加入 PATH (ascp);
    去掉 aria2c 不支持的 socks 形式 all_proxy (http_proxy 保留)."""
    env = dict(os.environ)
    bin_dir = Path(kf).parent
    extra = [str(bin_dir)]
    for d in ("~/.aspera/sdk", "~/.aspera/connect/bin"):
        d = Path(d).expanduser()
        if (d / "ascp").exists():
            extra.append(str(d))
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    env["CONDA_PREFIX"] = str(bin_dir.parent)
    for k in ("all_proxy", "ALL_PROXY"):
        if env.get(k, "").lower().startswith("socks"):
            env.pop(k)
    return env


def ascp_key():
    for k in ASCP_KEYS:
        p = Path(k).expanduser()
        if p.is_file():
            return str(p)
    return None


PROBE_FILE = ("/vol1/fastq/SRR121/066/SRR12118866/SRR12118866_1.fastq.gz", 4117481)   # ENA 上 4 MB 的小文件
PROBE_HOURS = 6


def _probe_cache():
    return Path(os.environ.get("ORGANISE_SRA_CACHE") or Path.home() / ".cache" / "organise_sra") / "ascp_probe.json"


def probe_ascp(ascp, key, tries=2, timeout=90, refresh=False):
    """真实下载一个 4 MB 文件, tries 次都成功才算可用; 结果缓存 PROBE_HOURS 小时.
    为什么不只测端口: 2026-10-28 本机 TCP 33001 每次都通、SSH 横幅也正常, 但 ascp 的 SSH 密钥交换
    约 3/4 次被中途丢包 (45 s 后 'Unable to connect via SSH'); 经代理时 SSH 稳定, 但 EBI 把 UDP 数据发往
    代理出口 IP 而失败. 只有直连稳定 (或代理软件开 TUN 模式) 时 ascp 才值得用, 否则每个 run 白等 ~50 s."""
    cache = _probe_cache()
    if not refresh and cache.exists():
        try:
            c = json.loads(cache.read_text())
            if time.time() - c["time"] < PROBE_HOURS * 3600 and c.get("key") == key:
                return c["ok"]
        except (ValueError, KeyError):
            pass
    ok = True
    for _ in range(tries):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                subprocess.run([ascp, "-QT", "-l", "300m", "-P", str(ASCP_HOST[1]), "-i", key,
                                f"era-fasp@{ASCP_HOST[0]}:{PROBE_FILE[0]}", tmp],
                               capture_output=True, timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                pass
            f = Path(tmp) / Path(PROBE_FILE[0]).name
            if not (f.exists() and f.stat().st_size == PROBE_FILE[1]):
                ok = False
                break
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"time": time.time(), "ok": ok, "key": key}))
    except OSError:
        pass
    return ok


def version(exe, env, args=("--version",)):
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
    return lines[-1].strip()[:60] if lines else ""


def check(kf, probe=True, refresh=False):
    """-> dict: kingfisher, env, programs{名: 路径}, ascp_key, ascp_reachable, methods_ok[]."""
    env = child_env(kf) if kf else dict(os.environ)
    progs = {p: shutil.which(p, path=env["PATH"]) for p in
             ("fasterq-dump", "prefetch", "aria2c", "curl", "pigz", "aws", "ascp")}
    key = ascp_key()
    reach = probe_ascp(progs["ascp"], key, refresh=refresh) if probe and progs["ascp"] and key else None
    ok = []
    for m in AUTO_ORDER + ["aws-cp"]:
        if any(all(progs.get(x) for x in grp) for grp in NEEDS[m]):
            if m == "ena-ascp" and not (key and reach):
                continue
            ok.append(m)
    return {"kingfisher": kf, "kingfisher_version": version(kf, env) if kf else "",
            "env": str(Path(kf).parent.parent) if kf else "", "programs": progs, "ascp_key": key,
            "ascp_reachable": reach, "methods_ok": ok, "_env": env}


def auto_methods(info):
    return [m for m in AUTO_ORDER if m in info["methods_ok"]]


def report(info):
    p = info["programs"]
    lines = [f"kingfisher: {info['kingfisher'] or '未找到'} {info['kingfisher_version']}",
             "程序: " + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in p.items())]
    if p.get("ascp"):
        r = info["ascp_reachable"]
        lines.append(f"ascp: key {'✓ ' + info['ascp_key'] if info['ascp_key'] else '✗ (运行 ascli config ascp install)'}; "
                     f"试下载 {'成功, 启用 ena-ascp' if r else '失败, 不用 ena-ascp (见 docs/kingfisher.md §6)' if r is False else '未测'}")
    lines.append("可用方法 (默认顺序): " + (" ".join(auto_methods(info)) or "无"))
    return lines


def main(argv):
    p = argparse.ArgumentParser(prog="organise-sra tools", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--conda-env", "--conda_env", help="kingfisher 所在 conda 环境名或目录 [自动: kingfisher]")
    p.add_argument("--kingfisher", help="kingfisher 可执行文件或其 conda 环境目录")
    p.add_argument("--no-probe", action="store_true", help="不试下载 (ascp 可用性未知时不启用 ena-ascp)")
    p.add_argument("--reprobe", action="store_true", help=f"忽略 {PROBE_HOURS} 小时内的 ascp 试下载结果, 重新测")
    p.add_argument("--json", action="store_true")
    a, _ = p.parse_known_args(argv)
    info = check(find_kingfisher(a.kingfisher, a.conda_env), probe=not a.no_probe, refresh=a.reprobe)
    if a.json:
        print(json.dumps({k: v for k, v in info.items() if not k.startswith("_")}, ensure_ascii=False, indent=1))
    else:
        print("[tools] " + "\n   ".join(report(info)))
    return 0 if info["kingfisher"] and info["methods_ok"] else 1
