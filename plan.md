# Notary Outreach Agent — V1

## Objective

Build a local Python agent that:

1. Finds 50–80 notaries around Stuttgart/Tübingen and nearby cities.
2. Finds their official website and professional contact details.
3. Verifies whether they are suitable for forming a UG (haftungsbeschränkt).
4. Generates a short personalised German email asking for the earliest available appointment.
5. Stores everything in Supabase.
6. Displays results in the existing Lovable frontend.
7. Allows me to edit, approve or reject each email.

V1 must NOT automatically send emails.

---

# Architecture

```text
Local Python Application
        │
        ▼
Groq Discovery
        │
        ▼
50–80 Notaries
        │
        ▼
Python Deduplication
        │
        ▼
Groq Verification
        │
        ▼
Groq Email Generation
        │
        ▼
Supabase
        │
        ▼
Lovable Frontend
        │
        ▼
Human Review
Approve / Edit / Reject
```

Lovable and the local Python application communicate only through Supabase.

The Python application remains local. Do NOT deploy it in V1.

---

# Stack

Use:

* Python 3
* Groq Python SDK
* Groq Compound for web discovery
* Groq-hosted model for verification/email generation
* Supabase
* Lovable
* python-dotenv
* requests/BeautifulSoup only if necessary

Do NOT use:

* LangChain
* LangGraph
* CrewAI
* AutoGen
* MCP
* Docker
* Kubernetes
* Redis
* Celery
* Kafka
* Vector databases
* Microservices

Keep V1 as a simple Python workflow.

---

# Environment Variables

Use `.env`.

Conceptually:

```text
GROQ_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=
```

Never hardcode secrets.

Create `.env.example`.

---

# Stage 1 — Supabase Connection

First connect the local Python application to the existing Supabase project used by Lovable.

Before creating tables, inspect the existing Supabase schema.

Prefer modifying/reusing the existing `notaries` table instead of creating duplicate tables.

Required fields should be equivalent to:

```text
id
name
city
website
email
phone

source_url

company_formation_supported
confidence
verification_reason

personalised_email

status

created_at
updated_at
```

Possible statuses:

```text
discovered
verified
relevant
not_relevant
email_generated
review
approved
rejected
error
```

First test:

```text
Python
  ↓
Insert test record
  ↓
Supabase
  ↓
Lovable
  ↓
Record visible
```

Do not continue until this works.

---

# Stage 2 — Notary Discovery

Use Groq Compound for live web discovery.

Start with `groq/compound-mini`.

Search city-by-city.

Target locations:

```text
Stuttgart
Tübingen
Reutlingen
Esslingen
Böblingen
Sindelfingen
Ludwigsburg
Nürtingen
Kirchheim unter Teck
```

Example search intent:

```text
Notar UG Gründung Stuttgart
Notar GmbH Gründung Stuttgart
Notar Gesellschaftsrecht Stuttgart

Notar UG Tübingen
Notar Gesellschaftsrecht Tübingen

Notar GmbH Reutlingen
Notar Gesellschaftsrecht Esslingen
```

Prefer:

1. Official German notary directory
2. Official notary websites
3. Reliable search results

Target approximately:

```text
100–120 raw results
        ↓
deduplication
        ↓
50–80 unique candidates
```

For each candidate collect:

```text
name
city
website
email
phone
source_url
```

Never invent missing information.

---

# Stage 3 — Deduplication

Use normal Python, NOT an LLM.

Deduplicate using:

* website domain
* email
* name
* office/address when available

Multiple notaries from the same office should not result in unnecessary duplicate outreach.

Store unique candidates in Supabase.

---

# Stage 4 — UG Compatibility Verification

For every unique candidate, determine whether the notary appears suitable for forming a:

```text
UG (haftungsbeschränkt)
```

Look for evidence such as:

```text
UG
GmbH
Gesellschaftsrecht
Gesellschaftsgründung
Unternehmensgründung
Handelsregister
Kapitalgesellschaft
```

Prefer evidence from the official notary website.

Return structured data:

```text
company_formation_supported:
    true | false | unknown

confidence:
    0.0 - 1.0

verification_reason:
    short explanation

source_url:
    evidence URL
```

Example:

```text
company_formation_supported = true

confidence = 0.94

verification_reason =
"Official website explicitly lists Gesellschaftsgründungen
and GmbH formations."

source_url =
"https://..."
```

Never claim compatibility without evidence.

If uncertain:

```text
company_formation_supported = unknown
```

Do not invent information.

---

# Stage 5 — Email Generation

Generate emails only for:

```text
company_formation_supported = true
```

Optionally include high-confidence `unknown` candidates later.

Generate a professional German email of approximately 60–120 words.

Goal:

Ask whether the notary can handle our UG formation and what their earliest available appointment is.

The email should contain:

```text
UG (haftungsbeschränkt)

request for formation/notarisation

request for earliest possible appointment
```

Lightly personalise using verified website information.

Example intent:

```text
Guten Tag Herr/Frau ...,

wir befinden uns derzeit in der Gründung einer
UG (haftungsbeschränkt) und suchen hierfür einen
möglichst zeitnahen Notartermin.

Da Sie auf Ihrer Website gesellschaftsrechtliche
Beurkundungen anbieten, möchten wir gerne anfragen,
wann bei Ihnen der frühestmögliche Termin für die
Gründung verfügbar wäre.

Vielen Dank und freundliche Grüße
...
```

Do NOT invent:

* titles
* expertise
* languages
* availability
* services
* personal information

Accuracy > creativity.

---

# Stage 6 — Store Results

Save the completed record to Supabase.

Example final record:

```text
Name:
Dr. Example

City:
Stuttgart

Website:
https://...

Email:
kanzlei@...

UG compatible:
true

Confidence:
0.94

Reason:
Website mentions Gesellschaftsgründung and GmbH.

Generated email:
...

Status:
review
```

---

# Stage 7 — Lovable Dashboard

Lovable should read directly from Supabase.

The dashboard should show:

```text
Total discovered
Verified
Relevant
Needs review
Approved
Rejected
Errors
```

For each notary display:

```text
Name
City
Website
Email

UG compatibility
Confidence
Verification reason
Evidence/source

Generated email
```

Provide:

```text
[Edit Email]

[Approve]

[Reject]
```

Changing the status should update Supabase.

No email sending in V1.

---

# Workflow

The complete V1 workflow is:

```text
START

  ↓

Search Stuttgart

  ↓

Search surrounding cities

  ↓

Collect ~100 raw candidates

  ↓

Python deduplication

  ↓

50–80 unique candidates

  ↓

Verify official website

  ↓

Check UG/company formation compatibility

  ↓

Generate email for relevant candidates

  ↓

Save to Supabase

  ↓

Lovable dashboard

  ↓

Human review

  ├── Approve
  ├── Edit
  └── Reject

STOP
```

---

# Failure Handling

One failed website must NOT stop the workflow.

For each candidate:

```text
try
    process candidate
except
    log error
    status = error
    continue
```

Log:

```text
candidate
stage
error
timestamp
```

Continue processing remaining candidates.

---

# Cost Control

Minimise Groq usage.

Use deterministic Python for:

```text
deduplication
URL handling
email extraction
database operations
filtering
status management
```

Use Groq for:

```text
web discovery
semantic verification
email generation
```

Do not repeatedly process candidates already completed in Supabase.

Do not send entire websites to the LLM unnecessarily.

---

# Project Structure

Keep the repository small.

Suggested structure:

```text
notary-agent/

├── main.py
├── config.py
│
├── discovery.py
├── verification.py
├── email_generator.py
├── database.py
├── models.py
├── utils.py
│
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Do not create unnecessary abstractions or additional services.

---

# Development Order

Implement strictly in this order:

### Phase 1

Supabase connection.

Test:

```text
Python → Supabase → Lovable
```

STOP and verify.

### Phase 2

Groq discovery for Stuttgart only.

Find approximately 10 notaries.

STOP and inspect results.

### Phase 3

Deduplication + website/contact extraction.

Store the 10 candidates in Supabase.

STOP and inspect results.

### Phase 4

UG compatibility verification.

Run against those 10 candidates.

STOP and inspect accuracy.

### Phase 5

Generate personalised emails.

Store them in Supabase.

STOP and inspect email quality.

### Phase 6

Scale discovery to all target cities.

Target 50–80 verified unique candidates.

### Phase 7

Finalize Lovable review UI.

Approve / Edit / Reject.

---

# Important Rules

1. Do not automatically send emails.
2. Do not invent information.
3. Prefer official notary websites as evidence.
4. Store source URLs.
5. Human approval is required.
6. Do not introduce an agent framework.
7. Do not deploy the Python worker.
8. Do not overengineer the architecture.
9. Make each phase independently testable.
10. Do not implement future phases until the current phase works.

---

# Instructions for Codex

Work one phase at a time.

For each phase:

1. Briefly explain what we are building.
2. List files being created/modified.
3. Implement it.
4. Give me the exact command to run.
5. Tell me what successful output should look like.
6. Stop.

Do not generate the entire application at once.

Start now with **Phase 1 only: connecting the local Python project to the existing Supabase database used by Lovable.**
