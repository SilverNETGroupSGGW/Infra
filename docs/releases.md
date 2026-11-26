# Releases

How an app calls [`flutter-release.yml`](../.github/workflows/flutter-release.yml)
and, to redeploy Pages by hand,
[`flutter-pages.yml`](../.github/workflows/flutter-pages.yml).

`.github/workflows/release.yml` in the app (Infra's App Store publisher only
takes requests from a workflow with this file name):

```yaml
name: Release
run-name: >-
  ${{ github.event_name == 'workflow_dispatch'
  && format('Publish {0} to {1} ({2})', github.ref_name, inputs.track, inputs.platforms)
  || format('Release {0} ({1})', github.ref_name, github.event.action) }}

on:
  release:
    # published: build a pre-release; released: build a release, or promote a
    # pre-release that was turned into one.
    types: [published, released]
  # Publishes a release's store builds again without building; run it from the
  # release's tag.
  workflow_dispatch:
    inputs:
      track:
        description: Store track; beta is open testing
        type: choice
        options: [beta, production]
      platforms:
        type: choice
        options: [all, android, ios]

permissions: {}

# Nothing from the Actions cache reaches a signed build.
cache-mode: none

concurrency:
  group: release-${{ github.ref_name }}
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
    uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-release.yml@<commit-sha> # v2.0.0
    # Store IDs come from apps.yaml in Infra
    with:
      app-dir: app
      pages: true
      publish-track: ${{ inputs.track }}
      publish-platforms: ${{ inputs.platforms }}
    secrets:
      ANDROID_KEYSTORE_BASE64: ${{ secrets.ANDROID_KEYSTORE_BASE64 }}
      ANDROID_KEY_ALIAS: ${{ secrets.ANDROID_KEY_ALIAS }}
      ANDROID_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}
      ANDROID_STORE_PASSWORD: ${{ secrets.ANDROID_STORE_PASSWORD }}
```

Pass the secrets one by one rather than with `secrets: inherit`, so the
workflow only gets what it uses.

| Input | Default | |
| --- | --- | --- |
| `app-dir` | `.` | Directory of the Flutter app |
| `artifact-prefix` | repository name | File name prefix of the release assets: `<prefix>-<tag>.aab`, `<prefix>-<tag>-web.zip`, ... |
| `android-keystore-path` | `android/app/key.jks` | Where the build reads the upload keystore, relative to `app-dir` |
| `play-beta-track` | `beta` | API name of the open testing track; Google names it `beta`, and custom names such as `openBeta` are closed testing tracks |
| `publish-track`, `publish-platforms` | | Manual runs: where to publish the release's builds (below) |
| `play-notes-language` | `en-US` | Language of the Google Play release notes; one of the store listing's languages |
| `pages` | `false` | Deploy the web builds to GitHub Pages once Pages is enabled for the repository |
| `environment` | | Environment of the jobs that use signing or store credentials, e.g. one with required reviewers |
| `build-tools` | `flutter,java` | mise tools the Android and web builds install |
| `macos-runner`, `xcode-version`, `mise-version` | `macos-26`, `26.6`, newest release | Runner and Xcode of the iOS archive only; Infra picks its own for the upload |

The workflow reads one repository (or organization) variable of the app:
`APP_STORE_USES_NON_EXEMPT_ENCRYPTION`, the optional export compliance answer,
`false` or `true` ([setup](stores.md#app-store-connect)).

The store IDs come from the app's entry in [`apps.yaml`](apps.md), read at
the Infra commit the caller pins; the run looks the app up by its repository
ID. Google Play runs when that entry has `play`, the iOS archive and the App
Store request when it has `app_store`. An app without an entry still gets its
builds attached to the release, but no store publishing and a warning: add
the entry in Infra and pin a newer commit. Missing signing secrets fail the
run before anything is built; with `environment`, the jobs that use the
secrets check them.

## How a release flows

- **Version:** the tag is the version: `X.Y.Z`, or `X.Y.Z-<label>.N` such as
  `1.2.0-beta.3` or `1.2.0-rc2`, with or without a leading `v`. The label is
  free text; the tag never picks the testers. The builds get `X.Y.Z` as their
  version name and the build number (Android `versionCode`, iOS
  `CFBundleVersion`) `X·1000000 + Y·10000 + Z·100 + N`, where a release counts
  as `N` = 99: every new version or pre-release gets a higher number, and a
  release sorts after its pre-releases. `X` goes up to 2099, `Y` and `Z` up to
  99 and `N` from 1 to 98, and the pre-releases of a version need different
  numbers across all labels (`beta.1`, `beta.2`, `rc.3`); the run checks the
  version's other tags before building, and refuses a pre-release once the
  version has a tag without a label (`1.2.0`). The version in `pubspec.yaml` is
  only used by local builds.
- **Beta:** publish a GitHub release with "Set as a pre-release" ticked. The
  workflow builds signed per-ABI APKs, an App Bundle and the web app and
  attaches them to the release. Google Play gets the bundle on the open
  testing track, TestFlight gets the iOS build in a group with a public link
  and sends it to Beta App Review (the link is in the summary of Infra's App
  Store run), and Pages serves it under `/beta/`. A Play tester gets the
  highest build number among the tracks they joined, production included.
- **Production:** publish a release without ticking "Set as a pre-release".
  Google Play gets it on the production track, App Review gets the iOS build
  (released once approved), and Pages serves it at the site root. An earlier
  version still waiting for App Review is withdrawn and replaced, as Google
  Play does; one that App Review has started stops the run. A production
  release older than the version a store already has stops that store's job.
- **iOS:** a build archives the app, signed ad hoc. Each run that publishes
  to iOS leaves an `app-store-request` artifact: build or promote, tag,
  version and build number. Infra's
  [`app-store.yml`](../.github/workflows/app-store.yml) looks for requests
  every 10 minutes, so its run starts 10 to 15 minutes later. It takes the
  bundle and Apple ID from `apps.yaml` on Infra `main` and checks that the
  archive is that bundle at that version. Then it signs the archive for
  distribution, uploads it and does the TestFlight or App Review step. The
  app's "App Store (Infra)" job links that run in its summary and ends with
  its result. An Infra admin re-runs a failed Infra run; "Re-run failed jobs"
  in the app then picks up the new result. The finder only looks at release
  runs from the last three days: a request from an older run, for example
  one re-run later, is never found, and the app's job fails after 45 minutes.
  An Infra admin then runs App Store by hand (Actions → App Store → Run
  workflow) with the app's repository and the run's ID.
- **Closed and internal tests** are not automated: upload the builds attached
  to a release in Play Console and App Store Connect.
- **Promotion:** untick "Set as a pre-release" on a tested pre-release. The
  same Play build moves to production, the same TestFlight build goes to App
  Review, and Pages serves it at the root without the BETA badge; nothing is
  rebuilt. The store jobs check again that the release is still a release
  before changing anything. The release's title and build files lose the
  pre-release label (`App 1.2.0 beta 3` becomes `App 1.2.0`); the tag keeps it.
- **Publishing again:** run the release workflow by hand from the release's
  tag ("Use workflow from", or
  `gh workflow run release.yml --ref v1.2.0-beta.3 -f track=production`). The
  builds that the release's run uploaded go to `production` (App Review on
  iOS) or the open testing track (the public TestFlight beta) without being
  rebuilt. `platforms` limits it to `android` or `ios`. Pages is not changed.
- **Release notes:** the release description becomes the store release notes
  (Google Play: up to 500 characters; App Store: "What's New" in every
  language).
- **Re-runs:** "Re-run failed jobs" continues where a run stopped: an uploaded
  bundle is not uploaded again. Re-running Infra's run completes an
  unfinished App Store submission (above). "Re-run all jobs" on a pre-release
  whose builds are attached stops at once, since the stores refuse a build
  number twice.

Don't enable immutable releases in the app repositories: the builds are
attached after the release is published.

## Security

- `cache-mode: none`: nothing from the Actions cache, which other workflows
  can write, reaches a signed build.
- Only the `publish` and `rename` jobs can write to the repository, and they
  run no build code. The upload key is only in the Android job.
- The App Store Connect key never reaches an app repository: only Infra's
  `app-store` environment holds it. Infra takes the bundle and Apple ID from
  `apps.yaml` on its `main` branch, never from the request, and checks that
  the request comes from the app's own `release.yml` run on the release's
  tag, at the tag's commit.
- Google Play uses Workload Identity Federation: no Google key is stored. One
  shared provider accepts only tokens from this workflow in the organization's
  tag runs, and each repository can use only its own account,
  `app-<repository ID>@silvernet-infra.iam.gserviceaccount.com`, which Play
  lets release only the package of its `apps.yaml` entry. The `apps.yaml`
  lookup enforces none of this: a caller that pins another Infra commit still
  gets only its own account.
- Set `environment` to an environment with required reviewers to approve
  releases: the Android, Google Play and App Store request jobs each wait for
  an approval when they start. Release runs deploy from their tag, so allow
  the release tags in the environment's deployment branches and tags (for
  example the tag rule `*`). Environment secrets only reach jobs that name the
  environment, under the names this workflow reads
  (`ANDROID_KEYSTORE_BASE64`, ...), whatever the caller passes. Infra does
  not check the App Store approval: someone who can push a tag and edit
  `release.yml` can skip it for their own app.

## Moving to v2

v2 takes the store IDs from `apps.yaml` and publishes iOS from Infra. In the
app's `release.yml`:

1. Make sure the app has an entry in [`apps.yaml`](apps.md), then pin v2.0.0.
2. Remove the inputs `android-package`, `bundle-id` and `apple-app-id` (the
   call fails on unknown inputs) and the `APP_STORE_CONNECT_KEY` secret.
3. Keystore secrets under older names are mapped in `secrets:`, for example
   `ANDROID_KEYSTORE_BASE64: ${{ secrets.ANDROID_JKS_BASE64 }}`.

The variables `GOOGLE_WORKLOAD_IDENTITY_PROVIDER`,
`GOOGLE_PLAY_SERVICE_ACCOUNT`, `APP_STORE_CONNECT_KEY_ID`,
`APP_STORE_CONNECT_ISSUER_ID` and `APPLE_TEAM_ID` and the
`APP_STORE_CONNECT_KEY` secret are no longer read; delete them once a release
went through. `APP_STORE_USES_NON_EXEMPT_ENCRYPTION` stays.

## Pages

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
    uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-pages.yml@<commit-sha> # v2.0.0
```

Enable Pages with "Source: GitHub Actions". Release runs deploy from their
tag, so allow the release tags in the `github-pages` environment:

```sh
gh api -X POST -f name='*' -f type=tag \
  repos/{owner}/{repo}/environments/github-pages/deployment-branch-policies
```
