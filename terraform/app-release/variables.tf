variable "repository" {
  description = "Name of the app's repository, owned by the GitHub provider's owner."
  type        = string
}

variable "android_package" {
  description = "Android application ID of the app in Play Console."
  type        = string
}

variable "google_project_id" {
  description = "ID of the Google Cloud project for the app's Google Play releases."
  type        = string
}

variable "google_project_name" {
  description = "Display name of that project, when the module creates it."
  type        = string
  default     = null
}

variable "create_google_project" {
  description = "Create the project; false uses an existing one (service accounts can only create projects in a Google Cloud organization)."
  type        = bool
  default     = true
}

variable "google_org_id" {
  description = "Google Cloud organization of the project; null for none (or with google_folder_id)."
  type        = string
  default     = null
}

variable "google_folder_id" {
  description = "Google Cloud folder of the project; null for none."
  type        = string
  default     = null
}

variable "environment" {
  description = "Release environment with required reviewers, passed as `environment` to flutter-release.yml; null for none (GitHub Free has no environments in private repositories)."
  type        = string
  default     = "release"
}

variable "reviewer_user_ids" {
  description = "Numeric GitHub user IDs that approve release jobs (`gh api users/NAME --jq .id`)."
  type        = list(number)
  default     = []
}

variable "reviewer_team_ids" {
  description = "Numeric GitHub team IDs that approve release jobs."
  type        = list(number)
  default     = []
}

variable "apple_team_id" {
  description = "Apple Developer team ID (Membership details); null without App Store Connect."
  type        = string
  default     = null
}

variable "app_store_connect_issuer_id" {
  description = "Issuer ID of the App Store Connect API keys; null without App Store Connect."
  type        = string
  default     = null
}

variable "app_store_connect_key_id" {
  description = "Key ID of the app's App Store Connect API key (Admin role); null until it exists."
  type        = string
  default     = null
}

variable "app_store_uses_non_exempt_encryption" {
  description = "Export compliance answer for TestFlight and App Review; null to answer in Info.plist."
  type        = bool
  default     = null
}
