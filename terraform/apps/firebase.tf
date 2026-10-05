# The apps' Firebase resources, imported as they were set up in the Firebase
# console. Deployments stay with the apps' repositories (`firebase deploy`):
# Firestore and Storage rules, Hosting releases and Cloud Functions are not
# managed here, so that the two never overwrite each other. Databases, buckets
# and Hosting sites can never be destroyed by Terraform.
#
# Not imported: Realtime Database (none), Analytics and Cloud Messaging (no
# Terraform resources), Dni SGGW's Storage bucket (locked on the Spark plan)
# and its sendNotificationBroadcast function (deployed from sggw_days).

locals {
  plan_wzim_project = "silvertimetable-bea41"
  dni_sggw_project  = "sggw-days"
}

# Plan WZIM

import {
  to = google_firebase_project.plan_wzim
  id = "projects/silvertimetable-bea41"
}

resource "google_firebase_project" "plan_wzim" {
  provider = google-beta
  project  = local.plan_wzim_project

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = google_firebase_android_app.plan_wzim
  id = "projects/silvertimetable-bea41/androidApps/1:494093391066:android:94ffbd475ce051c9e201e3"
}

resource "google_firebase_android_app" "plan_wzim" {
  provider        = google-beta
  project         = local.plan_wzim_project
  display_name    = "Plan WZIM (Android)"
  package_name    = "com.silvernet.silvertimetable"
  sha1_hashes     = ["511748c9a81e0a198b2b6f9d15e194d204780d4d"]
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [display_name]
  }
}

import {
  to = google_firebase_apple_app.plan_wzim
  id = "projects/silvertimetable-bea41/iosApps/1:494093391066:ios:b915854468782e08e201e3"
}

resource "google_firebase_apple_app" "plan_wzim" {
  provider        = google-beta
  project         = local.plan_wzim_project
  display_name    = "Plan WZIM (iOS)"
  bundle_id       = "com.silver.silvertimetable"
  team_id         = "MZMZBQPXAR"
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [display_name, app_store_id]
  }
}

# An older Apple app with the Android package name; no users.
import {
  to = google_firebase_apple_app.plan_wzim_legacy
  id = "projects/silvertimetable-bea41/iosApps/1:494093391066:ios:5785cfe0adf7dd85e201e3"
}

resource "google_firebase_apple_app" "plan_wzim_legacy" {
  provider        = google-beta
  project         = local.plan_wzim_project
  display_name    = "Plan WZIM (old iOS)"
  bundle_id       = "com.silvernet.silvertimetable"
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [display_name, app_store_id, team_id]
  }
}

import {
  to = google_firebase_web_app.plan_wzim
  id = "projects/silvertimetable-bea41/webApps/1:494093391066:web:1be29c4e97bd07e1e201e3"
}

resource "google_firebase_web_app" "plan_wzim" {
  provider        = google-beta
  project         = local.plan_wzim_project
  display_name    = "silvertimetableweb"
  deletion_policy = "ABANDON"
}

import {
  to = google_firestore_database.plan_wzim
  id = "projects/silvertimetable-bea41/databases/(default)"
}

resource "google_firestore_database" "plan_wzim" {
  project         = local.plan_wzim_project
  name            = "(default)"
  location_id     = "eur3"
  type            = "FIRESTORE_NATIVE"
  deletion_policy = "ABANDON"

  lifecycle {
    prevent_destroy = true
  }
}

# The legacy App Engine buckets of the project (multi-region EU): tracked, but
# their settings stay as they are.
import {
  for_each = toset(["silvertimetable-bea41.appspot.com", "staging.silvertimetable-bea41.appspot.com"])
  to       = google_storage_bucket.plan_wzim[each.key]
  id       = "silvertimetable-bea41/${each.key}"
}

resource "google_storage_bucket" "plan_wzim" {
  for_each = toset(["silvertimetable-bea41.appspot.com", "staging.silvertimetable-bea41.appspot.com"])

  project  = local.plan_wzim_project
  name     = each.key
  location = "EU"

  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}

import {
  to = google_firebase_storage_bucket.plan_wzim
  id = "projects/silvertimetable-bea41/buckets/silvertimetable-bea41.appspot.com"
}

resource "google_firebase_storage_bucket" "plan_wzim" {
  provider  = google-beta
  project   = local.plan_wzim_project
  bucket_id = google_storage_bucket.plan_wzim["silvertimetable-bea41.appspot.com"].name
}

# Dni SGGW

import {
  to = google_firebase_project.dni_sggw
  id = "projects/sggw-days"
}

resource "google_firebase_project" "dni_sggw" {
  provider = google-beta
  project  = local.dni_sggw_project

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = google_firebase_android_app.dni_sggw
  id = "projects/sggw-days/androidApps/1:583939995714:android:7d3fc1983787b0610755db"
}

resource "google_firebase_android_app" "dni_sggw" {
  provider        = google-beta
  project         = local.dni_sggw_project
  display_name    = "Dni SGGW (android)"
  package_name    = "com.silvernet.sggw_days"
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [sha1_hashes, sha256_hashes]
  }
}

import {
  to = google_firebase_apple_app.dni_sggw
  id = "projects/sggw-days/iosApps/1:583939995714:ios:bb8207bc872f33f40755db"
}

resource "google_firebase_apple_app" "dni_sggw" {
  provider        = google-beta
  project         = local.dni_sggw_project
  display_name    = "Dni SGGW (ios)"
  bundle_id       = "com.silver.sggw-days"
  deletion_policy = "ABANDON"

  lifecycle {
    ignore_changes = [app_store_id, team_id]
  }
}

import {
  to = google_firebase_web_app.dni_sggw
  id = "projects/sggw-days/webApps/1:583939995714:web:64bb87337df191160755db"
}

resource "google_firebase_web_app" "dni_sggw" {
  provider        = google-beta
  project         = local.dni_sggw_project
  display_name    = "Dni SGGW (web)"
  deletion_policy = "ABANDON"
}

import {
  to = google_firestore_database.dni_sggw
  id = "projects/sggw-days/databases/(default)"
}

resource "google_firestore_database" "dni_sggw" {
  project         = local.dni_sggw_project
  name            = "(default)"
  location_id     = "europe-central2"
  type            = "FIRESTORE_NATIVE"
  deletion_policy = "ABANDON"

  lifecycle {
    prevent_destroy = true
  }
}

# The default site; sggw_days deploys its releases.
import {
  to = google_firebase_hosting_site.dni_sggw
  id = "projects/sggw-days/sites/sggw-days"
}

resource "google_firebase_hosting_site" "dni_sggw" {
  provider = google-beta
  project  = local.dni_sggw_project
  site_id  = "sggw-days"

  lifecycle {
    prevent_destroy = true
  }
}
