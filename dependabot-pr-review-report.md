# Dependabot generated-output rebuild: PR review report

## Review target

- Draft PR: https://github.com/v-chiranjib-swain/reusable-workflows/pull/1
- Branch: `feat/dependabot-dist-rebuild`
- Reviewed head: `3860e6b77ef2688557c82c3e96dda8ad8a6c23e9`
- Report date: 2026-11-05
- Implementation worktree is clean. This report is outside the repository and is not a PR change.

## Executive summary

Adds an opt-in reusable workflow that rebuilds generated output for eligible same-repository Dependabot PRs and publishes only the configured output directory. Existing reusable workflows, including read-only `check-dist.yml`, are unchanged.

The original 537-line implementation embedded three Python scripts in YAML. The refactor reduces YAML to 167 lines and moves the same validation, packaging, and publishing logic into two scripts. AST comparison confirmed the three extracted bodies were unchanged; packaging and publishing have explicit entry points.

The final extracted implementation passed 36 local regression tests and a genuine cross-repository Dependabot publication test. Three controlled push-race cases also passed against real GitHub scratch branches. Automatic Dependabot rebase after main advances remains pending.

## Files in this PR

Paths below are relative to the repository; GitHub links show the reviewed head.

| File | Change | Purpose |
|---|---|---|
| [.github/workflows/dependabot-rebuild-dist.yml](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/.github/workflows/dependabot-rebuild-dist.yml) | Added, 167 lines | Declarative job orchestration, inputs, permissions, trusted helper checkout, artifacts |
| [scripts/validate.py](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/scripts/validate.py) | Added, 86 lines | Event/API eligibility, exact head validation, safe output path, job outputs |
| [scripts/publish.py](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/scripts/publish.py) | Added, 334 lines | Output packaging and isolated publication |
| [tests/test_dependabot_rebuild_dist.py](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/tests/test_dependabot_rebuild_dist.py) | Added, 632 lines | Dependency-free regression tests against actual implementation scripts |
| [README.md](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/README.md) | Modified, 67 added lines | Caller setup, permissions, inputs, helper maintenance, operational behavior, test command |

PR #1 retains tests. The earlier separate workflow-only branch is not this review target. Disposable fixtures and race harnesses are in `v-chiranjib-swain/setup-node-test`, not in this PR.

## Execution flow and responsibilities

### 1. Validation job

- Checks out a fixed helper repository/commit without persisted credentials.
- Moves helpers outside the caller workspace to runner temporary storage.
- Executes `validate.py` using Python isolated mode (`-I`).
- Requires `pull_request`, Dependabot event actor and PR author, an open same-repository PR, and exact event SHA/ref matching the current API response.
- Rejects unsafe output paths and invalid Git branch refs.
- Emits head SHA, head ref, PR number, and normalized output directory.
- Permissions: `contents: read`, `pull-requests: read`; no publishing PAT.

### 2. Unprivileged build job

- Independently loads pinned helpers on its runner.
- Checks out the validated caller SHA with `persist-credentials: false`.
- Sets up Node, installs dependencies, and builds.
- Runs `publish.py --package`, which calls only `package()`.
- Packaging rejects missing output directories, symlinks, and unsupported filesystem types.
- Uploads `generated-dist.zip` as `generated-dist`, with one-day retention.
- Permissions: `contents: read`; no publishing PAT.

### 3. Publishing job

- Starts on a fresh runner and independently checks out the pinned helpers.
- Checks out the exact validated caller SHA without persisted credentials.
- Downloads the build artifact; does not run caller installation/build commands.
- Runs `publish.py` without arguments, which calls `publish()`.
- Rejects empty PAT, changed/ineligible PR, mismatched checkout, unsafe archive paths, duplicate paths, file/directory conflicts, unsupported types/compression, encrypted entries, and corrupt payloads.
- Enforces 128 MiB archive size, 512 MiB declared decompressed size, and 20,000-entry limits.
- Replaces only the configured generated directory; normalizes file modes to 0644/0755.
- Rejects unexpected pre-staged changes; stages with a literal pathspec, including additions, ignored generated files, and deletions.
- Creates no commit when output is unchanged.
- Commits with hooks disabled and message `build(deps): rebuild generated dist [dependabot skip]`.
- Rechecks PR eligibility before push.
- Uses a temporary pre-push hook to require the advertised remote SHA/ref to match the built SHA/ref, followed by a normal push. No forced publisher push or automatic retry.
- Removes temporary credential/hook files after the push attempt.
- Job token permissions: `contents: read`, `pull-requests: read`. Publishing uses the separately supplied PAT.

## Inputs and caller requirements

| Input/secret | Default or requirement |
|---|---|
| `install-command` | `npm ci --ignore-scripts` |
| `build-command` | `npm run build` |
| `node-version` | `24.x` |
| `dist-path` | `dist` |
| `token` | Required publishing PAT; Contents read/write for the caller repository |

The caller should pin the reusable workflow to a reviewed full SHA, map a Dependabot secret explicitly, restrict execution to Dependabot actor/author and same-repository PRs, handle opened/reopened/synchronize events, and configure per-PR concurrency. Do not use `secrets: inherit` or derive commands from PR-controlled text.

The actor gate prevents a PAT-generated synchronize event from starting another rebuild. `[dependabot skip]` permits Dependabot to replace extra commits; it is not the Actions loop-prevention mechanism.

## Two-commit helper versioning

1. [Commit A: a03a3b1](https://github.com/v-chiranjib-swain/reusable-workflows/commit/a03a3b12b2a46da54adf351efd278765764bda86) adds only the two scripts.
2. [Commit B: 3860e6b](https://github.com/v-chiranjib-swain/reusable-workflows/commit/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9) wires the workflow to Commit A and updates documentation/tests.

All three implementation checkouts hardcode:

```yaml
repository: v-chiranjib-swain/reusable-workflows
ref: a03a3b12b2a46da54adf351efd278765764bda86
```

There are no caller-selectable implementation repository/SHA inputs. Both script files were verified remotely at the pinned SHA and matched local contents.

The fixed helper pin removes the additional caller-controlled helper selection interface. It does not eliminate the need to review the helper code and protect the caller workflow pin. Temporary storage is filesystem separation, not a security sandbox.

### Upstream adoption/maintenance requirement

The draft currently depends on a commit in the contributor fork. Before upstream adoption, maintainers must select an upstream-accessible reviewed helper commit and update all three pins. Do not assume fork ownership/availability is appropriate for an upstream production dependency.

Future helper changes require a script commit followed by a workflow pin update. Referenced commits must remain fetchable. Squash/rebase merging can change identities, so verify availability and update the pin when necessary.

## Validation evidence

### Local checks

- 36 regression tests passed on the refactored implementation.
- AST comparison: original validation, packaging, and publishing bodies match the extracted bodies.
- Workflow YAML parsed, all shell steps passed `bash -n`, and whitespace checks passed.
- No actionlint result is claimed.
- No CI test runner was added to PR #1; local coverage and the disposable test harness are distinct from automated PR enforcement.

Reproduce:

```sh
python3 -B -m unittest discover -s tests -p 'test_dependabot_rebuild_dist.py'
```

### Genuine live Dependabot scenarios

All runs are in `v-chiranjib-swain/setup-node-test`. Red runs for intentional rejection scenarios are expected; diagnostics and remote state were checked, not just run conclusions.

Tests 1-14 used the earlier inline implementation. Test 15 exercised the final pinned extracted implementation. Earlier runs establish behavioral evidence, not that every negative scenario was rerun after extraction.

| # | Scenario | Verified result | Run |
|---|---|---|---|
| 1 | Normal rebuild/push | Generated commit published; normal PR checks retriggered | [40507673367](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40507673367) |
| 2 | Explicit Dependabot recreation | New dependency head rebuilt and published | [40510148536](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40510148536) |
| 3 | Concurrent update, cancellation enabled | Outdated build cancelled | [40845088104](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40845088104) |
| 4 | Stale head, cancellation disabled | Build completed; publisher rejected changed PR | [40852621311](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40852621311) |
| 5 | No-op output | Success without generated commit | [40858788795](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40858788795) |
| 6 | PR closed during build | Publication rejected; no generated push | [40883060000](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40883060000) |
| 7 | Missing PAT | Explicit missing-token error; no publication | [40885834089](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40885834089) |
| 8 | Read-only PAT | Push denied HTTP 403; remote unchanged | [41024857662](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41024857662) |
| 9 | Symlink output | Build-side packaging rejected symlink; upload/publish skipped | [41025624632](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41025624632) |
| 10 | Controlled install failure | Exit 17; later build/package/publish skipped | [41028518765](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41028518765) |
| 11 | Controlled build failure | Install passed; exit 18; package/publish skipped | [41028858796](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41028858796) |
| 12 | Missing output directory | Build succeeded; packaging rejected absent output | [41213256200](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41213256200) |
| 13 | Add/modify/delete and unrelated exclusion | Only generated changes pushed; restored write PAT confirmed | [41213655035](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41213655035) |
| 14 | Root defaults | Default runtime, install/build commands and root output worked | [41215913093](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41215913093) |
| 15 | Final extracted-helper cross-repository execution | Helper checkout/isolation, validation, build, packaging and publishing all passed | [42012262857](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42012262857) |

For test 15, [Dependabot PR 36](https://github.com/v-chiranjib-swain/setup-node-test/pull/36) received generated commit `2f070e44026b70e7676823fda87a7f39759dd7d6`. Its exact diff added `dist/added.txt`, modified `dist/dependency.json`, and deleted `dist/obsolete.txt` within the fixture. The build deliberately changed tracked `unrelated.txt` outside output; its remote content stayed original.

Follow-up generated-head CI passed: [42012345739](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42012345739), [42012336819](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42012336819). Rebuild correctly skipped on the PAT update: [42012346902](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42012346902).

### GitHub-runner artifact regression groups

These executed the original pinned inline publisher in an isolated harness with mocked PR API responses, dummy credentials, and temporary local Git remotes. They are not genuine live publication tests. The final extracted implementation separately passed the corresponding local regressions.

| Group | Verified rejection | Run |
|---|---|---|
| Malformed/CRC | Non-ZIP artifact and corrupted ZIP payload | [41213868974](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41213868974) |
| Unsafe entries | Ten traversal/path/type cases, including symlinks/FIFOs | [41214237061](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41214237061) |
| Conflicts | Duplicate file, file as parent, shared file/directory path | [41214483340](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41214483340) |
| Limits | Actual sparse file of 128 MiB + 1 byte; mocked metadata of 20,001 entries and 512 MiB + 1 byte | [41214820207](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41214820207) |
| Encryption/compression | Mocked encryption flag and LZMA metadata | [41215063036](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41215063036) |
| NUL filename | Mocked NUL-truncated ZIP filename metadata | [41215281027](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41215281027) |

Limit tests do not establish network-transfer limits or full-size extraction behavior. Metadata-based encryption/NUL tests do not use actual crafted encrypted/NUL ZIP payloads.

### Real GitHub push-race harness

Successful run: [42025504718](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42025504718).

The harness executed the final publisher at `3860e6b`, reused its regression scaffolding, and injected mutations immediately after the final mocked API check and before the push. Pushes and remote ref verification used real GitHub transport.

| Case | Result |
|---|---|
| Reset scratch branch to built commit's ancestor | Stale publisher rejected; ancestor preserved |
| Advance scratch branch to a newer commit | Stale publisher rejected; newer commit preserved |
| Second publisher pushes before first | Second publisher succeeded; first rejected without retry; winner preserved |

These are controlled interleavings, not stress tests or an atomic PR-eligibility guarantee. PR API responses were mocked and scratch refs were not genuine Dependabot PR branches. The harness used an approved temporary `GITHUB_TOKEN` with Contents write in the disposable repository rather than the production PAT.

All three scratch branches were verified deleted after the run. The production PR branch was untouched. The harness was installed through setup PRs [38](https://github.com/v-chiranjib-swain/setup-node-test/pull/38) and [39](https://github.com/v-chiranjib-swain/setup-node-test/pull/39) in the disposable repository.

Initial harness run [42025255519](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42025255519) stopped before any scratch mutations because a Dependabot secret was unavailable to `workflow_dispatch`. This was a harness authorization issue, corrected before the successful run; not a publisher defect.

## Outstanding test: automatic Dependabot rebase

- [PR 36](https://github.com/v-chiranjib-swain/setup-node-test/pull/36) has the successful extracted-helper generated commit.
- Setup [PR 37](https://github.com/v-chiranjib-swain/setup-node-test/pull/37) enabled `rebase-strategy: auto` and advanced main to `d5abcaf2de695d25f142ab6b36086ffad4874a14`.
- No `@dependabot rebase` or `recreate` command was sent for this test.
- Latest check at report preparation: PR head remained `2f070e44026b70e7676823fda87a7f39759dd7d6`; automatic refresh has not been observed.
- Status is pending, not a verified pass or failure. There is no completed rebase run URL.
- Pass requires an automatic refresh incorporating the advanced base, followed by successful rebuild/publication and correct output/CI.

Explicit recreation in test 2 is not evidence of automatic rebasing.

## Review risks and boundaries

1. **Upstream helper source:** fork-specific pin must be resolved before upstream production adoption.
2. **Test automation:** PR #1 includes tests but adds no CI workflow that automatically runs them. Agree on repository test integration.
3. **Trust:** fixed helper SHA is immutable selection, not proof of safe code; review helpers and caller pins.
4. **Runner isolation:** caller build code can modify its own runner's accessible files, including its packaging helper. Publishing independently fetches trusted helpers and validates the artifact on a fresh runner.
5. **Races:** final PR eligibility check and Git ref update are not atomic. Closure/eligibility changes after the final API check and SHA changes away then back cannot be excluded by these checks.
6. **Resources:** archive limits are enforced after artifact download and include declared ZIP metadata. Network download is not bounded by these script checks.
7. **Permissions/policies:** publishing may fail under repository token restrictions, rulesets, branch protection, or API/infrastructure failures; no automatic retry is attempted.
8. **Coverage:** finite tests demonstrate tested behavior, not universal production readiness; automatic rebase remains outstanding.

## Suggested review order

1. Review the workflow's job boundaries, fixed helper source/pin, and PAT mapping.
2. Review validation eligibility/path checks.
3. Review publisher archive validation, staging scope, credentials, and push guard.
4. Inspect final live helper run and GitHub race run first.
5. Run the local regression suite and decide CI integration.
6. Resolve upstream helper pin and automatic-rebase evidence before treating the draft as ready.
