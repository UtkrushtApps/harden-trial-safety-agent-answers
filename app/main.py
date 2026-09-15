import logging
from uuid import UUID

from fastapi import FastAPI, HTTPException

from app.config import get_settings
from app.model_client import RealModelClient
from app.orchestrator import SafetySummaryOrchestrator, StudyNotFoundError
from app.schemas import SummaryRequest, SummaryResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Clinical Trial Safety Summary Agent", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    # Readiness/import checks deliberately do not require a provider key.
    return {"status": "ok"}


@app.post(
    "/api/studies/{study_id}/safety-summary",
    response_model=SummaryResponse,
)
async def create_safety_summary(
    study_id: UUID,
    request: SummaryRequest,
) -> SummaryResponse:
    settings = get_settings()
    orchestrator = SafetySummaryOrchestrator(settings, RealModelClient(settings))
    try:
        outcome = await orchestrator.run(study_id, request)
    except StudyNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc
    except Exception as exc:
        # Do not expose provider, database, prompt, or patient-bearing details.
        logging.getLogger(__name__).exception(
            "safety summary request failed study=%s", study_id
        )
        raise HTTPException(
            status_code=503,
            detail="Safety summary service is temporarily unavailable",
        ) from exc

    return SummaryResponse(
        run_id=outcome.run_id,
        study_id=study_id,
        summary=outcome.summary,
        status=outcome.status,
        model_calls=outcome.model_calls,
        estimated_cost_usd=outcome.estimated_cost_usd,
    )
