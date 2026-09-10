"""Prepare up to a target number of unique notary drafts in Lovable."""

import argparse
import json
import logging
from dataclasses import replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import NAMESPACE_URL, uuid5

from .config import load_config
from .database import connect
from .discovery import build_client, discover_notaries
from .email_generator import generate_emails
from .models import Candidate, CandidateStatus, ValidationError
from .utils import EMAIL_RE, dedupe_candidates, extract_contacts, normalize_url
from .verification import fetch_page_text, verify_candidates

CITIES = ("Stuttgart", "Tübingen", "Reutlingen", "Rottenburg am Neckar", "Esslingen",
          "Böblingen", "Sindelfingen", "Ludwigsburg", "Nürtingen", "Kirchheim unter Teck",
          "Leinfelden-Echterdingen", "Filderstadt", "Herrenberg")


def office_keys(row: dict) -> set[tuple]:
    keys = {("name", " ".join(row["name"].casefold().split()), (row.get("city") or "").casefold())}
    if row.get("email"):
        keys.add(("email", row["email"].strip().casefold()))
    if row.get("website"):
        try:
            keys.add(("domain", urlsplit(normalize_url(row["website"])).hostname.removeprefix("www.").rstrip(".")))
        except ValueError:
            pass
    return keys


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def confirm_contact(candidate: Candidate) -> tuple[Candidate, str]:
    if not candidate.website:
        raise ValidationError("No official website to confirm contact details.")
    pages = []
    contact_url = candidate.source_url if urlsplit(candidate.source_url).hostname == urlsplit(candidate.website).hostname else candidate.website
    for url in dict.fromkeys((candidate.website, contact_url, urljoin(candidate.website, "/impressum/"), urljoin(candidate.website, "/kontakt/"))):
        try:
            text = fetch_page_text(url)
        except Exception:
            continue
        pages.append(text)
        contact = extract_contacts(text)
        email = candidate.email if candidate.email and candidate.email.casefold() in text.casefold() else contact["email"]
        if email and EMAIL_RE.fullmatch(email.strip()) and candidate.city.casefold() in " ".join(pages).casefold():
            return replace(candidate, email=email.strip(), phone=candidate.phone or contact["phone"]), url
    raise ValidationError("Could not confirm both the city and contact email on the official website.")


def run(env_file: Path, target: int, checkpoint: Path, input_file: Path | None = None) -> int:
    db = connect(load_config(env_file), env_file)
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"cities": [], "candidates": []}
    seen = set()
    offset = 0
    while True:
        rows = db.table("notaries").select("id,name,city,website,email").range(offset, offset + 499).execute().data
        for row in rows:
            seen.update(office_keys(row))
        if len(rows) < 500:
            break
        offset += 500
    for row in state["candidates"]:
        seen.update(office_keys(row))
    prepared = sum(bool(row.get("id")) for row in state["candidates"])
    with build_client(env_file) as groq:
        for city in (("import",) if input_file else CITIES):
            if prepared >= target:
                break
            if city in state["cities"] and not input_file:
                continue
            print(f"Searching {city}; {prepared}/{target} drafts saved.", flush=True)
            try:
                candidates = dedupe_candidates(
                    [Candidate.from_dict(row) for row in json.loads(input_file.read_text(encoding="utf-8"))]
                    if input_file else discover_notaries(groq, city, 15)
                )
            except Exception:
                logging.error("city=%r stage=discovery error=Discovery request failed", city)
                continue
            for candidate in candidates:
                if prepared >= target:
                    break
                if candidate.city.casefold() not in {city.casefold() for city in CITIES}:
                    continue
                keys = office_keys(candidate.to_dict())
                if keys & seen:
                    continue
                seen.update(keys)
                print(f"Checking {candidate.name} ({candidate.city}).", flush=True)
                contact_source = None
                try:
                    candidate, contact_source = confirm_contact(candidate)
                    confirmed_keys = office_keys(candidate.to_dict())
                    if (confirmed_keys - keys) & seen:
                        continue
                    seen.update(confirmed_keys)
                    result = verify_candidates(groq, [candidate])[0]
                    result = generate_emails(groq, [result])[0]
                except ValidationError as exc:
                    result = replace(candidate, status=CandidateStatus.ERROR, verification_reason=str(exc))
                if result.status == CandidateStatus.REVIEW and result.company_formation_supported is True:
                    record_id = str(uuid5(NAMESPACE_URL, "notary-outreach:" + result.email.strip().casefold()))
                    payload = {field: getattr(result, field) for field in
                               ("name", "city", "website", "email", "phone", "source_url", "personalised_email")}
                    payload.update(id=record_id, status="pending")
                    try:
                        existing = db.table("notaries").select("id").eq("id", record_id).execute().data
                        if not existing:
                            db.table("notaries").insert(payload).execute()
                        result = replace(result, id=record_id)
                        prepared += 1
                        print(f"Saved draft {prepared}/{target}: {result.name}.", flush=True)
                    except Exception:
                        logging.error("candidate=%r stage=database error=Could not save draft", result.name)
                        state["candidates"].append(result.to_dict())
                        save_state(checkpoint, state)
                        raise
                state["candidates"].append({**result.to_dict(), "contact_source_url": contact_source})
                save_state(checkpoint, state)
            state["cities"].append(city)
            save_state(checkpoint, state)
    print(f"Prepared {prepared}/{target} drafts in Lovable. Evidence and other results: {checkpoint}")
    print("No emails sent.")
    return 0 if prepared >= target else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=30)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--checkpoint", type=Path, default=Path("runs/stuttgart-tuebingen.json"))
    parser.add_argument("--input", type=Path, help="Use sourced candidate JSON instead of Groq discovery.")
    args = parser.parse_args()
    if not 1 <= args.target <= 30:
        parser.error("--target must be between 1 and 30 for this pilot.")
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.ERROR)
    try:
        return run(args.env_file, args.target, args.checkpoint, args.input)
    except Exception:
        logging.error("Workflow stopped. Check configuration and network; completed candidates remain in the checkpoint.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
