# Releasing Arbor

Publishing a new version is fully automated. You pick a version number and create
a tag; GitHub Actions builds the package, publishes it to
[PyPI](https://pypi.org/project/arbor-agent/), and creates a GitHub Release.

> The distribution name on PyPI is **`arbor-agent`** (the import name stays `arbor`).

## TL;DR

```bash
git tag v0.2.0
git push origin v0.2.0
```

That's it. A few minutes later `pip install -U arbor-agent` gives users `0.2.0`.

## Versioning rules

- The version is derived **from the git tag** via `setuptools-scm` — never edit a
  `version` field by hand.
- Tags **must** start with `v` (e.g. `v0.2.0`). A tag without the `v` prefix will
  not trigger the release workflow.
- The version **must be higher** than what is already on PyPI. A version can never
  be re-published or overwritten — if `0.2.0` is taken, the next release is `0.2.1`
  or `0.3.0`.
- Follow [semantic versioning](https://semver.org/): `fix`-only → patch (`0.2.1`),
  new features → minor (`0.3.0`), breaking changes → major (`1.0.0`).

## Two ways to release

Both paths trigger the same workflow and publish to PyPI. They differ only in who
writes the GitHub Release notes.

### A. Command line — fully automatic (recommended for most releases)

```bash
git tag v0.2.0
git push origin v0.2.0
```

The workflow creates the GitHub Release for you and auto-generates the notes by
summarizing the pull requests merged since the previous tag.

### B. Web UI — when you want to hand-write the notes

GitHub → **Releases** → **Draft a new release**:

1. **Choose a tag** → type `v0.2.0` → *Create new tag on publish*
2. **Target**: `main`
3. Write your notes (or click **Generate release notes** to start from the auto list)
4. **Publish release**

Publishing creates the tag, which triggers the workflow. The workflow detects that
a Release already exists and **leaves your notes untouched**, only attaching the
built `.whl` / `.tar.gz` artifacts. Your hand-written notes always win.

## What the workflow does

Defined in [.github/workflows/publish.yml](.github/workflows/publish.yml), triggered
on any `v*` tag:

1. **build** — builds the wheel and sdist with `uv build`.
2. **publish** — uploads to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
   (OIDC). No API token is stored anywhere.
3. **github-release** — creates the GitHub Release (auto-notes) or, if you already
   drafted one on the web, updates it with the build artifacts without overwriting
   your notes.

Release-note grouping (Features / Bug Fixes / etc.) is configured in
[.github/release.yml](.github/release.yml) and driven by pull-request labels.

## Tips for good auto-generated notes

- Land changes via **pull requests** merged into `main` — auto-notes are aggregated
  per PR, not per commit.
- Label PRs (`feature`, `bug`, `docs`, `chore`, …) so they sort into the right
  section. Unlabeled PRs fall under *Other Changes*.

## If a release fails

A failed run never affects versions already on PyPI. Check the **Actions** tab for
logs, fix the issue, then push a **new** tag (you cannot reuse the failed version
number) — e.g. `v0.2.1`.

## One-time setup (already done)

For reference, the automation depends on a PyPI **Trusted Publisher** configured at
`https://pypi.org/manage/project/arbor-agent/settings/publishing/` with:

| Field | Value |
|-------|-------|
| Owner | `RUC-NLPIR` |
| Repository | `Arbor` |
| Workflow | `publish.yml` |
| Environment | `pypi` |
