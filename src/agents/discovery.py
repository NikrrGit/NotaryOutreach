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



    # Prompts

    @staticmethod
    def _system_prompt() -> str:
        return """
You are a research agent that discovers German notaries.

Your only responsibility is DISCOVERY.

You must use live web information.

Rules:

1. Find real Notare / Notarinnen in Germany.
2. Prefer official notary websites.
3. Public professional contact information only.
4. Never invent names, websites, emails, phone numbers or services.
5. Every candidate MUST have a source_url.
6. If a value cannot be verified, return null.
7. Do not decide definitively whether the candidate handles UG or GmbH
   formation. Another agent will verify that.
8. company_type_hint may contain a short indication such as
   "Gesellschaftsrecht mentioned" if discovered.
9. Return ONLY valid JSON.

Required JSON shape:

{
    "candidates": [
        {
            "name": "string",
            "city": "string",
            "website": "string or null",
            "email": "string or null",
            "phone": "string or null",
            "source_url": "string",
            "company_type_hint": "string or null"
        }
    ]
}
""".strip()

    @staticmethod
    def _build_prompt(
        location: str,
        company_type: str,
        count: int,
        excluded_domains: set[str],
    ) -> str:

        excluded = (
            "\n".join(sorted(excluded_domains))
            if excluded_domains
            else "None"
        )

        return f"""
Find up to {count} additional notaries around:

Location: {location}, Germany
Company formation type of interest: {company_type}

Search the given city and reasonable nearby locations.

We are eventually looking for notaries who may be able to assist with
forming a {company_type}, but at this stage only discover candidates.

Useful search concepts include:

- Notar {location}
- Notar Gesellschaftsrecht {location}
- Notar Unternehmensgründung {location}
- Notar GmbH Gründung {location}
- Notar UG Gründung {location}

Prefer:

1. official notary websites;
2. official/professional notary sources;
3. public professional contact information.

Do NOT return candidates from these domains because they were already
discovered:

{excluded}

Return candidates using exactly the JSON structure specified in the
system instructions.
""".strip()


 # Deterministic Python helpers

    @staticmethod
    def _domain(url: str | None) -> str:
        if not url:
            return ""

        parsed = urlparse(url)

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    def _candidate_key(self, candidate: Candidate) -> str:
        """
        Generate a deterministic identity for basic deduplication.

        Prefer website domain because names can vary in formatting.
        """

        domain = self._domain(candidate.website)

        if domain:
            return f"domain:{domain}"

        if candidate.email:
            return f"email:{candidate.email.lower().strip()}"

        return (
            f"name:"
            f"{candidate.name.lower().strip()}:"
            f"{candidate.city.lower().strip()}"
        )