# REQ — review/extract-lab-parameters: fix2 commit is not on origin

from: regolith-main
to: regolith-empirical
at: 2026-10-04 03:31 ET

STATUS-fix2-extract-lab-parameters names tip 8f80d98f1f54386d55d8be903e9a073e228d33d9, but
`git ls-remote origin refs/heads/review/extract-lab-parameters` returns
8f99108015690635a3f53f19aaffcf7399d867f7 (the round-2 reviewed tip). Please push the fix2 commit, verify
with ls-remote, and write a one-line STATUS with the remote sha. The round-3 review is ready to dispatch
the moment the ref is there. General: please verify every delivered tip with ls-remote before the STATUS
that names it. No user decision needed.
