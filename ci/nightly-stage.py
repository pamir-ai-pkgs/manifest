#!/usr/bin/env python3
"""Stage a nightly BSP build from the labelled pull requests across the BSP.

Every night this creates ``nightly/YYYY-MM-DD`` in the manifest repository and
in every component repository the manifest names, merges the open pull
requests labelled ``nightly`` into those branches (server side, no clone),
commits a manifest pinned to the resulting commits, and tags the manifest
commit ``rk3576-vX.Y.Z-nightly.N``. Pushing that tag is what starts the build:
the release workflow recognises the nightly tag form and builds it on the
``nightly`` channel.

Selection rules, per pull request:

- carries the ``nightly`` label,
- is not a draft,
- targets the repository's default branch,
- its head commit's own CI is green (every check run concluded
  success/neutral/skipped and the combined commit status is not failure;
  a commit with no checks and no statuses counts as green).

Pull requests sharing a head branch name across repositories travel together
as one group: a change that spans repositories is either in the nightly on
every side or on none. Groups merge in order of their oldest pull request. A
merge conflict anywhere in a group rolls the group's repositories back to
where they stood before it and skips the whole group; the night continues
with what is left. That rollback, before anything is pinned or tagged, is the
only time a nightly branch moves backwards. A rerun for the same date reuses
the existing branches and only adds pull requests labelled since.

``prune`` deletes ``nightly/*`` branches older than the retention window in
every repository and prints the nightly versions whose artifacts expired with
them, so the caller can drop the matching S3 prefixes and OTA registrations.

Usage:
  nightly-stage.py stage [--date YYYY-MM-DD] [--dry-run] ...
  nightly-stage.py prune [--keep-days 14] [--dry-run] ...

Env:   GH_TOKEN  token with contents:write and pull-requests:read on every
                 repository the manifest names, plus the manifest repository.

Only the Python standard library is used so the stager runs on the release
runner and on a laptop without a virtualenv.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

LABEL = "nightly"
BRANCH_PREFIX = "nightly/"
TAG_PREFIX = "rk3576-v"
NEXT_VERSION_PATH = "release/NEXT_VERSION"
NEXT_VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")
NIGHTLY_TAG_RE = re.compile(r"^rk3576-v(\d+\.\d+\.\d+)-nightly\.(\d+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GREEN_CONCLUSIONS = {"success", "neutral", "skipped"}


class StageError(Exception):
    """A condition that must stop the night with a clear message."""


# --------------------------------------------------------------------------
# GitHub REST client
# --------------------------------------------------------------------------


class GitHub:
    """Minimal GitHub REST client over urllib.

    ``request`` is the only method that talks to the network; the tests
    replace the whole class with an in-memory fake exposing the same method.
    """

    def __init__(self, token: str, api_url: str = "https://api.github.com"):
        self.token = token
        self.api_url = api_url.rstrip("/")

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None) -> tuple[int, object]:
        url = f"{self.api_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    return resp.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = {"message": raw.decode(errors="replace")}
                # Secondary rate limits and transient 5xx are worth one retry;
                # everything else is the caller's decision.
                if exc.code in (403, 429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                return exc.code, payload
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise AssertionError("unreachable")

    def paginate(self, path: str, params: dict | None = None) -> list:
        items: list = []
        page = 1
        while True:
            q = dict(params or {})
            q.update({"per_page": 100, "page": page})
            status, payload = self.request("GET", path, q)
            if status != 200:
                raise StageError(f"GET {path} failed: {status} {message(payload)}")
            if isinstance(payload, dict):
                # check-runs / statuses wrap their list in an object.
                for key in ("check_runs", "statuses"):
                    if key in payload:
                        payload = payload[key]
                        break
            if not payload:
                break
            items.extend(payload)
            if len(payload) < 100:
                break
            page += 1
        return items


def message(payload: object) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message", payload))
    return str(payload)


# --------------------------------------------------------------------------
# Manifest handling
# --------------------------------------------------------------------------


def parse_manifest(text: str) -> ET.Element:
    return ET.fromstring(text)


def manifest_projects(root: ET.Element) -> list[dict]:
    """Return the manifest's projects as {name, path} in file order."""
    projects = []
    for project in root.iter("project"):
        projects.append({"name": project.get("name"), "path": project.get("path")})
    return projects


def pin_manifest(text: str, pins: dict[str, str], branch: str) -> str:
    """Rewrite every project's revision to its nightly commit.

    ``pins`` maps repository name to the 40-hex commit. ``upstream`` names the
    nightly branch so ``repo sync -c`` fetches exactly that ref.
    """
    root = parse_manifest(text)
    for project in root.iter("project"):
        name = project.get("name")
        if name not in pins:
            raise StageError(f"manifest project {name!r} has no nightly pin")
        project.set("revision", pins[name])
        project.set("upstream", f"refs/heads/{branch}")
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def repo_default_branch(gh: GitHub, owner: str, repo: str) -> str:
    status, payload = gh.request("GET", f"/repos/{owner}/{repo}")
    if status != 200:
        raise StageError(f"cannot read {owner}/{repo}: {status} {message(payload)}")
    return payload["default_branch"]


def branch_head(gh: GitHub, owner: str, repo: str, branch: str) -> str | None:
    status, payload = gh.request(
        "GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
    if status == 404:
        return None
    if status != 200:
        raise StageError(
            f"cannot read {owner}/{repo} branch {branch}: {status} {message(payload)}")
    return payload["object"]["sha"]


def ci_state(gh: GitHub, owner: str, repo: str, sha: str) -> tuple[bool, str]:
    """Return (green, reason) for the commit's own CI."""
    runs = gh.paginate(f"/repos/{owner}/{repo}/commits/{sha}/check-runs")
    for run in runs:
        if run.get("status") != "completed":
            return False, f"check {run.get('name')!r} is {run.get('status')}"
        if run.get("conclusion") not in GREEN_CONCLUSIONS:
            return False, f"check {run.get('name')!r} concluded {run.get('conclusion')}"
    status, combined = gh.request(
        "GET", f"/repos/{owner}/{repo}/commits/{sha}/status")
    if status != 200:
        raise StageError(
            f"cannot read commit status of {owner}/{repo}@{sha[:12]}: "
            f"{status} {message(combined)}")
    statuses = combined.get("statuses") or []
    state = combined.get("state")
    if statuses and state != "success":
        return False, f"combined commit status is {state}"
    if not runs and not statuses:
        return True, "no CI on this commit"
    return True, "green"


def select_pulls(gh: GitHub, owner: str, repo: str, default_branch: str) -> tuple[list[dict], list[dict]]:
    """Return (selected, rejected) labelled pull requests, oldest first."""
    pulls = gh.paginate(f"/repos/{owner}/{repo}/pulls", {"state": "open"})
    selected, rejected = [], []
    for pr in sorted(pulls, key=lambda p: p["number"]):
        labels = {label["name"] for label in pr.get("labels", [])}
        if LABEL not in labels:
            continue
        entry = {
            "repo": repo,
            "number": pr["number"],
            "title": pr.get("title", ""),
            "head_ref": pr["head"]["ref"],
            "head_sha": pr["head"]["sha"],
            "created_at": pr.get("created_at", ""),
            "url": pr.get("html_url", ""),
        }
        if pr.get("draft"):
            entry["reason"] = "draft"
            rejected.append(entry)
            continue
        if pr["base"]["ref"] != default_branch:
            entry["reason"] = f"targets {pr['base']['ref']}, not {default_branch}"
            rejected.append(entry)
            continue
        green, reason = ci_state(gh, owner, repo, pr["head"]["sha"])
        if not green:
            entry["reason"] = f"CI not green: {reason}"
            rejected.append(entry)
            continue
        entry["ci"] = reason
        selected.append(entry)
    return selected, rejected


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------


def ensure_branch(gh: GitHub, owner: str, repo: str, branch: str, base_sha: str,
                  dry_run: bool) -> tuple[str, bool]:
    """Create ``branch`` at ``base_sha`` unless it exists. Return (sha, created)."""
    existing = branch_head(gh, owner, repo, branch)
    if existing:
        return existing, False
    if dry_run:
        return base_sha, True
    status, payload = gh.request(
        "POST", f"/repos/{owner}/{repo}/git/refs",
        body={"ref": f"refs/heads/{branch}", "sha": base_sha})
    if status == 422:
        # Lost a race with a concurrent run; reuse what it made.
        existing = branch_head(gh, owner, repo, branch)
        if existing:
            return existing, False
    if status != 201:
        raise StageError(
            f"cannot create {owner}/{repo} branch {branch}: {status} {message(payload)}")
    return payload["object"]["sha"], True


def merge_pull(gh: GitHub, owner: str, repo: str, branch: str, pr: dict,
               dry_run: bool) -> tuple[str, str | None]:
    """Merge the pull request head into the nightly branch.

    Returns (outcome, new_branch_sha). Outcomes: merged, already, conflict,
    error. Merging by head SHA pins exactly the commit whose CI was checked,
    and works for fork heads because pull request objects live in the base
    repository.
    """
    if dry_run:
        return "merged", None
    status, payload = gh.request(
        "POST", f"/repos/{owner}/{repo}/merges",
        body={
            "base": branch,
            "head": pr["head_sha"],
            "commit_message": f"nightly: merge #{pr['number']} {pr['head_ref']}",
        })
    if status == 201:
        return "merged", payload["sha"]
    if status == 204:
        return "already", None
    if status == 409:
        return "conflict", None
    return "error", None


def reset_branch(gh: GitHub, owner: str, repo: str, branch: str, sha: str) -> None:
    """Move ``branch`` back to ``sha``: the within-run rollback of a group."""
    status, payload = gh.request(
        "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
        body={"sha": sha, "force": True})
    if status != 200:
        raise StageError(
            f"cannot roll back {owner}/{repo} branch {branch} to {sha[:12]}: "
            f"{status} {message(payload)}")


def group_pulls(selected: list[dict], repo_order: list[str]) -> list[list[dict]]:
    """Group selected pull requests by head branch name across repositories.

    Groups are ordered by their oldest pull request, members by manifest
    order, so a night is reproducible from the same inputs.
    """
    by_ref: dict[str, list[dict]] = {}
    for pr in selected:
        by_ref.setdefault(pr["head_ref"], []).append(pr)
    rank = {repo: i for i, repo in enumerate(repo_order)}
    groups = []
    for ref, members in by_ref.items():
        members.sort(key=lambda p: (rank[p["repo"]], p["number"]))
        groups.append((min(p["created_at"] for p in members), ref, members))
    groups.sort(key=lambda g: (g[0], g[1]))
    return [members for _, _, members in groups]


def read_next_version(gh: GitHub, owner: str, repo: str, ref: str) -> str:
    status, payload = gh.request(
        "GET", f"/repos/{owner}/{repo}/contents/{NEXT_VERSION_PATH}", {"ref": ref})
    if status == 404:
        raise StageError(
            f"{owner}/{repo} has no {NEXT_VERSION_PATH} at {ref[:12]}; "
            "the nightly target version lives in that file")
    if status != 200:
        raise StageError(
            f"cannot read {NEXT_VERSION_PATH}: {status} {message(payload)}")
    text = base64.b64decode(payload["content"]).decode().strip()
    m = NEXT_VERSION_RE.match(text)
    if not m:
        raise StageError(
            f"{NEXT_VERSION_PATH} must hold X.Y.Z (optionally v-prefixed), got {text!r}")
    return m.group(1)


def list_tags(gh: GitHub, owner: str, repo: str, prefix: str) -> list[str]:
    status, payload = gh.request(
        "GET", f"/repos/{owner}/{repo}/git/matching-refs/tags/{prefix}")
    if status != 200:
        raise StageError(f"cannot list tags of {owner}/{repo}: {status} {message(payload)}")
    return [ref["ref"][len("refs/tags/"):] for ref in payload]


def next_nightly_tag(gh: GitHub, owner: str, manifest_repo: str, version: str) -> str:
    """rk3576-v<version>-nightly.<N+1>, N = highest existing nightly tag."""
    # matching-refs is a prefix match: one call sees the final tag, every
    # nightly of this target, and any rc tags cut by hand.
    tags = list_tags(gh, owner, manifest_repo, f"{TAG_PREFIX}{version}")
    if f"{TAG_PREFIX}{version}" in tags:
        raise StageError(
            f"{TAG_PREFIX}{version} is already released; bump {NEXT_VERSION_PATH}")
    highest = 0
    for tag in tags:
        m = NIGHTLY_TAG_RE.match(tag)
        if m and m.group(1) == version:
            highest = max(highest, int(m.group(2)))
    return f"{TAG_PREFIX}{version}-nightly.{highest + 1}"


def commit_files(gh: GitHub, owner: str, repo: str, branch: str, parent_sha: str,
                 files: dict[str, str], message_text: str) -> str:
    """Create one commit on ``branch`` carrying ``files`` (path -> text)."""
    status, parent = gh.request("GET", f"/repos/{owner}/{repo}/git/commits/{parent_sha}")
    if status != 200:
        raise StageError(f"cannot read commit {parent_sha[:12]}: {status} {message(parent)}")
    tree = []
    for path, text in files.items():
        status, blob = gh.request(
            "POST", f"/repos/{owner}/{repo}/git/blobs",
            body={"content": text, "encoding": "utf-8"})
        if status != 201:
            raise StageError(f"cannot create blob for {path}: {status} {message(blob)}")
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    status, new_tree = gh.request(
        "POST", f"/repos/{owner}/{repo}/git/trees",
        body={"base_tree": parent["tree"]["sha"], "tree": tree})
    if status != 201:
        raise StageError(f"cannot create tree: {status} {message(new_tree)}")
    status, commit = gh.request(
        "POST", f"/repos/{owner}/{repo}/git/commits",
        body={"message": message_text, "tree": new_tree["sha"], "parents": [parent_sha]})
    if status != 201:
        raise StageError(f"cannot create commit: {status} {message(commit)}")
    status, payload = gh.request(
        "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
        body={"sha": commit["sha"], "force": False})
    if status != 200:
        raise StageError(f"cannot advance {branch}: {status} {message(payload)}")
    return commit["sha"]


def create_tag(gh: GitHub, owner: str, repo: str, tag: str, sha: str) -> None:
    status, payload = gh.request(
        "POST", f"/repos/{owner}/{repo}/git/refs",
        body={"ref": f"refs/tags/{tag}", "sha": sha})
    if status != 201:
        raise StageError(f"cannot create tag {tag}: {status} {message(payload)}")


def stage(gh: GitHub, owner: str, manifest_repo: str, manifest_file: str,
          date: str, dry_run: bool, log=print) -> dict:
    branch = f"{BRANCH_PREFIX}{date}"
    report: dict = {
        "date": date, "branch": branch, "dry_run": dry_run,
        "repos": [], "included": [], "skipped": [],
    }

    # The manifest repository is staged like any component: its own labelled
    # pull requests (typically CI changes) ride the nightly, and the pinned
    # manifest is committed on top of them.
    manifest_default = repo_default_branch(gh, owner, manifest_repo)
    manifest_main = branch_head(gh, owner, manifest_repo, manifest_default)
    if not manifest_main:
        raise StageError(f"{owner}/{manifest_repo} has no {manifest_default}")
    status, payload = gh.request(
        "GET", f"/repos/{owner}/{manifest_repo}/contents/{manifest_file}",
        {"ref": manifest_main})
    if status != 200:
        raise StageError(f"cannot read {manifest_file}: {status} {message(payload)}")
    manifest_text = base64.b64decode(payload["content"]).decode()
    projects = manifest_projects(parse_manifest(manifest_text))
    repos = [manifest_repo] + [p["name"] for p in projects]

    # Pass 1: branches and selection in every repository.
    tips: dict[str, str] = {}
    created_flags: dict[str, bool] = {}
    selected: list[dict] = []
    for repo in repos:
        default = manifest_default if repo == manifest_repo else repo_default_branch(gh, owner, repo)
        base = manifest_main if repo == manifest_repo else branch_head(gh, owner, repo, default)
        if not base:
            raise StageError(f"{owner}/{repo} has no {default}")
        tips[repo], created_flags[repo] = ensure_branch(gh, owner, repo, branch, base, dry_run)
        log(f"{repo}: {branch} {'created' if created_flags[repo] else 'reused'} at {tips[repo][:12]}")
        chosen, rejected = select_pulls(gh, owner, repo, default)
        for pr in rejected:
            report["skipped"].append(pr)
            log(f"  skip #{pr['number']} {pr['head_ref']}: {pr['reason']}")
        selected.extend(chosen)

    # Pass 2: merge group by group. A group is every selected pull request
    # sharing a head branch name; it lands whole or not at all.
    for members in group_pulls(selected, repos):
        ref = members[0]["head_ref"]
        snapshot = {pr["repo"]: tips[pr["repo"]] for pr in members}
        merged: list[dict] = []
        failed: dict | None = None
        for pr in members:
            outcome, new_tip = merge_pull(gh, owner, pr["repo"], branch, pr, dry_run)
            if outcome in ("merged", "already"):
                if new_tip:
                    tips[pr["repo"]] = new_tip
                pr["outcome"] = outcome
                merged.append(pr)
                continue
            pr["reason"] = "merge conflict" if outcome == "conflict" else "merge failed"
            failed = pr
            break
        if failed is None:
            for pr in merged:
                pr["group"] = ref
                report["included"].append(pr)
                log(f"{pr['repo']}: {pr['outcome']} #{pr['number']} {ref} ({pr['ci']})")
            continue
        # Roll the group's repositories back to where this group found them
        # and skip every member, so a cross-repository change is never half
        # applied.
        for repo, sha in snapshot.items():
            if tips[repo] != sha:
                if not dry_run:
                    reset_branch(gh, owner, repo, branch, sha)
                tips[repo] = sha
                log(f"{repo}: rolled {branch} back to {sha[:12]}")
        log(f"{failed['repo']}: skip #{failed['number']} {ref}: {failed['reason']}")
        for pr in members:
            pr["group"] = ref
            if pr is not failed:
                pr["reason"] = (f"travels with {failed['repo']}#{failed['number']} "
                                f"({ref}): {failed['reason']}")
                log(f"{pr['repo']}: skip #{pr['number']} {ref}: {pr['reason']}")
            pr.pop("outcome", None)
            report["skipped"].append(pr)

    pins = dict(tips)
    for repo in repos:
        report["repos"].append({"repo": repo, "branch": branch, "sha": tips[repo],
                                "created": created_flags[repo]})

    # Version: the target from bsp-tools at its nightly tip, the counter from
    # the manifest repository's existing nightly tags.
    bsp_tools = next(p["name"] for p in projects if p["path"] == "bsp-tools")
    next_version = read_next_version(gh, owner, bsp_tools, pins[bsp_tools])
    tag = next_nightly_tag(gh, owner, manifest_repo, next_version)
    report["next_version"] = next_version
    report["tag"] = tag
    report["version"] = tag[len("rk3576-"):]  # v0.2.0-nightly.N, the IMAGE_VERSION form

    component_pins = {p["name"]: pins[p["name"]] for p in projects}
    pinned = pin_manifest(manifest_text, component_pins, branch)
    stamp = {
        "schema": "pamir-rk3576.nightly.v1",
        "date": date,
        "tag": tag,
        "version": report["version"],
        "branch": branch,
        "repos": report["repos"],
        "included": report["included"],
        "skipped": report["skipped"],
    }
    files = {
        manifest_file: pinned,
        "nightly.json": json.dumps(stamp, indent=2, sort_keys=True) + "\n",
    }
    if dry_run:
        report["manifest"] = pinned
        log(f"dry run: would commit {manifest_file} + nightly.json on {branch} and tag {tag}")
        return report
    commit = commit_files(
        gh, owner, manifest_repo, branch, pins[manifest_repo], files,
        f"nightly: pin {date} as {tag}\n\n"
        f"{len(report['included'])} pull request(s) included, "
        f"{len(report['skipped'])} skipped.",
    )
    create_tag(gh, owner, manifest_repo, tag, commit)
    report["manifest_commit"] = commit
    log(f"tagged {owner}/{manifest_repo}@{commit[:12]} as {tag}")
    return report


# --------------------------------------------------------------------------
# Pruning
# --------------------------------------------------------------------------


def prune(gh: GitHub, owner: str, manifest_repo: str, manifest_file: str,
          keep_days: int, today: dt.date, dry_run: bool, log=print) -> dict:
    """Delete nightly branches older than ``keep_days`` in every repository.

    Nightly tags stay: they are the immutable version record and the
    counter. The returned report lists the tags whose branches expired so the
    caller can prune the matching build artifacts; the newest nightly is
    never listed, so a device is always left something installable.
    """
    cutoff = today - dt.timedelta(days=keep_days)
    manifest_default = repo_default_branch(gh, owner, manifest_repo)
    manifest_main = branch_head(gh, owner, manifest_repo, manifest_default)
    status, payload = gh.request(
        "GET", f"/repos/{owner}/{manifest_repo}/contents/{manifest_file}",
        {"ref": manifest_main})
    if status != 200:
        raise StageError(f"cannot read {manifest_file}: {status} {message(payload)}")
    projects = manifest_projects(parse_manifest(base64.b64decode(payload["content"]).decode()))
    repos = [manifest_repo] + [p["name"] for p in projects]

    report: dict = {"cutoff": cutoff.isoformat(), "dry_run": dry_run,
                    "deleted": [], "expired_tags": []}
    for repo in repos:
        status, refs = gh.request(
            "GET", f"/repos/{owner}/{repo}/git/matching-refs/heads/{BRANCH_PREFIX}")
        if status != 200:
            raise StageError(f"cannot list branches of {owner}/{repo}: {status} {message(refs)}")
        for ref in refs:
            name = ref["ref"][len("refs/heads/"):]
            date_part = name[len(BRANCH_PREFIX):]
            if not DATE_RE.match(date_part):
                continue
            if dt.date.fromisoformat(date_part) >= cutoff:
                continue
            log(f"{repo}: delete {name}")
            report["deleted"].append({"repo": repo, "branch": name})
            if dry_run:
                continue
            status, payload = gh.request("DELETE", f"/repos/{owner}/{repo}/git/refs/heads/{name}")
            if status not in (204, 422):
                raise StageError(f"cannot delete {repo} {name}: {status} {message(payload)}")

    # Each nightly tag carries its date in nightly.json; the tag outlives the
    # branch, so expiry is decided from the tag alone. The newest nightly is
    # held back whatever its age.
    tags = []
    for tag in list_tags(gh, owner, manifest_repo, TAG_PREFIX):
        m = NIGHTLY_TAG_RE.match(tag)
        if m:
            tags.append((tuple(int(x) for x in m.group(1).split(".")), int(m.group(2)), tag))
    tags.sort()
    newest = tags[-1][2] if tags else None
    for _, _, tag in tags:
        if tag == newest:
            continue
        status, payload = gh.request(
            "GET", f"/repos/{owner}/{manifest_repo}/contents/nightly.json", {"ref": tag})
        if status != 200:
            continue
        stamp = json.loads(base64.b64decode(payload["content"]).decode())
        stamp_date = stamp.get("date", "")
        if DATE_RE.match(stamp_date) and dt.date.fromisoformat(stamp_date) < cutoff:
            report["expired_tags"].append(tag)
    return report


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def summary_markdown(report: dict) -> str:
    lines = []
    if "tag" in report:
        lines.append(f"## Nightly {report['date']}: `{report['tag']}`")
        if report.get("dry_run"):
            lines.append("_Dry run: nothing was written._")
        lines.append("")
        lines.append(f"Target version `{report['next_version']}`, branch `{report['branch']}`.")
        lines.append("")
        lines.append("| Repository | Commit |")
        lines.append("|---|---|")
        for r in report["repos"]:
            lines.append(f"| {r['repo']} | `{r['sha'][:12]}` |")
        lines.append("")
        lines.append(f"### Included ({len(report['included'])})")
        for pr in report["included"]:
            lines.append(f"- {pr['repo']}#{pr['number']} `{pr['head_ref']}` — {pr['title']}")
        lines.append("")
        lines.append(f"### Skipped ({len(report['skipped'])})")
        for pr in report["skipped"]:
            lines.append(f"- {pr['repo']}#{pr['number']} `{pr['head_ref']}` — {pr['reason']}")
    else:
        lines.append(f"## Nightly prune (older than {report['cutoff']})")
        if report.get("dry_run"):
            lines.append("_Dry run: nothing was deleted._")
        lines.append("")
        for d in report["deleted"]:
            lines.append(f"- {d['repo']} `{d['branch']}`")
        if report["expired_tags"]:
            lines.append("")
            lines.append("Expired nightlies: " + ", ".join(f"`{t}`" for t in report["expired_tags"]))
    return "\n".join(lines) + "\n"


def write_outputs(report: dict, args: argparse.Namespace) -> None:
    if args.report:
        with open(args.report, "w") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
            fh.write("\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as fh:
            fh.write(summary_markdown(report))
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a") as fh:
            for key in ("tag", "version", "branch", "manifest_commit"):
                if key in report:
                    fh.write(f"{key}={report[key]}\n")
            if "expired_tags" in report:
                fh.write("expired_tags=" + " ".join(report["expired_tags"]) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stage", "prune"):
        p = sub.add_parser(name)
        p.add_argument("--owner", default="pamir-ai-pkgs")
        p.add_argument("--manifest-repo", default="manifest")
        p.add_argument("--manifest-file", default="rk3576-debian-ab.xml")
        p.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--report", help="write the JSON report to this path")
        if name == "stage":
            p.add_argument("--date", help="nightly date, YYYY-MM-DD (default: today, UTC)")
        else:
            p.add_argument("--keep-days", type=int, default=14)
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN is required", file=sys.stderr)
        return 2
    gh = GitHub(token, args.api_url)
    today = dt.datetime.now(dt.timezone.utc).date()
    try:
        if args.command == "stage":
            date = args.date or today.isoformat()
            if not DATE_RE.match(date):
                raise StageError(f"--date must be YYYY-MM-DD, got {date!r}")
            report = stage(gh, args.owner, args.manifest_repo, args.manifest_file,
                           date, args.dry_run)
        else:
            report = prune(gh, args.owner, args.manifest_repo, args.manifest_file,
                           args.keep_days, today, args.dry_run)
    except StageError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    write_outputs(report, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
