"""Valkey host-v2 workflow entrypoint."""

from __future__ import annotations
import json
import sys
from flowsteward_extension_sdk import runtime_context_from_payload
import datasets
import toolbox
from commands import WRITES, compile_command, normalize_result
from validation import Failure, bounded_json, connection_values, require


def handle(payload):
    write = False
    dispatched = False
    try:
        runtime_context_from_payload(payload)
        action = payload.get("action") or {}
        name = action.get("action_id")
        args = action.get("input") or {}
        require(isinstance(args, dict), "Input must be an object.")
        bounded_json(args)
        context = payload["runtime_context"]
        require(type(context.get("test_mode", False)) is bool, "test_mode must be boolean.")
        if payload.get("mode") == "query":
            name = (payload.get("query") or {}).get("query_id")
            require(name in {"supported_connectors", "runtime_health"}, "Unknown query.")
            result = (
                {"connectors": datasets.catalog()}
                if name == "supported_connectors"
                else {"status": "healthy", "extension_id": "flowsteward.valkey"}
            )
            return {"ok": True, "result": result, "errors": []}
        require(payload.get("mode") == "action", "An action invocation is required.")
        if name in datasets.ACTIONS:
            return {"ok": True, "result": datasets.handle(name, args, toolbox.invoke), "errors": []}
        connection = (action.get("target") or {}).get("connection") or {}
        require(
            isinstance(connection, dict)
            and isinstance(args.get("connection_ref"), str)
            and bool(args["connection_ref"])
            and connection.get("connection_id") == args["connection_ref"]
            and connection.get("connection_type") in {"valkey", "valkey_database"},
            "Select the matching Valkey project connection.",
            code="invalid_connection",
        )
        config, secrets = connection_values(connection)
        command, arguments = compile_command(
            name, args, config, context, connection["connection_id"]
        )
        write = name in WRITES
        if write:
            if context.get("test_mode"):
                raise Failure("test_mode_suppressed", "Valkey writes are suppressed in test mode.")
            claim = context.get("external_effect_id")
            require(
                isinstance(claim, str) and bool(claim.strip()) and len(claim) <= 512,
                "A durable external-effect claim is required.",
                code="effect_claim_required",
            )
        try:
            value = toolbox.invoke(
                config,
                secrets,
                command,
                arguments,
                timeout_seconds=config["request_timeout_seconds"],
            )
            dispatched = True
        except Failure as exc:
            dispatched = exc.dispatched
            raise
        result = normalize_result(name, value, args["key"])
        require(
            len(json.dumps(result, allow_nan=False).encode()) <= 480 * 1024,
            "Provider result exceeds host inline budget.",
            code="response_too_large",
        )
        return {"ok": True, "result": result, "errors": []}
    except (
        Failure,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
    ) as exc:
        code = exc.code if isinstance(exc, Failure) else "invalid_payload"
        message = (
            str(exc)
            if isinstance(exc, Failure)
            else "Invalid bounded host envelope or action input."
        )
        uncertain = write and dispatched
        return {
            "ok": False,
            "error_code": code,
            "error": message,
            "errors": [{"code": code, "message": message}],
            "retryable": False,
            "definitely_no_external_effect": not uncertain,
            "external_effect_status": "timeout_unknown" if uncertain else "not_applied",
        }
    except Exception:
        return {
            "ok": False,
            "error_code": "provider_unavailable",
            "error": "Valkey operation failed.",
            "errors": [{"code": "provider_unavailable", "message": "Valkey operation failed."}],
            "retryable": False,
            "definitely_no_external_effect": not write,
            "external_effect_status": "timeout_unknown" if write else "not_applied",
        }


def main():
    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        require(len(raw) <= 256 * 1024, "Input exceeds 256 KiB.")
        response = handle(json.loads(raw))
    except (ValueError, TypeError, Failure):
        response = {
            "ok": False,
            "error_code": "invalid_payload",
            "error": "Invalid bounded JSON input.",
            "errors": [],
        }
    print(json.dumps(response, allow_nan=False))
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
