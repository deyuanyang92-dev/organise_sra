import gzip
import json
import stat

import pytest
from conftest import TAXON

from organise_sra import download, tools

# 假 kingfisher: 按 -r 写成对 fastq.gz; run 名含 FAIL 时退出 1; 记录每次调用的参数
FAKE = """#!/usr/bin/env python3
import gzip, os, sys
a = sys.argv[1:]
run = a[a.index("-r") + 1]
out = a[a.index("--output-directory") + 1]
open(os.path.join(out, "..", "calls.txt"), "a").write(" ".join(a) + "\\n")
if "FAIL" in os.environ.get("FAKE_FAIL", "") and run in os.environ["FAKE_FAIL"]:
    print("ERROR: No more specified download methods, cannot continue"); sys.exit(1)
if "--force" in a:                                  # 同 kingfisher: 先删已有输出
    for f in os.listdir(out):
        if f.split("_")[0].split(".")[0] == run:
            os.remove(os.path.join(out, f))
for i in (1, 2):
    with gzip.open(os.path.join(out, f"{run}_{i}.fastq.gz"), "wt") as f:
        f.write("@r\\nACGT\\n+\\nIIII\\n")
print("INFO: Method aws-http worked.")
"""


def fake_kf(tmp_path):
    kf = tmp_path / "bin" / "kingfisher"
    kf.parent.mkdir()
    kf.write_text(FAKE)
    kf.chmod(kf.stat().st_mode | stat.S_IEXEC)
    return str(kf)


def gz(path, text="@r\nACGT\n+\nIIII\n"):
    with gzip.open(path, "wt") as f:
        f.write(text)


def status(d):
    lines = (d / "download_status.tsv").read_text().splitlines()
    cols = lines[0].split("\t")
    return {x.split("\t")[0]: dict(zip(cols, x.split("\t"))) for x in lines[1:]}


def test_download_from_run_list_and_resume(project, tmp_path):
    kf = fake_kf(tmp_path)
    project.mkdir(parents=True)
    (project / "run_list_default.txt").write_text("SRR1\nSRR10\n")
    assert download.main([TAXON, "--kingfisher", kf, "--no-check"]) == 0
    fq = project / "fastq"
    assert sorted(p.name for p in fq.glob("*.gz")) == ["SRR10_1.fastq.gz", "SRR10_2.fastq.gz",
                                                      "SRR1_1.fastq.gz", "SRR1_2.fastq.gz"]
    st = status(project)
    assert st["SRR1"]["status"] == "done" and st["SRR1"]["method"] == "aws-http"
    assert json.loads((fq / ".done" / "SRR1.json").read_text())["files"].keys() == {"SRR1_1.fastq.gz",
                                                                                     "SRR1_2.fastq.gz"}
    calls = (project / "calls.txt").read_text().splitlines()
    assert len(calls) == 2 and "--check-md5sums" in calls[0] and "-m aws-http prefetch ena-ftp" in calls[0]
    # 重跑: 有完成标记, 不再调用 kingfisher
    assert download.main([TAXON, "--kingfisher", kf]) == 0
    assert len((project / "calls.txt").read_text().splitlines()) == 2


def test_existing_files_verified_or_redownloaded(project, tmp_path):
    kf = fake_kf(tmp_path)
    fq = project / "fastq"
    fq.mkdir(parents=True)
    gz(fq / "SRR1_1.fastq.gz")
    gz(fq / "SRR1_2.fastq.gz")                       # 旧脚本下好的, 完整
    gz(fq / "SRR2_1.fastq.gz")                       # 中途被杀: 缺 _2
    (fq / "SRR3.fastq.gz").write_bytes(gzip.compress(b"@r\nACGT\n+\nIIII\n" * 100)[:40])   # 截断
    assert download.main([TAXON, "-r", "SRR1", "SRR2", "SRR3", "--kingfisher", kf]) == 0
    st = status(project)
    assert "完整性检查通过" in st["SRR1"]["note"]
    calls = (project / "calls.txt").read_text().splitlines()
    assert len(calls) == 2 and all("--force" in c for c in calls)
    assert "remove_incomplete" in (project / "download_history.tsv").read_text()


def test_failed_run_listed_for_retry(project, tmp_path, monkeypatch):
    kf = fake_kf(tmp_path)
    monkeypatch.setenv("FAKE_FAIL", "FAIL SRR9")
    assert download.main([TAXON, "-r", "SRR9", "SRR8", "--kingfisher", kf, "--retries", "1"]) == 1
    st = status(project)
    assert st["SRR9"]["status"] == "failed" and "No more specified" in st["SRR9"]["note"]
    assert (project / "runs_failed.txt").read_text() == "SRR9\n"
    assert sum("SRR9" in c for c in (project / "calls.txt").read_text().splitlines()) == 2


def test_check_only_and_exclude(project, tmp_path):
    fq = project / "fastq"
    fq.mkdir(parents=True)
    gz(fq / "SRR1.fastq.gz")
    lst = tmp_path / "l.txt"
    lst.write_text("SRR1\nSRR2\nSRR3\nnot_a_run\n")
    old = tmp_path / "old.txt"
    old.write_text("SRR3\tdone\n")
    assert download.main([TAXON, "--runs", str(lst), "--check-only", "--exclude", str(old)]) == 0
    st = status(project)
    assert [st[r]["status"] for r in ("SRR1", "SRR2", "SRR3")] == ["done", "missing", "excluded"]
    assert (project / "runs_failed.txt").read_text() == "SRR2\n"


def test_dry_run_and_options(project, tmp_path):
    kf = fake_kf(tmp_path)
    assert download.main([TAXON, "-r", "SRR1", "--kingfisher", kf, "--dry-run", "-m", "ena-ftp",
                          "--no-md5", "--download-threads", "40", "-f", "fastq", "fastq.gz"]) == 0
    cmd = status(project)["SRR1"]["note"]
    assert "-m ena-ftp" in cmd and "--check-md5sums" not in cmd and "--download-threads 16" in cmd
    assert "-f fastq fastq.gz" in cmd and not (project / "calls.txt").exists()


def test_missing_run_list(project, capsys):
    assert download.main([TAXON]) == 1
    assert "run_list_default.txt" in capsys.readouterr().out


def test_run_files_and_socks_proxy(tmp_path, monkeypatch):
    for n in ("SRR1_1.fastq.gz", "SRR1_2.fastq.gz", "SRR10_1.fastq.gz", "SRR1.log"):
        (tmp_path / n).write_text("x")
    assert [p.name for p in download.run_files(tmp_path, "SRR1", ["fastq.gz"])] == ["SRR1_1.fastq.gz",
                                                                                    "SRR1_2.fastq.gz"]
    monkeypatch.setenv("all_proxy", "socks://127.0.0.1:7890/")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890/")
    env = tools.child_env("/x/bin/kingfisher")
    assert "all_proxy" not in env and env["https_proxy"] and env["PATH"].startswith("/x/bin")


def fake_env(tmp_path, progs):
    """假 conda 安装: <base>/envs/kf/bin/ 下放 kingfisher 与给定程序."""
    b = tmp_path / "conda" / "envs" / "kf" / "bin"
    b.mkdir(parents=True)
    for p in ["kingfisher", *progs]:
        (b / p).write_text("#!/bin/sh\necho 0.5.0\n")
        (b / p).chmod(0o755)
    return tmp_path / "conda"


def test_conda_env_and_auto_methods(tmp_path, monkeypatch):
    base = fake_env(tmp_path, ["fasterq-dump", "prefetch", "aria2c", "curl"])
    monkeypatch.setattr(tools, "CONDA_BASES", (str(base),))
    monkeypatch.setenv("PATH", "/nonexistent")
    for k in ("CONDA_EXE", "MAMBA_EXE", "CONDA_PREFIX", "KINGFISHER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(tools, "ASCP_KEYS", ())
    kf = tools.find_kingfisher(conda_env="kf")
    assert kf == str(base / "envs" / "kf" / "bin" / "kingfisher")
    assert tools.find_kingfisher(str(base / "envs" / "kf")) == kf          # --kingfisher 给环境目录也行
    info = tools.check(kf, probe=False)
    assert tools.auto_methods(info) == ["aws-http", "prefetch", "ena-ftp"]
    assert info["_env"]["CONDA_PREFIX"] == str(base / "envs" / "kf")
    with pytest.raises(SystemExit, match="找不到 conda 环境"):
        tools.find_kingfisher(conda_env="nope")


def test_ascp_used_only_when_reachable(tmp_path, monkeypatch):
    base = fake_env(tmp_path, ["fasterq-dump", "curl", "ascp"])
    key = tmp_path / "aspera_bypass_rsa.pem"
    key.write_text("k")
    monkeypatch.setattr(tools, "ASCP_KEYS", (str(key),))
    kf = str(base / "envs" / "kf" / "bin" / "kingfisher")
    monkeypatch.setattr(tools, "probe_ascp", lambda *a, **k: False)
    assert "ena-ascp" not in tools.auto_methods(tools.check(kf))
    monkeypatch.setattr(tools, "probe_ascp", lambda *a, **k: True)
    assert tools.auto_methods(tools.check(kf))[0] == "ena-ascp"


def test_ascp_key_passed_to_kingfisher(project, tmp_path, monkeypatch):
    kf = fake_kf(tmp_path)
    key = tmp_path / "rsa.pem"
    key.write_text("k")
    assert download.main([TAXON, "-r", "SRR1", "--kingfisher", kf, "-m", "ena-ascp", "aws-http",
                          "--ascp-key", str(key), "--dry-run"]) == 0
    assert f"--ascp-ssh-key {key}" in status(project)["SRR1"]["note"]


def test_probe_ascp_needs_real_file_and_caches(tmp_path, monkeypatch):
    fake = tmp_path / "ascp"
    fake.write_text("#!/bin/sh\nexit 1\n")                 # 不产生文件 -> 失败
    fake.chmod(0o755)
    from conftest import REAL_PROBE_ASCP as probe
    assert probe(str(fake), "k") is False
    good = tmp_path / "ascp_ok"
    good.write_text("#!/bin/sh\nfor a; do d=$a; done; head -c %d /dev/zero > $d/SRR12118866_1.fastq.gz\n"
                    % tools.PROBE_FILE[1])
    good.chmod(0o755)
    assert probe(str(good), "k") is False                    # 6 小时内用缓存
    assert probe(str(good), "k", refresh=True) is True
