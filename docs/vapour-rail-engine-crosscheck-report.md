# Vapour rail engine cross-check report

`vapour_rail_engine_crosscheck` reports matched builtin rail and VapoRock
pressures. Its JSON `schema_version` is 3. Version 3 adds provider notices to
the existing version 2 report without removing or renaming fields.

The builtin rail observation at `rows[].rail` contains
`notices_by_species`, a mapping from gas species to a list of typed provider
notices. A notice retains the provider's fields, including `kind`, `reason`,
`authority_level`, `affected_species`, and `affected_quantities`; provider
specific fields such as the certified band and extrapolation diagnostics remain
available. The field is scoped to the row's species, so an Fe pressure notice
appears on a matched Fe rail observation and does not appear on an unrelated gas
observation. VapoRock observations do not carry builtin provider notices.

The `engines.builtin` entry identifies the `BuiltinVaporPressureProvider`
route represented by the report's `rail` side. VapoRock remains an independent
pressure comparator.

Version 2 reports remain readable by consumers that use the existing fields.
The rail conformance reader consumes the established `domain`, `composition`,
and `rows` fields and does not reject an unknown version or require the new
notice field.
