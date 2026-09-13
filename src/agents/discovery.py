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
    candidates:list[Candidate] = Field(default_factory=list)


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


    # Public API 

    def discover(
            self,
            location : str,
            company_type : Literal["UG", "GmbH"],
            limit: int = 50,
            ) -> list[Candidate]:
        """
        Discover unique notary candidates near a German location.

        Example:
            candidates = agent.discover(
                location="Stuttgart",
                company_type="UG",
                limit=50,
            )
        """

        if limit <= 0:
            return []
        candidates : dict[str, Candidate] = {}

        for _ in range(self.max_attempts):

            if len(candidates) >= limit:
                break

            remaining = limit - len(candidates)
            current_batch_size = min(self.batch_size, remaining)

            excluded_domains = {
                self.domain(candidate.website)
                for condidate in candidates.values()
                if candidates                   
            }
 
            batch = self._discovery_batch(
                location=location, 
                company_type= company_type,
                count=current_batch_size,
                excluded_domains=excluded_domains,
            )

            new_candidate = 0

            for candidate in batch:
                key = self._candidate_key(candidate)

                if key not in candidates:
                    candidates[key] = candidate
                    new_candidates+=1

             # Groq is no longer finding anything new.
            # There is no reason to keep spending API calls.
            if new_candidates == 0:
                break

        return list(candidates.values())[:limit]
    

    # Groq Interaction
    def _discover_batch(
        self,
        location: str,
        company_type: str,
        count: int,
        excluded_domains: set[str],
    ) -> list[Candidate]:

        prompt = self._build_prompt(
            location=location,
            company_type=company_type,
            count=count,
            excluded_domains=excluded_domains,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": self._system_prompt(),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],

            # Compound can decide when to search the web
            # and when to visit a website.
            compound_custom={
                "tools": {
                    "enabled_tools": [
                        "web_search",
                        "visit_website",
                    ]
                }
            },

            # Compound supports JSON object mode.
            response_format={
                "type": "json_object",
            },
        )

        content = response.choices[0].message.content

        if not content:
            return []

        try:
            raw_data = json.loads(content)
            result = DiscoveryResult.model_validate(raw_data)

            return result.candidates

        except (json.JSONDecodeError, ValidationError) as exc:
            raise RuntimeError(
                "Groq returned an invalid discovery response."
            ) from exc