"""Bounded logical key views over normal database keys; never scan or write."""

from __future__ import annotations
import copy
import json
import time
from typing import Any
from validation import Failure, bounded_json, connection_values, integer, require, text

PROVIDER = "valkey"
ACTIONS = frozenset(
    {
        "test_connection",
        "introspect_schema",
        "validate_query",
        "preview_query",
        "execute_template",
        "infer_output_schema",
    }
)
TYPES = {"string", "integer", "number", "boolean", "null", "object", "array"}
VIEWS = {
    "strings": {"key": "string", "value": "string"},
    "hashes": {"field": "string", "value": "string"},
    "lists": {"index": "integer", "value": "string"},
    "json": {"key": "string", "value": "json"},
}


def catalog():
    return [
        {
            "connector_id": PROVIDER,
            "connector_type_id": PROVIDER + "_database",
            "database_kind": PROVIDER,
            "source_kind": PROVIDER,
            "display_name": PROVIDER.title(),
            "source_extension_id": "flowsteward." + PROVIDER,
            "source_extension_name": PROVIDER.title(),
            "source_provider": PROVIDER.title(),
            "query_language": "json",
            "access_mode": "read_only",
        }
    ]


def bounded(value):
    bounded_json(value, 65536)
    nodes = 0

    def walk(v, depth):
        nonlocal nodes
        nodes += 1
        require(nodes <= 10000 and depth <= 20, "Dataset JSON exceeds depth or node budget.")
        if isinstance(v, dict):
            for k, c in v.items():
                require(isinstance(k, str), "JSON object keys must be strings.")
                walk(c, depth + 1)
        elif isinstance(v, list):
            for c in v:
                walk(c, depth + 1)

    walk(value, 0)


def kind(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def bind(arguments, template, params, execution):
    schema = template.get("param_schema_jsonb", {})
    require(
        isinstance(schema, dict) and isinstance(schema.get("properties", {}), dict),
        "Invalid parameter schema.",
    )
    declarations = schema.get("properties", {})
    require(isinstance(params, dict), "Parameters must be an object.")
    names = set()
    missing = {}

    def visit(v):
        if isinstance(v, dict):
            if "$param" in v:
                require(
                    set(v) == {"$param"} and isinstance(v["$param"], str),
                    "Use single-member parameter markers.",
                )
                name = v["$param"]
                names.add(name)
                require(
                    name in declarations and isinstance(declarations[name], dict),
                    "Undeclared parameter.",
                )
                types = declarations[name].get("type")
                types = types if isinstance(types, list) else [types]
                require(bool(types) and all(t in TYPES for t in types), "Invalid parameter types.")
                if name not in params:
                    require(not execution, "Missing query parameter.")
                    missing[name] = set(types)
                    return dict(v)
                value = params[name]
                require(
                    kind(value) in types or kind(value) == "integer" and "number" in types,
                    "Parameter type mismatch.",
                )
                bounded(value)
                return copy.deepcopy(value)
            return {k: visit(c) for k, c in v.items()}
        if isinstance(v, list):
            return [visit(c) for c in v]
        return v

    value = visit(arguments)
    require(set(params) <= names, "Unknown query parameter.")
    bounded(value)
    return value, missing


def scope(dataset):
    require(isinstance(dataset, dict), "Dataset required.")
    raw = dataset.get("resource_scope_jsonb", {}).get("resources")
    require(isinstance(raw, list) and 1 <= len(raw) <= 4, "Select logical resources.")
    result = {}
    for item in raw:
        require(isinstance(item, dict), "Invalid resource scope.")
        resource = item.get("resource_id")
        require(
            isinstance(resource, str) and resource in VIEWS and resource not in result,
            "Invalid resource.",
        )
        columns = item.get("columns")
        require(
            columns is None
            or isinstance(columns, list)
            and all(isinstance(c, str) and c in VIEWS[resource] for c in columns),
            "Invalid column scope.",
        )
        result[resource] = list(VIEWS[resource]) if columns is None else columns
    return result


def compile_query(args, execution, limit, config):
    dataset = args.get("dataset")
    allowed = scope(dataset)
    t = args.get("query_template")
    require(
        isinstance(t, dict)
        and t.get("query_language") == "json"
        and isinstance(t.get("query_template_id"), str)
        and bool(t["query_template_id"]),
        "Saved JSON query required.",
    )
    require(
        isinstance(dataset.get("dataset_id"), str)
        and bool(dataset["dataset_id"])
        and t.get("dataset_id") == dataset["dataset_id"],
        "Query belongs to another dataset.",
    )
    d = t.get("query_definition_jsonb")
    bounded(d)
    require(
        isinstance(d, dict) and set(d) == {"resource_id", "operation", "arguments"},
        "Invalid query definition.",
    )
    resource = d["resource_id"]
    require(isinstance(resource, str) and resource in allowed, "Resource outside scope.")
    op = d["operation"]
    require(
        isinstance(op, str)
        and op
        in {
            "strings": {"get", "mget"},
            "json": {"get", "mget"},
            "hashes": {"hget", "hgetall"},
            "lists": {"lrange"},
        }[resource],
        "Unsupported read operation.",
    )
    require(isinstance(d["arguments"], dict), "Arguments must be an object.")
    a, missing = bind(d["arguments"], t, args.get("params", {}), execution)
    fields = {
        "get": {"key"},
        "mget": {"keys"},
        "hget": {"key", "field"},
        "hgetall": {"key"},
        "lrange": {"key", "start", "count"},
    }[op]
    require(
        set(a) <= fields and ("keys" if op == "mget" else "key") in a,
        "Unknown or missing arguments.",
    )

    def unresolved(v, types):
        if isinstance(v, dict) and set(v) == {"$param"} and v["$param"] in missing:
            require(bool(missing[v["$param"]] & types), "Parameter type cannot satisfy position.")
            return True
        return False

    def key(v):
        if unresolved(v, {"string"}):
            return ""
        v = text(v, "key", 256, nonempty=True)
        require(v.startswith(config["key_prefix"]), "Key outside prefix.")
        return v

    params: dict[str, Any] = {}
    if op == "mget":
        keys = a["keys"]
        if unresolved(keys, {"array"}):
            keys = []
        else:
            require(isinstance(keys, list) and 1 <= len(keys) <= limit, "Bounded keys required.")
        params["keys"] = [key(k) for k in keys]
        command = ["MGET", "$keys"]
    else:
        params["key"] = key(a["key"])
        command = [
            {"get": "GET", "hget": "HGET", "hgetall": "HGETALL", "lrange": "LRANGE"}[op],
            "$key",
        ]
        if op == "hget":
            v = a.get("field")
            params["field"] = (
                "" if unresolved(v, {"string"}) else text(v, "field", 256, nonempty=True)
            )
            command.append("$field")
        if op == "lrange":
            start = a.get("start", 0)
            count = a.get("count", limit)
            start = 0 if unresolved(start, {"integer"}) else integer(start, "start", 0, 2147483647)
            count = limit if unresolved(count, {"integer"}) else integer(count, "count", 1, 100)
            params.update(start=str(start), end=str(start + min(count, limit + 1) - 1))
            command += ["$start", "$end"]
    return command, params, resource, allowed[resource], sorted(missing)


def handle(name, args, invoke):
    require(
        set(args)
        <= {"connection_config", "secret_refs", "dataset", "query_template", "params", "limits"},
        "Unknown Dataset input.",
    )
    refs = args.get("secret_refs", {})
    require(isinstance(refs, dict), "Invalid credentials.")
    config, secrets = connection_values(
        {
            "connection_type": PROVIDER + "_database",
            "config": args.get("connection_config"),
            "secrets": refs.get("values", refs.get("refs", refs)),
        }
    )
    limits = args.get("limits", {})
    require(
        isinstance(limits, dict)
        and set(limits) <= {"max_preview_rows", "max_runtime_rows", "timeout_seconds", "max_bytes"},
        "Invalid limits.",
    )
    preview = integer(limits.get("max_preview_rows", 20), "preview", 1, 100)
    runtime = integer(limits.get("max_runtime_rows", 100), "runtime", 1, 100)
    budget = integer(limits.get("max_bytes", 400 * 1024), "bytes", 1024, 400 * 1024)
    timeout = integer(
        limits.get("timeout_seconds", config["request_timeout_seconds"]), "timeout", 1, 30
    )
    deadline = time.monotonic() + min(timeout, config["request_timeout_seconds"])

    def call(command, params):
        left = deadline - time.monotonic()
        require(left > 0, "Deadline exceeded.", code="timeout")
        value = invoke(config, secrets, command, params, timeout_seconds=left)
        require(time.monotonic() < deadline, "Deadline exceeded.", code="timeout")
        bounded_json(value, budget)
        return value

    if name == "test_connection":
        require(
            call(["PING"], {}) == "PONG", "Invalid PING response.", code="invalid_provider_response"
        )
        return {"status": "ok", "connector_id": PROVIDER}
    if name == "introspect_schema":
        allowed = (
            scope(args["dataset"]) if "dataset" in args else {r: list(c) for r, c in VIEWS.items()}
        )
        resources = [
            {
                "resource_id": r,
                "name": r,
                "resource_type": "logical_view",
                "selectable": True,
                "columns": [
                    {
                        "name": c,
                        "type": VIEWS[r][c],
                        "data_type": VIEWS[r][c],
                        "value_type": VIEWS[r][c],
                        "nullable": r == "json" and c == "value",
                        "output_schema": {} if VIEWS[r][c] == "json" else {"type": VIEWS[r][c]},
                    }
                    for c in cols
                ],
                "indexes": [],
                "metadata": {"static": True, "scans_keys": False},
            }
            for r, cols in allowed.items()
        ]
        result = {
            "schema_snapshot": {
                "schema_version": "dataset_schema_snapshot.v1",
                "source_kind": PROVIDER,
                "resources": resources,
                "truncated": False,
                "metadata": {"static": True, "connector_id": PROVIDER},
            },
            "resources": resources,
            "resource_count": len(resources),
            "truncated": False,
        }
        bounded_json(result, budget)
        return result
    limit = runtime if name == "execute_template" else preview
    command, params, resource, columns, missing = compile_query(
        args, name != "validate_query", limit, config
    )
    if name == "validate_query":
        result = {
            "valid": True,
            "read_only": True,
            "validation_complete": not missing,
            "referenced_resources": [resource],
        }
        if missing:
            result.update(
                validation_status="sample_parameters_required", required_sample_params=missing
            )
        return result
    value = call(command, params)
    op = command[0]
    rows = []
    if op in {"GET", "MGET"}:
        values = [value] if op == "GET" else value
        keys = [params["key"]] if op == "GET" else params["keys"]
        require(
            isinstance(values, list) and len(values) == len(keys),
            "Invalid key results.",
            code="invalid_provider_response",
        )
        for k, v in zip(keys, values, strict=True):
            require(
                v is None or isinstance(v, str),
                "Invalid string result.",
                code="invalid_provider_response",
            )
            if v is not None:
                if resource == "json":
                    try:
                        v = json.loads(
                            v, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
                        )
                    except (ValueError, TypeError) as exc:
                        raise Failure(
                            "invalid_provider_response", "Stored value is not JSON."
                        ) from exc
                rows.append({"key": k, "value": v})
    elif op == "HGET":
        require(
            value is None or isinstance(value, str),
            "Invalid field result.",
            code="invalid_provider_response",
        )
        if value is not None:
            rows = [{"field": params["field"], "value": value}]
    elif op == "HGETALL":
        if isinstance(value, list):
            require(len(value) % 2 == 0, "Invalid hash result.", code="invalid_provider_response")
            value = dict(zip(value[::2], value[1::2], strict=True))
        require(
            isinstance(value, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()),
            "Invalid hash result.",
            code="invalid_provider_response",
        )
        rows = [{"field": k, "value": v} for k, v in value.items()]
    else:
        require(
            isinstance(value, list) and all(isinstance(v, str) for v in value),
            "Invalid list result.",
            code="invalid_provider_response",
        )
        rows = [{"index": int(params["start"]) + i, "value": v} for i, v in enumerate(value)]
    require(len(rows) <= 100, "Provider exceeded row budget.", code="response_too_large")
    truncated = len(rows) > limit
    rows = [{k: v for k, v in row.items() if k in columns} for row in rows[:limit]]
    schema = {
        "type": "object",
        "properties": {
            c: ({} if VIEWS[resource][c] == "json" else {"type": VIEWS[resource][c]})
            for c in columns
        },
    }
    result = (
        {"output_schema": schema}
        if name == "infer_output_schema"
        else {"rows": rows, "row_count": len(rows), "output_schema": schema, "truncated": truncated}
    )
    bounded_json(result, budget)
    return result
