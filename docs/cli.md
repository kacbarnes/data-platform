# `lake` CLI

This document is populated alongside the CLI implementation. The intended
workflow is contract-first:

```text
lake datasets
lake ingest <dataset-id>
lake build <dataset-id>
lake test <dataset-id>
lake status [<dataset-id>]
lake lineage <dataset-id>
lake query
```

Commands obtain local platform configuration from the repository `.env`. They
must not require callers to manually register a table, update a catalog pointer,
or write operational metadata.

