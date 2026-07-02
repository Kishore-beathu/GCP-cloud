variable "project_id" {
  description = "GCP project ID to deploy into."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Artifact Registry, and Scheduler."
  type        = string
  default     = "europe-west4"
}

variable "bucket_location" {
  description = "GCS bucket location (can be multi-region, e.g. EU)."
  type        = string
  default     = "EU"
}

variable "job_name" {
  description = "Name for the Cloud Run Job."
  type        = string
  default     = "pharma-weekly-report"
}

variable "image" {
  description = "Full container image reference to deploy (e.g. europe-west4-docker.pkg.dev/PROJECT/pharma-report/pharma-report:latest). Push the image before applying."
  type        = string
}

variable "claude_model" {
  description = "Claude model ID used for the digest generation."
  type        = string
  default     = "claude-opus-4-8"
}

variable "schedule_cron" {
  description = "Cron expression for the weekly trigger (default: Monday 07:00)."
  type        = string
  default     = "0 7 * * 1"
}

variable "schedule_timezone" {
  description = "IANA timezone for the schedule."
  type        = string
  default     = "Europe/Amsterdam"
}
