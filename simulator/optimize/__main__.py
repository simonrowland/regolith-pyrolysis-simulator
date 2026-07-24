"""python -m simulator.optimize."""

from simulator.optimize.cli import main

# The __main__ guard is load-bearing: multiprocessing 'spawn' children
# re-import the parent's __main__ module — for `python -m
# simulator.optimize` that is THIS file. Unguarded, every optimizer
# process-pool worker re-executed the whole CLI recursively (same class
# as the sio_* virtual-entrypoint bug fixed in f1fb933).
if __name__ == "__main__":
    raise SystemExit(main())
