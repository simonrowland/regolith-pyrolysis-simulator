import sys
sys.path.insert(0,'.')
from pathlib import Path
from simulator.reference_data import nasa_glenn
from simulator.vapour_rail.source_rail import _nasa_record_from_path
root=Path('.').resolve(); ok=set()
for e in nasa_glenn.load_manifest()["entries"]:
    try:
        r=_nasa_record_from_path(root/e["path"])
    except Exception as ex: r=None
    if r is not None: ok.add(e["record_id"])
print(len(ok)); open(sys.argv[1],"w").write("\n".join(sorted(ok)))
