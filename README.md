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

## License

[MIT](LICENSE)
