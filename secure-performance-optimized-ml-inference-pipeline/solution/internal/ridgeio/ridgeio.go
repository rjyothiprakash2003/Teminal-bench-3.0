package ridgeio

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/big"
	"os"
	"strconv"
	"strings"
)

type FieldSpec struct {
	Name string
	Min  float64
	Max  float64
}

type Schema struct {
	Fields     []FieldSpec
	Forbidden  map[string]struct{}
	FieldIndex map[string]int
}

type Weights struct {
	BiasScalar float64
	W          []float64
	Mean       []float64
	Std        []float64
	Names      []string
}

type Sidecar struct {
	Listen     string
	TokenSHA   string
	BatchLimit int
	CoefPath   string
	ScalePath  string
	SchemaPath string
	TokenFile  string
}

func LoadKV(path string) (Sidecar, error) {
	var s Sidecar
	seenBatch := false
	f, err := os.Open(path)
	if err != nil {
		return s, err
	}
	defer f.Close()
	br := bufio.NewReader(f)
	for {
		raw, rerr := br.ReadBytes('\n')
		if len(raw) > 0 {
			line := raw
			if line[len(line)-1] == '\n' {
				line = line[:len(line)-1]
			}
			if len(line) > 0 && line[len(line)-1] == '\r' {
				line = line[:len(line)-1]
			}
			trim := strings.TrimSpace(string(line))
			if trim != "" && !strings.HasPrefix(trim, "#") {
				k, v, ok := strings.Cut(trim, " ")
				if !ok {
					return s, fmt.Errorf("short kv line")
				}
				v = strings.TrimSpace(v)
				switch k {
				case "listen":
					s.Listen = v
				case "token_sha256":
					s.TokenSHA = v
				case "batch_limit":
					n := new(big.Int)
					if _, ok := n.SetString(v, 10); !ok || n.Sign() < 1 {
						return s, fmt.Errorf("batch_limit must be a positive integer")
					}
					if n.IsInt64() {
						s.BatchLimit = int(n.Int64())
					} else {
						s.BatchLimit = int(math.MaxInt)
					}
					seenBatch = true
				case "coef":
					s.CoefPath = v
				case "scale":
					s.ScalePath = v
				case "schema":
					s.SchemaPath = v
				case "token_file":
					s.TokenFile = v
				default:
					return s, fmt.Errorf("unknown kv key %s", k)
				}
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			return s, rerr
		}
	}
	if s.Listen == "" || s.TokenSHA == "" || s.CoefPath == "" {
		return s, fmt.Errorf("sidecar kv incomplete")
	}
	if !seenBatch || s.BatchLimit < 1 {
		return s, fmt.Errorf("batch_limit must be a positive integer")
	}
	return s, nil
}

func LoadSchema(path string) (Schema, error) {
	var raw struct {
		Fields []struct {
			Name string  `json:"name"`
			Min  float64 `json:"min"`
			Max  float64 `json:"max"`
		} `json:"fields"`
		Forbidden []string `json:"forbidden"`
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return Schema{}, err
	}
	if err := json.Unmarshal(b, &raw); err != nil {
		return Schema{}, err
	}
	out := Schema{
		Forbidden:  map[string]struct{}{},
		FieldIndex: map[string]int{},
	}
	for i, f := range raw.Fields {
		out.Fields = append(out.Fields, FieldSpec{Name: f.Name, Min: f.Min, Max: f.Max})
		out.FieldIndex[f.Name] = i
	}
	for _, name := range raw.Forbidden {
		out.Forbidden[name] = struct{}{}
	}
	if len(out.Fields) == 0 {
		return Schema{}, fmt.Errorf("schema has no fields")
	}
	return out, nil
}

func LoadWeights(coefPath, scalePath string, schema Schema) (Weights, error) {
	w := Weights{
		W:     make([]float64, len(schema.Fields)),
		Mean:  make([]float64, len(schema.Fields)),
		Std:   make([]float64, len(schema.Fields)),
		Names: make([]string, len(schema.Fields)),
	}
	for i, f := range schema.Fields {
		w.Names[i] = f.Name
	}
	if err := readCoef(coefPath, &w, schema); err != nil {
		return w, err
	}
	if err := readScale(scalePath, &w, schema); err != nil {
		return w, err
	}
	for i, s := range w.Std {
		if s == 0 {
			return w, fmt.Errorf("zero std for %s", schema.Fields[i].Name)
		}
	}
	return w, nil
}

func readCoef(path string, w *Weights, schema Schema) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	seenW := 0
	ended := false
	for sc.Scan() {
		parts := strings.Fields(sc.Text())
		if len(parts) == 0 {
			continue
		}
		switch parts[0] {
		case "family":
			if len(parts) != 2 || parts[1] != "ridge-v3" {
				return fmt.Errorf("coef family")
			}
		case "bias":
			if len(parts) != 2 {
				return fmt.Errorf("bias line")
			}
			v, err := strconv.ParseFloat(parts[1], 64)
			if err != nil {
				return err
			}
			w.BiasScalar = v
		case "w":
			if len(parts) != 3 {
				return fmt.Errorf("weight line")
			}
			idx, ok := schema.FieldIndex[parts[1]]
			if !ok {
				return fmt.Errorf("weight for unknown field %s", parts[1])
			}
			v, err := strconv.ParseFloat(parts[2], 64)
			if err != nil {
				return err
			}
			w.W[idx] = v
			seenW++
		case "end":
			ended = true
		default:
			return fmt.Errorf("unknown coef token %s", parts[0])
		}
	}
	if !ended || seenW != len(schema.Fields) {
		return fmt.Errorf("coef incomplete")
	}
	return sc.Err()
}

func readScale(path string, w *Weights, schema Schema) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	ended := false
	for sc.Scan() {
		parts := strings.Fields(sc.Text())
		if len(parts) == 0 {
			continue
		}
		switch parts[0] {
		case "family":
			if len(parts) != 2 || parts[1] != "ridge-v3" {
				return fmt.Errorf("scale family")
			}
		case "mean", "std":
			if len(parts) != 3 {
				return fmt.Errorf("scale line")
			}
			idx, ok := schema.FieldIndex[parts[1]]
			if !ok {
				return fmt.Errorf("scale for unknown field %s", parts[1])
			}
			v, err := strconv.ParseFloat(parts[2], 64)
			if err != nil {
				return err
			}
			if parts[0] == "mean" {
				w.Mean[idx] = v
			} else {
				w.Std[idx] = v
			}
		case "end":
			ended = true
		default:
			return fmt.Errorf("unknown scale token %s", parts[0])
		}
	}
	if !ended {
		return fmt.Errorf("scale has no end")
	}
	return sc.Err()
}

func ReadToken(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(b)), nil
}
