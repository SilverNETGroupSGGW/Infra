# Store setup

The per-app setup of Google Play and App Store Connect for
[`flutter-release.yml`](../.github/workflows/flutter-release.yml).
[Terraform](terraform.md) sets up the sign-in and the repository variables; you
grant the Play Console access and set the secrets below by hand.

## Google Play

The App Bundle is signed with the app's upload key (the `ANDROID_*` secrets).
The workflow signs in with Workload Identity Federation, set up by
`terraform/apps`. Invite its `releases@<project>.iam.gserviceaccount.com`
service account in Play Console (Users and permissions) with "Release to
testing tracks" and "Release to production, exclude devices, and use Play App
Signing" for this app only. Store the upload key as secrets of
the release environment (without `--env release` while the app has no
environment):

```sh
base64 -w0 key.jks | gh secret set ANDROID_KEYSTORE_BASE64 --env release
gh secret set ANDROID_KEY_ALIAS --env release
gh secret set ANDROID_KEY_PASSWORD --env release
gh secret set ANDROID_STORE_PASSWORD --env release
```

Pre-releases go to the open testing track and releases to production, all
at once, each replacing the release on its track. Open testing needs its
countries or regions chosen once in Play Console (Test and release → Open
testing). A staged rollout started
in Play Console stops the job until it is finished or halted. With managed
publishing on, approved changes still wait for "Publish changes" in Play
Console.

## App Store Connect

The workflow archives the app on a GitHub macOS runner and has Apple sign it
for distribution (cloud signing), so no certificate is stored. macOS minutes
count ten times against the Actions minutes of a private repository.

1. In App Store Connect, Users and Access → Integrations → App Store Connect
   API, create a team key with the Admin role (cloud signing needs it; the
   Account Holder enables API access once). Download the `.p8` file, which
   can only be downloaded once. Every team key reaches all apps of the team; a
   key per app can still be revoked on its own. The App Store Connect API
   cannot create API keys, so this step stays manual.
2. Store the `.p8` contents as a secret of the release environment
   (`gh secret set APP_STORE_CONNECT_KEY --env release < AuthKey_KEYID.p8`;
   the repository without an environment), and pass its key ID, the issuer ID
   and the team ID (Membership details on developer.apple.com) to the app's
   block in `terraform/apps`, which sets the variables
   `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID` and
   `APPLE_TEAM_ID`.
3. Answer export compliance once per app: add `ITSAppUsesNonExemptEncryption`
   to `ios/Runner/Info.plist`, or set `app_store_uses_non_exempt_encryption`
   of the app's block (the variable `APP_STORE_USES_NON_EXEMPT_ENCRYPTION`)
   to `false` or `true`. This is a legal declaration for the app's owner to
   make. Without it the iOS jobs stop before building: TestFlight and App
   Review both need the answer.
4. Answer the age rating questions in App Information; App Store Connect asks
   new ones before it accepts updates.

Pre-releases go to TestFlight's public testers: the job adds the build to an
external group with a public link (a new "Public beta" group if there is
none), turns on "Automatically notify testers", sends it to Beta App Review
and puts the public link in the run summary; testers get the build once Apple
approves it.
TestFlight test information that is still empty (Beta App Review contact, beta
description, feedback e-mail) is filled from the App Store review details and
description, and the release description becomes "What to Test". A released
version accepts no new builds, so the beta after a release needs a higher
version name. Apple reviews one build per version at a time: a beta published
while the previous beta of its version is still in Beta App Review fails the
App Store Connect job; re-run the job once that review is done.
