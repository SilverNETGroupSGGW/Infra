# Infra

Infrastructure of SilverNETGroupSGGW:

- reusable GitHub Actions workflows for the Flutter apps: continuous
  integration, releases to GitHub, Google Play, App Store Connect and GitHub
  Pages (this README);
- the Terraform setup of the apps' store releases ([Store setup](#store-setup));
- the Docker Compose services on the club's server
  ([docs/deploy.md](docs/deploy.md)).

| Workflow | What it does |
| --- | --- |
| [`flutter-ci.yml`](.github/workflows/flutter-ci.yml) | Lints and tests the app, checks generated code, builds the release APKs and the web app, optionally the iOS app |
| [`flutter-release.yml`](.github/workflows/flutter-release.yml) | Builds a GitHub release, attaches the builds to it and publishes them to the stores and Pages; promotes a tested beta without rebuilding it |
| [`flutter-pages.yml`](.github/workflows/flutter-pages.yml) | Serves the newest release's web build at the site root and the newest build of any kind, usually a beta, under `/beta/` |

Each app keeps short caller workflows and implements a few `mise` tasks (the
[task contract](#task-contract)); everything else lives here, so a fix or a new
store requirement is made once.

## Using the workflows

Reference the workflows by commit SHA, with the version in a comment, and let
Renovate update the SHA (`config:recommended` does):

```yaml
uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-ci.yml@<commit-sha> # v1.0.0
```

Tags follow semantic versioning. A major version changes inputs, secrets,
variables or the task contract incompatibly; the release notes say how to
update.

### CI

`.github/workflows/ci.yml` in the app:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  ci:
    # Optional: skip while the repository is private, so CI costs no minutes.
    if: ${{ github.event.repository.visibility == 'public' }}
    permissions:
      contents: read # clone the repository
    uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-ci.yml@<commit-sha> # v1.0.0
    with:
      app-dir: app # the directory with pubspec.yaml; default "."
      codegen: true # if the app commits generated code
```

| Input | Default | |
| --- | --- | --- |
| `app-dir` | `.` | Directory of the Flutter app (the one with `pubspec.yaml`) |
| `codegen` | `false` | Run `mise run codegen` and fail if it changes the app's `lib/` |
| `android` | `true` | Build the release APKs (`mise run build:apk`) and keep them for 7 days |
| `web` | `true` | Build the web app (`mise run build:web`) |
| `ios` | `false` | Build the iOS app without signing (`mise run build:ios`) on macOS; macOS minutes count ten times on private repositories |
| `macos-runner`, `xcode-version` | `macos-26`, `26.6` | Runner and Xcode of the iOS build |
| `mise-version` | newest release | mise version to install |

The jobs are called `Lint and test`, `Build Android`, `Build web` and
`Build iOS` (shown as `ci / Lint and test` and so on) for branch protection.

### Releases

`.github/workflows/release.yml` in the app:

```yaml
name: Release
run-name: Release ${{ github.event.release.tag_name }} (${{ github.event.action }})

on:
  release:
    # published: build a release or pre-release; released: also promote a beta
    # that was turned into a release.
    types: [published, released]

permissions: {}

# Nothing from the Actions cache reaches a signed build.
cache-mode: none

concurrency:
  group: release-${{ github.event.release.tag_name }}
  cancel-in-progress: false
  # Wait in line instead of replacing a waiting run (e.g. a promotion).
  queue: max

jobs:
  release:
    # Required even without Pages or Google Play: GitHub checks what the called
    # jobs ask for when the run starts. Each job gets no more than it needs.
    permissions:
      contents: write # attach the builds to the release
      id-token: write # Google Play (Workload Identity Federation) and Pages
      pages: write # publish the web app
    uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-release.yml@<commit-sha> # v1.0.0
    with:
      app-dir: app
      android-package: com.example.myapp # turns on Google Play
      bundle-id: com.example.myapp # turns on iOS and App Store Connect
      apple-app-id: "1234567890"
      pages: true
    secrets:
      ANDROID_KEYSTORE_BASE64: ${{ secrets.ANDROID_KEYSTORE_BASE64 }}
      ANDROID_KEY_ALIAS: ${{ secrets.ANDROID_KEY_ALIAS }}
      ANDROID_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}
      ANDROID_STORE_PASSWORD: ${{ secrets.ANDROID_STORE_PASSWORD }}
      APP_STORE_CONNECT_KEY: ${{ secrets.APP_STORE_CONNECT_KEY }}
```

Pass the secrets one by one rather than with `secrets: inherit`, so the
workflow only gets what it uses.

| Input | Default | |
| --- | --- | --- |
| `app-dir` | `.` | Directory of the Flutter app |
| `artifact-prefix` | repository name | File name prefix of the release assets: `<prefix>-<tag>.aab`, `<prefix>-<tag>-web.zip`, ... |
| `android-package` | | Android application ID; turns on Google Play |
| `android-keystore-path` | `android/app/key.jks` | Where the build reads the upload keystore, relative to `app-dir` |
| `bundle-id`, `apple-app-id` | | iOS bundle ID and the app's numeric Apple ID (App Store Connect, App Information); `bundle-id` turns on the iOS build and App Store Connect |
| `play-notes-language` | `en-US` | Language of the Google Play release notes; one of the store listing's languages |
| `pages` | `false` | Deploy the web builds to GitHub Pages once Pages is enabled for the repository |
| `environment` | | Environment of the jobs that use signing or store credentials, e.g. one with required reviewers |
| `build-tools` | `flutter,java` | mise tools the Android and web builds install |
| `macos-runner`, `xcode-version`, `mise-version` | `macos-26`, `26.6`, newest release | |

The workflow reads these repository (or organization) variables of the app:

| Variable | |
| --- | --- |
| `GOOGLE_WORKLOAD_IDENTITY_PROVIDER`, `GOOGLE_PLAY_SERVICE_ACCOUNT` | Google Play sign-in ([setup](#google-play)) |
| `APP_STORE_CONNECT_KEY_ID`, `APP_STORE_CONNECT_ISSUER_ID`, `APPLE_TEAM_ID` | App Store Connect sign-in and signing ([setup](#app-store-connect)) |
| `APP_STORE_USES_NON_EXEMPT_ENCRYPTION` | Optional export compliance answer, `false` or `true` |

A store's jobs run when the app passes its store ID (`android-package`,
`bundle-id`) and the store's service account or key ID variable is set, so
organization-wide variables don't turn a store on for every app. A half-done
setup (a missing secret, variable or `apple-app-id`) fails the run before
anything is built; with `environment`, the jobs that use the secrets check
them.

#### How a release flows

- **Version:** the tag is the version: `X.Y.Z` for a release and
  `X.Y.Z-beta.N` (any label ending in its number, such as `rc.N`) for a
  pre-release, with or without a leading `v`. The builds get `X.Y.Z` as their
  version name and the build number (Android `versionCode`, iOS
  `CFBundleVersion`) `X·1000000 + Y·10000 + Z·100 + N`, where a release counts
  as `N` = 99: every new version or pre-release gets a higher number, and a
  release sorts after its pre-releases. `X` goes up to 2099, `Y` and `Z` up to
  99 and `N` from 1 to 98, and the pre-releases of a version need different
  numbers (`beta.1`, `beta.2`, `rc.3`). The version in `pubspec.yaml` is only
  used by local builds.
- **Beta:** publish a GitHub pre-release with a pre-release tag. The workflow
  builds signed per-ABI APKs, an App Bundle and the web app and attaches them
  to the release. Google Play gets the bundle on the beta (open testing) track,
  TestFlight gets the iOS build in a group with a public link and sends it to
  Beta App Review (the link is in the run summary), and Pages serves it under
  `/beta/`.
- **Production:** publish a release tagged `X.Y.Z`. Google Play gets it on the
  production track, App Review gets the iOS build (released once approved),
  and Pages serves it at the site root.
- **Promotion:** untick "Set as a pre-release" on a tested beta. The same Play
  build moves to production, the same TestFlight build goes to App Review, and
  Pages serves it at the root without the BETA badge; nothing is rebuilt. The
  store jobs check again that the release is still a release before changing
  anything.
- **Release notes:** the release description becomes the store release notes
  (Google Play: up to 500 characters; App Store: "What's New" in every
  language).
- **Re-runs** continue where a run stopped: an uploaded bundle is not uploaded
  again, and an unfinished App Store submission is completed.

Don't enable immutable releases in the app repositories: the builds are
attached after the release is published.

#### Security

- `cache-mode: none`: nothing from the Actions cache, which other workflows
  can write, reaches a signed build.
- Only the `publish` job can write to the repository, and it runs no build
  code. The upload key is only in the Android job; the App Store Connect key
  only in jobs that run no project build code.
- Google Play uses Workload Identity Federation: no Google key is stored, and
  the provider can require that the token comes from this workflow (below).
- Set `environment` to an environment with required reviewers to approve
  releases: the Android, Google Play, iOS upload and App Store Connect jobs
  each wait for an approval when they start. Release runs deploy from their
  tag, so allow the release tags in the environment's deployment branches and
  tags (for example the tag rule `*`). Environment secrets only reach jobs that
  name the environment, under the names this workflow reads
  (`ANDROID_KEYSTORE_BASE64`, ..., `APP_STORE_CONNECT_KEY`), whatever the
  caller passes.

### Pages

The release workflow deploys Pages itself when `pages: true` and Pages is
enabled for the repository (free plan: public repositories only). To redeploy by
hand, add `.github/workflows/pages.yml` to the app:

```yaml
name: Deploy web to GitHub Pages

on:
  workflow_dispatch:

permissions: {}

jobs:
  pages:
    permissions:
      contents: read # read the releases and their web builds
      pages: write # publish the site
      id-token: write # authenticate the Pages deployment (OIDC)
    uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-pages.yml@<commit-sha> # v1.0.0
```

Enable Pages with "Source: GitHub Actions". Release runs deploy from their
tag, so allow the release tags in the `github-pages` environment:

```sh
gh api -X POST -f name='*' -f type=tag \
  repos/{owner}/{repo}/environments/github-pages/deployment-branch-policies
```

### Renovate

`.github/renovate.json5` in the app, with non-major updates merged once CI
passes:

```json5
{
  $schema: 'https://docs.renovatebot.com/renovate-schema.json',
  extends: ['config:recommended'],
  packageRules: [
    {
      // Without bumped ranges, pub only gets updates outside pubspec.yaml's ranges.
      matchManagers: ['pub'],
      matchDepTypes: ['dependencies', 'dev_dependencies'],
      rangeStrategy: 'bump',
    },
    {
      matchUpdateTypes: ['minor', 'patch', 'pin', 'digest'],
      automerge: true,
    },
  ],
}
```

Renovate counts a skipped check as passed, so a CI caller that skips private
repositories must still run on Renovate's pull requests
(`github.event.pull_request.user.login == 'renovate[bot]'`).

## Task contract

The workflows run these `mise` tasks of the app (in the repository root's
`mise.toml`; set `[task_config] dir` to the app directory):

| Task | Used by | Must |
| --- | --- | --- |
| `check` | CI | Lint and test the app (and scan for secrets, if wanted: the history is fetched in full) |
| `codegen` | CI, `codegen: true` | Regenerate committed generated code |
| `build:apk` | CI, release | Build per-ABI release APKs (`flutter build apk --split-per-abi`). Add `-P force-version-code-ignoring-abi=true` so every APK gets the build number as its version code, like the App Bundle; otherwise an APK from a GitHub release blocks later Play updates |
| `build:appbundle` | release | Build the release App Bundle |
| `build:web` | CI, release | Build the web app and accept `--channel beta\|prod`; `prod` removes the BETA badge (below) |
| `build:ios` | CI, `ios: true` | Build the iOS app without code signing |

Also:

- **Version:** the release workflow writes `version: X.Y.Z+B` from the tag into
  `pubspec.yaml` before it builds, so the build tasks take the version from
  there (Flutter's default).
- **Tools and outputs:** Flutter is the mise tool `flutter` (the iOS jobs
  install only it). The builds use Flutter's default output paths
  (`build/app/outputs/flutter-apk/app-<abi>-release.apk`,
  `build/app/outputs/bundle/release/app-release.aab`, `build/web`), and the
  web build keeps `<base href="/">`, which Pages points at the site's paths.
- **Android signing:** the release workflow writes the upload keystore to
  `android-keystore-path` and sets `KEY_ALIAS`, `KEY_PASSWORD` and
  `STORE_PASSWORD`. The app's Gradle release signing reads them; without them
  (CI, local builds) it should fall back to the debug key.
- **iOS:** an Xcode project with the `Runner` scheme, workspace and app, as
  `flutter create` makes it. The release archive is signed ad hoc with
  `APPLE_TEAM_ID` and exported with Apple's cloud signing. If
  `ios/Runner/*.entitlements` declares push notifications (`aps-environment`),
  the workflow fails when the archive or the App Store build lacks them.
- **BETA badge:** optional. Wrap it in `web/index.html` as

  ```html
  <!-- beta-badge -->
  <p>BETA</p>
  <!-- /beta-badge -->
  ```

  so `build:web --channel prod` and the Pages site root can remove it.

## Store setup

### Release setup with Terraform

[`terraform/apps`](terraform/apps/main.tf) describes the store release setup
of every app with the [`terraform/app-release`](terraform/app-release) module:
a Google Cloud project in the organization's `releases` folder with keyless
Google Play sign-in, the service account's release access to the app in Play
Console, the app repository's release environment (required reviewers,
release tags allowed) and the repository variables the workflow reads. Secrets
stay out of Terraform; set them with `gh secret set`.

To add an app, add a module block to `terraform/apps/main.tf` and merge it to
`main`. The [Terraform workflow](.github/workflows/terraform.yml) plans the
change and, once a reviewer of the `terraform` environment has approved the
plan job and then the apply job, applies it. Settings that already exist get
an `import` block, as the ones set by hand before.

Pass `environment: release` to `flutter-release.yml` when the app has the
release environment: its Google provider then only accepts tokens of this
workflow's jobs in that environment. GitHub Free has no environments in
private repositories; set `environment = null` until the repository is public
and leave `environment` out of the workflow.

#### Terraform in CI

The workflow signs in to Google Cloud as the Terraform service account
(Workload Identity Federation, only from `main` in the `terraform`
environment) and to GitHub as the organization's Terraform GitHub App, and
keeps the state in a Cloud Storage bucket. An administrator sets this up once:

1. Create the Google Cloud organization: sign up for Cloud Identity Free with
   the `silver.sggw.pl` domain and verify it with the DNS TXT record it shows.
   Link a billing account to it (the state bucket needs one; it stays in the
   always-free tier).
2. Create the GitHub App in the organization's settings (Developer settings →
   GitHub Apps): no webhook, repository permissions Administration,
   Environments, Secrets and Variables (read and write) and Metadata (read).
   Install it on the app repositories and generate a private key.
3. Apply [`terraform/bootstrap`](terraform/bootstrap/main.tf) with your own
   accounts. It creates the `silvernet-infra` project with the state bucket,
   the Terraform service account and its sign-in, its rights on the
   `releases` folder and the Play Console admin permission, and the
   `terraform` environment with its variables:

   ```sh
   gcloud auth application-default login --scopes=openid,\
   https://www.googleapis.com/auth/userinfo.email,\
   https://www.googleapis.com/auth/cloud-platform,\
   https://www.googleapis.com/auth/androidpublisher
   export GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json
   export GITHUB_TOKEN=$(gh auth token)
   cd terraform/bootstrap
   terraform init
   terraform apply -var org_id=ORG_ID -var billing_account=BILLING_ACCOUNT \
     -var github_app_id=APP_ID -var github_app_installation_id=INSTALLATION_ID
   gh secret set TF_GITHUB_APP_PRIVATE_KEY --repo SilverNETGroupSGGW/Infra \
     --env terraform < private-key.pem
   ```

   If the Play Console call fails because the API is not enabled, run
   `gcloud auth application-default set-quota-project silvernet-infra` and
   apply again. Keep the bootstrap state (`terraform.tfstate`) private.
4. Run the Terraform workflow (Actions → Terraform → Run workflow).

### Google Play

The App Bundle is signed with the app's upload key (the `ANDROID_*` secrets).
No Google key is stored: the workflow signs in with Workload Identity
Federation, set up by `terraform/apps` together with the service account's
Play Console access ("Release to testing tracks" and "Release to
production, exclude devices, and use Play App Signing" for this app only).
Store the upload key as secrets of the release environment (without
`--env release` while the app has no environment):

```sh
base64 -w0 key.jks | gh secret set ANDROID_KEYSTORE_BASE64 --env release
gh secret set ANDROID_KEY_ALIAS --env release
gh secret set ANDROID_KEY_PASSWORD --env release
gh secret set ANDROID_STORE_PASSWORD --env release
```

Betas go to the beta (open testing) track and releases to production, all at
once. Open testing needs its countries or regions chosen once in Play Console
(Test and release → Open testing). A staged rollout started in Play Console
stops the job until it is finished or halted. With managed publishing on,
approved changes still wait for "Publish changes" in Play Console.

### App Store Connect

The workflow archives the app on a GitHub macOS runner and has Apple sign it
for distribution (cloud signing), so no certificate is stored. macOS minutes
count ten times against the Actions minutes of a private repository.

1. In App Store Connect, Users and Access → Integrations → App Store Connect
   API, create a team key with the Admin role (cloud signing needs it; the
   Account Holder enables API access once). Download the `.p8` file; it can
   only be downloaded once. Every team key reaches all apps of the team; a key
   per app can still be revoked on its own. This step stays manual: the App
   Store Connect API cannot create API keys.
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

Betas go to TestFlight's public testers: the job adds the build to an external
group with a public link (a new "Public beta" group if there is none), turns on
"Automatically notify testers", sends it to Beta App Review and puts the public
link in the run summary; testers get the build once Apple approves it.
TestFlight test information that is still empty (Beta App Review contact, beta
description, feedback e-mail) is filled from the App Store review details and
description, and the release description becomes "What to Test". Releases and
promotions are submitted for App Review with the release description as "What's
New" in every language, and released once approved. A released version accepts
no new builds, so the beta after a release needs a higher version name. Apple
reviews one build per version at a time: a beta published while the previous
beta of its version is still in Beta App Review fails the App Store Connect
job; re-run the job once that review is done.

## Development

```sh
mise install
mise run lint  # actionlint, zizmor, taplo, rumdl, gitleaks
mise run test  # the store scripts against local mocks of the store APIs
```

- `scripts/` holds the store clients (Python standard library only). The
  release workflow checks them out at the commit the caller pinned
  (`job.workflow_sha`). `tests/` runs them against local mocks: the App Store
  Connect mock checks every request against a trimmed copy of Apple's OpenAPI
  description, the Google Play mock checks track updates against Google's
  `Track` schema and the other requests with explicit assertions.
- actionlint 1.7.12 does not know some newer workflow syntax yet;
  [.github/actionlint.yaml](.github/actionlint.yaml) lists the ignored
  messages.
- Test changes to the workflows in an app before tagging a release here. The
  store jobs can only be tested with real store accounts: publish a beta of an
  app first.
