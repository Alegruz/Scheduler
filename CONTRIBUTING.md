# Contributing to Scheduler

Thank you for your interest in contributing to **Scheduler**! This document is the single source of truth for all Git workflow, branching, commit, and pull request conventions used in this repository. Following these guidelines ensures a clean, navigable history and a smooth collaboration experience.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Branching Strategy](#branching-strategy)
3. [Commit Message Convention](#commit-message-convention)
4. [Pull Request Guidelines](#pull-request-guidelines)
5. [Code Review Process](#code-review-process)
6. [Release & Versioning](#release--versioning)
7. [Keeping Your Fork / Branch Up to Date](#keeping-your-fork--branch-up-to-date)
8. [Git Hygiene](#git-hygiene)
9. [Common Workflows](#common-workflows)

---

## Quick Start

```bash
# 1. Fork the repo and clone your fork
git clone https://github.com/<your-username>/Scheduler.git
cd Scheduler

# 2. Add the upstream remote
git remote add upstream https://github.com/Alegruz/Scheduler.git

# 3. Create a branch off develop (see Branching Strategy)
git switch develop
git pull upstream develop
git switch -c feature/your-feature-name

# 4. Make changes, commit often (see Commit Convention)
git add .
git commit -m "feat(ui): add daily schedule view"

# 5. Push and open a PR targeting develop
git push origin feature/your-feature-name
```

---

## Branching Strategy

This project uses a **Scaled Trunk-Based Development** approach with long-lived integration branches. The model is intentionally lightweight for a personal project while remaining compatible with GitHub Actions CI/CD automation.

### Branch Hierarchy

```
main
 └── develop
      ├── feature/<scope>-<short-description>
      ├── fix/<scope>-<short-description>
      ├── refactor/<scope>-<short-description>
      ├── docs/<short-description>
      ├── test/<short-description>
      ├── chore/<short-description>
      └── release/<version>          (merged into main AND develop)
hotfix/<version>-<short-description>  (branches off main, merged into main AND develop)
```

### Branch Descriptions

| Branch | Purpose | Branches Off | Merges Into |
|---|---|---|---|
| `main` | Stable, production-ready code. Only tagged releases land here. | — | — |
| `develop` | Integration branch. All feature work converges here before a release. | `main` | `main` (via `release/*`) |
| `feature/*` | New functionality. | `develop` | `develop` |
| `fix/*` | Non-urgent bug fixes found during development. | `develop` | `develop` |
| `refactor/*` | Code restructuring with no behaviour change. | `develop` | `develop` |
| `docs/*` | Documentation-only changes. | `develop` | `develop` |
| `test/*` | Adding or fixing tests only. | `develop` | `develop` |
| `chore/*` | Dependency updates, CI config, build tooling, etc. | `develop` | `develop` |
| `release/*` | Release preparation (version bump, changelog). | `develop` | `main` & `develop` |
| `hotfix/*` | Critical production fixes that cannot wait for the next release cycle. | `main` | `main` & `develop` |

### Branch Naming Rules

- Use **lowercase** and **hyphens** only — no spaces, underscores, or capital letters.
- Keep names **short and descriptive** (≤ 50 characters total, excluding the prefix).
- Include a **scope** in `feature/*` and `fix/*` branches to indicate the area of change.

```
# ✅ Good
feature/scheduler-recurring-events
fix/auth-token-expiry
hotfix/1.2.1-crash-on-empty-schedule
release/1.3.0
docs/update-api-reference
chore/upgrade-dependencies

# ❌ Bad
feature/new_stuff          # underscores
Fix/Login                  # uppercase, no scope
my-branch                  # no type prefix
feature/fixed-the-thing-that-was-broken-and-also-refactored-some-stuff  # too long
```

### Branch Lifetime

- Short-lived branches (`feature/*`, `fix/*`, etc.) should be **opened, reviewed, and merged within one week** whenever possible.
- Delete the source branch immediately after merging.
- Never commit directly to `main` or `develop` — always go through a pull request.

---

## Commit Message Convention

This project follows the **[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)** specification. Conventional Commits make the history machine-readable, enable automated changelogs, and make `git log` genuinely useful.

### Format

```
<type>(<scope>): <short summary>
<BLANK LINE>
[optional body]
<BLANK LINE>
[optional footer(s)]
```

### Types

| Type | When to Use |
|---|---|
| `feat` | A new feature visible to the end user. |
| `fix` | A bug fix visible to the end user. |
| `docs` | Documentation changes only (README, comments, wiki). |
| `style` | Formatting, white-space, missing semicolons — no logic change. |
| `refactor` | Code change that neither adds a feature nor fixes a bug. |
| `perf` | A change that improves performance. |
| `test` | Adding or correcting tests. |
| `build` | Changes to the build system or external dependencies. |
| `ci` | Changes to CI configuration files and scripts. |
| `chore` | Other changes that don't modify source or test files. |
| `revert` | Reverts a previous commit. |

### Scope (optional but encouraged)

Use the area of the codebase affected, e.g., `ui`, `api`, `db`, `auth`, `scheduler`, `notifications`.

### Short Summary Rules

- **Imperative mood**: "add feature" not "added feature" or "adds feature".
- **Lowercase** first letter.
- **No period** at the end.
- **≤ 72 characters**.

### Body (optional)

Explain the *what* and *why*, not the *how*. Wrap at 72 characters.

### Footer (optional)

- Reference issues: `Closes #42`, `Fixes #17`, `Refs #99`.
- Breaking changes: `BREAKING CHANGE: <description>`.

### Examples

```
# Minimal
feat(scheduler): add weekly recurring event support

# With body and issue reference
fix(notifications): prevent duplicate alerts on app resume

Previously, the notification service was re-registering listeners every
time the app returned to the foreground, causing duplicate alerts.
Register listeners in the lifecycle init only.

Closes #34

# Breaking change
feat(api)!: rename /schedule endpoint to /events

BREAKING CHANGE: The REST endpoint `/schedule` has been renamed to
`/events` for consistency with the domain model. Update all clients.

# Revert
revert: feat(scheduler): add weekly recurring event support

Reverts commit abc1234.
Reason: introduced a regression in daily view rendering.
```

### What NOT to Do

```
# ❌ Vague
git commit -m "fix stuff"
git commit -m "WIP"
git commit -m "asdf"

# ❌ Past tense
git commit -m "fixed the login bug"

# ❌ No type
git commit -m "add recurring events"

# ❌ Mixing unrelated changes in one commit
# (Stage and commit each logical change separately)
```

### Commit Atomicity

Each commit should represent **one logical change**. If you find yourself writing "and" in a commit summary, split it into two commits. Use `git add -p` (interactive patch staging) to split a large diff into focused commits.

---

## Pull Request Guidelines

### Before You Open a PR

- [ ] Branch is up to date with the target branch (`develop` or `main` for hotfixes).
- [ ] All commits follow the [Conventional Commits](#commit-message-convention) format.
- [ ] Code compiles and all existing tests pass locally.
- [ ] New functionality is covered by tests.
- [ ] Documentation is updated if public APIs or behaviour changed.
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`.

### PR Title

Follow the same format as a commit message summary:

```
<type>(<scope>): <short summary>

# Examples
feat(scheduler): add support for recurring weekly events
fix(ui): correct misaligned calendar grid on small screens
docs: add API reference for the notifications module
```

### PR Description

Use the [pull request template](.github/pull_request_template.md) provided in this repository. It prompts you to fill in:

- **What** changed and **why**.
- **How** to test the change manually.
- Related issues.
- Screenshots / recordings for UI changes.
- Checklist of quality gates.

### PR Size

| Size | Diff Lines | Guidance |
|---|---|---|
| XS | < 10 | Ideal. Trivial fixes. |
| S | 10–99 | Good. Easy to review thoroughly. |
| M | 100–499 | Acceptable. May need multiple review sessions. |
| L | 500–999 | Needs justification. Split if possible. |
| XL | 1 000+ | Must be split unless it's a generated file or a single indivisible change. |

Large PRs take longer to review, introduce more risk, and are harder to revert. Keep them small.

### Draft PRs

Open a **Draft PR** early to signal that work is in progress and to get early feedback. Convert to "Ready for Review" only when all checklist items are met.

### Targeting the Right Branch

| PR Type | Target Branch |
|---|---|
| Feature / fix / refactor / docs / test / chore | `develop` |
| Release preparation | `main` |
| Hotfix | `main` |

---

## Code Review Process

### Reviewer Responsibilities

- Review within **48 hours** of being assigned.
- Read the PR description and linked issues before reading the code.
- Check for correctness, clarity, security, performance, and test coverage.
- Leave **actionable, specific, and constructive** comments. Prefer suggestions (`git suggest`) over vague observations.
- Distinguish between blockers (`🔴 blocker:`) and nits (`🟡 nit:`).

### Author Responsibilities

- Respond to all review comments — either fix, explain, or open a follow-up issue.
- Do **not** force-push after requesting review (it invalidates review threads). Use additional commits; squash before merge if needed.
- Do **not** resolve review threads yourself — let the reviewer resolve them.

### Merge Strategy

| Scenario | Strategy |
|---|---|
| Feature / fix PRs into `develop` | **Squash and merge** (keeps `develop` history linear and clean) |
| `release/*` into `main` | **Merge commit** (preserves the release boundary in history) |
| `hotfix/*` into `main` | **Merge commit** |
| `main` back-merge into `develop` | **Merge commit** |

> **Why squash for feature branches?** Individual WIP commits ("fix typo", "try again", "actually fix it") are noise at the repository level. A single squashed commit with the PR's conventional commit title gives a clean, meaningful history on `develop` and `main`.

### Approvals Required

- At least **1 approval** from a code owner before merging.
- All CI checks must pass.
- No unresolved review threads.

---

## Release & Versioning

This project uses **[Semantic Versioning (SemVer)](https://semver.org/)**: `MAJOR.MINOR.PATCH`.

| Version Part | Bump When |
|---|---|
| `MAJOR` | Incompatible API / data-model change (`BREAKING CHANGE` in commits). |
| `MINOR` | New backwards-compatible feature (`feat` commits). |
| `PATCH` | Backwards-compatible bug fix (`fix` commits). |

### Release Workflow

```bash
# 1. Create a release branch from develop
git switch develop && git pull upstream develop
git switch -c release/1.3.0

# 2. Bump version numbers in relevant files
# 3. Update CHANGELOG.md: replace [Unreleased] with [1.3.0] - YYYY-MM-DD
git commit -m "chore(release): bump version to 1.3.0"

# 4. Open a PR: release/1.3.0 → main
# 5. After merge into main, tag the merge commit
git switch main && git pull upstream main
git tag -a v1.3.0 -m "Release v1.3.0"
git push upstream v1.3.0

# 6. Open a PR: main → develop (back-merge)
# 7. Delete the release branch
```

### Tags

- Always annotated: `git tag -a v<version> -m "Release v<version>"`.
- Format: `v<MAJOR>.<MINOR>.<PATCH>` (e.g., `v1.3.0`).
- Never move or delete a published tag.

---

## Keeping Your Fork / Branch Up to Date

### Syncing a Feature Branch with develop

```bash
git switch develop
git pull upstream develop
git switch feature/your-feature-name
git rebase develop   # prefer rebase over merge to keep history linear
```

> Use `git rebase` for personal branches that have **not yet been shared** in a PR. Once a PR is open and reviewers have commented, do **not** force-push — add a merge commit from `develop` instead.

```bash
# After a PR is open: use merge (not rebase) to avoid rewriting shared history
git switch feature/your-feature-name
git merge develop
```

---

## Git Hygiene

### What to Commit

- Source code and tests.
- Configuration files needed to build or run the project.
- `CHANGELOG.md`, `README.md`, and other documentation.

### What NOT to Commit

- Build artefacts (`dist/`, `build/`, `*.class`, `*.o`).
- Dependencies (`node_modules/`, `vendor/`, `.venv/`).
- IDE / editor metadata (`.idea/`, `.vscode/`, `*.swp`).
- OS junk (`.DS_Store`, `Thumbs.db`).
- Secrets, credentials, API keys, `.env` files with real values.
- Large binary files (use Git LFS if unavoidable).

Always check `.gitignore` before committing and **never** use `git add .` blindly.

### Secrets

**Never commit secrets.** If you accidentally do:

1. Immediately rotate the credential.
2. Remove it from history with `git filter-repo` (not `git filter-branch`).
3. Force-push and notify all collaborators to re-clone.

### Handling Merge Conflicts

1. Understand both sides of the conflict before resolving.
2. Do not simply keep your side — understand the intent of the incoming change.
3. After resolving, run tests before committing the resolution.
4. Write a meaningful merge commit message.

---

## Common Workflows

### Fix a bug on develop

```bash
git switch develop && git pull upstream develop
git switch -c fix/scheduler-crash-on-null-event
# ... make changes ...
git add -p   # stage changes interactively
git commit -m "fix(scheduler): prevent crash when event title is null"
git push origin fix/scheduler-crash-on-null-event
# Open PR targeting develop
```

### Apply a hotfix to production

```bash
git switch main && git pull upstream main
git switch -c hotfix/1.2.1-notification-loop
# ... make changes ...
git commit -m "fix(notifications): break infinite loop on reminder reschedule"
git push origin hotfix/1.2.1-notification-loop
# Open PR targeting main
# After merge, tag v1.2.1, then back-merge main into develop
```

### Undo the last unpublished commit (keep changes)

```bash
git reset --soft HEAD~1
```

### Undo a published commit (safe, additive)

```bash
git revert <commit-sha>
git push origin <branch>
```

---

*Last updated: 2026-03-06. Maintained by [@Alegruz](https://github.com/Alegruz).*
