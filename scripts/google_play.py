"""Publishes a release to Google Play with the Play Developer API.
Run by flutter-release.yml with its settings in environment variables."""

import hashlib
import http.client
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.environ.get("PLAY_API", "https://androidpublisher.googleapis.com")
PACKAGE = os.environ["PACKAGE_NAME"]
API = f"{ROOT}/androidpublisher/v3/applications/{PACKAGE}"
UPLOAD_API = f"{ROOT}/upload/androidpublisher/v3/applications/{PACKAGE}"
# Play shows at most 500 characters of release notes per language.
NOTES_LIMIT = 500
TRACK = os.environ["TRACK"]
BETA_TRACK = os.environ["BETA_TRACK"]


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.body = body


def call(method, url, body=None, data=None, content_type="application/json", retries=4):
    if body is not None:
        data = json.dumps(body).encode()
    headers = {"Authorization": "Bearer " + os.environ["ACCESS_TOKEN"]}
    if data is not None or method == "POST":
        headers["Content-Type"] = content_type
        data = data if data is not None else b""
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                raw = response.read()
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            text = error.read().decode(errors="replace")
            # The edits insert and the track PUT are safe to send again; the
            # bundle upload and the commit pass retries=0.
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(60, 5 * 2**attempt))
                continue
            raise ApiError(error.code, text) from None
        except (OSError, http.client.HTTPException):
            # URLError, timeouts, resets and broken responses; only reads are safe to send again.
            if method != "GET" or attempt == retries:
                raise
            time.sleep(min(60, 5 * 2**attempt))


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


def check_track(edit, track, version_code):
    """Refuses to replace a running staged rollout, which a person decides on,
    or a newer release outside beta."""
    current = call("GET", f"{API}/edits/{edit}/tracks/{track}")
    for release in current.get("releases", []):
        if release.get("status") == "inProgress":
            sys.exit(
                f"The {track} track has a staged rollout ({release.get('name')}, "
                f"{release['status']}); finish or halt it in Play Console first."
            )
        # A lower beta code is a hotfix pre-release; testers get the highest code anyway.
        if track == BETA_TRACK:
            continue
        higher = [code for code in release.get("versionCodes", [])
                  if int(code) > int(version_code)]
        if higher:
            sys.exit(
                f"The {track} track already has version code {higher[0]} "
                f"({release.get('name')}), higher than {version_code}; not "
                "replacing it with an older build."
            )


def commit(edit):
    # Changes already in review (such as a policy declaration) are cancelled
    # and sent again together with this release.
    url = f"{API}/edits/{edit}:commit"
    try:
        call("POST", url, retries=0)
    except ApiError as error:
        if "changesNotSentForReview to true" not in error.body:
            raise
        # This app's changes must be sent for review by hand in Play Console.
        call("POST", url + "?changesNotSentForReview=true", retries=0)
        print("::notice::Send the changes for review in Play Console.")


def uploaded_bundles(edit):
    """The app's App Bundles by version code."""
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
        retries=0,  # a hidden success would make the retry fail as a used version code
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

    track = TRACK
    if mode == "build":
        upload(edit, version_code)
        release = {"name": tag, "versionCodes": [version_code], "status": "completed"}
    elif mode == "promote":
        beta = call("GET", f"{API}/edits/{edit}/tracks/{BETA_TRACK}")
        matching = [
            release for release in beta.get("releases", [])
            if version_code in release.get("versionCodes", [])
        ]
        # A later beta may have replaced this one on the beta track; its bundle
        # stays in Play and can still be released.
        if not matching and version_code not in uploaded_bundles(edit):
            sys.exit(
                f"Version code {version_code} is not in Google Play: only a build "
                "that the release workflow uploaded can be published again."
            )
        release = {
            "name": tag.split("-")[0] if track == "production" else tag,
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
    check_track(edit, track, version_code)
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
    except (urllib.error.URLError, ConnectionError, TimeoutError, ssl.SSLError,
            http.client.HTTPException) as error:
        sys.exit(f"Network error: {error}")
