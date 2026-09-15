from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ProviderRoute:
    provider_name: str
    provider_region: str
    model_name: str
    input_cost_per_million: Decimal
    output_cost_per_million: Decimal
    approved: bool
    priority: int


def provider_routes(
    rows: list[dict[str, Any]],
    configured_model: str = "",
) -> list[ProviderRoute]:
    """Build routes from policy rows without allowing environment overrides.

    configured_model is retained for API compatibility, but study policy is the
    authority for models as well as provider locations.
    """
    del configured_model
    return [
        ProviderRoute(
            provider_name=row["provider_name"],
            provider_region=row["provider_region"],
            model_name=row["model_name"],
            input_cost_per_million=Decimal(row["input_cost_per_million"]),
            output_cost_per_million=Decimal(row["output_cost_per_million"]),
            approved=bool(row["approved"]),
            priority=int(row["priority"]),
        )
        for row in rows
    ]


def eligible_routes(routes: list[ProviderRoute]) -> list[ProviderRoute]:
    return sorted(
        (route for route in routes if route.approved),
        key=lambda route: route.priority,
    )


def route_for_attempt(routes: list[ProviderRoute], attempt: int) -> ProviderRoute:
    approved = eligible_routes(routes)
    if attempt < 0 or attempt >= len(approved):
        raise RuntimeError("No eligible provider route remains")
    return approved[attempt]


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    # UTF-8 byte count plus per-message overhead is deliberately conservative.
    return max(
        1,
        sum(len(message.get("content", "").encode("utf-8")) + 16 for message in messages),
    )


def estimate_cost(
    route: ProviderRoute,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    million = Decimal(1_000_000)
    return (
        Decimal(input_tokens) * route.input_cost_per_million
        + Decimal(output_tokens) * route.output_cost_per_million
    ) / million


def projected_call_cost(
    route: ProviderRoute,
    messages: list[dict[str, str]],
    expected_output_tokens: int,
) -> Decimal:
    return estimate_cost(route, estimate_tokens(messages), expected_output_tokens)
