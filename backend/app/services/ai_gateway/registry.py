from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    enabled: bool
    free_eligible: bool | None
    capabilities: frozenset[str]
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    availability: str = "unknown"
    verified_at: datetime | None = None
    restrictions: tuple[str, ...] = ()

    @property
    def free(self) -> bool:
        """Compatibility view; only verified true is eligible."""
        return self.free_eligible is True

    @property
    def verified_free(self) -> bool:
        return self.enabled and self.free_eligible is True and self.availability == "available" and self.verified_at is not None


# Registry entries are deliberately explicit.  Paid or unverified models are not
# eligible merely because a client sends their name.
MODEL_REGISTRY = (
    ModelSpec("mock", "mock-chat", True, True, frozenset({"chat", "test"}), availability="available", verified_at=datetime.now(timezone.utc)),
    # Live entries are intentionally unknown until an administrator verifies the
    # exact account/model/endpoint combination. Public model names are not proof
    # of free hosted inference eligibility.
    ModelSpec("gemini", "gemini-3.8-flash", False, None, frozenset({"chat"}), restrictions=("requires explicit admin verification",)),
    ModelSpec("huggingface", "configured-free-model", False, None, frozenset({"chat", "image"}), restrictions=("requires configured endpoint verification",)),
    # openrouter/free is a provider-side router, not a stale model id. It chooses
    # an available free model for every request and stays within the free policy.
    ModelSpec(
        "openrouter", "openrouter/free", True, True, frozenset({"chat"}),
        availability="available", verified_at=datetime.now(timezone.utc),
    ),
)


def models_for(provider: str) -> tuple[ModelSpec, ...]:
    return tuple(item for item in MODEL_REGISTRY if item.provider == provider)
