"""Compatibility exports for the engine-agnostic worker pool.

New code imports :mod:`simulator.engine_pool`; this module remains so older
callers and third-party integrations do not break during the migration.
"""

from simulator.engine_pool import (
    EngineWorkerPool,
    EngineWorkerRemoteError,
    EngineWorkerTimeout,
    EngineWorkerTransport,
    INHERIT_PROCESS_GROUP_ENV,
    WarmEngineWorker,
)

__all__ = (
    'EngineWorkerPool',
    'EngineWorkerRemoteError',
    'EngineWorkerTimeout',
    'EngineWorkerTransport',
    'INHERIT_PROCESS_GROUP_ENV',
    'WarmEngineWorker',
)
