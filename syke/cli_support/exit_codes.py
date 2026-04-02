"""Shared CLI exit codes and small helpers."""

from __future__ import annotations

import click

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_RUNTIME = 4
EXIT_TRUST = 5
EXIT_DATA = 6


class SykeClickException(click.ClickException):
    """Click exception with an explicit Syke exit code."""

    def __init__(self, message: str, *, exit_code: int = EXIT_FAILURE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class SykeAuthException(SykeClickException):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_AUTH)


class SykeRuntimeException(SykeClickException):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_RUNTIME)


class SykeTrustException(SykeClickException):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_TRUST)


class SykeDataException(SykeClickException):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_DATA)


def provider_resolution_exit_code(exc: Exception) -> int:
    if isinstance(exc, ValueError):
        return EXIT_USAGE
    if isinstance(exc, RuntimeError):
        return EXIT_AUTH
    return EXIT_FAILURE
