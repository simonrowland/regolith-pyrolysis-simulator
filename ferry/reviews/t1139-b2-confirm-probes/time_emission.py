import time
t0=time.perf_counter()
from simulator.diagnostic_helpers.binary_pot_battery import trace_parent_activity_coefficient_emission
t1=time.perf_counter()
g,d=trace_parent_activity_coefficient_emission(1500.0)
t2=time.perf_counter()
g,d=trace_parent_activity_coefficient_emission(1673.0)
t3=time.perf_counter()
print(f"import {t1-t0:.3f}s first-emission {t2-t1:.4f}s second {t3-t2:.4f}s n={len(g)} gammas, {len(d)} details")
