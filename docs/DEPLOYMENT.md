# Deployment

How the live demo is hosted, and how to reproduce it from scratch.

Three services, each free:

| Piece | Host | Why |
|-------|------|-----|
| React bundle | **Vercel** | Static files on a CDN. Builds straight from the repo. |
| FastAPI backend | **Render** | Runs Python, reads `render.yaml`, injects secrets as env vars. |
| MySQL database | **TiDB Cloud Serverless** | MySQL wire-compatible, 5 GB free, and it does not sleep. |

The application code needed no changes to deploy. Every environment-specific
value — `DATABASE_URL`, `CORS_ORIGINS`, `GROQ_API_KEY`, `VITE_API_BASE_URL` —
was already an environment variable, which is the whole reason this is a
configuration exercise rather than a rewrite.

---

## Before you start

You will need accounts on [TiDB Cloud](https://tidbcloud.com),
[Render](https://render.com) and [Vercel](https://vercel.com) — all three sign
in with GitHub — plus a Groq API key from
[console.groq.com/keys](https://console.groq.com/keys).

There is a **circular dependency** between steps 2 and 3: the backend needs to
know the frontend's origin (for CORS), and the frontend needs to know the
backend's URL. The order below breaks the cycle by deploying the backend first
with a placeholder, then coming back to correct it in step 4. Do not skip step 4
or the browser will block every request.

---

## Step 1 — Database (TiDB Cloud Serverless)

1. Sign in at <https://tidbcloud.com> and create a **Serverless** cluster.
   Pick the region closest to you; the free tier needs no card.
2. Open the cluster's **SQL Editor** and create the schema:

   ```sql
   CREATE DATABASE complaint_qms;
   ```

   You do not need to create any tables. The app calls `Base.metadata.create_all`
   on startup (see `backend/app/database.py`) and builds both of them itself.

3. Click **Connect**, choose connection type **General**, and copy the host,
   port, user and password. Generate a password if you have not already — it is
   shown once.

4. Assemble the SQLAlchemy URL:

   ```
   mysql+pymysql://<USER>:<PASSWORD>@<HOST>:4000/complaint_qms?ssl_ca=/etc/ssl/certs/ca-certificates.crt
   ```

   Two things go wrong here more than anything else:

   - **`ssl_ca` is not optional.** TiDB refuses unencrypted connections, and
     PyMySQL will not negotiate TLS unless you point it at a CA bundle.
     `/etc/ssl/certs/ca-certificates.crt` is the system bundle on Render's
     Debian image, and TiDB's certificate is issued by a public CA, so it
     validates against it. (On Windows that path does not exist — this value is
     for the deployed backend, not your local `.env`.)
   - **Percent-encode the password** if it contains `@`, `:`, `/`, `#` or `?`.
     A raw `@` makes the URL parser treat everything before it as the username
     and you get a confusing authentication error. `@` → `%40`, `#` → `%23`,
     `/` → `%2F`.

---

## Step 2 — Backend (Render)

1. Render dashboard → **New** → **Blueprint** → select this repository.
   Render finds `render.yaml` in the root and reads the whole service
   definition from it: Python runtime, `rootDir: backend`, build command,
   uvicorn start command, health check, region.

2. It will prompt for the three values marked `sync: false` — the ones
   deliberately *not* committed:

   | Variable | Value |
   |----------|-------|
   | `GROQ_API_KEY` | your key from console.groq.com |
   | `DATABASE_URL` | the URL you built in step 1 |
   | `CORS_ORIGINS` | `https://placeholder.vercel.app` — corrected in step 4 |

3. Deploy, and watch the log. A healthy first boot ends with:

   ```
   Starting up - model=llama-3.3-70b-versatile
   Database tables ready
   Uvicorn running on http://0.0.0.0:10000
   ```

   `Database tables ready` is the line that matters: it means SQLAlchemy
   connected to TiDB and `create_all` succeeded. If the log stops before it,
   the problem is `DATABASE_URL`, not the app.

4. Confirm it is really up by visiting `https://<your-service>.onrender.com/health`.
   It should return `{"status":"ok","model":"llama-3.3-70b-versatile"}`.
   Note that it reports the model but never the key — see `backend/app/main.py`.

   Copy the service URL; step 3 needs it.

---

## Step 3 — Frontend (Vercel)

1. Vercel → **Add New** → **Project** → import this repository.
2. Set **Root Directory** to `frontend`. This is the one setting Vercel cannot
   infer — without it the build runs at the repo root, finds no `package.json`,
   and fails. Framework preset (Vite), build command and output directory are
   all detected correctly once the root is right.
3. Add one environment variable:

   ```
   VITE_API_BASE_URL = https://<your-service>.onrender.com
   ```

   No trailing slash — the API client concatenates paths directly onto it.

4. Deploy, and copy the resulting `https://<project>.vercel.app` URL.

> **Vite inlines env vars at build time, not run time.** If you later change
> `VITE_API_BASE_URL`, you must redeploy; editing the variable alone leaves the
> already-built bundle pointing at the old URL. This catches people out
> constantly.

---

## Step 4 — Close the loop (CORS)

Back in Render → your service → **Environment** → edit `CORS_ORIGINS` to the
real Vercel URL:

```
CORS_ORIGINS=https://<project>.vercel.app
```

No trailing slash, and no `*`. Saving triggers a redeploy.

If you also want Vercel's preview deployments to work, add them comma-separated
— but note each preview gets its own generated subdomain, so in practice it is
easier to test against production.

**How to tell this step was missed:** the page loads, but the chat never
responds and the browser console shows *"blocked by CORS policy"*. The backend
is fine; it is refusing to tell the browser that origin is allowed. That is
`app/main.py` doing exactly what it was configured to do.

---

## Step 5 — Verify

1. Open the Vercel URL. The form should appear with the badge on **Pending Triage**.
2. Upload `samples/complaint-01-strength-mixup.eml`. All 11 fields populate and
   the badge flips to **Ready to Commit**.
3. Send `actually the batch number is XLP-8396-0635`. Only that one field
   should highlight green — the rest must not flicker.
4. Click **Commit to QMS Ledger**, then check the row landed:

   ```sql
   SELECT id, product_name, batch_number, severity_suggested FROM complaints;
   ```

If all four pass, the whole path — browser → Vercel → Render → Groq → TiDB —
is working.

---

## What the free tiers actually cost you

Nothing in money, and three things in behaviour. All three are visible to
anyone you send the link to, so they are worth understanding before you post it.

**The backend sleeps.** Render stops a free service after 15 minutes of
inactivity, and the next request has to wait 30–60 seconds for the container to
start. The frontend handles this rather than looking broken: after 4 seconds
`bootSession` dispatches `serverWaking`, which shows a "Waking the demo server"
banner, and one automatic retry covers the case where the proxy rejects the very
first request. See `frontend/src/store/complaintSlice.js`.

If you want to avoid it entirely, an uptime pinger hitting `/health` every 10
minutes keeps the service warm — but Render's free plan allows 750 instance-hours
per month and a month is ~730 hours, so that consumes essentially the entire
allowance on this one service. Fine if it is the only thing you host there.

**The AI quota is shared and daily.** Groq's free tier is roughly 100,000
tokens per day for the whole account, which is about 30–40 complaint runs.
Every visitor draws from the same budget. Two mitigations are in place:

- `RATE_LIMIT_PER_HOUR=10` caps each visitor's IP, so one person cannot
  exhaust the day in a couple of minutes (`backend/app/services/rate_limit.py`).
- When the daily budget genuinely runs out, Groq returns 429 with the limit
  named in the body. `llm.py` reads that and says *"This demo has reached its
  daily AI quota… it resets in a few hours"* rather than the misleading
  *"please wait a moment"* it shows for a per-minute throttle.

**The rate limit is per process, not per cluster.** Counters live in memory.
With one free instance that is exact; if you ever scale to several, each keeps
its own count and the effective limit multiplies. It is a courtesy limit for a
demo, not a security control — the real ceiling on spend is the Groq account
quota itself.

---

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| Build fails compiling `pydantic-core` or `PyMuPDF` from source | `PYTHON_VERSION` drifted to a release with no prebuilt wheels. `render.yaml` pins 3.11.9 — check it was applied. |
| Log stops before `Database tables ready` | `DATABASE_URL`. Usually a missing `?ssl_ca=...` or an unencoded password. |
| `Access denied for user` | Password copied from the wrong place, or a special character not percent-encoded. |
| Page loads, chat does nothing, console says CORS | Step 4 was skipped, or `CORS_ORIGINS` has a trailing slash. |
| Chat says the AI service rejected the API key | `GROQ_API_KEY` is wrong or was revoked. |
| Chat says the model was decommissioned | Groq retired the model. Set `GROQ_MODEL` to a current one from <https://console.groq.com/docs/models>. |
| First load of the day takes ~45s | Expected. Free-tier cold start; the banner explains it to visitors. |

---

## Security notes

The deployment does not weaken anything the local setup enforces:

- **No secret is committed.** `render.yaml` marks every credential `sync: false`,
  which tells Render to prompt for it in the dashboard rather than read a value
  from the repo. The file is safe in a public repository.
- **The Groq key stays server-side.** It is read once in `llm.py` from settings
  and never returned by any endpoint. Nothing prefixed `VITE_` holds a
  credential — that prefix means "compiled into public JavaScript", which is
  exactly why the API key is not there.
- **CORS stays explicit.** A wildcard would let any site on the internet drive
  this API from a visitor's browser. Production names one origin.
- **The database is TLS-only.** Enforced by the provider, not something the app
  can accidentally turn off.
