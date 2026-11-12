# Third-party software acknowledgements

The research data platform is built on the work of the following projects and
their contributors. Thank you to their authors and maintainers.

This inventory covers the platform's directly declared Python dependencies,
explicitly loaded DuckDB extensions, configured services, and principal build
and runtime tools. It was reviewed on November 12, 2026 against
`pyproject.toml`, `environment.yml`, `compose.yaml`, and the catalog client.
Versions below describe repository requirements, not a lockfile or a complete
inventory of an installed environment. Links identify upstream license sources;
follow the notices included with the particular release you install.

## Python dependencies and DuckDB extensions

| Project and credit | Requirement | Use in this platform | Upstream license |
| --- | --- | --- | --- |
| [DuckDB](https://duckdb.org/), Stichting DuckDB Foundation and contributors | `duckdb==1.5.5`, `duckdb-cli==1.5.5` | Analytical SQL engine, Python client, and interactive query executable | [MIT](https://github.com/duckdb/duckdb/blob/v1.5.5/LICENSE); [Python client license](https://github.com/duckdb/duckdb-python/blob/v1.5.5/LICENSE) |
| [DuckDB Iceberg extension](https://github.com/duckdb/duckdb-iceberg), Stichting DuckDB Foundation and contributors | Installed by DuckDB's extension manager | Iceberg REST catalog attachment and table operations | [MIT](https://github.com/duckdb/duckdb-iceberg/blob/main/LICENSE) |
| [DuckDB httpfs extension](https://github.com/duckdb/duckdb-httpfs), Stichting DuckDB Foundation and contributors | Installed by DuckDB's extension manager | S3-compatible object access | [MIT](https://github.com/duckdb/duckdb-httpfs/blob/main/LICENSE) |
| [PyYAML](https://pyyaml.org/), Kirill Simonov, Ingy döt Net, and contributors | `PyYAML>=6.0` | Dataset contract parsing | [MIT](https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE) |
| [pytz](https://github.com/stub42/pytz), Stuart Bishop and contributors | `pytz>=2025.2` | Declared runtime dependency for timezone support | [MIT](https://github.com/stub42/pytz/blob/master/LICENSE.txt) |
| [podman-compose](https://github.com/containers/podman-compose), Muayyad Alsadi and contributors | `1.6.0`, optional `podman` extra and included in the environment specification | External Compose provider invoked through Podman | [GPL-2.0-only](https://github.com/containers/podman-compose/blob/v1.6.0/LICENSE); [release metadata](https://github.com/containers/podman-compose/blob/v1.6.0/pyproject.toml) |

The extension manager selects extension artifacts compatible with DuckDB.
Extension revisions are not independently pinned by this repository. Extension
binaries and Python wheels may contain additional third-party components and
notices beyond the top-level licenses shown here.

## Data services and table format

| Project and credit | Configured version | Use in this platform | Upstream license |
| --- | --- | --- | --- |
| [Apache Polaris](https://polaris.apache.org/), Apache Software Foundation and contributors | `1.7.0`, server and admin-tool images | Iceberg REST catalog, authorization, and realm bootstrap | [Apache-2.0 and bundled third-party notices](https://github.com/apache/polaris/blob/apache-polaris-1.7.0/LICENSE) |
| [Apache Iceberg](https://iceberg.apache.org/), Apache Software Foundation and contributors | Table format used through DuckDB and Polaris; no separately pinned Iceberg package | Managed table metadata and snapshots | [Apache-2.0](https://github.com/apache/iceberg/blob/main/LICENSE) |
| [PostgreSQL](https://www.postgresql.org/), PostgreSQL Global Development Group and contributors | Major version `17` | Persistent Polaris metastore | [PostgreSQL License](https://www.postgresql.org/about/licence/) |
| [RustFS](https://rustfs.com/), RustFS authors and contributors | `1.0.0-alpha.81` | S3-compatible object storage | [Apache-2.0](https://github.com/rustfs/rustfs/blob/main/LICENSE) |
| [AWS CLI](https://github.com/aws/aws-cli), Amazon.com, Inc. or its affiliates and contributors | `2.36.29` | One-shot bucket existence check and creation | [Apache-2.0](https://github.com/aws/aws-cli/blob/v2/LICENSE.txt) |

`compose.yaml` references upstream images; this repository does not contain
those images. Image base layers and bundled dependencies have their own license
notices. The bootstrap image also uses Python 3.13 on Alpine Linux.

## Runtime, build, and test tools

| Project and credit | Use in this platform | Upstream license |
| --- | --- | --- |
| [Python](https://www.python.org/), Python Software Foundation and contributors | Python 3.13 environment, standard library, and bootstrap interpreter | [PSF-2.0 and incorporated-software notices](https://docs.python.org/3.13/license.html) |
| [Podman](https://podman.io/), Podman contributors | Optional container runtime and machine/connection inspection | [Apache-2.0](https://github.com/containers/podman/blob/main/LICENSE) |
| [Moby/Docker Engine](https://github.com/moby/moby), Moby contributors | Alternative container runtime | [Apache-2.0](https://github.com/moby/moby/blob/master/LICENSE) |
| [Docker Compose](https://github.com/docker/compose), Docker Compose contributors | Compose provider for Docker deployments or an explicitly selected alternative provider | [Apache-2.0](https://github.com/docker/compose/blob/main/LICENSE) |
| [OpenSSH](https://www.openssh.com/), OpenSSH/OpenBSD authors and contributors | Optional managed Podman SSH forwarding | [BSD-style and other permissive notices](https://github.com/openssh/openssh-portable/blob/master/LICENCE); [Windows port](https://github.com/PowerShell/Win32-OpenSSH) |
| [Hatchling](https://hatch.pypa.io/), Ofek Lev and contributors | Package build backend | [MIT](https://github.com/pypa/hatch/blob/master/backend/LICENSE.txt) |
| [editables](https://github.com/pfmoore/editables), Paul Moore and contributors | Editable installation support | [MIT](https://github.com/pfmoore/editables/blob/main/LICENSE.txt) |
| [pip](https://pip.pypa.io/), pip developers | Package installation | [MIT and vendored-component notices](https://github.com/pypa/pip/blob/main/LICENSE.txt) |
| [pytest](https://pytest.org/), Holger Krekel and contributors | Regression testing | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |

Docker Desktop is a separate product from Moby and Docker Compose; the licenses
above describe those open-source projects, not Docker Desktop's product terms.
Optional environment managers, operating systems, and all transitive packages
are outside the scope of this direct-component inventory.

## Research citations

For research using this platform, credit the software actually used and record
its installed version, relevant extension versions, and the platform Git commit.
Prefer each upstream project's citation guidance where supplied.

DuckDB provides an upstream [CITATION.cff](https://github.com/duckdb/duckdb/blob/main/CITATION.cff)
crediting Mark Raasveldt and Hannes Muehleisen. Its foundational paper is:

Mark Raasveldt and Hannes Mühleisen. 2019. *DuckDB: an Embeddable Analytical
Database*. Proceedings of the 2019 International Conference on Management of
Data (SIGMOD '19), 1981–1984. [doi:10.1145/3299869.3320212](https://doi.org/10.1145/3299869.3320212).
See the [upstream publication page](https://duckdb.org/library/duckdb-sigmod-demo/).

For the other components, the project links above provide software references.
Dataset citations and permissions belong to the data sources and dataset
contracts; these software acknowledgements do not grant rights to managed data.

## License scope and maintenance

The platform's [Apache 2.0 license](LICENSE) applies to this project's work.
The components above retain their own licenses and copyright notices. They are
installed or obtained separately; the platform wheel does not vendor their
source code or executables. This acknowledgement file is included in the wheel
and source archive alongside the platform's [NOTICE](NOTICE).

Attribution links and research citations supplement, rather than replace, the
license texts and notices accompanying upstream distributions. Redistributing
third-party software can require preserving those notices and, for components
such as podman-compose, satisfying applicable source-distribution requirements
even when the upstream code is unmodified.

When adding or upgrading a dependency, extension, or service, update its entry
and check that release's license, notices, and citation guidance. If distributing
a bundled environment, executable, or container image, inventory that actual
artifact's transitive components and preserve their required notices too.
