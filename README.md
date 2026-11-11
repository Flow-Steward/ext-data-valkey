# Valkey

Flow Steward extension `flowsteward.valkey`, version **1.1.5**.

Connect Valkey to Flow Steward for small pieces of workflow state, duplicate-event checks and simple list processing. Read or set text and whole JSON values, delete keys, check existence or TTL, apply expiry and increment counters. Hash actions manage fields and values; list actions push or pop items, read a range and return length. Conditional SET supports create-only or update-only changes with optional expiry and reports whether a change applied.

Read-only Dataset views address explicit string, hash, list and JSON keys using saved definitions and typed parameters, with connection testing, validation, previews and schema inference. JSON is stored in normal string keys without a JSON module. Database connections can use existing keys and enforce an allowed key prefix; legacy project-state connections preserve their account/project/connection namespace.

Use create-only SET plus a TTL to claim an incoming event, or push right and pop left for a FIFO list. Choose an expiry appropriate to the duplicate window and check the applied result before continuing. These patterns provide neither exactly-once processing nor acknowledged delivery: list pops remove items immediately. The extension targets a single standalone server and exposes fixed commands rather than cluster discovery, arbitrary scripting or database subscriptions.

On Flow Steward 1.5.0+, create a Valkey database or project-state connection with a standalone host, port, database number, TLS settings and optional ACL secrets. Private endpoints need host administrator permission; use a least-privilege account and an optional key_prefix for existing database keys.

Flow Steward platform 1.5.0 or newer is required for native JSON Datasets and artifact-free service extensions.

## Connect

Configure a standalone server hostname or IP address, not an HTTP URL, cluster endpoint or Sentinel service. The initial release supports one fixed endpoint and logical databases 0–15. It does not discover topology, follow cluster redirects or provide Redis Stack/vector/search operations.

| Setting | Default | Bounds |
| --- | --- | --- |
| `host` | Required | Hostname or IP address |
| `port` | 6379 | Integer 1–65535 |
| `database` | 0 | Integer 0–15 |
| `namespace` (project state only) | `workflow` | 1–64 ASCII letters, digits, `_` or `-` |
| `key_prefix` (database only) | Empty | Literal prefix restricting full supplied keys |
| `tls` | `true` | Boolean |
| `request_timeout_seconds` | 15 | Integer 1–30 |

Save optional ACL `username` and `password` in the connection's masked secret fields. Use a least-privilege database account. Credentials are not action inputs. TLS checks the server certificate and hostname using the packaged driver's trusted certificate store; verification cannot be disabled. Plaintext (`tls: false`) is accepted only for private endpoints when the host administrator has explicitly enabled private network access. SDK network preflight and endpoint validation run before launching Toolbox. The egress declaration names the connection host fields; it does not promise TCP network isolation.

New database connections use exact supplied keys. Legacy project-state keys are automatically prefixed using the configured namespace and a stable account/project/connection scope. Pass the logical key in each action, without a generated prefix. Changing the connection identity or namespace changes the accessible key space and requires an intentional data migration. Namespacing is an application boundary, so server credentials should also enforce appropriate access. It is not a replacement for database ACLs.

## Actions and results

All 17 workflow actions require `connection_ref` and a nonempty `key`. Text actions use text values; `json_get` and `json_set` preserve any JSON value stored in an ordinary string key. Text is passed as typed Toolbox parameters, so whitespace, newlines, quotes and command-like text remain data.

| Action | Additional inputs | `value` result |
| --- | --- | --- |
| `get` | None | Text, or `null` if missing |
| `set` | Required text `value`; optional `ttl_seconds`, `condition` (`always`, `nx`, `xx`; default `always`) | Server result; `null` when the condition is unmet |
| `json_get` | None | Any JSON value; use `found` to distinguish null from missing |
| `json_set` | Required JSON `value`; optional TTL and condition as SET | Server SET result |
| `delete` | None | Number deleted |
| `exists` | None | Existence result |
| `ttl` | None | Seconds remaining; `-1` means no expiry, `-2` means missing |
| `expire` | Required `ttl_seconds` | Whether expiry was applied |
| `increment` | Optional integer `amount` (default 1) | New counter value |
| `hash_get` | Required text `field` | Text, or `null` if missing |
| `hash_set` | Required object `fields` containing text values | Number of new fields |
| `hash_delete` | Required text array `fields` | Number removed |
| `hash_get_all` | None | Hash fields and values |
| `list_push` | Required text array `values`; optional `direction` (`left` or `right`, default `right`) | List length |
| `list_pop` | Optional `direction` (`left` or `right`, default `left`) | Popped text, or `null` if empty |
| `list_range` | Optional `start` (default 0), `count` (default 100) | Up to `count` text values |
| `list_length` | None | List length |

Each result exposes `value` (JSON), `found` (boolean), `applied` (boolean) and the logical `key`. A missing text value is `null` with `found: false`; a stored empty string is `""` with `found: true`. Conditional SET with an unmet NX/XX condition returns `applied: false` and `value: null`. Check these flags before branching. Wrong-type commands fail instead of returning provider error text as successful data.

SET applies the value, NX/XX condition and optional expiry in one atomic server command. Omitting SET expiry follows the server's SET behavior and removes an existing expiry. Hash/list mutations and counters follow server TTL semantics; they do not accept a separate TTL option. Use `expire` when needed, understanding that it is a separate workflow operation.

## Limits

- Keys and hash fields: at most 256 UTF-8 bytes.
- Each text value: at most 64 KiB; total invocation: at most 256 KiB.
- Hash writes/deletions and list pushes: 1–64 fields or values.
- List reads: `start` 0–2147483647, `count` 1–100; no negative indices.
- TTL: integer 1–2147483647 seconds.
- Counter amount and returned counter: safe IEEE-754 integer range, ±9007199254740991.
- Result: at most 480 KiB. Oversized output fails; it is never silently truncated.

`hash_get_all` is intended for bounded workflow state. Large hashes must be partitioned into application-managed keys. List range reads are bounded, but list storage is not automatically capped.

## Workflow examples

For deduplication, SET `key: event:<event-id>`, `value: processing`, `condition: nx`, `ttl_seconds: 3600`. Continue only if `applied` is true. Choose expiry to match your duplicate window. If processing fails after claiming the key, expiry or an explicit reconciliation step is needed. Expiry can allow another worker to claim a still-running event, so this pattern does not guarantee exactly-once processing.

For a basic FIFO queue, `list_push` with `direction: right`, then `list_pop` with `direction: left`. Items in a multi-value push use native Redis ordering. Pop is nonblocking and removes an item immediately; there is no acknowledgement, visibility timeout, retry queue or crash recovery. Use a dedicated queue service when durable processing guarantees are required.

## Write safety and packaging

The nine mutations (`json_set`, `set`, `delete`, `expire`, `increment`, `hash_set`, `hash_delete`, `list_push`, `list_pop`) require the host's durable external-effect claim and are suppressed in test mode. The extension does not add Core locks. An error after sending a write is uncertain: do not retry automatically. Reconcile against your application state before deciding whether to repeat it. Host effect deduplication does not make server writes transactional with the host ledger.

Only a fixed packaged native Toolbox binary may run. There is no command, executable, tool-config or credential override in action inputs. Each invocation uses one managed command and a private temporary configuration removed after completion or failure. Secrets remain masked in errors and logs. The bounded subprocess adapter enforces output limits and a hard timeout. The minimal pinned Toolbox build disables automatic Redis command retries and maps missing values to JSON null while surfacing command errors. The Go driver owns the network connection; Python does not implement an alternate Redis client. Release packages include pinned Linux amd64/arm64 binaries and checksums.

Datasets provide the read-only logical views described below. There are no database triggers, subscriptions, arbitrary commands, EVAL, CONFIG, FLUSH, transactions or blocking pops. These actions do not provide cross-action serialization.

## Build and verify

From the repository root, run `sh scripts/build_toolbox.sh` to build
both pinned Linux architectures, then
`python3 scripts/package_extension.py --runtime-target linux-amd64`
(and `linux-arm64`) for installable archives under repository `dist/`.
Binaries are build artifacts and are not committed; exact hashes accompany the
source in `toolbox-provenance.json`. Native audit tests require both builds.

From a Flow Steward checkout, run its Docker `lint.sh --checks
extension-static,extension-tests --extension-root /absolute/ext-data-valkey`.
For live tests, create ignored `tests/live-kv.local.json` with a list
such as `[{"kind":"valkey","host":"disposable-valkey"}]`
reachable from `fs-audit`. An optional `password` is a disposable fixture input;
never commit credentials. The tests explicitly enable private access only for
these configured disposable endpoints. They create unique namespaced keys and
test data changes; use disposable servers. Missing live configuration skips
these integration tests without claiming real database verification.

The native build registers only the Redis-compatible source/tool packages needed
for Valkey. Its preserved `flowsteward.redis-valkey` buildType is a reproducibility
identifier for the pinned driver, not a second extension or connection type.

## Dataset queries (1.1.0)

New database connections use normal existing keys without hidden renaming. Workflow
actions target either database connections or legacy project-state connections;
legacy connections keep their original account/project/connection hash namespace.
Database connections optionally restrict full supplied keys using a case-sensitive
`key_prefix`; the prefix is checked, never prepended. Credentials still govern access.

Dataset JSON definitions have exactly `resource_id`, `operation`, and `arguments`.
Logical resources: `strings` (`get`, `mget`), `hashes` (`hget`, `hgetall`),
`lists` (`lrange`), and `json` (`get`, `mget`). No scans, SQL, arbitrary commands,
triggers, or writes are accepted by Datasets. Introspection describes these static
views; connection testing contacts the server with PING. Example:

```json
{"resource_id":"json","operation":"get","arguments":{"key":{"$param":"key"}}}
```

Typed parameters are declared in `param_schema_jsonb.properties`. List reads use
nonnegative `start` and `count` (1–100). Batch reads use `keys` (up to the row limit).
Hash reads use `key` plus `field` for `hget`. Keys and prefix checks run after binding.
Returned rows honour selected columns. Definitions are limited to 64 KiB, depth 20,
and 10,000 nodes; results to 100 rows and 400 KiB; deadlines to 30 seconds including
transport. Oversized hash results fail closed rather than scanning/paging.

JSON is stored in ordinary string keys, without requiring RedisJSON modules.
Workflow `json_set`/`json_get` preserve objects, arrays, booleans, numbers, and null.
A JSON null returns `found: true`; missing keys return `found: false`. JSON Dataset
reads omit missing keys and retain rows whose value is JSON null. Invalid stored
JSON fails. Standard delete removes the whole document. JSON writes retain durable
effect claims, test-mode suppression, and real workflow project-scope requirements.
