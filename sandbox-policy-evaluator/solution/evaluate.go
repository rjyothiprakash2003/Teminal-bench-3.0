package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"unicode"
)

var verdictOrder = []string{"allow", "deny", "deny_default", "reject_malformed"}

var scopeActions = map[string]map[string]struct{}{
	"fs":   {"read": {}, "write": {}, "exec": {}},
	"proc": {"spawn": {}, "kill": {}},
	"net":  {"connect": {}, "bind": {}},
}

var modeRank = map[string]int{"exact": 3, "prefix": 2, "glob": 1}

type rule struct {
	RuleID   string
	Ord      *big.Int
	Effect   string
	Scope    string
	Mode     string
	Selector string
	Action   string
}

type op struct {
	OpID   string
	Scope  string
	Action string
	Target string
}

type verdictRow struct {
	Action      string `json:"action"`
	MatchedRule string `json:"matched_rule"`
	OpID        string `json:"op_id"`
	Scope       string `json:"scope"`
	Target      string `json:"target"`
	Verdict     string `json:"verdict"`
}

func main() {
	policyDir := flag.String("policy", "", "")
	bookDir := flag.String("book", "", "")
	opsDir := flag.String("ops", "", "")
	outDir := flag.String("out", "", "")
	flag.Parse()
	if *policyDir == "" || *bookDir == "" || *opsDir == "" || *outDir == "" {
		fmt.Fprintln(os.Stderr, "missing required flags")
		os.Exit(1)
	}
	if err := clearOut(*outDir); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	ledger := filepath.Join(*bookDir, "SPE_LEDGER.md")
	samples := filepath.Join(*bookDir, "samples")
	if st, err := os.Lstat(ledger); err != nil || !st.Mode().IsRegular() {
		fmt.Fprintln(os.Stderr, "missing SPE ledger")
		os.Exit(1)
	}
	if st, err := os.Lstat(samples); err != nil || !st.IsDir() {
		fmt.Fprintln(os.Stderr, "missing samples")
		os.Exit(1)
	}
	rules, err := loadRules(filepath.Join(*policyDir, "rules.jsonl"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	ops, err := loadOps(filepath.Join(*opsDir, "ops.jsonl"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	sort.Slice(ops, func(i, j int) bool { return ops[i].OpID < ops[j].OpID })
	rows := make([]verdictRow, 0, len(ops))
	by := map[string]int{}
	for _, key := range verdictOrder {
		by[key] = 0
	}
	for _, item := range ops {
		row := decide(item, rules)
		rows = append(rows, row)
		by[row.Verdict]++
	}
	if err := writeVerdicts(filepath.Join(*outDir, "verdicts.jsonl"), rows); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := writeSummary(filepath.Join(*outDir, "summary.json"), len(rows), by); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func readJSONLLines(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	reader := bufio.NewReader(f)
	out := []string{}
	for {
		raw, err := reader.ReadBytes('\n')
		if len(raw) > 0 {
			if raw[len(raw)-1] == '\n' {
				raw = raw[:len(raw)-1]
			}
			if len(raw) > 0 && raw[len(raw)-1] == '\r' {
				raw = raw[:len(raw)-1]
			}
			line := strings.TrimSpace(string(raw))
			if line != "" {
				out = append(out, line)
			}
		}
		if err == io.EOF {
			return out, nil
		}
		if err != nil {
			return nil, err
		}
	}
}

func rawObject(line string) (map[string]json.RawMessage, bool) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(line), &raw); err != nil {
		return nil, false
	}
	return raw, true
}

func exactString(raw map[string]json.RawMessage, key string) (string, bool) {
	v, ok := raw[key]
	if !ok {
		return "", true
	}
	var s string
	if err := json.Unmarshal(v, &s); err != nil {
		return "", false
	}
	return s, true
}

func parseOrd(raw json.RawMessage) (*big.Int, bool) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, false
	}
	// ord must be a JSON number token, not a quoted string or other type.
	if raw[0] == '"' || (raw[0] != '-' && (raw[0] < '0' || raw[0] > '9')) {
		return nil, false
	}
	var n json.Number
	if err := json.Unmarshal(raw, &n); err != nil {
		return nil, false
	}
	s := string(n)
	if s == "" || strings.ContainsAny(s, ".eE") {
		return nil, false
	}
	bi := new(big.Int)
	if _, ok := bi.SetString(s, 10); !ok {
		return nil, false
	}
	return bi, true
}

func parseRule(line string) (rule, bool) {
	raw, ok := rawObject(line)
	if !ok {
		return rule{}, false
	}
	ordRaw, ok := raw["ord"]
	if !ok {
		return rule{}, false
	}
	ord, ok := parseOrd(ordRaw)
	if !ok {
		return rule{}, false
	}
	ruleID, ok := exactString(raw, "rule_id")
	if !ok {
		return rule{}, false
	}
	effect, ok := exactString(raw, "effect")
	if !ok {
		return rule{}, false
	}
	scope, ok := exactString(raw, "scope")
	if !ok {
		return rule{}, false
	}
	mode, ok := exactString(raw, "mode")
	if !ok {
		return rule{}, false
	}
	selector, ok := exactString(raw, "selector")
	if !ok {
		return rule{}, false
	}
	action, ok := exactString(raw, "action")
	if !ok {
		return rule{}, false
	}
	return rule{
		RuleID:   ruleID,
		Ord:      ord,
		Effect:   effect,
		Scope:    scope,
		Mode:     mode,
		Selector: selector,
		Action:   action,
	}, true
}

func coerceString(raw map[string]json.RawMessage, key string) string {
	v, ok := raw[key]
	if !ok {
		return ""
	}
	var s string
	if err := json.Unmarshal(v, &s); err != nil {
		// Wrong JSON type: treat as empty so the op still receives reject_malformed.
		return ""
	}
	return s
}

func parseOp(line string) (op, bool) {
	raw, ok := rawObject(line)
	if !ok {
		return op{}, false
	}
	return op{
		OpID:   coerceString(raw, "op_id"),
		Scope:  coerceString(raw, "scope"),
		Action: coerceString(raw, "action"),
		Target: coerceString(raw, "target"),
	}, true
}

func loadRules(path string) ([]rule, error) {
	lines, err := readJSONLLines(path)
	if err != nil {
		return nil, err
	}
	out := []rule{}
	for _, line := range lines {
		r, ok := parseRule(line)
		if !ok || !validRule(r) {
			continue
		}
		out = append(out, r)
	}
	return out, nil
}

func loadOps(path string) ([]op, error) {
	lines, err := readJSONLLines(path)
	if err != nil {
		return nil, err
	}
	out := []op{}
	for _, line := range lines {
		item, ok := parseOp(line)
		if !ok {
			continue
		}
		out = append(out, item)
	}
	return out, nil
}

func validRule(r rule) bool {
	if r.RuleID == "" || r.Selector == "" {
		return false
	}
	if strings.IndexFunc(r.Selector, unicode.IsSpace) >= 0 {
		return false
	}
	if r.Effect != "allow" && r.Effect != "deny" {
		return false
	}
	actions, ok := scopeActions[r.Scope]
	if !ok {
		return false
	}
	if _, ok := modeRank[r.Mode]; !ok {
		return false
	}
	if r.Action != "*" {
		if _, ok := actions[r.Action]; !ok {
			return false
		}
	}
	if r.Mode == "glob" && !globParsable(r.Selector, r.Scope) {
		return false
	}
	return true
}

func decide(item op, rules []rule) verdictRow {
	row := verdictRow{
		Action:      item.Action,
		MatchedRule: "",
		OpID:        item.OpID,
		Scope:       item.Scope,
		Target:      item.Target,
		Verdict:     "reject_malformed",
	}
	if !validOp(item) {
		return row
	}
	matches := []rule{}
	for _, r := range rules {
		if ruleMatches(r, item) {
			matches = append(matches, r)
		}
	}
	if len(matches) == 0 {
		row.Verdict = "deny_default"
		return row
	}
	bestRank := 0
	for _, r := range matches {
		if modeRank[r.Mode] > bestRank {
			bestRank = modeRank[r.Mode]
		}
	}
	top := []rule{}
	for _, r := range matches {
		if modeRank[r.Mode] == bestRank {
			top = append(top, r)
		}
	}
	wantDeny := false
	for _, r := range top {
		if r.Effect == "deny" {
			wantDeny = true
			break
		}
	}
	effect := "allow"
	if wantDeny {
		effect = "deny"
	}
	winners := []rule{}
	for _, r := range top {
		if r.Effect == effect {
			winners = append(winners, r)
		}
	}
	sort.Slice(winners, func(i, j int) bool {
		if c := winners[i].Ord.Cmp(winners[j].Ord); c != 0 {
			return c < 0
		}
		return winners[i].RuleID < winners[j].RuleID
	})
	win := winners[0]
	row.Verdict = effect
	row.MatchedRule = win.RuleID
	return row
}

func validOp(item op) bool {
	if item.OpID == "" || item.Target == "" {
		return false
	}
	if strings.IndexFunc(item.Target, unicode.IsSpace) >= 0 {
		return false
	}
	actions, ok := scopeActions[item.Scope]
	if !ok {
		return false
	}
	if _, ok := actions[item.Action]; !ok {
		return false
	}
	switch item.Scope {
	case "fs", "proc":
		return validPath(item.Target)
	case "net":
		return validNet(item.Target)
	default:
		return false
	}
}

func validPath(target string) bool {
	if target == "" || target[0] != '/' {
		return false
	}
	if strings.Contains(target, "\x00") || strings.Contains(target, "//") {
		return false
	}
	if target != "/" && strings.HasSuffix(target, "/") {
		return false
	}
	return true
}

func validNet(target string) bool {
	host, port, ok := strings.Cut(target, ":")
	if !ok || host == "" || port == "" {
		return false
	}
	if strings.Contains(host, ":") || strings.Contains(port, ":") {
		return false
	}
	for _, ch := range host {
		if !(unicode.IsLetter(ch) || unicode.IsDigit(ch) || ch == '.' || ch == '-') {
			return false
		}
	}
	if port[0] == '0' && port != "0" {
		return false
	}
	if port == "0" {
		return false
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		return false
	}
	if strconv.Itoa(n) != port {
		return false
	}
	return true
}

func ruleMatches(r rule, item op) bool {
	if r.Scope != item.Scope {
		return false
	}
	if r.Action != "*" && r.Action != item.Action {
		return false
	}
	switch r.Mode {
	case "exact":
		return r.Selector == item.Target
	case "prefix":
		return prefixMatch(r.Scope, r.Selector, item.Target)
	case "glob":
		return matchGlob(r.Selector, item.Target, r.Scope)
	default:
		return false
	}
}

func prefixMatch(scope, selector, target string) bool {
	if scope == "net" {
		if strings.Contains(selector, ":") {
			return selector == target
		}
		host, _, ok := strings.Cut(target, ":")
		if !ok {
			return false
		}
		if host == selector {
			return true
		}
		return strings.HasSuffix(host, "."+selector)
	}
	if target == selector {
		return true
	}
	return strings.HasPrefix(target, selector+"/")
}

func splitSegments(s string, stripLeadingSlash bool) ([]string, bool) {
	if s == "" {
		return nil, false
	}
	if stripLeadingSlash && strings.HasPrefix(s, "/") {
		s = s[1:]
		if s == "" {
			// "/" alone leaves an empty segment after the required strip.
			return nil, false
		}
	}
	parts := strings.Split(s, "/")
	for _, p := range parts {
		if p == "" {
			return nil, false
		}
	}
	return parts, true
}

func globParsable(selector string, scope string) bool {
	_, ok := splitSegments(selector, scope == "fs" || scope == "proc")
	return ok
}

func matchGlob(pattern, target, scope string) bool {
	strip := scope == "fs" || scope == "proc"
	pat, ok1 := splitSegments(pattern, strip)
	targ, ok2 := splitSegments(target, strip)
	if !ok1 || !ok2 {
		return false
	}
	memo := map[[2]int]bool{}
	var rec func(pi, ti int) bool
	rec = func(pi, ti int) bool {
		key := [2]int{pi, ti}
		if cached, ok := memo[key]; ok {
			return cached
		}
		ok := false
		switch {
		case pi == len(pat):
			ok = ti == len(targ)
		case pat[pi] == "**":
			if ti < len(targ) {
				for i := ti + 1; i <= len(targ); i++ {
					if rec(pi+1, i) {
						ok = true
						break
					}
				}
			}
		case ti == len(targ):
			ok = false
		case pat[pi] == "*" || charWild(pat[pi], targ[ti]):
			ok = rec(pi+1, ti+1)
		default:
			ok = false
		}
		memo[key] = ok
		return ok
	}
	return rec(0, 0)
}

func charWild(pat, text string) bool {
	pi, ti := 0, 0
	star := -1
	match := 0
	for ti < len(text) {
		if pi < len(pat) && pat[pi] == '*' {
			star = pi
			match = ti
			pi++
			continue
		}
		if pi < len(pat) && pat[pi] == text[ti] {
			pi++
			ti++
			continue
		}
		if star >= 0 {
			pi = star + 1
			match++
			ti = match
			continue
		}
		return false
	}
	for pi < len(pat) && pat[pi] == '*' {
		pi++
	}
	return pi == len(pat)
}

func clearOut(outDir string) error {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}
	entries, err := os.ReadDir(outDir)
	if err != nil {
		return err
	}
	for _, ent := range entries {
		path := filepath.Join(outDir, ent.Name())
		if err := os.RemoveAll(path); err != nil {
			return err
		}
	}
	return nil
}

func writeVerdicts(path string, rows []verdictRow) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	for _, row := range rows {
		obj := map[string]any{
			"action":       row.Action,
			"matched_rule": row.MatchedRule,
			"op_id":        row.OpID,
			"scope":        row.Scope,
			"target":       row.Target,
			"verdict":      row.Verdict,
		}
		keys := []string{"action", "matched_rule", "op_id", "scope", "target", "verdict"}
		var b strings.Builder
		b.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			kb, _ := json.Marshal(k)
			vb, _ := json.Marshal(obj[k])
			b.Write(kb)
			b.WriteByte(':')
			b.Write(vb)
		}
		b.WriteByte('}')
		b.WriteByte('\n')
		if _, err := f.WriteString(b.String()); err != nil {
			return err
		}
	}
	return nil
}

func writeSummary(path string, count int, by map[string]int) error {
	var b strings.Builder
	b.WriteString(`{"op_count":`)
	b.WriteString(strconv.Itoa(count))
	b.WriteString(`,"by_verdict":{`)
	for i, key := range verdictOrder {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, _ := json.Marshal(key)
		b.Write(kb)
		b.WriteByte(':')
		b.WriteString(strconv.Itoa(by[key]))
	}
	b.WriteString(`},"allow_count":`)
	b.WriteString(strconv.Itoa(by["allow"]))
	b.WriteString("}\n")
	return os.WriteFile(path, []byte(b.String()), 0o644)
}
