"""Base adapter interface for job-source ingestion."""
from typing import Iterable


class AdapterConfigError(Exception):
    """Raised when adapter configuration is invalid."""


class AdapterFetchError(Exception):
    """Raised when fetching from a source fails."""


class BaseAdapter:
    """Abstract base class for source-specific adapters.

    Subclasses must implement ``fetch`` and ``normalize``. They should set
    ``fetch_complete`` to ``True`` only when every page/record was retrieved
    successfully.
    """

    fetch_complete: bool = False

    def __init__(self, source, options: dict):
        self.source = source
        self.options = options

    def fetch(self) -> Iterable[dict]:
        """Yield raw records from the source.

        Each yielded value is a dict that will be passed to ``normalize``.
        """
        raise NotImplementedError

    def normalize(self, raw: dict) -> dict:
        """Map a raw record to Job model fields plus ``_source_name``.

        The returned dict should be suitable for passing to JobSerializer after
        merging with source metadata.
        """
        raise NotImplementedError

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        """Return True if the public config has required keys."""
        return True
