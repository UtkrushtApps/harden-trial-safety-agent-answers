import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.model_client import RealModelClient
from app.policy import eligible_routes, provider_routes
from app.prompts import build_source_prompt, redact_sensitive_text


def test_application_imports_and_health_without_provider_key() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.is_success
    assert response.json() == {"status": "ok"}
    assert RealModelClient(get_settings()) is not None


def test_fixture_loads() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures/eu_summary_request.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert payload
    assert isinstance(payload, dict)


def test_environment_model_cannot_override_study_policy() -> None:
    rows = [{
        "provider_name": "approved-gateway",
        "provider_region": "eu-west",
        "model_name": "approved-model",
        "input_cost_per_million": "1",
        "output_cost_per_million": "2",
        "approved": True,
        "priority": 1,
    }]
    routes = eligible_routes(provider_routes(rows, "unapproved-environment-model"))
    assert routes[0].model_name == "approved-model"


def test_patient_details_are_removed_from_prompt() -> None:
    study = {
        "study_code": "TEST-1",
        "title": "Test study",
        "protocol_version": "1",
        "summary_instructions": "Summarize facts.",
    }
    reports = [{
        "event_term": "Seizure",
        "seriousness": "serious",
        "onset_date": "2026-01-01",
        "narrative": (
            "Participant Elena Maric, date of birth 1981-09-17, was admitted. "
            "Call +49 151 5550129. Study treatment was interrupted."
        ),
    }]
    prompt = build_source_prompt(study, reports, "June", "safety team")
    assert "Elena" not in prompt
    assert "1981-09-17" not in prompt
    assert "5550129" not in prompt
    assert "Study treatment was interrupted." in prompt
    assert "Elena" not in redact_sensitive_text(reports[0]["narrative"])
