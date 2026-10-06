import yaml, sys, json, collections
from yaml import CSafeLoader as L
B='/workspace/repos/wt/rv-b730-3c56d0351/'; H='/workspace/repos/wt/rv-b730-1e8ed29ae/'
rel='data/literature/observations-v2/compilations-robie-hemingway-fisher-1978-usgs-b1452/robie-hemingway-fisher-1978-usgs-b1452-0003.yaml'
b=yaml.load(open(B+rel),Loader=L); h=yaml.load(open(H+rel),Loader=L)
print('top keys', b.keys()==h.keys(), {k:(b[k]==h[k]) for k in b if k!='observations'})
bo={o['observation_id']:o for o in b['observations']}; ho={o['observation_id']:o for o in h['observations']}
print('base',len(b['observations']),len(bo),'head',len(h['observations']),len(ho))
rem=sorted(set(bo)-set(ho)); add=sorted(set(ho)-set(bo)); com=set(bo)&set(ho)
print('removed',len(rem),'added',len(add),'common',len(com))
def flat(x,p=''):
    if isinstance(x,dict):
        for k,v in x.items(): yield from flat(v,p+'/'+str(k))
    elif isinstance(x,list):
        for i,v in enumerate(x): yield from flat(v,p+f'[{i}]')
    else: yield p,x
chg=collections.Counter(); per_obs=collections.Counter(); valchg=[]
import re
for i in com:
    fb=dict(flat(bo[i])); fh=dict(flat(ho[i]))
    keys=set(fb)|set(fh); diffs=[k for k in keys if fb.get(k,'<absent>')!=fh.get(k,'<absent>')]
    cats=set()
    for k in diffs:
        g=re.sub(r'\[\d+\]','[]',k); chg[g]+=1; cats.add(g)
    per_obs[tuple(sorted(cats))]+=1
for k,v in chg.most_common(): print(v,k)
print('---- per-observation change signatures')
for k,v in per_obs.most_common(): print(v,k)
json.dump({'removed':rem,'added':add},open('ids_0003.json','w'),indent=0)
# removed summary
for i in rem:
    o=bo[i]
    print('REM', o['identity']['quantity']['value'], o['identity']['species']['formula'], json.dumps(o.get('value'))[:80], o.get('locator',{}).get('published_page') if isinstance(o.get('locator'),dict) else '')
