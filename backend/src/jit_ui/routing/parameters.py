"""Strict parameter validation shared by routing and compilation."""

from __future__ import annotations

from typing import Any

from jit_ui.domain.models import ParameterSpec
from jit_ui.routing.parameter_helpers import validate_parameter_values


def validate_parameters(
    parameters: dict[str, Any], specs: dict[str, ParameterSpec]
) -> dict[str, Any]:
    return validate_parameter_values(
        parameters, {name: spec.model_dump() for name, spec in specs.items()}
    )
