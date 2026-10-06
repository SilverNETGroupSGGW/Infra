"""Tests scripts/google_play.py against a mock of the Google Play Developer API."""

import hashlib
import os
import re
import tempfile
import unittest
from pathlib import Path

from tests.support import MockApi, PlaySpec, fake_gh, run_script

PACKAGE = "com.example.app"
EDITS = f"/androidpublisher/v3/applications/{PACKAGE}/edits"
UPLOADS = f"/upload/androidpublisher/v3/applications/{PACKAGE}/edits"
BUNDLE = b"PK\x03\x04 an App Bundle"
IMAGE_TYPES = ("phoneScreenshots", "sevenInchScreenshots", "tenInchScreenshots")
BETA_TRACK = {
    "track": "beta",
    "releases": [{
        "name": "v4.3.0-beta.1",
        "versionCodes": ["4030001"],
        "status": "completed",
        "releaseNotes": [{"language": "pl-PL", "text": "Beta notes"}],
    }],
}


class PlayMock:
    """Answers like the Play Developer API for a given state of the app."""

    def __init__(self, bundles=None, tracks=None, used=False, manual_review=False,
                 images=None):
        self.bundles = bundles or {}  # version code -> sha256
        self.tracks = tracks or {}
        self.used = used
        self.manual_review = manual_review
        # Store listing images: language -> image type -> contents.
        self.images = images or {}
        self.spec = PlaySpec()
        self.puts = []

    def __call__(self, request):
        assert request.headers["Authorization"] == "Bearer token"
        path, query = request.path, request.query
        if request.method == "POST" and path == EDITS:
            assert request.json() == {}
            return 200, {"id": "e1", "expiryTimeSeconds": "1"}
        if request.method == "GET" and path == f"{EDITS}/e1/bundles":
            return 200, {"bundles": [
                {"versionCode": int(code), "sha256": sha} for code, sha in self.bundles.items()
            ]}
        if request.method == "POST" and path == f"{UPLOADS}/e1/bundles":
            assert query == {"uploadType": "media"}
            assert request.headers["Content-Type"] == "application/octet-stream"
            assert request.raw == BUNDLE
            if self.used:
                return 403, {"error": {"code": 403, "message":
                    "APK specifies a version code that has already been used."}}
            return 200, {"versionCode": 4030001, "sha256": "x"}
        if path.startswith(f"{EDITS}/e1/tracks/"):
            track = path.rsplit("/", 1)[1]
            if request.method == "GET":
                return 200, self.tracks.get(track, {"track": track, "releases": []})
            if request.method == "PUT":
                body = request.json()
                assert request.headers["Content-Type"] == "application/json"
                self.spec.validate(body, "Track")
                assert body["track"] == track
                self.puts.append(body)
                return 200, body
        if request.method == "DELETE" and path == f"{EDITS}/e1":
            return 204, None
        if request.method == "GET" and path == f"{EDITS}/e1/listings":
            return 200, {"listings": [
                {"language": language, "title": "App"} for language in self.images
            ]}
        listing = re.match(r"^(.*)/e1/listings/([^/]+)/([^/]+)$", path)
        if listing:
            root, language, image_type = listing.groups()
            assert image_type in IMAGE_TYPES, image_type
            images = self.images[language].setdefault(image_type, [])
            if request.method == "GET" and root == EDITS:
                return 200, {"images": [
                    {"id": str(index), "sha256": hashlib.sha256(image).hexdigest()}
                    for index, image in enumerate(images)
                ]} if images else {}
            if request.method == "DELETE" and root == EDITS:
                images.clear()
                return 200, {}
            if request.method == "POST" and root == UPLOADS:
                assert query == {"uploadType": "media"}
                assert request.headers["Content-Type"] == "image/png"
                images.append(request.raw)
                return 200, {"image": {"id": str(len(images))}}
        if request.method == "POST" and path == f"{EDITS}/e1:commit":
            assert "changesInReviewBehavior" not in query
            assert request.raw == b""
            if self.manual_review and "changesNotSentForReview" not in query:
                return 400, {"error": {"code": 400, "message":
                    "Changes cannot be sent for review automatically. Please set "
                    "the query parameter changesNotSentForReview to true."}}
            return 200, {"id": "e1"}
        raise AssertionError(f"unexpected request {request}")


class GooglePlayTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        fake_gh(self.dir)
        self.bundle = self.dir / "app.aab"
        self.bundle.write_bytes(BUNDLE)

    def run_play(self, mock, **env):
        api = MockApi(mock)
        self.addCleanup(api.close)
        values = {
            "PLAY_API": api.url,
            "ACCESS_TOKEN": "token",
            "PACKAGE_NAME": PACKAGE,
            "BUNDLE_PATH": str(self.bundle),
            "VERSION_CODE": "4030001",
            "BETA_TRACK": "beta",
            "NOTES_LANGUAGE": "pl-PL",
            "GITHUB_REPOSITORY": "owner/repo",
            "FAKE_NOTES": "",
            "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
        }
        values.update(env)
        result = run_script("google_play.py", dict(os.environ, **values))
        self.assertEqual(api.errors, [], result.stderr)
        return result, api

    def test_beta_upload(self):
        mock = PlayMock()
        result, api = self.run_play(
            mock, MODE="build", TRACK="beta", TAG="v4.3.0-beta.1", FAKE_NOTES="Nowości"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"POST {UPLOADS}/e1/bundles", api.calls())
        self.assertEqual(mock.puts, [{"track": "beta", "releases": [{
            "name": "v4.3.0-beta.1",
            "versionCodes": ["4030001"],
            "status": "completed",
            "releaseNotes": [{"language": "pl-PL", "text": "Nowości"}],
        }]}])
        self.assertEqual(api.calls()[-1], f"POST {EDITS}/e1:commit")

    def test_beta_track_with_another_name(self):
        mock = PlayMock()
        result, _ = self.run_play(
            mock, MODE="build", TRACK="openBeta", BETA_TRACK="openBeta", TAG="v4.3.0-beta.1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([put["track"] for put in mock.puts], ["openBeta"])

        mock = PlayMock(tracks={"openBeta": dict(BETA_TRACK, track="openBeta")})
        result, _ = self.run_play(
            mock, MODE="promote", TRACK="production", BETA_TRACK="openBeta", TAG="v4.3.0-beta.1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            mock.puts[0]["releases"][0]["releaseNotes"],
            [{"language": "pl-PL", "text": "Beta notes"}],
        )

    def test_publish_an_uploaded_beta_to_a_testing_track(self):
        mock = PlayMock(tracks={"beta": BETA_TRACK})
        result, api = self.run_play(
            mock, MODE="promote", TRACK="alpha", TAG="v4.3.0-beta.1", FAKE_NOTES="Notes"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"POST {UPLOADS}/e1/bundles", api.calls())
        self.assertEqual(mock.puts, [{"track": "alpha", "releases": [{
            "name": "v4.3.0-beta.1",
            "versionCodes": ["4030001"],
            "status": "completed",
            "releaseNotes": [{"language": "pl-PL", "text": "Notes"}],
        }]}])

    def test_production_upload_without_notes(self):
        mock = PlayMock()
        result, _ = self.run_play(mock, MODE="build", TRACK="production", TAG="v4.3.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.puts, [{"track": "production", "releases": [{
            "name": "v4.3.0", "versionCodes": ["4030001"], "status": "completed",
        }]}])

    def test_long_notes_are_shortened(self):
        mock = PlayMock()
        result, _ = self.run_play(
            mock, MODE="build", TRACK="beta", TAG="v4.3.0-beta.1", FAKE_NOTES="x" * 600
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = mock.puts[0]["releases"][0]["releaseNotes"][0]["text"]
        self.assertEqual(len(text), 500)
        self.assertTrue(text.endswith("…"))

    def test_rerun_with_the_same_bundle_does_not_upload_again(self):
        sha = hashlib.sha256(BUNDLE).hexdigest()
        mock = PlayMock(bundles={"4030001": sha})
        result, api = self.run_play(mock, MODE="build", TRACK="beta", TAG="v4.3.0-beta.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already uploaded", result.stdout)
        self.assertNotIn(f"POST {UPLOADS}/e1/bundles", api.calls())
        self.assertEqual(len(mock.puts), 1)

    def test_reused_build_number_with_another_bundle_fails(self):
        mock = PlayMock(bundles={"4030001": "another"})
        result, api = self.run_play(mock, MODE="build", TRACK="production", TAG="v4.3.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already uploaded with another build", result.stderr)
        self.assertEqual(mock.puts, [])
        self.assertNotIn(f"POST {EDITS}/e1:commit", api.calls())

    def test_version_code_used_according_to_the_api_fails(self):
        mock = PlayMock(used=True)
        result, _ = self.run_play(mock, MODE="build", TRACK="production", TAG="v4.3.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("This build number was already uploaded", result.stderr)
        self.assertEqual(mock.puts, [])

    def test_promote_with_notes(self):
        mock = PlayMock(tracks={"beta": BETA_TRACK})
        result, api = self.run_play(
            mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1",
            FAKE_NOTES="Release notes",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"POST {UPLOADS}/e1/bundles", api.calls())
        self.assertEqual(mock.puts, [{"track": "production", "releases": [{
            "name": "v4.3.0",
            "versionCodes": ["4030001"],
            "status": "completed",
            "releaseNotes": [{"language": "pl-PL", "text": "Release notes"}],
        }]}])

    def test_promote_without_notes_keeps_the_beta_notes(self):
        mock = PlayMock(tracks={"beta": BETA_TRACK})
        result, _ = self.run_play(mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            mock.puts[0]["releases"][0]["releaseNotes"],
            [{"language": "pl-PL", "text": "Beta notes"}],
        )

    def test_promote_a_beta_that_a_later_beta_replaced(self):
        mock = PlayMock(
            bundles={"4030001": "sha"},
            tracks={"beta": {"track": "beta", "releases": [{
                "name": "v4.3.0-beta.2", "versionCodes": ["4030002"], "status": "completed",
            }]}},
        )
        result, _ = self.run_play(
            mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1", FAKE_NOTES="Notes"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.puts[0]["releases"][0]["versionCodes"], ["4030001"])

    def test_promote_a_beta_that_was_never_uploaded_fails(self):
        mock = PlayMock()
        result, _ = self.run_play(mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not in Google Play", result.stderr)
        self.assertEqual(mock.puts, [])

    def test_staged_rollout_in_progress_stops_the_release(self):
        mock = PlayMock(tracks={"beta": BETA_TRACK, "production": {
            "track": "production",
            "releases": [{"name": "4.2.0", "versionCodes": ["420003"],
                          "status": "inProgress", "userFraction": 0.2}],
        }})
        result, _ = self.run_play(mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staged rollout", result.stderr)
        self.assertEqual(mock.puts, [])

    def test_halted_rollout_is_replaced(self):
        mock = PlayMock(tracks={"beta": BETA_TRACK, "production": {
            "track": "production",
            "releases": [{"name": "4.2.0", "versionCodes": ["420003"],
                          "status": "halted", "userFraction": 0.2}],
        }})
        result, _ = self.run_play(mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(mock.puts), 1)

    def test_commit_that_needs_a_manual_review_is_left_for_play_console(self):
        mock = PlayMock(manual_review=True)
        result, api = self.run_play(mock, MODE="build", TRACK="beta", TAG="v4.3.0-beta.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(api.calls().count(f"POST {EDITS}/e1:commit"), 2)
        self.assertEqual(
            api.requests[-1].query.get("changesNotSentForReview"), "true"
        )
        self.assertIn("Send the changes for review in Play Console", result.stdout)

    def test_promote_names_the_release_after_the_version(self):
        mock = PlayMock(tracks={"beta": BETA_TRACK})
        result, _ = self.run_play(mock, MODE="promote", TRACK="production", TAG="4.3.0-rc1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.puts[0]["releases"][0]["name"], "4.3.0")

    def test_listing_replaces_the_screenshots_alone(self):
        root = self.screenshots({
            "phone/en/01-schedule.png": b"en 1",
            "phone/pl/01-schedule.png": b"pl 1",
        })
        mock = PlayMock(images={"en-US": {"phoneScreenshots": [b"old"]}, "pl-PL": {}})
        result, api = self.run_play(
            mock, MODE="listing", TRACK="", TAG="store-listing", SCREENSHOTS_DIR=root
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.images["en-US"]["phoneScreenshots"], [b"en 1"])
        self.assertEqual(mock.images["pl-PL"]["phoneScreenshots"], [b"pl 1"])
        self.assertEqual(mock.puts, [])
        self.assertEqual(api.calls()[-1], f"POST {EDITS}/e1:commit")

    def test_listing_without_changes_drops_the_edit(self):
        root = self.screenshots({"phone/en/01-schedule.png": b"same"})
        mock = PlayMock(images={"en-US": {"phoneScreenshots": [b"same"]}})
        result, api = self.run_play(
            mock, MODE="listing", TRACK="", TAG="store-listing", SCREENSHOTS_DIR=root
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up to date", result.stdout)
        self.assertEqual(api.calls()[-1], f"DELETE {EDITS}/e1")

    def test_listing_without_screenshots_fails(self):
        result, api = self.run_play(PlayMock(), MODE="listing", TRACK="", TAG="x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("screenshots-task", result.stderr)

    def screenshots(self, files):
        """A screenshots folder with {"<device>/<language>/<name>": contents}."""
        root = self.dir / "screenshots"
        for name, contents in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(contents)
        return str(root)

    def test_production_replaces_the_screenshots_that_differ(self):
        root = self.screenshots({
            "phone/en/01-schedule.png": b"en 1",
            "phone/en/02-map.png": b"en 2",
            "phone/pl/01-schedule.png": b"pl 1",
            "tablet-10/pl/01-schedule.png": b"pl tablet",
        })
        mock = PlayMock(tracks={"beta": BETA_TRACK}, images={
            "en-US": {"phoneScreenshots": [b"old"]},
            "pl-PL": {"phoneScreenshots": [b"pl 1"]},
            "de-DE": {"phoneScreenshots": [b"alt"]},
        })
        result, api = self.run_play(
            mock, MODE="promote", TRACK="production", TAG="v4.3.0-beta.1",
            SCREENSHOTS_DIR=root,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mock.images["en-US"]["phoneScreenshots"], [b"en 1", b"en 2"])
        self.assertEqual(mock.images["pl-PL"]["tenInchScreenshots"], [b"pl tablet"])
        # Unchanged, or without screenshots of their language: left alone.
        self.assertEqual(mock.images["pl-PL"]["phoneScreenshots"], [b"pl 1"])
        self.assertEqual(mock.images["de-DE"]["phoneScreenshots"], [b"alt"])
        self.assertNotIn(f"DELETE {EDITS}/e1/listings/pl-PL/phoneScreenshots", api.calls())
        # In the edit that releases the version.
        self.assertEqual(api.calls()[-1], f"POST {EDITS}/e1:commit")

    def test_testing_tracks_keep_the_listing(self):
        root = self.screenshots({"phone/en/01-schedule.png": b"en 1"})
        mock = PlayMock(images={"en-US": {"phoneScreenshots": [b"old"]}})
        result, api = self.run_play(
            mock, MODE="build", TRACK="beta", TAG="v4.3.0-beta.1", SCREENSHOTS_DIR=root
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"GET {EDITS}/e1/listings", api.calls())
        self.assertEqual(mock.images["en-US"]["phoneScreenshots"], [b"old"])


if __name__ == "__main__":
    unittest.main()
