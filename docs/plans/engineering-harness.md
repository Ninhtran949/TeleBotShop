# Engineering harness setup

Historical setup evidence. Current project design and routing are in `docs/PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/CONVENTIONS.md`, and `docs/plans/shop/README.md`. File counts, instruction sizes, and state below describe the initial harness setup, not later project-design work.

## Scope and acceptance

The user's harness request is the specification. Preserve `task.md`; install exactly the six requested upstream skills without edits, retain shared references and license, pin the source, add concise repository policy and factual documentation, and verify layout, discovery, integrity, and proportional workflows. Application implementation, Git initialization, commits, pushes, and deployments are outside this task.

## Plan

1. Inspect the workspace, Git state, and starter ZIP availability; establish the original file hash.
2. Inspect and pin the upstream archive. Copy only the approved skill subset, shared references, and license, preserving relative paths and exact bytes.
3. Add repository rules and project/architecture documentation. Keep proposed stack and unimplemented behavior clearly labeled; preserve upstream workflows and constrain their conflicting defaults in repository policy.
4. Add an offline verifier and focused failure tests. Compare against the pinned archive, check native skill discovery, review task routing and instruction conflicts, then report actual results and limitations.

## Initial observations

- Only `task.md` existed; no starter ZIP was available in the workspace.
- Git status failed with `fatal: not a git repository (or any of the parent directories): .git`.
- Original `task.md` SHA-256: `ea9c82b3b400b270fdbae0cd6785db854e2f5f2aeecd2dd99c4ce9b79b669568`.
- Upstream archive pinned to `aeac41d4d005999432bb7312f7ff06383a91d59d`.

## Instruction compatibility and routing review

Repository rules resolve upstream defaults without changing original skills:

| Upstream default or example | Repository treatment |
| --- | --- |
| Automatic commits after slices | Commit only on explicit user request. |
| Root specs and `tasks/plan.md` / `tasks/todo.md` | Keep needed artifacts together in `docs/plans/`; preserve incomplete plans. |
| Further skill recommendations, including `context-engineering`, `git-workflow-and-versioning`, and `performance-optimization` | Remain optional guidance; only the approved six skills are installed. |
| Spec approval and generic ask-first checklists | Reuse clear user requirements and existing authorization; seek review for genuinely new specifications or unresolved scope, not already approved work. |
| JavaScript/browser examples and broad release checklists | Apply only to relevant technologies, task risks, and real project commands. Do not introduce a browser frontend or unrelated tooling. |

Routing acceptance cases (instruction review, not an automated model-behavior evaluation):

| Example | Expected scope |
| --- | --- |
| Fix a typo in documentation | Direct edit and focused file checks; no specification or TDD workflow. |
| Implement a clearly specified catalog feature | Reuse its requirements; plan only if useful, implement in slices, test changed behavior, review substantive code. |
| A vague request for a new capability | Clarify/specify with `spec-driven-development` before dependent implementation. |
| Change payment callback handling | Threat considerations, focused idempotency/ownership/concurrency tests, and security review in addition to implementation checks. |
| Review a diff | `code-review-and-quality`; report findings without editing unless asked. |

## Verification evidence

| Check | Observed result |
| --- | --- |
| Original content | `task.md` still matches its original SHA-256; no existing file was modified. |
| Layout | 25 files added; exactly one root `AGENTS.md` (4,733 bytes), six installed skills, seven shared references, one skill-local reference, original MIT license, provenance, requested docs, and harness checks. No router, plugin wrappers, or `.codex` configuration added. |
| Upstream integrity | All 15 vendored files match both the manifest hashes and the complete selected subset in the checksum-verified, commit-pinned archive. No upstream script, hook, or installer was executed. |
| References | Required shared and skill-local reference file paths resolve without rewriting upstream Markdown. |
| Native discovery | Installed `codex-cli 0.163.0`, JSON-RPC `skills/list` with `forceReload: true`: exactly six workspace skills, each `scope: repo` and `enabled: true`; zero discovery errors. Fourteen other pre-existing skills were visible outside the workspace subset; no global skill/configuration was changed. |
| Harness tests | Six tests pass on Python 3.11.9. They cover a valid installation, tampered skill, missing shared reference, extra skill, broken reference with an otherwise matching hash, and rejection of a bad archive checksum before parsing. The first five tests were exercised against a no-op verifier first: four expected failures, then five passes after implementation; the added archive failure test also passes. |
| Documentation links | All 10 local Markdown links in authored guidance/docs resolve. |
| Instruction review | The five routing cases above and conflicting upstream defaults were reviewed against root policy. This is an instruction review, not a model-behavior evaluation. |
| Focused secret scan | No matches for private-key headers or GitHub, AWS, Telegram-bot, and OpenAI token patterns in delivered files. This is a limited pattern check, not proof that arbitrary secrets cannot exist. |
| Git state | Initial and final `git status --short --branch` both report no Git repository. No Git metadata was created; no commit, push, or deployment occurred. |

## Files added

| Paths | Purpose |
| --- | --- |
| `AGENTS.md` | Concise repository rules, proportional skill activation, boundaries, checks, and authorization. |
| `.gitignore`, `.gitattributes` | Ignore environment files and Python caches; preserve vendored bytes across Git newline settings. |
| `.agents/skills/{spec-driven-development,planning-and-task-breakdown,incremental-implementation,test-driven-development,code-review-and-quality,security-and-hardening}/SKILL.md` | The six original skills. |
| `.agents/skills/security-and-hardening/references/hardening-patterns.md` | Original skill-local reference. |
| `.agents/references/*.md` | All seven original shared reference files; exact names and hashes are in `.agents/upstream.json`. |
| `.agents/LICENSE`, `.agents/upstream.json` | Original license and reproducible source/file provenance. |
| `docs/PROJECT.md`, `docs/ARCHITECTURE.md` | Verified current state, requested direction, actual check commands, and reproduction instructions. |
| `docs/adr/README.md`, `docs/plans/engineering-harness.md` | Reserved ADR location and this setup plan/evidence record. |
| `scripts/verify_harness.py`, `tests/test_harness.py` | Standard-library-only offline checks and failure tests. |

## Commands and actions actually executed

Run from `C:\Users\Admin\workspace\TeleBotShop` unless a temporary output path is specified:

| Command or action | Result |
| --- | --- |
| `Get-Location`, `Get-ChildItem -Force`, `rg --files --hidden` with instruction/ZIP filters; `Get-Content` on relevant files | Inspected the workspace, original brief, applicable guidance, upstream manifests, and final authored files. No starter ZIP found. |
| `git status --short --branch` | Failed before and after setup because there is no Git repository. |
| `Get-FileHash -LiteralPath '.\task.md' -Algorithm SHA256` | Established original file hash; final standard-library hash comparison matched. |
| `Get-Command codex,python,py,git,curl.exe,tar -ErrorAction SilentlyContinue` | Inspected available tooling; no installation performed. |
| `git ls-remote https://github.com/addyosmani/agent-skills.git HEAD` | Failed to connect inside the network-restricted sandbox. |
| Inline Python `urllib.request` download | Retrieved `https://api.github.com/repos/addyosmani/agent-skills/commits/HEAD`, then the commit-pinned archive URL in `.agents/upstream.json`, outside the network restriction. Archive remained in the OS temporary directory. |
| Inline Python `tarfile` inspection and byte copying | Inspected selected skill instructions, license, references, and conflicts; copied only the selected regular files using exclusive creation and wrote provenance. No archive content was executed. |
| `codex --version`, `codex app-server --help` | PowerShell `.ps1` wrappers were blocked by execution policy; `.cmd` wrappers below worked without changing that policy. |
| `codex.cmd --version`, `codex.cmd app-server --help`, `codex.cmd app-server generate-json-schema --help` | Confirmed CLI version and local app-server tooling. |
| `codex.cmd app-server generate-json-schema --out $schemaDirectory` | Generated protocol schemas under the OS temp directory to inspect `initialize` and `skills/list`. |
| `codex.cmd app-server --listen stdio://`, driven by an inline Python JSON-RPC client | Sandbox startup could not resolve the home directory. The read-only check outside that restriction succeeded: `initialize`, `initialized`, then `skills/list` with `cwds` containing the workspace and `forceReload: true`. No model turn was started. |
| `python --version` | Python 3.11.9. |
| `python scripts/verify_harness.py` | Passed offline layout, integrity, metadata, and reference checks. |
| `python scripts/verify_harness.py --archive "$env:TEMP\addyosmani-agent-skills-aeac41d4d005999432bb7312f7ff06383a91d59d.tar.gz"` | Passed offline checks plus archive checksum and full selected-subset comparison. |
| `python -m unittest discover -s tests -p test_harness.py -v` | Initial expected red run: four failures out of five against the no-op verifier; subsequent run: five passed. |
| `python -B -m unittest discover -s tests -p test_harness.py -v` | Final six tests passed after adding archive rejection coverage. |
| Inline Python final audit | Confirmed original hash, no Git initialization or `.codex` additions, a single root instruction file, 10 local links, 25 added delivery files, and zero targeted secret-pattern matches. |
| `Remove-Item -LiteralPath $resolvedCache -Recurse -Force` for generated Python cache directories | Removed only `scripts/__pycache__` and `tests/__pycache__` after verifying their resolved workspace paths, expected names, and absence of reparse points; the offline verifier and original file hash check then passed again. |

## Remaining limits

The harness is installed and verified; there are no remaining setup blockers. Git diff/history verification is unavailable until this folder becomes a Git repository. No application, PostgreSQL, Telegram, live-payment, deployment, build, lint, or type-check validation was possible because application tooling does not exist. Actual model skill-selection behavior was not evaluated; discovery and the written routing policy were checked separately. Repository instructions guide behavior but are not an access-control mechanism or an enforced CI gate.
