import json,re,sys
a=json.load(open(sys.argv[1]))['species_repr']; b=json.load(open(sys.argv[2]))['species_repr']
n=lambda s: re.sub(r' at 0x[0-9a-f]+','',s)
d=[k for k in set(a)|set(b) if n(a.get(k,''))!=n(b.get(k,''))]
print('normalized repr diffs:',len(d),sorted(d)[:10])
if d:
    k=sorted(d)[0]; x,y=n(a[k]),n(b[k])
    i=next(i for i,(p,q) in enumerate(zip(x,y)) if p!=q); print(k, x[i-200:i+200]); print(y[i-200:i+200])
