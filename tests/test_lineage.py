from conftest import FIX, fake_eutils
from test_runinfo import esearch_json

from organise_sra import intable, lineage
from organise_sra.runinfo import read_runs
from organise_sra.tsv import read_tsv
from organise_sra.xlsx import write_xlsx

TAXXML = (FIX / "taxonomy.xml").read_text(encoding="utf-8")
RUNINFO = (FIX / "runinfo.csv").read_text(encoding="utf-8-sig").replace("46593.0", "46594") \
    .replace("46593", "3682672")


def eutils():
    return fake_eutils([("db=taxonomy", TAXXML), ("efetch", RUNINFO), ("esearch", esearch_json(2))])


def test_pick_column_by_name_number_and_auto(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("id,note,SRA\nA,x,SRR1\nB,SRR9 is mentioned,SRR2;SRR3\nC,,\n")
    assert intable.runs_from_table(f, "sra")[0] == ["SRR1", "SRR2", "SRR3"]
    assert intable.runs_from_table(f, "3")[0] == ["SRR1", "SRR2", "SRR3"]
    assert intable.runs_from_table(f)[0] == ["SRR1", "SRR2", "SRR3"]           # 自动: run 号最多的列


def test_headerless_list_and_xlsx(tmp_path):
    f = tmp_path / "l.txt"
    f.write_text("SRR1\nERR2\n\nSRR1\n")
    runs, bad, header, rows, j = intable.runs_from_table(f)
    assert runs == ["SRR1", "ERR2"] and header == ["Run"] and len(rows) == 3
    x = write_xlsx(tmp_path / "s.xlsx", {"S": (["Sample", "Run"], [["a", "SRR5"], ["b", "DRR6"], ["c", "foo"]])})
    assert intable.runs_from_table(x, "Run")[:2] == (["SRR5", "DRR6"], ["FOO"])
    assert read_runs(x) == (["SRR5", "DRR6"], ["FOO"])                         # --runs 给 xlsx 自动识别列


def test_read_runs_plain_list_unchanged(tmp_path):
    f = tmp_path / "l.txt"
    f.write_text("SRR1, srr2\tSRR3\nbad\n")
    assert read_runs(f) == (["SRR1", "SRR2", "SRR3"], ["BAD"])


def test_lineage_appends_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "my.tsv"
    f.write_text("Sample\tRun\tfamily\nP1\tSRR1\tmine\nP2\tSRR3\t\nP3\tSRR404\t\n")
    eu, _ = eutils()
    assert lineage.main([str(f), "--column", "Run"], eutils=eu) == 0
    cols, rows = read_tsv(tmp_path / "my_sra" / "my_lineage.tsv")
    assert cols[:3] == ["Sample", "Run", "family"] and "family_sra" in cols     # 不覆盖原有同名列
    r = {x["Sample"]: x for x in rows}
    assert r["P1"]["family"] == "mine" and r["P1"]["family_sra"] == "Polynoidae"
    assert r["P1"]["phylum"] == "Annelida" and r["P1"]["Gb"] == "2.000"
    assert r["P2"]["genus"] == "Harmothoe" and r["P2"]["rank"] == "genus"
    assert r["P3"]["lineage_note"] == "SRR404 查不到"
    assert (tmp_path / "my_sra" / "my_lineage.xlsx").exists()


def test_lineage_no_runs(tmp_path, capsys):
    f = tmp_path / "x.csv"
    f.write_text("a,b\n1,2\n")
    try:
        lineage.main([str(f)])
    except SystemExit as e:
        assert "没有 SRR" in str(e)
