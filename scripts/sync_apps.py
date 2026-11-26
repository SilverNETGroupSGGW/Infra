"""Applies apps.yaml: for each app with a `play` section, a Google service
account that only its own repository's release runs can use, with access to its
one Play Console package; for every app, an upload keystore in its secrets when
it has none. Removes the accounts and Play users of apps no longer in the file.
Never overwrites, deletes or prints a secret. Run by sync-apps.yml
(docs/apps.md).

Usage: sync_apps.py APPS_JSON (what `yq -o=json apps.yaml` writes)"""

import base64
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apps  # noqa: E402

ORG = apps.ORG
PROJECT = "silvernet-infra"
PROJECT_NUMBER = "389882651708"
POOL = "releases"
DEVELOPER = "8827645756827128332"
PERMISSIONS = ["CAN_MANAGE_PUBLIC_APKS", "CAN_MANAGE_TRACK_APKS"]
# Play adds these read-only permissions to every grant by itself.
IMPLIED = {"CAN_VIEW_NON_FINANCIAL_DATA", "CAN_VIEW_APP_QUALITY"}
# Play refuses to create a user without any permission, and a new user has no
# grant yet, so it starts with this read-only one; the sync removes it once the
# grant exists.
STARTER = ["CAN_VIEW_APP_QUALITY_GLOBAL"]
ROLE = "roles/iam.workloadIdentityUser"
ACCOUNT = re.compile(r"^app-(\d+)@silvernet-infra\.iam\.gserviceaccount\.com$")
KEYSTORE = re.compile(r"KEYSTORE|JKS")
COMPANION = re.compile(r"KEY_ALIAS|KEY_PASSWORD|STORE_PASSWORD")
IAM = os.environ.get("IAM_API", "https://iam.googleapis.com") + f"/v1/projects/{PROJECT}/serviceAccounts"
PLAY = os.environ.get("PLAY_API", "https://androidpublisher.googleapis.com") + "/androidpublisher/v3"
USERS = f"{PLAY}/developers/{DEVELOPER}/users"
GITHUB = os.environ.get("GITHUB_API", "https://api.github.com")
# For an account created in this run, which IAM and Play may not see for a while.
PROPAGATING = {"retries": 5, "retry_on": (400, 404)}
SYNC_AGAIN = "run Sync apps again (Actions → Sync apps → Run workflow)"


def email(repository_id):
    return f"app-{repository_id}@{PROJECT}.iam.gserviceaccount.com"


def member(repository_id):
    return (f"principalSet://iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/"
            f"workloadIdentityPools/{POOL}/attribute.repository_id/{repository_id}")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def error_message(text):
    try:
        body = json.loads(text)
        return (body.get("error") or {}).get("message") or body.get("message") or text
    except (ValueError, AttributeError):
        return text


def call(method, url, token, body=None, retries=4, retry_on=()):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            message = error_message(error.read().decode(errors="replace"))
            if attempt < retries and error.code in retry_on:
                time.sleep(10)
                continue
            if attempt < retries and error.code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 5 * 2**attempt))
                continue
            raise ApiError(error.code, message) from None
        except (OSError, http.client.HTTPException):
            # Only reads are safe to send again after a lost answer.
            if method != "GET" or attempt == retries:
                raise
            time.sleep(min(60, 5 * 2**attempt))


def listing(url, token, field):
    """Every item of a Google list call, following nextPageToken."""
    items, page = [], ""
    while True:
        answer = call("GET", url + (f"&pageToken={urllib.parse.quote(page)}" if page else ""), token)
        items += answer.get(field, [])
        page = answer.get("nextPageToken")
        if not page:
            return items


class Sync:
    def __init__(self, google, github):
        self.google, self.github = google, github
        self.errors, self.warnings, self.rows, self.revoked = [], [], [], []

    def error(self, key, message):
        self.errors.append(f"{key}: {message}" if key else message)
        print(f"::error::{self.errors[-1]}", flush=True)

    def warn(self, key, message):
        self.warnings.append(f"{key}: {message}" if key else message)
        print(f"::warning::{self.warnings[-1]}", flush=True)

    def gh(self, path):
        return call("GET", GITHUB + path, self.github)

    def secret_names(self, path):
        names, page = [], 1
        while True:
            batch = self.gh(f"{path}?per_page=100&page={page}")["secrets"]
            names += [secret["name"] for secret in batch]
            if len(batch) < 100:
                return names
            page += 1

    def repository(self, app):
        """The repository's full name, or None when it is not the one in apps.yaml."""
        try:
            data = self.gh(f"/repos/{ORG}/{app['repo']}")
        except ApiError as error:
            if error.status != 404:
                raise
            self.error(app["key"], f"{ORG}/{app['repo']} not found, or the sync App is not installed on it")
            return None
        if data["id"] != app["repository_id"]:
            self.error(app["key"], f"apps.yaml says {app['repo']} is {app['repository_id']}, GitHub says "
                       f"{data['id']}: a renamed or replaced repository; fix the entry")
            return None
        if data["name"] != app["repo"]:
            # The App Store publisher compares names exactly.
            self.error(app["key"], f"apps.yaml must spell the name as GitHub does: {data['name']}")
            return None
        return data["full_name"]

    def account(self, app, row):
        """Creates the app's account and sets its one binding; returns whether it
        was created now, or None when IAM does not see it yet."""
        address, repository_id = email(app["repository_id"]), app["repository_id"]
        created = False
        if self.accounts is None or address not in self.accounts:
            name = f"Releases of {ORG}/{app['repo']} (apps.yaml: {app['key']})"
            try:
                call("POST", IAM, self.google, {"accountId": f"app-{repository_id}",
                                                "serviceAccount": {"displayName": name[:100]}})
                created = True
            except ApiError as error:
                if error.status != 409:  # it exists, though the listing did not show it
                    raise
        row["account"] = "created" if created else "ok"
        try:
            policy = call("POST", f"{IAM}/{address}:getIamPolicy", self.google, {},
                          **(PROPAGATING if created else {}))
        except ApiError as error:
            if not (created and error.status in (400, 404)):
                raise
            self.warn(app["key"], f"new account not visible to IAM yet; {SYNC_AGAIN} in a few minutes")
            return None
        want = [{"role": ROLE, "members": [member(repository_id)]}]
        bindings = [dict(b, members=sorted(b.get("members", []))) for b in policy.get("bindings", [])]
        if bindings == want:
            row["binding"] = "ok"
        else:
            call("POST", f"{IAM}/{address}:setIamPolicy", self.google,
                 {"policy": {"bindings": want, "etag": policy.get("etag")}},
                 **(PROPAGATING if created else {}))
            row["binding"] = "set"
        return created

    def play(self, app, row, created):
        address, package, done = email(app["repository_id"]), app["package"], []
        try:
            user = self.users.get(address)
            if user is None:
                try:
                    user = call("POST", USERS, self.google,
                                {"name": f"developers/{DEVELOPER}/users/{address}", "email": address,
                                 "developerAccountPermissions": STARTER},
                                **(PROPAGATING if created else {}))
                except ApiError as error:
                    if not (created and error.status in (400, 404)):
                        raise
                    self.warn(app["key"], f"new account not visible to Play yet; {SYNC_AGAIN} in a few minutes")
                    row["play"] = "waiting"
                    return
                done.append("user created")
            # Keyed by the grant name's last part: the package, or a draft app's ID.
            grants = {g["name"].rsplit("/", 1)[1]: g for g in user.get("grants", [])}
            grant = grants.pop(package, None)
            if grant is None:
                try:
                    call("POST", f"{USERS}/{address}/grants", self.google,
                         {"packageName": package, "appLevelPermissions": PERMISSIONS})
                    done.append("granted")
                except ApiError as error:
                    if not (error.status == 404 or error.status == 400 and (
                            package in error.message or "not found" in error.message.lower())):
                        raise
                    self.warn(app["key"], f"{package} has no bundle in Play Console yet (draft app): the "
                              f"lead uploads the first bundle by hand, then {SYNC_AGAIN} to grant access")
                    done.append("draft")
                    if "user created" in done:
                        # Without a grant the user would keep only the starter permission.
                        call("DELETE", f"{USERS}/{address}", self.google)
                        done.append("user removed until then")
            elif set(grant.get("appLevelPermissions", [])) - IMPLIED != set(PERMISSIONS):
                call("PATCH", f"{USERS}/{address}/grants/{package}?updateMask=appLevelPermissions",
                     self.google, {"appLevelPermissions": PERMISSIONS})
                done.append("updated")
            for other, stray in grants.items():
                call("DELETE", f"{PLAY}/{stray['name']}", self.google)
                done.append(f"grant on {other} removed")
            # A user without any grant cannot lose its last permission.
            if user.get("developerAccountPermissions") and "draft" not in done:
                call("PATCH", f"{USERS}/{address}?updateMask=developerAccountPermissions", self.google,
                     {"developerAccountPermissions": []})
                done.append("account-wide permissions removed")
        except ApiError as error:
            if error.status != 403:
                raise
            self.error(app["key"], f"Play refused (403): invite {address} in Play Console → Users and "
                       "permissions with Release to testing tracks and Release to production on "
                       f"{package} only, or check that sync-apps@ is an Admin there")
            done.append("refused")
        row["play"] = ", ".join(done) or "ok"

    def keystore(self, app, row, repository):
        names = self.secret_names(f"/repos/{repository}/actions/secrets")
        try:
            names += self.secret_names(f"/repos/{repository}/environments/release/secrets")
        except ApiError as error:
            if error.status != 404:  # 404: no release environment
                raise
        kept = sorted(name for name in names if KEYSTORE.search(name))
        if kept:
            row["keystore"] = f"kept ({', '.join(kept)})"
            return
        if any(COMPANION.search(name) for name in names):
            self.warn(app["key"], "upload-key secrets without a keystore; nothing changed: delete them "
                      "to let the sync create a new key, or add ANDROID_KEYSTORE_BASE64")
            row["keystore"] = "incomplete"
            return
        password = secrets.token_urlsafe(32)
        print(f"::add-mask::{password}", flush=True)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "key.jks")
            subprocess.run(
                ["keytool", "-genkeypair", "-noprompt", "-keystore", path, "-storetype", "PKCS12",
                 "-alias", "upload", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                 "-dname", f"CN={app['key']}, O=KN Silver .NET",
                 "-storepass:env", "STOREPASS", "-keypass:env", "STOREPASS"],
                env={**os.environ, "STOREPASS": password}, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, check=True,
            )
            with open(path, "rb") as file:
                encoded = base64.b64encode(file.read()).decode()
        # The keystore goes last, so an interrupted run leaves none (the next run warns).
        for name, value in (("ANDROID_KEY_ALIAS", "upload"), ("ANDROID_KEY_PASSWORD", password),
                            ("ANDROID_STORE_PASSWORD", password), ("ANDROID_KEYSTORE_BASE64", encoded)):
            subprocess.run(["gh", "secret", "set", name, "--repo", repository],
                           input=value, capture_output=True, text=True, check=True)
        row["keystore"] = "created"

    def revoke(self, desired):
        """Deletes the Play users and accounts of apps no longer in the file,
        and nothing that is not an app-<repository ID> account."""
        wanted = {email(repository_id) for repository_id in desired}
        for address in sorted((set(self.users) | self.accounts) - wanted):
            if not ACCOUNT.match(address):
                continue
            try:
                # The Play user first: a deleted account cannot be removed from Play by email later.
                if address in self.users:
                    call("DELETE", f"{USERS}/{address}", self.google)
                    self.revoked.append(f"{address}: Play access removed")
                    print(self.revoked[-1])
                if address in self.accounts:
                    call("DELETE", f"{IAM}/{address}", self.google)
                    self.revoked.append(f"{address}: account deleted")
                    print(self.revoked[-1])
            except (ApiError, OSError, http.client.HTTPException) as error:
                self.error(None, f"revoking {address}: {error}")

    def read_listings(self):
        self.accounts = self.users = None
        try:
            self.accounts = {a["email"] for a in listing(f"{IAM}?pageSize=100", self.google, "accounts")}
        except (ApiError, OSError, http.client.HTTPException) as error:
            self.error(None, f"listing the service accounts failed: {error}")
        try:
            self.users = {u["email"]: u for u in listing(f"{USERS}?pageSize=-1", self.google, "users")}
        except (ApiError, OSError, http.client.HTTPException) as error:
            self.error(None, f"listing the Play users failed ({error}); is sync-apps@ an Admin in Play Console?")
        return self.accounts is not None and self.users is not None

    def run(self, registry):
        listed = self.read_listings()
        for key in sorted(registry):
            app = registry[key]
            row = {"account": "-", "binding": "-", "play": "-", "keystore": "-"}
            self.rows.append((app, row))
            errors, warnings = len(self.errors), len(self.warnings)
            try:
                repository = self.repository(app)
                if repository is None:
                    continue
                if app["package"]:
                    created = self.account(app, row)
                    if not listed:
                        row["play"] = "skipped"
                    elif created is not None:
                        self.play(app, row, created)
                self.keystore(app, row, repository)
            except subprocess.CalledProcessError as error:
                self.error(key, f"{' '.join(error.cmd[:3])} failed: {(error.stderr or '').strip()}")
            except (ApiError, OSError, http.client.HTTPException) as error:
                self.error(key, str(error))
            finally:
                row["notes"] = len(self.errors) - errors, len(self.warnings) - warnings
        if listed and not self.errors:
            self.revoke({app["repository_id"] for app in registry.values() if app["package"]})
        else:
            self.warn(None, "revocation skipped: fix the errors above first")
        self.report()
        return 1 if self.errors else 0

    def report(self):
        lines = ["## Sync apps", "", "| App | Repository | Account | Play | Keystore | Notes |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for app, row in self.rows:
            print(f"{app['key']}: account {row['account']}, binding {row['binding']}, "
                  f"play {row['play']}, keystore {row['keystore']}")
            errors, warnings = row["notes"]
            notes = ", ".join(f"{n} {kind}{'s' if n > 1 else ''}"
                              for n, kind in ((errors, "error"), (warnings, "warning")) if n)
            lines.append(f"| {app['key']} | {app['repo']} | {row['account']}, binding {row['binding']} "
                         f"| {row['play']} | {row['keystore']} | {notes} |")
        lines += [""] + [f"- {line}" for line in self.revoked]
        lines += [f"- Error: {line}" for line in self.errors]
        lines += [f"- Warning: {line}" for line in self.warnings]
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as file:
                file.write("\n".join(lines) + "\n")


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    try:
        registry = apps.load(argv[0])
    except (OSError, ValueError) as error:
        for line in str(error).splitlines():
            print(f"::error::apps.yaml: {line}")
        return 1
    return Sync(os.environ["GOOGLE_TOKEN"], os.environ["GH_TOKEN"]).run(registry)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
