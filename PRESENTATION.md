# Ask Ticketing: 10-minute walkthrough

## 1. The user problem (1 min)
- A sales or ticketing rep is preparing for a client meeting and needs numbers now: "How did Nets games sell in September?", "Which upcoming events have the most seats left?"
- Today that means asking an analyst or writing SQL. They need a trustworthy answer, the rows behind it, and the query, so they can defend the number.
- Trust matters more than fluency. A wrong number in a client meeting costs more than a clarifying question.

## 2. Live demo (3 min)
Run `uv run streamlit run src/ask_ticketing/app.py`. The data is synthetic, as of 2026-10-01.
1. Example: **"How many tickets were sold for Brooklyn Nets home games with an event date in September 2026?"** Show the answer (269), the assumptions (event-date basis, refunds excluded), the table, and the exact SQL.
2. Example: **"How many tickets did we sell last month?"** It asks whether that means purchase date or event date; it is designed not to settle that ambiguity silently. Contrast with **"How many tickets were sold for Nets home games in September 2026?"**, which answered 269 directly in testing (event date, stated in the assumptions).
3. Type: **"How many tickets were sold for Brooklyn Nets home games with an event date in July 2026? Exclude refunded tickets."** (wording verified live). Answer: "No events matched…" with `matching_events = 0`. There were no games, which is different from "0 sold".
4. Optional: **"Which customers bought the most Nets tickets?"** It says customer data doesn't exist and offers what it can answer.

## 3. Architecture (2 min)
- LangGraph flow: interpret -> clarify | unsupported | SQL -> safe execute -> summarize, with **one** bounded repair.
- **Ports and adapters:** the workflow depends on a one-method `ModelProvider` interface. Reviewers choose Anthropic (default), OpenAI or Bedrock explicitly, with no automatic fallback. Each adapter uses its provider's documented structured-output mechanism. Only Anthropic has been fully evaluated. Anthropic and OpenAI each passed a 5-step live provider check. Bedrock (Claude Sonnet 4.6) first failed clarification: it read "sold last month" as purchase date. The request was identical across providers, so the cause was a gap in the rule's wording ("sold" was never said not to mean "purchased"). One general restatement fixed it: 7/7 date-basis checks on all three providers, and Bedrock's provider check 5/5. Tests use a scripted double and mocked SDKs, so the whole suite runs offline.
- **Safety in the database, not the prompt:** read-only SQLite, an authorizer allowlist, a single statement, time and row limits. The SQL shown is exactly what ran.
- **Grounding (intended behavior, not a guarantee):** arithmetic in SQL, money formatting in Python, and the summary model is instructed to phrase only the returned rows. For no-match cases, the app checks a `matching_events` support count and answers with fixed wording.
- **Limits:** the safeguards stop unsafe or runaway SQL, not wrong-but-valid SQL; the shown assumptions and SQL let users check it. The UI/CLI have per-question input/output limits, while the eval scripts add a pre-dispatch dollar bound, enforced by the app from configured prices and token limits (not a guarantee of the provider's bill).

## 4. Evaluation findings (2 min)
- A small synthetic evaluation set (13 cases), with ground truth computed in plain Python and not from SQL. It is not a benchmark.
- Dev: strict 5/8, then **8/8** after fixing the scorer. The model had correct rows but omitted columns the question didn't ask for. The scorer was fixed, not the model, and the change is documented.
- Held-out, one run: **3/4**. It found a real defect: "Liberty games in January" (none exist) was answered as "0 sold".
  - Root cause: the app's own prompt said zeros are real results.
  - Fixed with a validated support count and fixed wording.
  - Checked on a new dev case and a zero-sales contrast. Held-out was not rerun.
- Browser acceptance check: the app over-clarified "games in July". A one-sentence prompt refinement fixed it, verified on 6 new paired questions written beforehand (6/6, estimated $0.05). The earlier scores predate this change and weren't rerun. A second restatement of the same rule (2026-10-05) fixed the Bedrock failure and was rechecked on all three providers.
- About $0.01 per question (estimated from reported token usage at list prices) and about 5 s. Estimated total live spend about $0.57 (including about $0.28 for the provider checks and post-fix regression checks); the evaluation runs used app-enforced pre-dispatch budget bounds.

## 5. Tradeoffs (1 min)
- **Sonnet 5.5 over Opus:** the lower-cost candidate tested; it met the tested requirements, so the more expensive Opus wasn't needed. Cheaper models such as Haiku 4.5 were not evaluated.
- **Single-turn clarification** over chat memory: simpler and auditable, but the user retypes.
- **Developer-written eval set:** fast to build and precise, but small, and the held-out set is no longer unseen for the fixed behavior.
- **Bedrock** was the preferred stack, but account access was blocked, so the app was built and evaluated on the Claude API. The adapter boundary let us change providers without rewriting the core query workflow. A rebuilt Bedrock adapter (Claude Sonnet 4.6, because Sonnet 5.5 lacks structured outputs on Bedrock) and an OpenAI adapter were added later and given a small live check. OpenAI passed 5/5. Bedrock failed clarification. Lesson: swapping the provider is cheap, but behavior has to be re-verified per model. A second model also exposed an ambiguity in our own prompt.

## 6. Production changes (1 min)
- A warehouse plus a semantic layer for definitions; permissions and cost limits in the database.
- An eval set built from real questions, run in CI on every prompt change.
- SSO, audit logs (question, SQL, answer), per-user spend and rate limits, monitoring.
- Prompt caching for the static schema and definitions; multi-turn clarification.

## Q&A prep
- *Why not let the LLM grade answers?* Numeric correctness is checked against deterministic ground truth. An LLM judge would add its own errors.
- *What if the model writes a DELETE?* SQLite's authorizer denies it before execution; 16 unsafe forms are tested.
- *Cost control?* Per-question input and output limits in the UI/CLI. The eval scripts also reserve worst-case cost before every request against an app-enforced cap, computed from configured prices and token limits; it bounds what the app sends, not the provider's final bill. There is no total spend cap in the UI.
- *What isn't covered?* Multi-turn, real data quirks, concurrency, and the held-out set is small and now seen for no-match.
