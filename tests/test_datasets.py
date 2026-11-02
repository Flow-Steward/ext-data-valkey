"""Native key Dataset and compatibility regressions."""

import json
import pytest
import main
from test_runtime import envelope, fake

PROVIDER = "valkey"


def request(action="preview_query", resource="strings", operation="get", arguments=None):
    p = envelope(action)
    p["runtime_context"].update(
        account_id=None, project_id=None, step_id=None, step_execution_id=None
    )
    p["action"]["input"] = {
        "connection_config": {"host": "kv.example", "key_prefix": "app:"},
        "secret_refs": {},
        "dataset": {
            "dataset_id": "d",
            "resource_scope_jsonb": {"resources": [{"resource_id": resource}]},
        },
        "query_template": {
            "query_template_id": "q",
            "dataset_id": "d",
            "query_language": "json",
            "query_definition_jsonb": {
                "resource_id": resource,
                "operation": operation,
                "arguments": arguments or {"key": "app:item"},
            },
        },
    }
    return p


def direct(action="get", args=None):
    p = envelope(action, args, kind=PROVIDER + "_database")
    p["action"]["target"]["connection"]["config"].pop("namespace")
    return p


def test_direct_same_key_read_write(monkeypatch):
    calls = fake(monkeypatch, "OK")
    assert main.handle(direct("set", {"value": "x"}))["ok"]
    assert main.handle(direct())["ok"]
    assert [c[3]["key"] for c in calls] == ["item", "item"]


@pytest.mark.parametrize("value", [{"x": 1}, [1, False], False, 0, None])
def test_json_roundtrip_and_missing(monkeypatch, value):
    calls = fake(monkeypatch, "OK")
    assert main.handle(direct("json_set", {"value": value}))["ok"]
    assert json.loads(calls[0][3]["value"]) == value
    fake(monkeypatch, json.dumps(value))
    result = main.handle(direct("json_get"))["result"]
    assert result["value"] == value and result["found"]
    fake(monkeypatch, None)
    assert not main.handle(direct("json_get"))["result"]["found"]


def test_dataset_admin_direct_read(monkeypatch):
    calls = fake(monkeypatch, "value")
    r = main.handle(request())
    assert r["ok"], r
    assert r["result"]["rows"] == [{"key": "app:item", "value": "value"}]
    assert calls[0][3]["key"] == "app:item"


@pytest.mark.parametrize(
    "arguments", [{"key": "other"}, {"key": "APP:item"}, {"key": "app:item", "command": "DEL"}]
)
def test_prefix_unknown_arguments(monkeypatch, arguments):
    calls = fake(monkeypatch, "v")
    assert not main.handle(request(arguments=arguments))["ok"]
    assert not calls


def test_dataset_json_null_distinct_missing(monkeypatch):
    fake(monkeypatch, "null")
    assert main.handle(request(resource="json"))["result"]["rows"] == [
        {"key": "app:item", "value": None}
    ]
    fake(monkeypatch, None)
    assert main.handle(request(resource="json"))["result"]["rows"] == []


def test_ping_contacts_server(monkeypatch):
    calls = fake(monkeypatch, "PONG")
    assert main.handle(request("test_connection"))["ok"]
    assert calls[0][2] == ["PING"]


@pytest.mark.parametrize("operation", ["set", "scan", "eval", "delete"])
def test_dataset_writes_denied(monkeypatch, operation):
    calls = fake(monkeypatch, "OK")
    assert not main.handle(request(operation=operation))["ok"]
    assert not calls


def test_workflow_project_still_required(monkeypatch):
    calls = fake(monkeypatch, "v")
    p = direct()
    p["runtime_context"]["project_id"] = None
    assert not main.handle(p)["ok"] and not calls


def test_column_scope(monkeypatch):
    fake(monkeypatch, "v")
    p = request()
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = ["key"]
    r = main.handle(p)
    assert r["ok"], r
    assert r["result"]["rows"] == [{"key": "app:item"}]


def test_parameter_binding_precedes_prefix_checks(monkeypatch):
    calls = fake(monkeypatch, "v")
    p = request(arguments={"key": {"$param": "k"}})
    a = p["action"]["input"]
    a["query_template"]["param_schema_jsonb"] = {"properties": {"k": {"type": "string"}}}
    a["params"] = {"k": "other"}
    assert not main.handle(p)["ok"] and not calls
    a["params"]["k"] = "app:exact"
    assert main.handle(p)["ok"]
    assert calls[0][3]["key"] == "app:exact"


def test_missing_validation_samples_are_explicit(monkeypatch):
    calls = fake(monkeypatch, "v")
    p = request("validate_query", arguments={"key": {"$param": "k"}})
    p["action"]["input"]["query_template"]["param_schema_jsonb"] = {
        "properties": {"k": {"type": "string"}}
    }
    r = main.handle(p)
    assert r["ok"], r
    assert not r["result"]["validation_complete"] and not calls


def test_batch_all_keys_checked(monkeypatch):
    calls = fake(monkeypatch, ["v", "v"])
    p = request(operation="mget", arguments={"keys": ["app:one", "other"]})
    assert not main.handle(p)["ok"] and not calls
    p["action"]["input"]["query_template"]["query_definition_jsonb"]["arguments"]["keys"] = [
        "app:one",
        "app:two",
    ]
    assert main.handle(p)["ok"]
    assert calls[0][2] == ["MGET", "$keys"]


@pytest.mark.parametrize(
    "resource,operation,args,value,rows",
    [
        ("hashes", "hget", {"key": "app:h", "field": "a"}, "v", [{"field": "a", "value": "v"}]),
        ("hashes", "hgetall", {"key": "app:h"}, ["a", "v"], [{"field": "a", "value": "v"}]),
        (
            "lists",
            "lrange",
            {"key": "app:l", "start": 2, "count": 1},
            ["v"],
            [{"index": 2, "value": "v"}],
        ),
    ],
)
def test_logical_views(monkeypatch, resource, operation, args, value, rows):
    fake(monkeypatch, value)
    r = main.handle(request(resource=resource, operation=operation, arguments=args))
    assert r["ok"], r
    assert r["result"]["rows"] == rows


def test_json_writes_retain_claim_and_test_guards(monkeypatch):
    calls = fake(monkeypatch, "OK")
    p = direct("json_set", {"value": None})
    p["runtime_context"]["external_effect_id"] = None
    assert main.handle(p)["error_code"] == "effect_claim_required"
    p["runtime_context"]["test_mode"] = True
    assert main.handle(p)["error_code"] == "test_mode_suppressed"
    assert not calls


def test_response_budget(monkeypatch):
    fake(monkeypatch, "v" * 2000)
    p = request()
    p["action"]["input"]["limits"] = {"max_bytes": 1024}
    assert not main.handle(p)["ok"]


def test_deadline_checked_after_transport(monkeypatch):
    import datasets

    times = iter([0, 0, 31])
    monkeypatch.setattr(datasets.time, "monotonic", lambda: next(times))
    fake(monkeypatch, "v")
    assert main.handle(request())["error_code"] == "timeout"


def test_static_introspection_honours_scopes(monkeypatch):
    calls = fake(monkeypatch, "unused")
    p = request("introspect_schema", resource="json")
    p["action"]["input"]["dataset"]["resource_scope_jsonb"]["resources"][0]["columns"] = ["value"]
    r = main.handle(p)
    assert r["ok"], r
    resources = r["result"]["resources"]
    assert len(resources) == 1 and resources[0]["columns"][0]["output_schema"] == {}
    assert not calls


def test_legacy_key_hash_unchanged_after_credentials_edit(monkeypatch):
    import hashlib

    calls = fake(monkeypatch, "v")
    p = envelope("get")
    assert main.handle(p)["ok"]
    p["action"]["target"]["connection"]["secrets"]["password"] = "edited"
    assert main.handle(p)["ok"]
    scope = hashlib.sha256(
        json.dumps(["a", "p", "conn"], separators=(",", ":")).encode()
    ).hexdigest()[:32]
    assert [c[3]["key"] for c in calls] == ["workflows:" + scope + ":item"] * 2


@pytest.mark.parametrize(
    "action,budget_field",
    [("preview_query", "max_preview_rows"), ("execute_template", "max_runtime_rows")],
)
@pytest.mark.parametrize(
    "list_length,start,count,budget,expected_count,truncated",
    [
        (30, 0, 30, 20, 20, True),
        (30, 0, 30, 7, 7, True),
        (20, 0, 30, 20, 20, False),
        (5, 0, 30, 20, 5, False),
        (30, 0, 10, 20, 10, False),
        (200, 0, 100, 100, 100, False),
        (200, 0, 100, 99, 99, True),
        (30, 5, 30, 20, 20, True),
        (0, 0, 30, 20, 0, False),
    ],
)
def test_list_completeness_respects_requested_range_and_budget(
    monkeypatch, action, budget_field, list_length, start, count, budget, expected_count, truncated
):
    items = [str(i) for i in range(list_length)]
    calls = []

    def native_lrange(config, secrets, command, arguments, *, timeout_seconds):
        assert command == ["LRANGE", "$key", "$start", "$end"]
        assert arguments["key"] == "app:list"
        first, last = int(arguments["start"]), int(arguments["end"])
        assert 0 < last - first + 1 <= 100
        calls.append((first, last))
        # Redis LRANGE has an inclusive end and naturally shortens at list end.
        return items[first : last + 1]

    monkeypatch.setattr(main.toolbox, "invoke", native_lrange)
    p = request(action, "lists", "lrange", {"key": "app:list", "start": start, "count": count})
    p["action"]["input"]["limits"] = {budget_field: budget}
    r = main.handle(p)
    assert r["ok"], r
    result = r["result"]
    assert result["truncated"] is truncated
    assert result["row_count"] == expected_count
    assert result["rows"] == [
        {"index": i, "value": str(i)} for i in range(start, start + expected_count)
    ]
    assert len(calls) == 1
