import json

import pytest
from conftest import FIX, TAXON, fake_eutils, load_step


def esearch_json(count, ids=(), webenv="WE", qk="1"):
    return json.dumps({"esearchresult": {"count": str(count), "webenv": webenv, "querykey": qk,
                                         "idlist": list(ids)}})


RUNINFO = (FIX / "runinfo.csv").read_text(encoding="utf-8-sig")


def test_parse_runinfo_quirks():
    from organise_sra.parse import parse_runinfo
    rows = parse_runinfo((FIX / "runinfo.csv").read_text(encoding="utf-8"))
    assert [r["Run"] for r in rows] == ["SRR1", "SRR2", "SRR3"]
    assert "Run" in rows[0]                                  # BOM stripped from first header


def test_identity_env_overrides_config(tmp_path, monkeypatch):
    from organise_sra.eutils import load_identity
    cfg = tmp_path / "ncbi.json"
    cfg.write_text('{"email": "a@x", "api_key": "K1"}')
    monkeypatch.delenv("NCBI_EMAIL", raising=False)
    monkeypatch.setenv("NCBI_API_KEY", "K2")
    assert load_identity(config=cfg) == ("a@x", "K2")
    assert load_identity("b@y", "K3", config=cfg) == ("b@y", "K3")      # 命令行参数最优先
    monkeypatch.setenv("ORGANISE_SRA_CONFIG", str(cfg))
    assert load_identity() == ("a@x", "K2")


def test_resolve_taxid_ambiguous():
    from organise_sra.eutils import EutilsError, resolve_taxid
    eu, _ = fake_eutils([("esearch", esearch_json(2, ["111", "222"])),
                         ("efetch", "<TaxaSet><Taxon><TaxId>111</TaxId><ScientificName>X</ScientificName>"
                                    "<Lineage>A; B</Lineage></Taxon><Taxon><TaxId>222</TaxId>"
                                    "<ScientificName>X</ScientificName><Lineage>C; D</Lineage></Taxon></TaxaSet>")])
    with pytest.raises(EutilsError) as e:
        resolve_taxid(eu, "X")
    assert "111" in str(e.value) and "222" in str(e.value)


def test_resolve_taxid_numeric_passthrough():
    from organise_sra.eutils import resolve_taxid
    eu, t = fake_eutils([])
    assert resolve_taxid(eu, "46593") == "46593"
    assert t.urls == []


def test_error_body_retried_then_fails():
    from organise_sra.eutils import EutilsError
    eu, t = fake_eutils([("esearch", "<!DOCTYPE html><html>busy</html>")])
    with pytest.raises(EutilsError):
        eu.esearch("sra", "x")
    assert len(t.urls) == 5


def test_xml_error_body_is_failure():
    from organise_sra.eutils import EutilsError
    eu, t = fake_eutils([("efetch", "<eFetchResult><ERROR>Empty result</ERROR></eFetchResult>")])
    with pytest.raises(EutilsError):
        eu.efetch_ids("sra", ["1"])


def run01(argv, rules):
    eu, t = fake_eutils(rules)
    rc = load_step("01_runinfo").main(argv, eutils=eu)
    return rc, t


def manifest(project):
    return json.loads((project / "manifest.json").read_text(encoding="utf-8"))


def test_step01_taxon_invariant_ok(project):
    rc, _ = run01([TAXON, "--taxid", "46593"], [("esearch", esearch_json(2)), ("efetch", RUNINFO)])
    assert rc == 0
    m = manifest(project)
    assert m["ok"] is True and m["experiments"] == 2 and m["runs"] == 3
    assert m["term"] == "txid46593[Organism:exp]"
    tsv = (project / "runinfo.tsv").read_text(encoding="utf-8").splitlines()
    assert len(tsv) == 4 and tsv[0].startswith("Run\t")


def test_step01_invariant_mismatch(project):
    rc, _ = run01([TAXON, "--taxid", "46593"], [("esearch", esearch_json(3)), ("efetch", RUNINFO)])
    assert rc == 1
    m = manifest(project)
    assert m["ok"] is False and any("Experiment" in p for p in m["problems"])


def test_max_experiments_guard(project):
    rc, t = run01([TAXON, "--taxid", "1"], [("esearch", esearch_json(300000))])
    assert rc == 2
    assert not any("efetch" in u for u in t.urls)


def test_runs_not_found(project, tmp_path):
    lst = tmp_path / "runs.txt"
    lst.write_text("SRR1\nSRR2\n\nSRR3\nSRR3\nSRR999\n")
    rc, t = run01([TAXON, "--runs", str(lst)], [("efetch", RUNINFO)])
    assert rc == 0
    assert len(t.urls) == 1 and "id=SRR1%2CSRR2%2CSRR3%2CSRR999" in t.urls[0] and "rettype=runinfo" in t.urls[0]
    nf = (project / "runs_not_found.txt").read_text().split()
    assert nf == ["SRR999"]
    assert manifest(project)["not_found"] == 1


def test_runinfo_file_quirks(project):
    rc, t = run01([TAXON, "--runinfo", str(FIX / "runinfo.csv")], [])
    assert rc == 0 and t.urls == []
    m = manifest(project)
    assert m["mode"] == "runinfo" and m["runs"] == 3


def test_long_query_is_posted():
    eu, t = fake_eutils([("esearch", esearch_json(0))])
    eu.esearch("sra", " OR ".join(f"SRR{1000000 + i}[accn]" for i in range(200)))
    assert t.posts[0] is not None and len(t.urls[0]) < 2000
    assert b"SRR1000199" in t.posts[0]


def test_short_query_is_get():
    eu, t = fake_eutils([("esearch", esearch_json(0))])
    eu.esearch("sra", "txid1[Organism:exp]")
    assert t.posts[0] is None and "txid1" in t.urls[0]


def flaky(bad, good, n_bad=1):
    calls = {"n": 0}

    def body(url):
        calls["n"] += 1
        return bad if calls["n"] <= n_bad else good
    return body


def test_empty_json_body_is_retried():
    eu, t = fake_eutils([("esearch", flaky("", esearch_json(7)))])
    assert eu.esearch("sra", "x")["count"] == 7 and len(t.urls) == 2


def test_truncated_xml_is_retried():
    eu, t = fake_eutils([("efetch", flaky("<EXPERIMENT_PACKAGE_SET><EXPERIMENT_PACK", "<EXPERIMENT_PACKAGE_SET/>"))])
    pages = list(eu.efetch_history("sra", "W", "1", 1, retmode="xml"))
    assert pages == ["<EXPERIMENT_PACKAGE_SET/>"] and len(t.urls) == 2


# --- final-review fixes ---

def test_empty_runinfo_page_is_retried():
    eu, t = fake_eutils([("efetch", flaky("", RUNINFO))])
    pages = list(eu.efetch_history("sra", "W", "1", 1, rettype="runinfo", retmode="text"))
    assert pages == [RUNINFO] and len(t.urls) == 2


def test_truncated_runinfo_page_is_retried():
    cut = RUNINFO[:RUNINFO.index("SRR2") + 30]
    eu, t = fake_eutils([("efetch", flaky(cut, RUNINFO))])
    assert list(eu.efetch_history("sra", "W", "1", 1, rettype="runinfo", retmode="text")) == [RUNINFO]


def test_runs_mode_batches_in_parallel(project, tmp_path):
    from organise_sra.runinfo import ACC_BATCH
    lst = tmp_path / "runs.txt"
    lst.write_text("".join(f"SRR{i}\n" for i in range(1, ACC_BATCH * 2 + 2)))
    rc, t = run01([TAXON, "--runs", str(lst)], [("efetch", RUNINFO)])
    assert len(t.urls) == 3 and not any("esearch" in u for u in t.urls)
    assert all(len(u) < 1200 for u in t.urls)                     # 直连不超 DIRECT_MAX


def test_runs_case_and_invalid(project, tmp_path):
    lst = tmp_path / "runs.txt"
    lst.write_text("srr1\nSRR2,SRR3\nSRX99\n")
    rc, t = run01([TAXON, "--runs", str(lst)], [("efetch", RUNINFO)])
    assert rc == 0
    assert (project / "runs_not_found.txt").read_text().split() == ["SRX99"]
    assert "SRX99" not in t.urls[0]


def test_runinfo_file_without_required_columns(project, tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("Run;LibraryStrategy\nSRR1;WGS\n")
    rc, _ = run01([TAXON, "--runinfo", str(f)], [])
    assert rc == 1


def test_runs_not_found_cleared_in_other_modes(project, tmp_path):
    d = project
    d.mkdir(parents=True)
    (d / "runs_not_found.txt").write_text("SRR_OLD\n")
    run01([TAXON, "--runinfo", str(FIX / "runinfo.csv")], [])
    assert (d / "runs_not_found.txt").read_text() == ""
