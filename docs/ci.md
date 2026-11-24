# CI

How an app calls [`flutter-ci.yml`](../.github/workflows/flutter-ci.yml), and
the Renovate setup that keeps the pinned commit SHAs current. Pin the workflows
as [Using the workflows](../README.md#using-the-workflows) describes.

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
    # Optional: skip while the repository is private, so CI costs no minutes;
    # Renovate's pull requests still run, since it merges once checks pass.
    if: ${{ github.event.repository.visibility == 'public' || github.event.pull_request.user.login == 'renovate[bot]' }}
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

## Renovate

`.github/renovate.json5` in the app, with non-major updates merged once CI
passes and the update is three days old:

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
      matchUpdateTypes: ['minor', 'patch', 'digest'],
      minimumReleaseAge: '3 days',
      automerge: true,
    },
  ],
}
```

Renovate counts a skipped check as passed, so the CI caller above runs on its
pull requests even in a private repository.
