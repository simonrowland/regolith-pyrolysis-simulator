d6ccae4b4: SC-333 S333-04 (d-195), one formula owner for oxide molar
masses. It routes oxide projection through simulator/accounting/formulas.py
and removes the literal mass tables in core.py and the builtin
electrolysis/overhead-gas engines. The landing train's full regen of b145
(tip 3ae48641c on green a4608882d, studio-3) was a fixed point: the regen
commit equals the tip, so no store change. This acknowledges STORE
outputs only; the projection value moves (d-195) are covered by the
commit's tests.
