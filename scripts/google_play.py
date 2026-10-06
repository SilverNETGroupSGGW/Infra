"""Publishes a release to Google Play with the Play Developer API.
Run by flutter-release.yml with its settings in environment variables."""

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = os.environ.get("PLAY_API", "https://androidpublisher.googleapis.com")
PACKAGE = os.environ["PACKAGE_NAME"]
API = f"{ROOT}/androidpublisher/v3/applications/{PACKAGE}"
UPLOAD_API = f"{ROOT}/upload/androidpublisher/v3/applications/{PACKAGE}"
# Play shows at most 500 characters of release notes per language.
NOTES_LIMIT = 500
TRACK = os.environ["TRACK"]
BETA_TRACK = os.environ["BETA_TRACK"]
# Screenshot folders (SCREENSHOTS_DIR/<device>/<language>/*.png) by image type.
SCREENSHOT_TYPES = {
    "phone": "phoneScreenshots",
    "tablet-7": "sevenInchScreenshots",
    "tablet-10": "tenInchScreenshots",
}


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
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
    # Changes already in review (such as a policy declaration) are cancelled
    # and sent again together with this release.
    url = f"{API}/edits/{edit}:commit"
    try:
        call("POST", url)
    except ApiError as error:
        if "changesNotSentForReview to true" not in error.body:
            raise
        # This app's changes must be sent for review by hand in Play Console.
        call("POST", url + "?changesNotSentForReview=true")
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
    )
    if str(bundle["versionCode"]) != version_code:
        sys.exit(
            f"Uploaded version code {bundle['versionCode']}, expected {version_code}."
        )


def screenshot_folder(device, language):
    """The folder for a listing language: en-US, else en; None if neither."""
    root = Path(os.environ["SCREENSHOTS_DIR"]) / device
    for name in (language, language.split("-")[0]):
        if (root / name).is_dir():
            return root / name
    return None


def update_screenshots(edit):
    """Replaces the listings' screenshots that differ from SCREENSHOTS_DIR;
    returns how many image sets it replaced."""
    replaced = 0
    listings = call("GET", f"{API}/edits/{edit}/listings").get("listings", [])
    for language in sorted(listing["language"] for listing in listings):
        for device, image_type in SCREENSHOT_TYPES.items():
            folder = screenshot_folder(device, language)
            files = sorted(folder.glob("*.png")) if folder else []
            if not files:
                continue
            images = [file.read_bytes() for file in files]
            url = f"{API}/edits/{edit}/listings/{language}/{image_type}"
            current = call("GET", url).get("images", [])
            if [image.get("sha256") for image in current] == [
                hashlib.sha256(image).hexdigest() for image in images
            ]:
                continue
            call("DELETE", url)
            for image in images:
                call("POST", f"{UPLOAD_API}/edits/{edit}/listings/{language}/"
                     f"{image_type}?uploadType=media", data=image,
                     content_type="image/png")
            print(f"Replaced the {language} {image_type} with {folder}.")
            replaced += 1
    return replaced


def main():
    mode = os.environ["MODE"]
    tag = os.environ["TAG"]
    edit = call("POST", f"{API}/edits", body={})["id"]
    if mode == "listing":
        # The store listing alone, sent for review if anything changed.
        if not os.environ.get("SCREENSHOTS_DIR"):
            sys.exit("No screenshots to publish: set screenshots-task.")
        if update_screenshots(edit):
            commit(edit)
        else:
            call("DELETE", f"{API}/edits/{edit}")
            print("The store screenshots are up to date.")
        return
    version_code = os.environ["VERSION_CODE"]
    notes = release_notes(tag)

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
    check_no_staged_rollout(edit, track)
    # The listing goes with production; testers see the same one.
    if track == "production" and os.environ.get("SCREENSHOTS_DIR"):
        update_screenshots(edit)
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
