# GCP-cloud

## Weekly Europe ISPE / Pharma & Life Sciences Digest

Automated weekly pipeline that generates an HTML + JSON digest of:

1. Events from ISPE's European regional chapters (DACH, France, UK & Ireland, Nordic, Benelux, Italy, Spain, Poland, Ireland).
2. Notable pharma / life sciences industry news relevant to Europe from the past week.

It runs as a Cloud Run Job, triggered weekly by Cloud Scheduler, and writes the report to a GCS
bucket (`reports/<date>.html`, `reports/<date>.json`, plus `reports/latest.*`). Data collection
uses Claude's server-side `web_search` tool with a structured-output schema — see
`pharma_report/claude_client.py` and `pharma_report/schema.py`.

### Local run

```bash
cd GCP-cloud
pip install -r pharma_report/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m pharma_report.main --dry-run   # writes to ./out/ instead of GCS
```

### Tests

```bash
pip install pytest
pytest tests/
```

### Deploy

See `deploy/terraform/`. Build and push the container image, then:

```bash
cd deploy/terraform
terraform init
terraform apply -var="project_id=YOUR_PROJECT" -var="image=REGION-docker.pkg.dev/YOUR_PROJECT/pharma-report/pharma-report:latest"
# then populate the secret once:
printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-
```