This document is the authoritative surface for `/app/decode.py`.

Process every row of the pack's `manifest.jsonl` (for the staged pack: `/app/captures/manifest.jsonl`) in ascending `session_id` order.

`--captures` names a capture-pack directory that contains `manifest.jsonl` (not the manifest file path itself). `--spec` names a directory that contains `BW_SPEC.md` and `worked_examples/`. `--out` names an output directory. The decoder reads `manifest.jsonl` from inside the `--captures` directory.

The flags string may contain the characters e, i, and x, each at most once. Any other character, or a repeated allowed character, is disposition `reject_flag`.

Flag e makes the length field a big-endian 16-bit count of payload bytes. Without flag e the length field is one byte. Flag i allows a leading `0x` or `0X` prefix on a frame hex string after ASCII lowercasing; without flag i that prefix is disposition `reject_syntax`. Flag x requires the seal byte to equal the formula below; without flag x the seal byte is still present and consumed but not checked.

Each frames entry is a hex string of even length. Odd length, empty text, non-hex digits, or whitespace anywhere in the text, including at either end, is disposition `reject_syntax`. An empty frames array is disposition `reject_syntax`.

Every decoded frame begins with magic byte 66 (0x42). Byte 1 is the kind. Byte 2 is the sequence number in 0..255. Without flag e, byte 3 is the payload length. With flag e, bytes 3 and 4 are the big-endian payload length. The payload follows. The last byte is the seal. If the remaining length after the header is not payload length plus one, disposition is `reject_syntax`.

The seal byte is (XOR of every byte before the seal, XOR 165, XOR (payload length modulo 256)) modulo 256. Magic other than 66 is disposition `reject_syntax`.

Kind 1 is DATA, kind 2 is ACK, kind 3 is ENCAP, and kind 4 is TERM. Any other kind is disposition `reject_syntax`.

DATA, ENCAP, and TERM share one sequence space in capture order. The first of those kinds is accepted with the sequence number it carries. After a DATA, ENCAP, or TERM with sequence number S is accepted, the next of those kinds must carry (S plus one) modulo 256. A mismatch is disposition `reject_syntax`. ACK frames do not consume this sequence space.

A DATA frame appends its payload bytes to the session in capture order.

An ACK payload must be exactly one byte equal to the sequence number of the most recent DATA or ENCAP frame that contributed session bytes. An ACK before any such frame, or with any other payload, is disposition `reject_syntax`.

A TERM payload must be empty. After TERM, any further frames array entry is disposition `reject_syntax`. A session that never reaches TERM is disposition `reject_syntax`.

An ENCAP payload is itself one complete inner frame parsed with the same flag e and flag x settings as the outer session. If that inner kind is ENCAP, the row is disposition `reject_nested_encap` with empty `bytes` and `frame_count` zero. If the inner kind is DATA, append only the inner DATA payload to the session. Any other inner kind is disposition `reject_syntax`. Worked examples under `worked_examples/` illustrate `reject_nested_encap` on concrete frames.

`/app/decode.py` accepts `--captures`, `--spec`, and `--out` with the directory meanings above. When pointed at the staged pack it writes only `/app/output/sessions.jsonl` and `/app/output/summary.json`. Every other pre-existing directory entry under the output path is removed first, including directories and symbolic links.

sessions.jsonl carries one JSON object per input row, keys sorted, rows sorted by `session_id`, fields `session_id`, `disposition`, `flags`, `frame_count`, and `bytes`. `bytes` is a list of integers in 0..255 for `ok` rows and an empty list for rejects. `frame_count` is the number of frames array entries for `ok` rows and zero for rejects. Integer fields are JSON numbers, not booleans.

summary.json fields are `session_count`, `by_disposition`, and `ok_count` in that order. The object `by_disposition` uses exactly these keys in this order: `ok`, `reject_syntax`, `reject_flag`, `reject_nested_encap` (zeros allowed).

Every session receives exactly one of: `ok`, `reject_syntax`, `reject_flag`, `reject_nested_encap`.
