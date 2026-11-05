import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/dependabot-rebuild-dist.yml").read_text()
VALIDATE = (ROOT / "scripts/validate.py").read_text()
PUBLISH_MODULE = (ROOT / "scripts/publish.py").read_text()
PACKAGE = PUBLISH_MODULE + "\npackage()\n"
PUBLISH = PUBLISH_MODULE + "\npublish()\n"
REAL_RUN = subprocess.run


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.remote = self.root / "remote.git"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.repo.mkdir()
        self.git("init", "-b", "update")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "base.txt").write_text("base")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.ancestor = self.git("rev-parse", "HEAD").strip()
        (self.repo / "dist").mkdir()
        (self.repo / "dist/bundle.js").write_text("old")
        (self.repo / "dist/deleted.js").write_text("delete")
        self.git("add", ".")
        self.git("commit", "-m", "Dependabot update")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "origin", "HEAD:refs/heads/update")
        self.pr = {
            "state": "open",
            "user": {"login": "dependabot[bot]"},
            "head": {
                "repo": {"full_name": "owner/repo"},
                "sha": self.sha,
                "ref": "update",
            },
        }
        self.env = {
            "GH_TOKEN": "read-token",
            "PUBLISH_TOKEN": "test-publish-token",
            "PR_NUMBER": "1",
            "EXPECTED_HEAD_SHA": self.sha,
            "HEAD_REF": "update",
            "DIST_PATH": "dist",
            "REPOSITORY": "owner/repo",
            "ARTIFACT_DIRECTORY": str(self.artifacts),
            "RUNNER_TEMP": str(self.root),
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_OUTPUT": str(self.root / "outputs"),
            "EVENT_NAME": "pull_request",
            "EVENT_ACTOR": "dependabot[bot]",
            "EVENT_PR_AUTHOR": "dependabot[bot]",
            "EVENT_PR_NUMBER": "1",
            "EVENT_HEAD_SHA": self.sha,
            "EVENT_HEAD_REF": "update",
            "DIST_PATH_INPUT": "dist",
            "OUTPUT_ARCHIVE": str(self.artifacts / "generated-dist.zip"),
        }
        self.calls = []
        self.api_calls = 0

    def git(self, *arguments, cwd=None):
        result = REAL_RUN(
            ["git", *arguments],
            cwd=cwd or self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def remote_sha(self):
        return self.git("--git-dir", str(self.remote), "rev-parse", "refs/heads/update").strip()

    def api(self, request, timeout):
        self.assertEqual(timeout, 20)
        self.assertNotIn("test-publish-token", str(request.headers))
        self.api_calls += 1
        return io.BytesIO(json.dumps(self.pr).encode())

    def run_script(self, script, api=None, intercept=None, overrides=None):
        output = io.StringIO()
        previous_cwd = Path.cwd()
        environment = dict(os.environ)
        environment.pop("PUBLISH_TOKEN", None)
        environment.update(self.env)
        environment.update(overrides or {})

        def run(arguments, **kwargs):
            is_push = arguments[0] == "git" and "push" in arguments
            token = kwargs.get("env", os.environ).get("PUBLISH_TOKEN")
            if script == PUBLISH:
                self.assertEqual(token is not None, is_push)
            self.calls.append(arguments)
            if intercept:
                result = intercept(arguments, kwargs)
                if result is not None:
                    return result
            if "stdout" not in kwargs and "stderr" not in kwargs:
                kwargs.setdefault("capture_output", True)
            return REAL_RUN(arguments, **kwargs)

        try:
            os.chdir(self.repo)
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch("urllib.request.urlopen", side_effect=api or self.api),
                mock.patch("subprocess.run", side_effect=run),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(output),
            ):
                try:
                    exec(compile(script, "workflow-script", "exec"), {"__name__": "workflow_test"})
                except SystemExit as error:
                    if isinstance(error.code, str):
                        return 1, output.getvalue() + error.code
                    return error.code, output.getvalue()
            return 0, output.getvalue()
        finally:
            os.chdir(previous_cwd)

    def package(self):
        status, output = self.run_script(PACKAGE)
        self.assertEqual(status, 0, output)

    def changed_archive(self):
        (self.repo / "dist/bundle.js").write_text("rebuilt")
        (self.repo / "dist/deleted.js").unlink()
        (self.repo / "dist/new.js").write_text("added")
        self.package()
        self.git("restore", "dist")
        (self.repo / "dist/new.js").unlink()

    def write_archive(self, entries):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.env["OUTPUT_ARCHIVE"], "w") as archive:
                root = zipfile.ZipInfo("dist/")
                root.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(root, b"")
                for name, mode, data in entries:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = mode << 16
                    archive.writestr(info, data)

    def assert_rejected(self, expected, **kwargs):
        before = self.remote_sha()
        status, output = self.run_script(PUBLISH, **kwargs)
        self.assertNotEqual(status, 0, output)
        self.assertIn(expected, output)
        self.assertEqual(self.remote_sha(), before)
        return output

    def test_success_add_modify_delete_and_unrelated_changes(self):
        self.changed_archive()
        (self.repo / "base.txt").write_text("unrelated")
        status, output = self.run_script(PUBLISH)
        self.assertEqual(status, 0, output)
        self.assertNotEqual(self.remote_sha(), self.sha)
        changed = self.git("diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD")
        self.assertEqual(
            set(changed.splitlines()),
            {"M\tdist/bundle.js", "D\tdist/deleted.js", "A\tdist/new.js"},
        )
        self.assertIn("[dependabot skip]", self.git("log", "-1", "--format=%s"))
        self.assertEqual(list(self.root.glob("dist-push-*")), [])

    def test_no_op(self):
        self.package()
        status, output = self.run_script(PUBLISH)
        self.assertEqual(status, 0, output)
        self.assertIn("no commit is needed", output)
        self.assertEqual(self.remote_sha(), self.sha)
        self.assertFalse(any("push" in call for call in self.calls))

    def test_missing_generated_directory(self):
        self.env["DIST_PATH"] = "missing"
        status, output = self.run_script(PACKAGE)
        self.assertNotEqual(status, 0)
        self.assertIn("directory does not exist", output)

    def test_missing_pat_before_api_or_git(self):
        self.assert_rejected("PAT is empty or unavailable", overrides={"PUBLISH_TOKEN": ""})
        self.assertEqual(self.api_calls, 0)
        self.assertEqual(self.calls, [])

    def test_validation_never_needs_pat(self):
        self.env.pop("PUBLISH_TOKEN")
        status, output = self.run_script(VALIDATE)
        self.assertEqual(status, 0, output)
        self.assertIn(f"head-sha={self.sha}", Path(self.env["GITHUB_OUTPUT"]).read_text())

    def test_invalid_events_authors_and_paths(self):
        for key, value in [
            ("EVENT_ACTOR", "pat-owner"),
            ("EVENT_PR_AUTHOR", "contributor"),
            ("EVENT_NAME", "pull_request_target"),
            ("DIST_PATH_INPUT", "../dist"),
            ("DIST_PATH_INPUT", "/dist"),
            ("DIST_PATH_INPUT", ".git"),
            ("DIST_PATH_INPUT", "dist\\bad"),
        ]:
            with self.subTest(key=key, value=value):
                status, output = self.run_script(VALIDATE, overrides={key: value})
                self.assertNotEqual(status, 0, output)

    def test_pr_eligibility_before_build_and_publication(self):
        for change in ("sha", "ref", "closed", "author", "fork"):
            for script in (VALIDATE, PUBLISH):
                with self.subTest(change=change, phase="validate" if script == VALIDATE else "publish"):
                    pr = copy.deepcopy(self.pr)
                    if change in ("sha", "ref"):
                        pr["head"][change] = "f" * 40 if change == "sha" else "another"
                    elif change == "closed":
                        pr["state"] = "closed"
                    elif change == "author":
                        pr["user"]["login"] = "someone"
                    else:
                        pr["head"]["repo"] = None
                    response = lambda *args, **kwargs: io.BytesIO(json.dumps(pr).encode())
                    status, output = self.run_script(script, api=response)
                    self.assertNotEqual(status, 0, output)
                    self.assertEqual(self.remote_sha(), self.sha)

    def test_api_failures_fail_closed(self):
        failures = [
            urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None),
            urllib.error.URLError("unavailable"),
            TimeoutError("timeout"),
        ]
        for script in (VALIDATE, PUBLISH):
            for error in failures:
                with self.subTest(error=type(error).__name__, phase=script[:20]):
                    def api(*args, **kwargs):
                        raise error
                    status, output = self.run_script(script, api=api)
                    self.assertNotEqual(status, 0)
                    self.assertIn("GitHub PR inspection failed", output)
            for payload in (b"not-json", b"{}", b"[]", b"null"):
                with self.subTest(payload=payload):
                    status, output = self.run_script(
                        script, api=lambda *args, **kwargs: io.BytesIO(payload)
                    )
                    self.assertNotEqual(status, 0)
                    self.assertIn("::error::", output)

    def test_final_api_check_rejects_changed_or_closed_pr(self):
        self.changed_archive()
        for close in (False, True):
            with self.subTest(close=close):
                self.git("reset", "--soft", self.sha)
                self.git("restore", f"--source={self.sha}", "--staged", "--worktree", ".")
                count = 0
                def api(*args, **kwargs):
                    nonlocal count
                    count += 1
                    pr = copy.deepcopy(self.pr)
                    if count == 2:
                        if close:
                            pr["state"] = "closed"
                        else:
                            pr["head"]["sha"] = "f" * 40
                    return io.BytesIO(json.dumps(pr).encode())
                self.assert_rejected("no longer eligible", api=api)

    def test_push_permission_and_infrastructure_errors_are_not_stale(self):
        self.changed_archive()
        for diagnostic in (
            "remote: Permission denied (403)",
            "Could not resolve host: github.com",
            "! [rejected] HEAD -> update (repository policy)",
        ):
            with self.subTest(diagnostic=diagnostic):
                self.git("reset", "--soft", self.sha)
                self.git("restore", f"--source={self.sha}", "--staged", "--worktree", ".")
                def intercept(arguments, kwargs):
                    if "push" in arguments:
                        return subprocess.CompletedProcess(arguments, 1, "", diagnostic)
                output = self.assert_rejected("No retry was attempted", intercept=intercept)
                self.assertNotIn("branch changed during publication", output)
                self.assertEqual(list(self.root.glob("dist-push-*")), [])

    def test_remote_ancestor_reset_is_rejected(self):
        self.changed_archive()
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/update", self.ancestor)
        self.assert_rejected("Remote branch no longer matches the built SHA")

    def test_normal_push_without_guard_accepts_ancestor_reset(self):
        self.git("commit", "--allow-empty", "-m", "stale publisher")
        publisher_sha = self.git("rev-parse", "HEAD").strip()
        self.git("--git-dir", str(self.remote), "update-ref", "refs/heads/update", self.ancestor)
        self.git("push", "origin", "HEAD:refs/heads/update")
        self.assertEqual(self.remote_sha(), publisher_sha)

    def test_remote_forward_update_is_rejected(self):
        self.changed_archive()
        new_sha = self.git("commit-tree", f"{self.sha}^{{tree}}", "-p", self.sha, "-m", "new").strip()
        self.git("push", "origin", f"{new_sha}:refs/heads/update")
        self.assert_rejected("branch changed during publication")

    def test_remote_branch_deleted_is_rejected(self):
        self.changed_archive()
        self.git("--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/update")
        status, output = self.run_script(PUBLISH)
        self.assertNotEqual(status, 0)
        self.assertIn("Remote branch no longer matches", output)
        refs = REAL_RUN(
            ["git", "--git-dir", str(self.remote), "show-ref", "--heads"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(refs.returncode, 1)
        self.assertEqual(refs.stdout, "")

    def test_refresh_build_uses_new_head_and_rejects_old_event(self):
        self.git("commit", "--allow-empty", "-m", "Dependabot refreshed update")
        new_sha = self.git("rev-parse", "HEAD").strip()
        self.git("push", "origin", "HEAD:refs/heads/update")
        self.pr["head"]["sha"] = new_sha
        status, output = self.run_script(VALIDATE)
        self.assertNotEqual(status, 0, output)
        self.env["EVENT_HEAD_SHA"] = new_sha
        self.env["EXPECTED_HEAD_SHA"] = new_sha
        status, output = self.run_script(VALIDATE)
        self.assertEqual(status, 0, output)
        self.assertIn(f"head-sha={new_sha}", Path(self.env["GITHUB_OUTPUT"]).read_text())
        self.changed_archive()
        status, output = self.run_script(PUBLISH)
        self.assertEqual(status, 0, output)
        self.assertEqual(self.git("rev-parse", "HEAD^").strip(), new_sha)

    def test_update_after_advertisement_is_rejected_by_receive_pack(self):
        self.changed_archive()
        injected = False
        def intercept(arguments, kwargs):
            nonlocal injected
            if "push" in arguments and not injected:
                injected = True
                hook_directory = next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("core.hooksPath="))
                hook = Path(hook_directory) / "pre-push"
                hook.write_text(
                    hook.read_text()
                    + f"git --git-dir='{self.remote}' update-ref refs/heads/update {self.ancestor}\n"
                )
        status, output = self.run_script(PUBLISH, intercept=intercept)
        self.assertNotEqual(status, 0, output)
        self.assertEqual(self.remote_sha(), self.ancestor)
        self.assertIn("could not be pushed", output)

    def test_server_policy_rejection_is_not_reported_as_stale(self):
        self.changed_archive()
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho 'Repository policy forbids this push' >&2\nexit 1\n")
        hook.chmod(0o700)
        output = self.assert_rejected("No retry was attempted")
        self.assertIn("Repository policy forbids", output)
        self.assertNotIn("branch changed during publication", output)

    def test_two_publishers_only_one_updates_remote(self):
        self.changed_archive()
        second = self.root / "second"
        self.git("clone", "--branch", "update", str(self.remote), str(second))
        for key, value in [("user.name", "test"), ("user.email", "test@example.invalid")]:
            self.git("config", key, value, cwd=second)
        winner = None
        ran = False
        def intercept(arguments, kwargs):
            nonlocal ran, winner
            if "push" in arguments and not ran:
                ran = True
                current_repo = self.repo
                self.repo = second
                # The second publisher produces a different commit from the same parent.
                second_env = {"GIT_COMMITTER_DATE": "2001-01-01T00:00:00+00:00"}
                try:
                    code, output = self.run_script(PUBLISH, overrides=second_env)
                    self.assertEqual(code, 0, output)
                    winner = self.remote_sha()
                finally:
                    self.repo = current_repo
        status, output = self.run_script(PUBLISH, intercept=intercept)
        self.assertNotEqual(status, 0, output)
        self.assertIsNotNone(winner)
        self.assertEqual(self.remote_sha(), winner)

    def test_unsafe_archive_entries(self):
        entries = [
            ("../outside", stat.S_IFREG | 0o644, b"x"),
            ("/dist/absolute", stat.S_IFREG | 0o644, b"x"),
            ("dist/../outside", stat.S_IFREG | 0o644, b"x"),
            ("dist\\escape", stat.S_IFREG | 0o644, b"x"),
            ("dist/control\n", stat.S_IFREG | 0o644, b"x"),
            ("dist/.git/config", stat.S_IFREG | 0o644, b"x"),
            ("outside", stat.S_IFREG | 0o644, b"x"),
            ("dist/link", stat.S_IFLNK | 0o777, b"../../outside"),
            ("dist/fifo", stat.S_IFIFO | 0o644, b""),
            ("dist/wrong/", stat.S_IFREG | 0o644, b""),
        ]
        for entry in entries:
            with self.subTest(path=entry[0]):
                self.write_archive([entry])
                self.assert_rejected("::error::")
                self.assertEqual((self.repo / "dist/bundle.js").read_text(), "old")

    def test_duplicates_and_file_directory_conflicts(self):
        regular = stat.S_IFREG | 0o644
        for entries in (
            [("dist/a", regular, b"a"), ("dist/a", regular, b"b")],
            [("dist/a", regular, b"a"), ("dist/a/b", regular, b"b")],
            [("dist/a", regular, b"a"), ("dist/a/", stat.S_IFDIR | 0o755, b"")],
        ):
            with self.subTest(entries=entries):
                self.write_archive(entries)
                self.assert_rejected("::error::")

    def test_malformed_and_corrupt_archives(self):
        archive = Path(self.env["OUTPUT_ARCHIVE"])
        archive.write_bytes(b"not a zip")
        self.assert_rejected("not a readable ZIP")
        self.write_archive([("dist/bundle.js", stat.S_IFREG | 0o644, b"unique-payload")])
        archive.write_bytes(archive.read_bytes().replace(b"unique-payload", b"broken-payload"))
        self.assert_rejected("CRC check")

    def test_archive_resource_limits(self):
        archive = Path(self.env["OUTPUT_ARCHIVE"])
        with archive.open("wb") as output:
            output.truncate(128 * 1024 * 1024 + 1)
        self.assert_rejected("128 MiB")
        self.write_archive([])
        for count, size, expected in (
            (20001, 0, "20000 entry"),
            (1, 512 * 1024 * 1024 + 1, "512 MiB"),
        ):
            info = zipfile.ZipInfo("dist/")
            info.file_size = size
            with mock.patch.object(zipfile.ZipFile, "infolist", return_value=[info] * count):
                self.assert_rejected(expected)

    def test_encrypted_and_unsupported_compression_are_rejected(self):
        self.write_archive([])
        for encrypted in (True, False):
            with self.subTest(encrypted=encrypted):
                info = zipfile.ZipInfo("dist/")
                info.flag_bits = 1 if encrypted else 0
                info.compress_type = zipfile.ZIP_STORED if encrypted else zipfile.ZIP_LZMA
                with mock.patch.object(zipfile.ZipFile, "infolist", return_value=[info]):
                    self.assert_rejected("encrypted or unsupported")

    def test_nul_truncated_zip_names_are_rejected(self):
        self.write_archive([])
        info = zipfile.ZipInfo("dist/safe\x00hidden")
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        with mock.patch.object(zipfile.ZipFile, "infolist", return_value=[info]):
            self.assert_rejected("Unsafe path")

    def test_symlinked_destination_parent_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / "linked").symlink_to(outside, target_is_directory=True)
        self.env["DIST_PATH"] = "linked/dist"
        with zipfile.ZipFile(self.env["OUTPUT_ARCHIVE"], "w") as archive:
            info = zipfile.ZipInfo("linked/dist/")
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")
        self.assert_rejected("not a safe directory")
        self.assertFalse((outside / "dist").exists())

    def test_packager_rejects_symlinks(self):
        (self.repo / "dist/link").symlink_to(self.repo / "base.txt")
        status, output = self.run_script(PACKAGE)
        self.assertNotEqual(status, 0)
        self.assertIn("symbolic link", output)

    def test_packaging_script_entrypoint(self):
        result = REAL_RUN(
            [sys.executable, "-I", str(ROOT / "scripts/publish.py"), "--package"],
            cwd=self.repo,
            env=os.environ | {
                "DIST_PATH": self.env["DIST_PATH"],
                "OUTPUT_ARCHIVE": self.env["OUTPUT_ARCHIVE"],
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(self.env["OUTPUT_ARCHIVE"]) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"dist/", "dist/bundle.js", "dist/deleted.js"},
            )

    def test_publisher_script_rejects_invalid_arguments(self):
        result = REAL_RUN(
            [sys.executable, "-I", str(ROOT / "scripts/publish.py"), "--invalid"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage: publish.py [--package]", result.stderr)

    def test_file_permissions_are_normalized(self):
        self.write_archive([
            ("dist/world-writable", stat.S_IFREG | 0o666, b"x"),
            ("dist/executable", stat.S_IFREG | 0o4777, b"x"),
        ])
        status, output = self.run_script(PUBLISH)
        self.assertEqual(status, 0, output)
        self.assertEqual(stat.S_IMODE((self.repo / "dist/world-writable").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.repo / "dist/executable").stat().st_mode), 0o755)

    def test_ignored_generated_additions_are_committed(self):
        (self.repo / ".gitignore").write_text("dist/ignored.js\n")
        (self.repo / "dist/ignored.js").write_text("new")
        self.package()
        status, output = self.run_script(PUBLISH)
        self.assertEqual(status, 0, output)
        self.assertIn("dist/ignored.js", self.git("ls-tree", "-r", "--name-only", "HEAD"))

    def test_staged_unrelated_changes_are_rejected(self):
        self.package()
        (self.repo / "base.txt").write_text("staged")
        self.git("add", "base.txt")
        self.assert_rejected("unexpected staged changes")

    def test_python_isolation_blocks_pr_module_execution(self):
        (self.repo / "shutil.py").write_text("raise RuntimeError('PR module executed')\n")
        result = REAL_RUN(
            [sys.executable, "-I", str(ROOT / "scripts/publish.py")],
            cwd=self.repo,
            env={**os.environ, **self.env, "PUBLISH_TOKEN": ""},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PAT is empty", result.stdout)
        self.assertNotIn("PR module executed", result.stderr)

    def test_install_and_build_failures_stop_pipeline(self):
        commands = re.findall(r'run: (bash --noprofile --norc -euo pipefail -c "\$\w+")', WORKFLOW)
        self.assertEqual(len(commands), 2)
        for failing_phase in ("install", "build"):
            with self.subTest(failing_phase=failing_phase):
                marker = self.root / f"{failing_phase}-published"
                install = "exit 17" if failing_phase == "install" else "true"
                build = "exit 18" if failing_phase == "build" else "true"
                pipeline = "set -e\n" + "\n".join(commands) + f"\ntouch '{marker}'\n"
                result = REAL_RUN(
                    ["bash", "-c", pipeline],
                    env=os.environ | {"INSTALL_COMMAND": install, "BUILD_COMMAND": build},
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists())


class WorkflowContractTests(unittest.TestCase):
    def test_job_permissions_and_isolation(self):
        validate, rest = WORKFLOW.split("\n  build:", 1)
        build, publish = rest.split("\n  publish:", 1)
        self.assertNotIn("secrets.token", validate.split("\njobs:", 1)[1])
        self.assertNotIn("PUBLISH_TOKEN", validate)
        self.assertNotIn("secrets.token", build)
        self.assertNotIn("PUBLISH_TOKEN", build)
        self.assertIn("persist-credentials: false", build)
        self.assertIn("PUBLISH_TOKEN: ${{ secrets.token }}", publish)
        self.assertNotIn("inputs.install-command", publish)
        self.assertNotIn("inputs.build-command", publish)
        self.assertNotIn("write", WORKFLOW.split("\njobs:", 1)[1].split("steps:", 1)[0])
        self.assertIn("needs:\n      - validate\n      - build", WORKFLOW)
        self.assertNotIn("continue-on-error", WORKFLOW)
        self.assertNotIn("--force", WORKFLOW)
        self.assertNotIn("<<'PY'", WORKFLOW)
        self.assertIn('scripts/validate.py"', WORKFLOW)
        self.assertIn('scripts/publish.py" --package', WORKFLOW)

    def test_helpers_are_pinned_and_isolated_from_caller_checkout(self):
        self.assertNotIn("implementation-repository", WORKFLOW)
        self.assertNotIn("implementation-ref", WORKFLOW)
        self.assertEqual(WORKFLOW.count("repository: v-chiranjib-swain/reusable-workflows"), 3)
        pins = re.findall(r"ref: ([0-9a-f]{40}) # Reviewed helper implementation", WORKFLOW)
        self.assertEqual(len(pins), 3)
        self.assertEqual(len(set(pins)), 1)
        self.assertEqual(WORKFLOW.count("sparse-checkout: scripts"), 3)
        self.assertEqual(WORKFLOW.count("persist-credentials: false"), 5)
        self.assertEqual(WORKFLOW.count('run: mv "$GITHUB_WORKSPACE/.dependabot-rebuild-implementation" "$RUNNER_TEMP/dependabot-rebuild-implementation"'), 3)
        self.assertNotIn("github.event.pull_request.head.repo", WORKFLOW)

    def test_documented_caller_loop_prevention_contract(self):
        readme = (ROOT / "README.md").read_text()
        section = readme.split("- [`dependabot-rebuild-dist.yml`]", 1)[1].split("- [`codeql-analysis.yml`]", 1)[0]
        for expression in (
            "github.actor == 'dependabot[bot]'",
            "github.event.pull_request.user.login == 'dependabot[bot]'",
            "github.event.pull_request.head.repo.full_name == github.repository",
            "types: [opened, reopened, synchronize]",
            "cancel-in-progress: true",
            "group: dependabot-rebuild-${{ github.event.pull_request.number }}",
        ):
            self.assertIn(expression, section)
        self.assertIn('os.environ["EVENT_ACTOR"] != "dependabot[bot]"', VALIDATE)
        self.assertNotIn("[skip ci]", PUBLISH)
        self.assertNotIn("[ci skip]", PUBLISH)


if __name__ == "__main__":
    unittest.main()
