from conftest import FIX, TAXON, fake_eutils, load_step

XML = (FIX / "taxonomy.xml").read_text(encoding="utf-8")


def test_norm_taxid():
    from organise_sra.parse import norm_taxid
    assert norm_taxid("46593.0") == "46593"
    assert norm_taxid(" 46593 ") == "46593"
    assert norm_taxid("") == "" and norm_taxid("nan") == ""


def test_parse_taxonomy_ranks():
    from organise_sra.parse import parse_taxonomy_xml
    rows = {r["TaxID"]: r for r in parse_taxonomy_xml(XML)}
    sp = rows["3682672"]
    assert (sp["rank_derived"], sp["phylum"], sp["class"], sp["order"], sp["family"], sp["genus"], sp["species"]) == \
        ("species", "Annelida", "Polychaeta", "Phyllodocida", "Polynoidae", "Alentiana", "Alentiana sp. T76756-1")
    g = rows["46594"]
    assert g["rank_derived"] == "genus" and g["genus"] == "Harmothoe" and g["species"] == ""


def test_merged_taxid_maps_back():
    from organise_sra.parse import parse_taxonomy_xml
    xml = XML.replace("<TaxId>46594</TaxId>", "<TaxId>46594</TaxId><AkaTaxIds><TaxId>999999</TaxId></AkaTaxIds>", 1)
    rows = {r["TaxID"]: r for r in parse_taxonomy_xml(xml)}
    assert rows["999999"]["genus"] == "Harmothoe"


def test_step03_dedup_ids(project):
    d = project
    d.mkdir(parents=True)
    (d / "runinfo.tsv").write_text("Run\tTaxID\nSRR1\t3682672\nSRR2\t3682672.0\nSRR3\t46594\nSRR4\t5\n")
    eu, t = fake_eutils([("efetch", XML)])
    assert load_step("03_taxonomy").main([TAXON], eutils=eu) == 0
    assert len(t.urls) == 1 and "id=3682672%2C46594%2C5" in t.urls[0]
    lines = (d / "taxonomy.tsv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert (d / "taxonomy_missing.txt").read_text().split() == ["5"]
