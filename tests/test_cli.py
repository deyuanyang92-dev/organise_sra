import pytest
from conftest import FIX, TAXON

from organise_sra import cli


def test_all_runs_five_steps_offline(project, monkeypatch):
    """--runinfo 模式下 01、04、05 不联网; 02/03 用假 Eutils 也能跑通, 这里只测调度顺序与停止."""
    seen = []
    monkeypatch.setattr(cli, "run_step", lambda name, argv: seen.append((name, argv)) or 0)
    assert cli.main(["all", TAXON, "--runinfo", str(FIX / "runinfo.csv")]) == 0
    assert [n for n, _ in seen] == ["runinfo", "annotate", "taxonomy", "table", "select"]
    assert all(a == [TAXON, "--runinfo", str(FIX / "runinfo.csv")] for _, a in seen)


def test_all_stops_at_failed_step(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "run_step", lambda name, argv: seen.append(name) or (1 if name == "annotate" else 0))
    assert cli.main(["all", TAXON]) == 1
    assert seen == ["runinfo", "annotate"]
    assert f"organise-sra annotate {TAXON}" in capsys.readouterr().out


def test_numeric_alias_and_real_step(project):
    assert cli.main(["01", TAXON, "--runinfo", str(FIX / "runinfo.csv")]) == 0
    assert (project / "runinfo.tsv").exists()


def test_outdir_option(tmp_path):
    out = tmp_path / "x" / "y"
    assert cli.main(["runinfo", TAXON, "--runinfo", str(FIX / "runinfo.csv"), "-o", str(out)]) == 0
    assert (out / "runinfo.tsv").exists() and (out / "manifest.json").exists()


def test_unknown_command():
    with pytest.raises(SystemExit, match="未知命令"):
        cli.main(["nope", TAXON])


def test_help_and_version(capsys):
    assert cli.main([]) == 0 and "organise-sra all" in capsys.readouterr().out
    assert cli.main(["--version"]) == 0 and "organise-sra 0." in capsys.readouterr().out
