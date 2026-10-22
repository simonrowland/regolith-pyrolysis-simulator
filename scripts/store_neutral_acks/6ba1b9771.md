c21acfc5d, 6ba1b9771: t-1424 public battery tools. c21acfc5d names the
residual-key spellings in simulator/battery/score.py, shares its key
splitter with pins.py, and gives compilation_tier.py the shared
no-headline rail token (same strings throughout). 6ba1b9771
streams residual ledgers through iter_residual_payloads in score.py for
scripts/battery_residuals_report.py. Migration's only import from these
modules is the unchanged parse_species_formula, and neither commit
touches scripts/battery_migrate.py, migrate.py, validate.py or data/.
The landing train's full regen of b148 (on green 850a09e93) was a fixed
point. This acknowledges STORE outputs only; the key and report changes
are covered by the commits' tests.
