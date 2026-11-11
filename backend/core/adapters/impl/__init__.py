"""Register built-in adapters."""
from core.adapters.impl.ashby import AshbyAdapter
from core.adapters.impl.file_adapters import CsvAdapter, JsonAdapter
from core.adapters.impl.greenhouse import GreenhouseAdapter
from core.adapters.impl.lever import LeverAdapter
from core.adapters.impl.remoteok import RemoteOkAdapter
from core.adapters.impl.rss import RssAdapter
from core.adapters.impl.usajobs import UsaJobsAdapter
from core.adapters.registry import register_adapter


register_adapter("csv", CsvAdapter)
register_adapter("json", JsonAdapter)
register_adapter("usajobs", UsaJobsAdapter)
register_adapter("greenhouse", GreenhouseAdapter)
register_adapter("rss", RssAdapter)
register_adapter("remoteok", RemoteOkAdapter)
register_adapter("lever", LeverAdapter)
register_adapter("ashby", AshbyAdapter)
