# One-time setup that lets the Terraform workflow manage the apps
# (terraform/apps): the HCP Terraform workspace that keeps their state, the
# infra project with the Terraform service account and its keyless sign-in from
# this repository's `terraform` environment, its rights on the apps' projects
# and in Play Console, and the `terraform` environment. An administrator of
# HCP Terraform, the Google Cloud projects, Play Console and this repository
# applies it once from their machine; see README.md, "Terraform in CI".
#
# Nothing here links a Google Cloud billing account, so nothing can be billed.

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
    tfe = {
      source  = "hashicorp/tfe"
      version = "~> 0.81"
    }
  }
}

# Each app's Firebase project is its Google Cloud project; the release sign-in
# lives there next to the app's other parts. Project IDs cannot change, so the
# apps are keyed by name and the projects get the apps' names as display names.
variable "apps" {
  description = "The apps of terraform/apps: their Google Cloud project IDs and display names."
  type = map(object({
    project_id = string
    name       = string
  }))
  default = {
    plan_wzim = { project_id = "silvertimetable-bea41", name = "Plan WZIM" }
    dni_sggw  = { project_id = "sggw-days", name = "Dni SGGW" }
    # Once its owner gives the club access to the project:
    # kampus_sggw = { project_id = "kampus-sggw-2021", name = "Kampus SGGW" }
  }
}

# With a Google Cloud organization (silver.sggw.pl, later), new apps' projects
# can be created by terraform/apps in an `apps` folder:
#
# variable "org_id" {
#   description = "Numeric ID of the Google Cloud organization (gcloud organizations list)."
#   type        = string
# }

variable "infra_project_id" {
  description = "ID of the project that holds the Terraform service account."
  type        = string
  default     = "silvernet-infra"
}

variable "hcp_organization" {
  description = "HCP Terraform organization that keeps the state."
  type        = string
  default     = "KN-Silver"
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

provider "tfe" {}

data "github_repository" "infra" {
  name = "Infra"
}

locals {
  repository_id = tostring(data.github_repository.infra.repo_id)
  environment   = "terraform"
}

# The club's existing organization, adopted; its settings stay as they are.
import {
  to = tfe_organization.silver
  id = var.hcp_organization
}

resource "tfe_organization" "silver" {
  name  = var.hcp_organization
  email = "silvernetsggw@gmail.com"

  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}

# The state of terraform/apps. Runs execute in the Terraform workflow, which
# signs in to Google Cloud itself; HCP Terraform only keeps the state.
resource "tfe_workspace" "apps" {
  organization = tfe_organization.silver.name
  name         = "apps"
  description  = "terraform/apps of SilverNETGroupSGGW/Infra"
}

resource "tfe_workspace_settings" "apps" {
  workspace_id   = tfe_workspace.apps.id
  execution_mode = "local"
}

# resource "google_folder" "apps" {
#   display_name = "apps"
#   parent       = "organizations/${var.org_id}"
# }

resource "google_project" "infra" {
  project_id = var.infra_project_id
  name       = "SilverNET infra"
  # org_id   = var.org_id
}

# The apps' existing projects, adopted for their display names. Terraform never
# changes their billing or parent, and refuses to delete them.
import {
  for_each = var.apps
  to       = google_project.apps[each.key]
  id       = each.value.project_id
}

resource "google_project" "apps" {
  for_each = var.apps

  project_id          = each.value.project_id
  name                = each.value.name
  deletion_policy     = "PREVENT"
  auto_create_network = true

  lifecycle {
    ignore_changes = [billing_account, org_id, folder_id, labels, auto_create_network]
  }
}

resource "google_project_service" "infra" {
  for_each = toset([
    "androidpublisher.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "sts.googleapis.com",
  ])

  project = google_project.infra.project_id
  service = each.value
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

# What terraform/apps manages in each app's project: the release sign-in
# (terraform/app-release) and the app's Firebase resources. With the
# organization, the same roles plus roles/resourcemanager.projectCreator go on
# the apps folder for new apps.
resource "google_project_iam_member" "terraform" {
  for_each = {
    for pair in setproduct(keys(var.apps), [
      "roles/browser",
      "roles/firebase.admin",
      "roles/iam.serviceAccountAdmin",
      "roles/iam.workloadIdentityPoolAdmin",
      "roles/serviceusage.serviceUsageAdmin",
    ]) : "${pair[0]} ${pair[1]}" => { app = pair[0], role = pair[1] }
  }

  project = google_project.apps[each.value.app].project_id
  role    = each.value.role
  member  = google_service_account.terraform.member
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
    TF_CLOUD_ORGANIZATION                = tfe_organization.silver.name
    # TF_APPS_FOLDER_ID                  = google_folder.apps.folder_id
    TF_GITHUB_APP_ID              = var.github_app_id
    TF_GITHUB_APP_INSTALLATION_ID = var.github_app_installation_id
  }

  repository    = data.github_repository.infra.name
  environment   = github_repository_environment.terraform.environment
  variable_name = each.key
  value         = each.value
}

# The first run keyed the apps by project ID.

moved {
  from = google_project.apps["silvertimetable-bea41"]
  to   = google_project.apps["plan_wzim"]
}

moved {
  from = google_project_iam_member.terraform["silvertimetable-bea41 roles/browser"]
  to   = google_project_iam_member.terraform["plan_wzim roles/browser"]
}

moved {
  from = google_project_iam_member.terraform["silvertimetable-bea41 roles/iam.serviceAccountAdmin"]
  to   = google_project_iam_member.terraform["plan_wzim roles/iam.serviceAccountAdmin"]
}

moved {
  from = google_project_iam_member.terraform["silvertimetable-bea41 roles/iam.workloadIdentityPoolAdmin"]
  to   = google_project_iam_member.terraform["plan_wzim roles/iam.workloadIdentityPoolAdmin"]
}

moved {
  from = google_project_iam_member.terraform["silvertimetable-bea41 roles/serviceusage.serviceUsageAdmin"]
  to   = google_project_iam_member.terraform["plan_wzim roles/serviceusage.serviceUsageAdmin"]
}

moved {
  from = google_project.apps["sggw-days"]
  to   = google_project.apps["dni_sggw"]
}

moved {
  from = google_project_iam_member.terraform["sggw-days roles/browser"]
  to   = google_project_iam_member.terraform["dni_sggw roles/browser"]
}

moved {
  from = google_project_iam_member.terraform["sggw-days roles/iam.serviceAccountAdmin"]
  to   = google_project_iam_member.terraform["dni_sggw roles/iam.serviceAccountAdmin"]
}

moved {
  from = google_project_iam_member.terraform["sggw-days roles/iam.workloadIdentityPoolAdmin"]
  to   = google_project_iam_member.terraform["dni_sggw roles/iam.workloadIdentityPoolAdmin"]
}

moved {
  from = google_project_iam_member.terraform["sggw-days roles/serviceusage.serviceUsageAdmin"]
  to   = google_project_iam_member.terraform["dni_sggw roles/serviceusage.serviceUsageAdmin"]
}
