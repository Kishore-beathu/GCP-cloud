output "reports_bucket" {
  value       = google_storage_bucket.reports.name
  description = "GCS bucket holding the generated reports (reports/latest.html, reports/latest.json)."
}

output "artifact_registry_repo" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
  description = "Push container images here before applying/updating the Cloud Run Job."
}

output "cloud_run_job" {
  value       = google_cloud_run_v2_job.report_job.name
}

output "scheduler_job" {
  value       = google_cloud_scheduler_job.weekly_trigger.name
}
