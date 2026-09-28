# Outreach

Find notaries for company formation or venture capital investors for your startup, then prepare evidence-based outreach drafts for human review.

Outreach reduces the manual work of finding candidates, checking their websites, and writing a relevant first email. It collects sources, checks suitability, and generates personalized German email drafts in one local app. **It never sends emails.**

| Search | What you provide | What it checks |
| --- | --- | --- |
| Notary | Location, UG or GmbH, result count | Evidence of company-formation services |
| Venture Capital | Geography, startup description, industry, funding stage, result count | Evidence of investment fit |

## Quick start

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and an API key from Groq, OpenAI, or Anthropic. The project uses Python 3.13.

```sh
git clone https://github.com/NikrrGit/NotaryOutreach.git
cd NotaryOutreach
uv python install 3.13
uv sync --locked
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env` for the last command.

Open `.env`, choose a provider, and set its key. For example:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
```

Start the app from the project directory:

```sh
uv run streamlit run src/ui/app.py --server.address 127.0.0.1
```

Open the local URL printed in the terminal (usually <http://localhost:8501>). Press `Ctrl+C` to stop. The app creates the database files automatically; no database server or manual migration command is needed.

## Choose your provider

Set `LLM_PROVIDER` and its matching API key in `.env`; no code changes are needed.

| `LLM_PROVIDER` | Key | Default generation model | Default live search model |
| --- | --- | --- | --- |
| `groq` (default) | `GROQ_API_KEY` | `openai/gpt-oss-20b` | `groq/compound` |
| `openai` | `OPENAI_API_KEY` | `gpt-4.1-mini` | `gpt-4.1-mini` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | `claude-sonnet-4-6` |

For Anthropic, for example:

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key
```

Existing Groq-only `.env` files still work. Set `LLM_MODEL` to override generation and `SEARCH_MODEL` to override discovery. The search model must support its provider's native web-search tool, and your account must have access. There is no automatic fallback to another provider. Restart the app after changing settings.

OpenAI and Anthropic discovery use [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search) and [Anthropic web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool), followed by a separate call to format the research as JSON. Web-search charges and token usage apply.

### Other OpenAI-compatible endpoints

Use a Chat Completions-compatible endpoint for verification, drafting, and evaluation. It must follow JSON instructions; the app validates responses locally. Set a native search provider separately because a generic chat endpoint does not supply live web research:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=your_endpoint_api_key
LLM_MODEL=your_model_id
SEARCH_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
```

Replace the endpoint and model with your provider's values. Local servers can use an HTTP URL and a nonempty placeholder key if they do not require authentication. This supports compatible APIs, not arbitrary SDKs. `SEARCH_PROVIDER` can be `groq`, `openai`, or `anthropic`; provide that provider's key too.

## Run your first search

1. Choose **Notary** or **Venture Capital** and enter the search criteria.
2. Select **Start search**, or **Save search** to run it later.
3. Open a candidate and inspect the evidence, sources, draft, and evaluation.
4. Edit and evaluate the draft, then approve or reject it. Approval requires a passing evaluation.

Edits create a new draft version that needs fresh evaluation and approval. Previous versions and review decisions are retained. Approval records your decision locally; copy the reviewed draft into your email client when you are ready to contact someone.

## How it works

Both search modes use the same service and four agents. The selected target changes the research and verification criteria.

```mermaid
flowchart TD
    UI["Streamlit: search and human review"] --> Service["OutreachService"]
    CLI["CLI"] --> Service
    Service --> Workflow
    subgraph Workflow["Shared LangGraph workflow"]
        Discover["Discover and deduplicate"] --> Verify["Verify evidence and validate"]
        Verify --> Write["Write email draft"]
        Write --> Evaluate["Evaluate draft"]
    end
    Evaluate -->|Results| Service
    Service <--> Data[("SQLite: results and reviews")]
    Workflow --- Checkpoints[("SQLite: workflow checkpoints")]
```

The service saves jobs, candidates, verifications, drafts, evaluations, and reviews. Checkpoints track workflow progress for recovery. Verification uses website evidence; missing or inconclusive evidence stays unknown.

## Command line

Run these commands from the project directory. Searches create and execute a saved job.

```sh
uv run outreach search --type notary --location Berlin --company-type UG --limit 10

uv run outreach search --type vc --location Germany --description "Security software for SMEs" --industry Cybersecurity --stage Seed --limit 10

uv run outreach jobs
```

Replace `JOB_ID` below with the ID printed when a search starts or returned by `jobs`:

```sh
uv run outreach show JOB_ID
uv run outreach run JOB_ID
uv run outreach resume JOB_ID
```

| Command | Purpose |
| --- | --- |
| `show` | Load saved results |
| `run` | Start a saved job that has no checkpoint |
| `resume` | Continue an interrupted workflow, or reload a completed one |

Commands return JSON on stdout and diagnostics on stderr. `notaryoutreach` is an alias for `outreach`. Use `uv run outreach --help` for options; edit and review drafts in Streamlit.

## Configuration and local data

| Setting | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `groq` | Provider for verification, drafting, and evaluation |
| Provider API key | None | Use the matching key above; saved results can be browsed without it |
| `LLM_MODEL` | Provider default | Generation model override |
| `SEARCH_PROVIDER` | Same as `LLM_PROVIDER` | Native provider for discovery |
| `SEARCH_MODEL` | Search provider default | Live search model override |
| `LLM_BASE_URL`, `LLM_API_KEY` | None | Custom endpoint URL and key; used only for `openai_compatible` |
| `DATABASE_PATH` | `data/outreach.db` | Application results and review history |
| `CHECKPOINT_PATH` | `runs/checkpoints.sqlite3` | Workflow progress and recovery |

Environment variables override `.env`. CLI flags `--env-file`, `--database`, and `--checkpoint-path` select another configuration file or override database paths. Relative paths are resolved from the current working directory.

Results are stored locally, but agent calls send search details, startup descriptions, and relevant content to your selected generation and search providers. Research also accesses external websites. Internet access and provider usage limits apply. `.env` and local databases are excluded from Git; keep credentials private.

**Backups:** Stop the app before copying both databases and any SQLite sidecar files. Restore both together. Preserve the application database's original absolute path when restoring resumable jobs: checkpoint identities include that path.

## Recovery and limitations

- **Interrupted search:** Select **Resume search** in Streamlit when available, or use `outreach resume JOB_ID`. If no checkpoint exists, use **Start saved search** or `outreach run JOB_ID`.
- **Missing key:** Set the API key matching `LLM_PROVIDER` (and `SEARCH_PROVIDER` if different) in `.env` and restart the app. The configuration check below validates settings; it does not test the key against the provider.
- **No draft or manual review required:** Inspect the recorded errors and evidence. A completed `manual_review` job does not restart through resume; correct the input or external issue and create a new search if needed.
- **One local user:** Run one application process against the database files. Avoid running CLI workflows alongside Streamlit on the same files. Authentication, background workers, and multi-user hosting are outside this MVP.

Recovery may repeat an interrupted external call; stable record IDs prevent duplicate persisted results. The requested result count is a discovery target, not a guarantee of eligible candidates or drafts. Contacts and model assessments need human review, confidence scores are not calibrated probabilities, and Notary searches do not enforce an exact distance radius.

## Development checks

```sh
uv run python -m notaryoutreach.config --require-api-key
uv run python -m unittest discover -s tests
```

Configuration validation makes no external connection. Tests use temporary databases and mocked providers to cover both workflows, failures, recovery, persistence, and review preservation; they do not verify live provider availability or research quality.

Provider changes apply to new calls, including resumed jobs and re-evaluation; existing saved results remain unchanged.

The main code lives in `src/providers/`, `src/agents/`, `src/graph/`, `src/services/`, `src/storage/`, and `src/ui/`. The initial schema is packaged in `src/db/migrations/001_initial.sql`.

## License

[MIT](LICENSE)
