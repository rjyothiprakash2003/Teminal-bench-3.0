package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"unicode/utf8"
)

type audienceEntry struct {
	Path      string
	AllowUIDs []uint64
	AllowGIDs []uint64
}

type subject struct {
	UID  uint64
	GIDs []uint64
}

type audienceFile struct {
	Entries  []audienceEntry
	Subjects []subject
}

type remediation struct {
	Mode string `json:"mode"`
}

type finding struct {
	Exposure    []string    `json:"exposure"`
	Path        string      `json:"path"`
	Remediation remediation `json:"remediation"`
}

type skipped struct {
	Path   string `json:"path"`
	Reason string `json:"reason"`
}

type report struct {
	Findings []finding `json:"findings"`
	Skipped  []skipped `json:"skipped"`
}

func refuse() {
	os.Stderr.WriteString("refuse: bad args\n")
	os.Exit(2)
}

func parseArgs(argv []string) (root, audience, reportPath string) {
	i := 0
	for i < len(argv) {
		a := argv[i]
		if a == "--root" || a == "--audience" || a == "--report" {
			if i+1 >= len(argv) || argv[i+1] == "" {
				refuse()
			}
			val := argv[i+1]
			switch a {
			case "--root":
				root = val
			case "--audience":
				audience = val
			default:
				reportPath = val
			}
			i += 2
			continue
		}
		refuse()
	}
	if root == "" || audience == "" || reportPath == "" {
		refuse()
	}
	return root, audience, reportPath
}

func isDirNotLink(path string) bool {
	// Strip trailing separators so a symlink-to-dir spelled with trailing
	// slashes is still detected as a symlink by Lstat (Linux otherwise stats
	// the target when the path ends in '/'). A path of only '/' stays '/'.
	check := path
	for len(check) > 1 && (check[len(check)-1] == '/' || check[len(check)-1] == os.PathSeparator) {
		check = check[:len(check)-1]
	}
	st, err := os.Lstat(check)
	if err != nil {
		return false
	}
	return st.Mode().IsDir() && st.Mode()&os.ModeSymlink == 0
}

func isRegNotLink(path string) bool {
	st, err := os.Lstat(path)
	if err != nil {
		return false
	}
	return st.Mode().IsRegular() && st.Mode()&os.ModeSymlink == 0
}

func underRoot(rootReal, candidate string) bool {
	rootReal = filepath.Clean(rootReal)
	candidate = filepath.Clean(candidate)
	if candidate == rootReal {
		return true
	}
	if rootReal == string(os.PathSeparator) {
		return strings.HasPrefix(candidate, string(os.PathSeparator))
	}
	return strings.HasPrefix(candidate, rootReal+string(os.PathSeparator))
}

func validRelPath(p string) bool {
	if p == "" || strings.HasPrefix(p, "/") {
		return false
	}
	for _, part := range strings.Split(p, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

// decodeNonNegUint accepts a JSON number whose mathematical value is a
// non-negative integer fitting in uint64. Digit form, decimals that are exact
// integers (1.0), and exponent form (1e0, 0eN) are accepted. Quoted strings,
// booleans, null, and non-integral numbers are refused. JSON -0 is zero.
func decodeNonNegUint(raw json.RawMessage) (uint64, bool) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 {
		return 0, false
	}
	switch raw[0] {
	case '"', 't', 'f', 'n', '{', '[':
		return 0, false
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var num json.Number
	if err := dec.Decode(&num); err != nil {
		return 0, false
	}
	if dec.More() {
		return 0, false
	}
	s := string(num)
	if u, err := strconv.ParseUint(s, 10, 64); err == nil {
		return u, true
	}
	r := new(big.Rat)
	if _, ok := r.SetString(s); ok {
		if !r.IsInt() || r.Sign() < 0 {
			return 0, false
		}
		i := new(big.Int).Set(r.Num())
		if !i.IsUint64() {
			return 0, false
		}
		return i.Uint64(), true
	}
	// big.Rat rejects some extreme exponents; still accept exact-zero forms
	// such as 0e9223372036854775808 whose mathematical value is 0.
	if u, ok := decodeZeroScientific(s); ok {
		return u, true
	}
	return 0, false
}

// decodeZeroScientific accepts JSON numbers whose significand is exactly zero
// with any integer exponent (including magnitudes beyond signed 64-bit).
func decodeZeroScientific(s string) (uint64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	if s[0] == '+' || s[0] == '-' {
		s = s[1:]
	}
	ei := strings.IndexAny(s, "eE")
	if ei < 0 {
		return 0, false
	}
	sig := s[:ei]
	exp := s[ei+1:]
	if exp == "" {
		return 0, false
	}
	if exp[0] == '+' || exp[0] == '-' {
		exp = exp[1:]
	}
	if exp == "" {
		return 0, false
	}
	for _, c := range exp {
		if c < '0' || c > '9' {
			return 0, false
		}
	}
	sr := new(big.Rat)
	if _, ok := sr.SetString(sig); !ok {
		return 0, false
	}
	if sr.Sign() != 0 {
		return 0, false
	}
	return 0, true
}

func decodeUintArray(raw json.RawMessage) ([]uint64, bool) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || raw[0] != '[' {
		return nil, false
	}
	var elems []json.RawMessage
	if err := json.Unmarshal(raw, &elems); err != nil {
		return nil, false
	}
	out := make([]uint64, 0, len(elems))
	for _, e := range elems {
		v, ok := decodeNonNegUint(e)
		if !ok {
			return nil, false
		}
		out = append(out, v)
	}
	return out, true
}

func objectExactKeys(raw json.RawMessage, required []string) (map[string]json.RawMessage, bool) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	tok, err := dec.Token()
	if err != nil || tok != json.Delim('{') {
		return nil, false
	}
	fields := map[string]json.RawMessage{}
	for dec.More() {
		kt, err := dec.Token()
		if err != nil {
			return nil, false
		}
		key, ok := kt.(string)
		if !ok {
			return nil, false
		}
		var val json.RawMessage
		if err := dec.Decode(&val); err != nil {
			return nil, false
		}
		if _, dup := fields[key]; dup {
			return nil, false
		}
		fields[key] = val
	}
	end, err := dec.Token()
	if err != nil || end != json.Delim('}') {
		return nil, false
	}
	if len(fields) != len(required) {
		return nil, false
	}
	for _, k := range required {
		if _, ok := fields[k]; !ok {
			return nil, false
		}
	}
	return fields, true
}

func loadAudience(path string) audienceFile {
	raw, err := os.ReadFile(path)
	if err != nil {
		refuse()
	}
	if !utf8.Valid(raw) {
		refuse()
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	var rawObj json.RawMessage
	if err := dec.Decode(&rawObj); err != nil {
		refuse()
	}
	// Only JSON whitespace (SP/HT/LF/CR) may follow the top-level value.
	rest := raw[dec.InputOffset():]
	for _, c := range rest {
		if c != ' ' && c != '\t' && c != '\n' && c != '\r' {
			refuse()
		}
	}
	top, ok := objectExactKeys(rawObj, []string{"entries", "subjects"})
	if !ok {
		refuse()
	}

	entriesRaw := bytes.TrimSpace(top["entries"])
	if len(entriesRaw) == 0 || entriesRaw[0] != '[' {
		refuse()
	}
	var entryElems []json.RawMessage
	if err := json.Unmarshal(entriesRaw, &entryElems); err != nil {
		refuse()
	}

	subjectsRaw := bytes.TrimSpace(top["subjects"])
	if len(subjectsRaw) == 0 || subjectsRaw[0] != '[' {
		refuse()
	}
	var subjectElems []json.RawMessage
	if err := json.Unmarshal(subjectsRaw, &subjectElems); err != nil {
		refuse()
	}

	af := audienceFile{
		Entries:  make([]audienceEntry, 0, len(entryElems)),
		Subjects: make([]subject, 0, len(subjectElems)),
	}
	seen := map[string]struct{}{}
	for _, eraw := range entryElems {
		fields, ok := objectExactKeys(eraw, []string{"path", "allow_uids", "allow_gids"})
		if !ok {
			refuse()
		}
		var p string
		if err := json.Unmarshal(fields["path"], &p); err != nil {
			refuse()
		}
		if !validRelPath(p) {
			refuse()
		}
		uids, ok := decodeUintArray(fields["allow_uids"])
		if !ok {
			refuse()
		}
		gids, ok := decodeUintArray(fields["allow_gids"])
		if !ok {
			refuse()
		}
		if _, dup := seen[p]; dup {
			refuse()
		}
		seen[p] = struct{}{}
		af.Entries = append(af.Entries, audienceEntry{Path: p, AllowUIDs: uids, AllowGIDs: gids})
	}
	for _, sraw := range subjectElems {
		fields, ok := objectExactKeys(sraw, []string{"uid", "gids"})
		if !ok {
			refuse()
		}
		uid, ok := decodeNonNegUint(fields["uid"])
		if !ok {
			refuse()
		}
		gids, ok := decodeUintArray(fields["gids"])
		if !ok {
			refuse()
		}
		af.Subjects = append(af.Subjects, subject{UID: uid, GIDs: gids})
	}
	return af
}

type nodeMeta struct {
	path     string
	uid      uint64
	gid      uint64
	unixMode uint32
	perm     uint32
}

func metaFrom(path string, st os.FileInfo) (nodeMeta, bool) {
	statT, ok := st.Sys().(*syscall.Stat_t)
	if !ok {
		return nodeMeta{}, false
	}
	um := uint32(statT.Mode) & 0o7777
	return nodeMeta{
		path:     path,
		uid:      uint64(statT.Uid),
		gid:      uint64(statT.Gid),
		unixMode: um,
		perm:     um & 0o777,
	}, true
}

func classBit(subj subject, n nodeMeta, mask uint32) bool {
	if subj.UID == n.uid {
		return n.perm&mask != 0
	}
	for _, g := range subj.GIDs {
		if g == n.gid {
			return n.perm&(mask>>3) != 0
		}
	}
	return n.perm&(mask>>6) != 0
}

func classUsed(subj subject, n nodeMeta) string {
	if subj.UID == n.uid {
		return "owner"
	}
	for _, g := range subj.GIDs {
		if g == n.gid {
			return "group"
		}
	}
	return "other"
}

func canExec(subj subject, n nodeMeta) bool {
	return classBit(subj, n, 0o100)
}

func canRead(subj subject, n nodeMeta) bool {
	return classBit(subj, n, 0o400)
}

func unintended(e audienceEntry, s subject) bool {
	for _, u := range e.AllowUIDs {
		if u == s.UID {
			return false
		}
	}
	for _, ag := range e.AllowGIDs {
		for _, sg := range s.GIDs {
			if ag == sg {
				return false
			}
		}
	}
	return true
}

func dirsFromRootTo(rootReal, resolvedAbs string) ([]nodeMeta, bool) {
	resolvedAbs = filepath.Clean(resolvedAbs)
	rootReal = filepath.Clean(rootReal)
	if resolvedAbs == rootReal {
		return nil, true
	}
	if !underRoot(rootReal, resolvedAbs) {
		return nil, false
	}
	rel, err := filepath.Rel(rootReal, resolvedAbs)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return nil, false
	}
	var out []nodeMeta
	cur := rootReal
	for _, part := range strings.Split(rel, string(os.PathSeparator)) {
		if part == "" || part == "." {
			continue
		}
		cur = filepath.Join(cur, part)
		st, err := os.Lstat(cur)
		if err != nil {
			return nil, false
		}
		if st.Mode()&os.ModeSymlink != 0 {
			return nil, false
		}
		if !st.IsDir() {
			return nil, false
		}
		m, ok := metaFrom(cur, st)
		if !ok {
			return nil, false
		}
		out = append(out, m)
	}
	return out, true
}

// mergeDirs unions ancestry lists by path so earlier permitted-link pivots
// remain subject to execute checks after later pivots (CAPABILITY.md).
func mergeDirs(dst, src []nodeMeta) []nodeMeta {
	seen := map[string]struct{}{}
	out := make([]nodeMeta, 0, len(dst)+len(src))
	for _, d := range dst {
		if _, ok := seen[d.path]; ok {
			continue
		}
		seen[d.path] = struct{}{}
		out = append(out, d)
	}
	for _, d := range src {
		if _, ok := seen[d.path]; ok {
			continue
		}
		seen[d.path] = struct{}{}
		out = append(out, d)
	}
	return out
}

type resolveResult struct {
	kind string
	dirs []nodeMeta
	file nodeMeta
}

func realRoot(root string) (string, error) {
	// Resolve like realpath(3): walk components left-to-right so a symlink
	// followed by ".." is evaluated against the symlink target. filepath.Abs
	// lexically cleans first and would collapse link/../tree to the wrong dir.
	if !filepath.IsAbs(root) {
		wd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		root = wd + string(os.PathSeparator) + root
	}
	return physicalPath(root)
}

// physicalPath resolves symlinks component-wise without a prior lexical Clean
// of the whole pathname (which would erase symlink/.. sequences incorrectly).
func physicalPath(path string) (string, error) {
	if path == "" {
		return "", os.ErrInvalid
	}
	parts := strings.Split(path, string(os.PathSeparator))
	out := string(os.PathSeparator)
	for i, p := range parts {
		if i == 0 || p == "" || p == "." {
			continue
		}
		if p == ".." {
			parent := filepath.Dir(out)
			if parent != "" {
				out = parent
			}
			continue
		}
		candidate := filepath.Join(out, p)
		st, err := os.Lstat(candidate)
		if err != nil {
			return "", err
		}
		if st.Mode()&os.ModeSymlink != 0 {
			link, err := os.Readlink(candidate)
			if err != nil {
				return "", err
			}
			var next string
			if filepath.IsAbs(link) {
				next = link
			} else {
				// Do NOT filepath.Join: Join lexically collapses symlink/.. in
				// the relative target before those components are resolved.
				if strings.HasSuffix(out, string(os.PathSeparator)) {
					next = out + link
				} else {
					next = out + string(os.PathSeparator) + link
				}
			}
			resolved, err := physicalPath(next)
			if err != nil {
				return "", err
			}
			out = resolved
			continue
		}
		out = candidate
	}
	return out, nil
}

func resolveEntry(rootReal string, rel string) resolveResult {
	parts := strings.Split(rel, "/")
	cur := rootReal
	var dirs []nodeMeta

	for i, part := range parts {
		next := filepath.Join(cur, part)
		st, err := os.Lstat(next)
		if err != nil {
			return resolveResult{kind: "missing"}
		}
		final := i == len(parts)-1
		if st.Mode()&os.ModeSymlink != 0 {
			resolved, err := filepath.EvalSymlinks(next)
			if err != nil {
				if final {
					return resolveResult{kind: "unsupported"}
				}
				return resolveResult{kind: "escaped"}
			}
			resolved, err = filepath.Abs(resolved)
			if err != nil {
				if final {
					return resolveResult{kind: "unsupported"}
				}
				return resolveResult{kind: "escaped"}
			}
			resolved = filepath.Clean(resolved)
			if !underRoot(rootReal, resolved) {
				if final {
					return resolveResult{kind: "unsupported"}
				}
				return resolveResult{kind: "escaped"}
			}
			if final {
				rst, err := os.Lstat(resolved)
				if err != nil || !rst.Mode().IsRegular() || rst.Mode()&os.ModeSymlink != 0 {
					return resolveResult{kind: "unsupported"}
				}
				fm, ok := metaFrom(resolved, rst)
				if !ok {
					return resolveResult{kind: "unsupported"}
				}
				parent := filepath.Dir(resolved)
				anc, ok := dirsFromRootTo(rootReal, parent)
				if !ok {
					return resolveResult{kind: "escaped"}
				}
				return resolveResult{kind: "ok", dirs: mergeDirs(dirs, anc), file: fm}
			}
			rst, err := os.Lstat(resolved)
			if err != nil || !rst.IsDir() || rst.Mode()&os.ModeSymlink != 0 {
				return resolveResult{kind: "unsupported"}
			}
			anc, ok := dirsFromRootTo(rootReal, resolved)
			if !ok {
				return resolveResult{kind: "escaped"}
			}
			dirs = mergeDirs(dirs, anc)
			cur = resolved
			continue
		}
		if final {
			if !st.Mode().IsRegular() {
				return resolveResult{kind: "unsupported"}
			}
			fm, ok := metaFrom(next, st)
			if !ok {
				return resolveResult{kind: "unsupported"}
			}
			return resolveResult{kind: "ok", dirs: dirs, file: fm}
		}
		if !st.IsDir() {
			return resolveResult{kind: "unsupported"}
		}
		m, ok := metaFrom(next, st)
		if !ok {
			return resolveResult{kind: "unsupported"}
		}
		dirs = append(dirs, m)
		cur = next
	}
	return resolveResult{kind: "missing"}
}

func effectiveRead(subj subject, rootMeta nodeMeta, dirs []nodeMeta, file nodeMeta) (bool, string) {
	if !canExec(subj, rootMeta) {
		return false, ""
	}
	for _, d := range dirs {
		if !canExec(subj, d) {
			return false, ""
		}
	}
	if !canRead(subj, file) {
		return false, ""
	}
	return true, classUsed(subj, file)
}

func audit(root string, aud audienceFile) report {
	rootReal, err := realRoot(root)
	if err != nil {
		refuse()
	}
	st, err := os.Lstat(root)
	if err != nil || !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		refuse()
	}
	// Metadata for effective-read on --root itself comes from the named path
	// (same inode as rootReal when ancestors are the only links).
	rootMeta, ok := metaFrom(root, st)
	if !ok {
		refuse()
	}

	var findings []finding
	var skips []skipped

	for _, e := range aud.Entries {
		rr := resolveEntry(rootReal, e.Path)
		if rr.kind != "ok" {
			skips = append(skips, skipped{Path: e.Path, Reason: rr.kind})
			continue
		}
		expSet := map[string]struct{}{}
		used := map[string]bool{}
		for _, s := range aud.Subjects {
			if !unintended(e, s) {
				continue
			}
			okRead, cls := effectiveRead(s, rootMeta, rr.dirs, rr.file)
			if !okRead {
				continue
			}
			expSet[fmt.Sprintf("uid:%d", s.UID)] = struct{}{}
			used[cls] = true
		}
		if len(expSet) == 0 {
			continue
		}
		exp := make([]string, 0, len(expSet))
		for k := range expSet {
			exp = append(exp, k)
		}
		sort.Strings(exp)
		mode := rr.file.unixMode
		if used["other"] {
			mode &^= 0o004
		}
		if used["group"] {
			gidAllowed := false
			for _, g := range e.AllowGIDs {
				if g == rr.file.gid {
					gidAllowed = true
					break
				}
			}
			if !gidAllowed {
				mode &^= 0o040
			}
		}
		findings = append(findings, finding{
			Path:        e.Path,
			Exposure:    exp,
			Remediation: remediation{Mode: fmt.Sprintf("%04o", mode)},
		})
	}

	sort.Slice(findings, func(i, j int) bool { return findings[i].Path < findings[j].Path })
	sort.Slice(skips, func(i, j int) bool { return skips[i].Path < skips[j].Path })
	if findings == nil {
		findings = []finding{}
	}
	if skips == nil {
		skips = []skipped{}
	}
	return report{Findings: findings, Skipped: skips}
}

// pythonJSONString escapes like json.dumps(..., ensure_ascii=False): control
// characters become \uXXXX, but U+2028/U+2029 and other non-ASCII stay literal.
func appendJSONString(buf *bytes.Buffer, s string) {
	buf.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"', '\\':
			buf.WriteByte('\\')
			buf.WriteRune(r)
		case '\b':
			buf.WriteString(`\b`)
		case '\f':
			buf.WriteString(`\f`)
		case '\n':
			buf.WriteString(`\n`)
		case '\r':
			buf.WriteString(`\r`)
		case '\t':
			buf.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(buf, `\u%04x`, r)
			} else {
				buf.WriteRune(r)
			}
		}
	}
	buf.WriteByte('"')
}

func dumpsReport(rep report) []byte {
	var buf bytes.Buffer
	buf.WriteString("{\n  \"findings\": [")
	if len(rep.Findings) == 0 {
		buf.WriteString("],\n  \"skipped\": [")
	} else {
		buf.WriteByte('\n')
		for i, f := range rep.Findings {
			buf.WriteString("    {\n      \"exposure\": [\n")
			for j, e := range f.Exposure {
				buf.WriteString("        ")
				appendJSONString(&buf, e)
				if j+1 < len(f.Exposure) {
					buf.WriteString(",\n")
				} else {
					buf.WriteByte('\n')
				}
			}
			buf.WriteString("      ],\n      \"path\": ")
			appendJSONString(&buf, f.Path)
			buf.WriteString(",\n      \"remediation\": {\n        \"mode\": ")
			appendJSONString(&buf, f.Remediation.Mode)
			buf.WriteString("\n      }\n    }")
			if i+1 < len(rep.Findings) {
				buf.WriteString(",\n")
			} else {
				buf.WriteByte('\n')
			}
		}
		buf.WriteString("  ],\n  \"skipped\": [")
	}
	if len(rep.Skipped) == 0 {
		buf.WriteString("]\n}\n")
		return buf.Bytes()
	}
	buf.WriteByte('\n')
	for i, s := range rep.Skipped {
		buf.WriteString("    {\n      \"path\": ")
		appendJSONString(&buf, s.Path)
		buf.WriteString(",\n      \"reason\": ")
		appendJSONString(&buf, s.Reason)
		buf.WriteString("\n    }")
		if i+1 < len(rep.Skipped) {
			buf.WriteString(",\n")
		} else {
			buf.WriteByte('\n')
		}
	}
	buf.WriteString("  ]\n}\n")
	return buf.Bytes()
}

func writeReport(path string, rep report) {
	// Success path writes through an existing symlink (OpenFile follows). The
	// contract's no-follow rule applies only on refuse: bad args exits.
	out := dumpsReport(rep)
	parent := filepath.Dir(path)
	if parent != "" && parent != "." {
		if err := os.MkdirAll(parent, 0o755); err != nil {
			refuse()
		}
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		refuse()
	}
	if _, err := f.Write(out); err != nil {
		_ = f.Close()
		refuse()
	}
	if err := f.Close(); err != nil {
		refuse()
	}
}

func main() {
	root, audience, reportPath := parseArgs(os.Args[1:])
	if !isDirNotLink(root) {
		refuse()
	}
	if !isRegNotLink(audience) {
		refuse()
	}
	aud := loadAudience(audience)
	rep := audit(root, aud)
	writeReport(reportPath, rep)
}
