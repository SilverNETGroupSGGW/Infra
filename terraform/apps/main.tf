# Store release setup of the apps (terraform/app-release), applied by CI. A new
# app is a module block here plus its project in `apps` of terraform/bootstrap.

terraform {
  required_version = ">= 1.11"

  # State in HCP Terraform (TF_CLOUD_ORGANIZATION, TF_TOKEN_app_terraform_io);
  # runs execute locally in the Terraform workflow.
  cloud {
    workspaces {
      name = "apps"
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
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.5"
    }
    googleplay = {
      source  = "oliver-binns/googleplay"
      version = "~> 0.6.3"
    }
  }
}

# With an organization, new apps can get projects in the apps folder:
# uncomment this, set google_folder_id and drop create_google_project.
#
# variable "apps_folder_id" {
#   description = "Google Cloud folder of the apps' projects (TF_APPS_FOLDER_ID of the terraform environment)."
#   type        = string
# }

# The organization's Terraform GitHub App. The workflow writes its private key
# to a file, so the key never ends up in a saved plan.
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

# API quota is charged to the project of each resource (the apps' projects,
# where the APIs are enabled), not to the Terraform service account's project.
provider "google" {
  user_project_override = true
}

# Firebase resources (firebase.tf).
provider "google-beta" {
  user_project_override = true
}

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

  repository            = "SilverTimetable2"
  android_package       = "com.silvernet.silvertimetable"
  google_project_id     = "silvertimetable-bea41"
  create_google_project = false # the app's Firebase project
  # google_folder_id    = var.apps_folder_id
  # GitHub Free: no environments in private repos; "release" once public.
  environment       = null
  reviewer_user_ids = [33990351] # ThePhaseless

  apple_team_id               = local.apple.team_id
  app_store_connect_issuer_id = local.apple.issuer_id
  # The "Plan WZIM releases" team key (Admin role).
  app_store_connect_key_id = "36AU2J89DD"
  # The answer of the last iOS build, 4.1.4.
  app_store_uses_non_exempt_encryption = false
}

# Dni SGGW releases with its own workflows (sggw_days/.github/workflows); these
# settings are for when it moves to flutter-release.yml.
module "dni_sggw" {
  source = "../app-release"

  repository            = "sggw_days"
  android_package       = "com.silvernet.sggw_days"
  google_project_id     = "sggw-days"
  create_google_project = false # the app's Firebase project
  # google_folder_id    = var.apps_folder_id
  environment = null

  apple_team_id               = local.apple.team_id
  app_store_connect_issuer_id = local.apple.issuer_id
  # The "Dni SGGW" team key (Admin role).
  app_store_connect_key_id = "8P887NK246"
}

# Kampus SGGW waits until the club has access to its Firebase project;
# uncomment it together with its entry in terraform/bootstrap.
#
# module "kampus_sggw" {
#   source = "../app-release"
#
#   repository            = "kampus_sggw"
#   android_package       = "com.silvers.kampus_sggw_remake"
#   google_project_id     = "kampus-sggw-2021"
#   create_google_project = false # the app's Firebase project
#   # google_folder_id    = var.apps_folder_id
#   environment = null
#
#   apple_team_id               = local.apple.team_id
#   app_store_connect_issuer_id = local.apple.issuer_id
# }

# Variables set by hand before Terraform managed them.
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
  to = module.plan_wzim.github_actions_variable.release["APP_STORE_CONNECT_KEY_ID"]
  id = "SilverTimetable2:APP_STORE_CONNECT_KEY_ID"
}

import {
  to = module.dni_sggw.github_actions_variable.release["APPLE_TEAM_ID"]
  id = "sggw_days:APPLE_TEAM_ID"
}
