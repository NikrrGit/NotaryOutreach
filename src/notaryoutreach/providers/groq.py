"""Groq Compound web search adapter."""

from pathlib import Path
from contextlib import nullcontext

from groq import Groq

from ..discovery import build_client


def assess_formation(
    system_prompt: str, prompt: str, schema: dict, *,
    client: Groq | None = None, env_file: str = ".env",
) -> str:
    """Request a structured assessment; only close clients created here."""
    context = nullcontext(client) if client is not None else build_client(Path(env_file))
    with context as active_client:
        response = active_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            reasoning_effort="low",
            max_completion_tokens=2000,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "formation_assessment", "strict": True, "schema": schema},
            },
        )
        if not response.choices or response.choices[0].finish_reason != "stop":
            raise ValueError("Groq did not complete the verification response.")
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Groq returned no verification content.")
        return content


class GroqSearchProvider:
    def __init__(
        self, client: Groq | None = None, model: str = "groq/compound",
        env_file: str = ".env",
    ) -> None:
        self.client = client
        self.model = model
        self.env_file = env_file

    def search(self, *, system_prompt: str, prompt: str) -> str:
        if self.client is not None:
            return self._search(self.client, system_prompt, prompt)
        with build_client(Path(self.env_file)) as client:
            return self._search(client, system_prompt, prompt)

    def _search(self, client: Groq, system_prompt: str, prompt: str) -> str:
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            extra_headers={"Groq-Model-Version": "latest"},
            compound_custom={"tools": {"enabled_tools": ["web_search", "visit_website"]}},
            response_format={"type": "json_object"},
        )
        if not response.choices or response.choices[0].finish_reason != "stop":
            raise ValueError("Groq did not complete the discovery response.")
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Groq returned no discovery content.")
        return content
