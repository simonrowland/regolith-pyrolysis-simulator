import sys, json
from simulator.accounting.formulas import parse_formula
out={}
for line in open(sys.argv[1], encoding='utf-8'):
    f=line.rstrip('\n')
    if not f: continue
    try: out[f]={k:round(float(v),6) for k,v in sorted(parse_formula(f).elements.items())}
    except Exception as e: out[f]='ERR '+type(e).__name__
json.dump(out,open(sys.argv[2],'w'))
