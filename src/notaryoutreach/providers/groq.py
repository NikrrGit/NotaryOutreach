"""Groq Compound web search adapter."""

from pathlib import Path

from groq import Groq

from ..discovery import build_client


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
