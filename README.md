# text-to-sql-chat

![ci](https://github.com/ramaraihan12/text-to-sql-chat/actions/workflows/ci.yml/badge.svg)

Ask a sales database questions in plain English or Bahasa Indonesia and get back the SQL, the result table, a one-line answer and a chart when the result has a shape worth plotting. The same guarded query tools are also an MCP server, so Claude Desktop or Cursor can use them too.

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
- Its SQL is parsed with sqlglot. Only one SELECT over the five sales tables gets through: no DML or DDL, no writes hidden in a CTE, no `SELECT INTO`, no `pg_sleep`, `pg_read_file`, `dblink` and friends. What runs is the re-rendered tree, with a `LIMIT` added or clamped.
- It runs as a separate Postgres role that can only `SELECT` those tables, in a read-only transaction with a statement timeout. If the validator ever misses something, the database still says no.
- When Postgres returns an error (wrong column, bad GROUP BY), the error goes back to the model once for a corrected query.
- A second short call turns the rows into one sentence in the question's language. The chart is picked by plain rules: label + number is a bar chart, date + number is a line chart, anything else is just a table.
- `sqlchat-mcp` exposes `describe_schema`, `check_sql` and `run_sql` over stdio. There the client's model writes the SQL, and it goes through the same checks.

## Stack

Python 3.12, uv, LangChain (`init_chat_model`, Ollama by default), psycopg 3, sqlglot, Streamlit, MCP Python SDK 2 (`MCPServer`, formerly FastMCP), PostgreSQL 17, pytest, ruff.

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

`sqlchat setup` creates the tables, loads the seed data and creates the read-only `sales_reader` role. It's safe to run again. Settings live in `.env` (see `.env.example`). On my laptop I use a smaller quant, `LLM_MODEL=qwen3.5-9b:iq4xs`, because it fits a 6 GB GPU.

Other providers work through the same config. For OpenAI: `uv sync --extra openai`, then set `LLM_PROVIDER=openai`, an OpenAI model name in `LLM_MODEL` and `OPENAI_API_KEY`. The `gemini` (provider `google_genai`) and `anthropic` extras work the same way.

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

The validator cases, the pipeline (with a fake chat model, including the retry path) and the MCP tools run offline. The integration tests are skipped unless you point them at a scratch database. They rebuild its tables, check that the role really can't write or run past the timeout, and run the re-rendered SQL on real Postgres:

```bash
TEST_ADMIN_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/text_to_sql \
TEST_DATABASE_URL=postgresql://sales_reader:sales_reader@localhost:5432/text_to_sql \
uv run pytest -m integration
```

## Notes

- I don't trust the validator alone. A parser allowlist is easy to get subtly wrong, so the role permissions and read-only transaction are the real wall. The validator mostly gives the model a clear reason it can act on.
- With a 9B model on a 6 GB RTX 3060 a question takes about 5 to 10 seconds once the model is loaded. The first one after a model swap is slower.
- A 9B model handles the everyday questions here, but it's still a 9B model. Read the SQL before you trust a number; it's always one click away.
- Next I'd add a small eval set of questions with known answers, so a prompt or model change shows up as a number instead of a feeling.

## License

MIT
