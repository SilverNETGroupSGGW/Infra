"""Tests scripts/apps.py, which validates apps.yaml and looks apps up in it."""

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

spec = importlib.util.spec_from_file_location("apps", ROOT / "scripts" / "apps.py")
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)

VALID = {
    "dni_sggw": {
        "repo": "sggw_days",
        "repository_id": 1132232110,
        "play": {"package": "com.silvernet.sggw_days"},
        "app_store": {"bundle": "com.silver.sggw-days", "apple_id": 6767240418},
    },
    "web_only": {"repo": "Bolero", "repository_id": 42},
    "ios_only": {
        "repo": "Other.App",
        "repository_id": 7,
        "app_store": {"bundle": "com.example.other", "apple_id": "123"},
    },
}


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "apps.py"), *args],
                          capture_output=True, text=True, timeout=30)


def outputs(stdout):
    return dict(line.split("=", 1) for line in stdout.splitlines())


class ParseTest(unittest.TestCase):
    def assertProblem(self, data, *messages):
        with self.assertRaises(ValueError) as caught:
            apps.parse(data)
        for message in messages:
            self.assertIn(message, str(caught.exception))

    def changed(self, change):
        data = copy.deepcopy(VALID)
        change(data)
        return data

    def test_valid(self):
        result = apps.parse(VALID)
        self.assertEqual(result["dni_sggw"], {
            "key": "dni_sggw",
            "repo": "sggw_days",
            "repository_id": 1132232110,
            "package": "com.silvernet.sggw_days",
            "bundle": "com.silver.sggw-days",
            "apple_id": "6767240418",
        })
        self.assertEqual(result["web_only"]["package"], None)
        self.assertEqual(result["web_only"]["bundle"], None)
        self.assertEqual(result["ios_only"]["apple_id"], "123")
        self.assertEqual(list(result), ["dni_sggw", "web_only", "ios_only"])

    def test_empty(self):
        self.assertEqual(apps.parse({}), {})
        self.assertProblem(None, "write {} for no apps")
        self.assertProblem([], "top level must be a mapping")

    def test_keys(self):
        for key in ("Dni", "1app", "dni-sggw", "a" * 41, ""):
            with self.subTest(key=key):
                self.assertProblem({key: VALID["web_only"]}, f"{key}: keys must match")
        self.assertEqual(list(apps.parse({"a" * 40: VALID["web_only"]})), ["a" * 40])

    def test_unknown_fields(self):
        self.assertProblem(self.changed(lambda d: d["web_only"].update(owner="x")),
                           "web_only: unknown field owner")
        self.assertProblem(self.changed(lambda d: d["dni_sggw"]["play"].update(track="beta")),
                           "dni_sggw.play: unknown field track")
        self.assertProblem(self.changed(lambda d: d["dni_sggw"]["app_store"].update(team="x")),
                           "dni_sggw.app_store: unknown field team")

    def test_entry_not_a_mapping(self):
        self.assertProblem({"app": "sggw_days"}, "app: must be a mapping")
        self.assertProblem(self.changed(lambda d: d["dni_sggw"].update(play="com.a.b")),
                           "dni_sggw.play: must be a mapping")

    def test_repo(self):
        for repo in (None, "", "a/b", "a b", 5, "a" * 101):
            with self.subTest(repo=repo):
                self.assertProblem(self.changed(lambda d: d["web_only"].update(repo=repo)),
                                   "web_only: repo must be")
        self.assertProblem(self.changed(lambda d: d["web_only"].pop("repo")),
                           "web_only: repo must be")

    def test_repository_id(self):
        for value in (None, 0, -1, True, "42", 4.2):
            with self.subTest(value=value):
                self.assertProblem(
                    self.changed(lambda d: d["web_only"].update(repository_id=value)),
                    "web_only: repository_id must be")
        self.assertProblem(self.changed(lambda d: d["web_only"].pop("repository_id")),
                           "web_only: repository_id must be")

    def test_package(self):
        for play in ({}, None, {"package": "app"}, {"package": "com.1app"},
                     {"package": "com.app-x"}, {"package": 5}):
            with self.subTest(play=play):
                self.assertProblem(self.changed(lambda d: d["dni_sggw"].update(play=play)),
                                   "dni_sggw.play: package must be")

    def test_app_store(self):
        for store, message in (
            ({"apple_id": 1}, "bundle must be"),
            ({"bundle": "com.example.app"}, "apple_id must be"),
            ({"bundle": "app", "apple_id": 1}, "bundle must be"),
            ({"bundle": "com.example_app.x", "apple_id": 1}, "bundle must be"),
            ({"bundle": "com.example.app", "apple_id": 0}, "apple_id must be"),
            ({"bundle": "com.example.app", "apple_id": True}, "apple_id must be"),
            ({"bundle": "com.example.app", "apple_id": "12a"}, "apple_id must be"),
            ({"bundle": "com.example.app", "apple_id": "012"}, "apple_id must be"),
            (None, "bundle must be"),
        ):
            with self.subTest(store=store):
                self.assertProblem(
                    self.changed(lambda d: d["dni_sggw"].update(app_store=store)),
                    f"dni_sggw.app_store: {message}")

    def test_duplicates(self):
        for change, message in (
            (lambda d: d["web_only"].update(repo="SGGW_DAYS"),
             "web_only: repo SGGW_DAYS is also used by dni_sggw"),
            (lambda d: d["web_only"].update(repository_id=1132232110),
             "web_only: repository_id 1132232110 is also used by dni_sggw"),
            (lambda d: d["web_only"].update(play={"package": "com.silvernet.sggw_days"}),
             "web_only: package com.silvernet.sggw_days is also used by dni_sggw"),
            (lambda d: d["ios_only"]["app_store"].update(bundle="com.silver.sggw-days"),
             "ios_only: bundle com.silver.sggw-days is also used by dni_sggw"),
            (lambda d: d["ios_only"]["app_store"].update(apple_id="6767240418"),
             "ios_only: apple_id 6767240418 is also used by dni_sggw"),
        ):
            with self.subTest(message=message):
                self.assertProblem(self.changed(change), message)

    def test_lists_every_problem(self):
        data = self.changed(lambda d: (d["web_only"].pop("repo"),
                                       d["ios_only"].update(repository_id=0)))
        self.assertProblem(data, "web_only: repo must be", "ios_only: repository_id must be")


class CliTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.file = Path(directory.name) / "apps.json"
        self.file.write_text(json.dumps(VALID))

    def test_check(self):
        result = run("check", str(self.file))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_check_invalid(self):
        self.file.write_text(json.dumps({"web_only": {"repo": "a/b", "repository_id": 0}}))
        result = run("check", str(self.file))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.splitlines(), [
            "::error::apps.yaml: web_only: repo must be a repository name in SilverNETGroupSGGW",
            "::error::apps.yaml: web_only: repository_id must be the repository's numeric ID",
        ])

    def test_check_null(self):
        self.file.write_text("null")
        result = run("check", str(self.file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::apps.yaml: the file is empty: write {} for no apps",
                      result.stderr)

    def test_lookup_by_id(self):
        result = run("lookup", str(self.file), "--repository-id", "1132232110")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(outputs(result.stdout), {
            "found": "true",
            "key": "dni_sggw",
            "repo": "sggw_days",
            "repository-id": "1132232110",
            "android-package": "com.silvernet.sggw_days",
            "bundle-id": "com.silver.sggw-days",
            "apple-app-id": "6767240418",
        })

    def test_lookup_by_name(self):
        result = run("lookup", str(self.file), "--repo", "bolero")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(outputs(result.stdout), {
            "found": "true",
            "key": "web_only",
            "repo": "Bolero",
            "repository-id": "42",
            "android-package": "",
            "bundle-id": "",
            "apple-app-id": "",
        })

    def test_lookup_not_found(self):
        for args in (["--repository-id", "99"], ["--repo", "Infra"]):
            with self.subTest(args=args):
                result = run("lookup", str(self.file), *args)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(outputs(result.stdout), {
                    "found": "false", "key": "", "repo": "", "repository-id": "",
                    "android-package": "", "bundle-id": "", "apple-app-id": "",
                })

    def test_check_repeated_key(self):
        self.file.write_text('{"web_only": {"repo": "a", "repository_id": 1},'
                             ' "web_only": {"repo": "b", "repository_id": 2}}')
        result = run("check", str(self.file))
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::apps.yaml: web_only: the key appears more than once",
                      result.stderr)

    def test_lookup_invalid_file(self):
        self.file.write_text("{")
        result = run("lookup", str(self.file), "--repo", "Bolero")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("::error::apps.yaml:", result.stderr)


@unittest.skipUnless(shutil.which("yq"), "yq is not on PATH")
class RegistryTest(unittest.TestCase):
    def test_checked_in_file(self):
        # cwd: the mise shim finds yq's version only inside the repository.
        converted = subprocess.run(["yq", "-o=json", "apps.yaml"], cwd=ROOT,
                                   capture_output=True, text=True, check=True, timeout=30)
        registry = apps.parse(json.loads(converted.stdout))
        self.assertEqual(list(registry), ["plan_wzim", "dni_sggw", "bingownia", "studentou"])


if __name__ == "__main__":
    unittest.main()
