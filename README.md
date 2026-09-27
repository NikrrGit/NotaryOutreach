# Outreach

A local app for finding notaries or venture capital investors, checking official website evidence, and preparing personalized German email drafts for human review. It does not send emails.

## Setup

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and a Groq API key for research and generation.

```sh
uv sync
cp .env.example .env
# Set GROQ_API_KEY in .env
uv run streamlit run src/ui/app.py
```

| Setting | Default / purpose |
| --- | --- |
| `GROQ_API_KEY` | Required for agent calls; not needed to browse saved results |
| `DATABASE_PATH` | `data/outreach.db` — jobs, candidates, evidence, drafts, evaluations, reviews |
| `CHECKPOINT_PATH` | `runs/checkpoints.sqlite3` — workflow progress and recovery |

Environment variables override `.env`. Both databases are local files. Keep them on persistent storage and retain both when backing up or moving a project.

## Use

Choose Notary or Venture Capital in Streamlit, enter the search criteria, and start the search. You can also save settings and run them later. Interrupted searches offer a resume control when a checkpoint is available.

Inspect candidate evidence and evaluation results before approving a draft. Editing creates a new draft version requiring fresh evaluation and approval. Approval records a human decision; it never sends email.

CLI examples:

```sh
uv run outreach search --type notary --location Berlin --company-type UG --limit 10
uv run outreach search --type vc --location Germany \
  --description "Security software for SMEs" --industry Cybersecurity --stage Seed --limit 10
uv run outreach jobs
uv run outreach show <job-id>
uv run outreach run <job-id>
uv run outreach resume <job-id>
```

`run` starts a saved job without a checkpoint. `resume` continues an interrupted workflow or reloads a completed one. Commands print JSON to stdout and diagnostics to stderr. `notaryoutreach` remains an alias for `outreach`.

Use `--env-file`, `--database`, and `--checkpoint-path` to override CLI settings. The former `--stage` interface and legacy database/workflow modules have been removed.

## Implementation

Streamlit and CLI call `OutreachService`, which coordinates one LangGraph workflow and four shared agents: discovery, verification, email writing, and evaluation. `target_type` selects Notary or VC research criteria. The service saves results in SQLite and retains full verification history and workflow errors in graph checkpoints.

The service allows one workflow or re-evaluation at a time within a process. Run one application process against these files. Resuming may repeat an interrupted external call; stable record IDs prevent duplicate persisted results. A finished job requiring manual review is not automatically restarted by resume.

Missing or inconclusive evidence remains unknown. Confidence is a model assessment, not a calibrated probability. Discovery contacts still need review, and Notary searches do not enforce an exact distance radius. No email delivery or follow-up automation is included.

## Tests

```sh
PYTHONPATH=src uv run python -m unittest discover -s tests
uv run python -m notaryoutreach.config --require-api-key
```

The tests use temporary databases and mocked providers. The configuration command validates settings without making an external connection.

## License

[MIT](LICENSE)
