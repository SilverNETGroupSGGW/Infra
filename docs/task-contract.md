# Task contract

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
  The release workflow's Android and web jobs install only the `build-tools`
  mise tools (default `flutter,java`; CI installs all of them), so
  `build:apk`, `build:appbundle` and `build:web` must work with only those.
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
