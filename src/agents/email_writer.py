from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class EmailWriterProvider(Protocol):
    """
    Minimal provider contract required by the email writer.

    The agent should depend on a capability, not directly on client.
    """

    
    def generate_structured(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            response_model: type[BaseModel],
        ) -> BaseModel:
            ...

class EmailDraft(BaseModel):
      """Structured email produced for a verification notary"""

      subject: str = Field(min_length=1, max_length=150)
      body: str = Field(min_length=1, max_length= 2000)
      language : str = "de"
      personalisation_used: str | None = None 

class EmailWriterInput(BaseModel):
      """
      Only verified information is allowed into the writer
      This prevents writer from inventing facts
      """

      notary_name : str
      city: str 
      email: str | None = None
      company_type : str 

      verification_reason : str 
      evidence : str
      source_url : str
      
      sender_name : str | None = None 
      company_name : str | None = None 