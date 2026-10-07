# Bulk Certificate Generator API

A backend API that accepts a list of recipients in **one request**, validates each recipient, generates a PDF certificate for every valid one from a predefined template, and lets the client **track progress** and **download** the results (one by one or as a ZIP).

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (any SQLAlchemy-supported relational DB works, e.g. PostgreSQL) · ReportLab (PDF) · pytest

---

## 1. Setup

```bash
git clone <your-repo-url>
cd bulk-certificate-generator

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Configuration is optional. Defaults work out of the box. To override them, copy `.env.example` to `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `CERTGEN_DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL (e.g. `postgresql+psycopg://...`) |
| `CERTGEN_STORAGE_DIR` | `./storage/certificates` | Where PDFs are written |
| `CERTGEN_MAX_RECIPIENTS_PER_JOB` | `1000` | Upper bound per request |
| `CERTGEN_WORKER_THREADS` | `2` | Jobs processed concurrently |
| `CERTGEN_PROCESSING_MODE` | `background` | `background` or `inline` (synchronous, for debugging) |

## 2. Run the application

```bash
uvicorn app.main:app --reload
```

- Interactive API docs (Swagger): http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

Tables are created automatically on startup.

## 3. Run the tests

```bash
pytest
```

39 tests. Each test gets its own temporary database and storage folder, and jobs run in `inline` mode so results are deterministic.

| Test file | Covers |
|---|---|
| `test_create_job.py` | Creating a job, 202 response, defaults, trimming |
| `test_validation.py` | Request-level 422s, per-recipient validation, duplicates, limits |
| `test_generation.py` | PDF content (name, course, issuer, date, ID), atomic write, long names |
| `test_job_status.py` | Pending → processing → completed, live progress mid-job, pagination, filters |
| `test_failure_handling.py` | One failure doesn't stop others, all-fail → `failed`, retry, crash recovery |
| `test_retrieval.py` | Single PDF download, ZIP download, 404/409 cases |

## 4. Submit a certificate generation request

`POST /api/v1/jobs`

```json
{
  "certificate": {
    "course_name": "Drone Data Analytics Bootcamp",
    "issued_by": "AEREO Academy",
    "issue_date": "2026-10-07"
  },
  "recipients": [
    { "name": "Asha Rao", "email": "asha@example.com" },
    { "name": "Vikram Singh", "email": "vikram@example.com" },
    { "name": "Rahul Menon", "email": "not-an-email" }
  ]
}
```

`issue_date` is optional and defaults to today.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs -H "Content-Type: application/json" -d @samples/request.json
```

On Windows PowerShell, use `curl.exe` instead of `curl`.

**Response `202 Accepted`:**

```json
{
  "id": "d1f85f57-b0fb-4488-a3a8-887905789e77",
  "status": "pending",
  "certificate": { "course_name": "...", "issued_by": "...", "issue_date": "2026-10-07" },
  "progress": { "total": 3, "pending": 2, "generated": 0, "failed": 0, "invalid": 1,
                "processed": 1, "percent_complete": 33.3 },
  "invalid_recipients": [
    { "index": 2, "errors": ["email: value is not a valid email address: ..."] }
  ],
  "links": {
    "self": "/api/v1/jobs/d1f8...",
    "certificates": "/api/v1/jobs/d1f8.../certificates",
    "failed_certificates": "/api/v1/jobs/d1f8.../certificates?status=failed",
    "download_zip": "/api/v1/jobs/d1f8.../download"
  }
}
```

## 5. Track progress and retrieve certificates

| Method & path | Description |
|---|---|
| `GET /api/v1/jobs/{job_id}` | Job status and progress counts |
| `GET /api/v1/jobs/{job_id}/certificates?status=&limit=&offset=` | Per-recipient results (paginated; filter by `pending`, `generated`, `failed`, or `invalid`) |
| `GET /api/v1/jobs/{job_id}/download` | ZIP of all generated PDFs (works mid-job too; `X-Job-Status` header tells you whether the job has finished) |
| `GET /api/v1/certificates/{certificate_id}` | One certificate's status |
| `GET /api/v1/certificates/{certificate_id}/download` | One certificate PDF |
| `POST /api/v1/jobs/{job_id}/retry` | Re-run only the `failed` certificates of a finished job |

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=failed"
curl -o certificates.zip http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
curl -o cert.pdf http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
```

**End-to-end demo** (server must be running; uses only the Python standard library):

```bash
python scripts/demo.py                    # submits samples/request.json, polls, saves the ZIP to output/
python scripts/demo.py --recipients 1000  # synthetic 1000-recipient batch
```

### Status values

| Job status | Meaning |
|---|---|
| `pending` | Accepted, waiting for a worker |
| `processing` | Certificates are being generated |
| `completed` | Every recipient got a certificate |
| `completed_with_errors` | Some generated; some `failed` or `invalid` |
| `failed` | Nothing could be generated (or an unexpected job-level error, see `error`) |

| Certificate status | Meaning |
|---|---|
| `pending` | Valid, not generated yet |
| `generated` | PDF available for download |
| `failed` | Valid data, but generation raised an error (`error` has the reason). Retryable. |
| `invalid` | Rejected by validation (`error` lists the problems). Never generated. |

---

## 6. Design decisions

### Architecture

```
app/
  main.py         app factory, lifespan (DB init, worker start, crash recovery)
  api.py          HTTP layer: routing, status codes, response shaping
  services.py     job creation, progress queries, retry reset (no FastAPI imports)
  validation.py   per-recipient validation
  processing.py   JobProcessor: generates the certificates of a job
  generator.py    the predefined certificate template (ReportLab → PDF)
  worker.py       Dispatcher interface + thread-pool / inline implementations
  models.py       SQLAlchemy models (Job, Certificate)
  schemas.py      Pydantic request/response models
```

### Background processing (not synchronous)

A request can contain up to 1,000 recipients. Generating them inside the HTTP request would make the client wait, risk proxy timeouts, and lose all work if the connection drops. Instead:

1. `POST /jobs` validates the recipients, stores the job and one row per recipient in **one transaction**, and returns **`202 Accepted`** with a job id.
2. The job is handed to a **`Dispatcher`** *after the commit*, so the worker always sees the data.
3. The default `ThreadPoolDispatcher` runs jobs on a small in-process thread pool. ReportLab rendering is fast (about 5 ms per certificate; 1,000 certificates in about 5 s locally), so threads are enough and no extra infrastructure is needed.
4. The client polls `GET /jobs/{id}` for progress.

**Why not Celery/RQ + Redis?** They would add a broker and a separate worker process for an assignment-sized workload. The API only depends on the `Dispatcher` protocol (`submit(job_id)`), so moving to Celery later means writing a ~10-line `CeleryDispatcher` whose task calls `JobProcessor.process(job_id)`. The endpoints, services, and processor stay the same.

### Validation: two levels

- **Request level → `422`, nothing is created:** missing or blank certificate fields, `recipients` not a non-empty list, more than `MAX_RECIPIENTS_PER_JOB` recipients, or *no* valid recipient at all.
- **Recipient level → the job still runs:** each recipient is validated independently (`name` 1–100 chars, valid `email`, must be an object, no duplicate email within a job, case-insensitive). Invalid rows are stored with status `invalid` and their error messages, and are returned in `invalid_recipients` with their **index** in the submitted list. Rejecting a 1,000-person batch over one typo would be unhelpful, so the rest are still generated.

That's why `recipients` is typed `list[Any]` in the request schema: a strict list type would make Pydantic reject the whole request because of one bad item.

### Failure isolation

- Each certificate is generated in **its own try/except and its own DB transaction**. An exception marks only that certificate `failed` (with `"ExceptionType: message"`), and the loop continues.
- Committing after each certificate also makes progress **visible in real time** to status requests.
- PDFs are written to a temp file and atomically renamed (`os.replace`), so a crash never leaves a half-written PDF at the final path.
- `failed` (retryable, e.g. disk error) is kept separate from `invalid` (bad input, retrying won't help). `POST /jobs/{id}/retry` resets only `failed` certificates to `pending` and re-dispatches the job.

### Progress is derived, not counted

Progress is computed with `GROUP BY status` over the certificate rows (indexed on `(job_id, status)`) instead of maintaining counters on the job row. Counters could drift from reality (crash between two updates, concurrent writers). A derived count can't.

### Restart safety

The processor only ever picks up `pending` certificates, so processing is **resumable**. On startup, jobs left `pending` or `processing` (server crash, deploy) are re-dispatched and continue where they stopped. Already generated certificates are not redone. On graceful shutdown, workers stop after their current certificate.

### Storage and retrieval

- PDFs are stored on disk at `storage/certificates/<job_id>/<certificate_id>.pdf`. The DB stores the *relative* path. Production would use object storage such as S3, but the path abstraction stays the same.
- Paths are resolved and checked to stay inside the storage directory before being served.
- The ZIP is built in a temp file (not in memory) and deleted after the response is sent.
- Certificate IDs are UUIDs (not guessable), and each ID is printed on the certificate so it can be used for verification.

### Database

SQLAlchemy 2.0 with SQLite by default, so setup needs nothing extra. WAL mode is enabled so API reads aren't blocked by the worker's writes. Switching to PostgreSQL only requires changing `CERTGEN_DATABASE_URL`. Status enums are stored as strings (not native DB enums), so adding a status doesn't need a migration.

### Testability

`create_app(settings, renderer)` is an app factory. Tests inject a temp database, temp storage, `inline` processing, and **fake renderers** that fail for chosen recipients. That makes the failure scenarios reproducible without monkey-patching.

## 7. Known limitations / possible next steps

- **Single process:** with several uvicorn workers, each would run startup recovery and could process the same job twice. For horizontal scaling, move to Celery/RQ or claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL.
- **Fonts:** built-in PDF fonts cover Latin script only. Names in Devanagari, Tamil, etc. would need a TTF (e.g. Noto Sans) registered with ReportLab.
- No authentication, rate limiting, or idempotency keys (a client retrying a POST creates a second job).
- Schema is created with `create_all`. A real deployment would use Alembic migrations.
- Possible extras: emailing certificates to recipients, CSV upload, webhooks on job completion, a public verification endpoint by certificate ID.
