import json

from conftest import FIX, TAXON, fake_eutils, load_step

XML = (FIX / "sra_pkg.xml").read_text(encoding="utf-8")
ESEARCH = json.dumps({"esearchresult": {"count": "1", "webenv": "W", "querykey": "1", "idlist": []}})


def test_parse_sra_package():
    from organise_sra.parse import parse_sra_packages
    rows = parse_sra_packages(XML)
    assert len(rows) == 1
    r = rows[0]
    assert r["Run"] == "SRR37048088"
    assert r["experiment_title"] == "Genome skimming dataset of Alentiana barnichae"
    assert r["study_title"].startswith("Phylogenomic studies of the genus Alentiana")
    assert r["design_description"] == "Low coverage, partial genome scan"
    assert r["collection_date"] == "2019-06-09"
    assert r["organisation_country"] == "China"
    assert not any("email" in k or "contact" in k for k in r)


def test_attribute_without_value_skipped_and_clash_prefixed():
    from organise_sra.parse import parse_sra_packages
    extra = ("<SAMPLE_ATTRIBUTE><TAG>empty_tag</TAG></SAMPLE_ATTRIBUTE>"
             "<SAMPLE_ATTRIBUTE><TAG>study_title</TAG><VALUE>from sample</VALUE></SAMPLE_ATTRIBUTE>")
    r = parse_sra_packages(XML.replace("<SAMPLE_ATTRIBUTES>", "<SAMPLE_ATTRIBUTES>" + extra))[0]
    assert "empty_tag" not in r
    assert r["sample_study_title"] == "from sample"
    assert r["study_title"].startswith("Phylogenomic")


def write_runinfo(project, runs):
    d = project
    d.mkdir(parents=True, exist_ok=True)
    (d / "runinfo.tsv").write_text("Run\tExperiment\n" + "".join(f"{r}\tSRX{i}\n" for i, r in enumerate(runs)))
    return d


def test_step02_keys_by_run(project):
    d = write_runinfo(project, ["SRR37048088"])
    eu, _ = fake_eutils([("esearch", ESEARCH), ("efetch", XML)])
    assert load_step("02_annotate").main([TAXON], eutils=eu) == 0
    lines = (d / "annotate.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t")[0] == "Run" and len(lines) == 2


def test_step02_missing_runs_reported(project):
    d = write_runinfo(project, ["SRR37048088", "SRR0000001"])
    eu, _ = fake_eutils([("esearch", ESEARCH), ("efetch", XML)])
    assert load_step("02_annotate").main([TAXON], eutils=eu) == 1
    assert (d / "annotate_missing.txt").read_text().split() == ["SRR0000001"]


def test_step02_taxon_mode_reuses_term(project):
    d = write_runinfo(project, ["SRR37048088"])
    (d / "manifest.json").write_text(json.dumps({"mode": "taxon", "term": "txid46593[Organism:exp]"}))
    eu, t = fake_eutils([("esearch", ESEARCH), ("efetch", XML)])
    assert load_step("02_annotate").main([TAXON], eutils=eu) == 0
    assert "txid46593" in t.urls[0] and "accn" not in t.urls[0]
    assert "retmax=100" in t.urls[1]


def test_xml_entity_declaration_rejected():
    import pytest

    from organise_sra.eutils import EutilsError
    from organise_sra.parse import parse_sra_packages
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><x>&b;</x>'
    with pytest.raises(ValueError):
        parse_sra_packages(bomb)
    eu, t = fake_eutils([("efetch", bomb)])
    with pytest.raises(EutilsError):
        eu.efetch_ids("sra", ["1"])
    assert len(t.urls) == 5                                   # treated as a bad response: retried, then error


def test_bioproject_and_sample_description_from_xml():
    """benchmark 2026-10-27: RunInfo 对 51 个 DRR 的 BioProject 为空, XML 的 STUDY 里有 (同 kingfisher);
    SAMPLE/DESCRIPTION 不是属性, 之前漏取 (5 个 run)."""
    from organise_sra.parse import parse_sra_packages
    xml = XML.replace("<SAMPLE_NAME>", "<DESCRIPTION>RNA pool of multiple samples</DESCRIPTION><SAMPLE_NAME>")
    r = parse_sra_packages(xml)[0]
    assert r["bioproject"] == "PRJNA1416810"
    assert r["sample_description"] == "RNA pool of multiple samples"
    r = parse_sra_packages(XML.replace(' namespace="BioProject" label="primary">PRJNA1416810', ">X"))[0]
    assert r["bioproject"] == "" and r["sample_description"] == ""


def test_sample_description_attribute_fills_same_column():
    """很多 BioSample 把描述写成属性 sample_description (Polynoidae 16 个 run); 不应拆成 sample_sample_description."""
    from organise_sra.parse import parse_sra_packages
    attr = "<SAMPLE_ATTRIBUTE><TAG>sample_description</TAG><VALUE>from attribute</VALUE></SAMPLE_ATTRIBUTE>"
    r = parse_sra_packages(XML.replace("<SAMPLE_ATTRIBUTES>", "<SAMPLE_ATTRIBUTES>" + attr))[0]
    assert r["sample_description"] == "from attribute" and "sample_sample_description" not in r
    both = XML.replace("<SAMPLE_NAME>", "<DESCRIPTION>from element</DESCRIPTION><SAMPLE_NAME>")
    r = parse_sra_packages(both.replace("<SAMPLE_ATTRIBUTES>", "<SAMPLE_ATTRIBUTES>" + attr))[0]
    assert r["sample_description"] == "from element" and r["sample_sample_description"] == "from attribute"


def kf_file(tmp_path, sep="\t"):
    """kingfisher annotate -a 输出的样子: 列 run, bases 为浮点, UTF-8 被按 Latin-1 读坏 (benchmark 2026-10-27)."""
    bad = "Alejandro Martínez".encode().decode("latin-1")
    rows = [["run", "bioproject", "collected_by", "organisation_contact_name", "organisation_contact_email",
             "study_links", "bases", "geo_loc_name"],
            ["SRR1", "PRJDB1", bad, "Someone", "a@b.c", "[]", "353921600.0", "Japan:Okinawa"],
            ["SRR2", "PRJNA2", "李四 Zhang", "X", "x@y.z", "[]", "1.0", "China: Qingdao"]]
    f = tmp_path / f"kf.{'tsv' if sep == chr(9) else 'csv'}"
    f.write_text("".join(sep.join(r) + "\n" for r in rows), encoding="utf-8")
    return f


def test_step02_from_kingfisher_file(project, tmp_path):
    import csv
    d = write_runinfo(project, ["SRR1", "SRR2", "SRR3"])
    eu, t = fake_eutils([])                                   # 不应联网
    assert load_step("02_annotate").main([TAXON, "--kingfisher", str(kf_file(tmp_path))], eutils=eu) == 1
    assert t.urls == []
    rows = list(csv.DictReader((d / "annotate.tsv").open(encoding="utf-8"), delimiter="\t"))
    assert [r["Run"] for r in rows] == ["SRR1", "SRR2"]
    assert rows[0]["collected_by"] == "Alejandro Martínez"      # 乱码修复
    assert rows[1]["collected_by"] == "李四 Zhang"               # 本来正确的不动
    assert rows[0]["bioproject"] == "PRJDB1" and rows[0]["study_links"] == ""
    assert not any("contact" in k for k in rows[0])             # 不带联系人
    assert (d / "annotate_missing.txt").read_text().split() == ["SRR3"]


def test_step02_from_kingfisher_csv(project, tmp_path):
    write_runinfo(project, ["SRR1", "SRR2"])
    eu, _ = fake_eutils([])
    assert load_step("02_annotate").main([TAXON, "--kingfisher", str(kf_file(tmp_path, ","))], eutils=eu) == 0
