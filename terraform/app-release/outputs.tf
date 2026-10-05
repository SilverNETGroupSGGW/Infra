output "google_play_service_account" {
  description = "Service account that publishes the app on Google Play."
  value       = google_service_account.play_publisher.email
}

output "secrets_command" {
  description = "Stores the App Store Connect key as a secret of the release environment, or of the repository without one."
  value       = "gh secret set APP_STORE_CONNECT_KEY --repo ${data.github_repository.app.full_name}${var.environment == null ? "" : " --env ${var.environment}"} < AuthKey_KEYID.p8"
}
