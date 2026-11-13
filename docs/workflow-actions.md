# Valkey workflow actions

Configure a project connection with the server host, port, database, TLS settings and any username/password secrets. Grant the required key commands. Select the database connection to use exact stored keys within its configured key prefix, or the namespaced connection for account/project/connection-scoped logical keys. Choose the correct action for the key’s type: text/JSON, hash or list. These actions do not provide arbitrary commands or key scanning.

Action names below match the workflow builder. IDs in parentheses remain compatible with saved workflows. Examples show field values; bind values from earlier steps as needed.

## Choose and configure an action

### Text: Read a value (`get`)

Read a text value. Missing keys return null with found false; empty strings remain found.

```json
{
  "key": "orders:1042"
}
```

### Text: Save a value (`set`)

Atomically set a text value with optional TTL and NX or XX condition. An unmet condition returns applied false and value null.

```json
{
  "key": "orders:1042",
  "value": "approved"
}
```

### Key: Delete a stored value (`delete`)

Delete a key. Returns the number of keys deleted.

```json
{
  "key": "orders:1042"
}
```

### Key: Check whether it exists (`exists`)

Return whether the key exists.

```json
{
  "key": "orders:1042"
}
```

### Key: Read remaining expiry time (`ttl`)

Read remaining TTL in seconds: -1 means no expiry and -2 means missing.

```json
{
  "key": "orders:1042"
}
```

### Key: Set expiry time (`expire`)

Set an existing key expiry in seconds.

```json
{
  "key": "orders:1042",
  "ttl_seconds": 3600
}
```

### Counter: Add or subtract an integer (`increment`)

Atomically add an integer to a numeric text value; use a negative amount to subtract. A missing key starts at zero. The result must remain within the safe integer range.

```json
{
  "key": "orders:1042"
}
```

### Hash: Read one field (`hash_get`)

Read one text field from a hash.

```json
{
  "key": "orders:1042",
  "field": "status"
}
```

### Hash: Save field values (`hash_set`)

Set up to 64 hash fields. Returns the number of newly added fields.

```json
{
  "key": "orders:1042",
  "fields": {
    "status": "approved",
    "owner": "team_a"
  }
}
```

### Hash: Delete selected fields (`hash_delete`)

Remove up to 64 hash fields. Returns the number removed.

```json
{
  "key": "orders:1042",
  "fields": [
    "status",
    "owner"
  ]
}
```

### Hash: Read all fields (`hash_get_all`)

Read a bounded hash. Results exceeding the output limit fail without truncation.

```json
{
  "key": "orders:1042"
}
```

### List: Add values at either end (`list_push`)

Push up to 64 text values to the selected list end. Returns list length.

```json
{
  "key": "orders:1042",
  "values": [
    "order_1042",
    "order_1043"
  ]
}
```

### List: Remove and return one value (`list_pop`)

Pop one value from the selected list end without blocking.

```json
{
  "key": "orders:1042"
}
```

### List: Read a range of values (`list_range`)

Read up to 100 list values starting at a nonnegative index.

```json
{
  "key": "orders:1042"
}
```

### List: Count stored values (`list_length`)

Read list length.

```json
{
  "key": "orders:1042"
}
```

### JSON: Read a stored value (`json_get`)

Read JSON stored as an ordinary string; found distinguishes JSON null from a missing key.

```json
{
  "key": "orders:1042"
}
```

### JSON: Save a complete value (`json_set`)

Serialize any JSON value into an ordinary string key. Replaces the whole value; optional expiry and create/update conditions use the same rules as text writes.

```json
{
  "key": "orders:1042",
  "value": {
    "status": "approved",
    "order_id": 1042
  }
}
```

## Requirements and result handling

Text Save replaces the complete value. Omit expiry to save without expiry, clearing an old TTL; `nx` writes only absent keys and `xx` only existing keys. Inspect `result.applied` for unmet conditions. JSON Save serializes the complete JSON value into an ordinary string and JSON Read parses it; RedisJSON is not required and path updates are not offered. Missing keys use `found:false`; stored JSON null remains a found value. Remaining expiry uses `-1` for no expiry and `-2` for a missing key. List pop removes an item; it is a write even though it returns a value. Adding on the right and popping on the left provides queue order.

Workflow write actions are suppressed in test mode. Read/search actions still read the configured service. If a write fails after dispatch, inspect the service before repeating it; the write may already have applied.
