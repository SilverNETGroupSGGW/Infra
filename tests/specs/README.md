# Trimmed API descriptions

The test mocks check the store scripts' requests against these files.

- `androidpublisher-v3.json`: the `Track` schema of the Google Play Developer
  API and the schemas it references (for the track updates; the mock checks the
  other requests with explicit assertions), and the `User` and `Grant` schemas
  (for the Play users and grants of `scripts/sync_apps.py`), from the discovery
  document at <https://androidpublisher.googleapis.com/$discovery/rest?version=v3>
  (revision in the file; `User` and `Grant` are from revision 20261125).
- `app-store-connect-api-4.5.json`: the operations that
  `scripts/app_store_submit.py` calls (parameters and request bodies, without
  responses) and the schemas they reference, from the App Store Connect API
  OpenAPI specification 4.5, downloadable from
  <https://developer.apple.com/documentation/appstoreconnectapi>.

When a script calls a new operation, add it from the current specification.
