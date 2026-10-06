"""Finishes an App Store Connect upload of the app BUNDLE_ID.

Run by .github/workflows/flutter-release.yml after an upload or for a promoted
beta. Finds the build VERSION (BUILD_NUMBER) in App Store Connect, waiting until
a new upload is processed, and answers its export compliance if
USES_NON_EXEMPT_ENCRYPTION is set.

For a public beta (CHANNEL=beta) it then makes the build a public TestFlight
beta: it sets "What to Test" from the GitHub release description, fills in
missing TestFlight test information from the app's App Store details, adds the
build to an external group with a public link (created if needed) and submits
it for Beta App Review. The public link is printed and added to the job
summary. A private test (CHANNEL=alpha) goes the same way to the group
"Private beta", which has no public link. An internal test (CHANNEL=internal)
only gets "What to Test".

For a release or a promotion it attaches the build to the App Store version
VERSION (created if needed), sets "What's New" from the GitHub release
description and submits the version for App Review; it is released
automatically once approved. A re-run continues an unfinished submission.

Environment: KEY_ID, ISSUER_ID and KEY_PATH (the App Store Connect API key),
BUNDLE_ID, MODE (build or promote), CHANNEL, TAG, VERSION, BUILD_NUMBER,
USES_NON_EXEMPT_ENCRYPTION (optional: "false" or "true") and GH_TOKEN. Uses
only the Python standard library and the openssl command.
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

API = os.environ.get("ASC_API", "https://api.appstoreconnect.apple.com")
EDITABLE = {
    "PREPARE_FOR_SUBMISSION",
    "DEVELOPER_REJECTED",
    "REJECTED",
    "METADATA_REJECTED",
    "INVALID_BINARY",
}
SUBMITTED = {
    "WAITING_FOR_REVIEW",
    "IN_REVIEW",
    "ACCEPTED",
    "PENDING_DEVELOPER_RELEASE",
    "PENDING_APPLE_RELEASE",
    "PROCESSING_FOR_DISTRIBUTION",
    "READY_FOR_DISTRIBUTION",
}
# App Store Connect shows at most 4000 characters of "What's New" and "What to
# Test".
NOTES_LIMIT = 4000
# The TestFlight test information that external testing needs: the Beta App
# Review contact and the beta app details in the app's primary language.
CONTACT = ("contactFirstName", "contactLastName", "contactPhone", "contactEmail")
LISTING = ("description", "feedbackEmail")
# Copied with the contact when App Review needs a demo account.
DEMO_ACCOUNT = ("demoAccountRequired", "demoAccountName", "demoAccountPassword")
PUBLIC_GROUP = "Public beta"
# Testers invited by e-mail in App Store Connect.
PRIVATE_GROUP = "Private beta"


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_to_raw(der):
    """Converts an ECDSA signature from DER to the 64-byte r||s form of JWTs."""

    def length(index):
        size = der[index]
        index += 1
        if size < 0x80:
            return size, index
        count = size & 0x7F
        return int.from_bytes(der[index : index + count], "big"), index + count

    _, index = length(1)
    raw = b""
    for _ in range(2):
        assert der[index] == 0x02
        size, index = length(index + 1)
        raw += der[index : index + size].lstrip(b"\x00").rjust(32, b"\x00")
        index += size
    return raw


_token = {"expires": 0, "jwt": ""}


def token():
    """A JWT for the API, renewed shortly before it expires (20 minutes max)."""
    now = int(time.time())
    if _token["expires"] - now > 60:
        return _token["jwt"]
    header = {"alg": "ES256", "kid": os.environ["KEY_ID"], "typ": "JWT"}
    payload = {
        "iss": os.environ["ISSUER_ID"],
        "iat": now,
        "exp": now + 1140,
        "aud": "appstoreconnect-v1",
    }
    signing_input = (
        b64url(json.dumps(header).encode()) + "." + b64url(json.dumps(payload).encode())
    )
    der = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", os.environ["KEY_PATH"]],
        input=signing_input.encode(),
        capture_output=True,
        check=True,
    ).stdout
    _token["jwt"] = signing_input + "." + b64url(der_to_raw(der))
    _token["expires"] = payload["exp"]
    return _token["jwt"]


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def call(method, path, params=None, body=None, retries=4):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + token(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            text = error.read().decode(errors="replace")
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(60, 5 * 2**attempt))
                continue
            raise ApiError(error.code, text) from None


def release_notes(tag):
    notes = subprocess.run(
        ["gh", "release", "view", tag, "--repo", os.environ["GITHUB_REPOSITORY"],
         "--json", "body", "--jq", ".body"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return notes[:NOTES_LIMIT]


def find_build(app_id, version, build_number, uploaded):
    """The processed build; waits up to 80 minutes for a new upload."""
    for attempt in range(160):
        builds = call("GET", "/v1/builds", {
            "filter[app]": app_id,
            "filter[preReleaseVersion.version]": version,
            "filter[version]": build_number,
            "filter[preReleaseVersion.platform]": "IOS",
            "fields[builds]": "version,processingState,expired,"
            "usesNonExemptEncryption,buildAudienceType",
            "limit": "10",
        })["data"]
        if not builds and not uploaded:
            sys.exit(
                f"Build {version} ({build_number}) is not in App Store Connect: "
                "only a beta that the release workflow uploaded can be promoted."
            )
        if not builds and attempt == 40:
            sys.exit(
                f"Build {version} ({build_number}) is not in App Store Connect 20 "
                "minutes after the upload, so the upload most likely failed; check "
                "and re-run the 'Upload to App Store Connect' job."
            )
        if builds:
            build = builds[0]
            state = build["attributes"]["processingState"]
            if state == "VALID":
                if build["attributes"].get("expired"):
                    sys.exit(
                        f"Build {version} ({build_number}) has expired in "
                        "TestFlight (after 90 days) and cannot be submitted; "
                        "publish a new beta."
                    )
                return build
            if state in ("FAILED", "INVALID"):
                sys.exit(f"Build {version} ({build_number}) is {state}.")
        time.sleep(30)
    sys.exit(
        f"Build {version} ({build_number}) was not processed in App Store Connect "
        "in 80 minutes; re-run this job once it is."
    )


def answer_export_compliance(build):
    if build["attributes"].get("usesNonExemptEncryption") is not None:
        return
    answer = os.environ.get("USES_NON_EXEMPT_ENCRYPTION", "")
    if answer in ("false", "true"):
        call("PATCH", f"/v1/builds/{build['id']}", body={"data": {
            "type": "builds",
            "id": build["id"],
            "attributes": {"usesNonExemptEncryption": answer == "true"},
        }})
        return
    sys.exit(
        "The build has no export compliance answer. Answer it in App Store "
        "Connect, add ITSAppUsesNonExemptEncryption to ios/Runner/Info.plist, or "
        "set the APP_STORE_USES_NON_EXEMPT_ENCRYPTION repository variable."
    )


def missing(attributes, names):
    return [name for name in names if not attributes.get(name)]


def localized(localizations, locale, name):
    """The value of name in the locale, else in any localization that has it."""
    having = [item for item in localizations if item.get(name)]
    preferred = [item for item in having if item.get("locale") == locale]
    return (preferred + having)[0][name] if having else None


def related(resource, name, included):
    """The included resources that resource links to as name."""
    data = resource["relationships"][name].get("data") or []
    links = data if isinstance(data, list) else [data]
    keys = [(link["type"], link["id"]) for link in links]
    return [included[key] for key in keys if key in included]


def set_what_to_test(build_id, locale, notes):
    localizations = call("GET", f"/v1/builds/{build_id}/betaBuildLocalizations", {
        "fields[betaBuildLocalizations]": "locale", "limit": "200",
    })["data"]
    for localization in localizations:
        call("PATCH", f"/v1/betaBuildLocalizations/{localization['id']}", body={"data": {
            "type": "betaBuildLocalizations",
            "id": localization["id"],
            "attributes": {"whatsNew": notes},
        }})
    if not localizations:
        call("POST", "/v1/betaBuildLocalizations", body={"data": {
            "type": "betaBuildLocalizations",
            "attributes": {"locale": locale, "whatsNew": notes},
            "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
        }})


def app_store_details(app_id):
    """App Review contact details from the newest version that has them, and the
    App Store descriptions, newest first."""
    response = call("GET", f"/v1/apps/{app_id}/appStoreVersions", {
        "filter[platform]": "IOS",
        "fields[appStoreVersions]":
            "createdDate,appStoreReviewDetail,appStoreVersionLocalizations",
        "fields[appStoreReviewDetails]": ",".join(CONTACT + DEMO_ACCOUNT),
        "fields[appStoreVersionLocalizations]": "locale,description",
        "include": "appStoreReviewDetail,appStoreVersionLocalizations",
        "limit": "200",
        "limit[appStoreVersionLocalizations]": "50",
    })
    included = {(r["type"], r["id"]): r["attributes"] for r in response.get("included", [])}
    versions = sorted(
        response["data"],
        key=lambda v: datetime.fromisoformat(v["attributes"]["createdDate"]),
        reverse=True,
    )
    reviews = [r for v in versions for r in related(v, "appStoreReviewDetail", included)]
    review = next((r for r in reviews if not missing(r, CONTACT)), {})
    descriptions = [
        d for v in versions for d in related(v, "appStoreVersionLocalizations", included)
    ]
    return review, descriptions


def privacy_policy_url(app_id, locale):
    included = call("GET", f"/v1/apps/{app_id}/appInfos", {
        "fields[appInfos]": "appInfoLocalizations",
        "fields[appInfoLocalizations]": "locale,privacyPolicyUrl",
        "include": "appInfoLocalizations",
        "limit[appInfoLocalizations]": "50",
    }).get("included", [])
    return localized([r["attributes"] for r in included], locale, "privacyPolicyUrl")


def fill(resource, values):
    """Sets the resource's empty attributes that values has; returns them all."""
    attributes = resource["attributes"]
    changes = {name: value for name, value in values.items()
               if value and not attributes.get(name)}
    if changes:
        call("PATCH", f"/v1/{resource['type']}/{resource['id']}", body={"data": {
            "type": resource["type"], "id": resource["id"], "attributes": changes,
        }})
    return {**attributes, **changes}


def fill_test_information(app_id, locale):
    """Copies missing TestFlight test information from the App Store details.

    External testing needs a Beta App Review contact and, in the primary
    language, a beta app description and feedback e-mail. Set fields are kept.
    """
    detail = call("GET", f"/v1/apps/{app_id}/betaAppReviewDetail", {
        "fields[betaAppReviewDetails]": ",".join(CONTACT + DEMO_ACCOUNT),
    })["data"]
    localizations = call("GET", f"/v1/apps/{app_id}/betaAppLocalizations", {
        "fields[betaAppLocalizations]": "locale,description,feedbackEmail,privacyPolicyUrl",
        "limit": "200",
    })["data"]
    localization = next(
        (item for item in localizations if item["attributes"]["locale"] == locale), None
    )
    contact = detail["attributes"]
    listing = localization["attributes"] if localization else {}
    if missing(contact, CONTACT) or missing(listing, LISTING):
        review, descriptions = app_store_details(app_id)
        if missing(contact, CONTACT):
            demo = DEMO_ACCOUNT if review.get("demoAccountRequired") else ()
            contact = fill(detail, {name: review.get(name) for name in CONTACT + demo})
        if missing(listing, LISTING):
            values = {
                "description": localized(descriptions, locale, "description"),
                "feedbackEmail": contact.get("contactEmail"),
                "privacyPolicyUrl": listing.get("privacyPolicyUrl")
                or privacy_policy_url(app_id, locale),
            }
            if localization:
                listing = fill(localization, values)
            else:
                listing = {name: value for name, value in values.items() if value}
                call("POST", "/v1/betaAppLocalizations", body={"data": {
                    "type": "betaAppLocalizations",
                    "attributes": {"locale": locale, **listing},
                    "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
                }})
    still_missing = missing(contact, CONTACT) + missing(listing, LISTING)
    if still_missing:
        sys.exit(
            "External TestFlight testing needs test information that the app's App "
            f"Store details do not have ({', '.join(still_missing)}): fill it in "
            "under TestFlight > Test Information in App Store Connect and re-run "
            "this job."
        )


def external_groups(app_id):
    groups = call("GET", f"/v1/apps/{app_id}/betaGroups", {
        "fields[betaGroups]": "name,isInternalGroup,publicLinkEnabled,publicLink",
        "limit": "200",
    })["data"]
    return [g for g in groups if not g["attributes"]["isInternalGroup"]]


def public_group(app_id):
    """The external TestFlight group with a public link, set up if needed."""
    external = external_groups(app_id)
    group = next((g for g in external if g["attributes"]["publicLinkEnabled"]), None)
    if group:
        return group
    group = next((g for g in external if g["attributes"]["name"] == PUBLIC_GROUP), None)
    if group:
        return call("PATCH", f"/v1/betaGroups/{group['id']}", body={"data": {
            "type": "betaGroups",
            "id": group["id"],
            "attributes": {"publicLinkEnabled": True},
        }})["data"]
    return call("POST", "/v1/betaGroups", body={"data": {
        "type": "betaGroups",
        "attributes": {
            "name": PUBLIC_GROUP,
            "publicLinkEnabled": True,
            "publicLinkLimitEnabled": False,
        },
        "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
    }})["data"]


def private_group(app_id):
    """The external TestFlight group "Private beta", created if needed."""
    group = next((g for g in external_groups(app_id)
                  if g["attributes"]["name"] == PRIVATE_GROUP), None)
    if group is None:
        return call("POST", "/v1/betaGroups", body={"data": {
            "type": "betaGroups",
            "attributes": {"name": PRIVATE_GROUP, "publicLinkEnabled": False},
            "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
        }})["data"]
    if group["attributes"]["publicLinkEnabled"]:
        sys.exit(
            f'The TestFlight group "{PRIVATE_GROUP}" has a public link; turn it off '
            "in App Store Connect and re-run this job."
        )
    return group


def submit_for_beta_review(build_id, name):
    submissions = call("GET", "/v1/betaAppReviewSubmissions", {
        "filter[build]": build_id,
        "fields[betaAppReviewSubmissions]": "betaReviewState",
    })["data"]
    if submissions:
        state = submissions[0]["attributes"]["betaReviewState"]
        print(f"{name} is already {state} in Beta App Review.")
        return
    call("POST", "/v1/betaAppReviewSubmissions", body={"data": {
        "type": "betaAppReviewSubmissions",
        "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
    }})
    print(f"Submitted {name} for Beta App Review.")


def summarize(line):
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write(line + "\n")


def what_to_test(app, build):
    notes = release_notes(os.environ["TAG"])
    if notes:
        set_what_to_test(build["id"], app["attributes"]["primaryLocale"], notes)


def external_beta(app, build, name, public):
    """Gives the build to a public or private group after Beta App Review."""
    what_to_test(app, build)
    fill_test_information(app["id"], app["attributes"]["primaryLocale"])
    group = public_group(app["id"]) if public else private_group(app["id"])
    call("POST", f"/v1/betaGroups/{group['id']}/relationships/builds",
         body={"data": [{"type": "builds", "id": build["id"]}]})
    # Otherwise someone has to distribute the build once Beta App Review approves it.
    beta_detail = call("GET", f"/v1/builds/{build['id']}/buildBetaDetail", {
        "fields[buildBetaDetails]": "autoNotifyEnabled",
    })["data"]
    fill(beta_detail, {"autoNotifyEnabled": True})
    submit_for_beta_review(build["id"], name)
    if public:
        summarize(f"TestFlight public link for {name}: "
                  f"{group['attributes']['publicLink']} "
                  "(works once Beta App Review approves the build)")
    else:
        summarize(f'{name} is in the TestFlight group "{PRIVATE_GROUP}"; its testers '
                  "get it once Beta App Review approves the build (invite them in App "
                  "Store Connect)")


def app_store_version(app_id, version, versions):
    """The editable App Store version VERSION, created or renamed if needed."""
    same = [v for v in versions if v["attributes"]["versionString"] == version]
    editable = [v for v in versions if v["attributes"]["appVersionState"] in EDITABLE]
    if same:
        state = same[0]["attributes"]["appVersionState"]
        if state not in EDITABLE:
            sys.exit(f"App Store version {version} is {state}.")
        return same[0]["id"]
    if editable:
        # Only one editable version can exist: give it this version number.
        version_id = editable[0]["id"]
        call("PATCH", f"/v1/appStoreVersions/{version_id}", body={"data": {
            "type": "appStoreVersions",
            "id": version_id,
            "attributes": {"versionString": version},
        }})
        return version_id
    created = call("POST", "/v1/appStoreVersions", body={"data": {
        "type": "appStoreVersions",
        "attributes": {"platform": "IOS", "versionString": version},
        "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
    }})
    return created["data"]["id"]


def submit(submission_id, version_id):
    """Sends the submission once App Store Connect has prepared the version."""
    for _ in range(20):
        state = call("GET", f"/v1/appStoreVersions/{version_id}", {
            "fields[appStoreVersions]": "appVersionState",
        })["data"]["attributes"]["appVersionState"]
        if state == "READY_FOR_REVIEW":
            break
        time.sleep(15)
    for attempt in range(5):
        try:
            call("PATCH", f"/v1/reviewSubmissions/{submission_id}", body={"data": {
                "type": "reviewSubmissions",
                "id": submission_id,
                "attributes": {"submitted": True},
            }})
            return
        except ApiError as error:
            if error.status != 409 or "not ready" not in error.body or attempt == 4:
                raise
            time.sleep(30)


def open_submission(app_id):
    """The unsent review submission, if any, with the versions it contains."""
    submissions = call("GET", "/v1/reviewSubmissions", {
        "filter[app]": app_id,
        "filter[platform]": "IOS",
        "filter[state]": "READY_FOR_REVIEW,WAITING_FOR_REVIEW,IN_REVIEW,UNRESOLVED_ISSUES",
    })["data"]
    busy = [s for s in submissions if s["attributes"]["state"] != "READY_FOR_REVIEW"]
    if busy:
        sys.exit(
            f"A submission is already {busy[0]['attributes']['state']}; "
            "resolve it in App Store Connect."
        )
    if not submissions:
        return None, []
    items = call("GET", f"/v1/reviewSubmissions/{submissions[0]['id']}/items", {
        "include": "appStoreVersion",
    })["data"]
    versions = [
        (item["relationships"]["appStoreVersion"].get("data") or {}).get("id")
        for item in items
    ]
    return submissions[0]["id"], versions


def main():
    bundle_id = os.environ["BUNDLE_ID"]
    version = os.environ["VERSION"]
    build_number = os.environ["BUILD_NUMBER"]
    promote = os.environ["MODE"] == "promote"
    channel = os.environ["CHANNEL"]
    name = f"{version} ({build_number})"

    apps = call("GET", "/v1/apps", {
        "filter[bundleId]": bundle_id, "fields[apps]": "bundleId,primaryLocale",
    })["data"]
    app = next((a for a in apps if a["attributes"]["bundleId"] == bundle_id), None)
    if app is None:
        sys.exit(f"No app with the bundle ID {bundle_id} in App Store Connect.")
    app_id = app["id"]

    build = find_build(app_id, version, build_number, uploaded=not promote)
    answer_export_compliance(build)
    if channel == "internal":
        what_to_test(app, build)
        summarize(f"{name} is available to the internal TestFlight testers (groups "
                  "with automatic distribution get it at once)")
        return
    if build["attributes"].get("buildAudienceType") == "INTERNAL_ONLY":
        sys.exit("The build is for internal TestFlight testing only.")
    if channel in ("beta", "alpha"):
        external_beta(app, build, name, public=channel == "beta")
        return

    versions = call("GET", f"/v1/apps/{app_id}/appStoreVersions", {
        "filter[platform]": "IOS",
        "fields[appStoreVersions]": "versionString,appVersionState,releaseType",
        "limit": "200",
    })["data"]
    same = next((v for v in versions if v["attributes"]["versionString"] == version), None)
    state = same["attributes"]["appVersionState"] if same else None
    if state in SUBMITTED or state == "READY_FOR_REVIEW":
        attached = call("GET", f"/v1/appStoreVersions/{same['id']}/relationships/build")
        if (attached.get("data") or {}).get("id") != build["id"]:
            sys.exit(f"App Store version {version} is {state} with another build.")
        if state in SUBMITTED:
            print(f"{version} ({build_number}) is already {state}.")
            return
        # A previous run added the version to a submission but did not send it.
        submission_id, contents = open_submission(app_id)
        if submission_id is None or contents != [same["id"]]:
            sys.exit(f"Send the review submission of {version} in App Store Connect.")
        submit(submission_id, same["id"])
        print(f"Submitted {version} ({build_number}) for App Review.")
        return

    # "What's New" is required in every language of an update.
    is_update = any(
        v["attributes"]["appVersionState"] == "READY_FOR_DISTRIBUTION" for v in versions
    )
    notes = release_notes(os.environ["TAG"]) if is_update else ""
    if is_update and not notes:
        sys.exit(
            "App Store updates need \"What's New\": write it in the GitHub "
            "release description and re-run this job."
        )
    submission_id, contents = open_submission(app_id)
    if contents:
        sys.exit("An unsent submission already has items; check it in App Store Connect.")

    version_id = app_store_version(app_id, version, versions)
    call("PATCH", f"/v1/appStoreVersions/{version_id}", body={"data": {
        "type": "appStoreVersions",
        "id": version_id,
        "attributes": {"releaseType": "AFTER_APPROVAL"},
    }})
    call(
        "PATCH",
        f"/v1/appStoreVersions/{version_id}/relationships/build",
        body={"data": {"type": "builds", "id": build["id"]}},
    )
    if is_update:
        localizations = call(
            "GET",
            f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
            {"fields[appStoreVersionLocalizations]": "locale,whatsNew", "limit": "200"},
        )["data"]
        if not localizations:
            sys.exit(f"App Store version {version} has no languages.")
        for localization in localizations:
            call("PATCH", f"/v1/appStoreVersionLocalizations/{localization['id']}",
                 body={"data": {
                     "type": "appStoreVersionLocalizations",
                     "id": localization["id"],
                     "attributes": {"whatsNew": notes},
                 }})

    if submission_id is None:
        submission_id = call("POST", "/v1/reviewSubmissions", body={"data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": "IOS"},
            "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
        }})["data"]["id"]
    call("POST", "/v1/reviewSubmissionItems", body={"data": {
        "type": "reviewSubmissionItems",
        "relationships": {
            "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": submission_id}},
            "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
        },
    }})
    submit(submission_id, version_id)
    print(f"Submitted {version} ({build_number}) for App Review.")


if __name__ == "__main__":
    try:
        main()
    except ApiError as error:
        sys.exit(str(error))
    except subprocess.CalledProcessError as error:
        output = error.stderr
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        sys.exit(f"{' '.join(error.cmd)} failed:\n{output}")
