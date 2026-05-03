# Branch Protection Checklist (`main`)

Use this checklist to configure robust pull request safety for `main`.

## 1) Protect the branch

- [ ] Settings -> Branches -> Add branch protection rule
- [ ] Branch name pattern: `main`
- [ ] Require a pull request before merging
- [ ] Require approvals: at least `1`
- [ ] Dismiss stale pull request approvals when new commits are pushed
- [ ] Require conversation resolution before merging

## 2) Require CI checks

- [ ] Require status checks to pass before merging
- [ ] Require branches to be up to date before merging
- [ ] Select required checks (exact names from Actions):
- [ ] `Tests (3.12)`
- [ ] `Tests (3.13)`
- [ ] `Ruff`

Optional, if enabled:

- [ ] `codecov/project`
- [ ] `codecov/patch`

## 3) Tighten merge safety

- [ ] Do not allow bypassing the above settings
- [ ] Restrict who can push to matching branches (maintainers only)
- [ ] Allow force pushes: disabled
- [ ] Allow deletions: disabled

## 4) Dependency update hardening

- [ ] Require all above checks for Renovate/Dependabot PRs as well
- [ ] Enable auto-merge only for update classes with stable tests (for example patch-level updates)
- [ ] Keep major dependency updates manual (review required)
