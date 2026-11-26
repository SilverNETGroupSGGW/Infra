"""Finds the App Store requests that the apps' release runs left (the
app-store-request artifact) and dispatches app-store.yml once for each.
Run by app-store.yml's find job with its settings in environment variables."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import apps

API = os.environ.get("GITHUB_API", "https://api.github.com")
WORKFLOW = "app-store.yml"
REQUEST = "app-store-request"
# Requests live 3 days (the artifact's retention); Infra runs are read a day
# further back, so a request dispatched near its end is not dispatched again.
REQUEST_DAYS = 3
RUN_DAYS = 4


class NotFound(Exception):
    pass


def call(method, path, token, query=None, body=None):
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NotFound(path) from None
        text = error.read().decode(errors="replace")[:500]
        raise RuntimeError(f"{method} {path}: HTTP {error.code} {text}") from None
    return json.loads(data) if data else None


def listing(path, token, key, query):
    """Every item of a paginated GitHub listing."""
    items, page = [], 1
    while True:
        batch = call("GET", path, token, dict(query, per_page=100, page=page))[key]
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def since(days):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    return ">=" + start.strftime("%Y-%m-%dT%H:%M:%SZ")


def requests(app, token):
    """The app's release runs with a live request, as (created_at, run ID)."""
    repo = f"{apps.ORG}/{app['repo']}"
    try:
        found = call("GET", f"/repos/{repo}", token)
    except NotFound:
        # A warning, not a failure: this runs every 10 minutes.
        print(f"::warning::{repo} not found, or the publisher App is not installed on it. "
              "Skipping it.")
        return []
    # The run titles use the name from apps.yaml, which the app's run must match.
    if (found["id"], found["full_name"]) != (app["repository_id"], repo):
        print(f"::warning::{found['full_name']} has the ID {found['id']}; apps.yaml has "
              f"{repo} with {app['repository_id']}. Skipping it.")
        return []
    try:
        runs = listing(f"/repos/{repo}/actions/workflows/release.yml/runs", token,
                       "workflow_runs", {"created": since(REQUEST_DAYS)})
    except NotFound:
        return []  # no release.yml yet: nothing to publish
    result = []
    for run in runs:
        artifacts = call("GET", f"/repos/{repo}/actions/runs/{run['id']}/artifacts", token,
                         {"name": REQUEST})["artifacts"]
        if any(a["name"] == REQUEST and not a["expired"] for a in artifacts):
            result.append((run["created_at"], repo, run["id"]))
    return result


def main():
    registry = apps.load(os.environ["APPS_FILE"])
    infra = os.environ["INFRA_REPOSITORY"]
    infra_token = os.environ["GH_TOKEN"]
    app_token = os.environ["APP_TOKEN"]
    taken = {
        run["display_title"]
        for run in listing(f"/repos/{infra}/actions/workflows/{WORKFLOW}/runs", infra_token,
                           "workflow_runs",
                           {"event": "workflow_dispatch", "created": since(RUN_DAYS)})
    }
    failed, pending = False, []
    for app in registry.values():
        if not app["bundle"]:
            continue
        try:
            pending += requests(app, app_token)
        except (NotFound, RuntimeError, OSError, KeyError, ValueError) as error:
            print(f"::error::{app['repo']}: {error}")
            failed = True
    for _, repo, run_id in sorted(pending):
        if f"App Store {repo} run {run_id}" in taken:
            continue
        try:
            call("POST", f"/repos/{infra}/actions/workflows/{WORKFLOW}/dispatches",
                 infra_token, body={"ref": "main",
                                    "inputs": {"repository": repo, "run-id": str(run_id)}})
        except (NotFound, RuntimeError, OSError) as error:
            print(f"::error::Dispatching {repo} run {run_id}: {error}")
            failed = True
            continue
        print(f"Dispatched the App Store request of {repo} run {run_id}.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (NotFound, RuntimeError, OSError, ValueError) as error:
        for line in str(error).splitlines():
            print(f"::error::{line}")
        sys.exit(1)
