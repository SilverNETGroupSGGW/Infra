"""Tests scripts/app_store_queue.py against a mock of the GitHub REST API."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import MockApi, run_script

ORG = "SilverNETGroupSGGW"
INFRA = f"{ORG}/Infra"
DISPATCH = f"POST /repos/{INFRA}/actions/workflows/app-store.yml/dispatches"
APPS = {
    "first": {"repo": "First", "repository_id": 11,
              "app_store": {"bundle": "com.example.first", "apple_id": 101}},
    "second": {"repo": "Second", "repository_id": 22,
               "app_store": {"bundle": "com.example.second", "apple_id": 202}},
    "android": {"repo": "Android", "repository_id": 33,
                "play": {"package": "com.example.android"}},
}


class GitHubMock:
    """Answers like the GitHub REST API for the given repositories, runs and
    artifacts."""

    def __init__(self):
        self.ids = {"First": 11, "Second": 22, "Android": 33}
        self.names = {}  # repo -> full_name, when GitHub spells it otherwise
        self.runs = {"First": [], "Second": []}  # None: no release.yml
        self.artifacts = {}  # run ID -> artifacts
        self.infra_titles = []
        self.broken = set()

    def add_run(self, repo, run_id, created, expired=False, name="app-store-request"):
        self.runs[repo].append({"id": run_id, "created_at": created})
        self.artifacts[run_id] = [{"name": name, "expired": expired}]

    def __call__(self, request):
        parts = request.path.strip("/").split("/")
        if request.path == f"/repos/{INFRA}/actions/workflows/app-store.yml/runs":
            assert request.query["event"] == "workflow_dispatch", request.query
            assert request.query["created"].startswith(">="), request.query
            assert request.headers["Authorization"] == "Bearer infra-token"
            return 200, {"workflow_runs": [{"display_title": t} for t in self.infra_titles]}
        if request.method == "POST" and f"{request.method} {request.path}" == DISPATCH:
            assert request.headers["Authorization"] == "Bearer infra-token"
            return 204, None
        assert request.headers["Authorization"] == "Bearer app-token", request.path
        repo = parts[2]
        if repo not in self.ids:
            return 404, {"message": "Not Found"}
        if repo in self.broken:
            return 500, {"message": "Server Error"}
        if len(parts) == 3:
            return 200, {"id": self.ids[repo],
                         "full_name": self.names.get(repo, f"{ORG}/{repo}")}
        if parts[3:] == ["actions", "workflows", "release.yml", "runs"]:
            assert request.query["created"].startswith(">="), request.query
            if self.runs[repo] is None:
                return 404, {"message": "Not Found"}
            return 200, {"workflow_runs": self.runs[repo]}
        if parts[3:5] == ["actions", "runs"] and parts[6] == "artifacts":
            return 200, {"artifacts": self.artifacts.get(int(parts[5]), [])}
        raise AssertionError(f"unexpected {request}")


class QueueTest(unittest.TestCase):
    def run_queue(self, mock):
        api = MockApi(mock)
        self.addCleanup(api.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        apps_file = Path(directory.name) / "apps.json"
        apps_file.write_text(json.dumps(APPS))
        env = dict(os.environ, GITHUB_API=api.url, APPS_FILE=str(apps_file),
                   APP_TOKEN="app-token", GH_TOKEN="infra-token", INFRA_REPOSITORY=INFRA)
        result = run_script("app_store_queue.py", env)
        self.assertEqual(api.errors, [], result.stdout + result.stderr)
        dispatches = [r.json() for r in api.requests
                      if f"{r.method} {r.path}" == DISPATCH]
        return result, dispatches, api

    def test_new_request_is_dispatched_oldest_first(self):
        mock = GitHubMock()
        mock.add_run("Second", 7, "2026-11-25T10:00:00Z")
        mock.add_run("First", 5, "2026-11-25T11:00:00Z")
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dispatches, [
            {"ref": "main", "inputs": {"repository": f"{ORG}/Second", "run-id": "7"}},
            {"ref": "main", "inputs": {"repository": f"{ORG}/First", "run-id": "5"}},
        ])
        self.assertIn(f"Dispatched the App Store request of {ORG}/Second run 7.",
                      result.stdout)

    def test_request_with_an_infra_run_is_not_dispatched_again(self):
        mock = GitHubMock()
        mock.add_run("First", 5, "2026-11-25T11:00:00Z")
        mock.infra_titles = [f"App Store {ORG}/First run 5", "Find App Store requests"]
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dispatches, [])

    def test_missing_or_expired_request_is_skipped(self):
        mock = GitHubMock()
        mock.add_run("First", 5, "2026-11-25T11:00:00Z", expired=True)
        mock.add_run("First", 6, "2026-11-25T12:00:00Z", name="ios-archive")
        mock.runs["First"].append({"id": 8, "created_at": "2026-11-25T13:00:00Z"})
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dispatches, [])

    def test_repository_id_mismatch_is_skipped_with_a_warning(self):
        mock = GitHubMock()
        mock.ids["First"] = 99
        mock.add_run("First", 5, "2026-11-25T11:00:00Z")
        result, dispatches, api = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dispatches, [])
        self.assertIn(f"::warning::{ORG}/First has the ID 99; apps.yaml has {ORG}/First with 11.",
                      result.stdout)
        self.assertNotIn(f"GET /repos/{ORG}/First/actions/workflows/release.yml/runs",
                         api.calls())

    def test_renamed_repository_is_skipped_with_a_warning(self):
        mock = GitHubMock()
        mock.names["First"] = f"{ORG}/first"
        mock.add_run("First", 5, "2026-11-25T11:00:00Z")
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dispatches, [])
        self.assertIn(f"::warning::{ORG}/first has the ID 11", result.stdout)

    def test_missing_repository_is_skipped_with_a_warning(self):
        mock = GitHubMock()
        del mock.ids["First"]
        mock.add_run("Second", 7, "2026-11-25T10:00:00Z")
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"::warning::{ORG}/First not found", result.stdout)
        self.assertEqual(dispatches, [
            {"ref": "main", "inputs": {"repository": f"{ORG}/Second", "run-id": "7"}},
        ])

    def test_one_failing_app_does_not_stop_the_others(self):
        mock = GitHubMock()
        mock.broken.add("First")
        mock.add_run("Second", 7, "2026-11-25T10:00:00Z")
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::First:", result.stdout)
        self.assertEqual(dispatches, [
            {"ref": "main", "inputs": {"repository": f"{ORG}/Second", "run-id": "7"}},
        ])

    def test_app_without_app_store_is_ignored(self):
        result, _, api = self.run_queue(GitHubMock())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any("/Android" in call for call in api.calls()), api.calls())

    def test_app_without_release_workflow_is_skipped_silently(self):
        mock = GitHubMock()
        mock.runs["First"] = None
        mock.runs["Second"] = None
        result, dispatches, _ = self.run_queue(mock)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertEqual(dispatches, [])

    def test_invalid_registry_fails(self):
        mock = GitHubMock()
        api = MockApi(mock)
        self.addCleanup(api.close)
        with tempfile.TemporaryDirectory() as directory:
            apps_file = Path(directory) / "apps.json"
            apps_file.write_text(json.dumps({"first": {"repo": "First"}}))
            env = dict(os.environ, GITHUB_API=api.url, APPS_FILE=str(apps_file),
                       APP_TOKEN="app-token", GH_TOKEN="infra-token",
                       INFRA_REPOSITORY=INFRA)
            result = run_script("app_store_queue.py", env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::first: repository_id", result.stdout)
        self.assertEqual(api.requests, [])


if __name__ == "__main__":
    unittest.main()
