This page is the authority for the transactional cache at `/app/src/cachebox.py`.
Network replication, multi-process locking beyond the single-process fault model
named here, and storage backends other than the on-disk layout below are outside
this interface.

Drive. Run:

```
python3 /app/src/cachebox.py --store <dir> --scenario <file> --report <file>
```

Arguments. Each of `--store`, `--scenario`, and `--report` is required and must
appear with a non-empty value that is not itself one of those three flag names.
`--store` must name an existing directory that is not a symbolic link (`lstat`
must be a directory). `--scenario` must name an existing regular file that is
not a symbolic link. `--report` names the path where the program writes the
report on success. If any required flag is absent or lacks a value, if
`--store` or `--scenario` fails those checks, refuse: write nothing new to
`--report` (when `--report` is absent as a path, do not create it; when it names
a regular file, leave its bytes unchanged; when it is a symbolic link, do not
follow it); print exactly `refuse: bad args` followed by one LF on standard
error; exit with status 2. Standard output is empty on refusal and on success.

Scenario file. The scenario is a UTF-8 JSON object with a top-level array
`steps`. Each step is an object with a string field `op`. Unknown top-level
fields are ignored. Graded scenarios use only the operations and fields named
below. On success the program overwrites `--report` with UTF-8 JSON
(`indent=2`, `ensure_ascii=False`) ending in exactly one LF, and exits 0.

Store layout. Under `--store` the durable files are:

- `committed.json` — the only committed key/value/version and applied-op state.
- `committed.json.tmp` — a temporary file used while preparing a new
  `committed.json`. It is never a source of truth.
- `wal.jsonl` — a write-ahead log of transaction intents. Each line is one JSON
  object. Incomplete final lines are discarded on recovery.

`committed.json` is a JSON object:

```
{
  "format": 1,
  "entries": {
    "<key>": {"value": <JSON value>, "version": <positive integer>}
  },
  "ops": {
    "<op_id>": {
      "status": "committed" | "conflict",
      "writes": {"<key>": {"value": <JSON value>|null, "version": <integer>}}
    }
  }
}
```

Missing `committed.json` means empty `entries` and empty `ops`. Versions of
existing keys are positive integers. A key that is absent has expected version
`0` for conflict checks. After a successful put, the new version is exactly one
greater than the prior committed version (so the first put uses version `1`).
A successful delete removes the key from `entries`.

Keys and identifiers. Keys, transaction ids, and operation ids are non-empty
strings of at most 64 characters matching `^[A-Za-z0-9_.:-]+$`. Values are JSON
values that round-trip through `json.dumps`/`json.loads` with default settings
and contain no `NaN` or Infinity. Graded scenarios stay within these bounds.

In-memory cache. The process keeps an in-memory cache of committed entries.
Visible reads (`get`) must return only committed state. Staged transaction
writes must not become visible until that transaction has durably committed.
After any crash-style fault or process reopen, recovery must make the
in-memory cache equal the durable `committed.json` contents.

Transactions. `begin` opens a named transaction with empty staging. Opening a
transaction that is already open is a step error (`error`: `txn open`). `put`
and `delete` stage changes for an open transaction and do not mutate durable
state or the visible cache. Staging a second change for the same key in one
transaction replaces the earlier staged change. `abort` discards staging and
closes the transaction. `commit` closes the transaction.

Commit and versions. `commit` takes `op_id`. If `op_id` is already present in
`ops`, return that prior recorded result and leave durable state unchanged
(idempotent retry). Otherwise, for each staged write, compare `expected` to
the current committed version of that key (`0` if absent). If any staged write
has a mismatched `expected`, record `ops[op_id]` with `status` `conflict` and
empty `writes`, leave `entries` unchanged, and return status `conflict`. If
all expectations match, apply staged puts and deletes to produce a new
`entries` map, assign versions as defined above (deletes omit the key), record
`ops[op_id]` with `status` `committed` and a `writes` map describing the new
value-or-null and resulting version for each staged key (version `0` and
`value` `null` for deletes), then make that state durable before updating the
cache. Durability order for a successful commit:

1. Append one WAL intent line describing the transaction and `op_id`.
2. Write the full next `committed.json` bytes to `committed.json.tmp`.
3. Replace `committed.json` with that temporary file using an atomic rename
   onto the final name.
4. Update the in-memory cache to match the new committed entries.
5. Append a WAL commit marker line for the same `op_id`.

Conflict recording is also durable through the same atomic `committed.json`
replacement (it may omit a WAL intent; graded conflict paths do not inject
faults mid-conflict). A crash-style fault may interrupt a successful commit
path at the points named below.

Fault injection. A commit step may include `"fault": <name>`. Graded fault
names and the last durable effect before the process stops that commit are:

- `after_wal` — WAL intent line appended; `committed.json` unchanged; cache
  unchanged.
- `after_tmp_partial` — a partial (truncated) `committed.json.tmp` exists;
  `committed.json` unchanged; cache unchanged.
- `after_tmp` — complete `committed.json.tmp` exists; not yet renamed;
  `committed.json` unchanged; cache unchanged.
- `before_cache` — `committed.json` already replaced with the new state; cache
  not yet updated.

When a fault fires, the step result is `{"status": "crashed", "fault": <name>}`
and later steps run only after an `open` or `recover` recreates the store
handle from disk. The program must not leave the cache showing committed data
that is not durable, and must not leave durable committed data invisible after
recovery.

Recovery. Opening a store (scenario start, or an `open` step) and the `recover`
operation must:

1. Discard a partial or complete `committed.json.tmp` without copying it onto
   `committed.json`.
2. Load `committed.json` if present, else empty state.
3. Ignore WAL intent lines whose `op_id` is not yet in `ops` (intents without a
   durable commit are not replayed into `entries`).
4. Rebuild the in-memory cache from loaded `entries`.
5. Return `{"status": "ok", "discarded_tmp": <bool>, "pending_intents": <int>}`
   where `pending_intents` counts ignored intent lines not covered by `ops`.

Repeated recovery on an unchanged store must leave `committed.json` bytes and
visible state identical and must not duplicate ops or entries.

Reads. `get` returns `{"found": false}` for an absent key, or
`{"found": true, "value": <value>, "version": <version>}` for a committed key.
It must never return staged or rolled-back values.

Step operations (graded):

| op | fields | result highlights |
|---|---|---|
| `open` | optional `fault_clear` | recovery object as above |
| `begin` | `txn` | `{"status": "ok"}` or error |
| `put` | `txn`, `key`, `value`, `expected` | `{"status": "ok"}` or error |
| `delete` | `txn`, `key`, `expected` | `{"status": "ok"}` or error |
| `abort` | `txn` | `{"status": "ok"}` or error |
| `commit` | `txn`, `op_id`, optional `fault` | `committed` / `conflict` / `crashed` / idempotent prior |
| `get` | `key` | found payload |
| `recover` | | recovery object |
| `snapshot` | | `{"entries": ..., "ops": ...}` matching durable committed state |

Step errors use `{"status": "error", "error": <code>}` with codes `txn open`,
`txn missing`, `bad key`, `bad op_id`, `bad txn`. After a step error the
scenario continues unless the contract forbids a later op; graded scenarios do
not rely on continuing a missing txn after `txn missing`.

Report. On success `--report` contains:

```
{
  "ok": true,
  "results": [ <one result object per step, in order> ],
  "final": { "entries": ..., "ops": ... }
}
```

`final` must equal a `snapshot` of durable committed state after the last step.
Object key order inside `entries` and `ops` is ascending by key/`op_id` string.
The `writes` object inside each op record also uses ascending key order.

Concurrency model. Graded scenarios are single-process. Out-of-order updates
are expressed as interleaved transactions: a later commit that races an older
`expected` must conflict. Stale reads are graded by `get` between staging and
commit. Crash points, idempotent `op_id` retries, and repeated `recover` are
graded by sealed scenarios absent from `/app/examples`.

Limits. Graded stores use at most 64 keys, 128 ops, and scenario files at most
256 KiB. Each run may take at most 20 seconds.
