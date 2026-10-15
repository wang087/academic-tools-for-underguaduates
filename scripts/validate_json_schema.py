# -*- coding: utf-8 -*-
"""用项目内 JSON Schema 校验核心中间产物。"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMAS = {
    "stage-plan": ROOT / "schemas" / "stage-plan.schema.json",
    "bibliography-record": ROOT / "schemas" / "bibliography-record.schema.json",
    "disambiguation-result": ROOT / "schemas" / "disambiguation-result.schema.json",
    "fulltext-state": ROOT / "schemas" / "fulltext-state.schema.json",
    "interpret-task": ROOT / "schemas" / "interpret-task.schema.json",
    "review-task": ROOT / "schemas" / "review-task.schema.json",
    "interpret-card": ROOT / "schemas" / "interpret-card.schema.json",
    "review-card": ROOT / "schemas" / "review-card.schema.json",
    "graph-data": ROOT / "schemas" / "graph-data.schema.json",
    "run-status": ROOT / "schemas" / "run-status.schema.json",
}


def load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def type_ok(value: Any, expected: str | list[str]) -> bool:
    kinds = [expected] if isinstance(expected, str) else expected
    return any({
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(kind, True) for kind in kinds)


def validate(value: Any, schema: dict[str, Any], path: str = "$", errors: list[str] | None = None,
             root_schema: dict[str, Any] | None = None) -> list[str]:
    errors = errors if errors is not None else []
    root_schema = root_schema or schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            schema = root_schema.get("$defs", {}).get(ref.removeprefix("#/$defs/"), {})
    if "anyOf" in schema:
        branches = []
        for branch in schema["anyOf"]:
            branch_errors: list[str] = []
            validate(value, branch, path, branch_errors, root_schema)
            if not branch_errors:
                return errors
            branches.append(branch_errors)
        errors.append(f"{path}: 不符合任一允许结构")
        return errors
    if "type" in schema and not type_ok(value, schema["type"]):
        errors.append(f"{path}: 类型应为 {schema['type']}")
        return errors
    if "required" in schema and isinstance(value, dict):
        for key in schema["required"]:
            if key not in value:
                errors.append(f"{path}: 缺少必需字段 {key}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key, child in props.items():
            if key in value:
                validate(value[key], child, f"{path}.{key}", errors, root_schema)
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            for key, child_value in value.items():
                if key not in props:
                    validate(child_value, extra, f"{path}.{key}", errors, root_schema)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]", errors, root_schema)
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        errors.append(f"{path}: 不得小于 {schema['minimum']}")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        errors.append(f"{path}: 不能为空")
    return errors


def bibliography_values(data: Any) -> list[tuple[str, Any]]:
    if isinstance(data, list):
        return [(f"$[{i}]", item) for i, item in enumerate(data)]
    if isinstance(data, dict):
        if "resultList" in data or "kept" in data or "review" in data:
            return [(f"$.{key}[{i}]", item)
                    for key in ("resultList", "kept", "review")
                    if isinstance(data.get(key), list)
                    for i, item in enumerate(data[key])]
        return [(f"$.{key}", value) for key, value in data.items()
                if isinstance(value, dict)]
    return [("$", data)]


def main() -> int:
    parser = argparse.ArgumentParser(description="校验流程1核心 JSON 产物")
    parser.add_argument("--kind", choices=sorted(SCHEMAS), required=True)
    parser.add_argument("input", type=pathlib.Path)
    args = parser.parse_args()
    try:
        data = load(args.input)
        schema = load(SCHEMAS[args.kind])
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[X] 读取失败：{exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    if args.kind == "bibliography-record":
        records = bibliography_values(data)
        if not records:
            errors.append("$: 未找到题录记录")
        for path, record in records:
            validate(record, schema, path, errors)
    else:
        validate(data, schema, errors=errors)

    if errors:
        print(f"[X] {args.kind} 校验失败：{len(errors)} 项")
        for error in errors[:30]:
            print(f"  - {error}")
        if len(errors) > 30:
            print(f"  … 其余 {len(errors) - 30} 项省略")
        return 1
    print(f"[OK] {args.kind}：{args.input}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
