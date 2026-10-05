"""Publishes a release to Google Play with the Play Developer API.

Run by .github/workflows/flutter-release.yml. MODE=build uploads the App Bundle
BUNDLE_PATH to the beta (open testing) track for CHANNEL=beta or to production
for CHANNEL=prod; MODE=promote releases the version code that the beta uploaded
to production without uploading again, also when a later beta has replaced it
on the beta track.

Environment: ACCESS_TOKEN (OAuth token with the androidpublisher scope),
PACKAGE_NAME (the Android application ID), MODE, CHANNEL, TAG, VERSION_CODE,
BUNDLE_PATH (for MODE=build), NOTES_LANGUAGE, and GH_TOKEN for reading the
release notes from the GitHub release. Uses only the Python standard library.
"""

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.environ.get("PLAY_API", "https://androidpublisher.googleapis.com")
PACKAGE = os.environ["PACKAGE_NAME"]
API = f"{ROOT}/androidpublisher/v3/applications/{PACKAGE}"
UPLOAD_API = f"{ROOT}/upload/androidpublisher/v3/applications/{PACKAGE}"
# Play shows at most 500 characters of release notes per language.
NOTES_LIMIT = 500


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def call(method, url, body=None, data=None, content_type="application/json"):
    if body is not None:
        data = json.dumps(body).encode()
    headers = {"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]}
    if data is not None or method == "POST":
        headers["Content-Type"] = content_type
        data = data if data is not None else b""
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise ApiError(error.code, error.read().decode(errors="replace")) from None
    return json.loads(raw) if raw else None


def release_notes(tag):
    """The GitHub release description, shortened to what Play accepts."""
    notes = subprocess.run(
        ["gh", "release", "view", tag, "--repo", os.environ["GITHUB_REPOSITORY"],
         "--json", "body", "--jq", ".body"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if len(notes) > NOTES_LIMIT:
        notes = notes[: NOTES_LIMIT - 1].rstrip() + "…"
    return notes


def check_no_staged_rollout(edit, track):
    """Refuses to replace a running staged rollout, which a person decides on."""
    current = call("GET", f"{API}/edits/{edit}/tracks/{track}")
    for release in current.get("releases", []):
        if release.get("status") == "inProgress":
            sys.exit(
                f"The {track} track has a staged rollout ({release.get('name')}, "
                f"{release['status']}); finish or halt it in Play Console first."
            )


def commit(edit):
    url = f"{API}/edits/{edit}:commit?changesInReviewBehavior=ERROR_IF_IN_REVIEW"
    try:
        call("POST", url)
    except ApiError as error:
        if "changesNotSentForReview to true" not in error.body:
            raise
        # The app needs its changes sent for review by hand in Play Console.
        call("POST", url + "&changesNotSentForReview=true")
        print("::notice::Send the changes for review in Play Console.")


def uploaded_bundles(edit):
    """The App Bundles of the app, by version code."""
    listed = call("GET", f"{API}/edits/{edit}/bundles").get("bundles", [])
    return {str(bundle["versionCode"]): bundle for bundle in listed}


def upload(edit, version_code):
    with open(os.environ["BUNDLE_PATH"], "rb") as bundle_file:
        data = bundle_file.read()
    earlier = uploaded_bundles(edit).get(version_code)
    if earlier is not None:
        if earlier.get("sha256") != hashlib.sha256(data).hexdigest():
            sys.exit(
                f"Version code {version_code} was already uploaded with another build. "
                "Promote the beta (untick 'pre-release') or tag a new version."
            )
        # A re-run of this release: the same bundle is already in Play.
        print(f"Version code {version_code} is already uploaded.")
        return
    bundle = call(
        "POST",
        f"{UPLOAD_API}/edits/{edit}/bundles?uploadType=media",
        data=data,
        content_type="application/octet-stream",
    )
    if str(bundle["versionCode"]) != version_code:
        sys.exit(
            f"Uploaded version code {bundle['versionCode']}, expected {version_code}."
        )


def main():
    mode = os.environ["MODE"]
    tag = os.environ["TAG"]
    version_code = os.environ["VERSION_CODE"]
    notes = release_notes(tag)
    edit = call("POST", f"{API}/edits", body={})["id"]

    if mode == "build":
        track = "beta" if os.environ["CHANNEL"] == "beta" else "production"
        upload(edit, version_code)
        release = {"name": tag, "versionCodes": [version_code], "status": "completed"}
    elif mode == "promote":
        track = "production"
        beta = call("GET", f"{API}/edits/{edit}/tracks/beta")
        matching = [
            release for release in beta.get("releases", [])
            if version_code in release.get("versionCodes", [])
        ]
        # A later beta may have replaced this one on the beta track; its bundle
        # stays in Play and can still be released.
        if not matching and version_code not in uploaded_bundles(edit):
            sys.exit(
                f"Version code {version_code} is not in Google Play: only a beta "
                "that the release workflow uploaded can be promoted."
            )
        release = {
            "name": tag.split("-")[0],
            "versionCodes": [version_code],
            "status": "completed",
        }
        if not notes and matching and matching[0].get("releaseNotes"):
            release["releaseNotes"] = matching[0]["releaseNotes"]
    else:
        sys.exit(f"Unknown mode {mode}.")

    if notes:
        release["releaseNotes"] = [
            {"language": os.environ["NOTES_LANGUAGE"], "text": notes}
        ]
    check_no_staged_rollout(edit, track)
    call(
        "PUT",
        f"{API}/edits/{edit}/tracks/{track}",
        body={"track": track, "releases": [release]},
    )
    commit(edit)
    print(f"{tag}: version code {version_code} released to the {track} track.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(f"{' '.join(error.cmd)} failed:\n{error.stderr}")
    except ApiError as error:
        if "version code that has already been used" in error.body:
            sys.exit(
                f"{error}\nThis build number was already uploaded. Promote the beta "
                "(untick 'pre-release') or tag a new version."
            )
        sys.exit(str(error))
