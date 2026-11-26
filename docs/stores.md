# Store setup

The per-app setup of Google Play and App Store Connect for
[`flutter-release.yml`](../.github/workflows/flutter-release.yml).
The sync of [`apps.yaml`](apps.md) sets up the Play Console access and the
upload key; the rest below is done by hand.

## Google Play

The App Bundle is signed with the app's upload key (the `ANDROID_*` secrets).
The workflow signs in with Workload Identity Federation as the app's own
service account, which the sync creates and invites in Play Console
([What the sync does](apps.md#what-the-sync-does)). The sync also generates
the upload key; an app that already has one keeps it
([An existing upload key](apps.md#an-existing-upload-key)).

Pre-releases go to the open testing track and releases to production, all
at once, each replacing the release on its track. Open testing needs its
countries or regions chosen once in Play Console (Test and release → Open
testing). A staged rollout started
in Play Console stops the job until it is finished or halted. With managed
publishing on, approved changes still wait for "Publish changes" in Play
Console.

## App Store Connect

The app's release run archives the app on a GitHub macOS runner, signed ad
hoc, and leaves a request. Infra's
[`app-store.yml`](../.github/workflows/app-store.yml) signs it for
distribution with Apple's cloud signing (no certificate is stored), uploads it
and submits it with one team key that only Infra holds
([how a release flows](releases.md#how-a-release-flows)). The archive's macOS
minutes count ten times against the Actions minutes of a private app
repository; the upload runs in Infra, which is public.

### One-time setup (admin)

1. In App Store Connect, Users and Access → Integrations → App Store Connect
   API, create the team key "Infra releases" with the Admin role (cloud
   signing needs it; the Account Holder enables API access once). Download the
   `.p8` file, which can only be downloaded once. The App Store Connect API
   cannot create API keys, so this step stays manual.
2. Create the GitHub App "Silver App Store" with Actions, Contents and
   Metadata read only, install it on all repositories of the organization and
   generate a private key. Infra uses it to read the apps' release runs and
   their artifacts.
3. Create the Infra environment `app-store`, deployable from `main` only, and
   store both keys in it:

   ```sh
   gh api -X PUT repos/SilverNETGroupSGGW/Infra/environments/app-store -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
   gh api -X POST repos/SilverNETGroupSGGW/Infra/environments/app-store/deployment-branch-policies -f name=main -f type=branch
   gh secret set APP_STORE_CONNECT_KEY --env app-store -R SilverNETGroupSGGW/Infra < AuthKey_X.p8
   gh secret set RELEASES_APP_PRIVATE_KEY --env app-store -R SilverNETGroupSGGW/Infra < app.pem
   gh variable set APP_STORE_CONNECT_KEY_ID --env app-store -R SilverNETGroupSGGW/Infra --body <key id>
   gh variable set RELEASES_APP_CLIENT_ID --env app-store -R SilverNETGroupSGGW/Infra --body "$(gh api apps/<app slug> --jq .client_id)"
   ```

   Delete the `.p8` and `.pem` files afterwards.

The scheduled finder is the only scheduled workflow in Infra. GitHub turns
off schedules in a public repository after 60 days without activity; if that
happens, enable `app-store.yml` again under Actions.

### Per app

1. An admin creates the app in App Store Connect and adds the `app_store`
   section (bundle ID and Apple ID) to its entry in `apps.yaml`
   ([Apps](apps.md)). The app repository gets no key or variable for it.
2. The lead answers export compliance once: add
   `ITSAppUsesNonExemptEncryption` to `ios/Runner/Info.plist`, or set the
   variable `APP_STORE_USES_NON_EXEMPT_ENCRYPTION` to `false` or `true`
   (`gh variable set`). This is a legal declaration for the app's owner to
   make. Without it the iOS archive stops before building: TestFlight and App
   Review both need the answer.
3. The lead answers the age rating questions in App Information; App Store
   Connect asks new ones before it accepts updates.

### Rotating the key

Create a new team key with the Admin role, replace the `APP_STORE_CONNECT_KEY`
secret and the `APP_STORE_CONNECT_KEY_ID` variable of the `app-store`
environment (commands above), then revoke the old key in App Store Connect.
Revoke it at once if it may have leaked: it reaches every app of the team.

### An app removed from sale

An app removed from sale still gets TestFlight builds and App Review
submissions. It returns to the App Store only when an admin restores its
availability (Pricing and Availability in App Store Connect).

### TestFlight and App Review

Pre-releases go to TestFlight's public testers: Infra's run adds the build to
an external group with a public link (a new "Public beta" group if there is
none), turns on "Automatically notify testers", sends it to Beta App Review
and puts the public link in its run summary; testers get the build once Apple
approves it.
TestFlight test information that is still empty (Beta App Review contact, beta
description, feedback e-mail) is filled from the App Store review details and
description, and the release description becomes "What to Test". A released
version accepts no new builds, so the beta after a release needs a higher
version name. Apple reviews one build per version at a time: a beta published
while the previous beta of its version is still in Beta App Review fails the
App Store Connect job of Infra's run; an Infra admin re-runs it once that
review is done.
