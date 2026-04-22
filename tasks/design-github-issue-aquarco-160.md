# Design: Improve `aquarco --version` (Issue #160)

## Summary

Two changes are required:

1. **Short flag**: rename the `-V` alias for `--version` to `-v`.
2. **Dev version string**: when `BUILD_TYPE == "development"`, call `git` at runtime and print `local-dev <branch>@<short-hash>` instead of the static `__version__` string. If git is unavailable or the CWD is not a git repository, fall back to `local-dev unknown`.

Additionally, the static version string is reconciled: `__init__.py` currently says `1.0.0rc2` while `pyproject.toml` says `1.0.0rc1`. `pyproject.toml` is the canonical source; `__init__.py` is updated to match.

---

## Affected Files

| File | Change |
|------|--------|
| `cli/src/aquarco_cli/__init__.py` | Fix version string: `1.0.0rc2` → `1.0.0rc1` |
| `cli/src/aquarco_cli/main.py` | `-V` → `-v`; add `_get_dev_version()` helper; branch on `BUILD_TYPE` in `_version_callback` |
| `cli/tests/test_main.py` | Update `-V` test to `-v`; add dev-mode version tests |

---

## Detailed Design

### `cli/src/aquarco_cli/__init__.py`

Change the single constant:

```python
__version__ = "1.0.0rc1"   # was: 1.0.0rc2 — aligns with pyproject.toml
```

### `cli/src/aquarco_cli/main.py`

#### New import

Add `subprocess` to the stdlib imports and import `BUILD_TYPE`:

```python
import subprocess

from aquarco_cli import __version__
from aquarco_cli._build import BUILD_TYPE
```

(`BUILD_TYPE` is already re-exported via `__init__.py`, but importing directly from `_build` is cleaner and avoids circular-import risk.)

#### New helper `_get_dev_version()`

```python
def _get_dev_version() -> str:
    """Return a git-derived version string for development builds."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return f"local-dev {branch}@{commit}"
    except Exception:
        return "local-dev unknown"
```

Rationale: two separate `check_output` calls are simpler and more portable than a single `git log` format string. `stderr=subprocess.DEVNULL` suppresses git noise when not in a repo.

#### Updated `_version_callback`

```python
def _version_callback(value: bool) -> None:
    if value:
        version_str = _get_dev_version() if BUILD_TYPE == "development" else __version__
        typer.echo(f"aquarco {version_str}")
        raise typer.Exit()
```

#### Updated `main()` signature

Change the `typer.Option` call — only the flag list changes:

```python
version: bool = typer.Option(
    False, "--version", "-v", callback=_version_callback, is_eager=True,
    help="Show version and exit.",
),
```

### `cli/tests/test_main.py`

#### Updated `test_version_short_flag`

```python
def test_version_short_flag(self):
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    # In test environment BUILD_TYPE is "development", so output is not the static version
    assert result.exit_code == 0
    assert "aquarco" in result.output   # basic sanity
```

Since tests run in the source tree (a git repo), the output will be `aquarco local-dev <branch>@<hash>` or `aquarco local-dev unknown` — not `__version__`. The test should not assert on `__version__` here.

#### New `test_version_dev_mode` (using monkeypatch)

```python
def test_version_dev_mode_git_available(self, monkeypatch):
    """Dev build with git available should output local-dev <branch>@<hash>."""
    import aquarco_cli.main as main_mod
    monkeypatch.setattr(main_mod, "BUILD_TYPE", "development")
    monkeypatch.setattr(
        main_mod.subprocess, "check_output",
        lambda cmd, **_: "main\n" if "--abbrev-ref" in cmd else "abc1234\n",
    )
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "local-dev main@abc1234" in result.output
```

#### New `test_version_dev_mode_git_unavailable` (fallback)

```python
def test_version_dev_mode_git_unavailable(self, monkeypatch):
    """Dev build with no git should fall back to 'local-dev unknown'."""
    import aquarco_cli.main as main_mod
    monkeypatch.setattr(main_mod, "BUILD_TYPE", "development")
    monkeypatch.setattr(
        main_mod.subprocess, "check_output",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("git not found")),
    )
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "local-dev unknown" in result.output
```

#### New `test_version_production_mode`

```python
def test_version_production_mode(self, monkeypatch):
    """Production build should output the static __version__ string."""
    import aquarco_cli.main as main_mod
    monkeypatch.setattr(main_mod, "BUILD_TYPE", "production")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
```

---

## Assumptions

- `BUILD_TYPE` from `_build.py` is already `"development"` in the source tree and patched to `"production"` by the Homebrew formula. This distinction cleanly gates the new behaviour.
- No change is needed to `_build.py` itself.
- The `subprocess` module is in Python's stdlib — no new dependency is added to `pyproject.toml`.
- Short commit hash length defaults to git's configured `core.abbrev` (typically 7 characters). This is intentional and consistent with how git displays it in `git log --oneline`.

---

## Out of Scope

- Changing how the version is distributed via Homebrew (the formula already patches `_build.py`).
- Migrating version management to `importlib.metadata` or a VCS tagging scheme.
