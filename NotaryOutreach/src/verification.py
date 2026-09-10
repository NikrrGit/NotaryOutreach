"""Assess UG-formation suitability for a candidate from its website content."""
 
import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
 
from groq import APIError, Groq
 
from .config import ConfigurationError
from .discovery import build_client
from .models import Candidate, CandidateStatus, ValidationError
 
MODEL = "llama-3.3-70b-versatile"
FETCH_TIMEOUT = 15
MAX_PAGE_CHARS = 6000
TAG_RE = re.compile(r"<[^>]+>")
 
SYSTEM_PROMPT = (
    "You assess whether a notary's website shows evidence they support UG "
    "(Unternehmergesellschaft) and GmbH company formation, Gesellschaftsrecht. "
    "Base your judgment only on the provided page text, not assumptions. "
    "Respond with a JSON object only, no prose, no markdown fences: "
    '{"supported": true/false/null, "confidence": 0.0-1.0, "reason": string}. '
    "Use null for supported if the page gives no relevant evidence either way."
)

 
def fetch_page_text(url: str) -> str:
    """Fetch `url` and strip HTML tags, truncated to a bounded length."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=FETCH_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, ValueError) as exc:
        raise ValidationError(f"Could not fetch website: {exc}") from exc
    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", raw)).strip()
    return text[:MAX_PAGE_CHARS]
 
 
def verify_candidate(client: Groq, candidate: Candidate) -> Candidate:
    """Return `candidate` updated with formation-support evidence from its website."""
    if not candidate.website:
        return replace(
            candidate,
            company_formation_supported=None,
            confidence=0.0,
            verification_reason="No website to inspect.",
            status=CandidateStatus.ERROR,
        )
 
    try:
        page_text = fetch_page_text(candidate.website)
    except ValidationError as exc:
        return replace(
            candidate,
            company_formation_supported=None,
            confidence=0.0,
            verification_reason=str(exc),
            status=CandidateStatus.ERROR,
        )
    if not page_text:
        return replace(
            candidate,
            company_formation_supported=None,
            confidence=0.0,
            verification_reason="Website returned no readable text.",
            status=CandidateStatus.ERROR,
        )
 
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Page text:\n{page_text}"},
        ],
        temperature=0,
    )
    if not response.choices or not response.choices[0].message.content:
        raise ValidationError("Model returned no content.")
    content = response.choices[0].message.content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = "\n".join(content.splitlines()[1:-1])
 
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError("Model did not return valid JSON.") from exc
    if not isinstance(result, dict):
        raise ValidationError("Expected a JSON object.")
 
    supported = result.get("supported")
    if supported is not None and not isinstance(supported, bool):
        raise ValidationError("'supported' must be true, false, or null.")
    confidence = result.get("confidence", 0.0)
    if type(confidence) not in (int, float) or not 0.0 <= confidence <= 1.0:
        raise ValidationError("'confidence' must be a number between 0.0 and 1.0.")
    reason = result.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValidationError("'reason' must be a string or null.")
 
    status = CandidateStatus.RELEVANT if supported else CandidateStatus.NOT_RELEVANT
    return replace(
        candidate,
        company_formation_supported=supported,
        confidence=float(confidence),
        verification_reason=reason,
        status=CandidateStatus.VERIFIED if supported is None else status,
    )
 
 
def verify_candidates(client: Groq, candidates: list[Candidate]) -> list[Candidate]:
    """Verify each candidate; a per-candidate failure yields an ERROR status, not a crash."""
    verified: list[Candidate] = []
    for candidate in candidates:
        try:
            verified.append(verify_candidate(client, candidate))
        except (ValidationError, APIError) as exc:
            verified.append(
                replace(
                    candidate,
                    company_formation_supported=None,
                    confidence=0.0,
                    verification_reason=str(exc),
                    status=CandidateStatus.ERROR,
                )
            )
    return verified
 
 
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=None,
        help="JSON file of candidates (default: read from stdin).",
    )
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file (default: .env in the current directory).",
    )
    args = parser.parse_args()
 
    try:
        raw = args.input.read_text() if args.input else sys.stdin.read()
        candidates = [Candidate.from_dict(item) for item in json.loads(raw)]
    except (json.JSONDecodeError, ValidationError, OSError) as exc:
        print(f"Could not read candidates: {exc}", file=sys.stderr)
        return 1
 
    try:
        with build_client(args.env_file) as client:
            verified = verify_candidates(client, candidates)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except APIError:
        print("Groq request failed. Check the API key, quota, and network.", file=sys.stderr)
        return 1
 
    print(json.dumps([c.to_dict() for c in verified], indent=2, ensure_ascii=False))
    print(f"Verified {len(verified)} candidate(s).", file=sys.stderr)
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())
 