import csv
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from library_search import (analyze, build_index, expand_query, llm_reranker, load_inventory, load_synonyms,
                            normalize_isbn, normalize_text)
from library_search.app import create_app
from library_search.cli import main
from library_search.core import Hit, parse_copies

HEADER = ["title", "author", "category", "description", "isbn", "copies_available", "shelf"]
ROWS = [
    ["مُقَدِّمَةُ ابْنِ خَلْدُون", "ابن خلدون", "تاريخ", "قيام الدول وسقوطها والعصبية والعمران", "978-0-306-40615-7", "2", "A1"],
    ["ألف ليلة وليلة", "مجهول", "أدب", "حكايات شهرزاد والسندباد", "", "3", "A2"],
    ["كتاب الحيوان", "الجاحظ", "علوم", "طباع الحيوان وسلوكه", "9990000000028", "0", "A3"],
    ["Self-Help", "Samuel Smiles", "Self-help", "Perseverance and hard work", "9990000000011", "1", "B1"],
    ["The Time Machine", "H. G. Wells", "Science fiction", "An inventor travels to the far future", "9990000000035", "2", "B2"],
    ["The Muqaddimah", "Ibn Khaldun", "History", "Rise and fall of dynasties", "9990000000042", "1", "B3"],
]
SYNONYMS = {"groups": [["تنمية ذاتية", "self-help"], ["حيوان", "animals"], ["ibn sina", "avicenna"]]}


def write_csv(path, rows, header=HEADER):
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def files(tmp_path):
    synonyms = tmp_path / "synonyms.json"
    synonyms.write_text(json.dumps(SYNONYMS, ensure_ascii=False), encoding="utf-8")
    return write_csv(tmp_path / "inventory.csv", ROWS), synonyms


@pytest.fixture
def index(files):
    return build_index(*files)


def titles(result):
    return [h.book.title for h in result.hits]


@pytest.mark.parametrize("raw, expected", [
    ("كِتَابٌ", "كتاب"),                      # tashkeel
    ("كتـــاب", "كتاب"),                      # tatweel
    ("أحمد إبراهيم آمال ٱلله", "احمد ابراهيم امال الله"),  # alef forms
    ("مستشفى", "مستشفي"),                     # alef maqsura -> ya
    ("مكتبة", "مكتبه"),                       # ta marbuta -> ha
    ("‏مكتبة‏", "مكتبه"),            # RLM marks from Excel exports
    ("  Self\t HELP \n", "self help"),        # whitespace + case
    ("رف ١٢", "رف 12"),                       # Arabic-Indic digits
    ("مك؜تبة", "مكتبه"),                  # Arabic letter mark inside a word
    ("Frank­enstein", "frankenstein"),    # soft hyphen
])
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_analyzer_strips_articles_and_stopwords_and_keeps_fields_apart():
    assert analyze("التاريخ الإسلامي") == ["تاريخ", "اسلامي", "تاريخ اسلامي"]
    assert analyze("The history of books") == ["history"]
    assert "ابن خلدون" not in analyze("ابن\nخلدون")
    assert analyze("الكتاب") == []                 # stopword after the article is stripped
    assert analyze("والدة الطفل") == ["والده", "طفل", "والده طفل"]  # وال is not stripped down to two letters


def test_normalize_isbn():
    assert normalize_isbn("978-0-306-40615-7") == "9780306406157"
    assert normalize_isbn("0-306-40615-2") == "9780306406157"  # ISBN-10 converted
    assert normalize_isbn("9780306406157.0") == "9780306406157"
    assert normalize_isbn("ISBN-13: 978-0-306-40615-7") == "9780306406157"  # the label's 13 is not a digit
    assert normalize_isbn("ISBN 0-306-40615-2") == "9780306406157"
    assert normalize_isbn("N/A") == ""


@pytest.mark.parametrize("raw, expected", [
    ("3", 3), ("3.0", 3), ("٣", 3), ("1,200", 1200), ("2 نسخ", 2), ("-1", 0), ("", 0), ("inf", 0), ("9" * 5000, 0),
])
def test_parse_copies(raw, expected):
    assert parse_copies(raw) == expected


def test_availability_column_beats_total_copies_column(tmp_path):
    rows = [["Walden", "3", "0"]]
    book = load_inventory(write_csv(tmp_path / "two.csv", rows, header=["title", "copies", "available"])).books[0]
    assert book.copies_available == 0  # "copies" is what the library owns, "available" is what is on the shelf


def test_windows_arabic_csv_encoding(tmp_path):
    path = tmp_path / "cp1256.csv"
    path.write_bytes("title,copies_available\nمقدمة ابن خلدون,2\n".encode("cp1256"))
    assert load_inventory(path).books[0].title == "مقدمة ابن خلدون"


def test_inventory_without_searchable_words(tmp_path):
    index = build_index(write_csv(tmp_path / "tiny.csv", [["x", "", "", "", "", "1", ""]]))
    assert index.search("x").hits == [] and index.search("history").note == "inventory has no searchable text"


def test_dedupe_by_isbn_or_title_author(tmp_path):
    rows = ROWS + [
        ["Muqaddima (2nd copy)", "", "", "", "0-306-40615-2", "1", "C9"],        # same ISBN as ISBN-10, other shelf
        ["ألف ليلـــة وَلَيْلَة", "مجهول", "", "", "", "3", "A2"],             # same title+author, same shelf
        ["The Time Machine", "H. G. Wells", "", "", "9990000000099", "1", "B2"],  # different ISBN = other edition
        ["", "no title", "", "", "", "5", "Z"],                                   # skipped
    ]
    inventory = load_inventory(write_csv(tmp_path / "dupes.csv", rows))
    assert inventory.rows_read == 10 and inventory.rows_skipped == 1
    assert len(inventory.books) == 7 and len(inventory.merged) == 2
    muqaddima = inventory.books[0]
    assert muqaddima.copies_available == 3 and muqaddima.shelf == "A1 / C9"
    nights = inventory.books[1]
    assert nights.copies_available == 3  # the second row on the same shelf is a double entry, not new copies
    assert [b.title for b in inventory.books].count("The Time Machine") == 2


def test_dedupe_fills_blank_fields_from_later_rows(tmp_path):
    rows = [["Walden", "Thoreau", "", "", "", "1", "E1"], ["walden", "thoreau", "Essays", "Life in a cabin", "", "1", "E1"]]
    book = load_inventory(write_csv(tmp_path / "fill.csv", rows)).books[0]
    assert (book.category, book.description, book.copies_available) == ("Essays", "Life in a cabin", 1)


def test_missing_required_column(tmp_path):
    with pytest.raises(ValueError, match="copies_available"):
        load_inventory(write_csv(tmp_path / "bad.csv", [["x", "y"]], header=["title", "author"]))


def test_excel_with_arabic_headers(tmp_path):
    wb = Workbook()
    wb.active.append(["العنوان", "المؤلف", "التصنيف", "النسخ المتاحة", "الرف", "ردمك"])
    wb.active.append(["البخلاء", "الجاحظ", "أدب", 2, "R1", 9990000000011])
    wb.save(tmp_path / "inv.xlsx")
    book = load_inventory(tmp_path / "inv.xlsx").books[0]
    assert (book.title, book.author, book.copies_available, book.shelf, book.isbn) == ("البخلاء", "الجاحظ", 2, "R1", "9990000000011")


def test_arabic_query_ignores_diacritics_and_alef_forms(index):
    assert titles(index.search("مقدمه إبن خلدون"))[0] == "مُقَدِّمَةُ ابْنِ خَلْدُون"
    assert titles(index.search("الف ليله"))[0] == "ألف ليلة وليلة"


def test_only_in_stock_books_are_returned(index):
    result = index.search("الحيوان")
    assert result.hits == [] and result.hidden_out_of_stock == 1
    assert all(h.book.copies_available > 0 for q in ("ابن خلدون", "history", "time") for h in index.search(q).hits)


def test_cross_language_synonym_hit(index):
    result = index.search("كتب في التنمية الذاتية")
    assert ("تنمية ذاتية", "self-help") in result.expanded
    hit = result.hits[0]
    assert hit.book.title == "Self-Help"
    assert any(w["synonym_of"] == "تنمية ذاتية" and "title" in w["fields"] for w in hit.why)
    assert "via 'تنمية ذاتية'" in hit.why_text


def test_multi_word_synonym_matches_only_as_a_phrase(index):
    assert index.search("avicenna").hits == []  # expands to "ibn sina", which must not match "Ibn Khaldun"
    groups = [["science", "علوم"], ["science fiction", "خيال علمي"]]
    assert expand_query("science fiction", groups) == [("science fiction", "خيال علمي")]
    assert expand_query("war strategy", [["war", "strategy", "military"]]) == [("war", "military")]  # typed words not re-added


def test_why_names_terms_and_fields(index):
    hit = index.search("time machine").hits[0]
    assert hit.why[0] == {"term": "Time Machine", "fields": ["title"], "synonym_of": None}
    assert index.search("Tme Machin").hits[0].book.title == "The Time Machine"  # spelling slip, character n-grams


def test_empty_and_stopword_queries(index):
    empty = index.search("   ")
    assert empty.hits == [] and empty.note == "empty query"
    assert index.search("the of").hits == []


def test_bundled_synonyms_file():
    groups = load_synonyms()
    assert any("تنمية ذاتية" in g and "self-help" in g for g in groups)


def test_rerank_hook_reorders_and_falls_back(index):
    query = "Self-Help Time Machine"
    plain = titles(index.search(query))
    assert len(plain) == 2
    assert titles(index.search(query, rerank=lambda q, hits: hits[::-1])) == plain[::-1]

    def broken(q, hits):
        raise RuntimeError("model offline")

    failed = index.search(query, rerank=broken)
    assert titles(failed) == plain and not failed.reranked and "model offline" in failed.note


def test_rerank_hook_cannot_add_out_of_stock_books(index):
    out_of_stock = next(b for b in index.books if b.copies_available == 0)
    result = index.search("Self-Help Time Machine", rerank=lambda q, hits: [Hit(out_of_stock, 9.0)] + hits + hits)
    assert result.reranked and out_of_stock.title not in titles(result) and len(result.hits) == 2


def test_negative_limit_is_not_a_slice_from_the_end(index):
    assert len(index.search("history", limit=-1).hits) == 1


def test_llm_reranker_uses_client_and_keeps_every_candidate(index):
    class FakeClient:
        def __init__(self):
            self.calls = []

        def complete_json(self, *, system, prompt, schema):
            self.calls.append(prompt)
            return json.dumps({"ids": ["9990000000035", "not-a-candidate"]})

    client = FakeClient()
    result = index.search("Self-Help Time Machine", rerank=llm_reranker(client))
    assert result.reranked and titles(result) == ["The Time Machine", "Self-Help"]
    assert "id=9990000000011" in client.calls[0] and "Self-Help Time Machine" in client.calls[0]


@pytest.fixture
def client(files):
    with TestClient(create_app(*files, notice="SAMPLE <data>")) as test_client:
        yield test_client


def test_api_page_is_rtl_and_escapes_notice(client):
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'dir="rtl"' in page.text and "SAMPLE &lt;data&gt;" in page.text
    assert "innerHTML" not in page.text  # results are inserted with textContent only


def test_api_search_returns_json(client):
    body = client.get("/api/search", params={"q": "مقدمة ابن خلدون", "limit": 1}).json()
    assert body["results"][0]["title"] == "مُقَدِّمَةُ ابْنِ خَلْدُون" and len(body["results"]) == 1
    assert body["results"][0]["why"] and body["results"][0]["shelf"] == "A1"
    assert body["inventory"]["titles"] == 6 and body["inventory"]["in_stock"] == 5


def test_api_empty_query_and_bad_limit(client):
    body = client.get("/api/search", params={"q": ""}).json()
    assert body["results"] == [] and body["note"] == "empty query" and body["inventory"]["titles"] == 6
    assert client.get("/api/search", params={"q": "x", "limit": 0}).status_code == 422
    assert client.get("/api/search", params={"q": "x" * 201}).status_code == 422


def test_api_reload_picks_up_changes_and_survives_bad_file(files, client):
    inventory, _ = files
    write_csv(inventory, ROWS + [["Walden", "Henry David Thoreau", "Essays", "Simple living", "", "1", "E1"]])
    reloaded = client.post("/api/reload")
    assert reloaded.status_code == 200 and reloaded.json()["inventory"]["titles"] == 7
    assert client.get("/api/search", params={"q": "walden"}).json()["results"][0]["title"] == "Walden"

    write_csv(inventory, [["x"]], header=["something else"])
    failed = client.post("/api/reload")
    assert failed.status_code == 422 and "previous inventory kept" in failed.json()["detail"]
    assert client.get("/api/search", params={"q": "walden"}).json()["results"][0]["title"] == "Walden"


def test_api_reload_survives_corrupt_excel(tmp_path, files):
    _, synonyms = files
    workbook = tmp_path / "inv.xlsx"
    wb = Workbook()
    wb.active.append(["title", "copies_available"])
    wb.active.append(["Walden", 1])
    wb.save(workbook)
    with TestClient(create_app(workbook, synonyms)) as test_client:
        workbook.write_bytes(b"not a zip file")
        assert test_client.post("/api/reload").status_code == 422
        assert test_client.get("/api/search", params={"q": "walden"}).json()["results"][0]["title"] == "Walden"


def test_api_reload_key(files):
    with TestClient(create_app(*files, reload_key="k-1")) as secured:
        assert secured.post("/api/reload").status_code == 401
        assert secured.post("/api/reload", headers={"X-Reload-Key": "k-1"}).status_code == 200


def test_cli_search_markdown_and_clean(files, tmp_path, capsys):
    inventory, synonyms = files
    md = tmp_path / "out" / "queries.md"
    assert main(["search", str(inventory), "مقدمة", "الحيوان", "--synonyms", str(synonyms),
                 "--markdown", str(md), "--note", "SAMPLE DATA"]) == 0
    text = md.read_text(encoding="utf-8")
    assert "> SAMPLE DATA" in text and "مُقَدِّمَةُ ابْنِ خَلْدُون" in text and "hidden because no copies" in text
    assert "مقدمة" in capsys.readouterr().out

    cleaned = tmp_path / "clean.csv"
    assert main(["clean", str(inventory), "--out", str(cleaned)]) == 0
    with cleaned.open(encoding="utf-8-sig") as fh:
        assert len(list(csv.DictReader(fh))) == 6


def test_typed_topic_phrase_does_not_match_on_one_of_its_words(tmp_path):
    # "تنمية ذاتية" (self-help) must not pull in an autobiography just because its category "سيرة ذاتية" shares "ذاتية"
    rows = ROWS + [["الأيام", "طه حسين", "سيرة ذاتية", "طفولة في قرية مصرية", "9990000000059", "2", "C1"]]
    synonyms = tmp_path / "synonyms.json"
    synonyms.write_text(json.dumps(SYNONYMS, ensure_ascii=False), encoding="utf-8")
    index = build_index(write_csv(tmp_path / "inv.csv", rows), synonyms)
    titles = [h.book.title for h in index.search("تنمية ذاتية").hits]
    assert "Self-Help" in titles
    assert "الأيام" not in titles
