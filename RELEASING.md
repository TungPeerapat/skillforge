# Releasing SkillForge

This is the runbook for pushing the repository to GitHub and shipping a release.
The release workflow (`.github/workflows/release.yml`) verifies, builds, publishes
to PyPI with [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) and
creates a GitHub release. No API token is needed in the normal path.

---

## 0. One-time: publish the repository

```bash
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

If you are publishing under a different owner than the upstream placeholder
(`skillforge/skillforge`), update the URLs first:

```bash
python - <<'PY'
from pathlib import Path

slug = "OWNER/REPO"  # e.g. "ada-lovelace/skillforge"
targets = [
    "pyproject.toml",
    "README.md",
    "CONTRIBUTING.md",
    ".github/ISSUE_TEMPLATE/config.yml",
]
for name in targets:
    path = Path(name)
    text = path.read_text(encoding="utf-8")
    if "skillforge/skillforge" in text:
        path.write_text(text.replace("skillforge/skillforge", slug), encoding="utf-8")
        print("updated", name)
PY
git add -A && git commit -m "docs: point repository URLs at the published remote"
```

Then, in **Settings → Branches → Branch protection rule** for `main`, require
these status checks (they are the job names produced by `ci.yml`):

| Required check |
| --- |
| `Lint and format` |
| `Type check` |
| `Tests (ubuntu-latest, Python 3.12)` |
| `Tests (ubuntu-latest, Python 3.13)` |
| `Tests (windows-latest, Python 3.12)` |
| `Tests (windows-latest, Python 3.13)` |
| `Demo workflow (no API key)` |
| `Package build` |

Recommended repository settings:

- **Actions → General → Workflow permissions**: read-only by default (the
  workflows already request the minimum they need).
- Enable **Dependabot alerts** and **secret scanning** (push protection).
- `llm-integration` is manual-only and continues on error; it is **not** a
  required check, so a normal run never depends on an API key.

> The local equivalents of every CI step are in `CONTRIBUTING.md`. Run them
> before pushing if you want to avoid a red build.

## 1. One-time: configure PyPI publishing

1. Create the project on PyPI (or add a **pending publisher** so the first
   release can create it).
2. PyPI → *Your account* → **Publishing** → **Add a pending publisher**:
   - PyPI project name: `skillforge`
   - Owner: your GitHub owner
   - Repository name: your repository
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In GitHub → **Settings → Environments**, create an environment named `pypi`.
   Optionally add required reviewers so a human approves the publish step.

If you cannot use Trusted Publishing, create a PyPI API token, store it as the
`PYPI_API_TOKEN` repository secret, and change the publish step to:

```yaml
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN }}
```

## 2. Prepare the release

1. Update `CHANGELOG.md`: add a `## [x.y.z] - YYYY-MM-DD` section (the release
   workflow refuses to publish without it).
2. Bump `version` in `pyproject.toml`. That is the **single source of truth** —
   `skillforge.__version__` reads the installed distribution metadata, so there
   is no second file to edit.
3. Run the local gate:

   ```bash
   ruff check src tests && ruff format --check src tests
   mypy
   pytest -q
   skillforge analyze examples/fastapi-demo
   skillforge generate examples/fastapi-demo && skillforge validate examples/fastapi-demo
   ```
4. Commit through a pull request and merge to `main`.

## 3. Cut the release

```bash
git switch main && git pull
git tag -a v0.2.0 -m "SkillForge 0.2.0"
git push origin v0.2.0
```

The workflow then runs, in order:

1. **Verify** — ruff, mypy, `pytest -q` on the tagged commit.
2. **Build** — asserts the tag matches `pyproject.toml` and that `CHANGELOG.md`
   has the matching section, builds sdist + wheel, installs the wheel and runs
   `skillforge --version`.
3. **Publish to PyPI** — via OIDC, in the protected `pypi` environment.
4. **Create GitHub release** — `gh release create` with generated notes and the
   distributions attached (re-running with an existing release uploads with
   `--clobber`).

To re-run a release (for example after fixing a failed job) use
**Actions → Release → Run workflow** and pass the tag, e.g. `v0.2.0`.

## 4. Verify the release

```bash
python -m venv /tmp/sf && . /tmp/sf/bin/activate     # Windows: \tmp\sf\Scripts\activate
pip install "skillforge==0.2.0"
skillforge --version
skillforge doctor
skillforge analyze examples/fastapi-demo   # from a checkout of the repo
```

Check that the GitHub release has the wheel and sdist attached and that the
generated notes list the right commits.

## 5. When something goes wrong

| Problem | Action |
| --- | --- |
| Tag does not match the version | Delete the tag (`git push origin :v0.2.0`), fix `pyproject.toml`, re-tag |
| CHANGELOG check fails | Add the `## [x.y.z]` section, push to `main`, then re-run the workflow by tag |
| PyPI upload fails after partial success | PyPI never allows re-uploading a version: yank it, bump the patch version, tag again |
| PyPI project does not exist yet | Add the pending publisher from step 1 and re-run |
| GitHub release must be corrected | Edit it in the UI, or `gh release delete v0.2.0` and re-run the workflow |

## Notes

- Releases are immutable on PyPI. Treat the tag as write-once.
- The `release` environment concurrency group prevents two releases running at
  the same time.
- Version numbers follow [SemVer](https://semver.org/); user-visible changes go
  in `CHANGELOG.md` under *Added / Changed / Fixed / Removed*.
