import sys


def package():
    import os
    import stat
    import sys
    import zipfile
    from pathlib import Path

    root = Path(os.environ["DIST_PATH"])
    current = Path(".")
    for part in root.parts:
        current = current / part
        if current.is_symlink():
            sys.exit(f"::error::dist-path contains a symbolic link: {current}")
    if not root.is_dir():
        sys.exit(f"::error::Generated output directory does not exist: {root}")

    archive = Path(os.environ["OUTPUT_ARCHIVE"])
    def raise_walk_error(error):
        raise error

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.write(root, root.as_posix() + "/")
        for directory, subdirectories, filenames in os.walk(
            root, followlinks=False, onerror=raise_walk_error
        ):
            directory_path = Path(directory)
            subdirectories.sort()
            filenames.sort()
            for name in subdirectories + filenames:
                entry = directory_path / name
                mode = entry.lstat().st_mode
                if stat.S_ISLNK(mode):
                    sys.exit(f"::error::Generated output contains a symbolic link: {entry}")
                if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                    output.write(entry, entry.as_posix())
                else:
                    sys.exit(f"::error::Generated output contains an unsupported file type: {entry}")


def publish():
    import json
    import os
    import shutil
    import stat
    import subprocess
    import sys
    import tempfile
    import urllib.error
    import urllib.request
    import zipfile
    import zlib
    from pathlib import Path, PurePosixPath

    def fail(message):
        print(f"::error::{message}")
        sys.exit(1)

    if not os.environ.get("PUBLISH_TOKEN"):
        fail("The required publishing PAT is empty or unavailable.")
    publish_token = os.environ.pop("PUBLISH_TOKEN")

    repository = os.environ["REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    expected_sha = os.environ["EXPECTED_HEAD_SHA"]
    head_ref = os.environ["HEAD_REF"]
    dist_path = os.environ["DIST_PATH"]
    artifact_directory = Path(os.environ["ARTIFACT_DIRECTORY"])
    workspace = Path.cwd()

    def live_pull_request():
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/pulls/{pr_number}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            fail(f"GitHub PR inspection failed with HTTP {error.code}; check token permissions and API availability.")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            fail(f"GitHub PR inspection failed ({type(error).__name__}); no publication was attempted.")

    def verify_head():
        pull_request = live_pull_request()
        try:
            head = pull_request["head"]
            state = pull_request["state"]
            author = pull_request["user"]["login"]
            head_repository = head["repo"]["full_name"] if head["repo"] is not None else None
            head_sha = head["sha"]
            current_ref = head["ref"]
        except (KeyError, TypeError):
            fail("GitHub returned an unexpected PR response; no publication was attempted.")
        if (
            state != "open"
            or author != "dependabot[bot]"
            or head_repository != repository
            or head_sha != expected_sha
            or current_ref != head_ref
        ):
            fail("The Dependabot pull request changed or is no longer eligible; no files were published.")

    verify_head()
    current_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    if current_sha != expected_sha:
        fail("Publisher checkout does not match the validated Dependabot commit.")
    if subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{head_ref}"], check=False
    ).returncode != 0:
        fail("The pull request branch name is invalid.")

    parts = dist_path.split("/")
    if (
        not dist_path
        or dist_path.startswith("/")
        or "\\" in dist_path
        or any(ord(char) < 32 or ord(char) == 127 for char in dist_path)
        or any(part in ("", ".", "..", ".git") for part in parts)
    ):
        fail("The validated dist-path is unsafe.")

    archives = [
        item for item in artifact_directory.iterdir()
        if item.name == "generated-dist.zip" and item.is_file() and not item.is_symlink()
    ]
    if len(archives) != 1 or len(list(artifact_directory.iterdir())) != 1:
        fail("The generated artifact must contain only generated-dist.zip.")
    archive = archives[0]
    max_archive_bytes = 128 * 1024 * 1024
    max_output_bytes = 512 * 1024 * 1024
    max_entries = 20000
    if archive.stat().st_size > max_archive_bytes:
        fail("Generated archive exceeds the 128 MiB compressed size limit.")
    root_name = "/".join(parts)
    entries = []
    names = set()
    try:
        generated_archive = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError):
        fail("Generated artifact is not a readable ZIP archive.")
    with generated_archive as generated:
        if len(generated.infolist()) > max_entries:
            fail("Generated archive exceeds the 20000 entry limit.")
        if sum(info.file_size for info in generated.infolist()) > max_output_bytes:
            fail("Generated archive exceeds the 512 MiB decompressed size limit.")
        for info in generated.infolist():
            if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail("Generated archive contains encrypted or unsupported compressed entries.")
            name = info.filename
            if (
                not name
                or info.orig_filename != name
                or name.startswith("/")
                or "\\" in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
            ):
                fail(f"Unsafe path in generated artifact: {name!r}")
            is_directory = info.is_dir()
            entry_name = name[:-1] if is_directory and name.endswith("/") else name
            entry_parts = tuple(entry_name.split("/"))
            if (
                not entry_parts
                or (is_directory and not name.endswith("/"))
                or any(part in ("", ".", "..", ".git") for part in entry_parts)
                or entry_parts[:len(parts)] != tuple(parts)
            ):
                fail(f"Generated artifact contains a path outside dist-path: {name!r}")
            normalized_name = "/".join(entry_parts)
            if normalized_name in names:
                fail(f"Generated artifact contains a duplicate path: {name!r}")
            names.add(normalized_name)
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                fail(f"Generated artifact contains an unsupported file type: {name!r}")
            if info.is_dir() != (mode == stat.S_IFDIR):
                fail(f"Generated artifact has inconsistent file metadata: {name!r}")
            entries.append((info, entry_parts, is_directory))

        if root_name not in names or not any(is_directory and "/".join(parts_) == root_name for _, parts_, is_directory in entries):
            fail("Generated artifact does not contain its expected dist-path directory.")

        file_paths = {
            "/".join(entry_parts)
            for _, entry_parts, is_directory in entries
            if not is_directory
        }
        for file_path in file_paths:
            parent = PurePosixPath(file_path).parent
            while str(parent) != ".":
                if str(parent) in file_paths:
                    fail(f"Generated artifact has a file used as a parent directory: {file_path!r}")
                parent = parent.parent

        try:
            corrupt_entry = generated.testzip()
        except (zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error):
            fail("Generated archive is corrupt or unreadable; no files were staged.")
        if corrupt_entry is not None:
            fail("Generated archive failed its CRC check; no files were staged.")

        target = workspace.joinpath(*parts)
        current = workspace
        for part in parts[:-1]:
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                fail(f"dist-path parent is not a safe directory: {current}")

        if target.is_symlink() or (target.exists() and not target.is_dir()):
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        for info, entry_parts, is_directory in sorted(
            entries, key=lambda item: (len(item[1]), item[1])
        ):
            destination = workspace.joinpath(*entry_parts)
            if is_directory:
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with generated.open(info) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
            permissions = (info.external_attr >> 16) & 0o111
            destination.chmod(0o755 if permissions else 0o644)

    pathspec = f":(literal){dist_path}"
    if subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"]):
        fail("Publisher checkout has unexpected staged changes.")
    subprocess.run(["git", "add", "-f", "-A", "--", pathspec], check=True)
    diff_result = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", pathspec], check=False
    )
    if diff_result.returncode == 0:
        print("::notice::Generated output is already up to date; no commit is needed.")
        sys.exit(0)
    if diff_result.returncode != 1:
        fail("Unable to compare the staged generated output.")

    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"], check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-m",
            "build(deps): rebuild generated dist [dependabot skip]",
        ],
        check=True,
    )

    verify_head()
    credential_directory = Path(tempfile.mkdtemp(prefix="dist-push-", dir=os.environ["RUNNER_TEMP"]))
    askpass = credential_directory / "git-askpass"
    askpass.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
        '  *Password*) printf "%s\\n" "$PUBLISH_TOKEN" ;;\n'
        '  *) exit 1 ;;\nesac\n',
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    hook = credential_directory / "pre-push"
    # Git supplies the advertised old SHA; receive-pack locks and compares that SHA at update time.
    hook.write_text(
        '#!/bin/sh\nset -eu\ncount=0\n'
        'while read -r local_ref local_sha remote_ref remote_sha; do\n'
        '  count=$((count + 1))\n'
        '  if [ "$remote_ref" != "refs/heads/$HEAD_REF" ] || [ "$remote_sha" != "$EXPECTED_HEAD_SHA" ]; then\n'
        '    echo "Remote branch no longer matches the built SHA; publication rejected without retry." >&2\n'
        '    exit 1\n'
        '  fi\n'
        'done\n[ "$count" -le 1 ]\n',
        encoding="utf-8",
    )
    hook.chmod(0o700)
    push_environment = os.environ.copy()
    push_environment["PUBLISH_TOKEN"] = publish_token
    push_environment["GIT_ASKPASS"] = str(askpass)
    push_environment["GIT_TERMINAL_PROMPT"] = "0"
    push_environment["LC_ALL"] = "C"
    try:
        push_result = subprocess.run(
            ["git", "-c", f"core.hooksPath={credential_directory}", "push", "origin", f"HEAD:refs/heads/{head_ref}"],
            env=push_environment,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        shutil.rmtree(credential_directory)
    if push_result.returncode != 0:
        push_output = "\n".join(
            output.strip()
            for output in (push_result.stdout, push_result.stderr)
            if output.strip()
        )
        if push_output:
            print(push_output, file=sys.stderr)
        lowered_output = push_output.lower()
        if any(
            marker in lowered_output
            for marker in ("non-fast-forward", "fetch first", "remote branch no longer matches the built sha")
        ):
            fail(
                "The PR branch changed during publication, so Git rejected the push. "
                "The generated output was not retried against the newer head; rerun the workflow."
            )
        fail("The generated commit could not be pushed. No retry was attempted.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--package"]:
        package()
    elif not sys.argv[1:]:
        publish()
    else:
        sys.exit("Usage: publish.py [--package]")
