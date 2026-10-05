# Ask Ticketing

*Get the numbers for your next client meeting.* A sales or ticketing employee asks a plain-English question. Ask Ticketing turns it into one read-only SQL query, runs it, and returns a short answer with the supporting rows, the assumptions it made, and the exact SQL that was executed. When a question is ambiguous, the app is designed to ask one clarifying question; when the data can't answer it, it is designed to say so.

> **SYNTHETIC DATA.** Every schedule, price and sale is invented for this demo. Opponent names are used only as labels, and no date reflects a real schedule. "Harborview Arena" is a fictional venue. There is no customer or personal data.
>
> **Fixed demo date: 2026-10-01.** All data is "as of" this date. "Last month" means September 2026; "upcoming" means an event date after 2026-10-01.

## Choose your provider

| Provider | Default model | Status |
| --- | --- | --- |
| **Anthropic API** (recommended) | `claude-sonnet-5-5` | **Verified:** live invocation and answer-quality checks completed (see [Evaluation](#evaluation-and-results)) |
| OpenAI API (optional) | `gpt-6.1-sol` | **Invoked live (2026-10-04).** Passed the smoke test and all 4 provider checks (normal answer, clarification, no matching events, real zero). That is a small regression check, not a full evaluation. |
| Amazon Bedrock (optional) | `global.anthropic.claude-sonnet-4-6` | **Invoked live.** On 2026-10-04 it **failed the clarification check**: it answered "tickets sold last month" with a purchase-date count instead of asking. After a general fix to the date-basis rule (2026-10-05), it passed the post-fix regression checks: 5/5 provider check and 7/7 date-basis checks. That is a small regression check, not a full evaluation. |

The app uses exactly the one provider you select. It never switches providers automatically and never retries on another provider. All three adapters also have offline tests: mocked SDK tests of request construction, structured-result parsing, error mapping and missing credentials, plus the full workflow with a mocked client.

**The dev, held-out and date-basis evaluation scores in this README come from the Anthropic provider only.** All three providers ran the same 5-step provider check: Anthropic 5/5 and OpenAI 5/5 on 2026-10-04; Bedrock stopped at clarification that day, then passed 5/5 after the date-basis rule fix. OpenAI and Bedrock ran only that check (`evals/provider_checks.json`, frozen before either ran). Three of its four checks are known dev regression cases, not unseen held-out cases. Details: [`evals/RESULTS.md`](evals/RESULTS.md).

## Quick start (Anthropic, the verified path)

**Prerequisites:** [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 from `.python-version` if needed) and your own Anthropic API key with API billing enabled. No AWS account is needed.

    cd <your clone of this repository>
    export ANTHROPIC_API_KEY="<your-anthropic-api-key>"    # placeholder; never commit a real key
    uv sync --locked                                       # exact dependency versions from uv.lock
    uv run ask-ticketing-generate-data                     # builds data/local/ticketing.db (deterministic, gitignored)
    uv run streamlit run src/ask_ticketing/app.py          # starts the UI

Open **http://localhost:8501**, type the question below, and click **Ask**:

> How many tickets were sold for Brooklyn Nets home games with an event date in September 2026? Exclude refunded tickets.

Expected: **269 tickets**. The answer states its assumptions (event-date basis, refunds excluded), shows the supporting row, and has the exact SQL in a "SQL executed" section. (Verified live with the Anthropic provider.)

### Using OpenAI or Amazon Bedrock instead (optional)

Run the same steps, but replace the `export ANTHROPIC_API_KEY=...` line with **one** of these blocks.

**OpenAI** (API key):

    export ASK_TICKETING_PROVIDER=openai
    export OPENAI_API_KEY="<your-openai-api-key>"

**Amazon Bedrock** (AWS credentials, not an API key; uses the standard AWS credential chain):

    export ASK_TICKETING_PROVIDER=bedrock
    export ASK_TICKETING_AWS_PROFILE="<your-aws-profile>"   # optional; omit to use your default credentials
    export ASK_TICKETING_REGION="us-east-1"                 # optional; default: your AWS config, else us-east-1

<details>
<summary>Bedrock prerequisites and model notes</summary>

Do these yourself in your own AWS account. The app never changes AWS resources, IAM, subscriptions or account settings.
- Model access for Anthropic models in Amazon Bedrock, including Anthropic's one-time use-case ("first-time use") form.
- IAM permission `bedrock:InvokeModel` on the model and inference profile. Enabling a model for the first time also needs `aws-marketplace:Subscribe` and `aws-marketplace:ViewSubscriptions`.
- A valid AWS Marketplace payment method. Access can take up to about 15 minutes after first enablement.
- Observed live: without the use-case form, Bedrock let a few calls through and then rejected the rest with `ResourceNotFoundException` ("Model use case details have not been submitted for this account"). The app reports this as a setup problem naming the form. After the form was submitted, access worked about 15 minutes later.
- **Why Sonnet 4.6 and not 5.5:** AWS documents structured outputs as **not supported** for Claude Sonnet 5.5 and Opus 5.5 on Bedrock (checked 2026-10-04), and this app needs schema-constrained output. Sonnet 4.6 lists structured outputs as supported. It is a different, older model than the verified default.
</details>

If credentials are missing, the UI still starts, shows "Model provider setup needed" with the fix for the selected provider, and disables **Ask** until you set the variable and restart the app.

**CLI:**

    uv run ask-ticketing "How many tickets were sold for Brooklyn Nets home games with an event date in September 2026? Exclude refunded tickets."
    uv run ask-ticketing --provider openai "..."    # overrides ASK_TICKETING_PROVIDER for one question
    uv run ask-ticketing --json "..."               # full structured result

CLI exit codes: 0 answered/clarify/unsupported, 1 error, 2 provider setup error (for example, a missing key).

**Offline verification** (no API key, no AWS credentials, no paid calls):

    uv run pytest -q

**Cost.** Live questions are billed to *your* provider account. Figures are **estimates** from list prices and the app's token limits, not billing statements.

| Provider and model | List price per million input / output tokens | Pricing source | Worst case per question (estimated) |
| --- | --- | --- | --- |
| Anthropic, Claude Sonnet 5.5 | $2 / $10 | Anthropic pricing docs | about $0.44 |
| OpenAI, gpt-6.1-sol | $2 / $10 (reasoning tokens bill as output) | OpenAI pricing docs | about $0.68 |
| Bedrock, Claude Sonnet 4.6, `global.` profile, us-east-1 | $3.00 / $15.00 | AWS Price List API (verified 2026-10-04) | about $0.30 |
| Bedrock, Claude Sonnet 4.6, `us.` profile, us-east-1 | $3.30 / $16.50 | AWS Price List API (verified 2026-10-04) | about $0.33 |
| Bedrock, any other profile or region | **unverified** | none | unknown |

- Typical cost per question in the developer's runs (estimated from reported token usage): Anthropic about **$0.01**; OpenAI about **$0.005**; Bedrock about **$0.01**. The OpenAI and Bedrock figures come from only 4 and 3 questions.
- Worst case is calculated from the app's limits: question length, output tokens including a thinking/reasoning allowance, summary size, one repair, and the default single SDK retry.
- The UI and CLI have **no total spend cap**. Only the evaluation scripts (`evals/`) apply a dollar budget before each request. That budget is an application-enforced bound based on the configured prices and token limits, not a guarantee about the provider's final bill. The scripts refuse configurations with unverified prices.

**Troubleshooting**
- *Missing credentials:* the UI starts, shows "Model provider setup needed" with the fix for the selected provider (for example "…ANTHROPIC_API_KEY is not set…" or "…no AWS credentials were found…"), and disables **Ask**. The CLI prints the same message and exits with code 2. There is no traceback. Export the variable in the same shell, then restart the UI.
- *Invalid or rejected key:* the UI names the provider and the variable to check; the CLI prints it and exits with code 1.
- *Bedrock access refused:* the message lists the Bedrock prerequisites (see above). *Model not found:* check `ASK_TICKETING_MODEL_ID` and `ASK_TICKETING_REGION`.
- *Database already exists:* the generator refuses to overwrite it. Keep it, or rebuild with `uv run ask-ticketing-generate-data --rebuild`.
- *Port 8501 in use:* `uv run streamlit run src/ask_ticketing/app.py --server.port 8502`, then open http://localhost:8502.

### Configuration

| Setting | Flag (CLI) | Env var | Default |
| --- | --- | --- | --- |
| Provider | `--provider` | `ASK_TICKETING_PROVIDER` | `anthropic` (also `openai`, `bedrock`) |
| Model | `--model-id` | `ASK_TICKETING_MODEL_ID` | the provider's default above |
| AWS profile (Bedrock) | `--profile` | `ASK_TICKETING_AWS_PROFILE` | none: standard AWS credential chain |
| AWS region (Bedrock) | `--region` | `ASK_TICKETING_REGION` | your AWS config, else `us-east-1` |
| SDK retries | (none) | `ASK_TICKETING_MAX_RETRIES` | `1` (paid validation runs use `0`) |
| Database | `--db` | `ASK_TICKETING_DB` | `data/local/ticketing.db` |
| Anthropic key | (none) | `ANTHROPIC_API_KEY` | required for `anthropic` |
| OpenAI key | (none) | `OPENAI_API_KEY` | required for `openai` |

- Flags apply to the CLI; the UI reads the environment variables.
- **Model IDs are configurable. Their status:**
  - **Anthropic default (`claude-sonnet-5-5`):** live invocation and answer-quality checks completed.
  - **OpenAI and Bedrock defaults:** offline adapter tests only.
  - **Any other model ID:** not validated in any way. Anthropic and OpenAI accept only IDs with settings in their adapter's `MODEL_SETTINGS`; for Anthropic that also includes `claude-opus-5-5`, which has not been evaluated. Bedrock accepts any model or inference-profile ID, but the model must support Bedrock structured outputs.
  - The paid evaluation scripts refuse any configuration whose prices are unverified (see Cost).
- The generator also accepts `--out PATH`. Generated data: 2 venues, 50 events, 5,088 ticket rows (4,793 sold, 295 refunded).

## How it works

    question (max 1,000 chars)
      -> interpret: model returns a structured decision (JSON schema), validated with pydantic
           -> clarify      : one clarifying question (e.g. purchase date vs event date)
           -> unsupported  : what data is missing (e.g. there is no customer data)
           -> sql          : run under SQLite safeguards
                             -> event-filtered totals: verify the matching_events support count
                             -> summarize: the model phrases the actual rows (money pre-formatted)
      at most ONE repair (malformed output, SQLite error, or a missing/invalid support count);
      never for unsafe SQL, execution limits, provider/auth errors, or budget stops

- **Model interface (ports and adapters).** `model.py` defines `ModelProvider`, a one-method protocol (`call_tool`: return a JSON object matching a schema), plus app-level errors.
  - The LangGraph workflow (`workflow.py`), prompts (`prompts.py`) and SQL guard (`sql_guard.py`) depend only on that interface.
  - One adapter per provider, each using that provider's documented structured-output mechanism (no forced tool choice):
    - `anthropic_api.py`: Messages API with `output_config.format` (JSON schema).
    - `openai_api.py`: Responses API with `text.format` (strict JSON schema). Optional fields are sent as nullable, and nulls are dropped before validation. Refusals and incomplete responses become explicit errors.
    - `bedrock.py`: Converse API with `outputConfig.textFormat` (JSON schema), using the standard AWS credential chain.
  - Each adapter maps its SDK's errors to the same application errors (configuration, auth, access, unavailable, request, output). Auth and access errors are never retried, and messages never include credentials or raw provider payloads.
  - `providers.py` builds the one configured adapter for the CLI, the UI and the eval scripts, with no fallback. Tests inject a scripted double (`tests/fakes.py`).
- **SQL safeguards**, enforced by SQLite rather than by text matching:
  - a read-only `mode=ro` connection plus `PRAGMA query_only`
  - an authorizer allowlist: SELECT/CTEs, reads of `venues`/`events`/`tickets`, and ordinary functions only (no writes, DDL, ATTACH, PRAGMA, `sqlite_master` or `load_extension`)
  - one statement per call
  - a time/work budget
  - a 200-row cap, with truncation stated
  - The SQL shown is exactly the SQL executed.
  - These prevent unsafe or runaway queries. They **do not guarantee that a valid query answers the question correctly**: a model can write valid SQL with the wrong filter or interpretation. The stated assumptions and visible SQL let a user check this.
- **Grounding (intended behavior, not a guarantee).** Arithmetic happens in SQL, and money is formatted in Python. The summary model is instructed to use only the returned rows, and it sees at most 50 rows / 12,000 characters.
  - An empty result is reported as "no rows matched", with no model call.
  - **No matching events vs. zero sales:** for ticket counts or revenue over event-filtered events, the SQL must also return `matching_events`, counted with a LEFT JOIN so a sold-ticket filter can't hide events. The app validates it. If it is 0, the app answers with fixed wording ("No events matched … in this synthetic dataset"); a real event with 0 sold keeps its legitimate zero.
  - Clarification and date-basis choices depend on the model following its instructions. They are tested on a small set of questions but not guaranteed.
- **Limits and spend caps.** Two different mechanisms:
  - **CLI and UI:** per-question limits only (question length, output tokens including a thinking allowance, summary size). There is no total spend cap.
  - **Evaluation scripts** (`evals/run_eval.py`, `validate_live.py`, `smoke_test.py`): additionally wrap the model in `budget.py`. It reserves each request's worst-case cost *before* sending it and refuses to dispatch a request that could exceed the approved cap. The bound is computed by the app from the **configured** prices and token limits. It is not a guarantee about the provider's final bill (for example, if a price is wrong or the provider charges for something not counted). The scripts refuse any configuration whose prices are not verified.
- Runtime prompts contain the schema, business definitions and instructions only. **No evaluation questions, answers or reference SQL** appear in them (checked by a test).

## Model choice

The default provider and the only one with measured quality is **Claude Sonnet 5.5** (`claude-sonnet-5-5`, $2 / $10 per million input/output tokens), with effort `medium` and adaptive thinking.
- **Principle:** prefer the least expensive model that meets measured quality.
- **What was tested:** Sonnet 5.5 was the lower-cost candidate evaluated, and it met the tested requirements on the dev set. The more expensive Opus 5.5 wasn't needed.
  - Cheaper models such as Claude Haiku 4.5 were **not** evaluated, so Sonnet is not shown to be the cheapest model that works.
  - Estimated cost was about $0.008–0.012 per question (from reported token usage at list prices), at about 4–6 s per answer.
- **Settings:** taken from the official docs (verified 2026-10-03) and kept per model in `MODEL_SETTINGS`. Sonnet's default effort is `high`, so `medium` is set explicitly. Thinking tokens count toward `max_tokens`, so the adapter adds a thinking allowance.
- **Other providers' defaults** were chosen from official documentation (checked 2026-10-04), not from measured quality:
  - **OpenAI:** `gpt-6.1-sol`, reasoning effort `medium`. It is a mid-price model that lists structured-output support, at the same list price as Sonnet 5.5. Reasoning tokens count toward `max_output_tokens`, so the adapter adds an 8,000-token reasoning allowance. In the 5 live provider-check questions, output (including reasoning) peaked at 225 tokens, far below the allowance. That is a small sample.
  - **Bedrock:** Claude Sonnet 4.6 through its global inference profile, because Sonnet 5.5 does not support structured outputs on Bedrock. It runs without extended thinking. It is a different and older model than the default, so its answer quality is not covered by the Anthropic results. In its first live provider check it answered the ambiguous "tickets sold last month" with a purchase-date count instead of asking. After the date-basis rule was restated (see Evaluation), it asked as intended in the post-fix checks.

## Data

| Table | One row per | Columns |
| --- | --- | --- |
| `venues` | venue | `venue_id` PK, `name` (unique) |
| `events` | single dated game or show | `event_id` PK, `venue_id` → venues, `name`, `category` (basketball, concert, comedy, family, other), `home_team` ('Brooklyn Nets', 'New York Liberty', or NULL), `event_date`, `capacity` |
| `tickets` | one purchased ticket | `ticket_id` PK, `event_id` → events, `purchase_date`, `price_cents`, `status` ('sold' or 'refunded') |

- Dates are ISO text. Money is integer US cents, excluding taxes and fees.
- Tables are STRICT, with CHECK constraints, foreign keys, and triggers enforcing capacity and purchase-before-event.
- `capacity` is a scaled-down sellable allocation. Remaining inventory is always calculated, never stored.

**Business definitions** (the model is given these):
- **Tickets sold:** count of `status = 'sold'` tickets. Refunded tickets never count.
- **Ticket revenue:** `SUM(price_cents)` over sold tickets, excluding taxes and fees.
- **Average ticket price:** ticket-weighted (`SUM / COUNT` over sold tickets), not an average of per-event averages.
- **Refunded:** the seat returned to inventory. Refund dates are not recorded.
- **Event date vs. purchase date:** a period that describes games or events ("Nets games in July") should use event date; one that describes buying ("bought in September") should use purchase date. A period on the sale itself ("tickets sold last month") is ambiguous, so the app should ask. The basis is stated in the assumptions.
- **Remaining inventory:** capacity − tickets sold.
- **Upcoming:** `event_date > '2026-10-01'`.
- **Zero sales vs. no matching events:** see "Grounding" above.

## Evaluation and results

`evals/cases.json` has 13 hand-written cases: 9 dev and 4 held-out.
- It is a **small synthetic evaluation set written by the developer, not a benchmark**.
- Expected rows come from plain-Python ground truth (`evals/ground_truth.py`), cross-checked against reference SQL and a hand-worked fixture.
- Scoring checks the decision branch and the fields each question asks for: values, entities, row set, and order where a ranking is asked. Extra columns are ignored.
- Full tables and diagnosis: **[`evals/RESULTS.md`](evals/RESULTS.md)**.

| Run | Result | Notes |
| --- | --- | --- |
| Dev, first live pass | strict **5/8** | All three misses had correct rows and order, but no columns the question didn't ask for |
| Dev, re-scored after a scorer fix | **8/8** | Same saved responses; this is a re-score, not a fresh pass |
| Held-out, single run | **3/4** | Failure: "Liberty games in January 2026" (none exist) was answered as "0 tickets sold" |
| Post-fix regression checks (not held-out) | no-match case and zero-sales contrast both correct | The fix was validated on a new dev case; held-out was not rerun |
| Date-basis refinement (post-evaluation, 6 new questions) | **6/6** | "games in July" -> event date; "bought in September" -> purchase date; "sold last month" -> asks. See `evals/RESULTS.md` |
| Date-basis rule restated after the Bedrock failure (post-fix regression, 2026-10-05) | **7/7 on each provider** | Same 6 frozen checks plus the failed "sell last month" check, on Anthropic, OpenAI and Bedrock. Bedrock provider check then 5/5. Not held-out evidence |

Estimated live spend across all validation and acceptance runs: about $0.57 (from reported token usage at list prices; some UI runs estimated). That includes about $0.085 for the first provider checks and about $0.19 for the post-fix regression checks, whose provider errors are described in `evals/RESULTS.md`. The Anthropic runs had 0 provider errors. The dev and held-out scores were produced **before** both date-basis prompt changes and were not rerun, so they are not a fresh evaluation of the final prompt.

## Limitations

- The evaluation set is small (13 cases), synthetic, and written by the developer. The held-out set has now been seen for the no-match behavior.
- Valid SQL can still be wrong. The safeguards stop unsafe queries, not misinterpretations.
- Single-turn only: after a clarifying question, the user resubmits a more specific question.
- The no-match check depends on the model declaring an event-filtered total. A total it doesn't declare gets no support-count check, and the summary prompt is the only guard.
- **OpenAI and Bedrock were checked only with the small provider batch, not the evaluation sets.** All scores above are Anthropic-only.
  - OpenAI passed 5/5 steps.
  - Bedrock (Claude Sonnet 4.6, a different and older model than the default, run without extended thinking) first failed the clarification check. It passed after a general restatement of the date-basis rule, but only on small regression checks, so its clarification behavior on other phrasings is less proven than Anthropic's.
  - The Anthropic default also passed the same 5-step provider check, including the new "real event with zero sold" check (Indie Showcase).
- No authentication, deployment, conversation memory, caching, or total spend cap in the UI/CLI. One SQLite file.
- The UI doesn't display token usage.

## What would change for production

- A real warehouse with semantic-layer definitions instead of prompt-embedded definitions. Read replicas, row-level permissions, and query cost limits enforced by the database.
- A larger eval set built from real employee questions, run in CI, plus an LLM-independent check for every numeric answer.
- Auth (SSO), audit logging of questions/SQL/answers, per-user rate and spend limits, monitoring of latency, cost and failure kinds.
- Prompt caching for the static schema and definitions, which are about 2,000 tokens per call.
- Multi-turn clarification, and saved or shared answers.

## AI assistance

This project was built with AI assistance. The developer set the requirements, made the decisions, and approved every paid model call and account action.
- **Claude Code (Claude Opus 5.5):** implementation, tests, documentation, setup, evaluation scripts and debugging. This includes research subagents for model-provider troubleshooting and for checking the current Bedrock and OpenAI documentation behind the adapters.
- **Claude in Chrome (browser automation, driven by Claude Code):** the live Streamlit UI checks and some cloud-console troubleshooting steps.
- **ChatGPT:** planning, design review and interview preparation.
- **At runtime**, the app calls the model provider the reviewer selects. By default that is Claude Sonnet 5.5 through the Anthropic API, which was used for all evaluation runs. OpenAI (gpt-6.1-sol) and Amazon Bedrock (Claude Sonnet 4.6) were called live only for the provider checks and post-fix regression checks described in `evals/RESULTS.md`.

## Appendix: provider history

Amazon Bedrock was the preferred stack, but Bedrock model calls were refused for the developer's own AWS accounts, so development and every live evaluation used the Anthropic API. The adapter boundary let the providers change without rewriting the core query workflow. The Bedrock adapter was later rebuilt on Converse structured outputs, because current Claude models reject the forced tool choice it originally used, and an OpenAI adapter was added. Both were later invoked live with a small provider check (see the status table at the top).
