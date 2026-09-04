# Deploying to Google Cloud

The backend runs on **Cloud Run** (container, scales to zero) against
**Cloud SQL for PostgreSQL** or the existing Supabase database. The dashboard
is static files, so it goes to any static host.

Everything below assumes `gcloud` is installed and authenticated:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
export PROJECT_ID=$(gcloud config get-value project)
export REGION=europe-west4      # pick one near you
```

## 1. Enable the APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com
```

## 2. Create the Artifact Registry repository

```bash
gcloud artifacts repositories create trading-agent \
  --repository-format=docker \
  --location=$REGION \
  --description="Trading intelligence containers"
```

## 3. Database

**Option A — keep Supabase.** Nothing to create. Use the session-pooler
connection string you already have. Simplest, and the app is already configured
for it.

**Option B — Cloud SQL.** Keeps data inside your GCP project:

```bash
gcloud sql instances create trading-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=$REGION

gcloud sql databases create pharma --instance=trading-db
gcloud sql users set-password postgres --instance=trading-db --password=CHOOSE_A_STRONG_ONE
```

Cloud Run reaches Cloud SQL over a Unix socket, so the URL uses the socket path
rather than a host:

```
postgresql+asyncpg://postgres:PASSWORD@/pharma?host=/cloudsql/PROJECT_ID:REGION:trading-db
```

## 4. Store secrets

Never put credentials in `cloudbuild.yaml` or the image — they belong in
Secret Manager:

```bash
printf '%s' 'postgresql+asyncpg://...' | \
  gcloud secrets create database-url --data-file=-

# Required in production — the service refuses to start without both.
printf '%s' 'CHOOSE-A-STRONG-PASSWORD' | gcloud secrets create auth-password --data-file=-
python -c "from app.security import generate_secret_key; print(generate_secret_key())" | \
  gcloud secrets create secret-key --data-file=-

printf '%s' 'YOUR_FINNHUB_KEY' | gcloud secrets create finnhub-api-key --data-file=-
printf '%s' 'YOUR_ALPHA_VANTAGE_KEY' | gcloud secrets create alpha-vantage-api-key --data-file=-
printf '%s' 'https://hooks.slack.com/services/...' | gcloud secrets create slack-webhook-url --data-file=-
```

Grant the Cloud Run service account read access:

```bash
export SA="$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for secret in database-url auth-password secret-key finnhub-api-key alpha-vantage-api-key slack-webhook-url; do
  gcloud secrets add-iam-policy-binding $secret \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
done
```

## 5. First deploy

Build and push the image, then create the service with its configuration:

```bash
gcloud builds submit --config backend/cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_SERVICE=trading-api

gcloud run services update trading-api \
  --region=$REGION \
  --set-secrets=DATABASE_URL=database-url:latest,AUTH_PASSWORD=auth-password:latest,SECRET_KEY=secret-key:latest,FINNHUB_API_KEY=finnhub-api-key:latest,ALPHA_VANTAGE_API_KEY=alpha-vantage-api-key:latest,SLACK_WEBHOOK_URL=slack-webhook-url:latest \
  --set-env-vars=ENVIRONMENT=production,CREATE_TABLES_ON_STARTUP=false,SEC_USER_AGENT="Your Company you@example.com",CORS_ORIGINS=https://your-dashboard.example.com \
  --min-instances=1 \
  --max-instances=1 \
  --cpu=1 --memory=1Gi \
  --timeout=3600
```

Add `--add-cloudsql-instances=$PROJECT_ID:$REGION:trading-db` if you chose
Cloud SQL.

Subsequent deploys are just the `builds submit` line — the service keeps its
secrets and env vars.

### Why `min-instances=1` and `max-instances=1`

Two constraints drive this, and both matter:

- **The scheduler must be a singleton.** Every instance runs APScheduler, so
  two instances mean two SEC pulls and double the API-quota burn.
- **Scale-to-zero stops the scheduler.** Cloud Run freezes idle instances, so
  with `min-instances=0` background ingestion only runs while HTTP traffic
  happens to be arriving.

`min=max=1` keeps exactly one always-warm instance — roughly $10–15/month, well
inside the project budget. WebSocket connections also stay put, which matters
because the price hub keeps subscriber state in memory.

**To scale the API horizontally later:** set `SCHEDULER_ENABLED=false` on the
public service, raise `--max-instances`, and run a second single-instance
service (same image, `SCHEDULER_ENABLED=true`) that does ingestion only. The
WebSocket hub would then need Redis pub/sub to fan out across instances.

### Authentication is mandatory in production

`ENVIRONMENT=production` without `AUTH_PASSWORD` and `SECRET_KEY` raises at
startup and the revision never goes live. That is deliberate: a Cloud Run URL
is effectively public, and an open `/admin/ingest/*` lets anyone who finds it
burn your market-data quota — or get your IP blocked by the SEC. If a deploy
fails with *"Refusing to start in production with an insecure configuration"*,
that check is doing its job; add the secrets rather than lowering
`ENVIRONMENT`.

## 6. Apply database migrations

The image ships with Alembic, and production runs with
`CREATE_TABLES_ON_STARTUP=false`, so schema changes are explicit:

```bash
# One-off, from your machine against the production database:
cd backend
DATABASE_URL='postgresql+asyncpg://...' alembic upgrade head
```

**First time on a database that already has tables** (a Supabase project
created by the old `create_all` path) — record the baseline instead of trying
to re-create it:

```bash
DATABASE_URL='postgresql+asyncpg://...' alembic stamp head
```

After that, every model change needs a revision (`alembic revision
--autogenerate -m "..."`), and CI fails the build if one is missing.

## 7. Verify

```bash
export URL=$(gcloud run services describe trading-api --region=$REGION --format='value(status.url)')
curl $URL/health
curl "$URL/jobs/status"
```

`/health` should report `"database": "ok"`, and `/jobs/status` should list the
scheduled jobs for whichever API keys you configured.

## 8. Deploy the dashboard

Build it against the deployed API and upload the static files:

```bash
cd frontend
VITE_API_URL=$URL npm run build

gcloud storage buckets create gs://your-dashboard-bucket --location=$REGION
gcloud storage rsync dist gs://your-dashboard-bucket --recursive --delete-unmatched-destination-objects
gcloud storage buckets update gs://your-dashboard-bucket --web-main-page-suffix=index.html
```

Then set `CORS_ORIGINS` on the Cloud Run service to the dashboard's origin
(step 5) so the browser is allowed to call the API.

Any static host works equally well — Vercel, Netlify, or Firebase Hosting —
since the build is plain files.

## Cost sketch

| Item | Monthly |
|------|---------|
| Cloud Run, 1 always-on instance (1 vCPU, 1 GiB) | ~$10–15 |
| Cloud SQL `db-f1-micro` (skip if staying on Supabase) | ~$8–10 |
| Artifact Registry, Secret Manager, Cloud Build free tiers | ~$0–2 |
| Market data APIs (Finnhub + Alpha Vantage paid tiers) | $0 on free tiers |

Comfortably inside the $1000–3000/month budget, leaving room for paid data
feeds — which is where the money is better spent.

## Operational notes

- **Logs:** `gcloud run services logs read trading-api --region=$REGION --limit=100`.
- **Rotating a secret:** add a new version (`gcloud secrets versions add ...`);
  services pinned to `:latest` pick it up on the next deploy.
- **Schema changes:** the app calls `create_all` at startup, which adds missing
  tables but never alters existing ones. Introduce Alembic before the first
  destructive change.
- **SEC compliance:** set a real contact address in `SEC_USER_AGENT`. The SEC
  blocks anonymous traffic, and the block is by IP.
