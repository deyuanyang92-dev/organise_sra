from conftest import TAXON, load_step

from organise_sra.classify import select_samples
from organise_sra.tsv import write_tsv


def s(key, gb, taxid="1", bp="P1", cat="shallow_WGS", layout="PAIRED", runs=None, plat="ILLUMINA"):
    return {"sample_key_derived": key, "category_derived": cat, "TaxID": taxid, "BioProject": bp,
            "total_Gb": gb, "LibraryLayout": layout, "Platform": plat, "runs": runs or f"{key}r"}


def keys(rows):
    return [r["sample_key_derived"] for r in rows]


def test_top_per_group():
    df = [s("A", 1), s("B", 5), s("C", 3), s("D", 2)]
    out = select_samples(df, ["shallow_WGS"], None, "PAIRED", 3)
    assert keys(out) == ["B", "C", "D"]


def test_selected_sample_brings_all_runs(project):
    d = project
    d.mkdir(parents=True)
    rows = [s("A", 2, runs="SRR1;SRR2"), s("B", 1, cat="Hi-C", runs="SRR3")]
    write_tsv(d / "samples.tsv", list(rows[0]), rows)
    assert load_step("05_select").main([TAXON, "--type", "shallow_WGS"]) == 0
    assert (d / "run_list_default.txt").read_text().split() == ["SRR1", "SRR2"]
    assert (d / "selected_default.tsv").exists()


def test_layout_filter():
    df = [s("A", 1, layout="SINGLE"), s("B", 1)]
    assert keys(select_samples(df, ["shallow_WGS"], None, "PAIRED", 3)) == ["B"]
    assert len(select_samples(df, ["shallow_WGS"], None, "ALL", 3)) == 2


def test_platform_filter():
    df = [s("A", 1, plat="DNBSEQ"), s("B", 1)]
    assert keys(select_samples(df, ["shallow_WGS"], ["DNBSEQ"], "ALL", 3)) == ["A"]


def test_per_run_mode():
    runs = [{"Run": r, "sample_key_derived": "A", "category_derived": "shallow_WGS", "TaxID": "1",
                          "BioProject": "P1", "bases": str(b), "LibraryLayout": "PAIRED", "Platform": "ILLUMINA"}
                         for r, b in [("R1", 1e9), ("R2", 3e9), ("R3", 2e9), ("R4", 4e9)]]
    out = select_samples([], ["shallow_WGS"], None, "PAIRED", 3, per="run", runs=runs)
    assert [r["runs"] for r in out] == ["R4", "R2", "R3"]


def test_sp_taxids_not_collapsed():
    df = [s("A", 1, taxid="900"), s("B", 1, taxid="901")]
    assert len(select_samples(df, ["shallow_WGS"], None, "PAIRED", 1)) == 2


def test_select_empty(project):
    d = project
    d.mkdir(parents=True)
    (d / "samples.tsv").write_text("sample_key_derived\tcategory_derived\tTaxID\tBioProject\ttotal_Gb\t"
                                   "LibraryLayout\tPlatform\truns\n")
    assert load_step("05_select").main([TAXON]) == 0
    assert (d / "run_list_default.txt").read_text() == ""


def test_per_run_with_no_runs(project):
    d = project
    d.mkdir(parents=True)
    (d / "table.tsv").write_text("Run\tcategory_derived\tbases\n")
    assert load_step("05_select").main([TAXON, "--per", "run"]) == 0
    assert (d / "run_list_default.txt").read_text() == ""


def test_per_run_groups_by_backfilled_bioproject():
    """RunInfo BioProject 为空的两个不同研究不应并到同一个空键下."""
    runs = [{"Run": f"R{i}", "TaxID": "1", "BioProject": "", "BioProject_derived": bp, "bases": str(i * 10**9),
             "category_derived": "shallow_WGS", "LibraryLayout": "PAIRED", "Platform": "ILLUMINA"}
            for i, bp in [(1, "PRJDB1"), (2, "PRJDB2")]]
    out = select_samples(None, ["shallow_WGS"], None, "PAIRED", 1, per="run", runs=runs)
    assert sorted(r["Run"] for r in out) == ["R1", "R2"]
