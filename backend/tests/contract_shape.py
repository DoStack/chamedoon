"""Checks a JSON body against a schema of openapi.yaml, so tests and mock fixtures are held to what the client is typed on."""

from __future__ import annotations

from pathlib import Path

import yaml

SCHEMA = yaml.safe_load((Path(__file__).resolve().parent.parent / "home_api" / "openapi.yaml").read_text(encoding="utf-8"))
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def _resolve(schema: dict) -> dict:
    while "$ref" in schema:
        schema = SCHEMA["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def problems(body: object, schema: dict, where: str = "$") -> list[str]:
    schema = _resolve(schema)
    if body is None:
        return [] if schema.get("nullable") or any(_resolve(part).get("nullable") or _resolve(part).get("enum") == [None] for part in schema.get("oneOf", [])) else [f"{where}: null"]
    for key in ("allOf", "oneOf", "anyOf"):
        if key in schema:
            found = [problems(body, part, where) for part in schema[key]]
            return found[0] if key == "allOf" and len(found) == 1 else ([] if any(not item for item in found) else [f"{where}: fits no {key}"])
    expected = schema.get("type")
    if expected and (not isinstance(body, _TYPES[expected]) or (expected in {"integer", "number"} and isinstance(body, bool))):
        return [f"{where}: {type(body).__name__}, wanted {expected}"]
    if "enum" in schema and body not in schema["enum"]:
        return [f"{where}: {body!r} not in {schema['enum']}"]
    out: list[str] = []
    if isinstance(body, dict) and "properties" in schema:
        out += [f"{where}.{name}: missing" for name in schema.get("required", []) if name not in body]
        out += [f"{where}.{name}: not in the contract" for name in body if name not in schema["properties"]]
        for name, value in body.items():
            if name in schema["properties"]:
                out += problems(value, schema["properties"][name], f"{where}.{name}")
    if isinstance(body, list) and "items" in schema:
        for index, value in enumerate(body):
            out += problems(value, schema["items"], f"{where}[{index}]")
    return out


def named(body: object, name: str) -> list[str]:
    return problems(body, {"$ref": f"#/components/schemas/{name}"})
