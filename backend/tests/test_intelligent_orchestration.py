from datetime import datetime, timezone

import pytest

from app.services.ai_gateway.providers import GatewayResponse, MuseSparkGatewayProvider
from app.services.ai_gateway.registry import ModelSpec
from app.services.intelligent_orchestrator import assess_intent, preferred_providers
from app.services.image_orchestrator import ImageOrchestrationError, _cloudflare, generate


def test_complex_follow_up_prefers_muse_spark():
    assessment = assess_intent(
        "Continue that architecture analysis and compare the trade-offs in detail.",
        conversation=[{"role": "user", "content": "We discussed a distributed system."}],
    )
    assert assessment.follow_up is True
    assert assessment.needs_reasoning is True
    assert assessment.complexity >= 0.65
    assert preferred_providers(assessment)[0] == "muse_spark"


def test_current_image_request_prefers_image_capability():
    assessment = assess_intent("Create an image of today's event poster")
    assert assessment.capability == "image"
    assert assessment.needs_image_generation is True
    assert preferred_providers(assessment)[0] == "muse_image"


def test_document_attachment_is_contextual_not_a_text_only_route():
    assessment = assess_intent(
        "Compare the uploaded files and identify the numerical differences.",
        attachments=[{"kind": "document", "filename": "budget.xlsx"}],
    )
    assert assessment.needs_document_analysis is True
    assert assessment.needs_multimodal is True


def test_muse_adapter_uses_openai_compatible_contract(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "verified"}}]}

    seen = {}

    def post(url, **kwargs):
        seen["url"] = url
        seen["payload"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.services.ai_gateway.providers.requests.post", post)
    result = MuseSparkGatewayProvider("https://muse.invalid/v1/chat/completions", "secret", "spark-test").generate("hello")
    assert isinstance(result, GatewayResponse)
    assert result.provider == "muse_spark"
    assert seen["payload"]["model"] == "spark-test"


def test_model_spec_keeps_unverified_provider_out_of_free_pool():
    spec = ModelSpec("muse_spark", "spark", False, None, frozenset({"chat"}), verified_at=datetime.now(timezone.utc))
    assert spec.verified_free is False


def test_image_orchestrator_fails_closed_without_verified_metered_provider(db_session, monkeypatch):
    monkeypatch.setattr("app.services.image_orchestrator.settings.MUSE_IMAGE_VERIFIED", False)
    monkeypatch.setattr("app.services.image_orchestrator.settings.CLOUDFLARE_IMAGE_MODEL_VERIFIED", False)
    with pytest.raises(ImageOrchestrationError, match="No verified image provider"):
        generate(db=db_session, request_id="image-test-no-provider", prompt="a landscape")


def test_cloudflare_flux2_uses_verified_multipart_contract(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"image": "aGVsbG8="}}

    def post(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr("app.services.image_orchestrator.requests.post", post)
    monkeypatch.setattr("app.services.image_orchestrator.settings.CLOUDFLARE_IMAGE_MODEL_VERIFIED", True)
    monkeypatch.setattr("app.services.image_orchestrator.settings.CLOUDFLARE_IMAGE_EDITING_VERIFIED", True)
    monkeypatch.setattr("app.services.image_orchestrator.settings.CLOUDFLARE_ACCOUNT_ID", "account")
    monkeypatch.setattr("app.services.image_orchestrator.settings.CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setattr("app.services.image_orchestrator.settings.CLOUDFLARE_IMAGE_PRIMARY_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")

    data, model = _cloudflare("turn it into a sketch", b"input-bytes")
    assert data == b"hello"
    assert model.endswith("flux-2-klein-4b")
    assert "json" not in seen
    assert seen["files"]["prompt"] == (None, "turn it into a sketch")
    assert seen["files"]["input_image_0"][0] == "reference.png"
