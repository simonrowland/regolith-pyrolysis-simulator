"""Engine source trees for the chemistry-engine refactor.

Each subpackage (``engines.alphamelts``, ``engines.magemin``,
``engines.vaporock``, ``engines.factsage``, ``engines.builtin``) holds the
kernel-shadow / kernel-authoritative provider source for one engine. Binary
install artifacts under those directories (``MAGEMin``, ``run_alphamelts``,
``.cst`` license files, etc.) are gitignored; only the provider Python
modules live under version control here.

The today-hook adapters that drive the simulator before the kernel exists
remain in ``simulator/melt_backend/`` and stay the call site for
``simulator/core.py`` until ``\\goal CHEMISTRY-KERNEL-CARVE-OUT`` lands.
"""
