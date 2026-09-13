from __future__ import annotations

import json
import os
from typing import Literal
from urllib.parse import urlparse

from groq import Groq
from pydantic import BaseModel, Field, ValidationError


# Structured output

class Candidate(BaseModel):
    """
    A notary candidate discovered from the web.

    IMPORTANT:
    Discovery does NOT decide whether this notary is actually suitable
    for UG/GmbH formation. That belongs to the verification agent.
    """

    name:str
    city:str

    website:str | None = None 
    email:str | None = None 
    phone :str | None = None 
    source_url : str 

    company_type_hint: str | None = None 

class DiscoverResult(BaseModel):
    candidates:list[Condidate] = Field(default_factory=list)


class DiscoveryAgent:
    """
    Finds potential German notaries using Groq Compound.

    Responsibilities:
        - Search the live web.
        - Prefer official notary websites.
        - Collect basic contact information.
        - Return structured candidate records.

    NOT responsible for:
        - Determining UG/GmbH compatibility.
        - Writing outreach emails.
        - Sending emails.
    """

    def __init__(
            self,
            client: Groq | None = None,
            model: str =  "groq/compound",
            batch_size: int = 10,
            max_attempts : int = 10
    ) -> None:
        self.client = client or Groq(
            api_key=os.environ["GROQ_API_KEY"],
            default_headers={
                "Groq_Model_Version": "latest",
            },
        )
        self.model = model
        self.batch_size = batch_size
        self.max_attempts = max_attempts

        