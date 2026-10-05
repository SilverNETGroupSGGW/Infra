terraform {
  required_version = ">= 1.11"

  required_providers {
    github = {
      source  = "integrations/github"
      version = ">= 6.13, < 7.0"
    }
    google = {
      source  = "hashicorp/google"
      version = ">= 8.0, < 9.0"
    }
    googleplay = {
      source  = "oliver-binns/googleplay"
      version = ">= 0.6.3, < 0.7.0"
    }
  }
}
