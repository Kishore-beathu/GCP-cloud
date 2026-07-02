terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = [
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Storage -----------------------------------------------------------

resource "google_storage_bucket" "reports" {
  name                        = "${var.project_id}-pharma-reports"
  location                    = var.bucket_location
  project                     = var.project_id
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.apis]
}

# --- Artifact Registry ---------------------------------------------------

resource "google_artifact_registry_repository" "repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "pharma-report"
  format        = "DOCKER"
  description   = "Container images for the weekly ISPE/pharma report job."

  depends_on = [google_project_service.apis]
}

# --- Secret Manager (secret created here; add the value out-of-band) -----

resource "google_secret_manager_secret" "anthropic_api_key" {
  project   = var.project_id
  secret_id = "anthropic-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}
# After apply, populate it once:
#   printf '%s' "$ANTHROPIC_API_KEY" | gcloud secrets versions add anthropic-api-key --data-file=-

# --- Service accounts -----------------------------------------------------

resource "google_service_account" "job_sa" {
  project      = var.project_id
  account_id   = "pharma-report-job"
  display_name = "Weekly pharma report Cloud Run Job"
}

resource "google_service_account" "scheduler_sa" {
  project      = var.project_id
  account_id   = "pharma-report-scheduler"
  display_name = "Cloud Scheduler invoker for the pharma report job"
}

resource "google_storage_bucket_iam_member" "job_sa_bucket_writer" {
  bucket = google_storage_bucket.reports.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "job_sa_secret_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.anthropic_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.job_sa.email}"
}

# --- Cloud Run Job ---------------------------------------------------------

resource "google_cloud_run_v2_job" "report_job" {
  name     = var.job_name
  project  = var.project_id
  location = var.region

  template {
    template {
      service_account = google_service_account.job_sa.email
      max_retries      = 1
      timeout          = "900s"

      containers {
        image = var.image

        env {
          name  = "CLAUDE_MODEL"
          value = var.claude_model
        }
        env {
          name  = "REPORT_BUCKET"
          value = google_storage_bucket.reports.name
        }
        env {
          name = "ANTHROPIC_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.anthropic_api_key.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_can_invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.report_job.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_project_iam_member" "scheduler_can_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

# --- Cloud Scheduler ---------------------------------------------------------

resource "google_cloud_scheduler_job" "weekly_trigger" {
  name      = "${var.job_name}-weekly"
  project   = var.project_id
  region    = var.region
  schedule  = var.schedule_cron
  time_zone = var.schedule_timezone

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${var.job_name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler_sa.email
    }
  }

  depends_on = [google_project_service.apis, google_cloud_run_v2_job.report_job]
}
