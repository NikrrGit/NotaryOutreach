"""Candidate fields and statuses for the outreach workflow."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique
from typing import Any, Self


class ValidationError(ValueError):
    pass


@unique
class CandidateStatus(Enum):
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    EMAIL_GENERATED = "email_generated"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ERROR = "error"

    @classmethod
    def has_value(cls, value: str) -> bool:
        return any(value == item.value for item in cls)


@dataclass(frozen=True)
class Candidate:
    name: str
    city: str
    source_url: str

    id: str | int | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    company_formation_supported: bool | None = None
    confidence: float = 0.0
    verification_reason: str | None = None
    personalised_email: str | None = None
    status: CandidateStatus = CandidateStatus.DISCOVERED

    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("name", "city", "source_url"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"Candidate '{name}' must be nonempty text.")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.status, CandidateStatus):
            raise ValidationError("Status must be a CandidateStatus.")
        if self.company_formation_supported is not None and type(self.company_formation_supported) is not bool:
            raise ValidationError("Company formation support must be true, false, or None.")
        if type(self.confidence) not in (int, float) or not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("Confidence must be a number between 0.0 and 1.0.")
        for name in ("created_at", "updated_at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, datetime):
                raise ValidationError(f"'{name}' must be a datetime or None.")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "city": self.city,
            "source_url": self.source_url,
            "website": self.website,
            "email": self.email,
            "phone": self.phone,
            "company_formation_supported": self.company_formation_supported,
            "confidence": self.confidence,
            "verification_reason": self.verification_reason,
            "personalised_email": self.personalised_email,
            "status": self.status.value,
        }

        if self.id is not None:
            payload["id"] = self.id
        if self.created_at is not None:
            payload["created_at"] = self.created_at.isoformat()
        if self.updated_at is not None:
            payload["updated_at"] = self.updated_at.isoformat()

        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            status_enum = CandidateStatus(data.get("status", "discovered"))
            created_at = data.get("created_at")
            updated_at = data.get("updated_at")

            return cls(
                id=data.get("id"),
                name=data.get("name", ""),
                city=data.get("city", ""),
                website=data.get("website"),
                email=data.get("email"),
                phone=data.get("phone"),
                source_url=data.get("source_url", ""),
                company_formation_supported=data.get("company_formation_supported"),
                confidence=data.get("confidence", 0.0),
                verification_reason=data.get("verification_reason"),
                personalised_email=data.get("personalised_email"),
                status=status_enum,
                created_at=datetime.fromisoformat(created_at) if isinstance(created_at, str) else created_at,
                updated_at=datetime.fromisoformat(updated_at) if isinstance(updated_at, str) else updated_at,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValidationError(f"Failed to parse candidate data payload: {exc}") from exc
