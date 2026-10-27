import json
from types import SimpleNamespace

from conftest import TAXON, fake_eutils

from organise_sra import genome
from organise_sra.tsv import read_tsv, write_tsv


def run(**kw):
    base = {"LibraryStrategy": "WGS", "LibrarySource": "GENOMIC", "Platform": "ILLUMINA", "Model": "NovaSeq 6000"}
    return dict(base, **kw)


def test_assign_role():
    role = lambda **kw: genome.assign_role(run(**kw))[0]          # noqa: E731
    assert role() == "WGS"
    assert role(LibrarySource="METAGENOMIC") == ""
    assert role(LibraryStrategy="Hi-C") == "HiC"
    assert role(LibraryStrategy="Tethered Chromatin Conformation Capture") == "HiC"
    assert role(experiment_title="Hi-C library of worm") == "HiC"              # WGS 标注但其实是 Hi-C
    assert role(Platform="PACBIO_SMRT", Model="Revio") == "HiFi"
    assert role(Platform="PACBIO_SMRT", Model="Sequel II", LibraryName="hifiread_library") == "HiFi"
    assert role(Platform="PACBIO_SMRT", Model="Sequel II") == "PacBio"
    assert role(Platform="OXFORD_NANOPORE", Model="PromethION") == "ONT"
    assert role(LibraryStrategy="RNA-Seq", LibrarySource="TRANSCRIPTOMIC") == "RNA"
    assert role(LibraryStrategy="FL-cDNA", Platform="PACBIO_SMRT") == "IsoSeq"
    assert role(LibraryStrategy="AMPLICON") == "" and role(LibraryStrategy="RAD-Seq") == ""


def test_tier():
    a = SimpleNamespace(long_gb=15, hic_gb=10, short_gb=30, long_x=20, hic_x=10, short_x=50)
    gb = dict.fromkeys(genome.ROLES, 0.0)
    assert genome.tier_of(dict(gb, HiFi=20, HiC=12), None, a)[0] == "A"
    assert genome.tier_of(dict(gb, ONT=20), None, a)[0] == "B"
    assert genome.tier_of(dict(gb, WGS=40), None, a)[0] == "C"
    assert genome.tier_of(dict(gb, RNA=100), None, a)[0] == "D"
    assert genome.tier_of(dict(gb, HiFi=20, HiC=12), 2000, a)[0] == "D"        # 2 Gb 基因组: 10x 不够


ASM = json.dumps({"result": {"uids": ["1"], "1": {
    "assemblyaccession": "GCA_1.1", "assemblyname": "x", "organism": "Worma one (segmented worms)", "taxid": 11,
    "speciestaxid": 11, "assemblystatus": "Chromosome", "contign50": 2000000, "scaffoldn50": 9e7, "coverage": "40",
    "asmreleasedate_genbank": "2025/01/01 00:00", "submitterorganization": "S", "refseq_category": "",
    "gb_bioprojects": [{"bioprojectaccn": "PRJEB1"}],
    "meta": '<Stat category="total_length" sequence_tag="all">1000000000</Stat>'}}})


def test_genome_from_existing_table(project):
    d = project
    d.mkdir(parents=True)
    rows = [run(Run="R1", TaxID="11", ScientificName="Worma one", Platform="PACBIO_SMRT", Model="Revio",
                bases="30e9", spots="2e6", BioProject="P1", BioSample="S1", family="Wormidae"),
            run(Run="R2", TaxID="11", ScientificName="Worma one", LibraryStrategy="Hi-C", bases="15e9", spots="1e8",
                BioProject="P1", BioSample="S1"),
            run(Run="R3", TaxID="22", ScientificName="Worma two", bases="5e9", spots="3e7", BioSample="S3"),
            run(Run="R4", TaxID="22", ScientificName="Worma two", LibraryStrategy="AMPLICON", bases="1e6")]
    write_tsv(d / "table.tsv", list(rows[0]) + ["experiment_title", "family"], rows)
    (d / "manifest.json").write_text(json.dumps({"mode": "taxon", "taxid": "6340"}))
    eu, t = fake_eutils([("esearch", json.dumps({"esearchresult": {"count": "1", "webenv": "W", "querykey": "1",
                                                                   "idlist": ["1"]}})), ("esummary", ASM)])
    assert genome.main([TAXON], eutils=eu) == 0
    assert "txid6340%5BOrganism%3Aexp%5D" in t.urls[0]                          # 类群模式: 一次子树检索
    _, sp = read_tsv(d / "genome" / "genome_species.tsv")
    s = {r["ScientificName"]: r for r in sp}
    assert s["Worma one"]["tier"] == "A" and s["Worma one"]["genome_size_source"] == "组装 GCA_1.1"
    assert s["Worma one"]["HiFi_x"] == "30.0" and s["Worma one"]["best_assembly_level"] == "Chromosome"
    assert s["Worma two"]["tier"] == "D"
    lst = d / "genome" / "run_lists" / "Worma_one_11"
    assert (lst / "all.txt").read_text().split() == ["R1", "R2"] and (lst / "HiFi.txt").read_text() == "R1\n"
    assert (d / "genome" / "run_list_HiC.txt").read_text() == "R2\n"
    assert "R4" not in (d / "genome" / "genome_runs.tsv").read_text()


def test_genome_sizes_file_overrides(project, tmp_path):
    d = project
    d.mkdir(parents=True)
    rows = [run(Run="R1", TaxID="22", ScientificName="Worma two", bases="40e9", spots="3e8", BioSample="S")]
    write_tsv(d / "table.tsv", list(rows[0]), rows)
    sz = tmp_path / "sizes.tsv"
    sz.write_text("species\tMb\nWorma two\t500\n")
    assert genome.main([TAXON, "--genome-sizes", str(sz), "--no-assembly"]) == 0
    r = read_tsv(d / "genome" / "genome_species.tsv")[1][0]
    assert r["genome_size_Mb"] == "500.0" and r["WGS_x"] == "80.0" and r["tier"] == "C"
