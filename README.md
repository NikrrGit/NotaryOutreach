# Notary Outreach

A Python CLI for finding notaries and preparing German email enquiries about UG formation. It checks official websites for relevant services, removes duplicate contacts, and saves drafts to Supabase for review in Lovable. It does not send emails.

## Setup

Requires Python 3.13+, uv, a Groq API key, and an existing Supabase project.

```sh
uv sync
cp .env.example .env
```

Fill in `.env`:

| Variable | Value |
| --- | --- |
| `GROQ_API_KEY` | Groq API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase API key |
| `SUPABASE_EMAIL` | Supabase Auth user email |
| `SUPABASE_PASSWORD` | Supabase Auth user password |

The workflow expects a `public.notaries` table with `id`, `name`, `city`, `website`, `email`, `phone`, `source_url`, `personalised_email`, `status`, and `created_at` columns. The authenticated user needs read and insert access, and the table must accept `pending` as a status. Database provisioning and the Lovable interface are maintained separately.

Check the connection:

```sh
uv run notaryoutreach --stage connect
```

## Usage

Prepare up to 30 drafts across Stuttgart, Tübingen, and nearby towns:

```sh
uv run python -m notaryoutreach.workflow --target 30
```

The workflow confirms contact details, checks for company formation evidence, and saves eligible drafts with status `pending`. Progress and evidence are recorded in `runs/stuttgart-tuebingen.json`; rerunning the command resumes from that checkpoint.

To inspect each stage separately:

```sh
uv run notaryoutreach --stage discover Stuttgart --limit 10 > candidates.json
uv run notaryoutreach --stage dedupe --input candidates.json > unique.json
uv run notaryoutreach --stage verify --input unique.json > verified.json
uv run notaryoutreach --stage email --input verified.json > drafts.json
```

These stages return JSON without writing to Supabase. Discovery, verification, and drafting use Groq. The email template and sender signature are defined in [email_generator.py](src/notaryoutreach/email_generator.py).

## Discovery agent

`agents.discovery.DiscoveryAgent` provides batched discovery for a location, company type (`UG` or `GmbH`), and result limit. It returns validated candidate records and removes duplicate offices by domain, email, or name and city. Groq is the default search provider; credentials are read from `.env` or the environment.

Search attempts are bounded. If a batch fails, `DiscoveryError.candidates` contains the results from earlier batches. Contact details and service hints still require verification. Nearby locations are requested in the search prompt; an exact distance radius is not enforced.

This agent is separate from the existing CLI workflow. LangGraph integration is pending.

## Verification agent

`agents.verifier.VerificationAgent` checks discovery candidates against their official website and linked service pages. Use `verify(candidate, company_type)` for one office or `verify_candidates(candidates, company_type)` for a batch, with `UG` or `GmbH` as the company type.

Each result includes the candidate, status (`supported`, `unsupported`, or `unknown`), confidence, reasoning, evidence quote, source URL, reviewed pages, and errors. Definite results require a quote found on the cited page. Missing or inconclusive evidence stays `unknown`; `unsupported` is reserved for an explicit statement that the service is not offered. Confidence is a model assessment, not a calibrated probability.

By default, the agent attempts up to four pages per office and requires confidence of at least 0.8 for a definite result. Failed pages and model responses are recorded per candidate, and batch processing continues. Groq credentials come from `.env` or the environment. The agent is separate from the existing CLI and awaits LangGraph integration.

## Email writer handoff

`EmailWriter.write_verified()` accepts a `VerificationResult` and returns an
`EmailDraft`. It rejects results unless their status is `supported`, their
reasoning and evidence quote are nonempty, and their evidence source is a valid
HTTP(S) URL. The verifier remains responsible for matching evidence and applying
its configured confidence threshold.

With an initialized verifier, candidate, and provider implementing
`generate_structured()`:

```python
from agents.email_writer import EmailWriter

result = verifier.verify(candidate, "UG")
if result.status == "supported":
    draft = EmailWriter(provider).write_verified(result, sender_name="Alex")
```

`EmailWriterInput.from_verification()` exposes the same validated conversion
separately. It uses the verification source and requested company type, rather
than discovery hints. Discovery contact details are carried through without
additional contact verification; missing email addresses do not prevent drafting.
The existing `writer(data)` method remains a low-level entry point for callers
that already have verified input. Drafts are not sent or persisted by this handoff;
LangGraph integration remains pending.

Run the offline tests:

```sh
uv run python -m unittest discover -s tests
```

## License

[MIT](LICENSE)
