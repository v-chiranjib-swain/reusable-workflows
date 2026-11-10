# Dependabot dist rebuild: review guide

**PR:** [v-chiranjib-swain/reusable-workflows#1](https://github.com/v-chiranjib-swain/reusable-workflows/pull/1)

**Reviewed version:** `3860e6b77ef2688557c82c3e96dda8ad8a6c23e9`

**Updated:** 2026-11-10

## 1. Purpose and changes

The opt-in workflow rebuilds generated output for same-repository Dependabot PRs and commits only the configured output directory. Existing workflows, including read-only `check-dist.yml`, are unchanged.

| File | Responsibility |
|---|---|
| [dependabot-rebuild-dist.yml](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/.github/workflows/dependabot-rebuild-dist.yml) | Runs validation, build, and publishing as separate jobs |
| [validate.py](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/scripts/validate.py) | Checks PR eligibility, current head, branch, and output path |
| [publish.py](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/scripts/publish.py) | Packages output with `--package`; validates and publishes it without arguments |
| [test_dependabot_rebuild_dist.py](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/tests/test_dependabot_rebuild_dist.py) | 36 dependency-free regression tests |
| [README.md](https://github.com/v-chiranjib-swain/reusable-workflows/blob/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9/README.md) | Caller configuration and maintenance instructions |

Extraction reduced the YAML from 537 to 167 lines. The validation, packaging, and publishing bodies were compared structurally and retain the original logic.

## 2. How it works

1. **Validate:** require an open, same-repository PR authored and triggered by Dependabot; confirm the event matches the current PR head.
2. **Build:** check out that exact SHA, install dependencies, rebuild, and upload the generated directory as a ZIP. No publishing PAT is provided.
3. **Publish:** on a fresh runner, validate the artifact and PR head, stage only the configured directory, and push a generated commit. No install or build commands run here.

### Main safeguards

- Helpers are checked out from a fixed repository and full SHA, independently in each job. Callers cannot override their source.
- Helpers are moved outside the caller checkout; Python runs with `-I`. This is not a sandbox for build commands; the publishing job has its own fresh helper checkout.
- Artifact checks reject unsafe paths, symlinks, unsupported types, conflicts, duplicates, and corrupted ZIPs. Limits: 128 MiB compressed, 512 MiB declared decompressed size, 20,000 entries.
- Only generated files are staged, including additions and deletions. Unchanged output creates no commit.
- The publisher rechecks eligibility and guards the advertised remote SHA before a normal push. It never force-pushes or retries stale output.
- The generated message is `build(deps): rebuild generated dist [dependabot skip]`. The marker permits Dependabot to replace the extra commit; the caller's actor condition prevents rebuild loops.

### Caller requirements

Use a reviewed workflow SHA and explicitly map a Dependabot secret containing a PAT with Contents read/write access. Keep the token out of validation and build jobs. Handle `opened`, `reopened`, and `synchronize` events with per-PR concurrency.

Defaults: Node `24.x`, `npm ci --ignore-scripts`, `npm run build`, and output directory `dist`. Commands must be trusted caller configuration.

### Helper pinning

- [Commit A: a03a3b1](https://github.com/v-chiranjib-swain/reusable-workflows/commit/a03a3b12b2a46da54adf351efd278765764bda86): scripts only.
- [Commit B: 3860e6b](https://github.com/v-chiranjib-swain/reusable-workflows/commit/3860e6b77ef2688557c82c3e96dda8ad8a6c23e9): workflow references Commit A; tests and documentation updated.

All three helper checkouts use `v-chiranjib-swain/reusable-workflows` at Commit A. Future helper changes require a new script commit and a workflow pin update.

## 3. Real-action test: setup-go

These genuine Dependabot PRs target `test/dependabot-rebuild` in the setup-go fork. Results were checked on 2026-11-10; all five PRs were open.

| PR | Update | Rebuild result | Independent check-dist |
|---|---|---|---|
| [v-chiranjib-swain/setup-go#2](https://github.com/v-chiranjib-swain/setup-go/pull/2) | `@vercel/ncc` | All three jobs passed; generated commit pushed. [Run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256913559) | [Failed before](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256912421), [passed after](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42258435510) |
| [v-chiranjib-swain/setup-go#3](https://github.com/v-chiranjib-swain/setup-go/pull/3) | `eslint` | All passed; output unchanged, no commit. [Run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256931395) | [Passed](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256930652) |
| [v-chiranjib-swain/setup-go#4](https://github.com/v-chiranjib-swain/setup-go/pull/4) | `semver` and types | All passed; generated commit pushed. [Run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256958292) | [Passed after rebuild](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42258470958) |
| [v-chiranjib-swain/setup-go#5](https://github.com/v-chiranjib-swain/setup-go/pull/5) | `typescript` | Installation failed; publication skipped. [Run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256963412) | [Failed](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42256962081) |
| [v-chiranjib-swain/setup-go#6](https://github.com/v-chiranjib-swain/setup-go/pull/6) | `@jest/globals` | All passed; output unchanged, no commit. [Run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42300692499) | [Passed](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42300692209) |

- PR 2's generated commit changed only `dist/setup/index.js` and `dist/cache-save/index.js`.
- PAT updates skipped another rebuild on [PR 2](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42258435985) and [PR 4](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42258472201).
- PR 5's TypeScript 7.0.2 conflicts with `@typescript-eslint/eslint-plugin@8.62.0`, which requires TypeScript `>=4.8.4 <6.1.0`. This is a dependency compatibility failure, not a publishing failure.
- Separate npm audit checks failed on PR 2. Successful rebuilding does not mean all PR checks are green.

### Rebase compatibility: passed

After advancing the test base, the repository owner requested `@dependabot rebase` on [v-chiranjib-swain/setup-go#2](https://github.com/v-chiranjib-swain/setup-go/pull/2). Verified on 2026-11-10:

| Check | Evidence |
|---|---|
| Refreshed dependency commit includes the advanced base | New dependency commit `a26598f`; merge base is `7d7a3004ed08af53bbce562af5f72692ed49dc60` |
| Validation, build, and publication succeed again | [Rebuild run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42751376726) |
| Fresh generated commit is created | `309ab876b26176eeecfd91c9569f4c04476d97b7`, following the refreshed dependency commit |
| Independent check-dist passes on the new head | [Check-dist run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42751640806) |
| PAT-generated update does not start another rebuild | [Skipped follow-up run](https://github.com/v-chiranjib-swain/setup-go/actions/runs/42751640980) |

This verifies command-triggered rebase compatibility with the generated commit, not automatic rebase scheduling.

## 4. Additional test evidence

All runs below are in the disposable `v-chiranjib-swain/setup-node-test` repository.

### End-to-end tests

| Test | Result | Run |
|---|---|---|
| Extracted helpers across repositories | All jobs passed; generated add/modify/delete published; unrelated tracked-file change excluded; follow-up CI passed | [42012262857](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42012262857) |
| Default root inputs | Default runtime, commands, and output path worked | [41215913093](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41215913093) |
| Normal publication | Generated commit pushed | [40507673367](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40507673367) |
| Explicit Dependabot recreation | Fresh head rebuilt | [40510148536](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40510148536) |
| Concurrent update, cancellation enabled | Outdated build cancelled | [40845088104](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40845088104) |
| Stale head, cancellation disabled | Publisher rejected changed head | [40852621311](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40852621311) |
| No-op output | No generated commit | [40858788795](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40858788795) |
| PR closed during build | Publication rejected | [40883060000](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40883060000) |
| Missing PAT | Explicit error; no push | [40885834089](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/40885834089) |
| Read-only PAT | HTTP 403; remote unchanged | [41024857662](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41024857662) |
| Symlink output | Packaging rejected | [41025624632](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41025624632) |
| Controlled install failure | Exit 17; later phases skipped | [41028518765](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41028518765) |
| Controlled build failure | Exit 18; publication skipped | [41028858796](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41028858796) |
| Missing output directory | Packaging rejected | [41213256200](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41213256200) |
| Generated changes and unrelated exclusion | Only output-directory changes pushed | [41213655035](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41213655035) |

Intentional rejection runs are expected to be red. Except for the extracted-helper test, these disposable end-to-end runs used the earlier inline implementation.

### Publisher harness tests

| Test group | Run |
|---|---|
| Malformed ZIP and CRC corruption | [41213868974](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41213868974) |
| Unsafe paths and file types | [41214237061](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41214237061) |
| Duplicate entries and file/directory conflicts | [41214483340](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41214483340) |
| Archive resource limits | [41214820207](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41214820207) |
| Encryption and unsupported compression | [41215063036](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41215063036) |
| NUL-truncated filenames | [41215281027](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/41215281027) |
| GitHub push races: ancestor reset, forward update, competing publishers | [42025504718](https://github.com/v-chiranjib-swain/setup-node-test/actions/runs/42025504718) |

Artifact harness runs used the earlier publisher with mocked PR API responses, dummy credentials, and local Git remotes. Entry-count, decompressed-size, encryption, and NUL tests used mocked ZIP metadata; they are not full payload/download tests.

The race harness used the extracted publisher, a temporary write-enabled `GITHUB_TOKEN`, mocked PR eligibility, and real GitHub scratch branches. Controlled mutations occurred after the final API check and before push. All three cases passed without stale retries; scratch branches were deleted.

### Local validation

36 tests passed on the refactored implementation. YAML parsing, shell syntax, and whitespace checks passed.

```sh
python3 -B -m unittest discover -s tests -p 'test_dependabot_rebuild_dist.py'
```

## 5. Remaining observation

**Automatic rebase scheduling remains unverified.** Rebase compatibility passed through the explicit command above. The earlier automatic-rebase observation on [v-chiranjib-swain/setup-node-test#36](https://github.com/v-chiranjib-swain/setup-node-test/pull/36) did not show a refresh before a manual rebase request was posted on 2026-11-10. That request means subsequent activity there cannot establish an unprompted automatic rebase.

## 6. Other review considerations

- **Upstream helper source:** before upstream adoption, use an upstream-accessible reviewed helper commit and verify the pin remains fetchable after squash/rebase.
- **CI integration:** the PR includes regression tests but no automated workflow to run them.
- **Known boundaries:** PR eligibility checks and Git push are not atomic; closure after the final check and SHA changes away then back are not excluded. Archive limits apply after download.

**Review order:** workflow job boundaries and helper pin, validation script, publisher script, setup-go evidence, regression tests, then the remaining considerations above.
