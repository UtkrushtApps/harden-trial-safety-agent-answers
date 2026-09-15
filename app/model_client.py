from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings


@dataclass(frozen=True)
class ModelResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    response_id: str | None


class RealModelClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> AsyncOpenAI:
        if not self._settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for model calls")
        return AsyncOpenAI(
            api_key=self._settings.openai_api_key,
            base_url=self._settings.openai_base_url,
            timeout=self._settings.model_timeout_seconds,
            max_retries=0,
        )

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        provider_region: str,
        temperature: float = 0.1,
        max_output_tokens: int = 512,
    ) -> ModelResult:
        response = await self._client().chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_output_tokens,
            extra_headers={"x-provider-region": provider_region},
        )
        if not response.choices:
            raise RuntimeError("Provider returned no completion")
        choice = response.choices[0]
        usage: Any = response.usage
        return ModelResult(
            text=choice.message.content or "",
            input_tokens=int(usage.prompt_tokens if usage else 0),
            output_tokens=int(usage.completion_tokens if usage else 0),
            model=response.model,
            response_id=response.id,
        )

    async def ping(self) -> None:
        if not self._settings.openai_model:
            raise RuntimeError("OPENAI_MODEL is required for direct model ping")
        await self.complete(
            model=self._settings.openai_model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            provider_region="configured",
            temperature=0,
            max_output_tokens=8,
        )
