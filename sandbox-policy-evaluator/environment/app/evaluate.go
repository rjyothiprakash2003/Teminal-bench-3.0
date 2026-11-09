package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
)

func main() {
	policy := flag.String("policy", "", "")
	book := flag.String("book", "", "")
	ops := flag.String("ops", "", "")
	out := flag.String("out", "", "")
	flag.Parse()
	if *policy == "" || *book == "" || *ops == "" || *out == "" {
		fmt.Fprintln(os.Stderr, "missing required flags")
		os.Exit(1)
	}
	ledger := filepath.Join(*book, "SPE_LEDGER.md")
	samples := filepath.Join(*book, "samples")
	if st, err := os.Lstat(ledger); err != nil || !st.Mode().IsRegular() {
		fmt.Fprintln(os.Stderr, "missing SPE ledger")
		os.Exit(1)
	}
	if st, err := os.Lstat(samples); err != nil || !st.IsDir() {
		fmt.Fprintln(os.Stderr, "missing samples")
		os.Exit(1)
	}
	if err := os.MkdirAll(*out, 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	_ = os.WriteFile(filepath.Join(*out, "verdicts.jsonl"), []byte{}, 0o644)
	_ = os.WriteFile(filepath.Join(*out, "summary.json"), []byte(
		`{"op_count":0,"by_verdict":{"allow":0,"deny":0,"deny_default":0,"reject_malformed":0},"allow_count":0}`+"\n",
	), 0o644)
	fmt.Fprintln(os.Stderr, "evaluate stub: not yet implemented")
}
