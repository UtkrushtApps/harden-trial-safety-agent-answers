import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.config import Settings
from app.database import (
    begin_run,
    complete_run,
    load_study_bundle,
    record_attempt,
)
from app.model_client import ModelResult, RealModelClient
from app.policy import (
    ProviderRoute,
    eligible_routes,
    estimate_cost,
    projected_call_cost,
    provider_routes,
)
from app.prompts import (
    PROMPT_VERSION,
    append_verified_serious_facts,
    build_source_prompt,
    draft_messages,
    prompt_fingerprint,
    redact_sensitive_text,
    review_messages,
    serious_event_fallback,
)
from app.schemas import SummaryRequest

logger = logging.getLogger(__name__)

MAX_MODEL_CALLS = 2
MAX_OUTPUT_TOKENS = 512


class StudyNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class SummaryOutcome:
    run_id: UUID
    summary: str
    status: str
    model_calls: int
    estimated_cost_usd: Decimal


def _error_category(exc: Exception) -> str:
    # Exception messages can contain echoed request data and are never persisted.
    name = type(exc).__name__
    if name in {"RateLimitError", "APITimeoutError", "APIConnectionError"}:
        return name
    if isinstance(exc, RuntimeError):
        return "ModelConfigurationError"
    return "ProviderError"


class SafetySummaryOrchestrator:
    def __init__(self, settings: Settings, model_client: RealModelClient) -> None:
        self._settings = settings
        self._model_client = model_client

    async def run(self, study_id: UUID, request: SummaryRequest) -> SummaryOutcome:
        bundle = load_study_bundle(self._settings.database_url, study_id)
        if bundle is None:
            raise StudyNotFoundError(str(study_id))

        reports = bundle["reports"]
        source_prompt = build_source_prompt(
            bundle["study"],
            reports,
            request.reporting_window,
            request.audience,
        )
        run_id = begin_run(
            self._settings.database_url,
            study_id,
            request.request_id,
            request.budget_usd,
            prompt_fingerprint(source_prompt),
            PROMPT_VERSION,
            prompt_text=source_prompt,
            audit_details={
                "privacy_transform": "structured-canonical-v1",
                "max_model_calls": MAX_MODEL_CALLS,
                "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
                "terminal": False,
            },
        )
        logger.info("starting safety summary run=%s study=%s", run_id, study_id)

        routes = eligible_routes(
            provider_routes(bundle["providers"], self._settings.openai_model)
        )
        calls = 0
        total_input_tokens = 0
        total_output_tokens = 0
        accounted_cost = Decimal(0)
        last_error: str | None = None
        draft: ModelResult | None = None
        draft_route: ProviderRoute | None = None

        draft_messages_value = draft_messages(source_prompt)
        for route in routes:
            if calls >= MAX_MODEL_CALLS:
                break
            projected = projected_call_cost(
                route, draft_messages_value, MAX_OUTPUT_TOKENS
            )
            remaining = request.budget_usd - accounted_cost
            base_evidence: dict[str, Any] = {
                "stage": "draft",
                "attempt_number": calls + 1,
                "provider": route.provider_name,
                "region": route.provider_region,
                "model": route.model_name,
                "approved": route.approved,
                "reason": "approved study route in priority order",
                "projected_cost_usd": str(projected),
                "budget_remaining_before_usd": str(max(Decimal(0), remaining)),
            }
            if projected > remaining:
                record_attempt(
                    self._settings.database_url,
                    run_id,
                    {**base_evidence, "decision": "skipped_budget"},
                )
                continue

            # Count and reserve before sending context. Failed calls remain charged
            # at their projection because actual provider usage is unknown.
            calls += 1
            accounted_cost += projected
            try:
                result = await self._model_client.complete(
                    model=route.model_name,
                    messages=draft_messages_value,
                    provider_region=route.provider_region,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                )
                actual_cost = estimate_cost(
                    route, result.input_tokens, result.output_tokens
                )
                accounted_cost += actual_cost - projected
                total_input_tokens += result.input_tokens
                total_output_tokens += result.output_tokens
                draft = result
                draft_route = route
                record_attempt(
                    self._settings.database_url,
                    run_id,
                    {
                        **base_evidence,
                        "decision": "completed",
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "actual_cost_usd": str(actual_cost),
                        "response_model": result.model,
                    },
                )
                break
            except Exception as exc:
                last_error = _error_category(exc)
                record_attempt(
                    self._settings.database_url,
                    run_id,
                    {
                        **base_evidence,
                        "decision": "provider_failure",
                        "error_category": last_error,
                        "reserved_cost_usd": str(projected),
                    },
                )
                logger.warning(
                    "model attempt failed run=%s stage=draft category=%s",
                    run_id,
                    last_error,
                )

        if draft is None or draft_route is None:
            reason = (
                "budget_limited"
                if calls == 0 or accounted_cost >= request.budget_usd
                else "draft_unavailable"
            )
            return self._finish_degraded(
                run_id,
                reports,
                calls,
                accounted_cost,
                total_input_tokens,
                total_output_tokens,
                reason,
                last_error,
            )

        safe_draft = redact_sensitive_text(draft.text)
        final_text = safe_draft
        status = "degraded"
        degradation = "review_not_attempted"

        if calls < MAX_MODEL_CALLS:
            review_messages_value = review_messages(source_prompt, safe_draft)
            review_route = draft_route
            projected = projected_call_cost(
                review_route, review_messages_value, MAX_OUTPUT_TOKENS
            )
            remaining = request.budget_usd - accounted_cost
            evidence: dict[str, Any] = {
                "stage": "review",
                "attempt_number": calls + 1,
                "provider": review_route.provider_name,
                "region": review_route.provider_region,
                "model": review_route.model_name,
                "approved": review_route.approved,
                "reason": "review on approved successful draft route",
                "projected_cost_usd": str(projected),
                "budget_remaining_before_usd": str(max(Decimal(0), remaining)),
            }
            if projected <= remaining:
                calls += 1
                accounted_cost += projected
                try:
                    reviewed = await self._model_client.complete(
                        model=review_route.model_name,
                        messages=review_messages_value,
                        provider_region=review_route.provider_region,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    )
                    actual_cost = estimate_cost(
                        review_route,
                        reviewed.input_tokens,
                        reviewed.output_tokens,
                    )
                    accounted_cost += actual_cost - projected
                    total_input_tokens += reviewed.input_tokens
                    total_output_tokens += reviewed.output_tokens
                    final_text = redact_sensitive_text(reviewed.text)
                    status = "completed"
                    degradation = None
                    record_attempt(
                        self._settings.database_url,
                        run_id,
                        {
                            **evidence,
                            "decision": "completed",
                            "input_tokens": reviewed.input_tokens,
                            "output_tokens": reviewed.output_tokens,
                            "actual_cost_usd": str(actual_cost),
                            "response_model": reviewed.model,
                        },
                    )
                except Exception as exc:
                    last_error = _error_category(exc)
                    degradation = "review_provider_failure"
                    record_attempt(
                        self._settings.database_url,
                        run_id,
                        {
                            **evidence,
                            "decision": "provider_failure",
                            "error_category": last_error,
                            "reserved_cost_usd": str(projected),
                        },
                    )
                    logger.warning(
                        "model attempt failed run=%s stage=review category=%s",
                        run_id,
                        last_error,
                    )
            else:
                degradation = "review_budget_limited"
                record_attempt(
                    self._settings.database_url,
                    run_id,
                    {**evidence, "decision": "skipped_budget"},
                )

        summary = append_verified_serious_facts(final_text, reports)
        complete_run(
            self._settings.database_url,
            run_id,
            status,
            summary,
            calls,
            last_error,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            estimated_cost_usd=accounted_cost,
            degradation_outcome=degradation,
            audit_details={
                "terminal": True,
                "terminal_outcome": status,
                "budget_accounted_usd": str(accounted_cost),
                "serious_facts_appended": True,
            },
        )
        logger.info("finished safety summary run=%s status=%s", run_id, status)
        return SummaryOutcome(run_id, summary, status, calls, accounted_cost)

    def _finish_degraded(
        self,
        run_id: UUID,
        reports: list[dict[str, Any]],
        calls: int,
        cost: Decimal,
        input_tokens: int,
        output_tokens: int,
        outcome: str,
        error_category: str | None,
    ) -> SummaryOutcome:
        summary = serious_event_fallback(reports)
        complete_run(
            self._settings.database_url,
            run_id,
            "degraded",
            summary,
            calls,
            error_category,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            degradation_outcome=outcome,
            audit_details={
                "terminal": True,
                "terminal_outcome": "degraded",
                "budget_accounted_usd": str(cost),
                "serious_facts_appended": True,
            },
        )
        logger.info("finished safety summary run=%s status=degraded", run_id)
        return SummaryOutcome(run_id, summary, "degraded", calls, cost)
