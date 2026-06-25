# Named recipe library

Files in this directory are optimizer recipe files: YAML setpoints patches with
the same shape as `winner.recipe.yaml` from `simulator.optimize.study`.

Load one with:

```shell
python -m simulator.runner --feedstock lunar_mare_low_ti --campaign C2A_staged --recipe data/recipes/c2a_staged_temperature_ladder.yaml --output run.json
```

Save an optimizer winner into this library with:

```shell
scripts/save_recipe.py path/to/optimizer-output-dir recipe_name
```

Recipes are validated against the optimizer recipe allowlist before they are
merged into runner setpoints. Runtime campaign overrides are applied later and
therefore take precedence for their fields.
