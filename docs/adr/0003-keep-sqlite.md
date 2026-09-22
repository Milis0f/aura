# ADR-0003 — Keep SQLite, and treat it as a real database

## Status

Accepted — 2026-09-22

## Context

"Optimise the backend" invites replacing the database, so the choice deserves to be written down rather
than left as inertia.

Aura is a single-process appliance on one machine, frequently offline, installed by a script on a box
with no operator. Concurrency is one household: a television, a phone, a browser, and background scan
jobs. The measurements on a seeded 3000-title, 10 600-file library are the relevant evidence:

| Query | Before | After |
|---|---|---|
| A library page | 36.6 ms | 13.8 ms |
| The library home | 141.5 ms | 37.9 ms |

Nothing in those numbers was a SQLite limit. The cost was a query that aggregated the whole collection to
return sixty rows, and it is just as slow on any engine. Changing database would have hidden the bug
behind a bigger machine.

## Decision

Keep SQLite, in WAL mode, with thread-local connections, and invest in the schema instead: indexes that
serve the actual sort and filter clauses, denormalised columns where an aggregate cannot be indexed, and
pagination that does not touch rows outside the page.

`tools/bench.py` stays in the repository as the arbiter. A query change is justified by a measurement on
a realistic dataset, never by an opinion about which engine is faster.

## Alternatives considered

- **PostgreSQL.** Better concurrent writes and richer indexing. It also means a second service to
  install, supervise, back up and upgrade on an appliance whose whole promise is that `install.sh` runs
  once and is never thought about again. A crashed Postgres is a box that shows nothing; a crashed
  SQLite is a file that is still there. Rejected on operational cost, not capability.
- **DuckDB.** Faster for analytical scans over the library. The workload is point lookups and small
  paginated reads with concurrent writers from the scanner, which is what SQLite is built for and what
  DuckDB is not.
- **An ORM over the current schema.** Would make the query layer more uniform, at the price of hiding the
  generated SQL — and the entire performance problem here was only visible by reading `EXPLAIN QUERY
  PLAN` and seeing four temporary B-trees. Rejected: the diagnosis path matters more than the uniformity.

## Consequences

Positive — nothing new to install, back up, or fail at boot. The database is one file, copied to back it
up. Full-text search and JSON support are available if ever needed.

Negative — one writer at a time. The scan job must keep its writes batched in transactions, as it does,
or readers will stall behind it. No network access to the data from another machine.

Neutral — should a second project ever need Postgres, the core's database access is narrow enough
(`query`, `query_one`, `execute`, `transaction`) that swapping it is a contained job. That is a reason
not to decide now, not a reason to decide differently.

## Trade-offs

Operational silence is bought with a ceiling on write concurrency — a ceiling one household will not
reach, and which the benchmark will reveal long before a user does.
