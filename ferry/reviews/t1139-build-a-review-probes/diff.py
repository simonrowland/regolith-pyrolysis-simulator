import json,sys
b=json.load(open(sys.argv[1])); h=json.load(open(sys.argv[2]))
print('n', b['n'], h['n'])
print('press identical:', b['press']==h['press'])
print('repr identical:', b['species_repr']==h['species_repr'])
diffs=[k for k in set(b['species_repr'])|set(h['species_repr']) if b['species_repr'].get(k)!=h['species_repr'].get(k)]
print('repr diffs', len(diffs), sorted(diffs)[:40])
def walk(a,c,path,out):
    if type(a)!=type(c): out.append((path,a,c)); return
    if isinstance(a,dict):
        for k in set(a)|set(c):
            if k not in a or k not in c: out.append((path+'/'+k, a.get(k,'<absent>'), c.get(k,'<absent>')))
            else: walk(a[k],c[k],path+'/'+k,out)
    elif isinstance(a,list):
        if len(a)!=len(c): out.append((path,len(a),len(c)))
        else:
            for i,(x,y) in enumerate(zip(a,c)): walk(x,y,f'{path}[{i}]',out)
    elif a!=c: out.append((path,a,c))
out=[]; walk(b['legacy'],h['legacy'],'',out)
print('legacy diffs:', len(out))
for o in sorted(out, key=str)[:80]: print(o)
