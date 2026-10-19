"""Content-stable observation / point id fragments (F4 / S15 R-ord).

Durable ids must not embed list ordinals (segment-N, row=N, ::point:N) or
locator / provenance. Key a row by its identity fields. Temperature, when
the row has one, is the ``T=`` token rather than part of the row digest.
"""

from __future__ import annotations

import hashlib
import json

from decimal import Decimal
from typing import Any, Mapping


def temperature_token(value: Any) -> str:
    """Canonical temperature fragment for an id (as-published when given a str)."""

    if value is None:
        raise ValueError("temperature token is required for a content-stable id")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("temperature token is empty")
        return text
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def phase_window_suffix(
    quantity: str,
    *,
    lower_K: Any = None,
    upper_K: Any = None,
) -> str:
    """JANAF series segment key from closed T-bounds, not ordinal segment index."""

    lo = "open" if lower_K is None else temperature_token(lower_K)
    hi = "open" if upper_K is None else temperature_token(upper_K)
    if lo == "open" and hi == "open":
        return f"{quantity}:phase-window:whole"
    return f"{quantity}:phase-window:T={lo}..{hi}"


def _name_slug(name: str) -> str:
    return "-".join(str(name).strip().lower().split())


def tabulated_cell_suffix(
    quantity: str,
    *,
    temperature: Any,
    column: str,
    basis: str | None = None,
    name: str | None = None,
    extra: str | None = None,
) -> str:
    """USGS (and peers) cell key: quantity + T + column (+ printed name) — no row ordinal."""

    mid = f"{basis}:" if basis else ""
    suffix = f"{quantity}:{mid}T={temperature_token(temperature)}:col={column}"
    if name:
        suffix = f"{suffix}:name={_name_slug(name)}"
    if extra:
        suffix = f"{suffix}:{extra}"
    return suffix


# Temperature is the ``series_point_id`` ``T=`` token. Drop it only at the
# top of the row. A nested ``T_K`` is a value, not that token.
_TOP_LEVEL_TEMPERATURE_KEYS = frozenset({"T_K", "T_C", "temperature_K"})

# Locator fields (``records.Locator``) plus printed provenance that is not a
# value coordinate. Dropped at every depth, not only as a top-level key.
_PROVENANCE_KEYS = frozenset(
    {
        "locator",
        "page",
        "published_page",
        "pdf_page_index",
        "pdf_page",
        "locator_page",
        "table",
        "figure",
        "paragraph",
        "section",
        "equation",
        "line_range",
        "pdftotext_line_range",
        "note",
        "notes",
        "notes_printed",
        "source_path",
        "record",
        "quote",
        "temperature_quote",
        "printed_quote",
        "quoted_from",
        "quote_header",
        "footnote",
        "footnote_marker",
        "composition_footnote_as_printed",
        "composition_role",
        "row_number",
        "row_as_printed",
    }
)


def _is_provenance_key(key: str) -> bool:
    return (
        key in _PROVENANCE_KEYS
        or key.endswith(("_locator", "_quote", "_note", "_notes"))
        or "footnote" in key
    )


def _printed_ordinal(key: str, value: Any) -> bool:
    """A numeric ``row`` is the printed line number, not a measured coordinate."""

    return key == "row" and isinstance(value, (int, float)) and not isinstance(value, bool)


def _row_digest(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return "h=" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _without_provenance(value: Any, *, top: bool) -> Any:
    """Copy ``value`` without locator or other provenance. Do not mutate it."""

    if isinstance(value, Mapping):
        kept: dict[Any, Any] = {}
        for key, item in value.items():
            name = key if isinstance(key, str) else str(key)
            if _is_provenance_key(name) or _printed_ordinal(name, item):
                continue
            if top and name in _TOP_LEVEL_TEMPERATURE_KEYS:
                continue
            if isinstance(item, (Mapping, list)):
                stripped = _without_provenance(item, top=False)
                # A child that was already empty is part of the row. A child
                # that held only provenance is not.
                if stripped in ({}, []) and item not in ({}, []):
                    continue
                kept[key] = stripped
            else:
                kept[key] = item
        return kept
    if isinstance(value, list):
        kept_items: list[Any] = []
        for item in value:
            if isinstance(item, (Mapping, list)):
                stripped = _without_provenance(item, top=False)
                if stripped in ({}, []) and item not in ({}, []):
                    continue
                kept_items.append(stripped)
            else:
                kept_items.append(item)
        return kept_items
    return value


def series_row_extra(raw_item: Any, *, include_locator: bool = True) -> str | None:
    """Identity suffix for one source row.

    The digest covers identity-bearing fields only. A provenance fix — a
    page, a quote, a printed line number — must not rename the observation.

    Included, and why:

    - ``run``, else ``sample``, as the printed label ``row=<slug>``. The
      label names which experiment the number belongs to. The experimental
      ``series`` name (O'Neill AD6 / CMAS7, and the same kind of label
      elsewhere) stays inside the digest for the same reason. It is not
      the ``row=`` label; ``run`` / ``sample`` already are.
    - Species, phase, oxide, formula, method class, conditions, and value
      coordinates, at every depth. That is the composition, the activity,
      the printed value, ``log_fO2``, and every other key that is not
      provenance. ``value_as_printed`` and ``T_K_as_printed`` stay: they
      are the printed number, not a quote.

    Left out, and why:

    - Locator fields at every depth, including a locator nested under a
      value: ``page``, ``published_page``, ``pdf_page_index``, ``pdf_page``,
      ``locator_page``, ``table``, ``figure``, ``paragraph``, ``section``,
      ``equation``, ``line_range``, ``pdftotext_line_range``, ``source_path``,
      ``record``, and the key ``locator``. ``locator.paragraph`` and
      ``locator.row`` are locators. They are not the row label. Using them
      as ``row=`` was a locator tiebreak.
    - Notes, quotes, and footnotes: ``note`` / ``notes`` / ``notes_printed``,
      ``quote`` / ``temperature_quote`` / ``printed_quote`` / ``quoted_from``
      / ``quote_header``, any key ending ``_locator`` / ``_quote`` /
      ``_note`` / ``_notes``, and any key containing ``footnote``.
    - ``composition_role`` (b-716). It says what a printed oxide map stands
      for. Declaring it must not rename the row.
    - Printed ordinals: ``row_number``, ``row_as_printed``, and a numeric
      ``row`` (not a boolean). They renumber when a table is re-set. They
      are not a measured coordinate. A non-numeric ``row`` stays.
    - Top-level ``T_K``, ``T_C``, and ``temperature_K``. Temperature is the
      ``T=`` token on ``series_point_id``, not part of this digest. The same
      keys nested under a value stay; they are not that token.

    ``include_locator`` is accepted because the migrator and the extract
    gate already pass it (``container != "series"``). It does not put a
    locator, or any other provenance, back into the digest. Both call
    shapes are one identity.

    A mapping is never hashed raw. Hashing the original put the page back
    in whenever the stripped row was empty. An empty child that held only
    provenance is omitted. A child that was already empty stays, so a row
    that never carried provenance keeps its digest. Two rows that differ
    only by a dropped field are the same observation. The migrator reports
    that collision. It is not split with a locator tiebreak.

    A non-mapping (a bare cell) has no locator. Its suffix is the sha1 of
    its canonical JSON, the same bytes the exploded-point fallback used
    when it hashed the cell.
    """

    # Callers pass the flag. Provenance is excluded either way.
    del include_locator
    if not isinstance(raw_item, Mapping):
        return _row_digest(raw_item)
    parts: list[str] = []
    label = raw_item.get("run")
    if label is None:
        label = raw_item.get("sample")
    if label is not None and str(label).strip():
        parts.append(f"row={_name_slug(str(label))}")
    residual = _without_provenance(raw_item, top=True)
    if residual:
        parts.append(_row_digest(residual))
    return ":".join(parts) if parts else None


def exploded_point_id(
    parent_id: str,
    item: Any,
    coord: Any,
    raw_item: Any,
    row_extra: str | None,
) -> str:
    """Child id for an exploded row. No encounter ordinal.

    A temperature uses the published-T key. A row without one uses
    ``series_row_extra``. ``rows`` and ``points`` are part of that suffix
    so two containers cannot merge. The raw mapping is not hashed: that
    digest included locator and quote. A row with no identity fields and
    no container shares one empty suffix, so two provenance-only rows are
    one observation rather than two ids split by page.
    """

    if coord is not None:
        return series_point_id(parent_id, temperature=coord, extra=row_extra)
    container = item.get("container") if isinstance(item, Mapping) else None
    suffix = row_extra or series_row_extra(raw_item)
    if isinstance(container, str) and container:
        suffix = f"{container}:{suffix}" if suffix else container
    elif not suffix:
        suffix = _row_digest({})
    return series_point_id(parent_id, extra=suffix)



def series_point_id(
    parent_id: str,
    *,
    temperature: Any = None,
    field: str | None = None,
    extra: str | None = None,
) -> str:
    """Exploded series / multi-field child id without an encounter-order index."""

    if field:
        base = f"{parent_id}::field:{field}"
        return f"{base}:{extra}" if extra else base
    if temperature is not None:
        base = f"{parent_id}::T={temperature_token(temperature)}"
        return f"{base}:{extra}" if extra else base
    # No temperature: the printed row/content suffix is the identity.
    # An encounter index would rename the row when the table is reordered.
    if extra:
        return f"{parent_id}::{extra}"
    raise ValueError(
        "series point id requires a temperature, field name, or printed-row identity (no ordinal index)"
    )


def cao_raw_pca_id(*, temperature: Any) -> str:
    return f"cao_raw_pCa:T={temperature_token(temperature)}"
