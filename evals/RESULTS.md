# Evaluation results (compact, sanitized)

Small hand-built evaluation set over SYNTHETIC data (as of 2026-10-01). It had 12 cases (8 dev, 4 held-out) at the time of the runs below. A 13th, dev case was added after the held-out run (see "Post-fix regression checks").
It is a reserved sanity check written by the developer, not a blind or independent benchmark.
Expected rows come from plain-Python ground truth (`evals/ground_truth.py`), not from the reference SQL.
Raw run files live in `runs/` (gitignored); this file is the reviewer-facing summary.
**Provider scope:** every dev, held-out and date-basis result below was produced with the **Anthropic API provider (Claude Sonnet 5.5)**. The OpenAI and Bedrock adapters were added later and ran only the small provider check (last section). None of the evaluation scores apply to them.
All dollar figures are **estimates** computed from the token usage the API reported, at list prices ($2 / $10 per million input/output tokens for Sonnet 5.5). Some UI figures are estimated from comparable runs. They are not billing statements.

Model: Anthropic Claude API, `claude-sonnet-5-5`, effort `medium`, adaptive thinking, structured output
(`output_config.format`), SDK retries 0, at most one application repair per question, pre-dispatch spend cap enforced by the app from configured list prices and token limits (a bound on what the app sends, not a guarantee about the provider's bill).

## Dev set: first live run (2026-10-03 13:33 local, run `validate-20261003-133328`)

Original strict scorer (every reference column required, matched by position): **5/8**.

| case | strict (original) | branch | summary (manual) | repairs | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- | --- |
| nets_tickets_sep_2026_by_event_date | PASS | answered | faithful | 0 | 4.6 s | 2910/309 |
| purchases_sep_2026_any_event | PASS | answered | faithful | 0 | 3.9 s | 2964/315 |
| top5_barclays_revenue_2026_jan_sep | FAIL | answered | faithful | 0 | 5.3 s | 3134/582 |
| upcoming_by_remaining_inventory | FAIL | answered | faithful | 0 | 5.1 s | 3304/502 |
| category_revenue_2026_jan_sep | PASS | answered | faithful | 0 | 5.1 s | 3018/498 |
| zero_sales_sep_2026 | FAIL | answered | faithful | 0 | 4.3 s | 2938/371 |
| ambiguous_last_month_date_basis | PASS | clarify | n/a | 0 | 2.1 s | 2264/128 |
| unsupported_customers | PASS | unsupported | n/a | 0 | 2.2 s | 2275/142 |

Batch total (smoke test + 1 CLI question + 8 dev cases): 17 calls, 0 errors, 0 repairs, 25,957 input / 3,159 output tokens,
**$0.0835** actual spend, median latency 4.6 s.

## Scorer correction (made after the dev run, before any held-out run)

Why: the three strict failures returned the correct rows in the correct order, but omitted columns that the
question did not ask for (`tickets_sold` for the top-five revenue ranking; `venue` for the inventory ranking;
`venue`/`refunded_tickets` for the zero-sales list). The original scorer required every reference column, so
it scored column choices rather than answers.

What changed (`evals/run_eval.py::compare`, contracts in `evals/cases.json`):
- Each SQL case has a `contract`: `required_fields` taken from the question text, and `ordered` (true when a
  ranking or a single "highest" answer is asked for). Extra result columns are ignored.
- Columns are matched by name through an explicit alias table (`FIELD_ALIASES`, including unit factors such
  as cents vs dollars), not by position. Unlisted names are not guessed.
- Still fails: a missing required field, a wrong value or entity, missing, extra or duplicate rows, a wrong
  order where a ranking is asked (including tie-breaks), truncation, or the wrong decision branch.
- Expected values, expected columns and reference SQL are unchanged. No ground-truth defects were found.
- Regression tests: `tests/test_eval_runner.py`.

Dev re-scored offline with the corrected scorer (same saved responses, no new model calls): **8/8**.
This is a re-score after a scorer fix, not an untouched first-pass result.

Held-out contracts were written from the held-out question text before any held-out run, with no held-out
model outputs available. The contract is frozen from this point on.

## Held-out set: single run (2026-10-03 14:33 local, run `eval-held_out-20261003-143324`)

One pass, frozen contract, cap $1.00 enforced before every request. Prompts and app behavior were not tuned on held-out cases.

| case | result | branch | SQL / repairs | summary (manual) | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- | --- |
| nets_vs_liberty_2026_jan_sep | **PASS** | answered | event-date filter, LEFT JOIN, status='sold'; 0 repairs | faithful: correct teams, event-date basis stated, $ from cents correct, fees excluded, the "Nets earned more despite fewer games" comparison is correct | 5.9 s | 3026/514 |
| barclays_2024_highest_avg_price | **PASS** | answered | weighted SUM/COUNT over sold tickets, LIMIT 1; 0 repairs | faithful: Neon Tides Live, 2024-04-13, $147.78, 134 non-refunded tickets | 5.3 s | 2992/500 |
| ambiguous_best_events | **PASS** | clarify | none | asks which metric (tickets, revenue, average price, sell-through); its stated assumptions note the missing period | 2.0 s | 2261/129 |
| no_match_liberty_jan_2026 | **FAIL** | answered (1 row) | `SELECT COUNT(t.ticket_id) … home_team='New York Liberty' AND January 2026`; 0 repairs | **misleading**: "Zero tickets were sold … a real result of zero rather than missing data", implying games existed; there were no Liberty home games in January 2026 | 4.1 s | 2886/326 |

**Held-out: 3/4.** 7 calls, 0 errors, 0 repairs, 11,165 input / 1,469 output tokens, **$0.0370** actual spend; all reservation bounds held.

### Failure diagnosis: no_match_liberty_jan_2026 (an evaluation failure, not provider/infrastructure)
- The SQL is a valid read-only aggregate. COUNT over a join returns 0 both when games exist with no sales and when no games exist, so "no matching events" cannot be told apart from "zero sales".
- The app's own instructions push toward this: the decision prompt says "for 'how many'/totals … return the aggregate (it may legitimately be 0)", and the summary prompt says "a row containing 0 is a real result of zero". That violates the documented business rule (README: zero sales vs. no matching events).
- Coverage gap: the dev set had no no-match case, so the first time this path was tested was the held-out run.

### Smallest proposed fix (as proposed after the held-out run; since applied, see below)
- Decision prompt: for counts or totals over events, also return the number of matching events (e.g. `COUNT(DISTINCT e.event_id) AS matching_events`).
- Summary prompt: if `matching_events` is 0, say no matching events exist in the data for that filter, rather than "0 sold".
- Validate on a **new dev no-match case** (e.g. Brooklyn Nets home games in July 2026), not by rerunning held-out. After this fix the held-out set is no longer unseen for this behavior; report it as such.

## No-match fix and post-fix regression checks (2026-10-03, after the held-out run)

**The defect.** For event-scoped ticket counts, a query that filters `status = 'sold'` in WHERE (or one plain `COUNT`) gives 0 both when no events match and when matching events had no sold tickets. The prompts also told the model that any aggregate zero was "a real result", so the answer implied that games had taken place.

**The fix** (`prompts.py`, `workflow.py`):
- The model now declares `event_filtered_total` in its structured decision. Such queries must return `matching_events`, counted with `events LEFT JOIN tickets ON ... AND <ticket conditions>`, so ticket conditions can't remove events.
- The app validates the support count. Missing, non-integer, negative, or 0 alongside positive ticket values triggers the existing single repair, then the error `invalid_result`.
- If the verified count is 0, the app answers with fixed wording ("No events matched this question's event filters in this synthetic dataset…") and makes no summary call. A real event with 0 sold keeps its legitimate zero.
- The blanket "any aggregate 0 is a real result" summary instruction was removed.
- Offline regression tests (`tests/test_no_match.py`) use an independent fixture: no matching events / an event with no ticket rows / an event with only refunded tickets / an event with sales. They also cover the missing and invalid support-count repair paths.

**New dev case** `no_match_nets_jul_2026` ("…Brooklyn Nets home games with an event date in July 2026?"). It was verified independently from the generated data (no Nets events in 2026-07) before running. Its contract passes on an empty event list or a verified `matching_events = 0`. The original held-out case and its 3/4 result are unchanged, and this behavior is **no longer unseen** on the held-out set.

**Live post-fix checks** (Sonnet 5.5, medium effort, SDK retries 0, app-enforced cap $0.50, run `postfix-20261003-*`):

| check | SQL (abridged) | result | answer | repairs | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- | --- |
| no_match_nets_jul_2026 (dev) | `COUNT(DISTINCT e.event_id) AS matching_events, COUNT(t.ticket_id) … FROM events e LEFT JOIN tickets t ON … AND t.status='sold' WHERE home_team='Brooklyn Nets' AND July 2026` | `matching_events=0, tickets_sold=0` | fixed no-match wording | 0 | 4.0 s | 2619/297 |
| contrast: Indie Showcase (2026-09-18, real event, 26 refunded, 0 sold) | same LEFT JOIN pattern, `e.name='Indie Showcase'` | `matching_events=1, tickets_sold=0` | "One event matched… 0 tickets sold (refunded tickets excluded)…" (legitimate zero kept) | 0 | 4.6 s | 3221/309 |

Spend for these two checks: **$0.0177** (3 calls, bounds held).

## Other live checks (2026-10-03)
- **Fresh setup** from a clean copy of the working tree (no `.git`, venv, DB, runs or secrets): `uv sync --locked` -> generate DB (2/50/5,088 rows) -> `uv run pytest -q` (169 passed at the time) -> one CLI question ("Which three upcoming events have sold the most tickets so far?"): correct (101 / 99 / 92 tickets, matching ground truth), 3,320/444 tokens, about $0.011, 9.5 s including `uv` startup.
- **Streamlit UI**, live: "What was ticket revenue by venue for events dated in August 2026?" Barclays Center 5 events / $63,472.00, Harborview Arena 2 events / $10,680.00, matching Python ground truth. It took 2 model calls (decide + summary). Expanding the SQL and an example-button rerun made **no** further calls.
  - A rendering defect was found and fixed: Streamlit markdown rendered two `$` amounts as a LaTeX span, dropping the dollar signs. Dollar signs are now escaped (with a regression test), and SQL lines wrap. Re-checked live: correct.
  - UI token usage isn't recorded; cost was about $0.011 per question (estimated from comparable CLI runs).

## Cumulative live spend
Dev batch $0.0835 + held-out $0.0370 + post-fix checks $0.0177 + fresh-setup CLI $0.0111 + 2 UI questions about $0.022 (estimated) = **about $0.17**.

## Post-evaluation usability refinement: date-basis interpretation (2026-10-03)

**Issue (from the browser acceptance check):** "Nets home games in July 2026" and "Barclays events from January through September 2026" got a purchase-vs-event-date clarifying question, even though the period clearly describes the games or events.

**Change (one sentence in `prompts.py`):** if the period describes the games or events, use `event_date`; if it describes buying, use `purchase_date`; state the basis in the assumptions. Ask only when the period describes the sale itself and could mean either ("tickets sold last month"). SQL safeguards, the no-match fix, schema, model and earlier evaluation records are unchanged.

**Regression set:** 6 newly constructed questions (3 patterns, each with a paraphrase), written with expected branches and values **before** the change: `evals/date_basis_checks.json`. Values come from plain Python, and the other date basis gives a different number, so the returned value shows which column was used.

| check | expected | date column in SQL | result | assumption stated | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- | --- |
| Nets home games in September 2026 | answer, event_date, 269 | event_date | 269 (2 matching games) | "September 2026 describes the games, so … event_date" | 5.2 s | 3369/363 |
| paraphrase: games played in September 2026 | answer, event_date, 269 | event_date | 269 | "'Played in September 2026' describes the games…" | 4.5 s | 3346/339 |
| tickets purchased in September 2026 | answer, purchase_date, 668 | purchase_date | 668 | "…because the question says tickets were purchased…" | 3.7 s | 3313/237 |
| paraphrase: tickets people bought during September 2026 | answer, purchase_date, 668 | purchase_date | 668 | "Period describes buying, so purchase_date…" | 3.3 s | 3291/230 |
| tickets sold last month | clarify | (none) | asks purchase vs event date for September 2026 | sale itself is ambiguous | 1.8 s | 2682/143 |
| paraphrase: ticket sales in September 2026 | clarify | (none) | asks purchase vs event date (and count vs revenue) | sale itself is ambiguous | 2.0 s | 2685/123 |

**6/6 as expected**, 0 repairs, 10 calls, **$0.0517** (cap $0.50, bounds held). Run once, with no tuning iterations.

Note: the dev (8/8 re-scored, 9 cases incl. post-fix) and held-out (3/4) results above were produced **before** this prompt change and were not rerun. This set is a small post-evaluation usability check, not new held-out evidence.

Cumulative live spend is now about $0.29 (including the browser acceptance check, about $0.07).

## Provider checks: OpenAI and Amazon Bedrock (2026-10-04)

**What was run.** `evals/validate_live.py --provider-check`: a structured-output smoke test plus the 4 checks in `evals/provider_checks.json`, frozen before either provider ran.
- Three checks are exact copies of known **dev** regression cases: the normal answer, the clarification, and no matching events.
- The fourth, `legit_zero_indie_showcase`, was new: a real event whose 26 tickets were all refunded, so `matching_events = 1` and `tickets_sold = 0`. It was verified in plain Python before any run.
- These are known regression checks, **not unseen held-out cases**, and not a full evaluation.
- Settings: SDK retries 0, the existing single application repair, the provider's default model, and a pre-dispatch cap enforced by the app from configured prices.
- The batch **stops at the first unexpected failure**.
- Expectations were not changed after any run.

### OpenAI: `gpt-6.1-sol`, reasoning effort medium (run `validate-20261004-190600`)

| step | expected | actual | result | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- |
| smoke test | `{"ok": true}` | `{"ok": true}` | pass | 5.0 s | 61/13 |
| Nets home games, event date Sept 2026, excl. refunds | answer 269 | 269, `matching_events` 2; event-date basis stated | pass | 7.5 s | 1821/225 |
| "How many tickets did we sell last month?" | clarify | asked purchase date vs event date for September 2026; no SQL | pass | 4.1 s | 1465/89 |
| Nets home games, event date July 2026 | no matching events | `matching_events` 0; fixed no-match wording | pass | 5.0 s | 1481/172 |
| "How many tickets were sold for Indie Showcase? Exclude refunded tickets." | 0 sold, 1 matching event | `[tickets_sold 0, matching_events 1]` | pass | 6.5 s | 1747/150 |

**5/5 passed.** 0 repairs, 7 calls, about **$0.020** estimated ($2 / $10 per million tokens).

Summary review (by hand, against the returned rows):
- "269 tickets … Two events matched" matches `[269, 2]`.
- "0 tickets were sold for Indie Showcase … 1 event matched" matches `[0, 1]`. It does not imply that the event is missing.
- The no-match answer is the app's fixed wording, with no summary-model call.
- No unsupported numbers.

Before this run, three attempts were rejected by OpenAI (HTTP 401, `invalid_api_key`) during key setup. No tokens were processed. The spend guard still counted about $0.25 of reservations for them, by design.

### Amazon Bedrock: `global.anthropic.claude-sonnet-4-6`, us-east-1, no extended thinking

**First attempt** (run `validate-20261004-184039`):
- Smoke test and the normal answer passed (269, `matching_events` 2).
- The next two calls failed with `ResourceNotFoundException`. CloudTrail recorded the message "Model use case details have not been submitted for this account."
- The adapter originally mislabeled this as "model not found". It now maps the error to a setup message naming the form, and a test covers it.
- The account owner then submitted the form. No other account change was made.

**Second attempt** (run `validate-20261004-191658`, about 15 minutes after the form):

| step | expected | actual | result | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- |
| smoke test | `{"ok": true}` | `{"ok": true}` | pass | 1.4 s | 180/8 |
| Nets home games, event date Sept 2026, excl. refunds | answer 269 | 269, `matching_events` 2; event-date basis stated | pass | 5.2 s | 2361/259 |
| "How many tickets did we sell last month?" | clarify | **answered** 668, choosing purchase date (stated as an assumption) | **FAIL** | 6.8 s | 2288/167 |
| Nets home games, July 2026 | no matching events | not run (batch stopped) | — | | |
| Indie Showcase | 0 sold, 1 matching event | not run (batch stopped) | — | | |

- 668 is the correct purchase-date count, and the basis was disclosed. But the app's rule is to **ask** when "sold last month" could mean either date basis, and this model resolved the ambiguity silently.
- With Bedrock as configured (Claude Sonnet 4.6, no extended thinking), **clarification behavior is not met**. No-match and zero-sales behavior is **unverified live**.
- No prompt was changed to accommodate this model.
- Spend: about **$0.030** estimated from reported tokens across both attempts ($3 / $15 per million tokens, the AWS Price List rate for this profile).
- The spend guard counted $0.092, because it charges the full reservation for the two calls that failed for lack of the form.

### Anthropic: `claude-sonnet-5-5`, effort medium (run `validate-20261004-192542`, same frozen batch)

Run so that all three providers have the same check, including the new Indie Showcase real-zero case.

| step | expected | actual | result | latency | tokens in/out |
| --- | --- | --- | --- | --- | --- |
| smoke test | `{"ok": true}` | `{"ok": true}` | pass | 2.4 s | 246/9 |
| Nets home games, event date Sept 2026, excl. refunds | answer 269 | 269, `matching_events` 2; event-date basis stated | pass | 6.0 s | 3380/373 |
| "How many tickets did we sell last month?" | clarify | asked purchase date vs event date for September 2026 | pass | 2.3 s | 2683/146 |
| Nets home games, event date July 2026 | no matching events | `matching_events` 0; fixed no-match wording | pass | 2.5 s | 2712/285 |
| Indie Showcase, excl. refunds | 0 sold, 1 matching event | `[tickets_sold 0, matching_events 1]` | pass | 4.0 s | 3292/267 |

**5/5 passed**, 0 repairs, about **$0.035** estimated.
- Summary review: "269 tickets … 2 matching events" matches the rows.
- "No tickets were sold for Indie Showcase … the count is 0. One event matched" matches `[0, 1]` and does not claim the event is missing.

### Spend for this step

| | estimated from reported tokens | counted by the spend guard (includes reservations for failed calls) |
| --- | --- | --- |
| OpenAI | about $0.020 | $0.272 |
| Bedrock | about $0.030 | $0.092 |
| Anthropic | about $0.035 | $0.035 |
| **Total** | **about $0.085** | **$0.399** |

The OpenAI and Bedrock batches ran under a $2.00 shared ceiling; the Anthropic batch under its own $0.25 cap. Cumulative estimated live spend across the project was then about $0.38.

## Post-fix regression checks: Bedrock clarification failure (2026-10-05)

**Original failure (kept above):** Bedrock run `validate-20261004-191658` answered "How many tickets did we sell last month?" with 668, a purchase-date count, instead of asking. Anthropic and OpenAI asked.

**Diagnosis from saved evidence (no model calls).**
- Confirmed:
  - The system instructions (5,647 characters), the user prompt and the JSON schema were byte-identical across the three adapters. They were rebuilt offline with each adapter's `build_request`.
  - The clarification rule was present in the Bedrock request, with no truncation and no adapter transformation beyond the shared strict-schema step.
  - The model's own assumption read "Using purchase_date as the date basis since the question is about when tickets were sold/purchased". It treated "sold" as "purchased".
  - Settings differ: Anthropic (adaptive thinking) and OpenAI (reasoning) both run with effort medium, while Bedrock runs Claude Sonnet 4.6 with no extended thinking.
- Hypotheses:
  - H1, rule wording: the rule mapped "buying" to purchase date and "the sale itself" to clarify, but never said that "sold" alone does not mean purchased. It also said "Clarify **only** when…", which leans toward answering.
  - H2, no reasoning: the Bedrock configuration has none.
- Only H1 was tested, as one fix. H2 was not tested, and the model and settings were not changed.

**Fix (one change, `prompts.py`).** The date-basis rule was restated as three ordered rules:
1. A period describing games or events → `event_date`.
2. An explicit bought or purchased period → `purchase_date`.
3. A period attached only to selling or sales → unresolved: ask. "Sold" or "sales" alone does not mean purchase date.

The rule's example changed from "tickets sold last month" to "tickets sold in August", so the prompt no longer nearly copies an evaluation question. A new test asserts that no date-basis or provider-check question appears in the runtime prompt. Ground truth in `date_basis_checks.json` and `provider_checks.json` is unchanged; a test pins it.

**Validation.** New runner `evals/run_date_basis.py`. It runs the failed provider check plus the 6 frozen date-basis checks, scored on branch, value, contrast value and the date column in the SQL. SDK retries 0, an app-enforced cap, one application repair allowed. Run once per provider, no tuning iterations.

| provider (model) | failed "sell last month" check | 6 date-basis checks | provider check (5 steps) | spend (estimated) | run |
| --- | --- | --- | --- | --- | --- |
| Anthropic (`claude-sonnet-5-5`) | asks | 6/6 | not rerun (5/5 on 2026-10-04, before the fix) | $0.060 | `date-basis-20261005-110420` |
| OpenAI (`gpt-6.1-sol`) | asks | 6/6 | not rerun (5/5 on 2026-10-04, before the fix) | $0.034 | `date-basis-20261005-110542` |
| Bedrock (`global.anthropic.claude-sonnet-4-6`) | **asks** (was: answered 668) | 6/6 | **5/5** (smoke, 269, clarify, no-match, Indie Showcase real zero) | $0.062 + $0.038 | `date-basis-20261005-110947`, `validate-20261005-111014` |

**Review of answers and assumptions (by hand).**
- Every clear-basis question was still answered directly, with the basis stated: 269 via `event_date`; 668 via `purchase_date`, with the contrast values 308 and 721 absent.
- Every unresolved "sold/sales" question got a clarifying question offering purchase date vs event date. Some also asked count vs revenue.
- Bedrock volunteered an unrequested revenue figure, $63,334.00, in one purchase-date answer. It came from the returned rows and matches plain Python, but it is extra detail.
- Bedrock's clarifying questions use short markdown lists.
- No unsupported numbers in any summary.
- **No regressions** on Anthropic or OpenAI.

**Caveats.**
- These are post-fix regression checks on known questions, not held-out evidence.
- Anthropic and OpenAI did not rerun the full provider check after the fix. The part the fix affects (date basis and clarification) was rerun.
- The dev and held-out scores predate both date-basis prompt changes.
- OpenAI latency was higher on this day (7–16 s per question).
- Spend for this repair: about **$0.193** estimated (authorized $2.00). Cumulative project estimate: about $0.57.
