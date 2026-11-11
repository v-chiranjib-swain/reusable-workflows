import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

def fail(message):
    print(f"::error::{message}")
    sys.exit(1)

if os.environ["EVENT_NAME"] != "pull_request":
    fail("This workflow only supports pull_request events.")
if os.environ["EVENT_ACTOR"] != "dependabot[bot]":
    fail("This workflow only accepts events initiated by dependabot[bot].")
if os.environ["EVENT_PR_AUTHOR"] != "dependabot[bot]":
    fail("The pull request must be authored by dependabot[bot].")

repository = os.environ["GITHUB_REPOSITORY"]
pr_number = os.environ["EVENT_PR_NUMBER"]
event_sha = os.environ["EVENT_HEAD_SHA"]
event_ref = os.environ["EVENT_HEAD_REF"]
if not pr_number.isdigit() or not re.fullmatch(r"[0-9a-fA-F]{40}", event_sha):
    fail("The caller must provide a pull_request event with a valid head SHA.")

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
        pull_request = json.load(response)
except urllib.error.HTTPError as error:
    fail(f"GitHub PR inspection failed with HTTP {error.code}; check token permissions and API availability.")
except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
    fail(f"GitHub PR inspection failed ({type(error).__name__}); no build was started.")

try:
    head = pull_request["head"]
    state = pull_request["state"]
    author = pull_request["user"]["login"]
    head_repository = head["repo"]["full_name"] if head["repo"] is not None else None
    head_sha = head["sha"]
    head_ref = head["ref"]
except (KeyError, TypeError):
    fail("GitHub returned an unexpected PR response; no build was started.")
if not all(isinstance(value, str) for value in (state, author, head_sha, head_ref)):
    fail("GitHub returned invalid PR field types; no build was started.")
if state != "open":
    fail("The pull request is no longer open.")
if author != "dependabot[bot]":
    fail("The current pull request author is not dependabot[bot].")
if head_repository != repository:
    fail("Dependabot pull requests from forks cannot be published.")
if head["sha"] != event_sha or head["ref"] != event_ref:
    fail("The pull request changed after this workflow event; start a fresh run.")
if subprocess.run(
    ["git", "check-ref-format", f"refs/heads/{head['ref']}"],
    check=False,
).returncode != 0:
    fail("The pull request branch name is invalid.")

raw_path = os.environ["DIST_PATH_INPUT"]
if "\\" in raw_path or any(ord(char) < 32 or ord(char) == 127 for char in raw_path):
    fail("dist-path must be a safe repository-relative POSIX path.")
while raw_path.startswith("./"):
    raw_path = raw_path[2:]
parts = raw_path.split("/")
if (
    not raw_path
    or raw_path.startswith("/")
    or any(part in ("", ".", "..", ".git") for part in parts)
):
    fail("dist-path must be a safe repository-relative directory outside Git metadata.")
dist_path = "/".join(parts)

with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write(f"head-ref={head['ref']}\n")
    output.write(f"head-sha={head['sha']}\n")
    output.write(f"pr-number={pr_number}\n")
    output.write(f"dist-path={dist_path}\n")
