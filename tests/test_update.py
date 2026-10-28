"""增量更新: 01 只取新增/修改/删除的 experiment, 02 只重取变化的 run, 04 输入不变则跳过."""
import json

from conftest import FIX, TAXON, fake_eutils, load_step

RUNINFO = (FIX / "runinfo.csv").read_text(encoding="utf-8-sig")         # SRR1, SRR2 (SRX1); SRR3 (SRX2)
XML = (FIX / "sra_pkg.xml").read_text(encoding="utf-8")                  # SRR37048088
HEAD = RUNINFO.splitlines()[0]
NEW_RUN = HEAD + "\n" + "SRR37048088,2026-10-01,10,3000000000,900,SRX3" + "," * (HEAD.count(",") - 5) + "\n"


def esearch(all_ids, mdat_ids=()):
    def body(url):
        if "MDAT" in url:
            ids = list(mdat_ids)
        elif "retstart" in url:
            ids = list(all_ids)
        else:                                               # usehistory 检索
            return json.dumps({"esearchresult": {"count": str(len(all_ids)), "webenv": "W", "querykey": "1",
                                                 "idlist": []}})
        return json.dumps({"esearchresult": {"count": str(len(ids)), "idlist": ids}})
    return ("esearch.fcgi", body)


def esummary(mapping):
    res = {"uids": list(mapping)}
    res.update({u: {"uid": u, "expxml": f'<Experiment acc="{x}" ver="1"/>'} for u, x in mapping.items()})
    return ("esummary.fcgi", json.dumps({"result": res}))


def full_run(project):
    eu, _ = fake_eutils([esearch(["1", "2"]), ("efetch", RUNINFO)])
    assert load_step("01_runinfo").main([TAXON, "--taxid", "46593"], eutils=eu) == 0
    m = json.loads((project / "manifest.json").read_text())
    assert m["incremental"] is False and m["last_full"] == m["date"]
    assert (project / "sra_uids.txt").read_text().split() == ["1", "2"]
    return m


def runs_of(project):
    return [line.split("\t")[0] for line in (project / "runinfo.tsv").read_text().splitlines()[1:]]


def test_taxon_incremental_new_and_removed(project):
    full_run(project)
    # uid 1 (SRX1: SRR1, SRR2) 撤回, uid 3 (SRX3) 新增
    eu, t = fake_eutils([esearch(["2", "3"]), esummary({"1": "SRX1"}), ("efetch", NEW_RUN)])
    assert load_step("01_runinfo").main([TAXON, "--taxid", "46593"], eutils=eu) == 0
    m = json.loads((project / "manifest.json").read_text())
    assert m["incremental"] is True and m["ok"] is True
    assert (m["new_runs"], m["removed_runs"], m["update"]["removed_experiments"]) == (1, 2, 1)
    assert sorted(runs_of(project)) == ["SRR3", "SRR37048088"]
    efetch = [u for u in t.urls if "efetch" in u]
    assert len(efetch) == 1 and "id=3&" in efetch[0]                   # 只取新增的 UID
    assert (project / "runs_new.txt").read_text().split() == ["SRR37048088"]
    hist = (project / "update_history.tsv").read_text().splitlines()
    assert sum("\tremoved\tSRR" in x for x in hist) == 2 and any("\tadded\tSRR37048088" in x for x in hist)

    # 02: 沿用 SRR3 的旧注释, 只取 SRR37048088
    (project / "annotate.tsv").write_text("Run\tx\nSRR1\told1\nSRR3\told3\n")
    eu, t = fake_eutils([("efetch", XML)])
    assert load_step("02_annotate").main([TAXON], eutils=eu) == 0
    assert len(t.urls) == 1 and "SRR37048088" in t.urls[0] and "SRR3%2C" not in t.urls[0]
    rows = (project / "annotate.tsv").read_text().splitlines()
    assert any(r.startswith("SRR3\t") and r.endswith("old3") for r in rows) and len(rows) == 3


def test_taxon_modified_experiment_refetched(project):
    full_run(project)
    changed = HEAD + "\n" + "SRR3,2025-01-01,10,9000000000,2000,SRX2" + "," * (HEAD.count(",") - 5) + "\n"
    eu, _ = fake_eutils([esearch(["1", "2"], mdat_ids=["2"]), ("efetch", changed)])
    assert load_step("01_runinfo").main([TAXON, "--taxid", "46593"], eutils=eu) == 0
    m = json.loads((project / "manifest.json").read_text())
    assert (m["new_runs"], m["updated_runs"], m["removed_runs"]) == (0, 1, 0)
    assert (project / "runs_changed.txt").read_text().split() == ["SRR3"]
    assert "9000000000" in (project / "runinfo.tsv").read_text()


def test_unknown_removed_uid_falls_back_to_full(project):
    full_run(project)
    eu, _ = fake_eutils([esearch(["2"]), esummary({}), ("efetch", RUNINFO.split("\n\n")[1])])
    assert load_step("01_runinfo").main([TAXON, "--taxid", "46593"], eutils=eu) == 0
    m = json.loads((project / "manifest.json").read_text())
    assert m["incremental"] is False and runs_of(project) == ["SRR3"]
    assert sum("\tremoved\t" in x for x in (project / "update_history.tsv").read_text().splitlines()) == 2


def test_full_flag_keeps_old_behaviour(project):
    full_run(project)
    eu, t = fake_eutils([esearch(["1", "2"]), ("efetch", RUNINFO)])
    assert load_step("01_runinfo").main([TAXON, "--taxid", "46593", "--full"], eutils=eu) == 0
    assert json.loads((project / "manifest.json").read_text())["incremental"] is False
    assert any("WebEnv" in u for u in t.urls)


def test_runs_mode_fetches_only_new(project, tmp_path):
    lst = tmp_path / "l.txt"
    lst.write_text("SRR1\nSRR2\n")
    eu, _ = fake_eutils([("efetch", RUNINFO.split("\n\n")[0])])
    assert load_step("01_runinfo").main([TAXON, "--runs", str(lst)], eutils=eu) == 0
    lst.write_text("SRR1\nSRR2\nSRR3\n")
    eu, t = fake_eutils([("efetch", RUNINFO.split("\n\n")[1])])
    assert load_step("01_runinfo").main([TAXON, "--runs", str(lst)], eutils=eu) == 0
    assert len(t.urls) == 1 and "id=SRR3&" in t.urls[0]
    assert runs_of(project) == ["SRR1", "SRR2", "SRR3"]
    assert (project / "runs_new.txt").read_text().split() == ["SRR3"]


def test_table_skipped_when_inputs_unchanged(project, capsys):
    project.mkdir(parents=True)
    (project / "runinfo.tsv").write_text("Run\tExperiment\tLibraryStrategy\tbases\tTaxID\tPlatform\n"
                                         "SRR1\tSRX1\tWGS\t2000000000\t1\tILLUMINA\n")
    table = load_step("04_table")
    assert table.main([TAXON]) == 0
    assert table.main([TAXON]) == 0
    assert "跳过" in capsys.readouterr().out
    (project / "runinfo.tsv").write_text((project / "runinfo.tsv").read_text().replace("2000000000", "1"))
    assert table.main([TAXON]) == 0
    assert "跳过" not in capsys.readouterr().out
