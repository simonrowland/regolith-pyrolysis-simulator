"""Deliberate analytical-model corpus version config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class CorpusVersionConfigError(RuntimeError):
    """Raised when the shared corpus-version config is missing or invalid."""


@dataclass(frozen=True)
class CorpusVersionConfig:
    corpus_version: str
    interoperable_versions: tuple[str, ...]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def corpus_version_path() -> Path:
    override = os.environ.get("REGOLITH_CORPUS_VERSION_FILE")
    if override:
        return Path(override).expanduser()
    return repo_root() / "data" / "corpus_version.yaml"


def load_corpus_version_config(
    path: str | Path | None = None,
) -> CorpusVersionConfig:
    config_path = Path(path).expanduser() if path is not None else corpus_version_path()
    if not config_path.is_file():
        raise CorpusVersionConfigError(
            f"corpus version config missing: {config_path}"
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise CorpusVersionConfigError(
            f"corpus version config must be a mapping: {config_path}"
        )
    corpus_version = str(raw.get("corpus_version") or "").strip()
    if not corpus_version:
        raise CorpusVersionConfigError(
            f"corpus_version missing or empty in {config_path}"
        )
    raw_interoperable = raw.get("interoperable_versions")
    if not isinstance(raw_interoperable, list):
        raise CorpusVersionConfigError(
            f"interoperable_versions must be a list in {config_path}"
        )
    interoperable_versions = tuple(
        str(value).strip()
        for value in raw_interoperable
        if str(value or "").strip()
    )
    if not interoperable_versions:
        raise CorpusVersionConfigError(
            f"interoperable_versions missing or empty in {config_path}"
        )
    if corpus_version not in interoperable_versions:
        raise CorpusVersionConfigError(
            "corpus_version must appear in interoperable_versions: "
            f"{corpus_version!r}"
        )
    return CorpusVersionConfig(
        corpus_version=corpus_version,
        interoperable_versions=interoperable_versions,
    )


def current_corpus_version() -> str:
    return load_corpus_version_config().corpus_version


def interoperable_corpus_versions() -> tuple[str, ...]:
    return load_corpus_version_config().interoperable_versions
