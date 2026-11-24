# Infra

Infrastructure of SilverNETGroupSGGW:

- reusable GitHub Actions workflows for the Flutter apps: continuous
  integration, releases to GitHub, Google Play, App Store Connect and GitHub
  Pages ([CI](docs/ci.md), [releases](docs/releases.md));
- the Terraform setup of the apps' store releases
  ([Release setup with Terraform](docs/terraform.md)).

| Workflow | What it does |
| --- | --- |
| [`flutter-ci.yml`](.github/workflows/flutter-ci.yml) | Lints and tests the app, checks generated code, builds the release APKs and the web app, optionally the iOS app |
| [`flutter-release.yml`](.github/workflows/flutter-release.yml) | Builds a GitHub release, attaches the builds to it and publishes them to the stores and Pages; promotes a tested beta without rebuilding it |
| [`flutter-pages.yml`](.github/workflows/flutter-pages.yml) | Serves the newest release's web build at the site root and the newest build of any kind, usually a beta, under `/beta/` |

Each app keeps short caller workflows and implements a few `mise` tasks (the
[task contract](docs/task-contract.md)); everything else lives here, so a fix
or a new store requirement is made once.

## Using the workflows

Reference the workflows by commit SHA, with the version in a comment, and let
Renovate update the SHA (`config:recommended` does):

```yaml
uses: SilverNETGroupSGGW/Infra/.github/workflows/flutter-ci.yml@<commit-sha> # v1.0.0
```

Tags follow semantic versioning. A major version changes inputs, secrets,
variables or the task contract incompatibly; the release notes say how to
update.

## Add a new app

1. Add the app's project to `terraform/bootstrap` and apply it. For an iOS
   app, create its App Store Connect key first
   ([App Store Connect](docs/stores.md#app-store-connect), step 1). Then add a
   module block to `terraform/apps/main.tf` with the key ID, issuer ID and
   team ID, and merge it
   ([Release setup with Terraform](docs/terraform.md)).
2. Invite the app's release service account in Play Console, and store the
   upload key and the App Store Connect key as secrets of the app's release
   environment (of the repository while it has none)
   ([Google Play](docs/stores.md#google-play),
   [App Store Connect](docs/stores.md#app-store-connect)).
3. Implement the `mise` tasks the workflows run
   ([task contract](docs/task-contract.md)).
4. Add the caller workflows for [CI](docs/ci.md), [releases](docs/releases.md)
   and, optionally, [Pages](docs/releases.md#pages), and add the
   [Renovate](docs/ci.md#renovate) configuration.

## Documentation

| Document | What it covers |
| --- | --- |
| [CI](docs/ci.md) | The CI caller workflow, its inputs, job names for branch protection, Renovate |
| [Releases](docs/releases.md) | The release caller workflow, its inputs and variables, how a release flows, security, Pages |
| [Task contract](docs/task-contract.md) | The `mise` tasks each app implements |
| [Store setup](docs/stores.md) | Per-app Google Play and App Store Connect setup |
| [Release setup with Terraform](docs/terraform.md) | The Terraform setup of the apps, Terraform in CI and its one-time bootstrap |
| [Trimmed API descriptions](tests/specs/README.md) | The store API specs the test mocks check against |

## Development

```sh
mise install
mise run lint  # actionlint, zizmor, taplo, rumdl, terraform, gitleaks
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
