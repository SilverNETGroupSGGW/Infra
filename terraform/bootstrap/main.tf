# One-time setup that lets the Terraform workflow manage the apps
# (terraform/apps): the infra project with the state bucket, the Terraform
# service account and its keyless sign-in from this repository's `terraform`
# environment, its rights on the `releases` folder and in Play Console, and the
# `terraform` environment. An administrator of the Google Cloud organization,
# Play Console and this repository applies it once from their machine; see
# README.md, "Terraform in CI".

terraform {
  required_version = ">= 1.11"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
    googleplay = {
      source  = "oliver-binns/googleplay"
      version = "~> 0.6.3"
    }
  }
}

variable "org_id" {
  description = "Numeric ID of the Google Cloud organization (gcloud organizations list)."
  type        = string
}

variable "billing_account" {
  description = "Billing account of the infra project, which holds the state bucket (gcloud billing accounts list)."
  type        = string
}

variable "infra_project_id" {
  description = "ID of the project that holds the state bucket and the Terraform service account."
  type        = string
  default     = "silvernet-infra"
}

variable "github_app_id" {
  description = "App ID of the organization's Terraform GitHub App."
  type        = string
}

variable "github_app_installation_id" {
  description = "ID of that app's installation on the organization."
  type        = string
}

variable "reviewer_user_ids" {
  description = "Numeric GitHub user IDs that approve Terraform runs."
  type        = list(number)
  default     = [33990351] # ThePhaseless
}

provider "github" {
  owner = "SilverNETGroupSGGW"
}

provider "google" {}

provider "googleplay" {
  developer_id = "8827645756827128332" # KN Silver .NET
}

data "github_repository" "infra" {
  name = "Infra"
}

locals {
  repository_id = tostring(data.github_repository.infra.repo_id)
  environment   = "terraform"
}

resource "google_folder" "releases" {
  display_name = "releases"
  parent       = "organizations/${var.org_id}"
}

resource "google_project" "infra" {
  project_id      = var.infra_project_id
  name            = "SilverNET infra"
  org_id          = var.org_id
  billing_account = var.billing_account
}

resource "google_project_service" "infra" {
  for_each = toset([
    "androidpublisher.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ])

  project = google_project.infra.project_id
  service = each.value
}

# Holds IDs and names only; secrets stay out of Terraform. us-central1 is in
# Cloud Storage's always-free tier.
resource "google_storage_bucket" "state" {
  project                     = google_project.infra.project_id
  name                        = "${var.infra_project_id}-terraform-state"
  location                    = "US-CENTRAL1"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.infra]
}

resource "google_service_account" "terraform" {
  project      = google_project.infra.project_id
  account_id   = "terraform"
  display_name = "Terraform workflow of SilverNETGroupSGGW/Infra"

  depends_on = [google_project_service.infra]
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = google_project.infra.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.infra]
}

resource "google_iam_workload_identity_pool_provider" "terraform" {
  project                            = google_project.infra.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "infra-terraform"
  display_name                       = "Infra terraform.yml"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }
  # Only terraform.yml from main, in the approved `terraform` environment.
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${local.repository_id}'",
    "assertion.environment == '${local.environment}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.workflow_ref == 'SilverNETGroupSGGW/Infra/.github/workflows/terraform.yml@refs/heads/main'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "terraform_github" {
  service_account_id = google_service_account.terraform.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${local.repository_id}"
}

resource "google_storage_bucket_iam_member" "terraform_state" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.terraform.member
}

# What terraform/app-release creates in each app's project.
resource "google_folder_iam_member" "terraform" {
  for_each = toset([
    "roles/browser",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/resourcemanager.projectCreator",
    "roles/serviceusage.serviceUsageAdmin",
  ])

  folder = google_folder.releases.name
  role   = each.value
  member = google_service_account.terraform.member
}

# Inviting the apps' service accounts needs the Play Console admin permission.
resource "googleplay_user" "terraform" {
  email              = google_service_account.terraform.email
  global_permissions = ["CAN_MANAGE_PERMISSIONS_GLOBAL"]
}

resource "github_repository_environment" "terraform" {
  repository  = data.github_repository.infra.name
  environment = local.environment

  reviewers {
    users = var.reviewer_user_ids
  }

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "terraform_main" {
  repository     = data.github_repository.infra.name
  environment    = github_repository_environment.terraform.environment
  branch_pattern = "main"
}

resource "github_actions_environment_variable" "terraform" {
  for_each = {
    TF_GOOGLE_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.terraform.name
    TF_GOOGLE_SERVICE_ACCOUNT            = google_service_account.terraform.email
    TF_STATE_BUCKET                      = google_storage_bucket.state.name
    TF_RELEASES_FOLDER_ID                = google_folder.releases.folder_id
    TF_GITHUB_APP_ID                     = var.github_app_id
    TF_GITHUB_APP_INSTALLATION_ID        = var.github_app_installation_id
  }

  repository    = data.github_repository.infra.name
  environment   = github_repository_environment.terraform.environment
  variable_name = each.key
  value         = each.value
}
