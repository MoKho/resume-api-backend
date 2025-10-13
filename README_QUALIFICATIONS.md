# Resume Check — Qualifications Flow

This document explains the new qualifications-driven resume check flow and includes example requests you can use from the frontend.

Summary
- The `/profiles/check-resume` endpoint now accepts an optional `qualifications` JSON array. If provided, the backend will use that list (and skip extraction) to evaluate the resume.
- If `qualifications` is not provided, the backend will either:
  - Summarize the provided `job_post` (if `summarize_job_post=true`) and then extract qualifications from the summarized text, or
  - Extract qualifications directly from the `job_post` (if `summarize_job_post=false`).
- Extracted qualifications are saved to the `resume_checks.qualifications` JSONB column.
- The worker (`app/workers/resume_check_worker.py`) continues to poll for pending jobs and calls `run_resume_check_process()` to perform the analysis.

API contract (new/changed fields)
- POST /profiles/check-resume
  - Request body (`ResumeCheckRequest`):
    - `job_post` (string) — required
    - `resume_text` (string | optional) — if omitted, server will use user's base resume
    - `summarize_job_post` (boolean | optional, default true)
    - `qualifications` (optional): array of objects: `{ "qualification": string, "weight": int }`
  - Response: `{ job_id, status_url, status }` (202 Accepted)

- GET /profiles/check-resume/{job_id}
  - Response includes: `job_id`, `status`, `analysis`, `error`, `qualifications`, `updated_at`

Examples

1) Frontend provides qualifications directly (skip summarization/extraction)

Request body example (JSON):

{
  "job_post": "Senior Backend Engineer — Work on APIs, distributed systems, Python/Go...",
  "resume_text": "... candidate resume text ...",
  "summarize_job_post": false,
  "qualifications": [
    { "qualification": "Python", "weight": 9 },
    { "qualification": "Distributed Systems", "weight": 8 },
    { "qualification": "Golang", "weight": 6 }
  ]
}

cURL example:

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -d '@payload.json' \
  https://your-api.example.com/profiles/check-resume
```

Notes:
- Save the returned `job_id` and poll `GET /profiles/check-resume/{job_id}` for status and `analysis`.

2) Let backend summarize and extract qualifications

Request body example:

{
  "job_post": "Full job posting text...",
  "resume_text": "... candidate resume text ...",
  "summarize_job_post": true
}

Behavior:
- Backend will summarize the job posting, extract a ranked list of qualifications (integers 1-10), persist them to `resume_checks.qualifications`, and then run the resume check using that qualifications list.

3) Let backend extract qualifications directly from job_post (skip summarization)

Request body example:

{
  "job_post": "Full job posting text...",
  "resume_text": "... candidate resume text ...",
  "summarize_job_post": false
}

Behavior:
- Backend will call the qualifications extractor directly on `job_post`, persist the extracted `qualifications`, and run the resume check using that list.

How to verify
1. POST to `/profiles/check-resume` with a payload as above.
2. Check the returned `job_id` and call `GET /profiles/check-resume/{job_id}`.
3. Verify `qualifications` is present in the status response (either your pre-supplied list or the extracted list).
4. When the worker completes the job, `analysis` will be filled and `status` will be `completed`.

Notes and troubleshooting
- Qualifications weights are integers (1-10). The extractor will coerce to integers; malformed items may be dropped.
- The worker is the component that updates `analysis` and `status` on the resume_checks row. If you want synchronous processing, call the worker directly or run the `process_pending_jobs()` script during testing.
- If the DB row cannot be found to persist the qualifications, the process will continue but qualifications won't be saved to the row.

Files touched by this feature
- `app/models/schemas.py` — `ResumeCheckRequest` now accepts `qualifications`.
- `app/system_prompts.py` — new prompt for qualifications extractor.
- `app/services/llm_service.py` — added `extract_job_qualifications()` and qualifications-aware `check_resume()` prompt.
- `app/services/resume_service.py` — extracts/persists qualifications and calls `check_resume()` with the qualifications JSON.
- `app/routers/profiles.py` — persists `qualifications` during enqueue and returns them in status responses.

If you want, I can add example `payload.json` files or a tiny Postman collection for easy testing.
