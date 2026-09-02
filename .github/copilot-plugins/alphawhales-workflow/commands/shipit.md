---
description: Close the current task, reconcile docs and memory, validate, clean, commit, push, and open or update a PR
argument-hint: Optional additional scope, intent, or issue reference
---

# Ship AlphaWhales Work

Close the current AlphaWhales task end-to-end and leave it ready for review.

Additional scope or intent from the user:

$ARGUMENTS

Follow `CONTRIBUTING.md`, `docs/ENGINEERING_WORKFLOW.md`, `CLAUDE.md`, and the
applicable repository instructions. Treat the current conversation, plan, todo
state, git diff, and untracked files as evidence of the task's intended scope.

Execute the workflow rather than merely describing it.

## 1. Establish scope and safety

- Inspect the branch, status, staged and unstaged diffs, untracked files,
  recent commits, and any current pull request.
- Identify the cohesive task being closed and distinguish its files from
  unrelated or pre-existing user work.
- Never discard, overwrite, stash, reset, or stage unrelated changes. If
  intended and unrelated edits are inseparable, stop before committing and
  explain the blocker.
- Scan the proposed commit for credentials, private data, generated caches,
  database artifacts, browser traces, logs, screenshots, virtual
  environments, and other ignored or prohibited content.

## 2. Finish the implementation

- Resolve incomplete task todos, placeholders, debug code, temporary
  instrumentation, and directly related defects.
- Review the complete change set for correctness, security, error handling,
  type and data-contract safety, backward compatibility, and repository
  conventions.
- Prefer surgical fixes. Do not introduce speculative infrastructure or
  unrelated refactors.

## 3. Reconcile documentation and memory

- Update only documentation affected by the change, including README,
  CHANGELOG, architecture, methodology, operations, contribution guidance,
  API contracts, or inline help when applicable.
- Remove task notes or planning documents only when they are clearly obsolete,
  superseded, and safe to delete. Preserve durable history and methodology
  evidence.
- Review available repository and user memories. Store only genuinely new,
  durable, non-sensitive facts that will help future work; upvote verified
  existing memories instead of duplicating them; downvote outdated memories.
  Never store secrets, personal data, transient task details, or facts already
  inferable from the code.

## 4. Validate in layers

- Run the smallest focused tests that cover the changed behavior first.
- Run the complete offline merge gate from
  `docs/ENGINEERING_WORKFLOW.md`: Python compile smoke checks, the application
  import smoke check, the JavaScript syntax check, and the full pytest suite.
- Do not substitute live SEC or market-data refreshes for tests.
- For frontend changes, run the application on port 8010 and verify desktop
  plus 390px mobile behavior, console errors, page-level horizontal overflow,
  static-asset cache busting, and cache-warm behavior using the configured
  browser tools.
- Fix failures caused by this task. Report unrelated baseline failures
  precisely and do not conceal them.

## 5. Prepare reviewable git history

- If currently on `main`, create a focused branch using `feat/`, `fix/`,
  `docs/`, `refactor/`, `test/`, `ci/`, or `chore/` naming.
- Stage explicit task files only. Re-check the staged diff and generated-file
  boundaries before committing.
- Create one or more logical Conventional Commits. Include the required
  Copilot co-author trailer.
- Never amend, force-push, rewrite history, or bypass hooks unless the user
  explicitly requested it.

## 6. Push and create the review boundary

- Push the branch to `origin` with upstream tracking.
- Create a pull request against `main`, or update the existing pull request for
  the branch.
- Use a concise Conventional Commit-style title and a body covering summary,
  rationale, material changes, validation, risks or caveats, and documentation
  impact.
- Do not merge automatically. CI and review remain the merge gate.

## 7. Leave the workspace clean

- Remove only task-owned temporary files and generated artifacts after
  inspecting their exact paths.
- Confirm the branch is pushed and git status is clean for the task. Never
  delete broad directories or clean unrelated user files to manufacture a
  clean status.
- Finish with the branch, commit or commits, pull-request link, validation
  result, and any explicit blocker. Do not offer optional follow-up work.
