# Upstream submission draft: headless singleton MPI defaults

Repository: `ComputationalThermodynamics/MAGEMin`
Base: v1.9.6, commit `40ed109`

## Issue text

### Title

Avoid unused OpenCL probing and network listeners for MPI singletons

### Description

Each standalone MAGEMin invocation initializes Open MPI as a singleton. On macOS, hwloc's OpenCL
topology probe can contact WindowServer even though MAGEMin does not use GPU topology. The PMIx and
PRTE runtimes also open listening sockets for a process that does not need network peers. These
per-call side effects are unnecessary for the current CPU-only execution path.

### Minimal reproduction

Build with MPI enabled, then launch one point from a logged-in macOS console session:

```sh
./MAGEMin --Verb=0 --db=ig --Temp=1100 --Pres=3
```

Record the process ID while it is running, then inspect that PID's unified logs and sockets:

```sh
log show --last 5m --style compact --predicate "processIdentifier == $PID"
lsof -nP -a -p "$PID" -iTCP -iUDP
```

### Measurements

A console-session probe on an Apple-silicon Mac Studio running macOS 27.x measured one MAGEMin
PID per invocation. The headless row used the same hwloc and transport values proposed below; that
probe also unset `DISPLAY`.

| Environment | WindowServer connections | SkyLight lines | OpenCL lines | TCC lines | TCP/UDP sockets | LISTEN sockets |
|---|---:|---:|---:|---:|---:|---:|
| Default singleton | 1 | 2 | 4 | 1 | 2 | 4 |
| Equivalent headless environment | 0 | 0 | 0 | 0 | 0 | 0 |

Counts were filtered by process ID. Process-name log windows included unrelated MAGEMin runs and
were not used for these measurements.

## Pull request text

### Title

Use headless local defaults for standalone MPI runs

### Description

Before `MPI_Init`, this change excludes hwloc's unused OpenCL component. When MAGEMin is not under
a parallel launcher, it defaults Open MPI's BTL/PML and the PMIx/PRTE interfaces to local
transport (`lo0` on macOS, `lo` elsewhere). Each setting uses `setenv(..., 0)`, so values supplied
explicitly by the caller take precedence.

The launcher check looks for `PMIX_RANK`, `OMPI_COMM_WORLD_SIZE`, and `PMI_RANK`. When one is
present, the singleton transport defaults are skipped and the launcher retains control of BTL,
PML, PMIx, and PRTE configuration. The OpenCL exclusion still applies because MAGEMin does not use
GPU topology.

The default singleton probe recorded one WindowServer connection and four LISTEN sockets per
process. The equivalent headless environment recorded zero for both, with no per-PID SkyLight,
OpenCL, or TCC log lines. The proposed defaults are intended to remove these unneeded singleton
side effects while preserving caller overrides and parallel launcher settings.

## Verification

Verified outside a sandbox on macOS 27.2 with Homebrew Open MPI 5.0.9 and hwloc 2.13.0, with no
environment overrides. Build A (`40ed109` plus patch 0002) recorded 1 WindowServer connection,
2 SkyLight lines, 4 OpenCL lines, 1 TCC line, and 2 LISTEN sockets for one PID. Build B (A plus
patch 0004) recorded zero for all five counts. Under `mpirun -np 2`, both builds returned 0 and
produced byte-identical pseudosection output (SHA-256
`d23e91e4f6fc4d186af79a701c04631910d117c1080f26f75292c41764581b90`).
