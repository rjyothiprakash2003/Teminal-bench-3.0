This document is the authoritative surface for /app/evaluate.go.

Process every row of the probe pack's ops.jsonl (for the staged pack: /app/probes/ops.jsonl) in ascending op_id order. Apply the ordered rules from the policy pack's rules.jsonl (for the staged pack: /app/policy/rules.jsonl).

--policy names a directory that contains rules.jsonl. --book names a directory that contains SPE_LEDGER.md as a regular file and samples/ as a directory. --ops names a directory that contains ops.jsonl. --out names an output directory. Before loading rules or ops, clear every pre-existing directory entry under --out (including directories and symbolic links). Then verify that SPE_LEDGER.md exists as a regular file under --book (a symbolic link is not a regular file, even when its target is) and that samples/ exists as a directory under --book; if either check fails, exit nonzero without writing verdicts.jsonl or summary.json. The --out clear still applies on that failure path.

Each rules.jsonl row is a JSON object with fields rule_id (string), ord (integer), effect (allow or deny), scope (fs, proc, or net), mode (exact, prefix, or glob), selector (string), and action (string). Declared field names are exact case-sensitive JSON object keys; an extra key that differs only by letter case is ignored and does not replace a declared field. Ord is an arbitrary-precision JSON integer (no size bound such as int64 or uint64); values may repeat; lower ord is earlier. A rule row that fails the checks below is skipped and does not participate in matching. If a declared string field has a JSON type other than string, or ord is missing, null, a JSON string, a non-integer number, or otherwise not a JSON integer, the rule is invalid and skipped.

Allowed actions by scope: fs accepts read, write, exec; proc accepts spawn, kill; net accepts connect, bind. A rule action may also be the single character * which matches every allowed action for that rule's scope. Any other action string makes the rule invalid. Empty rule_id, empty selector, unknown scope, unknown mode, or unknown effect makes the rule invalid. Selector must not contain whitespace.

Each ops.jsonl row is a JSON object with fields op_id (string), scope (fs, proc, or net), action (string), and target (string). Declared field names are exact case-sensitive JSON object keys; an extra key that differs only by letter case is ignored and does not replace a declared field. If a row is a JSON object but any of those fields has a JSON type other than string, the row still receives exactly one disposition: reject_malformed with matched_rule set to an empty string; continue with the remaining rows. In that verdict row, each field whose input JSON type was not string is written as an empty string; fields that were JSON strings keep their decoded string values. An op with empty op_id, unknown scope, an action not allowed for its scope, empty target, or whitespace in target is disposition reject_malformed with matched_rule set to an empty string.

Filesystem and process targets must be absolute paths that begin with /. The path / is allowed. Any other target that ends with /, contains //, or contains a NUL is reject_malformed. Network targets must be host:port where host is a non-empty hostname of letters, digits, dots, and hyphens, and port is a decimal integer in 1..65535 with no leading zeros unless the port is exactly 0 which is forbidden. Any other network target is reject_malformed.

A rule matches an op only when scopes are equal, the rule action is * or equals the op action, and the selector matches the target under the rule mode:

exact: selector equals target.

prefix for fs and proc: target equals selector, or target begins with selector followed by /. This is path inheritance.

prefix for net: split target into host and port. If selector contains a colon, it must equal the full target. If selector has no colon, it matches when host equals selector or host ends with a dot plus selector. That second form is DNS inheritance for subdomains.

glob: the selector is matched with a segment engine after stripping one leading slash from absolute fs and proc selectors and targets. The character / separates segments. Network targets keep host:port as a single string split only on /. A pattern segment that is exactly ** matches one or more target segments. A pattern segment that is exactly * matches exactly one target segment. Any other pattern segment uses character wildcards where * matches any run of characters that does not contain /. An empty segment after splitting, including from //, makes the rule invalid and skipped.

Specificity ranks: exact is 3, prefix is 2, glob is 1.

Among all matching rules for an op, keep only those at the highest specificity present. Among that set, if any effect is deny, the winning effect is deny; otherwise the winning effect is allow. When more than one remaining rule shares that winning effect, the winner is the one with the lowest ord; if ord also ties, the winner is the one whose rule_id is lexicographically least. The verdict is that winning effect and matched_rule is that winner's rule_id.

If no rule matches a well-formed op, verdict is deny_default and matched_rule is empty.

Every op receives exactly one of: allow, deny, deny_default, reject_malformed.

/app/evaluate.go must accept --policy, --book, --ops, and --out with the directory meanings above. Build it as a Go module whose main package lives at /app/evaluate.go with module path cordon.evaluate in /app/go.mod. When pointed at the staged pack it writes only /app/out/verdicts.jsonl and /app/out/summary.json as regular files (not symbolic links). Every other pre-existing directory entry under the output path is removed first, including directories and symbolic links.

verdicts.jsonl carries one JSON object per input op, keys sorted, rows sorted by op_id, fields action, matched_rule, op_id, scope, target, and verdict. Integer fields are JSON numbers, not booleans. matched_rule is a string.

summary.json fields are op_count, by_verdict, and allow_count in that order. The object by_verdict uses exactly these keys in this order: allow, deny, deny_default, reject_malformed (zeros allowed).

Samples under samples/ illustrate specificity and equal-rank denial on concrete rows. Required behavior is stated in this ledger.
