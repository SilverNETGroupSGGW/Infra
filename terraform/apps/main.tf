# Store release setup of the organization's apps (terraform/app-release),
# applied by the Terraform workflow after a reviewer approves it. A new app is
# a new module block here. Secrets are set with `gh secret set`.

terraform {
  required_version = ">= 1.11"

  # The bucket comes from `-backend-config=bucket=...` (TF_STATE_BUCKET).
  backend "gcs" {
    prefix = "apps"
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
    googleplay = {
      source  = "oliver-binns/googleplay"
      version = "~> 0.6.3"
    }
  }
}

variable "releases_folder_id" {
  description = "Google Cloud folder of the apps' projects (TF_RELEASES_FOLDER_ID of the terraform environment)."
  type        = string
}

# The organization's Terraform GitHub App. The workflow writes its private key
# to a file, so that the key never ends up in a saved plan.
variable "github_app_id" {
  type = string
}

variable "github_app_installation_id" {
  type = string
}

variable "github_app_private_key_file" {
  type = string
}

provider "github" {
  owner = "SilverNETGroupSGGW"

  app_auth {
    id              = var.github_app_id
    installation_id = var.github_app_installation_id
    pem_file        = file(var.github_app_private_key_file)
  }
}

provider "google" {}

provider "googleplay" {
  developer_id = "8827645756827128332" # KN Silver .NET
}

locals {
  apple = {
    team_id   = "MZMZBQPXAR"
    issuer_id = "69a6de96-7490-47e3-e053-5b8c7c11a4d1"
  }
}

module "plan_wzim" {
  source = "../app-release"

  repository          = "SilverTimetable2"
  android_package     = "com.silvernet.silvertimetable"
  google_project_id   = "plan-wzim-releases"
  google_project_name = "Plan WZIM releases"
  google_folder_id    = var.releases_folder_id
  # GitHub Free has no environments in private repositories; "release" once
  # the repository is public.
  environment       = null
  reviewer_user_ids = [33990351] # ThePhaseless

  apple_team_id               = local.apple.team_id
  app_store_connect_issuer_id = local.apple.issuer_id
  # Set once the Plan WZIM team key (Admin role) exists.
  app_store_connect_key_id = null
  # The answer of the last iOS build, 4.1.4.
  app_store_uses_non_exempt_encryption = false
}

# Dni SGGW still releases with its own workflows (sggw_days/.github/workflows);
# these settings are for flutter-release.yml once it moves to it.
module "dni_sggw" {
  source = "../app-release"

  repository          = "sggw_days"
  android_package     = "com.silvernet.sggw_days"
  google_project_id   = "dni-sggw-releases"
  google_project_name = "Dni SGGW releases"
  google_folder_id    = var.releases_folder_id
  environment         = null

  apple_team_id               = local.apple.team_id
  app_store_connect_issuer_id = local.apple.issuer_id
  # The "Dni SGGW" team key (Admin role).
  app_store_connect_key_id = "8P887NK246"
}

module "kampus_sggw" {
  source = "../app-release"

  repository          = "kampus_sggw"
  android_package     = "com.silvers.kampus_sggw_remake"
  google_project_id   = "kampus-sggw-releases"
  google_project_name = "Kampus SGGW releases"
  google_folder_id    = var.releases_folder_id
  environment         = null

  apple_team_id               = local.apple.team_id
  app_store_connect_issuer_id = local.apple.issuer_id
}

# Variables that were set by hand before Terraform managed them.
import {
  to = module.plan_wzim.github_actions_variable.release["APPLE_TEAM_ID"]
  id = "SilverTimetable2:APPLE_TEAM_ID"
}

import {
  to = module.plan_wzim.github_actions_variable.release["APP_STORE_CONNECT_ISSUER_ID"]
  id = "SilverTimetable2:APP_STORE_CONNECT_ISSUER_ID"
}

import {
  to = module.plan_wzim.github_actions_variable.release["APP_STORE_USES_NON_EXEMPT_ENCRYPTION"]
  id = "SilverTimetable2:APP_STORE_USES_NON_EXEMPT_ENCRYPTION"
}

import {
  to = module.dni_sggw.github_actions_variable.release["APPLE_TEAM_ID"]
  id = "sggw_days:APPLE_TEAM_ID"
}
