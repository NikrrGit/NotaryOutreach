# src/providers/groq.py

from __future__ import annotations

import json
import os
from typing import Any

from groq import Groq

class GroqProvider:
    """
    Central Groq integration used by workflow agents.

    Responsibilities:
        - Create and hold the Groq client.
        - Call Groq Compound for live web research.
        - Call normal Groq models for structured reasoning tasks.
        - Parse model responses consistently.
        - Keep Groq-specific API details out of agent code.

    Agents should NOT instantiate Groq directly.
    """

    def __init__(
            self,
            api_key: str | None = None,
            compound_model : str = "groq/compound",
            reasoning_model : str ="opeai/gpt-oss- 20b",
            ) -> None:

        resolved_api_key = api_key or os.getenv("GROQ_API_KEY")

        if not resolved_api_key:
            raise ValueError(
                "GROQ_API_KEY is missing. "
                "Set it in the environment or pass api_key explicitely."
            )
        self.client= Groq(
            api_key=resolved_api_key,
            default_headers={
                "Groq-Model-Version": "latest",
            },
        )
        self.compound.model = compound_model
        self,reasoning_model = reasoning_model

    # Live web reseach
    def search_web(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            json_mode: bool = True,
        ) -> dict[str, Any] | str:
            """
            Perform live web research using Groq Compound.

            Compound is allowed to:
                - search the web
                - visit public websites

            Intended for:
                - discovery
                - source gathering
                - current website/contact research
            """

            request: dict[str, Any] = {
                "model": self.compound_model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                "compound_custom": {
                    "tools": {
                        "enabled_tools": [
                            "web_search",
                            "visit_website",
                        ]
                    }
                },
            }

            if json_mode:
                request["response_format"] = {
                    "type": "json_object",
                }

            response = self.client.chat.completions.create(**request)

            content = response.choices[0].message.content

            if not content:
                raise RuntimeError(
                    "Groq returned an empty response."
                )

            if not json_mode:
                return content

            return self._parse_json(content)
        )