# Build from the repo root:
#   docker build -f pharma_report/Dockerfile -t pharma-report .
FROM python:3.12-slim

WORKDIR /app

COPY pharma_report/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY pharma_report/ ./pharma_report/

ENTRYPOINT ["python", "-m", "pharma_report.main"]
