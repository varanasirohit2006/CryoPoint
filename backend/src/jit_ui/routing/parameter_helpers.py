"""Standard-library parameter rules also bundled into standalone exports."""

from math import isfinite


def validate_parameter_values(parameters, specs):
    unexpected = set(parameters) - set(specs)
    if unexpected:
        raise ValueError("Unexpected parameters: " + ", ".join(sorted(unexpected)))
    validated = {}
    for name, spec in specs.items():
        if name not in parameters:
            if spec.get("required", True):
                raise ValueError("Missing required parameter: " + name)
            value = spec.get("default")
            if value is None:
                validated[name] = None
                continue
        else:
            value = parameters[name]
        if value is None and not spec.get("required", True):
            validated[name] = None
            continue
        kind = spec["type"]
        matches_type = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }[kind]
        if not matches_type:
            raise ValueError("Parameter " + name + " must be " + kind + ".")
        if kind == "string" and not value.strip():
            raise ValueError("Parameter " + name + " must not be blank.")
        if kind in ("integer", "number"):
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("Parameter " + name + " must be finite.")
            minimum = spec.get("minimum")
            if minimum is not None and value < minimum:
                raise ValueError("Parameter " + name + " must be at least " + str(minimum) + ".")
            maximum = spec.get("maximum")
            if maximum is not None and value > maximum:
                raise ValueError("Parameter " + name + " must be at most " + str(maximum) + ".")
        if spec.get("enum") is not None and value not in spec["enum"]:
            raise ValueError("Parameter " + name + " is not an allowed value.")
        validated[name] = value
    return validated
