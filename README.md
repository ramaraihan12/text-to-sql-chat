# text-to-sql-chat

![ci](https://github.com/ramaraihan12/text-to-sql-chat/actions/workflows/ci.yml/badge.svg)

Ask a sales database questions in plain English or Bahasa Indonesia and get back the SQL, the result table, a one-line answer and a chart when the result has a shape worth plotting. The same guarded query tools are also an MCP server, so Claude Desktop or Cursor can use them too.

**Live demo: [text-to-sql-chat.streamlit.app](https://text-to-sql-chat.streamlit.app)**, no login needed. It runs on Streamlit Community Cloud with Neon Postgres and Llama 3.3 70B on Cloudflare Workers AI. Streamlit puts the app to sleep after 12 hours without traffic, so you may need to click the button to wake it and wait a minute. Questions are capped per visit and to 40 a day across everyone, which keeps it inside the free Workers AI allowance. The day resets at midnight UTC.

**Tracing.** If `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set (plus `LANGFUSE_HOST`, default Langfuse Cloud), about a quarter of questions (`TRACE_SAMPLE_RATE`, picked by a hash of the question) are sent to Langfuse after the answer is shown: the question, the SQL, the answer and the row count, with emails, phone numbers and long digit runs removed. It's off without the keys, runs on a background thread with a 2 second timeout, and a failure is only logged. The chat page says so under the title.

The data is Kopi Lereng, a made-up coffee chain: 14 stores in 10 Indonesian cities, 24 products, 700 loyalty members, about 3,700 orders and 6,800 order lines from January 2025 to September 2026. It's generated from a fixed seed, so every install has the same rows.

![Streamlit app answering "Revenue per city in September 2026"](docs/screenshot.png)

From the terminal, against local Ollama (table trimmed):

```
$ uv run sqlchat ask "Produk apa yang paling laris di Bandung tahun 2026?"
SELECT
  p.name AS product_name,
  SUM(oi.quantity) AS total_sold
FROM order_items AS oi
JOIN orders AS o
  ON oi.order_id = o.id
JOIN stores AS s
  ON o.store_id = s.id
JOIN products AS p
  ON oi.product_id = p.id
WHERE
  s.city = 'Bandung'
  AND o.status = 'completed'
  AND o.ordered_at >= '2026-01-01'
  AND o.ordered_at < '2027-01-01'
GROUP BY
  p.name
ORDER BY
  total_sold DESC
LIMIT 200

product_name            total_sold
----------------------  ----------
Es Kopi Susu Gula Aren  84
Cappuccino              64
Caffe Latte             48
Es Kopi Pandan          36
...

Produk yang paling laris di Bandung tahun 2026 adalah Es Kopi Susu Gula Aren dengan total penjualan 84 unit.
```

`LIMIT 200` is the row cap. The validator adds it when the model leaves it out and lowers anything bigger.

## How it works

- The model gets the schema built from Postgres table and column comments, plus three sample rows per table. The comments carry the business rules ("only `completed` orders are revenue"), so the prompt stays short.
- Its SQL is parsed with sqlglot. Only one SELECT over the five sales tables gets through: no DML or DDL, no writes hidden in a CTE, no `SELECT INTO`. Postgres-only functions have to be on a short allowlist, so `pg_sleep`, `pg_read_file`, `dblink` and friends are out. Casts are limited to plain data types too, because `10::regrole` is a catalog lookup in disguise. What runs is the re-rendered tree, with a `LIMIT` added or clamped.
- It runs as a separate Postgres role that can only `SELECT` those tables, in a read-only transaction with a statement timeout. If the validator ever misses something, the database still refuses writes and anything over the timeout. Catalog views like `pg_roles` stay readable to every role, though, so keeping queries on the sales tables is the validator's job.
- When Postgres returns an error (wrong column, bad GROUP BY) or the validator rejects the query (a made-up table, a function that isn't allowed), the reason goes back to the model once for a corrected query.
- A second short call turns the rows into one sentence in the question's language. The chart is picked by plain rules: label + number is a bar chart, date + number is a line chart, anything else is just a table.
- The chat UI limits what it can spend on the model: questions up to 300 characters, 10 per visit and 50 a day across everyone, all set in `.env`. A page reload clears the per-visit count, so the daily cap is the real limit. It's counted in Postgres so it survives restarts, by a separate `usage_counter` role that can only touch that one table. The read-only role stays read-only, and no model-written SQL runs on that connection. If the counter can't be reached, no question goes to the model.
- `sqlchat-mcp` exposes `describe_schema`, `check_sql` and `run_sql` over stdio. There the client's model writes the SQL, and it goes through the same checks.

## Stack

Python 3.12, uv, LangChain (`init_chat_model`, Ollama by default, Llama 3.3 on Cloudflare Workers AI on the live demo), psycopg 3, sqlglot, Streamlit, MCP Python SDK 2 (`MCPServer`, formerly FastMCP), PostgreSQL 17, pytest, ruff.

## Run it

You need Postgres and [Ollama](https://ollama.com). Compose can start the database for you.

```bash
git clone https://github.com/ramaraihan12/text-to-sql-chat.git
cd text-to-sql-chat
uv sync
cp .env.example .env
docker compose up -d db
uv run sqlchat setup
ollama pull qwen3.5:9b
uv run streamlit run streamlit_app.py
```

`sqlchat setup` creates the tables, loads the seed data and creates the read-only `sales_reader` role and the `usage_counter` role. It's safe to run again, and it leaves the daily counter alone. Settings live in `.env` (see `.env.example`). On my laptop I use a smaller quant, `LLM_MODEL=qwen3.5-9b:iq4xs`, because it fits a 6 GB GPU.

Other providers work through the same config. The live demo uses Cloudflare Workers AI, which has a free daily allowance: set `LLM_PROVIDER=cloudflare`, `LLM_MODEL=@cf/meta/llama-3.3-70b-instruct-fp8-fast`, `CF_ACCOUNT_ID` and a `CF_API_TOKEN` with the Workers AI permission. It goes through the OpenAI-compatible endpoint, so any Workers AI chat model that follows instructions should work. Gemini is installed too: set `LLM_PROVIDER=google_genai`, a Gemini model name in `LLM_MODEL` and `GOOGLE_API_KEY` (the free Gemini API tier isn't offered to users in the EEA, Switzerland or the UK, which is why the public demo doesn't use it). OpenAI works the same way with `LLM_PROVIDER=openai` and `OPENAI_API_KEY`. For Anthropic: `uv sync --extra anthropic`, then `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

One question without the UI:

```bash
uv run sqlchat ask "Which city had the highest revenue in September 2026?"
```

Everything in Docker (Postgres, the setup job and the app on http://localhost:8501, with Ollama still on the host):

```bash
docker compose up --build
```

### Use it from Claude Desktop or Cursor

Add this to `claude_desktop_config.json` (Settings, Developer, Edit Config) or to Cursor's `mcp.json`, with your own path:

```json
{
  "mcpServers": {
    "kopi-lereng-sales": {
      "command": "uv",
      "args": ["--directory", "/path/to/text-to-sql-chat", "run", "sqlchat-mcp"]
    }
  }
}
```

The server reads the same `.env`, so it connects as the read-only role. Then ask something like "which store had the most delivery orders last quarter?" and watch it call `describe_schema` and `run_sql`.

## Tests

```bash
uv run pytest
```

The validator cases, the pipeline (with a fake chat model, including the retry path) and the MCP tools run offline. The integration tests are skipped unless you point them at a scratch database. They rebuild its tables, check that the role really can't write, read other tables or run past the timeout, and run the re-rendered SQL on real Postgres:

```bash
TEST_ADMIN_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/text_to_sql \
TEST_DATABASE_URL=postgresql://sales_reader:sales_reader@localhost:5432/text_to_sql \
TEST_USAGE_DATABASE_URL=postgresql://usage_counter:usage_counter@localhost:5432/text_to_sql \
uv run pytest -m integration
```

## Notes

- A parser allowlist is easy to get subtly wrong. My first version collected CTE names from the whole query, so `WITH pg_roles AS (...)` inside a subquery let the real `pg_roles` through outside it. That probe and a few cousins are test cases now. Casts got me the same way: `g::regrole` over `generate_series` listed every role on the server without naming a catalog table. For writes the role and the read-only transaction are the real wall; for catalog reads the validator is all there is.
- With a 9B model on a 6 GB RTX 3060 a question takes about 5 to 10 seconds once the model is loaded. The first one after a model swap is slower.
- On Cloudflare a question takes 3 to 6 seconds and costs roughly 70 to 150 Workers AI neurons (two model calls, three when a query is retried), so the 10,000 a day free allowance covers well over the 40-question cap. The cap leaves room for another demo on the same account.
- A 9B model handles the everyday questions here, but it's still a 9B model. Read the SQL before you trust a number; it's always one click away.

## Eval gate

Pull requests that touch `src/`, `evals/`, `pyproject.toml`, `uv.lock` or the workflows run [evalgate](https://github.com/ramaraihan12/evalgate): 50 questions with known answers (`evals/sql_cases.jsonl`, a few in Bahasa Indonesia and four the data can't answer) go through the real pipeline on the PR and on its base, and a bot comment shows both scores. The check is called `Eval gate` and fails only when the PR is less accurate and the drop is bigger than noise (an exact McNemar test, p < 0.05). The model is `qwen2.5-coder:3b` on a GitHub runner, pinned by digest, not the Llama 3.3 the live demo uses. With 50 cases only large drops show up, so a pass means "no big drop", not "nothing changed".

## License

MIT

Built with Llama. The live demo runs Llama 3.3 under the [Llama 3.3 Community License](https://www.llama.com/llama3_3/license/).
