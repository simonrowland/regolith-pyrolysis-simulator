#!/usr/bin/env python3
"""t-1127 review probe: max |new-old| Mn(l) segment over 1519-2334.526 K and anchor value at 2334.526 K."""
import math
ant = lambda A,B,C,T: A - B/(T+C)
old = (9.79086818966, 10402.946653439165, -160.520331526149)
new = (9.808020661644, 10488.603020313, -150.448750249)
d = [(abs(ant(*new,T)-ant(*old,T)), T) for T in [1519 + i*0.5 for i in range(int((2334.526-1519)/0.5)+1)] + [2334.526]]
print("max |new-old| Mn(l) 1519-2334.526 K: %.5f dex at %.1f K" % max(d))
print("new at 2334.526: %.6f (log10 101325 = %.6f; JANAF FUGACITY=1 bar there -> 5.000000)" % (ant(*new,2334.526), math.log10(101325)))
