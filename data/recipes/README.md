# Named recipe library

Files in this directory are optimizer recipe files: YAML setpoints patches with
the same shape as `winner.recipe.yaml` from `simulator.optimize.study`.

Load one with:

```shell
python -m simulator.runner --feedstock lunar_mare_low_ti --campaign C0 --hours 400 \
  --recipe data/recipes/canonical_lunar_full_yield.yaml --output run.json
```

`canonical_lunar_full_yield.yaml` is the demo/test/FAQ default: staged Path A,
catalog-bounded C3 inventory, Mg recovery before/through C4, then a final
continuous boiloff; C5/MRE stays disabled and the ramp stops at the 1843 C dense-alumina
maximum-service ceiling.

Save an optimizer winner into this library with:

```shell
scripts/save_recipe.py path/to/optimizer-output-dir recipe_name
```

Recipes are validated against the optimizer recipe allowlist before they are
merged into runner setpoints. Runtime campaign overrides are applied later and
therefore take precedence for their fields.
