# Ask Ticketing

Ask plain-English questions about ticket sales, revenue, prices and event inventory. You get an answer, the supporting rows and the SQL behind it.

All data is synthetic, with a fixed as-of date of October 1, 2026.

## Run locally

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/) (the Python environment and package manager), and credentials for one supported model provider. API usage is billed to your own provider account.

```bash
git clone https://github.com/ausarenglish/bkse-ask-ticketing.git
cd bkse-ask-ticketing
```

Choose one provider and set its credentials in the terminal you will use to launch the app.

**Anthropic**

```bash
export ASK_TICKETING_PROVIDER=anthropic
export ANTHROPIC_API_KEY="<your-anthropic-api-key>"
```

**OpenAI**

```bash
export ASK_TICKETING_PROVIDER=openai
export OPENAI_API_KEY="<your-openai-api-key>"
```

**Amazon Bedrock** requires working AWS credentials and access to the configured Claude model (Claude Sonnet 4.6).

```bash
export ASK_TICKETING_PROVIDER=bedrock
export ASK_TICKETING_AWS_PROFILE="<your-aws-profile>"   # optional; omit to use your default AWS credentials
export ASK_TICKETING_REGION=us-east-1
```

Then install, generate the data and start the app:

```bash
uv sync --locked
uv run ask-ticketing-generate-data
uv run streamlit run src/ask_ticketing/app.py
```

Open [http://localhost:8501](http://localhost:8501). If the database already exists, skip the generation step.

## Try it

Ask:

> How many tickets were sold for Brooklyn Nets home games with an event date in September 2026?

The expected answer is 269 tickets. Each answer also lists the assumptions the app made, the rows it used and the SQL it ran.

You can save questions for reuse, clear the page to start over, and download the supporting rows as CSV. In the CSV, money columns ending in `_cents` contain cents.

## Under the hood

Ask Ticketing is built with Python, LangGraph, SQLite and Streamlit. The model sits behind interchangeable provider adapters for Anthropic, OpenAI and Amazon Bedrock. Generated SQL runs read-only against the database, with limits on execution time and returned rows.

## Tests and limitations

The test suite needs no credentials:

```bash
uv run pytest -q
```

All three providers were tested live. See [evaluation results](evals/RESULTS.md) for coverage and limitations.

Keep in mind:

- Valid SQL can still answer the wrong question. Check the assumptions and SQL shown with each answer.
- When the app asks a clarifying question, resubmit a more specific question.
- Saved questions are shared by everyone using the same local installation.
- The app and command line have no total spend cap.

## AI assistance

This project was built with Claude Code for implementation, testing and documentation, and with Claude's browser automation for UI checks. ChatGPT was used for planning, design review and presentation preparation.
