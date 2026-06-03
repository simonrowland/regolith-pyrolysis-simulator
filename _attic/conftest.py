# _attic/ holds code removed from the live tree but preserved for later
# re-integration (e.g. the FactSAGE melt backend). Nothing here is wired
# into the live simulator, so its tests cannot import/run as-is. Block
# pytest from collecting anything under _attic unconditionally -- even when
# pytest is pointed directly at this directory (an explicit path argument
# overrides the `norecursedirs` guard in pyproject.toml).
collect_ignore_glob = ['*']
