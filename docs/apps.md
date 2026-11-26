# Apps

[`apps.yaml`](../apps.yaml) lists every app that may publish to a store.
[`sync-apps.yml`](../.github/workflows/sync-apps.yml) applies it: it sets up
each app's Google Play access and upload key.
[`app-store.yml`](../.github/workflows/app-store.yml) publishes the iOS builds
of the apps with `app_store`, reading the file on `main` for each request
([App Store Connect](stores.md#app-store-connect)). An admin's review approves
every change to the file ([CODEOWNERS](../.github/CODEOWNERS)).

## The file

```yaml
bingownia: # Bingownia
  repo: Bingownia
  repository_id: 1353978751
  play:
    package: com.silver.bingownia
  app_store:
    bundle: com.silver.bingownia
    apple_id: 6838833785
```

- The key names the app: lower-case letters, digits and `_`, starting with a
  letter, at most 40 characters.
- `repo`: the repository's name in SilverNETGroupSGGW.
- `repository_id`: its numeric ID, from
  `gh api repos/SilverNETGroupSGGW/<repo> --jq .id`. The ID stays the same
  when a repository is renamed and changes when it is deleted and created
  again, so the sync refuses an entry whose name and ID disagree on GitHub.
- `play.package` (optional): the Android package in Play Console.
- `app_store.bundle` and `app_store.apple_id` (optional, both or neither):
  the iOS bundle ID and the app's Apple ID in App Store Connect (App
  Information → Apple ID).

No two entries share a repository, ID, package, bundle or Apple ID.
`mise run lint:apps` checks the file, and CI runs it on every pull request.

## Adding an app

1. An admin creates the app in Play Console (and in App Store Connect for
   iOS) and gives the lead access.
2. The lead or an admin opens a pull request that adds the app's entry. The
   lead can open it from a fork.
3. An admin reviews and merges it, which runs Sync apps. The `app_store`
   section needs nothing else: Infra's App Store publisher serves the app from
   the next request on.

Then the lead implements the [task contract](task-contract.md), adds
`ci.yml` and `release.yml` from [Continuous integration](ci.md) and
[Releases](releases.md), adds Renovate, and picks the open-testing countries
in Play Console once. For iOS, the lead answers export compliance and the age
rating in App Store Connect ([per app](stores.md)); the app
repository holds no App Store Connect key.

A brand-new Play app has no package in the Play API until its first bundle is
uploaded, so the sync can only warn. Publish a pre-release; its Google Play job
fails, so download the `.aab` attached to the release and upload it in Play
Console. Then someone runs Sync apps by hand (Actions → Sync apps → Run
workflow) to grant access, and re-runs the failed job.

## What the sync does

It runs on every merge to `main` that changes `apps.yaml`, and by hand
(Actions → Sync apps → Run workflow). It has no schedule: after a manual
change in Play Console or Google Cloud IAM, run it by hand to put things back.

For each app with a `play` section:

- It creates the Google service account
  `app-<repository_id>@silvernet-infra.iam.gserviceaccount.com`, and sets its
  only IAM binding: `roles/iam.workloadIdentityUser` for the tokens of that
  repository in the `releases` pool. Any other member is removed.
- It invites the account in Play Console with "Release to testing tracks" and
  "Release to production" on the app's package, corrects other permissions on
  it, and removes the account's access to any other app.

For every app, it looks at the secret names of the repository and of its
`release` environment:

- A name containing `KEYSTORE` or `JKS` means the app already has an upload
  key; nothing changes.
- Otherwise a name containing `KEY_ALIAS`, `KEY_PASSWORD` or `STORE_PASSWORD`
  gives a warning and nothing changes.
- With none of these, it generates an upload keystore and sets
  `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, `ANDROID_STORE_PASSWORD` and,
  last, `ANDROID_KEYSTORE_BASE64` in the repository.

It never overwrites, deletes or prints a secret.

Then it removes what belongs to apps no longer in the file: each Play user and
service account named `app-<digits>@silvernet-infra.iam.gserviceaccount.com`
whose ID has no entry with `play`, the Play user first. It touches no other
account or user. It does this only when both listings were read in full and no
app had an error in the same run; otherwise it warns that it skipped it.

A draft Play app, an account that IAM or Play does not see yet, and a skipped
removal are warnings in a green run. A name and ID mismatch, a refusal from
Play, an IAM error or an invalid file are errors and fail the run, after the
other apps are done. The run summary has a table of what happened to each app.

## An existing upload key

Put it in the repository's (or its `release` environment's) secrets before
adding the entry. Any name containing `KEYSTORE` or `JKS` is recognised; the
app's `release.yml` maps legacy names to the `ANDROID_*` secrets. A keystore
under any other name gets a second, new upload key.

## Removing an app

Delete its entry, or only its `play` section, and merge. The sync removes the
Play user and deletes the service account. The `ANDROID_*` secrets stay on
purpose: an upload key cannot be recovered without a Play upload-key reset.
Delete them by hand with `gh secret delete` if they are no longer wanted.

Deleting the `app_store` section stops Infra's App Store publisher for the
app at once, since it reads the file on `main`. Releases of an app that pins
an older Infra commit still build the iOS archive, and their "App Store
(Infra)" job fails after 45 minutes. Nothing changes in App Store Connect:
remove the app from sale there if wanted.

## Troubleshooting

- "has no bundle in Play Console yet (draft app)": upload the first bundle by
  hand (see [Adding an app](#adding-an-app)), then run Sync apps again.
- "Play refused (403)": check that `sync-apps@` is still an Admin in Play
  Console. Otherwise invite the `app-<id>@` account named in the error by hand
  (Users and permissions, the two release permissions on that app only).
- "a renamed or replaced repository": the repository at that name has another
  ID. After a rename, fix `repo`; after a repository was replaced, check who
  owns the new one before changing `repository_id`.
- "upload-key secrets without a keystore": an earlier run was interrupted, or
  the keystore sits under a name the sync does not recognise. Delete the
  partial secrets to let the sync create a new key, or add
  `ANDROID_KEYSTORE_BASE64`.
- "not visible to IAM yet" or "to Play yet": the account was just created; run
  Sync apps again in a few minutes.
- "App Store (Infra)" ends with "no ... run in Infra after 45 minutes": the
  app has no `app_store` section on Infra `main`, or the finder is late. Check
  that `app-store.yml` is enabled under Actions in Infra and that its last
  scheduled run is green, then re-run the failed job in the app.

## One-time setup

The sync signs in to Google Cloud and Play as `sync-apps@silvernet-infra`, and
to GitHub as the Silver Sync App. An admin with Owner on `silvernet-infra`
sets this up once:

```sh
gh workflow disable terraform.yml -R SilverNETGroupSGGW/Infra # only while terraform.yml still exists
P=silvernet-infra; N=389882651708
gcloud iam workload-identity-pools list --project $P --location global --show-deleted # no deleted "releases" pool may exist
gcloud iam workload-identity-pools create releases --project $P --location global --display-name "App releases"
gcloud iam workload-identity-pools providers create-oidc flutter-release --project $P --location global \
  --workload-identity-pool releases --issuer-uri https://token.actions.githubusercontent.com \
  --attribute-mapping google.subject=assertion.sub,attribute.repository_id=assertion.repository_id \
  --attribute-condition "assertion.repository_owner_id == '11016071' && assertion.ref.startsWith('refs/tags/') && assertion.job_workflow_ref.startsWith('SilverNETGroupSGGW/Infra/.github/workflows/flutter-release.yml@')"
gcloud iam workload-identity-pools providers create-oidc infra-sync-apps --project $P --location global \
  --workload-identity-pool github --issuer-uri https://token.actions.githubusercontent.com \
  --attribute-mapping google.subject=assertion.sub \
  --attribute-condition "assertion.repository_id == '1094204589' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == 'SilverNETGroupSGGW/Infra/.github/workflows/sync-apps.yml@refs/heads/main' && assertion.environment == 'sync-apps'"
gcloud iam service-accounts create sync-apps --project $P --display-name "sync-apps.yml of SilverNETGroupSGGW/Infra"
gcloud iam service-accounts add-iam-policy-binding sync-apps@$P.iam.gserviceaccount.com --project $P \
  --role roles/iam.workloadIdentityUser \
  --member "principal://iam.googleapis.com/projects/$N/locations/global/workloadIdentityPools/github/subject/repo:SilverNETGroupSGGW/Infra:environment:sync-apps"
gcloud projects add-iam-policy-binding $P --member serviceAccount:sync-apps@$P.iam.gserviceaccount.com \
  --role roles/iam.serviceAccountAdmin --condition None
```

1. Play Console → Users and permissions: invite
   `sync-apps@silvernet-infra.iam.gserviceaccount.com` with "Admin (all
   permissions)".
2. Gate: temporarily grant yourself `roles/iam.serviceAccountTokenCreator` on
   `sync-apps@`; get a token with
   `POST https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/sync-apps@silvernet-infra.iam.gserviceaccount.com:generateAccessToken`,
   body `{"scope":["https://www.googleapis.com/auth/androidpublisher"]}`;
   `GET …/developers/8827645756827128332/users?pageSize=-1` must answer 200;
   remove your binding. A 403 means Play refuses service-account user
   management: stop and bring it back. The fallback: the sync still does
   accounts, bindings and keystores, and the admin invites `app-<id>@` once
   per app in Play Console.
3. Repurpose GitHub App 5200235: rename "Silver Sync"; permissions Secrets
   read/write, Environments read, Metadata read; remove Administration and
   Variables; install on all repositories; generate a new private key. The
   old keys would now carry Secrets write, and with `terraform.yml` disabled
   nothing reads them, so retire them right away:
   - App settings → Private keys: delete every key except the new one.
   - Delete the Infra environments `terraform` and `terraform-plan` (they hold
     `TF_GITHUB_APP_PRIVATE_KEY` and `TF_HCP_TOKEN`) and the `TF_*` variables,
     and revoke the HCP token in HCP.
4. Infra environment `sync-apps` (main only) and its secret:

   ```sh
   gh api -X PUT repos/SilverNETGroupSGGW/Infra/environments/sync-apps -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
   gh api -X POST repos/SilverNETGroupSGGW/Infra/environments/sync-apps/deployment-branch-policies -f name=main -f type=branch
   gh secret set SYNC_APP_PRIVATE_KEY --env sync-apps -R SilverNETGroupSGGW/Infra < key.pem
   ```

   Delete the `.pem` afterwards.
5. After the `apps.yaml` change merges: Settings → Rules → `main` → tick
   "Require review from Code Owners".
6. Once every app has released through the sync's accounts, retire the old
   Google access:
   - Delete the provider `infra-terraform` in the pool `github`, remove the
     roles of `terraform@silvernet-infra` in the app projects, and delete
     that account.
   - Remove the old Play users `releases@silvertimetable-bea41` and
     `github-action-1132232110@sggw-days`, then delete
     `releases@silvertimetable-bea41` and the pool `github` of
     `silvertimetable-bea41`. Keep `github-action-1132232110@sggw-days`:
     Dni SGGW's `firebase.yml` deploys with it.
