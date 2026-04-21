"""Tests verifying the DRY refactoring of GitHub auth error detection.

Both github_source.py and github_tasks.py previously contained duplicated
_is_github_auth_error functions with identical (and overly-broad) keyword lists.
The implementation agent extracted the function into pollers/auth_utils.py and
both modules now import from the shared location.

These tests guard against re-introduction of duplicate logic.
"""

from __future__ import annotations

import inspect


# ---------------------------------------------------------------------------
# Import source verification
# ---------------------------------------------------------------------------


class TestAuthUtilsDryRefactoring:
    """Both poller modules must use the shared auth_utils function."""

    def test_github_source_imports_from_auth_utils(self) -> None:
        """github_source must import is_github_auth_error from auth_utils."""
        from aquarco_supervisor.pollers import github_source

        source = inspect.getsource(github_source)
        assert "from .auth_utils import" in source or "from ..pollers.auth_utils import" in source, (
            "github_source.py should import is_github_auth_error from auth_utils, "
            "not define its own copy."
        )

    def test_github_tasks_imports_from_auth_utils(self) -> None:
        """github_tasks must import is_github_auth_error from auth_utils."""
        from aquarco_supervisor.pollers import github_tasks

        source = inspect.getsource(github_tasks)
        assert "from .auth_utils import" in source or "from ..pollers.auth_utils import" in source, (
            "github_tasks.py should import is_github_auth_error from auth_utils, "
            "not define its own copy."
        )

    def test_github_source_has_no_local_auth_error_function(self) -> None:
        """github_source must NOT define its own _is_github_auth_error."""
        from aquarco_supervisor.pollers import github_source

        # The module should not have a locally-defined function with this name.
        # It may have a reference (the imported alias), but the function's
        # __module__ should be auth_utils, not github_source.
        fn = getattr(github_source, "_is_github_auth_error", None)
        if fn is not None and callable(fn):
            assert fn.__module__ == "aquarco_supervisor.pollers.auth_utils", (
                "_is_github_auth_error in github_source should be the import from "
                f"auth_utils, but __module__ is {fn.__module__}"
            )

    def test_github_tasks_has_no_local_auth_error_function(self) -> None:
        """github_tasks must NOT define its own _is_github_auth_error."""
        from aquarco_supervisor.pollers import github_tasks

        fn = getattr(github_tasks, "_is_github_auth_error", None)
        if fn is not None and callable(fn):
            assert fn.__module__ == "aquarco_supervisor.pollers.auth_utils", (
                "_is_github_auth_error in github_tasks should be the import from "
                f"auth_utils, but __module__ is {fn.__module__}"
            )

    def test_auth_utils_module_exists(self) -> None:
        """The shared auth_utils module must exist and be importable."""
        from aquarco_supervisor.pollers import auth_utils

        assert hasattr(auth_utils, "is_github_auth_error")
        assert callable(auth_utils.is_github_auth_error)

    def test_both_pollers_use_same_function_object(self) -> None:
        """Both pollers must reference the exact same function, not copies."""
        from aquarco_supervisor.pollers import auth_utils, github_source, github_tasks

        source_fn = getattr(github_source, "_is_github_auth_error", None)
        tasks_fn = getattr(github_tasks, "_is_github_auth_error", None)

        assert source_fn is not None, "github_source should have _is_github_auth_error"
        assert tasks_fn is not None, "github_tasks should have _is_github_auth_error"

        # Both should point to the same underlying function
        assert source_fn is tasks_fn, (
            "github_source and github_tasks should reference the same "
            "is_github_auth_error function from auth_utils"
        )


# ---------------------------------------------------------------------------
# Keyword list consistency
# ---------------------------------------------------------------------------


class TestAuthUtilsKeywordConsistency:
    """The shared keyword list should not contain the removed 'token' keyword."""

    def test_no_token_keyword_in_shared_module(self) -> None:
        """The overly-broad 'token' keyword must not reappear."""
        from aquarco_supervisor.pollers.auth_utils import _AUTH_ERROR_KEYWORDS

        assert "token" not in _AUTH_ERROR_KEYWORDS, (
            "The bare 'token' keyword causes false-positive auth failure detection "
            "on unrelated errors like 'invalid JSON token'. It was removed in this fix."
        )

    def test_essential_keywords_present(self) -> None:
        """Core auth error keywords must be present for proper detection."""
        from aquarco_supervisor.pollers.auth_utils import _AUTH_ERROR_KEYWORDS

        # These keywords reliably indicate authentication failures
        for kw in ("401", "unauthorized", "bad credentials", "not logged in"):
            assert kw in _AUTH_ERROR_KEYWORDS, (
                f"Essential keyword '{kw}' is missing from _AUTH_ERROR_KEYWORDS"
            )
