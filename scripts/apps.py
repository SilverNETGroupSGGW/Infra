"""Reads apps.yaml, the registry of the apps that may publish to the stores,
from the JSON that `yq -o=json apps.yaml` writes. The workflows and the other
scripts read the registry only through this module.

Usage:
  apps.py check FILE
  apps.py lookup FILE (--repository-id N | --repo NAME)"""

import argparse
import json
import re
import sys

ORG = "SilverNETGroupSGGW"
KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}")
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
BUNDLE = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
APPLE_ID = re.compile(r"[1-9][0-9]*")
FIELDS = {"repo", "repository_id", "play", "app_store"}


def positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def section(name, value, fields, problems):
    """Returns the section as a dict (empty when null) after checking its fields."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.append(f"{name}: must be a mapping")
        return {}
    problems.extend(f"{name}: unknown field {field}" for field in sorted(value.keys() - fields))
    return value


def parse(data):
    """Validates the registry and returns {key: app}; raises ValueError listing
    every problem, one per line."""
    if data is None:
        raise ValueError("the file is empty: write {} for no apps")
    if not isinstance(data, dict):
        raise ValueError("the top level must be a mapping of app keys to entries")
    problems, result = [], {}
    for key, entry in data.items():
        if not KEY.fullmatch(key):
            problems.append(f"{key}: keys must match {KEY.pattern}")
        if not isinstance(entry, dict):
            problems.append(f"{key}: must be a mapping")
            continue
        problems.extend(f"{key}: unknown field {field}" for field in sorted(entry.keys() - FIELDS))
        # Invalid values become None, so the duplicate checks below see only valid ones.
        repo, repository_id = entry.get("repo"), entry.get("repository_id")
        if not (isinstance(repo, str) and REPO.fullmatch(repo)):
            problems.append(f"{key}: repo must be a repository name in {ORG}")
            repo = None
        if not positive_int(repository_id):
            problems.append(f"{key}: repository_id must be the repository's numeric ID")
            repository_id = None
        package = bundle = apple_id = None
        if "play" in entry:
            package = section(f"{key}.play", entry["play"], {"package"}, problems).get("package")
            if not (isinstance(package, str) and PACKAGE.fullmatch(package)):
                problems.append(f"{key}.play: package must be an Android package name")
                package = None
        if "app_store" in entry:
            store = section(f"{key}.app_store", entry["app_store"], {"bundle", "apple_id"}, problems)
            bundle, apple_id = store.get("bundle"), store.get("apple_id")
            if not (isinstance(bundle, str) and BUNDLE.fullmatch(bundle)):
                problems.append(f"{key}.app_store: bundle must be an iOS bundle ID")
                bundle = None
            if positive_int(apple_id):
                apple_id = str(apple_id)
            if not (isinstance(apple_id, str) and APPLE_ID.fullmatch(apple_id)):
                problems.append(f"{key}.app_store: apple_id must be the app's numeric Apple ID")
                apple_id = None
        result[key] = {
            "key": key,
            "repo": repo,
            "repository_id": repository_id,
            "package": package,
            "bundle": bundle,
            "apple_id": apple_id,
        }
    for field in ("repo", "repository_id", "package", "bundle", "apple_id"):
        seen = {}
        for key, app in result.items():
            value = app[field]
            if value is None:
                continue
            # GitHub repository names are case-insensitive.
            other = seen.setdefault(value.lower() if field == "repo" else value, key)
            if other != key:
                problems.append(f"{key}: {field} {value} is also used by {other}")
    if problems:
        raise ValueError("\n".join(problems))
    return result


def unique_keys(pairs):
    # yq keeps a repeated YAML key and json would keep only its last value.
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"{', '.join(repeated)}: the key appears more than once")
    return dict(pairs)


def load(path):
    with open(path, encoding="utf-8") as file:
        return parse(json.load(file, object_pairs_hook=unique_keys))


def find(apps, repository_id=None, repo=None):
    for app in apps.values():
        if app["repository_id"] == repository_id or (
            repo is not None and app["repo"].lower() == repo.lower()
        ):
            return app
    return None


def main(argv):
    parser = argparse.ArgumentParser(description="Check apps.yaml or look up an app in it.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("file")
    lookup = commands.add_parser("lookup")
    lookup.add_argument("file")
    by = lookup.add_mutually_exclusive_group(required=True)
    by.add_argument("--repository-id", type=int)
    by.add_argument("--repo")
    args = parser.parse_args(argv)
    try:
        apps = load(args.file)
    except (OSError, ValueError) as error:
        for line in str(error).splitlines():
            print(f"::error::apps.yaml: {line}", file=sys.stderr)
        return 1
    if args.command == "lookup":
        app = find(apps, args.repository_id, args.repo) or {}
        # Lines for $GITHUB_OUTPUT; the values were validated, so none has a newline.
        print(f"found={'true' if app else 'false'}")
        for name, field in (("key", "key"), ("repo", "repo"), ("repository-id", "repository_id"),
                            ("android-package", "package"), ("bundle-id", "bundle"),
                            ("apple-app-id", "apple_id")):
            value = app.get(field)
            print(f"{name}={'' if value is None else value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
