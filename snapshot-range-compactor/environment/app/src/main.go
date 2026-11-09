package main

import (
	"encoding/json"
	"fmt"
	"os"
)

type Point struct {
	Key   string `json:"key"`
	Seq   uint64 `json:"seq"`
	Kind  string `json:"kind"`
	Value *int64 `json:"value,omitempty"`
}
type Range struct {
	Start string `json:"start"`
	End   string `json:"end"`
	Seq   uint64 `json:"seq"`
}
type Span struct {
	Start string `json:"start"`
	End   string `json:"end"`
}
type Table struct {
	Points []Point `json:"points"`
	Ranges []Range `json:"ranges"`
}
type Request struct {
	Latest    uint64   `json:"latest"`
	Snapshots []uint64 `json:"snapshots"`
	Tables    []Table  `json:"tables"`
	Lower     []Span   `json:"lower"`
	Cutoff    uint64   `json:"cutoff"`
	Release   uint64   `json:"release"`
	Lift      uint64   `json:"lift"`
}
type Fragment struct {
	Start string   `json:"start"`
	End   string   `json:"end"`
	Seqs  []uint64 `json:"seqs"`
}
type Result struct {
	Points []Point    `json:"points"`
	Ranges []Fragment `json:"ranges"`
}

func main() {
	var request Request
	if err := json.NewDecoder(os.Stdin).Decode(&request); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := json.NewEncoder(os.Stdout).Encode(Compact(request)); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
