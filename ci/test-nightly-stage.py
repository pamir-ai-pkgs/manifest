#!/usr/bin/env python3
"""Unit tests for ci/nightly-stage.py against an in-memory GitHub.

Run:  python3 ci/test-nightly-stage.py
"""

from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import json
import pathlib
import re
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("nightly_stage", HERE / "nightly-stage.py")
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <remote name="github" fetch="https://github.com/pamir-ai-pkgs/" review="https://github.com/pamir-ai-pkgs/" />
  <default remote="github" revision="main" dest-branch="main" sync-j="4" sync-c="true" />
  <project name="linux-rockchip-kernel" path="kernel-6.1" revision="refs/tags/v0.1.0-rc3">
    <linkfile src="." dest="kernel"/>
  </project>
  <project name="linux-rockchip-bsp-tools" path="bsp-tools" revision="refs/tags/v0.1.0-rc3"/>
  <repo-hooks in-project="linux-rockchip-bsp-tools" enabled-list="post-sync"/>
</manifest>
"""


class FakeGitHub(ns.GitHub):
    """Enough of the REST API for the stager, with write tracking.

    Only ``request`` is replaced; ``paginate`` runs the real code over it.
    """

    def __init__(self):
        super().__init__(token="fake")
        self.repos: dict = {}
        self.writes: list = []
        self.forced: list = []
        self._n = 0

    def sha(self) -> str:
        self._n += 1
        return f"{self._n:040x}"

    def add_repo(self, name: str, files: dict | None = None, default_branch: str = "main") -> str:
        head = self.sha()
        tree = self.sha()
        self.repos[name] = {
            "default_branch": default_branch,
            "branches": {default_branch: head},
            "tags": {},
            "pulls": [],
            "check_runs": {},
            "statuses": {},
            "conflicts": set(),
            "merged": {},
            "commits": {head: {"tree": tree}},
            "trees": {tree: dict(files or {})},
            "blobs": {},
        }
        return head

    def add_pull(self, repo: str, number: int, head_ref: str, labels=("nightly",),
                 draft=False, base="main", conclusion="success", conflict=False,
                 status=None, title="", created_at=None) -> str:
        r = self.repos[repo]
        sha = self.sha()
        r["pulls"].append({
            "number": number, "title": title or head_ref, "draft": draft,
            "labels": [{"name": label} for label in labels],
            "head": {"ref": head_ref, "sha": sha}, "base": {"ref": base},
            "created_at": created_at or f"2026-08-{number:02d}T00:00:00Z",
            "html_url": f"https://github.com/o/{repo}/pull/{number}",
        })
        if conclusion is not None:
            r["check_runs"][sha] = [{"name": "ci", "status": "completed", "conclusion": conclusion}]
        if status is not None:
            r["statuses"][sha] = status
        if conflict:
            r["conflicts"].add(sha)
        return sha

    def files_at(self, repo: str, sha: str) -> dict:
        r = self.repos[repo]
        return r["trees"][r["commits"][sha]["tree"]]

    def _resolve(self, r: dict, ref: str) -> str | None:
        return r["branches"].get(ref) or r["tags"].get(ref) or (ref if ref in r["commits"] else None)

    def _new_commit(self, r: dict, parent: str, files: dict) -> str:
        sha, tree = self.sha(), self.sha()
        r["commits"][sha] = {"tree": tree}
        r["trees"][tree] = files
        return sha

    def request(self, method, path, params=None, body=None):
        params = params or {}
        if method != "GET":
            self.writes.append((method, path))
        m = re.match(r"^/repos/([^/]+)/([^/]+)(/.*)?$", path)
        _owner, repo, rest = m.groups()
        rest = rest or ""
        r = self.repos.get(repo)
        if r is None:
            return 404, {"message": "Not Found"}
        if rest == "":
            return 200, {"default_branch": r["default_branch"]}
        if rest.startswith("/git/ref/heads/"):
            sha = r["branches"].get(rest[len("/git/ref/heads/"):])
            return (200, {"object": {"sha": sha}}) if sha else (404, {"message": "Not Found"})
        if rest == "/git/refs" and method == "POST":
            ref, sha = body["ref"], body["sha"]
            kind, name = ref.split("/", 2)[1], ref.split("/", 2)[2]
            store = r["branches"] if kind == "heads" else r["tags"]
            if name in store:
                return 422, {"message": "Reference already exists"}
            store[name] = sha
            return 201, {"ref": ref, "object": {"sha": sha}}
        if rest.startswith("/git/refs/heads/") and method == "PATCH":
            name = rest[len("/git/refs/heads/"):]
            if body.get("force"):
                # Only a rerun rebuild or a group rollback moves a branch backwards.
                self.forced.append((repo, name, body["sha"]))
            r["branches"][name] = body["sha"]
            return 200, {"object": {"sha": body["sha"]}}
        if rest.startswith("/git/refs/heads/") and method == "DELETE":
            r["branches"].pop(rest[len("/git/refs/heads/"):], None)
            return 204, None
        if rest.startswith("/git/matching-refs/tags/"):
            prefix = rest[len("/git/matching-refs/tags/"):]
            return 200, [{"ref": f"refs/tags/{t}"} for t in sorted(r["tags"]) if t.startswith(prefix)]
        if rest.startswith("/git/matching-refs/heads/"):
            prefix = rest[len("/git/matching-refs/heads/"):]
            return 200, [{"ref": f"refs/heads/{b}"} for b in sorted(r["branches"]) if b.startswith(prefix)]
        if rest == "/pulls":
            return 200, (r["pulls"] if params.get("page", 1) == 1 else [])
        cm = re.match(r"^/commits/([0-9a-f]+)/(check-runs|status)$", rest)
        if cm:
            sha, what = cm.groups()
            if what == "check-runs":
                runs = r["check_runs"].get(sha, []) if params.get("page", 1) == 1 else []
                return 200, {"total_count": len(runs), "check_runs": runs}
            return 200, r["statuses"].get(sha, {"state": "pending", "statuses": []})
        if rest == "/merges" and method == "POST":
            base, head = body["base"], body["head"]
            if head in r["conflicts"]:
                return 409, {"message": "Merge conflict"}
            # "Already merged" follows ancestry, as on GitHub: a head merged
            # into a tip the branch has since been moved off is not in the base.
            parent = r["branches"][base]
            reachable = r["commits"][parent].setdefault("merged", set())
            if head in reachable:
                return 204, None
            sha = self._new_commit(r, parent, dict(self.files_at(repo, parent)))
            r["commits"][sha]["merged"] = reachable | {head}
            r["branches"][base] = sha
            r["merged"].setdefault(base, set()).add(head)  # every merge ever made
            return 201, {"sha": sha}
        if rest.startswith("/contents/"):
            sha = self._resolve(r, params.get("ref", r["default_branch"]))
            text = self.files_at(repo, sha).get(rest[len("/contents/"):]) if sha else None
            if text is None:
                return 404, {"message": "Not Found"}
            return 200, {"content": base64.b64encode(text.encode()).decode(), "sha": "f" * 40}
        if rest.startswith("/git/commits/") and method == "GET":
            sha = rest[len("/git/commits/"):]
            return 200, {"sha": sha, "tree": {"sha": r["commits"][sha]["tree"]}}
        if rest == "/git/blobs" and method == "POST":
            sha = self.sha()
            r["blobs"][sha] = body["content"]
            return 201, {"sha": sha}
        if rest == "/git/trees" and method == "POST":
            files = dict(r["trees"][body["base_tree"]])
            for entry in body["tree"]:
                files[entry["path"]] = r["blobs"][entry["sha"]]
            sha = self.sha()
            r["trees"][sha] = files
            return 201, {"sha": sha}
        if rest == "/git/commits" and method == "POST":
            sha = self.sha()
            r["commits"][sha] = {"tree": body["tree"]}
            return 201, {"sha": sha}
        raise AssertionError(f"unhandled {method} {path}")


def build_fixture(next_version="0.2.0", with_next_version=True):
    gh = FakeGitHub()
    gh.add_repo("manifest", {"rk3576-debian-ab.xml": MANIFEST})
    gh.add_repo("linux-rockchip-kernel")
    bsp_files = {"release/NEXT_VERSION": next_version + "\n"} if with_next_version else {}
    gh.add_repo("linux-rockchip-bsp-tools", bsp_files)
    return gh


def run_stage(gh, date="2026-08-25", dry_run=False):
    return ns.stage(gh, "o", "manifest", "rk3576-debian-ab.xml", date, dry_run, log=lambda *_: None)


class StageTests(unittest.TestCase):
    def test_selects_merges_pins_and_tags(self):
        gh = build_fixture()
        gh.repos["manifest"]["tags"]["rk3576-v0.1.0"] = gh.sha()
        gh.repos["manifest"]["tags"]["rk3576-v0.2.0-nightly.2"] = gh.sha()
        gh.repos["manifest"]["tags"]["rk3576-v0.2.0-rc.1"] = gh.sha()
        good = gh.add_pull("linux-rockchip-kernel", 7, "frank/fix")
        gh.add_pull("linux-rockchip-kernel", 8, "frank/draft", draft=True)
        gh.add_pull("linux-rockchip-kernel", 9, "frank/red", conclusion="failure")
        gh.add_pull("linux-rockchip-kernel", 10, "frank/conflict", conflict=True)
        gh.add_pull("linux-rockchip-kernel", 11, "frank/wrong-base", base="release/x")
        gh.add_pull("linux-rockchip-kernel", 12, "frank/unlabelled", labels=())
        gh.add_pull("linux-rockchip-kernel", 13, "frank/pending", conclusion=None,
                    status={"state": "pending", "statuses": [{"state": "pending"}]})
        no_ci = gh.add_pull("linux-rockchip-bsp-tools", 3, "frank/no-ci", conclusion=None)

        report = run_stage(gh)

        self.assertEqual(report["tag"], "rk3576-v0.2.0-nightly.3")
        self.assertEqual(report["version"], "v0.2.0-nightly.3")
        self.assertEqual({p["number"] for p in report["included"]}, {7, 3})
        self.assertEqual(
            {(p["number"], p["reason"].split(":")[0]) for p in report["skipped"]},
            {(8, "draft"), (9, "CI not green"), (10, "merge conflict"),
             (11, "targets release/x, not main"), (13, "CI not green")})
        for repo in ("manifest", "linux-rockchip-kernel", "linux-rockchip-bsp-tools"):
            self.assertIn("nightly/2026-08-25", gh.repos[repo]["branches"], repo)
        kernel = gh.repos["linux-rockchip-kernel"]
        self.assertIn(good, kernel["merged"]["nightly/2026-08-25"])
        self.assertIn(no_ci, gh.repos["linux-rockchip-bsp-tools"]["merged"]["nightly/2026-08-25"])

        # The tag points at the pinned-manifest commit on the nightly branch.
        tag_sha = gh.repos["manifest"]["tags"]["rk3576-v0.2.0-nightly.3"]
        self.assertEqual(tag_sha, gh.repos["manifest"]["branches"]["nightly/2026-08-25"])
        files = gh.files_at("manifest", tag_sha)
        root = ns.parse_manifest(files["rk3576-debian-ab.xml"])
        pins = {p.get("name"): (p.get("revision"), p.get("upstream")) for p in root.iter("project")}
        self.assertEqual(pins["linux-rockchip-kernel"],
                         (kernel["branches"]["nightly/2026-08-25"], "refs/heads/nightly/2026-08-25"))
        self.assertEqual(pins["linux-rockchip-bsp-tools"][0],
                         gh.repos["linux-rockchip-bsp-tools"]["branches"]["nightly/2026-08-25"])
        self.assertIsNotNone(root.find("project/linkfile"), "linkfile children survive pinning")
        stamp = json.loads(files["nightly.json"])
        self.assertEqual(stamp["tag"], "rk3576-v0.2.0-nightly.3")
        self.assertEqual(stamp["date"], "2026-08-25")
        self.assertEqual(len(stamp["included"]), 2)

    def test_first_nightly_of_a_cycle(self):
        gh = build_fixture(next_version="v0.3.0")
        gh.repos["manifest"]["tags"]["rk3576-v0.2.0-nightly.9"] = gh.sha()
        report = run_stage(gh)
        self.assertEqual(report["tag"], "rk3576-v0.3.0-nightly.1")

    def test_released_target_version_stops_the_night(self):
        gh = build_fixture(next_version="0.1.0")
        gh.repos["manifest"]["tags"]["rk3576-v0.1.0"] = gh.sha()
        with self.assertRaisesRegex(ns.StageError, "already released"):
            run_stage(gh)

    def test_missing_next_version_stops_the_night(self):
        gh = build_fixture(with_next_version=False)
        with self.assertRaisesRegex(ns.StageError, "NEXT_VERSION"):
            run_stage(gh)

    def test_dry_run_writes_nothing(self):
        gh = build_fixture()
        gh.add_pull("linux-rockchip-kernel", 7, "frank/fix")
        report = run_stage(gh, dry_run=True)
        self.assertEqual(gh.writes, [])
        self.assertEqual(report["tag"], "rk3576-v0.2.0-nightly.1")
        self.assertIn('upstream="refs/heads/nightly/2026-08-25"', report["manifest"])

    def test_rerun_rebuilds_branches_from_the_default_branch(self):
        gh = build_fixture()
        gh.add_pull("linux-rockchip-kernel", 7, "frank/fix")
        first = run_stage(gh)
        self.assertEqual({r["state"] for r in first["repos"]}, {"created"})
        kernel = gh.repos["linux-rockchip-kernel"]
        first_tip = kernel["branches"]["nightly/2026-08-25"]
        # Between attempts main moves and a second pull request is labelled.
        new_main = gh._new_commit(kernel, kernel["branches"]["main"], {})
        kernel["branches"]["main"] = new_main
        gh.add_pull("linux-rockchip-kernel", 8, "frank/later")
        gh.forced.clear()

        second = run_stage(gh)

        self.assertEqual(second["tag"], "rk3576-v0.2.0-nightly.2")
        states = {r["repo"]: r["state"] for r in second["repos"]}
        self.assertEqual(states, {"linux-rockchip-kernel": "reset",
                                  "manifest": "reset",  # the first pin commit is dropped
                                  "linux-rockchip-bsp-tools": "unchanged"})
        # Moved back to main, then every labelled pull request merged afresh on top.
        self.assertIn(("linux-rockchip-kernel", "nightly/2026-08-25", new_main), gh.forced)
        self.assertEqual({(p["number"], p["outcome"]) for p in second["included"]},
                         {(7, "merged"), (8, "merged")})
        self.assertNotEqual(first_tip, kernel["branches"]["nightly/2026-08-25"])
        self.assertNotEqual(first["manifest_commit"], second["manifest_commit"])
        pins = {r["repo"]: r["sha"] for r in second["repos"]}
        self.assertEqual(pins["linux-rockchip-kernel"], kernel["branches"]["nightly/2026-08-25"])

    def test_rerun_drops_a_pull_request_that_lost_the_label(self):
        gh = build_fixture()
        gh.add_pull("linux-rockchip-kernel", 7, "frank/fix")
        run_stage(gh)
        gh.repos["linux-rockchip-kernel"]["pulls"][0]["labels"] = []
        second = run_stage(gh)
        self.assertEqual(second["included"], [])
        kernel = gh.repos["linux-rockchip-kernel"]
        self.assertEqual(kernel["branches"]["nightly/2026-08-25"], kernel["branches"]["main"])

    def test_dry_run_rerun_moves_nothing(self):
        gh = build_fixture()
        gh.add_pull("linux-rockchip-kernel", 7, "frank/fix")
        run_stage(gh)
        gh.writes.clear()
        report = run_stage(gh, dry_run=True)
        self.assertEqual(gh.writes, [])
        states = {r["repo"]: r["state"] for r in report["repos"]}
        self.assertEqual(states, {"linux-rockchip-kernel": "reset", "manifest": "reset",
                                  "linux-rockchip-bsp-tools": "unchanged"})

    def test_no_forced_moves_without_a_group_conflict(self):
        gh = build_fixture()
        gh.add_pull("linux-rockchip-kernel", 7, "frank/fix")
        gh.add_pull("linux-rockchip-kernel", 8, "frank/conflict", conflict=True)
        run_stage(gh)
        self.assertEqual(gh.forced, [])

    def test_cross_repo_group_travels_together(self):
        gh = build_fixture()
        kernel = gh.add_pull("linux-rockchip-kernel", 7, "frank/pair")
        tools = gh.add_pull("linux-rockchip-bsp-tools", 3, "frank/pair")
        report = run_stage(gh)
        self.assertEqual({(p["repo"], p["number"], p["group"]) for p in report["included"]},
                         {("linux-rockchip-kernel", 7, "frank/pair"),
                          ("linux-rockchip-bsp-tools", 3, "frank/pair")})
        self.assertIn(kernel, gh.repos["linux-rockchip-kernel"]["merged"]["nightly/2026-08-25"])
        self.assertIn(tools, gh.repos["linux-rockchip-bsp-tools"]["merged"]["nightly/2026-08-25"])
        self.assertEqual(gh.forced, [])

    def test_group_conflict_rolls_back_every_member(self):
        gh = build_fixture()
        # kernel merges first (manifest order), then bsp-tools conflicts:
        # the kernel branch must return to where the group found it.
        gh.add_pull("linux-rockchip-kernel", 5, "frank/solo", created_at="2026-08-01T00:00:00Z")
        kernel_pair = gh.add_pull("linux-rockchip-kernel", 7, "frank/pair", created_at="2026-08-02T00:00:00Z")
        gh.add_pull("linux-rockchip-bsp-tools", 3, "frank/pair", conflict=True,
                    created_at="2026-08-03T00:00:00Z")
        report = run_stage(gh)

        self.assertEqual([p["number"] for p in report["included"]], [5])
        skipped = {p["number"]: p for p in report["skipped"]}
        self.assertEqual(set(skipped), {7, 3})
        self.assertEqual(skipped[3]["reason"], "merge conflict")
        self.assertEqual(skipped[7]["reason"],
                         "travels with linux-rockchip-bsp-tools#3 (frank/pair): merge conflict")
        self.assertEqual(skipped[7]["group"], "frank/pair")
        kernel = gh.repos["linux-rockchip-kernel"]
        merged = kernel["merged"]["nightly/2026-08-25"]
        self.assertIn(kernel_pair, merged, "the merge happened before the rollback")
        # Rolled back to the tip after #5 alone, and the pinned manifest
        # carries that tip.
        self.assertEqual(len(gh.forced), 1)
        forced_repo, forced_branch, forced_sha = gh.forced[0]
        self.assertEqual((forced_repo, forced_branch), ("linux-rockchip-kernel", "nightly/2026-08-25"))
        self.assertEqual(kernel["branches"]["nightly/2026-08-25"], forced_sha)
        pins = {r["repo"]: r["sha"] for r in report["repos"]}
        self.assertEqual(pins["linux-rockchip-kernel"], forced_sha)
        files = gh.files_at("manifest", gh.repos["manifest"]["tags"][report["tag"]])
        self.assertIn(f'revision="{forced_sha}"', files["rk3576-debian-ab.xml"])

    def test_groups_merge_oldest_first(self):
        selected = [
            {"repo": "b", "number": 1, "head_ref": "y", "created_at": "2026-08-02T00:00:00Z"},
            {"repo": "a", "number": 9, "head_ref": "x", "created_at": "2026-08-03T00:00:00Z"},
            {"repo": "a", "number": 2, "head_ref": "y", "created_at": "2026-08-04T00:00:00Z"},
            {"repo": "b", "number": 3, "head_ref": "x", "created_at": "2026-08-01T00:00:00Z"},
        ]
        groups = ns.group_pulls(selected, ["a", "b"])
        self.assertEqual([[(p["repo"], p["number"]) for p in g] for g in groups],
                         [[("a", 9), ("b", 3)], [("a", 2), ("b", 1)]])

    def test_pin_manifest_rejects_unknown_project(self):
        with self.assertRaisesRegex(ns.StageError, "no nightly pin"):
            ns.pin_manifest(MANIFEST, {"linux-rockchip-kernel": "a" * 40}, "nightly/x")


class PruneTests(unittest.TestCase):
    def test_deletes_old_branches_and_reports_expired_tags(self):
        gh = build_fixture()
        today = dt.date(2026, 8, 25)
        for date in ("2026-08-01", "2026-08-10", "2026-08-12", "2026-08-24"):
            for repo in gh.repos:
                gh.repos[repo]["branches"][f"nightly/{date}"] = gh.sha()
        gh.repos["manifest"]["branches"]["nightly/not-a-date"] = gh.sha()
        # Tags carry their dates in nightly.json; the newest is held back.
        for n, date in ((1, "2026-08-01"), (2, "2026-08-10"), (3, "2026-08-12"), (4, "2026-08-24")):
            r = gh.repos["manifest"]
            sha = gh._new_commit(r, r["branches"]["main"], {"nightly.json": json.dumps({"date": date})})
            r["tags"][f"rk3576-v0.2.0-nightly.{n}"] = sha
        gh.repos["manifest"]["tags"]["rk3576-v0.1.0"] = gh.sha()

        report = ns.prune(gh, "o", "manifest", "rk3576-debian-ab.xml", 14, today, False,
                          log=lambda *_: None)

        self.assertEqual(report["cutoff"], "2026-08-11")
        deleted = {(d["repo"], d["branch"]) for d in report["deleted"]}
        self.assertEqual(len(deleted), 3 * 2)
        for repo in gh.repos:
            self.assertEqual(
                sorted(b for b in gh.repos[repo]["branches"] if b.startswith("nightly/")),
                sorted(["nightly/2026-08-12", "nightly/2026-08-24"] +
                       (["nightly/not-a-date"] if repo == "manifest" else [])))
        self.assertEqual(report["expired_tags"], ["rk3576-v0.2.0-nightly.1", "rk3576-v0.2.0-nightly.2"])

    def test_newest_nightly_is_never_expired(self):
        gh = build_fixture()
        r = gh.repos["manifest"]
        sha = gh._new_commit(r, r["branches"]["main"], {"nightly.json": json.dumps({"date": "2026-01-01"})})
        r["tags"]["rk3576-v0.2.0-nightly.1"] = sha
        report = ns.prune(gh, "o", "manifest", "rk3576-debian-ab.xml", 14, dt.date(2026, 8, 25),
                          False, log=lambda *_: None)
        self.assertEqual(report["expired_tags"], [])

    def test_dry_run_deletes_nothing(self):
        gh = build_fixture()
        gh.repos["manifest"]["branches"]["nightly/2026-01-01"] = gh.sha()
        report = ns.prune(gh, "o", "manifest", "rk3576-debian-ab.xml", 14, dt.date(2026, 8, 25),
                          True, log=lambda *_: None)
        self.assertEqual(len(report["deleted"]), 1)
        self.assertEqual(gh.writes, [])
        self.assertIn("nightly/2026-01-01", gh.repos["manifest"]["branches"])


if __name__ == "__main__":
    unittest.main()
