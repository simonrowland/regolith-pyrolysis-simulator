#!/usr/bin/env python3
"""Snapshot live caches, accumulate a master, and seed new box-local files.

expire, prune, and clear-mask are manual. They print counts and delete
nothing unless ``--apply`` is set. Nothing in the cache read or write path
calls them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import engine_cache as cache  # noqa: E402


def _add_apply_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--apply",
        action="store_true",
        help="delete the matched rows and vacuum. The default is a dry run",
    )
    command.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print counts and delete nothing. This is the default",
    )


def _apply(parser: argparse.ArgumentParser, args: argparse.Namespace) -> bool:
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run cannot be combined")
    return bool(args.apply)


def _expire_species_spellings(names: tuple[str, ...]) -> tuple[str, ...]:
    """Casefolded spellings the cache may have stored for these names.

    The cache leaf stores each request name casefolded and does not import
    the oxide alias map. ``canonical_melt_oxide_activity_name`` is that map:
    ``FeO_total`` and ``FeOT`` are ``FeO``, and ``sio2`` is ``SiO2``. Walking
    the helper's existing ``_OXIDE_ALIASES`` collects every spelling that
    folds to the same oxide. That dict is the provider copy of the adapter
    map, not a third table. Feedstock total-iron keys that the oxide map
    does not contain (``FeO_T``, ``FeO*``) come from
    ``_TOTAL_IRON_AS_FEO_KEYS``. Those keys are already FeO-equivalent.
    A query whose casefold is one of them is looked up as ``FeO`` before
    expansion, and the original casefold stays in the match set. Without
    that, the helper returns None for ``FeO_T`` and ``FeO*``, and the
    expire would keep serving ``feo`` and ``feo_total``. Copying either
    list would let the two drift. A name neither source knows stays its
    own casefold. The cache casefolds again, which does not change these
    strings.
    """

    from engines.alphamelts.domain import (
        _OXIDE_ALIASES,
        canonical_melt_oxide_activity_name,
    )
    from simulator.feedstock_composition import _TOTAL_IRON_AS_FEO_KEYS

    total_iron = {
        str(key).strip().casefold()
        for key in _TOTAL_IRON_AS_FEO_KEYS
        if str(key).strip()
    }
    spellings: list[str] = []
    for name in names:
        folded = name.strip().casefold()
        if not folded:
            continue
        found = {folded}
        lookup = "FeO" if folded in total_iron else name
        canonical = canonical_melt_oxide_activity_name(lookup)
        if canonical is not None:
            canonical_folded = canonical.strip().casefold()
            if canonical_folded:
                found.add(canonical_folded)
            for alias, mapped in _OXIDE_ALIASES.items():
                same = canonical_melt_oxide_activity_name(alias) == canonical
                if not same:
                    same = canonical_melt_oxide_activity_name(mapped) == canonical
                if not same:
                    continue
                for spelling in (alias, mapped):
                    spelling_folded = str(spelling).strip().casefold()
                    if spelling_folded:
                        found.add(spelling_folded)
            # The helper returns "FeO" for FeO and for FeO_total. The
            # feedstock tuple is the other existing spelling list for that
            # same iron, including FeO_T and FeO*.
            if canonical == "FeO":
                for key in _TOTAL_IRON_AS_FEO_KEYS:
                    key_folded = str(key).strip().casefold()
                    if key_folded:
                        found.add(key_folded)
        spellings.extend(sorted(found))
    return tuple(dict.fromkeys(spellings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "seed"):
        command = commands.add_parser(name, help="copy a HOT/COLD pair to new files")
        command.add_argument("source", type=Path)
        command.add_argument("destination", type=Path)
    merge = commands.add_parser("merge", help="accumulate new rows, keeping the master's values")
    merge.add_argument("master", type=Path)
    merge.add_argument("inputs", type=Path, nargs="+")
    merge.add_argument("--strict", action="store_true", help="exit 2 after merging if values conflicted")
    merge.add_argument("--vacuum", action="store_true", help="reclaim unused HOT/COLD pages after merge")
    stats = commands.add_parser(
        "stats",
        help=(
            "report allocated HOT/COLD bytes per row and species-unknown "
            "rows per engine. Read-only"
        ),
    )
    stats.add_argument("file", type=Path)
    expire = commands.add_parser(
        "expire",
        help=(
            "delete one engine's rows whose recorded inputs include a named "
            "species, and that engine's species-unknown digest rows"
        ),
    )
    expire.add_argument("file", type=Path)
    expire.add_argument("--engine", required=True)
    choice = expire.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--species",
        help=(
            "comma-separated species names; FeO_T and FeO* are FeO, and "
            "each name includes the oxide spellings "
            "canonical_melt_oxide_activity_name treats as the same"
        ),
    )
    choice.add_argument(
        "--all",
        action="store_true",
        dest="all_rows",
        help="delete every row of --engine, including rows with no recorded species",
    )
    _add_apply_flags(expire)
    prune = commands.add_parser(
        "prune",
        help="delete rows whose chemistry version is not the current one",
    )
    prune.add_argument("file", type=Path)
    _add_apply_flags(prune)
    migrate = commands.add_parser(
        "migrate",
        help="publish a schema-4 copy of a schema-3 HOT/COLD pair without deleting values",
    )
    migrate.add_argument("file", type=Path)
    migrate.add_argument(
        "--no-vacuum",
        action="store_true",
        help="keep the freed pages. The default reclaims them",
    )
    migrate.add_argument(
        "--recover",
        action="store_true",
        help=(
            "finish a complete schema-4 copy, or restore the schema-3 pair "
            "from hard-linked backups, then remove leftover migration files"
        ),
    )
    clear_mask = commands.add_parser(
        "clear-mask",
        help="delete one engine's refusal-mask cells and audit rows for one class",
    )
    clear_mask.add_argument("file", type=Path)
    clear_mask.add_argument("--engine", required=True)
    clear_mask.add_argument("--class", required=True, dest="refusal_class")
    _add_apply_flags(clear_mask)
    args = parser.parse_args(argv)
    try:
        if args.command in {"snapshot", "seed"}:
            result = cache.snapshot_cache(args.source, args.destination)
        elif args.command == "merge":
            result = cache.merge_cache_files(args.master, args.inputs, vacuum=args.vacuum)
        elif args.command == "expire":
            species = None
            if args.species is not None:
                species = _expire_species_spellings(
                    tuple(part.strip() for part in args.species.split(",") if part.strip())
                )
            result = cache.expire_cache_file(
                args.file,
                engine=args.engine,
                species=species,
                all_rows=bool(args.all_rows),
                apply=_apply(parser, args),
            )
        elif args.command == "prune":
            result = cache.prune_cache_file(args.file, apply=_apply(parser, args))
        elif args.command == "migrate":
            if args.recover:
                result = cache.recover_cache_file(args.file)
            else:
                result = cache.migrate_cache_file(args.file, vacuum=not args.no_vacuum)
        elif args.command == "clear-mask":
            result = cache.clear_refusal_mask(
                args.file,
                engine=args.engine,
                refusal_class=args.refusal_class,
                apply=_apply(parser, args),
            )
        else:
            result = cache.engine_cache_stats(args.file.expanduser())
    except (OSError, sqlite3.Error, ValueError, cache.EngineCacheSchemaMismatch) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if isinstance(result, dict) and args.command == "expire":
        unknown = result.get("species_unknown_rows")
        if unknown is not None:
            # First line, before the JSON, so the cost is not buried in it.
            print(f"species-unknown rows: {unknown}", file=sys.stderr)
            print(
                "any expire --species on this engine will delete these rows",
                file=sys.stderr,
            )
        unclassifiable = result.get("unclassifiable_rows")
        if unclassifiable is not None:
            print(f"unclassifiable rows: {unclassifiable}", file=sys.stderr)
    if isinstance(result, dict) and args.command == "migrate" and args.recover:
        print(
            f"recover: {result.get('action')}; "
            f"schema {result.get('schema_version')}; "
            f"hot rows {result.get('hot_rows')}",
            file=sys.stderr,
        )
    if isinstance(result, dict) and args.command == "stats":
        unknown_by_engine = result.get("species_unknown_by_engine") or {}
        listed = (
            ", ".join(
                f"{engine} {count}"
                for engine, count in sorted(unknown_by_engine.items())
            )
            or "0"
        )
        print(f"species-unknown rows: {listed} per engine", file=sys.stderr)
    if isinstance(result, dict) and args.command == "migrate" and result.get("migrated"):
        unknown_by_engine = result.get("species_unknown_by_engine") or {}
        listed = (
            ", ".join(
                f"{engine} {count}" for engine, count in sorted(unknown_by_engine.items())
            )
            or "0"
        )
        print(
            f"species-unknown rows: {listed} per engine; "
            "any expire --species on that engine will delete them; "
            "prefer re-warming over migrating",
            file=sys.stderr,
        )
        before = result.get("before") or {}
        after = result.get("after") or {}
        hot_before = (before.get("hot") or {}).get("database_bytes")
        hot_after = (after.get("hot") or {}).get("database_bytes")
        cold_before = (before.get("cold") or {}).get("database_bytes")
        cold_after = (after.get("cold") or {}).get("database_bytes")
        print(
            f"migrate bytes: hot {hot_before} -> {hot_after}; "
            f"cold {cold_before} -> {cold_after}",
            file=sys.stderr,
        )
    print(json.dumps(result, sort_keys=True))
    return 2 if args.command == "merge" and args.strict and result["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
