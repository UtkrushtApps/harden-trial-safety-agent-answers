from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@contextmanager
def connection(database_url: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        yield conn


def database_probe(database_url: str) -> int:
    with connection(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM studies")
            row = cursor.fetchone()
            return int(row["count"] if row else 0)


def load_study_bundle(database_url: str, study_id: UUID) -> dict[str, Any] | None:
    with connection(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM studies WHERE id = %s AND active = TRUE",
                (study_id,),
            )
            study = cursor.fetchone()
            if study is None:
                return None

            cursor.execute(
                """
                SELECT * FROM adverse_event_reports
                WHERE study_id = %s
                ORDER BY received_at, id
                """,
                (study_id,),
            )
            reports = cursor.fetchall()

            cursor.execute(
                """
                SELECT * FROM provider_region_policy
                WHERE study_id = %s AND enabled = TRUE
                ORDER BY priority, id
                """,
                (study_id,),
            )
            providers = cursor.fetchall()

    return {"study": study, "reports": reports, "providers": providers}


def begin_run(
    database_url: str,
    study_id: UUID,
    request_id: str,
    budget_usd: Any,
    prompt_fingerprint: str,
    prompt_version: str,
    prompt_text: str | None = None,
    audit_details: dict[str, Any] | None = None,
) -> UUID:
    with connection(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_runs (
                    study_id, request_id, requested_budget_usd, status,
                    prompt_fingerprint, prompt_version, prompt_text, audit_details
                ) VALUES (%s, %s, %s, 'running', %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    study_id,
                    request_id,
                    budget_usd,
                    prompt_fingerprint,
                    prompt_version,
                    prompt_text,
                    Jsonb(audit_details or {}),
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("Run record was not created")
            return row["id"]


def record_attempt(database_url: str, run_id: UUID, attempt: dict[str, Any]) -> None:
    """Append only privacy-safe structured attempt evidence."""
    with connection(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_runs
                SET audit_details = jsonb_set(
                        audit_details,
                        '{attempts}',
                        COALESCE(audit_details->'attempts', '[]'::jsonb)
                            || %s::jsonb,
                        TRUE
                    ),
                    selected_provider = COALESCE(%s, selected_provider),
                    provider_region = COALESCE(%s, provider_region),
                    model_name = COALESCE(%s, model_name),
                    routing_reason = COALESCE(%s, routing_reason)
                WHERE id = %s
                """,
                (
                    Jsonb([attempt]),
                    attempt.get("provider"),
                    attempt.get("region"),
                    attempt.get("model"),
                    attempt.get("reason"),
                    run_id,
                ),
            )


def complete_run(
    database_url: str,
    run_id: UUID,
    status: str,
    response_text: str | None,
    model_call_count: int,
    error_category: str | None = None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: Decimal = Decimal(0),
    degradation_outcome: str | None = None,
    audit_details: dict[str, Any] | None = None,
) -> None:
    with connection(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_runs
                SET status = %s,
                    response_text = %s,
                    model_call_count = %s,
                    error_category = %s,
                    input_tokens = %s,
                    output_tokens = %s,
                    estimated_cost_usd = %s,
                    degradation_outcome = %s,
                    audit_details = audit_details || %s,
                    completed_at = NOW()
                WHERE id = %s
                """,
                (
                    status,
                    response_text,
                    model_call_count,
                    error_category,
                    input_tokens,
                    output_tokens,
                    estimated_cost_usd,
                    degradation_outcome,
                    Jsonb(audit_details or {}),
                    run_id,
                ),
            )
