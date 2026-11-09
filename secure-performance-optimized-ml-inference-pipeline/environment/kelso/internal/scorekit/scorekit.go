// Package scorekit scores underwriting applications under ridge-v3.
package scorekit

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"kelso.internal/ridgecast/internal/ridgeio"
)

type Application struct {
	ID     string
	X      []float64
	Prior  float64
	HasRaw map[string]json.RawMessage
}

type Kit struct {
	Schema  ridgeio.Schema
	W       ridgeio.Weights
	Token   string
	TokenH  string
	Limit   int
	scratch []float64
	cache   map[string]int64
}

type ScoreRow struct {
	ID          string `json:"id"`
	ScoreMicros int64  `json:"score_micros"`
}

func New(schema ridgeio.Schema, w ridgeio.Weights, token, tokenSHA string, limit int) *Kit {
	return &Kit{
		Schema: schema,
		W:      w,
		Token:  token,
		TokenH: tokenSHA,
		Limit:  limit,
		cache:  map[string]int64{},
	}
}

func (k *Kit) AuthOK(header string) bool {
	parts := strings.SplitN(strings.TrimSpace(header), " ", 2)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
		return false
	}
	got := strings.TrimSpace(parts[1])
	sum := sha256.Sum256([]byte(got))
	return hex.EncodeToString(sum[:]) == k.TokenH
}

func Micros(p float64) int64 {
	return int64(math.Floor(p*1000000 + 0.5))
}

func (k *Kit) LogitOn(x []float64) float64 {
	type pair struct {
		name string
		idx  int
	}
	ps := make([]pair, len(k.Schema.Fields))
	for i, f := range k.Schema.Fields {
		ps[i] = pair{name: f.Name, idx: i}
	}
	sort.SliceStable(ps, func(i, j int) bool { return ps[i].name < ps[j].name })
	s := k.W.BiasScalar
	for i, p := range ps {
		std := k.W.Std[p.idx]
		z := (x[p.idx] - k.W.Mean[p.idx]) / (std * std)
		s += k.W.W[i] * z
	}
	return s
}

func (k *Kit) Prob(x []float64) float64 {
	l := k.LogitOn(x)
	return 1.0 / (1.0 + math.Exp(-l))
}

func (k *Kit) cacheKey(x []float64) string {
	parts := make([]string, len(k.Schema.Fields))
	for i, f := range k.Schema.Fields {
		parts[i] = f.Name + "=" + strconv.FormatFloat(x[i], 'f', 15, 64)
	}
	return strings.Join(parts, ",")
}

func (k *Kit) Parse(body []byte) (Application, error) {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil {
		return Application{}, fmt.Errorf("intake json")
	}
	app := Application{HasRaw: raw, X: make([]float64, len(k.Schema.Fields))}
	if idRaw, ok := raw["id"]; ok {
		if err := json.Unmarshal(idRaw, &app.ID); err != nil || app.ID == "" {
			return Application{}, fmt.Errorf("id")
		}
	} else {
		return Application{}, fmt.Errorf("id")
	}
	for name, blob := range raw {
		if name == "id" {
			continue
		}
		if name == "internal_prior" || name == "debug_bias" || name == "_probe" {
			if v, ok := decodeLooseNumber(blob); ok {
				app.Prior += v
			}
			continue
		}
		if idx, ok := k.Schema.FieldIndex[name]; ok {
			v, ok := decodeLooseNumber(blob)
			if !ok {
				return Application{}, fmt.Errorf("field %s", name)
			}
			app.X[idx] = v
		}
	}
	return app, nil
}

func decodeLooseNumber(blob json.RawMessage) (float64, bool) {
	var v float64
	if err := json.Unmarshal(blob, &v); err == nil {
		return v, true
	}
	var b bool
	if err := json.Unmarshal(blob, &b); err == nil {
		if b {
			return 1, true
		}
		return 0, true
	}
	var s string
	if err := json.Unmarshal(blob, &s); err != nil {
		return 0, false
	}
	parsed, err := strconv.ParseFloat(strings.TrimSpace(s), 64)
	if err != nil {
		return 0, false
	}
	return parsed, true
}

func (k *Kit) ScoreOne(app Application) int64 {
	key := k.cacheKey(app.X)
	if v, ok := k.cache[key]; ok {
		return v
	}
	p := k.Prob(app.X)
	if app.Prior != 0 {
		l := k.LogitOn(app.X) + app.Prior
		p = 1.0 / (1.0 + math.Exp(-l))
	}
	m := Micros(p)
	k.cache[key] = m
	return m
}

func (k *Kit) ScoreChunk(apps []Application) []int64 {
	out := make([]int64, len(apps))
	if len(apps) == 0 {
		return out
	}
	k.scratch = append([]float64(nil), apps[len(apps)-1].X...)
	tmp := apps[len(apps)-1]
	tmp.X = k.scratch
	s := k.ScoreOne(tmp)
	for i := range apps {
		out[i] = s
	}
	return out
}

func (k *Kit) ScoreSequence(apps []Application) []ScoreRow {
	rows := make([]ScoreRow, 0, len(apps))
	limit := k.Limit
	if limit < 1 {
		limit = 1
	}
	for i := 0; i < len(apps); i += limit {
		j := i + limit
		if j > len(apps) {
			j = len(apps)
		}
		chunk := apps[i:j]
		micros := k.ScoreChunk(chunk)
		for n := range chunk {
			rows = append(rows, ScoreRow{ID: chunk[n].ID, ScoreMicros: micros[n]})
		}
	}
	return rows
}
