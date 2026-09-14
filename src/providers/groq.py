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