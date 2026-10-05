# The release setup of one app: a Google Cloud project that lets only the app's
# release jobs act as its Google Play service account (Workload Identity
# Federation, no key), the service account's access to the app in Play Console,
# and the app repository's release environment and variables. Secrets stay out
# of Terraform: set them with `gh secret set`.

data "github_repository" "app" {
  name = var.repository
}

locals {
  # Numeric IDs survive renames of the repository and its owner.
  repository_id    = tostring(data.github_repository.app.repo_id)
  release_workflow = "SilverNETGroupSGGW/Infra/.github/workflows/flutter-release.yml@"
}

resource "google_project" "release" {
  project_id = var.google_project_id
  name       = var.google_project_name
  org_id     = var.google_org_id
  folder_id  = var.google_folder_id
}

resource "google_project_service" "release" {
  for_each = toset([
    "androidpublisher.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])

  project = google_project.release.project_id
  service = each.value
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = google_project.release.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.release]
}

resource "google_iam_workload_identity_pool_provider" "flutter_release" {
  project                            = google_project.release.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "flutter-release"
  display_name                       = "flutter-release.yml"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }
  # Only the shared release workflow, run by the app's repository (in its
  # release environment, after a reviewer approved the job).
  attribute_condition = join(" && ", compact([
    "assertion.repository_id == '${local.repository_id}'",
    var.environment == null ? null : "assertion.environment == '${var.environment}'",
    "assertion.job_workflow_ref.startsWith('${local.release_workflow}')",
  ]))

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "play_publisher" {
  project      = google_project.release.project_id
  account_id   = "play-publisher"
  display_name = "Google Play releases of ${var.repository}"

  depends_on = [google_project_service.release]
}

resource "google_service_account_iam_member" "play_publisher_github" {
  service_account_id = google_service_account.play_publisher.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${local.repository_id}"
}

# The Play Console invite of the service account, limited to this app's
# releases.
resource "googleplay_user" "play_publisher" {
  email = google_service_account.play_publisher.email
  # The provider requires an account-wide permission; this is the narrowest
  # (read-only crash and vitals data).
  global_permissions = ["CAN_VIEW_APP_QUALITY_GLOBAL"]
}

resource "googleplay_app_iam" "play_publisher" {
  app_id  = var.android_package
  user_id = googleplay_user.play_publisher.email
  permissions = [
    "CAN_MANAGE_TRACK_APKS",  # Release to testing tracks
    "CAN_MANAGE_PUBLIC_APKS", # Release to production, exclude devices, and use Play App Signing
    # Implied by the two above.
    "CAN_VIEW_NON_FINANCIAL_DATA",
    "CAN_VIEW_APP_QUALITY",
  ]
}

resource "github_repository_environment" "release" {
  count = var.environment == null ? 0 : 1

  repository  = data.github_repository.app.name
  environment = var.environment

  reviewers {
    users = var.reviewer_user_ids
    teams = var.reviewer_team_ids
  }

  # Release runs deploy from their tag.
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "release_tags" {
  count = var.environment == null ? 0 : 1

  repository  = data.github_repository.app.name
  environment = github_repository_environment.release[0].environment
  tag_pattern = "*"
}

locals {
  app_store_variables = {
    APPLE_TEAM_ID                        = var.apple_team_id
    APP_STORE_CONNECT_ISSUER_ID          = var.app_store_connect_issuer_id
    APP_STORE_CONNECT_KEY_ID             = var.app_store_connect_key_id
    APP_STORE_USES_NON_EXEMPT_ENCRYPTION = var.app_store_uses_non_exempt_encryption == null ? null : tostring(var.app_store_uses_non_exempt_encryption)
  }
}

# Repository variables, which the workflow reads before any job names the
# environment.
resource "github_actions_variable" "release" {
  for_each = merge(
    {
      GOOGLE_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.flutter_release.name
      GOOGLE_PLAY_SERVICE_ACCOUNT       = google_service_account.play_publisher.email
    },
    { for name, value in local.app_store_variables : name => value if value != null },
  )

  repository    = data.github_repository.app.name
  variable_name = each.key
  value         = each.value
}
