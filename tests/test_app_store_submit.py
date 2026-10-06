"""Tests scripts/app_store_submit.py against a mock of the App Store Connect API."""

import base64
import copy
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support import AppStoreSpec, MockApi, fake_gh, run_script

BUNDLE_ID = "com.example.app"
# App Review's details of an App Store version.
REVIEW = {
    "contactFirstName": "Jan",
    "contactLastName": "Kowalski",
    "contactPhone": "+48 22 123 45 67",
    "contactEmail": "jan@example.com",
    "demoAccountRequired": True,
    "demoAccountName": "demo",
    "demoAccountPassword": "secret",
}
NO_CONTACT = {**dict.fromkeys(REVIEW), "demoAccountRequired": False}
LIVE = {"v-old": {
    "version": "4.2.0", "state": "READY_FOR_DISTRIBUTION", "build": "b0",
    "created": "2025-03-01T10:00:00-08:00", "review": REVIEW,
    "descriptions": {"en-US": "Timetable", "pl": "Plan zajęć"},
}}
LISTING = {
    "locale": "pl",
    "description": "Wersja testowa",
    "feedbackEmail": "beta@example.com",
    "privacyPolicyUrl": None,
}


def resource(kind, resource_id, attributes):
    return {"type": kind, "id": resource_id, "attributes": dict(attributes)}


class AppStoreMock:
    """Answers like App Store Connect for a given state of the app."""

    def __init__(self, build=True, encryption=None, versions=None, submissions=None,
                 slow_ready=False, reject_once=False, audience="APP_STORE_ELIGIBLE",
                 errors=()):
        self.build = build
        self.encryption = encryption
        self.versions = copy.deepcopy(LIVE if versions is None else versions)
        self.submissions = copy.deepcopy(submissions or {})
        self.slow_ready = slow_ready
        self.reject_once = reject_once
        self.audience = audience
        # HTTP statuses of the first answers.
        self.errors = list(errors)
        self.polls = 0
        self.whats_new = []
        # TestFlight: test information, "What to Test", groups and submissions.
        self.beta_contact = dict(NO_CONTACT)
        self.beta_listings = {}
        self.what_to_test = {}
        self.privacy = {"pl": "https://example.com/prywatnosc"}
        self.groups = {"g-team": {"name": "Team", "isInternalGroup": True,
                                  "publicLinkEnabled": False, "publicLink": None}}
        self.group_builds = {}
        self.auto_notify = False
        self.beta_submissions = []
        self.spec = AppStoreSpec()

    def version(self, version_id):
        version = self.versions[version_id]
        return {"type": "appStoreVersions", "id": version_id, "attributes": {
            "versionString": version["version"],
            "appVersionState": version["state"],
            "releaseType": "MANUAL",
        }}

    def with_details(self, versions):
        """The versions with the review details and localizations they include."""
        included = []
        for item in versions:
            version = self.versions[item["id"]]
            item["attributes"]["createdDate"] = version["created"]
            review = None
            if "review" in version:
                review = {"type": "appStoreReviewDetails", "id": f"{item['id']}-review"}
                included.append({**review, "attributes": version["review"]})
            localizations = []
            for locale, description in version.get("descriptions", {}).items():
                localization = {"type": "appStoreVersionLocalizations",
                                "id": f"{item['id']}-{locale}"}
                localizations.append(localization)
                included.append({**localization, "attributes": {
                    "locale": locale, "description": description,
                }})
            item["relationships"] = {
                "appStoreReviewDetail": {"data": review},
                "appStoreVersionLocalizations": {"data": localizations},
            }
        return {"data": versions, "included": included}

    def testflight(self, method, path, query, body):
        """Answers the TestFlight requests, or returns None for the others."""
        if path == "/v1/apps/app1/betaAppReviewDetail":
            return 200, {"data": resource("betaAppReviewDetails", "app1", self.beta_contact)}
        if path == "/v1/betaAppReviewDetails/app1":
            self.beta_contact.update(body["data"]["attributes"])
            return 200, {"data": resource("betaAppReviewDetails", "app1", self.beta_contact)}
        if path == "/v1/apps/app1/betaAppLocalizations":
            return 200, {"data": [resource("betaAppLocalizations", i, a)
                                  for i, a in self.beta_listings.items()]}
        if path == "/v1/betaAppLocalizations":
            assert body["data"]["relationships"]["app"]["data"]["id"] == "app1"
            self.beta_listings["listing-new"] = body["data"]["attributes"]
            return 201, {"data": resource("betaAppLocalizations", "listing-new",
                                          body["data"]["attributes"])}
        match = re.match(r"^/v1/betaAppLocalizations/([^/]+)$", path)
        if match:
            listing = self.beta_listings[match.group(1)]
            listing.update(body["data"]["attributes"])
            return 200, {"data": resource("betaAppLocalizations", match.group(1), listing)}
        if path == "/v1/apps/app1/appInfos":
            return 200, {"data": [{"type": "appInfos", "id": "info1"}], "included": [
                resource("appInfoLocalizations", f"info1-{locale}",
                         {"locale": locale, "privacyPolicyUrl": url})
                for locale, url in self.privacy.items()
            ]}
        if path == "/v1/builds/b1/betaBuildLocalizations":
            return 200, {"data": [resource("betaBuildLocalizations", i, a)
                                  for i, a in self.what_to_test.items()]}
        if path == "/v1/betaBuildLocalizations":
            assert body["data"]["relationships"]["build"]["data"]["id"] == "b1"
            self.what_to_test["notes-new"] = body["data"]["attributes"]
            return 201, {"data": resource("betaBuildLocalizations", "notes-new",
                                          body["data"]["attributes"])}
        match = re.match(r"^/v1/betaBuildLocalizations/([^/]+)$", path)
        if match:
            notes = self.what_to_test[match.group(1)]
            notes.update(body["data"]["attributes"])
            return 200, {"data": resource("betaBuildLocalizations", match.group(1), notes)}
        if path == "/v1/apps/app1/betaGroups":
            return 200, {"data": [resource("betaGroups", i, a)
                                  for i, a in self.groups.items()]}
        if path == "/v1/betaGroups":
            assert body["data"]["relationships"]["app"]["data"]["id"] == "app1"
            self.groups["g-new"] = {"isInternalGroup": False, **body["data"]["attributes"],
                                    "publicLink": "https://testflight.apple.com/join/g-new"}
            return 201, {"data": resource("betaGroups", "g-new", self.groups["g-new"])}
        match = re.match(r"^/v1/betaGroups/([^/]+)(/relationships/builds)?$", path)
        if match:
            group_id, builds = match.groups()
            if builds:
                self.group_builds.setdefault(group_id, set()).update(
                    item["id"] for item in body["data"])
                return 204, None
            group = self.groups[group_id]
            group.update(body["data"]["attributes"])
            group["publicLink"] = f"https://testflight.apple.com/join/{group_id}"
            return 200, {"data": resource("betaGroups", group_id, group)}
        if path == "/v1/builds/b1/buildBetaDetail":
            return 200, {"data": resource("buildBetaDetails", "b1",
                                          {"autoNotifyEnabled": self.auto_notify})}
        if path == "/v1/buildBetaDetails/b1":
            self.auto_notify = body["data"]["attributes"]["autoNotifyEnabled"]
            return 200, {"data": resource("buildBetaDetails", "b1",
                                          {"autoNotifyEnabled": self.auto_notify})}
        if path == "/v1/betaAppReviewSubmissions" and method == "GET":
            builds = query["filter[build]"].split(",")
            return 200, {"data": [
                resource("betaAppReviewSubmissions", f"review-{build}",
                         {"betaReviewState": "WAITING_FOR_REVIEW"})
                for build in self.beta_submissions if build in builds
            ]}
        if path == "/v1/betaAppReviewSubmissions":
            build = body["data"]["relationships"]["build"]["data"]["id"]
            self.beta_submissions.append(build)
            return 201, {"data": resource("betaAppReviewSubmissions", f"review-{build}",
                                          {"betaReviewState": "WAITING_FOR_REVIEW"})}
        return None

    def __call__(self, request):
        header, _, _ = request.headers["Authorization"].removeprefix("Bearer ").split(".")
        header = json.loads(base64.urlsafe_b64decode(header + "=="))
        assert header == {"alg": "ES256", "kid": "KEY123", "typ": "JWT"}, header
        self.spec.check(request)
        if self.errors:
            status = self.errors.pop(0)
            return status, {"errors": [{"status": str(status), "title": "Try again"}]}
        method, path = request.method, request.path
        body = request.json() if request.raw else None
        if path == "/v1/apps":
            return 200, {"data": [{"type": "apps", "id": "app1", "attributes": {
                "bundleId": BUNDLE_ID, "primaryLocale": "pl",
            }}]}
        if path == "/v1/builds":
            if not self.build:
                return 200, {"data": []}
            return 200, {"data": [{"type": "builds", "id": "b1", "attributes": {
                "version": "430001",
                "processingState": "VALID",
                "expired": False,
                "usesNonExemptEncryption": self.encryption,
                "buildAudienceType": self.audience,
            }}]}
        if path == "/v1/builds/b1":
            self.encryption = body["data"]["attributes"]["usesNonExemptEncryption"]
            return 200, {"data": {"type": "builds", "id": "b1", "attributes": {}}}
        if path == "/v1/apps/app1/appStoreVersions":
            versions = [self.version(v) for v in self.versions]
            if "include" in request.query:
                return 200, self.with_details(versions)
            return 200, {"data": versions}
        if path == "/v1/appStoreVersions" and method == "POST":
            self.versions["v-new"] = {
                "version": body["data"]["attributes"]["versionString"],
                "state": "PREPARE_FOR_SUBMISSION",
                "build": None,
            }
            return 201, {"data": self.version("v-new")}
        match = re.match(r"^/v1/appStoreVersions/([^/]+)(/.*)?$", path)
        if match:
            version_id, rest = match.groups()
            version = self.versions[version_id]
            if rest == "/relationships/build":
                if method == "PATCH":
                    version["build"] = body["data"]["id"]
                    return 204, None
                build = version["build"]
                return 200, {"data": {"type": "builds", "id": build} if build else None}
            if rest == "/appStoreVersionLocalizations":
                return 200, {"data": [{"type": "appStoreVersionLocalizations", "id": "l1",
                                       "attributes": {"locale": "pl", "whatsNew": None}}]}
            if method == "PATCH":
                attributes = body["data"]["attributes"]
                version["version"] = attributes.get("versionString", version["version"])
                return 200, {"data": self.version(version_id)}
            self.polls += 1
            state = version["state"]
            if self.slow_ready and self.polls < 3:
                state = "PREPARE_FOR_SUBMISSION"
            return 200, {"data": {"type": "appStoreVersions", "id": version_id,
                                  "attributes": {"appVersionState": state}}}
        if path == "/v1/appStoreVersionLocalizations/l1":
            self.whats_new.append(body["data"]["attributes"]["whatsNew"])
            return 200, {"data": {"type": "appStoreVersionLocalizations", "id": "l1",
                                  "attributes": {}}}
        if path == "/v1/reviewSubmissions" and method == "GET":
            return 200, {"data": [
                {"type": "reviewSubmissions", "id": s, "attributes": {"state": v["state"]}}
                for s, v in self.submissions.items()
            ]}
        if path == "/v1/reviewSubmissions" and method == "POST":
            self.submissions["rs-new"] = {"state": "READY_FOR_REVIEW", "items": []}
            return 201, {"data": {"type": "reviewSubmissions", "id": "rs-new",
                                  "attributes": {"state": "READY_FOR_REVIEW"}}}
        match = re.match(r"^/v1/reviewSubmissions/([^/]+)/items$", path)
        if match:
            return 200, {"data": [
                {"type": "reviewSubmissionItems", "id": f"item{index}", "relationships": {
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": item}},
                }}
                for index, item in enumerate(self.submissions[match.group(1)]["items"])
            ]}
        if path == "/v1/reviewSubmissionItems":
            relationships = body["data"]["relationships"]
            submission_id = relationships["reviewSubmission"]["data"]["id"]
            version_id = relationships["appStoreVersion"]["data"]["id"]
            self.submissions[submission_id]["items"].append(version_id)
            self.versions[version_id]["state"] = "READY_FOR_REVIEW"
            return 201, {"data": {"type": "reviewSubmissionItems", "id": "item",
                                  "attributes": {"state": "READY_FOR_REVIEW"}}}
        match = re.match(r"^/v1/reviewSubmissions/([^/]+)$", path)
        if match and method == "PATCH":
            if self.reject_once:
                self.reject_once = False
                return 409, {"errors": [{"status": "409", "code": "STATE_ERROR",
                                         "title": "Conflict", "detail":
                                         "Version is not ready to be submitted yet, "
                                         "please try again later."}]}
            submission = self.submissions[match.group(1)]
            submission["state"] = "WAITING_FOR_REVIEW"
            for version_id in submission["items"]:
                self.versions[version_id]["state"] = "WAITING_FOR_REVIEW"
            return 200, {"data": {"type": "reviewSubmissions", "id": match.group(1),
                                  "attributes": {"state": "WAITING_FOR_REVIEW"}}}
        answer = self.testflight(method, path, request.query, body)
        if answer is None:
            raise AssertionError(f"unexpected request {request}")
        return answer


class AppStoreSubmitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        key = Path(cls.keys.name) / "key.pem"
        cls.key_path = Path(cls.keys.name) / "AuthKey_KEY123.p8"
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey",
                        "-noout", "-out", str(key)], check=True)
        subprocess.run(["openssl", "pkcs8", "-topk8", "-nocrypt", "-in", str(key),
                        "-out", str(cls.key_path)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        fake_gh(self.dir)

    def submit(self, mock, mode, channel, encryption="false", notes="", **env):
        api = MockApi(mock)
        self.addCleanup(api.close)
        values = {
            "ASC_API": api.url,
            "KEY_ID": "KEY123",
            "ISSUER_ID": "issuer",
            "KEY_PATH": str(self.key_path),
            "BUNDLE_ID": BUNDLE_ID,
            "TAG": "v4.3.0",
            "VERSION": "4.3.0",
            "BUILD_NUMBER": "430001",
            "MODE": mode,
            "CHANNEL": channel,
            "USES_NON_EXEMPT_ENCRYPTION": encryption,
            "FAKE_NOTES": notes,
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_STEP_SUMMARY": str(self.dir / "summary.md"),
            "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
        }
        values.update(env)
        result = run_script("app_store_submit.py", dict(os.environ, **values),
                            skip_sleep=True)
        self.assertEqual(api.errors, [], result.stderr)
        return result, api

    def test_release_is_submitted_for_review(self):
        mock = AppStoreMock()
        result, api = self.submit(mock, "build", "prod", notes="Poprawki")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Submitted 4.3.0 (430001) for App Review.", result.stdout)
        self.assertFalse(mock.encryption)
        self.assertEqual(mock.versions["v-new"]["build"], "b1")
        self.assertEqual(mock.versions["v-new"]["state"], "WAITING_FOR_REVIEW")
        self.assertEqual(mock.whats_new, ["Poprawki"])
        self.assertIn("POST /v1/reviewSubmissionItems", api.calls())

    def test_update_without_notes_fails(self):
        result, api = self.submit(AppStoreMock(), "build", "prod")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("What's New", result.stderr)
        self.assertNotIn("POST /v1/appStoreVersions", api.calls())

    def test_first_release_needs_no_notes(self):
        mock = AppStoreMock(versions={})
        result, _ = self.submit(mock, "build", "prod")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.whats_new, [])
        self.assertEqual(mock.versions["v-new"]["state"], "WAITING_FOR_REVIEW")

    def test_editable_version_gets_the_new_version_number(self):
        mock = AppStoreMock(versions={**LIVE, "v-edit": {
            "version": "4.2.1", "state": "PREPARE_FOR_SUBMISSION", "build": None,
        }})
        result, api = self.submit(mock, "build", "prod", notes="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("POST /v1/appStoreVersions", api.calls())
        self.assertEqual(mock.versions["v-edit"]["version"], "4.3.0")
        self.assertEqual(mock.versions["v-edit"]["build"], "b1")
        self.assertEqual(mock.versions["v-edit"]["state"], "WAITING_FOR_REVIEW")

    def test_submission_in_review_blocks_a_new_one(self):
        mock = AppStoreMock(
            submissions={"rs1": {"state": "WAITING_FOR_REVIEW", "items": []}},
        )
        result, api = self.submit(mock, "build", "prod", notes="x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("A submission is already WAITING_FOR_REVIEW", result.stderr)
        self.assertNotIn("POST /v1/appStoreVersions", api.calls())
        self.assertNotIn("POST /v1/reviewSubmissions", api.calls())

    def test_beta_becomes_a_public_testflight_beta(self):
        mock = AppStoreMock(versions={
            "v-older": {
                "version": "4.1.0", "state": "REPLACED_WITH_NEW_VERSION", "build": "b-1",
                "created": "2024-09-01T10:00:00-07:00",
                "review": {**REVIEW, "contactFirstName": "Anna"},
                "descriptions": {"pl": "Stary opis"},
            },
            **LIVE,
            "v-next": {
                "version": "4.3.0", "state": "PREPARE_FOR_SUBMISSION", "build": None,
                "created": "2026-01-10T10:00:00-08:00",
                "descriptions": {"en-US": "Timetable for students"},
            },
        })
        result, api = self.submit(mock, "build", "beta", notes="Nowości")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(mock.encryption)
        self.assertEqual(mock.what_to_test,
                         {"notes-new": {"locale": "pl", "whatsNew": "Nowości"}})
        # Contact from the newest version with App Review details; description
        # in the primary language.
        self.assertEqual(mock.beta_contact, REVIEW)
        self.assertEqual(mock.beta_listings, {"listing-new": {
            "locale": "pl",
            "description": "Plan zajęć",
            "feedbackEmail": "jan@example.com",
            "privacyPolicyUrl": "https://example.com/prywatnosc",
        }})
        link = "https://testflight.apple.com/join/g-new"
        self.assertEqual(mock.groups["g-new"], {
            "name": "Public beta",
            "isInternalGroup": False,
            "publicLinkEnabled": True,
            "publicLinkLimitEnabled": False,
            "publicLink": link,
        })
        self.assertEqual(mock.group_builds, {"g-new": {"b1"}})
        self.assertTrue(mock.auto_notify)
        self.assertEqual(mock.beta_submissions, ["b1"])
        self.assertIn("Submitted 4.3.0 (430001) for Beta App Review.", result.stdout)
        self.assertIn(link, result.stdout)
        self.assertIn(link, (self.dir / "summary.md").read_text())
        self.assertEqual([call for call in api.calls() if "appStoreVersions" in call],
                         ["GET /v1/apps/app1/appStoreVersions"])

    def test_beta_keeps_the_test_information_and_group_that_are_set(self):
        mock = AppStoreMock()
        contact = {**NO_CONTACT, "contactFirstName": "Anna", "contactLastName": "Nowak",
                   "contactPhone": "+48 600 000 000", "contactEmail": "anna@example.com"}
        mock.beta_contact = dict(contact)
        mock.beta_listings = {"listing1": dict(LISTING)}
        mock.what_to_test = {"notes-en": {"locale": "en-US", "whatsNew": "Old"},
                             "notes-pl": {"locale": "pl", "whatsNew": "Stare"}}
        link = "https://testflight.apple.com/join/TESTERS"
        mock.groups["g-testers"] = {"name": "Testers", "isInternalGroup": False,
                                    "publicLinkEnabled": True, "publicLink": link}
        mock.auto_notify = True
        for _ in range(2):
            result, api = self.submit(mock, "build", "beta", notes="Nowości")
            self.assertEqual(result.returncode, 0, result.stderr)
        # The second run finds the submission of the first.
        self.assertIn("4.3.0 (430001) is already WAITING_FOR_REVIEW in Beta App Review.",
                      result.stdout)
        self.assertEqual(mock.beta_submissions, ["b1"])
        self.assertEqual(mock.beta_contact, contact)
        self.assertEqual(mock.beta_listings, {"listing1": LISTING})
        self.assertEqual(mock.what_to_test, {
            "notes-en": {"locale": "en-US", "whatsNew": "Nowości"},
            "notes-pl": {"locale": "pl", "whatsNew": "Nowości"},
        })
        self.assertEqual(mock.group_builds, {"g-testers": {"b1"}})
        self.assertNotIn("g-new", mock.groups)
        self.assertNotIn("GET /v1/apps/app1/appStoreVersions", api.calls())
        self.assertNotIn("PATCH /v1/buildBetaDetails/b1", api.calls())
        self.assertIn(link, (self.dir / "summary.md").read_text())

    def test_beta_fills_in_only_the_missing_test_information(self):
        mock = AppStoreMock()
        mock.beta_contact["contactEmail"] = "beta@example.com"
        english = {**LISTING, "locale": "en-US", "description": "Beta"}
        mock.beta_listings = {"listing-en": dict(english),
                              "listing-pl": {**LISTING, "feedbackEmail": None}}
        result, api = self.submit(mock, "build", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.beta_contact, {**REVIEW, "contactEmail": "beta@example.com"})
        self.assertEqual(mock.beta_listings, {
            "listing-en": english,
            "listing-pl": {**LISTING, "privacyPolicyUrl": "https://example.com/prywatnosc"},
        })
        self.assertNotIn("POST /v1/betaAppLocalizations", api.calls())

    def test_beta_turns_on_the_public_link_of_the_public_beta_group(self):
        mock = AppStoreMock()
        mock.beta_contact = dict(REVIEW)
        mock.beta_listings = {"listing1": dict(LISTING)}
        mock.groups["g-public"] = {"name": "Public beta", "isInternalGroup": False,
                                   "publicLinkEnabled": False, "publicLink": None}
        result, api = self.submit(mock, "build", "beta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("POST /v1/betaGroups", api.calls())
        self.assertTrue(mock.groups["g-public"]["publicLinkEnabled"])
        self.assertEqual(mock.group_builds, {"g-public": {"b1"}})
        self.assertIn("https://testflight.apple.com/join/g-public", result.stdout)
        # An empty release description sets no "What to Test".
        self.assertFalse(any("betaBuildLocalizations" in call for call in api.calls()))

    def test_alpha_goes_to_the_private_testflight_group(self):
        mock = AppStoreMock()
        mock.groups["g-public"] = {"name": "Public beta", "isInternalGroup": False,
                                   "publicLinkEnabled": True,
                                   "publicLink": "https://testflight.apple.com/join/p"}
        result, api = self.submit(mock, "build", "alpha", notes="Nowości")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.what_to_test,
                         {"notes-new": {"locale": "pl", "whatsNew": "Nowości"}})
        self.assertEqual(mock.beta_contact, REVIEW)
        self.assertEqual(mock.groups["g-new"]["name"], "Private beta")
        self.assertFalse(mock.groups["g-new"]["publicLinkEnabled"])
        self.assertEqual(mock.group_builds, {"g-new": {"b1"}})
        self.assertTrue(mock.auto_notify)
        self.assertEqual(mock.beta_submissions, ["b1"])
        self.assertNotIn("testflight.apple.com", result.stdout)
        self.assertIn('TestFlight group "Private beta"',
                      (self.dir / "summary.md").read_text())

    def test_alpha_reuses_the_private_group_and_refuses_a_public_link(self):
        for public_link, code in ((False, 0), (True, 1)):
            with self.subTest(public_link=public_link):
                mock = AppStoreMock()
                mock.groups["g-private"] = {"name": "Private beta",
                                            "isInternalGroup": False,
                                            "publicLinkEnabled": public_link,
                                            "publicLink": None}
                result, api = self.submit(mock, "build", "alpha", notes="x")
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertNotIn("POST /v1/betaGroups", api.calls())
                if public_link:
                    self.assertIn("has a public link", result.stderr)
                    self.assertEqual(mock.group_builds, {})
                else:
                    self.assertEqual(mock.group_builds, {"g-private": {"b1"}})

    def test_internal_test_stays_with_the_internal_testers(self):
        for audience in ("INTERNAL_ONLY", "APP_STORE_ELIGIBLE"):
            with self.subTest(audience):
                mock = AppStoreMock(audience=audience)
                result, api = self.submit(mock, "build", "internal", notes="Nowości")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(mock.what_to_test,
                                 {"notes-new": {"locale": "pl", "whatsNew": "Nowości"}})
                self.assertEqual(mock.group_builds, {})
                self.assertEqual(mock.beta_submissions, [])
                self.assertNotIn("betaGroups", " ".join(api.calls()))
                self.assertIn("internal TestFlight testers",
                              (self.dir / "summary.md").read_text())

    def test_beta_without_test_information_to_copy_fails(self):
        mock = AppStoreMock(versions={})
        result, api = self.submit(mock, "build", "beta", notes="x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TestFlight > Test Information", result.stderr)
        self.assertIn("contactPhone", result.stderr)
        self.assertIn("description", result.stderr)
        self.assertEqual(mock.beta_contact, NO_CONTACT)
        self.assertNotIn("POST /v1/betaGroups", api.calls())
        self.assertEqual(mock.beta_submissions, [])

    def test_build_without_an_export_compliance_answer_fails(self):
        for channel in ("prod", "beta"):
            with self.subTest(channel):
                result, api = self.submit(AppStoreMock(), "build", channel,
                                          encryption="", notes="x")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("export compliance", result.stderr)
                self.assertEqual(api.calls()[-1], "GET /v1/builds")

    def test_upload_that_never_appears_fails_after_twenty_minutes(self):
        result, api = self.submit(AppStoreMock(build=False), "build", "beta")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("20 minutes after the upload", result.stderr)
        self.assertEqual(api.calls().count("GET /v1/builds"), 41)

    def test_promotion_without_a_build_fails_at_once(self):
        result, api = self.submit(AppStoreMock(build=False), "promote", "prod", notes="x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only a beta that the release workflow uploaded", result.stderr)
        self.assertEqual(api.calls().count("GET /v1/builds"), 1)

    def test_rerun_sends_a_prepared_submission(self):
        mock = AppStoreMock(
            encryption=False,
            versions={**LIVE, "v-new": {"version": "4.3.0", "state": "READY_FOR_REVIEW",
                                        "build": "b1"}},
            submissions={"rs1": {"state": "READY_FOR_REVIEW", "items": ["v-new"]}},
        )
        result, api = self.submit(mock, "promote", "prod", notes="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PATCH /v1/reviewSubmissions/rs1", api.calls())
        self.assertNotIn("POST /v1/appStoreVersions", api.calls())
        self.assertEqual(mock.versions["v-new"]["state"], "WAITING_FOR_REVIEW")

    def test_rerun_after_the_submission_was_sent_does_nothing(self):
        mock = AppStoreMock(
            encryption=False,
            versions={**LIVE, "v-new": {"version": "4.3.0", "state": "WAITING_FOR_REVIEW",
                                        "build": "b1"}},
            submissions={"rs1": {"state": "WAITING_FOR_REVIEW", "items": ["v-new"]}},
        )
        result, api = self.submit(mock, "promote", "prod", notes="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already WAITING_FOR_REVIEW", result.stdout)
        self.assertFalse(any(call.startswith("PATCH") for call in api.calls()))

    def test_version_in_review_with_another_build_fails(self):
        mock = AppStoreMock(
            encryption=False,
            versions={**LIVE, "v-new": {"version": "4.3.0", "state": "WAITING_FOR_REVIEW",
                                        "build": "b9"}},
        )
        result, _ = self.submit(mock, "promote", "prod", notes="x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("with another build", result.stderr)

    def test_submission_waits_until_the_version_is_ready(self):
        mock = AppStoreMock(slow_ready=True, reject_once=True)
        result, api = self.submit(mock, "build", "prod", notes="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(api.calls().count("PATCH /v1/reviewSubmissions/rs-new"), 2)

    def test_internal_only_build_is_not_submitted(self):
        for channel in ("prod", "beta", "alpha"):
            with self.subTest(channel):
                mock = AppStoreMock(audience="INTERNAL_ONLY")
                result, api = self.submit(mock, "build", channel, notes="x")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("internal TestFlight testing only", result.stderr)
                self.assertEqual(api.calls()[-1], "PATCH /v1/builds/b1")

    def test_rate_limits_and_server_errors_are_retried(self):
        result, api = self.submit(AppStoreMock(errors=[429, 503]), "build", "prod",
                                  notes="x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(api.calls()[:3], ["GET /v1/apps"] * 3)

    def test_other_and_lasting_errors_fail(self):
        for errors in ([500] * 5, [403]):
            with self.subTest(errors):
                result, api = self.submit(AppStoreMock(errors=errors), "build", "prod",
                                          notes="x")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"HTTP {errors[0]}", result.stderr)
                self.assertEqual(api.calls(), ["GET /v1/apps"] * len(errors))

    def test_unreadable_key_fails_with_the_openssl_error(self):
        result, api = self.submit(AppStoreMock(), "build", "prod", notes="x",
                                  KEY_PATH=str(self.dir / "missing.p8"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("openssl dgst", result.stderr)
        self.assertEqual(api.requests, [])


if __name__ == "__main__":
    unittest.main()
