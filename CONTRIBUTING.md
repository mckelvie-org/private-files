# Contributing

This project uses [PDM](https://pdm-project.org/) for dependency management, linting, type checking, and testing.

### 1. GitHub environments

Run script `bin/install-github-invironments` to set up GitHub for building and publishing.
This will create two GitHub environments:

- **`testpypi`** — used by `publish-test.yml`
- **`pypi`** — used by `publish.yml`

No secrets or protection rules are required; the environment names are what
the workflows reference.

### 2. TestPyPI trusted publisher

On [test.pypi.org](https://test.pypi.org), go to **Account → Publishing → Add a
new pending publisher** (use "pending" because the project won't exist there yet):

| Field | Value |
|---|---|
| PyPI project name | `private-files` *(or your fork's package name)* |
| Owner | `mckelvie-org` *(or your fork's GitHub owner) |
| Repository | `private-files` |
| Workflow | `publish-test.yml` |
| Environment | `testpypi` |

### 3. PyPI trusted publisher

On [pypi.org](https://pypi.org), same flow — **Account → Publishing → Add a new
pending publisher**:

| Field | Value |
|---|---|
| PyPI project name | `private-files` *(or your fork's package name)* |
| Owner | `mckelvie-org` *(or your fork's GitHub owner) |
| Repository | `private-files` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

### Trigger sequence

Once the above is in place:

1. `bin/bump-dev` — iterate the dev version on `main`
2. `bin/cut-rc` — pushes a `v*-rc.*` tag → triggers `publish-test.yml` → TestPyPI
3. `bin/cut-prod` — pushes a `v*.*.*` tag → triggers `publish.yml` → PyPI + auto-bumps `main`

---

## Development setup

```bash
bin/install        # first-time setup (installs PDM and dependencies)
pdm install -G dev # subsequent dependency updates
pdm run lint       # ruff check
pdm run typecheck  # mypy
pdm run test       # pytest
pdm build
```

## Release workflow

Releases follow a three-channel model:

| Channel | Tag format             | Moving tag   | Index    |
|---------|------------------------|--------------|----------|
| dev     | —                      | —            | —        |
| rc      | `v<x.y.z>-rc.<n>`     | `rc-latest`  | TestPyPI |
| prod    | `v<x.y.z>`             | `prod-latest`| PyPI     |

The `main` GitHub branch is the only relevant branch, and always carries `X.Y.Z-dev.N` as the package version.
It is never necessary to check out any other branch. Releases are driven entirely by tags on branchless commits —
no `rc` or `prod` branches are created. When a release is cut, a custom tagged commit with only the modified version
information is created--that commit is not on any branch, and the only thing keeping the commit alive is
the tag that labels it. This approach ensures that prod/rc version labels never leak into development code, and that
dev versions always apear "newer" than rc or prod versions they are based on, and older than the prod/rc versions they become.

### Bump the dev version

Any time you like, you can bump the version number on the dev branch.

```bash
bin/bump-dev [dev|patch|minor|major]   # edits pyproject.toml, does not commit
```

| `bump_type` | Example |
|-------------|---------|
| `dev`       | `1.0.0-dev.1` → `1.0.0-dev.2` |
| `patch`     | `1.0.0-dev.2` → `1.0.1-dev.1` |
| `minor`     | `1.0.0-dev.2` → `1.1.0-dev.1` |
| `major`     | `1.0.0-dev.2` → `2.0.0-dev.1` |

This will modify pyproject.toml to reflect the version change.
Commit and push to `main` before cutting a release.

### Cut a release candidate on TestPyPi

First, bump the dev version to the version you want the release candidate to carry, if necessary.
It is not necessary to be unique, since a unique build number will be added to the released version.

Then, run from `main`:

```bash
bin/cut-rc [--force]
```

This command reads `X.Y.Z-dev.N` from `pyproject.toml`, finds the next unused rc counter
from existing `v<x.y.z>-rc.*` tags, sets the version to `X.Y.Z-rc.N` in a
worktree, tags the commit `v<x.y.z>-rc.<n>`, and pushes the tag —
triggering the `Publish TestPyPI` workflow.

After a successful publish the workflow updates the `rc-latest` tag.

Use `--force` to overwrite an existing tag and retry a failed publish.

### Cut a production release

If you have already published a release candidate, the rc-latest tag should already reflect the version you want
to publish. Run from anywhere on the repo:

```bash
bin/cut-prod [--force] [RC_REF]
```

`RC_REF` is optional. Resolution order:
1. Explicit argument (tag, sha, or bare version like `1.0.5-rc.1`).
2. `HEAD`, if `pyproject.toml` in the working tree carries an `X.Y.Z-rc.N` version.
3. The `rc-latest` tag (the most recently published rc).

Strips the rc qualifier, commits to a worktree, tags the commit `v<x.y.z>`,
and pushes the tag — triggering `Publish`, which updates `prod-latest` and
auto-bumps `main` to `X.Y.(Z+1)-dev.1` after a successful PyPI push.

Use `--force` to overwrite an existing tag and retry a failed publish.

### Guards

Both publish workflows validate that:

- The version in `pyproject.toml` matches the expected format for the target index.
- The version does not already exist on the target index.
- Lint, type checks, and tests pass.

### Smoke test

Use the **Install Smoke Test** workflow to verify an install without publishing:

```bash
# From GitHub source
gh workflow run install-smoke.yml --field source=github --field git_ref=main

# From TestPyPI
gh workflow run install-smoke.yml --field source=testpypi --field version=1.0.0rc1
```

## Forking this repository

If you fork this project and want the full release pipeline to work, you need
to wire up OIDC trusted publishing on both PyPI indexes.
