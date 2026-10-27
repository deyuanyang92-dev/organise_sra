import json

import pytest
from conftest import TAXON, load_step
from xlsx_read import Book

SHEETS = ["Summary", "Samples", "shallow_WGS", "deep_WGS", "long_read_WGS", "RNA-Seq", "Hi-C", "Other",
          "Runs", "QC", "Query"]
HDR = "Run\tExperiment\tLibraryStrategy\tLibrarySelection\tLibrarySource\tLibraryLayout\tPlatform\tbases\t" \
      "BioProject\tBioSample\tTaxID\tScientificName\n"


def setup(project, runinfo_rows, annotate=None, taxonomy=None):
    d = project
    d.mkdir(parents=True)
    (d / "runinfo.tsv").write_text(HDR + "".join(runinfo_rows), encoding="utf-8")
    if annotate is not None:
        (d / "annotate.tsv").write_text(annotate, encoding="utf-8")
    if taxonomy is not None:
        (d / "taxonomy.tsv").write_text(taxonomy, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"taxon": TAXON, "mode": "taxon", "term": "txid1[Organism:exp]",
                                                 "runs": len(runinfo_rows), "problems": []}))
    return d


ROWS = ["SRR1\tX1\tWGS\tRANDOM\tGENOMIC\tPAIRED\tILLUMINA\t2000000000\tP1\tS1\t11.0\tA b\n",
        "SRR2\tX2\tRNA-Seq\tcDNA\tTRANSCRIPTOMIC\tPAIRED\tILLUMINA\t5000000000\t\tS2\t11\tA b\n",
        "SRR3\tX3\tWGS\tPCR\tGENOMIC\tSINGLE\tILLUMINA\t80000000\tP3\tS3\t12\tC d\n"]
ANN = "Run\texperiment_title\tgeo_loc_name\nSRR1\t=cmd|' /C calc'!A0\tChina: Qingdao\n"
TAX = "TaxID\trank_derived\tphylum\tclass\torder\tfamily\tgenus\tspecies\n11\tspecies\tAnnelida\t\t\tF\tA\tA b\n"


def xlsx(project):
    return next(project.glob("*_SRA_*.xlsx"))


def test_merge_preserves_rows(project):
    d = setup(project, ROWS, ANN, TAX)
    assert load_step("04_table").main([TAXON]) == 0
    lines = (d / "table.tsv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    hdr = lines[0].split("\t")
    r1 = dict(zip(hdr, lines[1].split("\t")))
    assert r1["family"] == "F" and r1["geo_loc_name"] == "China: Qingdao" and r1["TaxID"] == "11.0"
    assert r1["category_derived"] == "shallow_WGS"


def test_excel_sheets(project):
    setup(project, ROWS, ANN, TAX)
    load_step("04_table").main([TAXON])
    wb = Book(xlsx(project))
    assert wb.sheetnames == SHEETS
    runs = wb.sheet("Runs")["rows"]
    hdr = [runs[0][j][1] for j in sorted(runs[0])]
    assert len(runs) == 4
    kind, value, _ = runs[1][hdr.index("experiment_title")]
    assert kind == "s" and value.startswith("=cmd")                 # NCBI text never becomes a formula
    qc = [r[0][1] for r in wb.sheet("QC")["rows"][1:]]
    assert "WGS_selection_PCR" in qc and "empty_BioProject" in qc
    assert runs[0][hdr.index("category_derived")][2]["fill"].endswith("FFF2CC")


def test_empty_pipeline(project):
    setup(project, [])
    assert load_step("04_table").main([TAXON]) == 0
    q = {r[0]: r[1] for r in Book(xlsx(project)).values("Query")[1:]}
    assert str(q["runs"]) == "0"


def test_requires_inputs(project):
    with pytest.raises(SystemExit) as e:
        load_step("04_table").main([TAXON])
    assert "runinfo.tsv" in str(e.value)


def test_excel_look_kept(project):
    setup(project, ROWS, ANN, TAX)
    load_step("04_table").main([TAXON])
    ws = Book(xlsx(project)).sheet("Runs")
    from organise_sra.xlsx import col_letter
    assert ws["freeze"] == "A2" and ws["filter"] == f"A1:{col_letter(len(ws['rows'][0]) - 1)}4"
    assert ws["rows"][0][0][2]["bold"]
    assert all(8 <= w <= 51 for w in ws["widths"])


def test_numbers_are_numbers(project):
    setup(project, ROWS, ANN, TAX)
    load_step("04_table").main([TAXON])
    s = Book(xlsx(project)).sheet("Samples")["rows"]
    hdr = [s[0][j][1] for j in sorted(s[0])]
    assert s[1][hdr.index("n_runs")][:2] == ("n", 1)
    assert s[1][hdr.index("total_Gb")][0] == "n"


def test_xlsx_cleans_text(tmp_path):
    from organise_sra.xlsx import MAX_CELL, write_xlsx
    path = write_xlsx(tmp_path / "t.xlsx", {"S": (["a", "b"], [{"a": "x\x01y<&>", "b": "z" * (MAX_CELL + 5)}])})
    row = Book(path).values("S")[1]
    assert row[0] == "xy<&>" and len(row[1]) == MAX_CELL


def test_xlsx_opens_in_openpyxl(project):
    openpyxl = pytest.importorskip("openpyxl")          # 可选: 装了 openpyxl 时核对第三方读取器也能打开
    setup(project, ROWS, ANN, TAX)
    load_step("04_table").main([TAXON])
    wb = openpyxl.load_workbook(xlsx(project))
    assert wb.sheetnames == SHEETS and wb["Runs"].freeze_panes == "A2" and wb["Runs"].max_row == 4


def test_sample_attribute_named_like_rank_does_not_hide_lineage(project):
    ann = "Run\tfamily\tspecies\nSRR1\tsubmitter family\tsubmitter species\n"
    d = setup(project, ROWS, ann, TAX)
    load_step("04_table").main([TAXON])
    lines = (d / "table.tsv").read_text(encoding="utf-8").splitlines()
    r1 = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
    assert r1["family"] == "F" and r1["sample_family"] == "submitter family"
