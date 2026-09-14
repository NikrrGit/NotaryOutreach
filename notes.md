# LangGraph Notes

These notes document the main architectural concepts behind the first files in the LangGraph-based version of the project.

The goal is not only to remember **what the code does**, but also:

* why the component exists;
* why it is designed this way;
* what problem it solves;
* what principles can be reused in other AI systems.

---

# 1. `agents/discovery.py`

## Purpose

The discovery agent is responsible for finding potential notaries based on user input.

Input:

```text
location
company type
requested result count
```

Example:

```text
location = Stuttgart
company_type = UG
limit = 50
```

Output:

```text
Candidate[]
```

Each candidate contains information such as:

```text
name
city
website
email
phone
source URL
```

The discovery agent's responsibility ends after finding potential candidates.

It does **not** determine whether a notary is definitely suitable for forming a UG or GmbH.

That responsibility belongs to the verification agent.

---

# First Principle: Why Discovery Exists

Initially, the application knows something like:

```text
Stuttgart
+
UG
```

But it needs:

```text
real notaries
+
their websites
+
their contact information
```

There is therefore an information gap:

```text
Known information
      ↓
Stuttgart + UG

      ???

      ↓

Actual notaries
```

The discovery agent exists to close that gap.

Its job is:

```text
query
   ↓
internet research
   ↓
potential entities
```

---

# Discovery and Verification Must Be Separate

A major architectural rule is:

```text
Discovery ≠ Verification
```

Discovery answers:

> Which notaries might be relevant?

Verification answers:

> Is this specific notary actually suitable?

For example, discovery might find:

```text
Müller Notare
Stuttgart

Website mentions:
Gesellschaftsrecht
```

This does not automatically prove that the notary handles UG formation.

A poor system might reason:

```text
Gesellschaftsrecht
      ↓
probably company formation
      ↓
probably UG
      ↓
supports_UG = true
```

That converts assumptions into facts.

Instead:

```text
Discovery

"I found Müller Notare."

          ↓

Verification

"Find evidence that Müller Notare
can handle UG/GmbH formation."
```

This separation reduces hallucinations.

---

# Recall vs Precision

A useful mental model:

## Discovery should maximize recall

The goal is to avoid missing good candidates.

It is acceptable for discovery to return some candidates that later turn out to be unsuitable.

## Verification should maximize precision

The verification step should be conservative.

A candidate should only pass when there is evidence.

Therefore:

```text
Discovery → broad

Verification → strict
```

---

# Why Use Web Search

A normal LLM only knows information contained in or derived from its training data.

But this project requires current information such as:

```text
current notary websites
current email addresses
current office locations
current services
```

These may change.

Therefore:

```text
LLM only
   ✗

LLM + live web access
   ✓
```

The search agent needs tools that can obtain current information.

---

# Why Groq Compound Is Used

Groq Compound can combine reasoning with tools such as:

```text
web search
website retrieval
```

The model can therefore perform operations like:

```text
search
   ↓
inspect result
   ↓
visit website
   ↓
extract contact information
```

This is more useful for discovery than a normal completion model.

---

# Why Discovery Happens in Batches

It would be possible to ask:

```text
Find 80 notaries.
```

in a single LLM request.

However, this tends to produce lower-quality results.

Large requests often cause:

```text
more duplicates
missing contact information
missing sources
less careful research
```

Instead, perform smaller searches:

```text
find 10
   ↓
deduplicate

find another 10
   ↓
deduplicate

find another 10
```

Continue until:

```text
unique candidates >= requested number
```

This pattern is called:

```text
bounded iterative discovery
```

---

# Why Python Controls the Loop

An LLM should not decide things that normal code can determine exactly.

For example:

```python
if len(candidates) >= limit:
    break
```

This decision does not require reasoning.

If:

```text
requested = 50
found = 50
```

then the workflow should stop.

The general rule is:

> If a decision can be made deterministically, use normal code instead of an LLM.

Use an LLM for ambiguity.

Use normal code for certainty.

---

# Stop When No Progress Is Made

Suppose the application already has:

```text
40 candidates
```

Another search returns only those same candidates.

Then:

```text
new information = 0
```

Continuing to call the API would waste:

```text
time
tokens
money
```

Therefore the discovery loop should stop when no new candidates are found.

Conceptually:

```text
previous candidates = 40

new search
      ↓

unique candidates = 40

      ↓

no progress

      ↓

STOP
```

This is an important principle in retry systems too:

> Do not continue retrying when retries are producing no useful progress.

---

# Why `max_attempts` Exists

External APIs can behave unpredictably.

Without a hard boundary, code could accidentally create:

```text
search
 ↓
retry
 ↓
retry
 ↓
retry
 ↓
retry forever
```

Therefore there should always be an upper bound:

```text
max_attempts
```

Example:

```text
maximum discovery requests = 10
```

This makes the system:

```text
bounded
predictable
cheaper
safer
```

---

# Structured Candidate Objects

The LLM should not return human-readable paragraphs such as:

```text
Here are some notaries I found...
```

The application needs machine-readable data.

Therefore the agent returns objects like:

```text
Candidate
├── name
├── city
├── website
├── email
├── phone
└── source_url
```

This allows downstream code to use:

```python
candidate.email
candidate.website
candidate.city
```

without parsing prose.

---

# Treat LLM Output Like an External API

An LLM is outside the trusted boundary of the application.

Its output should therefore be treated similarly to:

```text
external API response
webhook
user input
Kafka message
uploaded file
```

The application should never blindly trust it.

The flow should be:

```text
LLM output
    ↓
validation
    ↓
trusted application object
```

---

# Why Pydantic Is Important

Pydantic validates the structure returned by the model.

For example, the application expects:

```json
{
  "name": "Müller Notare"
}
```

But the model might return:

```json
{
  "notary_name": "Müller Notare"
}
```

or miss required fields entirely.

Pydantic acts as a boundary:

```text
untrusted output
      ↓
Pydantic
      ↓
valid Python model
```

If the output does not match the schema, it should fail clearly instead of silently corrupting the workflow.

General principle:

> Validate data whenever it crosses a system boundary.

---

# Why `source_url` Is Required

A research system should not only return a fact.

It should return:

```text
fact
+
where the fact came from
```

For example:

```text
email:
info@example-notar.de

source:
https://example-notar.de/kontakt
```

This is called:

```text
provenance
```

Provenance is critical because later stages need to evaluate whether the information is trustworthy.

The evaluator and human user should always be able to answer:

> Where did this information come from?

---

# Observation vs Conclusion

Discovery may observe:

```text
Website mentions Gesellschaftsrecht
```

That is useful.

But it should not convert this into:

```text
supports_ug = true
```

The correct distinction is:

```text
Observation:
"Gesellschaftsrecht mentioned"

Conclusion:
"This notary can form a UG."
```

These are not the same thing.

A useful engineering principle is:

> Preserve raw observations separately from derived conclusions.

---

# Why Deduplication Is Done in Python

Suppose search returns:

```text
https://www.example-notar.de

https://example-notar.de
```

These are probably the same website.

Python can deterministically normalize the domains.

There is no reason to ask an LLM:

> Are these duplicates?

The discovery system can compare:

```text
domain
email
name + city
```

This is cheaper and more reliable.

Again:

> Use AI only where uncertainty requires intelligence.

---

# Why Dependency Injection Matters

Instead of creating the Groq client inside every method, the class accepts a client.

Conceptually:

```python
DiscoveryAgent(client=...)
```

Production can use:

```text
real Groq client
```

Tests can use:

```text
fake client
```

This means unit tests do not need to:

```text
access the internet
use API credits
depend on Groq uptime
```

This principle is called:

```text
dependency injection
```

It also makes the future provider architecture easier.

Eventually:

```text
DiscoveryAgent
      ↓
SearchProvider
      ↓
Groq / Tavily / another provider
```

---

# Least Privilege for Agents

The discovery agent only needs:

```text
search internet
read websites
```

Therefore it should only receive those tools.

It does not need:

```text
database deletion
email sending
calendar access
code execution
```

General principle:

> Give an agent only the capabilities required to complete its responsibility.

This limits accidental actions and reduces security risk.

---

# Discovery Mental Model

When implementing discovery from scratch, think:

```text
INPUT
location + company type + result count

        ↓

UNKNOWN
Which notaries exist?

        ↓

TOOL
Web search

        ↓

UNTRUSTED DATA
Search + LLM output

        ↓

VALIDATION
Pydantic

        ↓

DETERMINISTIC CONTROL
dedupe
limits
stop conditions

        ↓

OUTPUT
Candidate[]
```

This pattern can be reused in many AI applications:

```text
Input
 ↓
AI handles uncertainty
 ↓
structured output
 ↓
validation
 ↓
normal application logic
```

---

# 2. `graph/state.py`

## Purpose

`state.py` defines the shared data structure that flows through the LangGraph workflow.

The graph contains multiple nodes:

```text
Discovery
    ↓
Verification
    ↓
Email Writer
    ↓
Evaluator
```

Each node needs access to information produced by previous nodes.

The state provides that shared memory.

---

# First Principle: Why State Exists

Without shared state:

```text
Discovery Agent
     ↓
finds candidates

Verification Agent
     ↓
does not know what Discovery found
```

The application therefore needs something that travels through the workflow:

```text
WorkflowState
```

Conceptually:

```text
WorkflowState
├── search settings
├── candidates
├── verification results
├── email drafts
├── evaluation results
├── errors
└── retry counts
```

Every graph node receives the current state.

---

# State Is the Workflow's Memory

Think of LangGraph as a function:

```text
State₀
  ↓
Node A
  ↓
State₁
  ↓
Node B
  ↓
State₂
  ↓
Node C
  ↓
State₃
```

The graph does not need agents to communicate directly with each other.

They communicate indirectly through state.

Example:

```text
Discovery
    ↓
writes candidates to state

Verification
    ↓
reads candidates from state

Verification
    ↓
writes verification results

Email Writer
    ↓
reads verification results
```

---

# Nodes Should Return State Updates

A node should not recreate the entire workflow state.

For example:

```python
def discovery_node(state):
    candidates = discover(...)

    return {
        "candidates": candidates
    }
```

The node only returns:

```text
what changed
```

LangGraph then merges that update into the existing state.

This keeps nodes:

```text
small
focused
easy to test
```

---

# Search Settings

The workflow input should be represented explicitly.

Example:

```text
SearchSettings
├── location
├── company_type
├── target_count
├── radius_km
└── language
```

This is better than passing unrelated function arguments throughout the graph.

Instead of:

```text
location
company_type
radius
language
count
```

being passed everywhere independently, all search configuration is contained in one object.

---

# Why Structured Search Settings Matter

Imagine later adding:

```text
online notarisation preference
maximum distance
preferred language
company type
```

Without a structured object, function signatures become increasingly messy.

Instead:

```text
SearchSettings
```

becomes the stable input contract.

---

# Workflow State Contains Data, Not Infrastructure

State should contain:

```text
candidates
results
errors
configuration
retry information
```

State should NOT contain:

```text
Groq client
database connection
API keys
HTTP session
Supabase credentials
```

Why?

Because workflow state represents:

```text
the data required to understand execution
```

Infrastructure objects belong in dependency/configuration layers.

A useful question is:

> If I persisted this workflow and resumed it tomorrow, would this value make sense to store?

For:

```text
candidates
retry count
verification result
```

the answer is yes.

For:

```text
Groq client object
```

the answer is no.

---

# Why Reducers Exist

A LangGraph state field can have a reducer.

Example:

```python
Annotated[list[Candidate], operator.add]
```

This tells LangGraph how multiple updates should be combined.

Suppose the current state contains:

```text
[A, B]
```

and another node returns:

```text
[C, D]
```

Without an append reducer, the list might become:

```text
[C, D]
```

With:

```text
operator.add
```

the result becomes:

```text
[A, B, C, D]
```

---

# Reducer Mental Model

A reducer answers:

> If the state already has a value and a node returns another value, how should these values be combined?

Example:

```text
old value
+
new value
=
next state value
```

For accumulating lists:

```text
[A, B] + [C]
=
[A, B, C]
```

---

# When to Use Reducers

Reducers are useful for append-only information such as:

```text
errors
events
evaluation records
```

Be careful using reducers for data that may be regenerated.

For example, if verification is retried:

```text
attempt 1 verification result
+
attempt 2 verification result
```

you need to decide whether you want:

```text
both attempts
```

or:

```text
only the newest result
```

This is an important design decision.

Reducers should match the semantics of the data.

---

# Why Errors Belong in State

A production workflow should not necessarily crash because one external operation fails.

For example:

```text
Candidate A → verification success

Candidate B → website timeout

Candidate C → verification success
```

If one failure terminates everything, the batch becomes fragile.

Instead, record:

```text
WorkflowError
├── node
├── candidate_id
├── message
└── retryable
```

Then execution can continue.

---

# Structured Errors Are Better Than Strings

Bad:

```python
"errors": [
    "something failed"
]
```

Better:

```text
node = verification

candidate_id = 123

message = website timeout

retryable = true
```

Structured errors enable the workflow to make decisions.

For example:

```text
retryable = true
      ↓
retry

retryable = false
      ↓
skip/manual review
```

---

# Retry Counts Must Be Part of State

Imagine this graph:

```text
Verification
     ↓
FAIL
     ↓
retry Verification
```

If the workflow does not remember how many times it retried, it can loop forever.

Therefore state contains:

```text
retry_counts
```

Example:

```text
verification = 0
```

After failure:

```text
verification = 1
```

Another failure:

```text
verification = 2
```

Then:

```text
2 >= MAX_RETRIES
```

Route to:

```text
manual review
```

instead of retrying again.

---

# First Principle of Retry State

A workflow decision needs memory.

To answer:

> Should I retry again?

the system needs to know:

```text
how many attempts already happened?
```

Therefore the retry count belongs in workflow state.

General principle:

> Anything required to determine what happens next belongs in workflow state.

---

# State Enables Conditional Routing

Later, the graph may contain logic such as:

```text
verification result
        ↓
confidence >= 0.8?
       / \
     yes  no
      |    |
 email    retry
```

The router does not need to call the LLM again.

It simply reads state:

```text
state["verification_results"]
state["retry_counts"]
```

and makes a deterministic decision.

---

# State Makes the Workflow Resumable

One important benefit of LangGraph is that a workflow can eventually be persisted.

Imagine the graph stops after verification.

The stored state might contain:

```text
search settings
candidates
verification results
retry counts
errors
```

The workflow can later resume from that point.

This would be impossible if important information existed only inside temporary Python function variables.

---

# State Is a Contract Between Nodes

A useful mental model is:

```text
Discovery Node
        │
        │ Candidate[]
        ▼
WorkflowState
        │
        │ Candidate[]
        ▼
Verification Node
```

Neither component needs to know the internals of the other.

They only agree on:

```text
data contracts
```

This creates loose coupling.

---

# Why Loose Coupling Matters

Suppose later you replace:

```text
Groq Discovery
```

with:

```text
Tavily Discovery
```

If both produce:

```text
Candidate[]
```

the verification agent does not need to change.

Likewise:

```text
Verification v1
```

can later become:

```text
Verification v2
```

without changing discovery.

This is one of the major reasons structured models are so important.

---

# Workflow State Mental Model

Think of the graph like this:

```text
                    STATE
                     │
       ┌─────────────┼─────────────┐
       │             │             │
       ▼             ▼             ▼

   Discovery     Verification    Evaluator

       │             │             │
       └────── updates STATE ──────┘
```

Agents do not need to directly communicate.

They communicate through a shared, typed state.

---

# What Should Go Into State?

Ask:

> Does another workflow node need this information later?

If yes, it probably belongs in state.

Examples:

```text
search configuration      ✓
candidate list            ✓
verification result       ✓
retry count               ✓
errors                    ✓
evaluation result         ✓
```

Ask:

> Is this application infrastructure?

If yes, it usually should not be in state.

Examples:

```text
API key                   ✗
Groq client               ✗
database connection       ✗
HTTP session              ✗
logger                    ✗
```

---

# Core LangGraph Mental Model

Do not think:

```text
Agent A talks to Agent B,
which talks to Agent C.
```

Think:

```text
STATE
  ↓
Node
  ↓
STATE UPDATE
  ↓
Router
  ↓
Next Node
```

A LangGraph application is fundamentally:

```text
state
+
nodes
+
edges
+
routing conditions
```

The LLM is only one implementation detail inside some nodes.

---

# Important Rules to Remember

## Rule 1

Use AI when the problem contains uncertainty.

```text
Does this webpage imply the notary handles company formation?
```

This may require an LLM.

---

## Rule 2

Use normal code when the answer is deterministic.

```text
Have we already found 50 candidates?
```

Use Python.

---

## Rule 3

Do not trust LLM output directly.

```text
LLM
 ↓
schema validation
 ↓
application
```

---

## Rule 4

Research outputs need provenance.

```text
claim
+
evidence
+
source
```

---

## Rule 5

Separate observation from conclusion.

```text
Website mentions Gesellschaftsrecht
```

is not identical to:

```text
Notary definitely handles UG formation.
```

---

## Rule 6

Agents should have narrow responsibilities.

```text
Discovery → discover

Verifier → verify

Writer → write

Evaluator → evaluate
```

---

## Rule 7

State should describe workflow execution.

It should not contain infrastructure.

---

## Rule 8

Every retry loop must be bounded.

```text
max retries
max attempts
timeout
```

should always exist.

---

# Overall Architecture So Far

At this stage the system looks like:

```text
SearchSettings
      │
      ▼
WorkflowState
      │
      ▼
Discovery Agent
      │
      ▼
Candidate[]
      │
      ▼
WorkflowState
      │
      ▼
Verification Agent
      │
      ▼
VerificationResult[]
      │
      ▼
WorkflowState
```

Later:

```text
SearchSettings
      │
      ▼
Discovery
      │
      ▼
Deduplicate
      │
      ▼
Verification
      │
      ├── insufficient evidence
      │          ↓
      │        retry
      │
      ▼
Validation
      │
      ▼
Email Writer
      │
      ▼
Evaluator
      │
      ├── fail → retry/manual review
      │
      ▼
Human Review
```

The main idea is:

> LangGraph is not valuable because it lets us create many agents. It is valuable because it gives us explicit state, routing, retries, persistence, and control around AI-powered steps.
