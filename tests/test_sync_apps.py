"""Tests scripts/sync_apps.py against one mock of the IAM, Play Developer and
GitHub APIs, with fake gh and keytool commands."""

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT, MockApi, PlaySpec

ORG = "SilverNETGroupSGGW"
DEV = "8827645756827128332"
IAM = "/v1/projects/silvernet-infra/serviceAccounts"
USERS = f"/androidpublisher/v3/developers/{DEV}/users"
PERMISSIONS = ["CAN_MANAGE_PUBLIC_APKS", "CAN_MANAGE_TRACK_APKS"]
# What Play stores for a grant of PERMISSIONS: it adds two read-only ones.
IMPLIED = ["CAN_VIEW_NON_FINANCIAL_DATA", "CAN_VIEW_APP_QUALITY"]
STORED = PERMISSIONS + IMPLIED
ROLE = "roles/iam.workloadIdentityUser"
SECRET_NAMES = ["ANDROID_KEY_ALIAS", "ANDROID_KEY_PASSWORD", "ANDROID_STORE_PASSWORD",
                "ANDROID_KEYSTORE_BASE64"]
# Logs its name, argv and stdin, NUL-separated, one call per line; keytool
# writes $STOREPASS into the keystore so the test can see what it was given.
FAKE = r"""#!/bin/bash
name=$(basename "$0")
printf '%s\0' "$name" "$@" >> "$FAKE_LOG"
if [ "$name" = keytool ]; then
  while [ $# -gt 1 ]; do
    [ "$1" = -keystore ] && printf 'keystore:%s' "$STOREPASS" > "$2"
    shift
  done
  printf 'stdin=\n' >> "$FAKE_LOG"
else
  printf 'stdin=%s\n' "$(cat)" >> "$FAKE_LOG"
fi
"""
# support.run_script passes no arguments; this also sets the script's argv.
RUNNER = ("import runpy, sys, time; time.sleep = lambda seconds: None; "
          "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")


def email(repository_id):
    return f"app-{repository_id}@silvernet-infra.iam.gserviceaccount.com"


def member(repository_id):
    return ("principalSet://iam.googleapis.com/projects/389882651708/locations/global/"
            f"workloadIdentityPools/releases/attribute.repository_id/{repository_id}")


def entry(repo, repository_id, package=None):
    app = {"repo": repo, "repository_id": repository_id}
    if package:
        app["play"] = {"package": package}
    return app


# Repository names that do not exist in the org.
ONE = entry("TestAppOne", 1001, "com.example.one")
TWO = entry("TestAppTwo", 1002, "com.example.two")


def refused(status):
    return status, {"error": {"code": status, "message": "Refused"}}


class World:
    """The state of IAM, Play Console and GitHub, answered like their APIs."""

    def __init__(self):
        self.repos = {}  # name -> repository ID
        self.names = {}  # name -> the spelling GitHub returns, when it differs
        self.secrets = {}  # repository -> secret names
        self.env_secrets = {}  # repository -> secret names of its release environment
        self.accounts = {"sync-apps@silvernet-infra.iam.gserviceaccount.com"}
        self.policies = {}  # account -> bindings
        self.users = {}  # Play user -> {grant name's last part: permissions}
        self.developer = {}  # Play user -> account-wide permissions
        self.drafts = set()
        self.fail = {}  # "METHOD path" -> HTTP status
        self.spec = PlaySpec()

    def app(self, app, settled=False, secrets=()):
        self.repos[app["repo"]] = app["repository_id"]
        self.secrets[app["repo"]] = list(secrets)
        if settled:
            account = email(app["repository_id"])
            self.accounts.add(account)
            self.policies[account] = [{"role": ROLE, "members": [member(app["repository_id"])]}]
            self.users[account] = {app["play"]["package"]: list(STORED)}
            self.secrets[app["repo"]] = list(secrets) or ["ANDROID_KEYSTORE_BASE64"]

    def user(self, address):
        user = {"name": f"developers/{DEV}/users/{address}", "email": address,
                "accessState": "ACCESS_GRANTED"}
        if self.developer.get(address):
            user["developerAccountPermissions"] = self.developer[address]
        if self.users[address]:
            user["grants"] = [
                {"name": f"developers/{DEV}/users/{address}/grants/{name}",
                 "packageName": name, "appLevelPermissions": permissions}
                for name, permissions in self.users[address].items()
            ]
        return user

    def __call__(self, request):
        method, path = request.method, request.path
        github = path.startswith("/repos/")
        assert request.headers["Authorization"] == ("Bearer gh" if github else "Bearer google")
        if f"{method} {path}" in self.fail:
            return refused(self.fail[f"{method} {path}"])
        if github:
            return self.github(request, path.split("/")[2:])
        if path == IAM and method == "GET":
            assert request.query["pageSize"] == "100"
            return 200, {"accounts": [
                {"email": a, "name": f"projects/silvernet-infra/serviceAccounts/{a}"}
                for a in sorted(self.accounts)
            ]}
        if path == IAM and method == "POST":
            body = request.json()
            assert set(body) == {"accountId", "serviceAccount"}, body
            account = f"{body['accountId']}@silvernet-infra.iam.gserviceaccount.com"
            if account in self.accounts:
                return refused(409)
            self.accounts.add(account)
            return 200, {"email": account}
        if path.startswith(IAM + "/"):
            account, _, verb = path[len(IAM) + 1:].partition(":")
            if account not in self.accounts:
                return refused(404)
            if method == "POST" and verb == "getIamPolicy":
                policy = {"version": 1, "etag": "etag1"}
                if self.policies.get(account):
                    policy["bindings"] = self.policies[account]
                return 200, policy
            if method == "POST" and verb == "setIamPolicy":
                policy = request.json()["policy"]
                assert set(policy) == {"bindings", "etag"} and policy["etag"] == "etag1", policy
                self.policies[account] = policy["bindings"]
                return 200, policy
            if method == "DELETE" and not verb:
                self.accounts.remove(account)
                self.policies.pop(account, None)
                return 200, {}
        if path == USERS and method == "GET":
            assert request.query == {"pageSize": "-1"}
            return 200, {"users": [self.user(a) for a in sorted(self.users)]}
        if path == USERS and method == "POST":
            body = request.json()
            self.spec.validate(body, "User")
            assert set(body) <= {"name", "email", "developerAccountPermissions"}, body
            assert body["name"] == f"developers/{DEV}/users/{body['email']}", body
            if not body.get("developerAccountPermissions"):
                return 400, {"error": {"code": 400, "message": "No permissions set for this user."}}
            self.users[body["email"]] = {}
            self.developer[body["email"]] = body["developerAccountPermissions"]
            return 200, self.user(body["email"])
        if path.startswith(USERS + "/"):
            address, *rest = path[len(USERS) + 1:].split("/")
            if address not in self.users:
                return refused(404)
            if method == "DELETE" and not rest:
                del self.users[address]
                self.developer.pop(address, None)
                return 200, {}
            if method == "PATCH" and not rest:
                assert request.query == {"updateMask": "developerAccountPermissions"}
                body = request.json()
                self.spec.validate(body, "User")
                if not body["developerAccountPermissions"] and not self.users[address]:
                    return 400, {"error": {"code": 400, "message": "No permissions set for this user."}}
                self.developer[address] = body["developerAccountPermissions"]
                return 200, self.user(address)
            if method == "POST" and rest == ["grants"]:
                body = request.json()
                self.spec.validate(body, "Grant")
                if body["packageName"] in self.drafts:
                    return 404, {"error": {"code": 404, "message": "Package not found."}}
                self.users[address][body["packageName"]] = body["appLevelPermissions"] + IMPLIED
                return 200, body
            if len(rest) == 2 and rest[0] == "grants" and rest[1] in self.users[address]:
                if method == "PATCH":
                    assert request.query == {"updateMask": "appLevelPermissions"}
                    body = request.json()
                    self.spec.validate(body, "Grant")
                    self.users[address][rest[1]] = body["appLevelPermissions"] + IMPLIED
                    return 200, body
                if method == "DELETE":
                    del self.users[address][rest[1]]
                    return 200, {}
        raise AssertionError(f"unexpected request {method} {path}")

    def github(self, request, parts):
        assert request.method == "GET"
        repo = parts[1]
        assert parts[0] == ORG
        if repo not in self.repos:
            return 404, {"message": "Not Found"}
        if len(parts) == 2:
            name = self.names.get(repo, repo)
            return 200, {"id": self.repos[repo], "name": name, "full_name": f"{ORG}/{name}"}
        assert request.query["per_page"] == "100"
        if parts[2:] == ["actions", "secrets"]:
            names = self.secrets.get(repo, [])
        elif parts[2:] == ["environments", "release", "secrets"]:
            if repo not in self.env_secrets:
                return 404, {"message": "Not Found"}
            names = self.env_secrets[repo]
        else:
            raise AssertionError(f"unexpected request GET {request.path}")
        return 200, {"total_count": len(names), "secrets": [{"name": n} for n in names]}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.api = MockApi(self.world)
        self.addCleanup(self.api.close)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        for name in ("gh", "keytool"):
            (self.bin / name).write_text(FAKE)
            (self.bin / name).chmod(0o755)
        self.log = self.dir / "calls"
        self.summary = self.dir / "summary.md"

    def sync(self, registry):
        """Runs the sync on registry; the fake tools' log starts empty."""
        (self.dir / "apps.json").write_text(json.dumps(registry))
        self.log.write_bytes(b"")
        self.api.requests.clear()
        # Only the fake tools and the mock are reachable: no real gh, no real token.
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.dir),
            "GH_CONFIG_DIR": str(self.dir / "gh"),
            "GOOGLE_TOKEN": "google",
            "GH_TOKEN": "gh",
            "IAM_API": self.api.url,
            "PLAY_API": self.api.url,
            "GITHUB_API": self.api.url,
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "FAKE_LOG": str(self.log),
        }
        result = subprocess.run(
            [sys.executable, "-c", RUNNER, str(ROOT / "scripts" / "sync_apps.py"),
             str(self.dir / "apps.json")],
            env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(self.api.errors, [], result.stdout + result.stderr)
        return result

    def tools(self):
        """The fake tools' calls as (argv, stdin)."""
        calls = []
        for line in self.log.read_text().splitlines():
            *argv, stdin = line.split("\0")
            calls.append((argv, stdin.removeprefix("stdin=")))
        return calls

    def mutations(self):
        return [c for c in self.api.calls()
                if not c.startswith("GET ") and not c.endswith(":getIamPolicy")]

    def test_fresh_app(self):
        self.world.app(ONE)
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        account = email(1001)
        self.assertEqual(self.mutations(), [
            f"POST {IAM}",
            f"POST {IAM}/{account}:setIamPolicy",
            f"POST {USERS}",
            f"POST {USERS}/{account}/grants",
            f"PATCH {USERS}/{account}",
        ])
        create = next(r for r in self.api.requests if r.method == "POST" and r.path == IAM)
        self.assertEqual(create.json(), {"accountId": "app-1001", "serviceAccount": {
            "displayName": f"Releases of {ORG}/TestAppOne (apps.yaml: one)"}})
        self.assertEqual(self.world.policies[account], [{"role": ROLE, "members": [member(1001)]}])
        self.assertEqual(set(self.world.users[account]["com.example.one"]), set(STORED))
        self.assertEqual(self.world.developer[account], [])

        calls = self.tools()
        self.assertEqual([argv[0] for argv, _ in calls], ["keytool", "gh", "gh", "gh", "gh"])
        keytool = calls[0][0]
        self.assertIn("-storepass:env", keytool)
        self.assertEqual(keytool[keytool.index("-alias") + 1], "upload")
        self.assertEqual([argv[1:] for argv, _ in calls[1:]],
                         [["secret", "set", n, "--repo", f"{ORG}/TestAppOne"] for n in SECRET_NAMES])
        values = dict(zip(SECRET_NAMES, (stdin for _, stdin in calls[1:])))
        password = values["ANDROID_KEY_PASSWORD"]
        self.assertGreater(len(password), 30)
        self.assertEqual(values["ANDROID_KEY_ALIAS"], "upload")
        self.assertEqual(values["ANDROID_STORE_PASSWORD"], password)
        self.assertEqual(base64.b64decode(values["ANDROID_KEYSTORE_BASE64"]),
                         f"keystore:{password}".encode())
        for argv, _ in calls:
            self.assertFalse(any(password in arg for arg in argv))
        self.assertNotIn(password, result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if password in line],
                         [f"::add-mask::{password}"])
        self.assertIn("one: account created, binding set", result.stdout)
        self.assertIn("keystore created", result.stdout)
        self.assertIn("| one | TestAppOne |", self.summary.read_text())

    def test_second_run_changes_nothing(self):
        self.world.app(ONE)
        self.assertEqual(self.sync({"one": ONE}).returncode, 0)
        self.world.secrets["TestAppOne"] = SECRET_NAMES  # what the fake gh set
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.tools(), [])
        self.assertIn("keystore kept (ANDROID_KEYSTORE_BASE64)", result.stdout)

    def test_existing_keystores_are_kept(self):
        apps = {
            "jks": entry("TestJks", 11, "com.example.jks"),
            "env": entry("TestEnv", 12, "com.example.env"),
            "dni": entry("TestDni", 13, "com.example.dni"),
        }
        self.world.app(apps["jks"], settled=True,
                       secrets=["ANDROID_JKS_BASE64", "KEY_ALIAS", "KEY_PASSWORD", "STORE_PASSWORD"])
        self.world.app(apps["env"], settled=True, secrets=["OTHER"])
        self.world.env_secrets["TestEnv"] = ["ANDROID_KEYSTORE_BASE64", "ANDROID_KEY_ALIAS"]
        self.world.app(apps["dni"], settled=True, secrets=[
            "KEYSTORE_ALIAS", "KEYSTORE_ALIAS_PASSWORD", "KEYSTORE_BASE64", "KEYSTORE_PASSWORD"])
        result = self.sync(apps)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.tools(), [])
        self.assertIn("keystore kept (ANDROID_JKS_BASE64)", result.stdout)
        self.assertIn("keystore kept (ANDROID_KEYSTORE_BASE64)", result.stdout)
        self.assertIn("keystore kept (KEYSTORE_", result.stdout)

    def test_partial_upload_key_warns(self):
        self.world.app(ONE, settled=True, secrets=["ANDROID_KEY_PASSWORD"])
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.tools(), [])
        self.assertIn("::warning::one: upload-key secrets without a keystore", result.stdout)

    def test_grants_are_corrected(self):
        self.world.app(ONE, settled=True)
        account = email(1001)
        self.world.users[account] = {
            "com.example.one": STORED + ["CAN_REPLY_TO_REVIEWS"],
            "com.example.other": ["CAN_MANAGE_TRACK_APKS"],
        }
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.mutations(), [
            f"PATCH {USERS}/{account}/grants/com.example.one",
            f"DELETE {USERS}/{account}/grants/com.example.other",
        ])
        self.assertEqual({k: set(v) for k, v in self.world.users[account].items()},
                         {"com.example.one": set(STORED)})

    def test_draft_app_warns(self):
        self.world.app(ONE)
        self.world.app(TWO, settled=True)
        self.world.secrets["TestAppOne"] = ["ANDROID_KEYSTORE_BASE64"]
        self.world.users[email(1002)] = {}
        self.world.drafts.add("com.example.one")
        result = self.sync({"one": ONE, "two": TWO})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::warning::one: com.example.one has no bundle in Play Console yet", result.stdout)
        self.assertIn("run Sync apps again", result.stdout)
        self.assertEqual(self.api.calls().count(f"POST {USERS}/{email(1001)}/grants"), 1)
        self.assertEqual(set(self.world.users[email(1002)]["com.example.two"]), set(STORED))

    def test_new_user_for_draft_app_is_removed_again(self):
        self.world.app(ONE)
        self.world.secrets["TestAppOne"] = ["ANDROID_KEYSTORE_BASE64"]
        self.world.drafts.add("com.example.one")
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(email(1001), self.world.users)
        self.assertIn("draft, user removed until then", result.stdout)

    def test_leftover_account_wide_permissions_are_removed(self):
        self.world.app(ONE, settled=True)
        self.world.developer[email(1001)] = ["CAN_VIEW_APP_QUALITY_GLOBAL"]
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.mutations(), [f"PATCH {USERS}/{email(1001)}"])
        self.assertEqual(self.world.developer[email(1001)], [])

    def test_play_refusal_fails_and_skips_revocation(self):
        self.world.app(ONE, settled=True)
        self.world.app(TWO, settled=True)
        self.world.users[email(1001)] = {}
        self.world.users[email(1002)] = {}
        self.world.accounts.add(email(999))
        self.world.fail[f"POST {USERS}/{email(1001)}/grants"] = 403
        result = self.sync({"one": ONE, "two": TWO})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::one: Play refused (403)", result.stdout)
        self.assertEqual(set(self.world.users[email(1002)]["com.example.two"]), set(STORED))
        self.assertIn(email(999), self.world.accounts)
        self.assertIn("::warning::revocation skipped", result.stdout)

    def test_new_user_refused_grant_is_removed_again(self):
        self.world.app(ONE, settled=True)
        del self.world.users[email(1001)]
        self.world.fail[f"POST {USERS}/{email(1001)}/grants"] = 403
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::one: Play refused (403)", result.stdout)
        self.assertNotIn(email(1001), self.world.users)
        self.assertIn("user created, refused, user removed", result.stdout)

    def test_repository_id_mismatch(self):
        self.world.app(ONE)
        self.world.repos["TestAppOne"] = 5555
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 1)
        self.assertIn("GitHub says 5555", result.stdout)
        self.assertEqual(sorted(self.api.calls()), sorted([
            f"GET {IAM}", f"GET {USERS}", f"GET /repos/{ORG}/TestAppOne"]))
        self.assertEqual(self.tools(), [])

    def test_repository_name_spelled_otherwise(self):
        self.world.app(ONE)
        self.world.names["TestAppOne"] = "testappone"
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::one: apps.yaml must spell the name as GitHub does: testappone",
                      result.stdout)
        self.assertEqual(self.tools(), [])

    def test_revocation(self):
        self.world.app(ONE, settled=True)
        others = ["sync-apps@silvernet-infra.iam.gserviceaccount.com",
                  "terraform@silvernet-infra.iam.gserviceaccount.com",
                  "github-action-1132232110@sggw-days.iam.gserviceaccount.com",
                  "app-999@other-project.iam.gserviceaccount.com",
                  "lead@example.com"]
        self.world.accounts |= {email(999), others[1]}
        for address in [email(999), *others]:
            self.world.users[address] = {"com.example.old": ["CAN_MANAGE_TRACK_APKS"]}
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.mutations(), [f"DELETE {USERS}/{email(999)}",
                                            f"DELETE {IAM}/{email(999)}"])
        self.assertEqual(set(self.world.users), {email(1001), *others})
        self.assertIn(others[1], self.world.accounts)
        self.assertEqual(self.tools(), [])

    def test_unreadable_secrets_change_nothing(self):
        self.world.app(ONE, settled=True)
        self.world.fail[f"GET /repos/{ORG}/TestAppOne/actions/secrets"] = 403
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::one: HTTP 403", result.stdout)
        self.assertEqual(self.tools(), [])

    def test_play_listing_failure_skips_play(self):
        self.world.app(ONE)
        self.world.accounts.add(email(999))
        self.world.fail[f"GET {USERS}"] = 403
        result = self.sync({"one": ONE})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::listing the Play users failed", result.stdout)
        self.assertEqual(self.mutations(), [f"POST {IAM}", f"POST {IAM}/{email(1001)}:setIamPolicy"])
        self.assertIn(email(999), self.world.accounts)
        self.assertEqual([argv[0] for argv, _ in self.tools()], ["keytool", "gh", "gh", "gh", "gh"])

    def test_invalid_file(self):
        result = self.sync({"Bad Key": {}})
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::apps.yaml:", result.stdout)
        self.assertEqual(self.api.requests, [])

    def test_app_without_play(self):
        web = entry("TestWeb", 21)
        self.world.app(web)
        result = self.sync({"web": web})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertFalse([c for c in self.api.calls() if c.startswith(("POST", f"GET {IAM}/"))])
        self.assertEqual([argv[0] for argv, _ in self.tools()], ["keytool", "gh", "gh", "gh", "gh"])


if __name__ == "__main__":
    unittest.main()
