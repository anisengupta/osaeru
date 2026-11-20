# osaeru

>The Japanese verb osaeru (おさえる) means "to hold down," "to restrain," >"to suppress," or "to grasp/comprehend"

![alt text](docs/image.png)

A personal jiu jitsu training companion for iPad. Runs
on Cloud Run in GCP and calls out to the
Claude API when there's a connection, to parse session notes into a skills
flowchart, chat about training, and generate recommendations.

## How it works

### Architecture

The same app runs in two places. On Cloud Run it sits behind Identity-Aware
Proxy and uses Cloud SQL; on your Mac it uses a local SQLite file and the
keychain. `database.py` and `claude_sync.py` pick the right one from
environment variables.

```mermaid
flowchart TB
    ipad["iPad Safari<br/>(production)"]
    browser["Browser on your Mac<br/>(development)"]

    subgraph gcp["Google Cloud"]
        iap["Identity-Aware Proxy<br/>Google sign-in, only your account"]
        cloudsql[("Cloud SQL<br/>PostgreSQL 16")]
    end

    subgraph app["Osaeru app: Cloud Run in production, localhost:8000 locally"]
        web["web/main.py<br/>FastAPI routes + Jinja pages"]
        graphmod["web/graph.py<br/>flowchart data for Cytoscape"]
        sync["claude_sync.py<br/>Claude calls + API key storage"]
        dbmod["database.py<br/>SQLAlchemy tables + queries"]
    end

    subgraph mac["Your Mac only"]
        sqlite[("SQLite file<br/>~/.osaeru/osaeru.sqlite3")]
        keychain["macOS keychain"]
    end

    claude["Anthropic API<br/>(Claude)"]

    ipad -->|HTTPS| iap --> web
    browser -->|"python -m osaeru.web"| web
    web --> graphmod
    web --> sync
    web --> dbmod
    sync --> dbmod
    sync -->|"analyse notes, chat"| claude
    dbmod ==>|"Cloud Run: CLOUD_SQL_INSTANCE set<br/>(API key stored in app_settings)"| cloudsql
    dbmod -.->|"locally: no DB settings"| sqlite
    sync -.->|"locally: API key"| keychain
```

### What happens when you save a note

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant Web as web/main.py
    participant DB as database.py
    participant Sync as claude_sync.py
    participant Claude as Anthropic API

    You->>Web: Save note (Notes page)
    Web->>DB: add_session_note()
    DB-->>DB: store note + add to sync_queue
    Web-->>You: Note saved, "Claude is reading your note…"
    You->>Web: POST /sync (htmx, automatic)
    Web->>Sync: run_sync()
    Sync->>DB: pending notes + current flowchart
    Sync->>Claude: note + flowchart
    Claude-->>Sync: new skills, connections, recommendations (JSON)
    Sync->>DB: upsert skills and edges, add recommendations, mark synced
    Web-->>You: "Analysed 1 note. 2 new skills on your flowchart."
    You->>Web: Open Flowchart
    Web->>DB: get_flowchart()
    Web-->>You: Cytoscape draws the graph in the browser
```

## Setting your Claude API key

Open the **Settings** tab, paste your key and press Save. The app checks the
key against the API before saving it (if you're offline it saves anyway and
says so). On desktop the key goes in the OS keychain via `keyring`; on iOS,
where `keyring` has no backend, it goes in a private file in the app's
sandboxed data directory. It is never written to the database or a config file.

## Version control

This directory is a normal Python project - `git init` and commit as-is.
The `.gitignore` already excludes the local SQLite file, virtualenvs, and
Briefcase build artefacts, so nothing personal or generated gets checked in.

## Web front end

`src/osaeru/web/` is a browser front end built on the same `database.py` and
`claude_sync.py` as the desktop app. It runs anywhere a browser does,
including Safari on an iPad (Add to Home Screen opens it full-screen).

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m osaeru.web          # http://127.0.0.1:8000
```

It uses the same SQLite file and keychain key as the desktop app.
`OSAERU_DB_PATH` points it at a different SQLite file, `OSAERU_RELOAD=1`
auto-reloads while developing, and `PORT` / `OSAERU_HOST` change where it
listens. It has no login, so it only listens on localhost by default; don't
expose it on a shared network.

Tests: `pytest`. For a coverage report:
`pytest --cov=osaeru --cov-report=term-missing`.

Tests never touch your real data: each one runs against a temp database, a
temp home folder and an in-memory keychain, and Claude is replaced by a fake
client, so the suite runs offline and costs nothing. A few tests are marked
`xfail` to document known gaps; they'll start failing loudly (strict mode)
once the gap is fixed, as a reminder to remove the marker.

## Database: SQLite locally, Cloud SQL on Cloud Run

`database.py` uses SQLAlchemy, so the same code runs on SQLite and
PostgreSQL. Environment variables pick which one, in this order:

| Set | Uses |
|---|---|
| `OSAERU_DATABASE_URL` | Any SQLAlchemy URL, e.g. `postgresql+pg8000://user:pass@localhost/osaeru` to try Postgres locally |
| `CLOUD_SQL_INSTANCE` | Cloud SQL for PostgreSQL via the Cloud SQL Python Connector (see below) |
| nothing | SQLite at `OSAERU_DB_PATH`, else `~/.osaeru/osaeru.sqlite3` |

Running locally needs nothing set, and existing SQLite files (including ones
made before this change) are used as-is. On Cloud Run, if neither database
variable is set the app refuses to start rather than quietly writing to a
SQLite file that disappears when the instance stops.

Cloud SQL settings:

| Variable | Meaning |
|---|---|
| `CLOUD_SQL_INSTANCE` | Instance connection name, `project:region:instance` |
| `DB_NAME` | Database name (default `osaeru`) |
| `DB_USER` | Database user. For IAM auth this is the service account email without `.gserviceaccount.com` |
| `DB_PASS` | Password. Leave unset to use IAM database authentication (recommended) |
| `CLOUD_SQL_IP_TYPE` | `public` (default) or `private` |

Tables are created automatically on first connection.

### Deploying (outline)

```bash
PROJECT=your-project REGION=europe-west2 INSTANCE=osaeru-db
SA=osaeru-run@$PROJECT.iam.gserviceaccount.com

gcloud sql instances create $INSTANCE --database-version=POSTGRES_16 \
  --edition=ENTERPRISE --tier=db-f1-micro --region=$REGION \
  --database-flags=cloudsql.iam_authentication=on
gcloud sql databases create osaeru --instance=$INSTANCE

gcloud iam service-accounts create osaeru-run
for role in roles/cloudsql.client roles/cloudsql.instanceUser; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=$role
done
gcloud sql users create osaeru-run@$PROJECT.iam --instance=$INSTANCE --type=cloud_iam_service_account
# Then, as the postgres user, let it create tables in the public schema:
#   GRANT ALL ON SCHEMA public TO "osaeru-run@your-project.iam";

gcloud run deploy osaeru --source . --region=$REGION --service-account=$SA \
  --no-allow-unauthenticated \
  --set-env-vars=CLOUD_SQL_INSTANCE=$PROJECT:$REGION:$INSTANCE,DB_NAME=osaeru,DB_USER=osaeru-run@$PROJECT.iam
```

Keep it behind authentication (`--no-allow-unauthenticated` plus IAP):
the app has no login of its own.

Testing against Postgres: the suite runs on SQLite by default. To run it
against a real (empty, throwaway) Postgres database as well:

```bash
OSAERU_TEST_DATABASE_URL=postgresql+pg8000://user@localhost:5432/osaeru_test pytest
```
