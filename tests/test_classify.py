from organise_sra.classify import classify_runs, load_rules, summarize_samples, summarize_species

R = load_rules()


def run(run="SRR1", bs="SAMN1", strategy="WGS", sel="RANDOM", src="GENOMIC", plat="ILLUMINA", gb=1.0, **kw):
    return {"Run": run, "BioSample": bs, "LibraryStrategy": strategy, "LibrarySelection": sel,
            "LibrarySource": src, "Platform": plat, "bases": str(int(gb * 1e9)), "LibraryLayout": "PAIRED",
            "TaxID": kw.get("taxid", "1"), "ScientificName": kw.get("name", "A b"), "BioProject": kw.get("bp", "P1")}


def cat(*rows):
    return [r["category_derived"] for r in classify_runs(list(rows), R)]


def col(rows, name):
    return [r[name] for r in rows]


def test_pcr_selection_classified_by_volume():
    # Annelida 2026-10-27: WGS+PCR are mostly genome skimming / genome data (PRJNA1075478, PRJNA551036)
    df = classify_runs([run(sel="PCR", gb=1.84), run(run="R2", bs="S2", sel="PCR", gb=0.08)], R)
    assert col(df, "category_derived") == ["shallow_WGS", "low_data_WGS"]
    assert "PCR" in df[0]["category_rule_derived"]


def test_rule_metagenomic():
    assert cat(run(src="METAGENOMIC", gb=5)) == ["WGS_metagenomic"]


def test_rule_long():
    assert cat(run(plat="PACBIO_SMRT", gb=30), run(run="SRR2", bs="S2", plat="OXFORD_NANOPORE")) == \
        ["long_read_WGS", "long_read_WGS"]


def test_thresholds():
    rows = [run(run=f"R{i}", bs=f"S{i}", gb=g) for i, g in enumerate([0.49, 0.5, 9.99, 10.0])]
    assert cat(*rows) == ["low_data_WGS", "shallow_WGS", "shallow_WGS", "deep_WGS"]


def test_sample_sum_includes_pcr_runs_excludes_metagenomic():
    df = classify_runs([run(run="R1", gb=3), run(run="R2", sel="PCR", gb=20),
                                     run(run="R3", src="METAGENOMIC", gb=50)], R)
    assert col(df, "category_derived") == ["deep_WGS", "deep_WGS", "WGS_metagenomic"]
    assert df[0]["sample_total_Gb_derived"] == 23.0


def test_runs_of_one_sample_are_summed():
    df = classify_runs([run(run="R1", gb=6), run(run="R2", gb=6)], R)
    assert set(col(df, "category_derived")) == {"deep_WGS"}


def test_empty_biosample_uses_run():
    df = classify_runs([run(run="R1", bs=""), run(run="R2", bs="")], R)
    assert col(df, "sample_key_derived") == ["R1", "R2"]


def test_rnaseq_raw_and_long():
    assert cat(run(strategy="RNA-Seq", sel="cDNA", src="TRANSCRIPTOMIC"),
               run(run="R2", bs="S2", strategy="RNA-Seq", plat="PACBIO_SMRT"),
               run(run="R3", bs="S3", strategy="Hi-C")) == ["RNA-Seq", "long_read_RNA", "Hi-C"]


def test_rule_text():
    df = classify_runs([run(gb=1.84)], R)
    assert "1.84 Gb in [0.5,10) -> shallow_WGS" in df[0]["category_rule_derived"]


def test_case_insensitive():
    assert cat(run(strategy="wgs", src="metagenomic")) == ["WGS_metagenomic"]


def test_summaries():
    df = classify_runs([run(run="R1", gb=1), run(run="R2", gb=1),
                                     run(run="R3", bs="S3", strategy="RNA-Seq", gb=4)], R)
    cols, s = summarize_samples(df, list(df[0]))
    assert len(s) == 2
    wgs = [r for r in s if r["category_derived"] == "shallow_WGS"][0]
    assert wgs["n_runs"] == 2 and wgs["total_Gb"] == 2.0 and wgs["runs"] == "R1;R2"
    tax = [{"TaxID": "1", "rank_derived": "species", "phylum": "Annelida", "class": "", "order": "",
            "family": "F", "genus": "A", "species": "A b"}]
    sp_cols, sp = summarize_species(s, list(tax[0]), tax)
    assert len(sp) == 1
    row = sp[0]
    assert row["family"] == "F" and row["shallow_WGS_samples"] == 1 and row["RNA-Seq_Gb"] == 4.0
    assert sp_cols[:3] == ["TaxID", "ScientificName", "rank_derived"]


def test_input_rows_not_modified():
    rows = [run()]
    classify_runs(rows, R)
    assert "category_derived" not in rows[0]


def test_bioproject_derived_backfilled_from_xml():
    a = dict(run(bp=""), bioproject="PRJDB35883")
    b = run(run="R2", bs="S2", bp="P9")
    out = classify_runs([a, b, run(run="R3", bs="S3", bp="")], R)
    assert col(out, "BioProject_derived") == ["PRJDB35883", "P9", ""]
    assert col(out, "BioProject") == ["", "P9", ""]                    # 原列不改


def test_samples_use_backfilled_bioproject():
    rows = classify_runs([dict(run(bp=""), bioproject="PRJDB1")], R)
    cols, samples = summarize_samples(rows, list(rows[0]))
    assert samples[0]["BioProject"] == "PRJDB1"
