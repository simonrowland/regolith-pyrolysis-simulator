"""python -m simulator.runner.sio_tsweep."""

from simulator.runner import main_sio_tsweep

# The __main__ guard is load-bearing: multiprocessing 'spawn' children
# re-import the parent's __main__ module (sweep-corpus SC-92).
if __name__ == "__main__":
    raise SystemExit(main_sio_tsweep())
