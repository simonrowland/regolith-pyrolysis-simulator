# Frequently asked questions

## Which recipe is the canonical lunar demo?

Use `data/recipes/canonical_lunar_full_yield.yaml` with a 1-tonne
`lunar_mare_low_ti` charge.  It runs staged Path A, uses the catalog-bounded
C3 Na/K shuttle inventory, and lets the non-FeO dissociation driver recover Mg
in the 1150 °C C3_NA window before C4. It then re-enables the continuous Path A
boiloff. It keeps C5/MRE off and stops that final ramp at the
1843 °C dense-alumina maximum-service limit from `data/furnace_materials.yaml`.

```shell
python -m simulator.runner --feedstock lunar_mare_low_ti --campaign C0 --hours 400 \
  --recipe data/recipes/canonical_lunar_full_yield.yaml --output canonical-lunar.json
```

K remains a recovered alkali product, but the executable thermodynamic gate
refuses K-to-FeO reduction at practical melt temperatures.  Na performs the
surviving FeO cleanup at the catalogued 1150 °C window.
