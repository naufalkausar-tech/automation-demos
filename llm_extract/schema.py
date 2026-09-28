from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S+$")


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": _is_integer,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def type_name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if TYPE_CHECKS[name](value):
            return name
    return type(value).__name__


def _same(a: Any, b: Any) -> bool:
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _check_format(value: str, fmt: str) -> bool:
    try:
        if fmt == "date":
            dt.date.fromisoformat(value)
        elif fmt == "date-time":
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif fmt == "email":
            return bool(_EMAIL.match(value))
        elif fmt == "uri":
            return bool(_URI.match(value))
    except ValueError:
        return False
    return True


def _resolve(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref values are supported, got {ref!r}")
    target: Any = root
    for part in ref[2:].split("/"):
        target = target[part.replace("~1", "/").replace("~0", "~")]
    return _resolve(target, root)


def validate(instance: Any, schema: Any, root: Any = None, path: str = "$") -> list[str]:
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [f"{path}: no value is allowed here"]
    root = schema if root is None else root
    schema = _resolve(schema, root)
    errors: list[str] = []

    for key in ("anyOf", "oneOf"):
        if key in schema:
            passing = [s for s in schema[key] if not validate(instance, s, root, path)]
            if not passing or (key == "oneOf" and len(passing) > 1):
                errors.append(f"{path}: does not match {'exactly one' if key == 'oneOf' else 'any'} of the allowed shapes")
    for sub in schema.get("allOf", []):
        errors.extend(validate(instance, sub, root, path))

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(TYPE_CHECKS[t](instance) for t in types):
            errors.append(f"{path}: expected {' or '.join(types)}, got {type_name(instance)}")
            return errors

    if "enum" in schema and not any(_same(instance, option) for option in schema["enum"]):
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")
    if "const" in schema and not _same(instance, schema["const"]):
        errors.append(f"{path}: must equal {schema['const']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
        if "format" in schema and not _check_format(instance, schema["format"]):
            errors.append(f"{path}: not a valid {schema['format']}")

    if TYPE_CHECKS["number"](instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: less than minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: greater than maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be greater than {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be less than {schema['exclusiveMaximum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child = f"{path}.{key}"
            if key in properties:
                errors.extend(validate(value, properties[key], root, child))
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    errors.append(f"{path}: unexpected property {key!r}")
                elif isinstance(extra, dict):
                    errors.extend(validate(value, extra, root, child))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], root, f"{path}[{index}]"))
    return errors
