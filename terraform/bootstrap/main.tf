# One-time setup for the Terraform workflow, applied by an administrator
# (README.md, "Terraform in CI"). It links no billing account.

terraform {
  required_version = ">= 1.11"

  # State in HCP Terraform; plan and apply run on the admin's machine with
  # the admin's accounts (local execution mode, README.md).
  cloud {
    organization = "KN-Silver"

    workspaces {
      name = "bootstrap"
    }
  }

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
    tfe = {
      source  = "hashicorp/tfe"
      version = "~> 0.81"
    }
  }
}

# Keyed by app name, which the projects also get (project IDs cannot change).
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

# With an organization, terraform/apps can create projects in an `apps` folder:
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

data "github_repository" "infra" {
  name = "Infra"
}

locals {
  repository_id = tostring(data.github_repository.infra.repo_id)
  environment   = "terraform"

  # "<app> <role>" => { app, role }: the Terraform account's roles per app project.
  app_roles = {
    for pair in setproduct(keys(var.apps), [
      "roles/browser",
      "roles/firebase.admin",
      "roles/iam.serviceAccountAdmin",
      "roles/iam.workloadIdentityPoolAdmin",
      "roles/serviceusage.serviceUsageAdmin",
    ]) : "${pair[0]} ${pair[1]}" => { app = pair[0], role = pair[1] }
  }
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

# This configuration's own state. `terraform init` creates the workspace; it
# must be set to local before the first plan (README.md).
resource "tfe_workspace" "bootstrap" {
  organization = tfe_organization.silver.name
  name         = "bootstrap"
  description  = "terraform/bootstrap of SilverNETGroupSGGW/Infra"

  lifecycle {
    prevent_destroy = true
  }
}

resource "tfe_workspace_settings" "bootstrap" {
  workspace_id         = tfe_workspace.bootstrap.id
  execution_mode       = "local"
  global_remote_state  = false
  project_remote_state = false
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

  project_id = each.value.project_id
  name       = each.value.name

  lifecycle {
    ignore_changes = [billing_account, org_id, folder_id, labels]
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
  # Only terraform.yml from main: its plan job, and its apply job once
  # approved in the `terraform` environment.
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${local.repository_id}'",
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

# Lets terraform/apps manage release sign-in and Firebase in each app project;
# with an organization: on the apps folder, plus resourcemanager.projectCreator.
resource "google_project_iam_member" "terraform" {
  for_each = local.app_roles

  project = google_project.apps[each.value.app].project_id
  role    = each.value.role
  member  = google_service_account.terraform.member
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

# The workflow's secrets for the plan job: no reviewers, main only.
resource "github_repository_environment" "terraform_plan" {
  repository  = data.github_repository.infra.name
  environment = "terraform-plan"

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "terraform_plan_main" {
  repository     = data.github_repository.infra.name
  environment    = github_repository_environment.terraform_plan.environment
  branch_pattern = "main"
}

# Repository variables, so the plan job reads them without an approval.
resource "github_actions_variable" "terraform" {
  for_each = {
    TF_GOOGLE_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.terraform.name
    TF_GOOGLE_SERVICE_ACCOUNT            = google_service_account.terraform.email
    TF_CLOUD_ORGANIZATION                = tfe_organization.silver.name
    # TF_APPS_FOLDER_ID                  = google_folder.apps.folder_id
    TF_GITHUB_APP_ID              = var.github_app_id
    TF_GITHUB_APP_INSTALLATION_ID = var.github_app_installation_id
  }

  repository    = data.github_repository.infra.name
  variable_name = each.key
  value         = each.value
}

# Created by runs whose local state was lost (2026-10); adopted into the HCP
# state. Later additions have no import here and show as creates.
import {
  to = google_project.infra
  id = var.infra_project_id
}

import {
  to = google_service_account.terraform
  id = "projects/${var.infra_project_id}/serviceAccounts/terraform@${var.infra_project_id}.iam.gserviceaccount.com"
}

import {
  to = github_repository_environment.terraform
  id = "Infra:terraform"
}

import {
  to = github_repository_environment_deployment_policy.terraform_main
  id = "Infra:terraform:62064227"
}

import {
  to = github_repository_environment.terraform_plan
  id = "Infra:terraform-plan"
}

import {
  to = github_repository_environment_deployment_policy.terraform_plan_main
  id = "Infra:terraform-plan:65637395"
}

import {
  to = tfe_workspace.apps
  id = "${var.hcp_organization}/apps"
}

import {
  to = tfe_workspace_settings.apps
  id = "${var.hcp_organization}/apps"
}

import {
  to = tfe_workspace.bootstrap
  id = "${var.hcp_organization}/bootstrap"
}

import {
  to = tfe_workspace_settings.bootstrap
  id = "${var.hcp_organization}/bootstrap"
}

import {
  for_each = toset([
    "androidpublisher.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "sts.googleapis.com",
  ])
  to = google_project_service.infra[each.key]
  id = "${var.infra_project_id}/${each.key}"
}

import {
  to = google_iam_workload_identity_pool.github
  id = "projects/${var.infra_project_id}/locations/global/workloadIdentityPools/github"
}

import {
  to = google_iam_workload_identity_pool_provider.terraform
  id = "projects/${var.infra_project_id}/locations/global/workloadIdentityPools/github/providers/infra-terraform"
}

# 389882651708 is the number of silvernet-infra, which the pool's name uses.
import {
  to = google_service_account_iam_member.terraform_github
  id = "projects/${var.infra_project_id}/serviceAccounts/terraform@${var.infra_project_id}.iam.gserviceaccount.com roles/iam.workloadIdentityUser principalSet://iam.googleapis.com/projects/389882651708/locations/global/workloadIdentityPools/github/attribute.repository_id/${local.repository_id}"
}

# Only the apps of 2026-10; an app added later gets its bindings created.
import {
  for_each = { for k, v in local.app_roles : k => v if contains(["plan_wzim", "dni_sggw"], v.app) }
  to       = google_project_iam_member.terraform[each.key]
  id       = "${var.apps[each.value.app].project_id} ${each.value.role} serviceAccount:terraform@${var.infra_project_id}.iam.gserviceaccount.com"
}

import {
  for_each = toset([
    "TF_CLOUD_ORGANIZATION",
    "TF_GITHUB_APP_ID",
    "TF_GITHUB_APP_INSTALLATION_ID",
    "TF_GOOGLE_SERVICE_ACCOUNT",
    "TF_GOOGLE_WORKLOAD_IDENTITY_PROVIDER",
  ])
  to = github_actions_variable.terraform[each.key]
  id = "Infra:${each.key}"
}
