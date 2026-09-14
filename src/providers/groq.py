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