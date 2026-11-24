"""Error hierarchy for the rules engine. Every error derives from ``AppError``."""

from __future__ import annotations


class AppError(Exception):
    """Base class for all scoring-grading errors."""


class ConfigurationError(AppError):
    """A ruleset file or run setting is invalid."""


class UnknownRulesetError(AppError):
    """The requested ruleset version is not registered or has no file."""


class RulesetChangedError(AppError):
    """A registered ruleset version was edited. Versions are immutable: use a new version."""


class InvalidTriggerError(AppError):
    """The run trigger is not one of the allowed values."""


class ExecutionError(AppError):
    """A SQL statement failed on the backend."""
