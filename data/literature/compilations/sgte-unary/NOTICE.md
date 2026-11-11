# SGTE Unary database notice

Source: <https://www.sgte.net/en/free-pure-elements-database>

Citation: Dinsdale, A. T. (1991). SGTE data for pure elements. *CALPHAD* 15, 317–425. DOI: 10.1016/0364-5916(91)90011-S.

Upstream terms, quoted from the official SGTE free-pure-elements page:

> This database can only be used for extracting data for assessment work or to tabulate or plot data for the pure elements.

The verbatim `unary50.tdb` is not distributed in this repository. The loader fetches it on demand from the official SGTE URL recorded in `source/sidecar.yaml`, verifies its SHA-256, and caches it outside the repository. Use `python -m simulator.chemistry.sgte_unary fetch` to populate the cache.

The derived records in `records/` remain listed under the quoted SGTE terms, not the project licence. The synthetic parser fixture in the test suite is not SGTE data.
