# Agent Builder Metadata API

FastAPI metadata server for the React Agent Workflow Builder.

**Phase 1** — project skeleton: settings, logging, CORS, `/health`.
**Phase 2** — MongoDB integration via the **PyMongo Async API**
(`pymongo.AsyncMongoClient`; Motor is deprecated and reaches end-of-life
on 2026-05-14). Adds the connection lifecycle, collection constants,
index plan, index creation service, and a `/health/db` readiness probe.

Workflow CRUD, validation, versioning, and execution arrive in later phases.

## Requirements

- Python 3.11+
- MongoDB 4.4+ (replica set required for transactions in later phases;
  a standalone single-node Mongo is fine for Phase 2 read/index work)

## Project layout

```
agent_builder_metadata_api/
├── pyproject.toml
├── README.md
├── .env.example
├── config.example.yaml
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + lifespan + uvicorn entrypoint
│   ├── config.py            # pydantic-settings (env > .env > yaml > defaults)
│   ├── logging_setup.py     # dictConfig logging
│   ├── deps.py              # FastAPI dependencies (get_mongo, get_db)
│   ├── api/
│   │   ├── __init__.py
│   │   └── health.py        # GET /health, GET /health/db
│   └── db/
│       ├── __init__.py
│       ├── mongo.py         # MongoManager (PyMongo Async)
│       ├── collections.py   # WORKFLOWS / WORKFLOW_VERSIONS / WORKFLOW_AUDIT_LOGS
│       └── indexes.py       # IndexModel lists + ensure_indexes()
└── tests/
    ├── conftest.py
    ├── test_health.py
    ├── test_indexes.py            # unit, no Mongo needed
    └── test_mongo_integration.py  # requires TEST_MONGODB_URI
```

## Configuration

Three layers, highest precedence first:

1. Process environment variables
2. `.env` file in the project root
3. YAML file at `./config.yaml` (override path with `CONFIG_FILE=...`)

Bootstrap:

**PowerShell**
```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```

**bash**
```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

### MongoDB settings

| Variable | YAML key | Default | Purpose |
|---|---|---|---|
| `MONGODB_URI` | `mongodb_uri` | `mongodb://localhost:27017` | Connection string. Atlas: `mongodb+srv://USER:PASS@CLUSTER/...` |
| `MONGODB_DB` | `mongodb_db` | `agent_workflows` | Database name |
| `MONGODB_CONNECT_TIMEOUT_MS` | `mongodb_connect_timeout_ms` | `5000` | TCP connect timeout |
| `MONGODB_SERVER_SELECTION_TIMEOUT_MS` | `mongodb_server_selection_timeout_ms` | `5000` | How long to wait for a healthy server |
| `MONGODB_SKIP_STARTUP` | `mongodb_skip_startup` | `false` | Skip startup ping + index creation (used by unit tests) |

Configure `MONGODB_URI` either way:

**.env**
```ini
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=agent_workflows
```

**config.yaml**
```yaml
mongodb_uri: "mongodb://localhost:27017"
mongodb_db: "agent_workflows"
```

**One-off shell override (PowerShell)**
```powershell
$env:MONGODB_URI = "mongodb+srv://user:pass@cluster.mongodb.net"
python -m app.main
```

**One-off shell override (bash)**
```bash
MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net" python -m app.main
```

If MongoDB is unreachable at startup, the service still boots: a warning is
logged and `/health/db` returns 503 until the connection succeeds.

## Setup

**PowerShell**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

**bash**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run a local MongoDB (one option)

Quickest path with Docker:

```bash
docker run -d --name agent-mongo -p 27017:27017 mongo:7
```

For replica-set transactions (needed in later phases):

```bash
docker run -d --name agent-mongo -p 27017:27017 mongo:7 \
  --replSet rs0 --bind_ip_all
docker exec -it agent-mongo mongosh --eval "rs.initiate()"
```

## Run the server

```bash
python -m app.main
```

Or invoke uvicorn directly:
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

OpenAPI docs at `http://127.0.0.1:8000/docs`.

## Run tests

```bash
pytest                              # unit tests only
TEST_MONGODB_URI=mongodb://localhost:27017 pytest -m integration -v
```

## Verify endpoints

### Liveness — process is up

```bash
curl http://127.0.0.1:8000/health
```
```json
{"status":"ok","app":"Agent Workflow Metadata API","version":"0.1.0","env":"dev"}
```

### Readiness — MongoDB connection works

```bash
curl -i http://127.0.0.1:8000/health/db
```

On success (200):
```json
{"status":"ok","mongodb":{"ok":1.0,"db":"agent_workflows","server_version":"7.0.x"}}
```

On failure (503): `detail.mongodb.error` describes the underlying PyMongoError.

PowerShell equivalents:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/db
```

### Verify indexes were created

After hitting `/health/db` once (or any startup with `MONGODB_SKIP_STARTUP=false`),
the indexes listed in `app/db/indexes.py` exist on the configured database:

```bash
docker exec -it agent-mongo mongosh agent_workflows --eval \
  'db.workflows.getIndexes(); db.workflow_versions.getIndexes(); db.workflow_audit_logs.getIndexes()'
```

## What's indexed

`workflows`:
- `workflow_id` unique
- `slug` unique sparse
- `owner_id`, `status`, `tags`, `category`, `node_types`, `current_version`
- `updated_at` desc, `created_at` desc

`workflow_versions`:
- `(workflow_id asc, version asc)` unique compound

`workflow_audit_logs`:
- `(workflow_id asc, created_at desc)` compound
