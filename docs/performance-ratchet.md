# Performance Ratchet Maintenance

`benchmarks/engine_throughput_bench.py` measures isolated simulator hot paths
and compares them with checked-in, machine-specific throughput floors. Rebless
the floors only after an intentional performance improvement:

```bash
python3 benchmarks/engine_throughput_bench.py --rebless-ratchet
```

The current machine class must match the baseline's `machine_class`; the tool
refuses cross-machine updates. Never hand-edit baseline rates. Reblessing is
up-only: each rate becomes the greater of its existing floor and the current
measurement.

When the performance gate is red during a co-suite run, rerun that gate once in
isolation. The benchmark already isolates stages in subprocesses and takes
about 30 seconds solo; report both results rather than widening margins or
reblessing a contention artifact.
