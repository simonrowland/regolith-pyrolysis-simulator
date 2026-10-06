# Scan 0003 printed uncertainties at head; count newly attached vs 3c56d0351; flag implausible tokens.
import yaml
from yaml import CSafeLoader as L
from collections import Counter
rel='data/literature/observations-v2/compilations-robie-hemingway-fisher-1978-usgs-b1452/robie-hemingway-fisher-1978-usgs-b1452-0003.yaml'
B='/workspace/repos/wt/rv-b730-3c56d0351/'; H='/workspace/repos/wt/rv-b730-1e8ed29ae/'
b={o['observation_id']:o for o in yaml.load(open(B+rel),Loader=L)['observations']}
h={o['observation_id']:o for o in yaml.load(open(H+rel),Loader=L)['observations']}
def pr(o): return (o.get('uncertainty') or {}).get('kind')=='printed'
new=Counter(); allp=Counter(); bad=[]
for i,o in h.items():
    if not pr(o): continue
    q=o['identity']['quantity']['value']; st=o['admission']['status']; allp[(q,st)]+=1
    if i in b and not pr(b[i]): new[(q,st)]+=1
    vb=str(o['uncertainty'].get('verbatim')); v=o['value'].get('point')
    try: w=float(vb)
    except ValueError: bad.append(('non-numeric',q,st,o['identity']['species']['formula'],v,vb,o['locator'].get('published_page'))); continue
    vv=abs(float(v)) if v else 0.0
    r=None
    if w<0: r='negative'
    elif q=='S' and vv and w>0.25*vv: r='S>25%'
    elif q in('delta_fH','delta_fG') and w>60000: r='H/G>60kJ'
    elif q=='log10_Kf' and w>3: r='logK>3'
    if r: bad.append((r,q,st,o['identity']['species']['formula'],v,vb,o['locator'].get('published_page')))
print('printed at head',sum(allp.values()),dict(allp)); print('newly attached',sum(new.values()),dict(new))
for x in bad: print('FLAG',x)
