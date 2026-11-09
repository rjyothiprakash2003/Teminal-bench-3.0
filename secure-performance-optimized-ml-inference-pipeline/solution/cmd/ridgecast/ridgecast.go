package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"kelso.internal/ridgecast/internal/httpapi"
	"kelso.internal/ridgecast/internal/ridgeio"
	"kelso.internal/ridgecast/internal/scorekit"
	"kelso.internal/ridgecast/internal/tickbench"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
	switch cmd {
	case "selfcheck":
		os.Exit(selfcheck())
	case "score":
		os.Exit(runScore())
	case "serve":
		os.Exit(runServe())
	case "bench":
		os.Exit(runBench())
	default:
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, "usage: ridgecast selfcheck|score|serve|bench\n")
}

func resolveAsset(p string) string {
	if p == "" || filepath.IsAbs(p) {
		return p
	}
	cwd, err := os.Getwd()
	if err != nil {
		return p
	}
	return filepath.Clean(filepath.Join(cwd, p))
}

func loadKit(configPath string) (*scorekit.Kit, ridgeio.Sidecar, error) {
	cfg, err := ridgeio.LoadKV(configPath)
	if err != nil {
		return nil, cfg, err
	}
	cfg.SchemaPath = resolveAsset(cfg.SchemaPath)
	cfg.CoefPath = resolveAsset(cfg.CoefPath)
	cfg.ScalePath = resolveAsset(cfg.ScalePath)
	cfg.TokenFile = resolveAsset(cfg.TokenFile)
	schema, err := ridgeio.LoadSchema(cfg.SchemaPath)
	if err != nil {
		return nil, cfg, err
	}
	w, err := ridgeio.LoadWeights(cfg.CoefPath, cfg.ScalePath, schema)
	if err != nil {
		return nil, cfg, err
	}
	tok, err := ridgeio.ReadToken(cfg.TokenFile)
	if err != nil {
		return nil, cfg, err
	}
	return scorekit.New(schema, w, tok, cfg.TokenSHA, cfg.BatchLimit), cfg, nil
}

func defaultConfig() string {
	return "/app/deploy/sidecar.kv"
}

// illegalSingleDash reports whether args contain a single-hyphen flag form such as
// -config. Double-hyphen flags and their values (which may begin with '-') are allowed.
func illegalSingleDash(args []string) bool {
	valueNext := false
	for _, a := range args {
		if valueNext {
			valueNext = false
			continue
		}
		if a == "--" {
			break
		}
		if strings.HasPrefix(a, "--") {
			body := a[2:]
			name, _, hasEq := strings.Cut(body, "=")
			if !hasEq && (name == "config" || name == "intake" || name == "out" || name == "spec") {
				valueNext = true
			}
			continue
		}
		if strings.HasPrefix(a, "-") {
			return true
		}
	}
	return false
}

// hasBareEndOfFlags reports a bare "--" where a flag or positional is expected
// (not when "--" is consumed as the value of --config/--intake/--out/--spec).
func hasBareEndOfFlags(args []string) bool {
	valueNext := false
	for _, a := range args {
		if valueNext {
			valueNext = false
			continue
		}
		if a == "--" {
			return true
		}
		if strings.HasPrefix(a, "--") {
			body := a[2:]
			name, _, hasEq := strings.Cut(body, "=")
			if !hasEq && (name == "config" || name == "intake" || name == "out" || name == "spec") {
				valueNext = true
			}
			continue
		}
	}
	return false
}

func recoverOutAmong(args []string, start int) string {
	found := ""
	for j := start; j < len(args); j++ {
		a := args[j]
		if a == "--" {
			break
		}
		// Honor value consumption for other known score flags so a value token
		// that happens to be spelled "--out" is not treated as an output flag.
		if a == "--config" || a == "--intake" {
			if j+1 < len(args) {
				j++
			}
			continue
		}
		if strings.HasPrefix(a, "--config=") || strings.HasPrefix(a, "--intake=") {
			continue
		}
		if a == "--out" && j+1 < len(args) {
			found = args[j+1]
			j++
			continue
		}
		if strings.HasPrefix(a, "--out=") {
			found = strings.TrimPrefix(a, "--out=")
			continue
		}
		// Unknown flags and leftover positionals are skipped so the last later
		// --out among the remaining tokens is recovered (score --bogus --out A
		// --bogus2 --out B removes B).
		if strings.HasPrefix(a, "--") {
			continue
		}
		if strings.HasPrefix(a, "-") && a != "-" {
			continue
		}
		// Leftover positional: keep scanning.
		continue
	}
	return found
}

func outPathFromArgs(args []string) string {
	// Mirror Go flag value consumption for the score FlagSet: --config, --intake,
	// and --out each take the next token as their value even when that token is
	// the two-character string "--". A bare "--" is the end-of-flags marker only
	// when it appears where a flag (or positional) would be expected, not when it
	// is consumed as such a value. An unknown flag stops Go flag parsing: the
	// last --out consumed before that unknown flag remains the cleanup target, and
	// a later --out after the unknown flag is not consumed. When no --out was
	// consumed before an unknown flag, the last later --out among the remaining tokens is
	// still recovered for cleanup. A leftover positional stops parsing the same
	// way; among leftovers the last later --out is recovered only when none was already
	// consumed. An explicitly consumed empty PATH (e.g. --out=) still counts as
	// consumed, so later recovery must not replace it.
	found := ""
	consumed := false
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--" {
			// Bare end-of-flags is illegal for score; it stops parsing but does
			// NOT unlock later --out recovery (only unknown flags / positionals do).
			break
		}
		if a == "--out" || a == "--config" || a == "--intake" {
			if i+1 >= len(args) {
				continue
			}
			if a == "--out" {
				found = args[i+1]
				consumed = true
			}
			i++
			continue
		}
		if strings.HasPrefix(a, "--out=") {
			found = strings.TrimPrefix(a, "--out=")
			consumed = true
			continue
		}
		if strings.HasPrefix(a, "--config=") || strings.HasPrefix(a, "--intake=") {
			continue
		}
		if strings.HasPrefix(a, "--") {
			// Unknown flag: stop. Recover a later --out only if none consumed yet.
			if !consumed {
				found = recoverOutAmong(args, i+1)
			}
			break
		}
		if strings.HasPrefix(a, "-") && a != "-" {
			// Illegal single-hyphen flag: stop. Go would still consume -out PATH
			// as the output, so that PATH is the cleanup target. For other -name
			// forms, skip a value token so score -intake PATH --out A recovers A.
			name, val, hasEq := strings.Cut(strings.TrimPrefix(a, "-"), "=")
			if name == "out" {
				if hasEq {
					found = val
					consumed = true
				} else if i+1 < len(args) {
					found = args[i+1]
					consumed = true
				}
				break
			}
			if !consumed {
				start := i + 1
				if !hasEq && (name == "config" || name == "intake") && start < len(args) {
					start++
				}
				found = recoverOutAmong(args, start)
			}
			break
		}
		// Leftover positional stops Go flag parsing. If --out was already
		// consumed, that PATH remains the sole cleanup target — a later
		// --out after the positional must NOT be recovered or deleted.
		// Only when none was consumed yet, recover the last later --out
		// among the remaining tokens (value-aware reparse).
		if !consumed {
			found = recoverOutAmong(args, i+1)
		}
		break
	}
	return found
}

func selfcheck() int {
	// Contract: selfcheck takes none. Any argv after the subcommand is illegal,
	// including flags, positionals, and the bare end-of-flags token "--".
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "selfcheck takes no arguments")
		return 2
	}
	kit, _, err := loadKit(defaultConfig())
	if err != nil {
		fmt.Fprintln(os.Stderr, "selfcheck: cannot load config")
		return 1
	}
	x := append([]float64(nil), kit.W.Mean...)
	logit := kit.LogitOn(x)
	m := scorekit.Micros(kit.Prob(x))
	// Mean lab requires exact logit == bias -0.45, not only matching micros.
	if logit != -0.45 || m != 389361 {
		fmt.Printf("selfcheck mean lab got logit=%g micros=%d\n", logit, m)
		return 1
	}
	xOff := append([]float64(nil), kit.W.Mean...)
	xOff[0] = 72
	xOff[1] = 0.36
	off := scorekit.Micros(kit.Prob(xOff))
	if off != 364705 {
		fmt.Println("selfcheck off-mean lab mismatch")
		return 1
	}
	// Print offmean only after both labs pass (not before the mean-lab check).
	fmt.Printf("offmean_micros %d\n", off)
	fmt.Println("selfcheck ok")
	return 0
}

func runScore() int {
	args := os.Args[1:]
	namedOut := outPathFromArgs(args)
	removeOut := func() {
		if namedOut == "" {
			return
		}
		target := filepath.Clean(namedOut)
		if target == "" {
			return
		}
		absOut, err := filepath.Abs(target)
		if err != nil {
			absOut = target
		}
		st, err := os.Lstat(absOut)
		if err != nil {
			// Already absent.
			return
		}
		// Contract: remove regular files and symlinks only — never directories
		// (including `.` / the process cwd).
		if st.IsDir() {
			return
		}
		_ = os.Remove(absOut)
	}
	if illegalSingleDash(args) {
		fmt.Fprintln(os.Stderr, "score: single-hyphen flags are illegal")
		removeOut()
		return 2
	}
	if hasBareEndOfFlags(args) {
		fmt.Fprintln(os.Stderr, "score: bare end-of-flags marker is illegal")
		removeOut()
		return 2
	}
	fs := flag.NewFlagSet("score", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.Usage = func() {}
	config := fs.String("config", defaultConfig(), "sidecar kv")
	intake := fs.String("intake", "", "intake jsonl")
	out := fs.String("out", "", "scores jsonl")
	if err := fs.Parse(args); err != nil {
		removeOut()
		return 2
	}
	// Prefer the parsed --out when consumed; otherwise keep the pre-scan target
	// (Go stops at leftover positionals without error and leaves *out empty).
	if *out != "" {
		namedOut = *out
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "score: unexpected arguments")
		removeOut()
		return 2
	}
	if *intake == "" || *out == "" {
		fmt.Fprintln(os.Stderr, "score requires --intake and --out")
		removeOut()
		return 2
	}
	// Contract: failure text must not disclose filesystem paths, model filenames, or the bearer token.
	fail := func(msg string) int {
		fmt.Fprintln(os.Stderr, msg)
		removeOut()
		return 1
	}
	kit, _, err := loadKit(*config)
	if err != nil {
		return fail("score: cannot load config")
	}
	apps, err := readIntake(kit, *intake)
	if err != nil {
		return fail("score: cannot read intake")
	}
	rows := kit.ScoreSequence(apps)
	if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil && filepath.Dir(*out) != "." {
		return fail("score: cannot prepare output")
	}
	tmp := *out + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return fail("score: cannot write output")
	}
	enc := json.NewEncoder(f)
	enc.SetEscapeHTML(false)
	for _, row := range rows {
		if err := enc.Encode(row); err != nil {
			f.Close()
			_ = os.Remove(tmp)
			return fail("score: cannot write output")
		}
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return fail("score: cannot write output")
	}
	if err := os.Rename(tmp, *out); err != nil {
		_ = os.Remove(tmp)
		return fail("score: cannot write output")
	}
	fmt.Printf("score %d rows\n", len(rows))
	return 0
}

func runServe() int {
	args := os.Args[1:]
	if illegalSingleDash(args) {
		fmt.Fprintln(os.Stderr, "serve: single-hyphen flags are illegal")
		return 2
	}
	if hasBareEndOfFlags(args) {
		fmt.Fprintln(os.Stderr, "serve: bare end-of-flags marker is illegal")
		return 2
	}
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.Usage = func() {}
	config := fs.String("config", defaultConfig(), "sidecar kv")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "serve: unexpected arguments")
		return 2
	}
	kit, cfg, err := loadKit(*config)
	if err != nil {
		fmt.Fprintln(os.Stderr, "serve: cannot load config")
		return 1
	}
	// Serve the handler directly — do not wrap in ServeMux, which redirects
	// cleaned unknown paths (e.g. /v1//score → 301) instead of NOT_FOUND.
	srv := &httpapi.Server{Kit: kit}
	fmt.Printf("serve %s\n", cfg.Listen)
	if err := http.ListenAndServe(cfg.Listen, srv); err != nil {
		fmt.Fprintln(os.Stderr, "serve: listen failed")
		return 1
	}
	return 0
}

func runBench() int {
	args := os.Args[1:]
	if illegalSingleDash(args) {
		fmt.Fprintln(os.Stderr, "bench: single-hyphen flags are illegal")
		return 2
	}
	if hasBareEndOfFlags(args) {
		fmt.Fprintln(os.Stderr, "bench: bare end-of-flags marker is illegal")
		return 2
	}
	fs := flag.NewFlagSet("bench", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.Usage = func() {}
	config := fs.String("config", defaultConfig(), "sidecar kv")
	spec := fs.String("spec", "bench/loadspec.txt", "tick loadspec")
	out := fs.String("out", "out/bench-ledger.json", "ledger")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "bench: unexpected arguments")
		return 2
	}
	kit, cfg, err := loadKit(*config)
	if err != nil {
		fmt.Fprintln(os.Stderr, "bench: cannot load config")
		return 1
	}
	if err := os.MkdirAll(parentDir(*out), 0o755); err != nil {
		fmt.Fprintln(os.Stderr, "bench: cannot prepare output")
		return 1
	}
	if err := tickbench.Run(*spec, *out, kit, cfg); err != nil {
		fmt.Fprintln(os.Stderr, "bench: run failed")
		return 1
	}
	fmt.Printf("bench wrote %s\n", *out)
	return 0
}

func parentDir(path string) string {
	for i := len(path) - 1; i >= 0; i-- {
		if path[i] == '/' {
			if i == 0 {
				return "/"
			}
			return path[:i]
		}
	}
	return "."
}

func readIntake(kit *scorekit.Kit, path string) ([]scorekit.Application, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var apps []scorekit.Application
	seen := map[string]struct{}{}
	sc := bufio.NewScanner(f)
	// Keep CR in the token: contract length excludes only the terminating LF, so a
	// CRLF record whose CR+payload is 1048577 bytes must be rejected.
	sc.Split(func(data []byte, atEOF bool) (advance int, token []byte, err error) {
		if atEOF && len(data) == 0 {
			return 0, nil, nil
		}
		if i := bytes.IndexByte(data, '\n'); i >= 0 {
			return i + 1, data[0:i], nil
		}
		if atEOF {
			return len(data), data, nil
		}
		return 0, nil, nil
	})
	// Content may be 1048576 bytes; leave room for the terminating newline byte.
	sc.Buffer(make([]byte, 0, 64*1024), 1048576+2)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) > 1048576 {
			return nil, fmt.Errorf("intake line too long")
		}
		if len(bytes.TrimSpace(line)) == 0 {
			return nil, fmt.Errorf("blank intake line")
		}
		app, err := kit.Parse(line)
		if err != nil {
			return nil, err
		}
		if _, dup := seen[app.ID]; dup {
			return nil, fmt.Errorf("duplicate id")
		}
		seen[app.ID] = struct{}{}
		apps = append(apps, app)
	}
	return apps, sc.Err()
}
