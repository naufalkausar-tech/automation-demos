from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from llm_extract import (
    AnthropicClient,
    Extractor,
    FakeClient,
    LLMClientError,
    LLMConfigError,
    load_client,
    load_documents,
    parse_json,
    results_to_rows,
    validate,
    write_results,
)
from llm_extract.cli import main as extract_main

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": "string", "minLength": 1},
        "date": {"type": ["string", "null"], "format": "date"},
        "total": {"type": "number", "minimum": 0},
        "currency": {"type": "string", "enum": ["USD", "EUR", "IDR"]},
        "items": {
            "type": "array",
            "items": {"$ref": "#/$defs/line"},
        },
    },
    "required": ["invoice_number", "total", "currency", "items"],
    "additionalProperties": False,
    "$defs": {
        "line": {
            "type": "object",
            "properties": {"sku": {"type": "string"}, "qty": {"type": "integer", "minimum": 1}},
            "required": ["sku", "qty"],
            "additionalProperties": False,
        }
    },
}
GOOD = {"invoice_number": "INV-1", "date": "2024-05-01", "total": 12.5, "currency": "USD", "items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]}


def test_validate_accepts_good_record():
    assert validate(GOOD, INVOICE_SCHEMA) == []


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"total": -1}, "less than minimum"),
        ({"total": "12"}, "expected number"),
        ({"currency": "GBP"}, "is not one of"),
        ({"date": "May 1"}, "not a valid date"),
        ({"extra": 1}, "unexpected property 'extra'"),
        ({"items": [{"sku": "A", "qty": 0}]}, "$.items[0].qty: less than minimum"),
        ({"items": [{"sku": "A", "qty": True}]}, "expected integer, got boolean"),
        ({"invoice_number": ""}, "shorter than 1"),
    ],
)
def test_validate_reports_problems(mutation, fragment):
    errors = validate({**GOOD, **mutation}, INVOICE_SCHEMA)
    assert any(fragment in e for e in errors), errors


def test_validate_required_nullable_anyof_and_patterns():
    record = {k: v for k, v in GOOD.items() if k != "total"}
    assert "missing required property 'total'" in " ".join(validate(record, INVOICE_SCHEMA))
    assert validate({**GOOD, "date": None}, INVOICE_SCHEMA) == []
    shape = {"anyOf": [{"type": "string", "pattern": "^[A-Z]{3}$"}, {"type": "integer"}]}
    assert validate("ABC", shape) == [] and validate(5, shape) == []
    assert validate("abc", shape)
    assert validate("a@b.co", {"type": "string", "format": "email"}) == []
    assert validate([1, 2, 3], {"type": "array", "maxItems": 2})


def test_parse_json_variants():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert parse_json('Here you go: {"a": 3} hope that helps') == {"a": 3}
    assert parse_json("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_extract_success_and_prompt_shape():
    client = FakeClient([GOOD])
    result = Extractor(client, INVOICE_SCHEMA, instructions="Amounts are in the invoice footer.").extract("Invoice INV-1 ...", source="a.txt")
    assert result.ok and result.record == GOOD and result.attempts == 1 and result.source == "a.txt"
    call = client.calls[0]
    assert "Amounts are in the invoice footer." in call["prompt"]
    assert "<document>\nInvoice INV-1 ...\n</document>" in call["prompt"]
    assert '"invoice_number"' in call["prompt"]
    assert "never invent values" in call["system"]
    assert call["schema"] == INVOICE_SCHEMA


def test_retries_with_feedback_until_valid():
    client = FakeClient(["not json at all", {**GOOD, "total": -5}, GOOD])
    result = Extractor(client, INVOICE_SCHEMA, max_attempts=3).extract("doc")
    assert result.ok and result.attempts == 3
    assert "response is not valid JSON" in client.calls[1]["prompt"]
    assert "less than minimum" in client.calls[2]["prompt"]
    assert "Previous answer" in client.calls[2]["prompt"]


def test_gives_up_after_max_attempts():
    client = FakeClient([{**GOOD, "currency": "XXX"}])
    result = Extractor(client, INVOICE_SCHEMA, max_attempts=2).extract("doc", "d1")
    assert not result.ok and result.record is None and result.attempts == 2
    assert any("is not one of" in e for e in result.errors)
    assert len(client.calls) == 2


def test_client_errors_are_recorded_not_retried():
    client = FakeClient([LLMClientError("rate limited")])
    result = Extractor(client, INVOICE_SCHEMA).extract("doc")
    assert not result.ok and result.errors == ["client error: rate limited"] and len(client.calls) == 1


def test_pydantic_models_work_as_schemas():
    class Contact(BaseModel):
        name: str
        age: int = Field(ge=0)

    extractor = Extractor(FakeClient([{"name": "Ana", "age": -1}, {"name": "Ana", "age": "41"}]), Contact)
    assert extractor.schema["properties"]["age"]["minimum"] == 0
    result = extractor.extract("Ana is 41")
    assert result.ok and result.record == {"name": "Ana", "age": 41} and result.attempts == 2


def test_extract_many_keeps_order_with_workers():
    client = FakeClient(handler=lambda prompt: {**GOOD, "invoice_number": prompt.split("<document>\n")[1].split("\n")[0]})
    results = Extractor(client, INVOICE_SCHEMA).extract_many([(f"s{i}", f"INV-{i}") for i in range(6)], workers=3)
    assert [r.source for r in results] == [f"s{i}" for i in range(6)]
    assert [r.record["invoice_number"] for r in results] == [f"INV-{i}" for i in range(6)]


def test_load_documents_all_formats(tmp_path):
    folder = tmp_path / "docs"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("alpha", encoding="utf-8")
    (folder / "sub" / "b.md").write_text("bravo", encoding="utf-8")
    (folder / "skip.png").write_bytes(b"\x89PNG")
    assert load_documents(folder) == [("a.txt", "alpha"), ("sub/b.md", "bravo")]

    (tmp_path / "d.jsonl").write_text('{"id": "x1", "body": "one"}\n\n{"body": "two"}\n', encoding="utf-8")
    assert load_documents(tmp_path / "d.jsonl", text_column="body", id_column="id") == [("x1", "one"), ("line-3", "two")]

    (tmp_path / "d.csv").write_text("ref,text\nr1,hello\n,world\n", encoding="utf-8")
    assert load_documents(tmp_path / "d.csv", id_column="ref") == [("r1", "hello"), ("row-2", "world")]
    with pytest.raises(ValueError):
        load_documents(tmp_path / "d.csv", text_column="missing")

    assert load_documents(folder / "a.txt") == [("a.txt", "alpha")]


def test_results_to_rows_and_writers(tmp_path):
    results = Extractor(FakeClient([GOOD, {"bad": True}]), INVOICE_SCHEMA, max_attempts=1).extract_many([("a", "x"), ("b", "y")])
    rows = results_to_rows(results, explode="items")
    assert len(rows) == 3
    assert rows[0]["items.sku"] == "A" and rows[0]["invoice_number"] == "INV-1" and rows[0]["_ok"] is True
    assert rows[2]["_source"] == "b" and rows[2]["_ok"] is False and "missing required" in rows[2]["_errors"]

    csv_path = write_results(results, tmp_path / "out.csv", explode="items")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 3
    jsonl = write_results(results, tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(jsonl[0])["record"] == GOOD and json.loads(jsonl[1])["ok"] is False
    assert len(json.loads(write_results(results, tmp_path / "out.json").read_text(encoding="utf-8"))) == 2
    with pytest.raises(ValueError):
        write_results(results, tmp_path / "out.xml")


class StubMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def stub_sdk(text='{"invoice_number": "INV-9"}', stop_reason="end_turn", category=None):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        content=[SimpleNamespace(type="fallback"), SimpleNamespace(type="text", text=text)],
    )
    return SimpleNamespace(messages=StubMessages(response), beta=SimpleNamespace(messages=StubMessages(response)))


def test_anthropic_client_request_shape(monkeypatch):
    monkeypatch.delenv("LLM_EXTRACT_MODEL", raising=False)
    monkeypatch.delenv("LLM_EXTRACT_FALLBACKS", raising=False)
    sdk = stub_sdk()
    client = AnthropicClient(sdk_client=sdk)
    assert client.complete_json(system="sys", prompt="doc", schema=INVOICE_SCHEMA) == '{"invoice_number": "INV-9"}'
    kwargs = sdk.beta.messages.calls[0]
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["system"] == "sys" and kwargs["messages"] == [{"role": "user", "content": "doc"}]
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["total"] == {"type": "number", "description": "{minimum: 0}"}
    assert fmt["schema"]["properties"]["date"]["anyOf"] == [{"type": "string", "format": "date"}, {"type": "null"}]
    assert fmt["schema"]["$defs"]["line"]["additionalProperties"] is False
    assert INVOICE_SCHEMA["properties"]["total"]["minimum"] == 0
    assert INVOICE_SCHEMA["properties"]["date"]["type"] == ["string", "null"]
    assert not sdk.messages.calls


def test_anthropic_client_env_overrides_and_no_fallbacks(monkeypatch):
    monkeypatch.setenv("LLM_EXTRACT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("LLM_EXTRACT_FALLBACKS", "0")
    sdk = stub_sdk()
    AnthropicClient(sdk_client=sdk, effort="low").complete_json(system="s", prompt="p", schema={"type": "object"})
    kwargs = sdk.messages.calls[0]
    assert kwargs["model"] == "claude-sonnet-5" and "fallbacks" not in kwargs and "betas" not in kwargs
    assert kwargs["output_config"]["effort"] == "low"


@pytest.mark.parametrize(
    "stop_reason, text, message",
    [("refusal", "", "declined"), ("max_tokens", '{"a"', "max_tokens"), ("end_turn", "   ", "no text")],
)
def test_anthropic_client_bad_responses(stop_reason, text, message):
    client = AnthropicClient(sdk_client=stub_sdk(text=text, stop_reason=stop_reason, category="cyber"), fallbacks=False)
    with pytest.raises(LLMClientError, match=message):
        client.complete_json(system="s", prompt="p", schema={"type": "object"})


def test_load_client(monkeypatch):
    monkeypatch.delenv("LLM_EXTRACT_PROVIDER", raising=False)
    with pytest.raises(LLMConfigError, match="LLM_EXTRACT_PROVIDER"):
        load_client()
    assert isinstance(load_client("fake"), FakeClient)
    assert isinstance(load_client("llm_extract.clients:FakeClient"), FakeClient)
    monkeypatch.setenv("LLM_EXTRACT_PROVIDER", "fake")
    assert isinstance(load_client(), FakeClient)
    with pytest.raises(LLMConfigError):
        load_client("mystery")


def test_cli_with_fake_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LLM_EXTRACT_PROVIDER", raising=False)
    (tmp_path / "schema.json").write_text(json.dumps(INVOICE_SCHEMA), encoding="utf-8")
    (tmp_path / "fake.json").write_text(json.dumps(GOOD), encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "one.txt").write_text("Invoice INV-1", encoding="utf-8")
    (docs / "two.txt").write_text("Invoice INV-2", encoding="utf-8")
    out = tmp_path / "out.csv"
    args = [str(docs), "--schema", str(tmp_path / "schema.json"), "-o", str(out), "--explode", "items"]
    assert extract_main([*args, "--provider", "fake", "--fake-response", str(tmp_path / "fake.json")]) == 0
    assert "extracted 2/2" in capsys.readouterr().out
    with out.open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 4

    assert extract_main(args, client=FakeClient([{"nope": 1}])) == 1
    assert extract_main(args) == 2
    assert "LLM_EXTRACT_PROVIDER" in capsys.readouterr().err


def test_cli_accepts_files_saved_with_utf8_bom(tmp_path):
    (tmp_path / "schema.json").write_text(json.dumps({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}), encoding="utf-8-sig")
    (tmp_path / "fake.json").write_text('{"name": "Ana"}', encoding="utf-8-sig")
    (tmp_path / "doc.txt").write_text("Ana signed.", encoding="utf-8-sig")
    client = FakeClient([{"name": "Ana"}])
    args = [str(tmp_path / "doc.txt"), "--schema", str(tmp_path / "schema.json"), "-o", str(tmp_path / "o.jsonl")]
    assert extract_main([*args, "--provider", "fake", "--fake-response", str(tmp_path / "fake.json")]) == 0
    assert extract_main(args, client=client) == 0
    assert "<document>\nAna signed.\n</document>" in client.calls[0]["prompt"]
