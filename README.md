# Infra

Infrastructure of SilverNETGroupSGGW:

- reusable GitHub Actions workflows for the Flutter apps: continuous
  integration, releases to GitHub, Google Play, App Store Connect and GitHub
  Pages ([CI](docs/ci.md), [releases](docs/releases.md));
- the app registry `apps.yaml` and its sync ([Apps](docs/apps.md)).

| Workflow | What it does |
| --- | --- |
| [`flutter-ci.yml`](.github/workflows/flutter-ci.yml) | Lints and tests the app, checks generated code, builds the release APKs and the web app, optionally the iOS app |
| [`flutter-release.yml`](.github/workflows/flutter-release.yml) | Builds a GitHub release, attaches the builds to it and publishes them to Google Play and Pages; hands the iOS archive to `app-store.yml`; promotes a tested beta without rebuilding it |
| [`flutter-pages.yml`](.github/workflows/flutter-pages.yml) | Serves the newest release's web build at the site root and the newest build of any kind, usually a beta, under `/beta/` |
| [`sync-apps.yml`](.github/workflows/sync-apps.yml) | Applies `apps.yaml`: each app's Google Play access and upload key |
| [`app-store.yml`](.github/workflows/app-store.yml) | Every 10 minutes, finds the apps' iOS release requests; checks each against `apps.yaml` on `main`, then signs, uploads and submits it with the club's one App Store Connect key |

iOS builds reach App Store Connect only through `app-store.yml`, so no app
repository holds an App Store Connect key
([Store setup](docs/stores.md#app-store-connect)).

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

1. An admin creates the app in the stores and merges the app's entry in
   `apps.yaml` ([Apps](docs/apps.md)).
2. The lead implements the `mise` tasks the workflows run
   ([task contract](docs/task-contract.md)), adds the caller workflows for
   [CI](docs/ci.md), [releases](docs/releases.md) and, optionally,
   [Pages](docs/releases.md#pages), and adds the
   [Renovate](docs/ci.md#renovate) configuration
   ([Store setup](docs/stores.md) lists what is left in the stores).

## Documentation

| Document | What it covers |
| --- | --- |
| [CI](docs/ci.md) | The CI caller workflow, its inputs, job names for branch protection, Renovate |
| [Releases](docs/releases.md) | The release caller workflow, its inputs and variables, how a release flows, security, Pages |
| [Task contract](docs/task-contract.md) | The `mise` tasks each app implements |
| [Apps](docs/apps.md) | The app registry `apps.yaml`, what its sync sets up and its one-time setup |
| [Store setup](docs/stores.md) | Per-app Google Play and App Store Connect setup, and the App Store publisher's one-time setup |
| [Trimmed API descriptions](tests/specs/README.md) | The store API specs the test mocks check against |

## Development

```sh
mise install
mise run lint  # actionlint, zizmor, taplo, rumdl, apps.yaml, gitleaks
mise run test  # the store scripts against local mocks of the store APIs
```

- `scripts/` holds the store clients (Python standard library only). The
  release workflow checks them out at the commit the caller pinned
  (`job.workflow_sha`); `app-store.yml` and `sync-apps.yml` run them from
  `main`. `tests/` runs them against local mocks: the App Store Connect mock
  checks every request against a trimmed copy of Apple's OpenAPI description,
  the Google Play mock checks track updates against Google's `Track` schema
  and the other requests with explicit assertions.
- actionlint 1.7.12 does not know some newer workflow syntax yet;
  [.github/actionlint.yaml](.github/actionlint.yaml) lists the ignored
  messages.
- Test changes to the workflows in an app before tagging a release here. The
  store jobs can only be tested with real store accounts: publish a beta of an
  app first.
