"""Exceptions and warning output shared by the repoArchiver package."""

from __future__ import annotations

import sys

from .i18n import tr


class RepoArchiverError(Exception):
    """Base class for every expected, user-facing failure."""

    exit_code = 1


class ConfigError(RepoArchiverError):
    """The configuration file is missing, unreadable, or invalid."""


class ArchiveError(RepoArchiverError):
    """An archive is missing, corrupt, or cannot be produced."""


class GitError(RepoArchiverError):
    """A git subprocess failed (or git itself is unavailable)."""


class AmbiguousRef(RepoArchiverError):
    """A short ref matched more than one full ref name."""

    def __init__(self, ref: str, candidates: list[str]) -> None:
        self.ref = ref
        self.candidates = list(candidates)
        candidate_list = ", ".join(self.candidates)
        super().__init__(
            tr(
                'ambiguous ref "{ref}", candidates: {candidates} (use the full name and retry)',
                ref=ref,
                candidates=candidate_list,
            )
        )


class RefNotFound(RepoArchiverError):
    """The requested ref does not exist in the archive."""

    def __init__(self, ref: str, archive_id: str | None = None) -> None:
        self.ref = ref
        message = (
            tr('ref "{ref}" not found in archive "{archive_id}"', ref=ref, archive_id=archive_id)
            if archive_id
            else tr('ref "{ref}" not found', ref=ref)
        )
        super().__init__(message)


class StrictFallback(RepoArchiverError):
    """A restore fallback was requested while --strict is in effect."""


class DestNotEmpty(RepoArchiverError):
    """The restore destination already exists and is not empty."""


def warn(message: str) -> None:
    """Print a non-fatal warning to stderr, prefixed with warning:."""

    print(tr("warning: {message}", message=message), file=sys.stderr)
