import csv
import io
import json

from scraper.exports import rows_to_csv, rows_to_json


def parse(text):
    return list(csv.reader(io.StringIO(text)))


def test_csv_header_is_the_union_of_keys_in_first_seen_order_and_gaps_are_empty():
    rows = [{"name": "A", "price": 1}, {"name": "B", "url": "https://x.test"}, {"price": 3, "name": "C"}]
    assert parse(rows_to_csv(rows)) == [
        ["name", "price", "url"],
        ["A", "1", ""],
        ["B", "", "https://x.test"],
        ["C", "3", ""],
    ]


def test_csv_round_trips_commas_quotes_newlines_and_unicode():
    # Review Focus 6
    rows = [{"t": 'say "hi", then\nleave', "u": "Café ☃"}]
    assert parse(rows_to_csv(rows)) == [["t", "u"], ['say "hi", then\nleave', "Café ☃"]]


def test_csv_stringifies_none_and_nested_values():
    rows = [{"a": None, "b": {"k": [1, 2]}, "c": True}]
    assert parse(rows_to_csv(rows)) == [["a", "b", "c"], ["", '{"k": [1, 2]}', "True"]]


def test_csv_neutralises_spreadsheet_formulas_but_keeps_numbers():
    # Review Focus 6
    rows = [{"a": "=HYPERLINK(\"http://evil.test\")", "b": "+1+1", "c": "@SUM(A1)", "d": "-5", "e": "-dash-text", "f": "plain"}]
    header, row = parse(rows_to_csv(rows))
    assert row == ["'=HYPERLINK(\"http://evil.test\")", "'+1+1", "'@SUM(A1)", "-5", "'-dash-text", "plain"]


def test_csv_of_no_rows_is_empty():
    assert rows_to_csv([]) == ""


def test_json_is_a_list_of_objects_and_keeps_unicode():
    rows = [{"name": "Café"}, {"name": "B", "n": 2}]
    text = rows_to_json(rows)
    assert "Café" in text
    assert json.loads(text) == rows
