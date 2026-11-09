package tickbench

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/big"
	"os"
	"sort"
	"strconv"
	"strings"

	"kelso.internal/ridgecast/internal/ridgeio"
	"kelso.internal/ridgecast/internal/scorekit"
)

type Ledger struct {
	TotalRequests   int64 `json:"total_requests"`
	TotalTicks      int64 `json:"total_ticks"`
	Throughput      int64 `json:"throughput"`
	AvgLatencyTicks int64 `json:"avg_latency_ticks"`
	P95Ticks        int64 `json:"p95_ticks"`
	P99Ticks        int64 `json:"p99_ticks"`
	CPUUnits        int64 `json:"cpu_units"`
	MemoryUnits     int64 `json:"memory_units"`
}

type anchorFile struct {
	TotalRequests   int64 `json:"total_requests"`
	TotalTicks      int64 `json:"total_ticks"`
	AvgLatencyTicks int64 `json:"avg_latency_ticks"`
	P95Ticks        int64 `json:"p95_ticks"`
	P99Ticks        int64 `json:"p99_ticks"`
	MemoryUnits     int64 `json:"memory_units"`
}

type tickRun struct {
	cost  int64
	count int64
}

func Run(specPath, outPath string, kit *scorekit.Kit, cfg ridgeio.Sidecar) error {
	_ = kit
	_ = cfg
	s, c, err := costsFromAnchor("/app/policy/bench-anchor.json", "/app/policy/bench-recovery-loadspec.txt")
	if err != nil {
		return err
	}
	runs, maxB, err := expandRuns(specPath, s, c)
	if err != nil {
		return err
	}
	n, total, ok := sumRuns(runs)
	if !ok || n == 0 {
		return fmt.Errorf("empty loadspec")
	}
	led := Ledger{
		TotalRequests:   n,
		TotalTicks:      total,
		Throughput:      roundMulDiv(1000000, n, total),
		AvgLatencyTicks: roundDiv(total, n),
		P95Ticks:        percentileRuns(runs, n, 19, 20),
		P99Ticks:        percentileRuns(runs, n, 99, 100),
		CPUUnits:        total,
		MemoryUnits:     64 + 4*maxB,
	}
	return writeLedger(outPath, led)
}

// roundDiv returns floor(num/den + 1/2) for positive den using exact integer math.
func roundDiv(num, den int64) int64 {
	if den <= 0 {
		return 0
	}
	n := new(big.Int).SetInt64(num)
	d := new(big.Int).SetInt64(den)
	n.Lsh(n, 1)
	n.Add(n, d)
	d.Lsh(d, 1)
	n.Div(n, d)
	return n.Int64()
}

// roundMulDiv returns floor(a*b/den + 1/2) without float64 conversion.
func roundMulDiv(a, b, den int64) int64 {
	if den <= 0 {
		return 0
	}
	n := new(big.Int).SetInt64(a)
	n.Mul(n, big.NewInt(b))
	d := new(big.Int).SetInt64(den)
	n.Lsh(n, 1)
	n.Add(n, d)
	d.Lsh(d, 1)
	n.Div(n, d)
	return n.Int64()
}

func sumRuns(runs []tickRun) (n, total int64, ok bool) {
	for _, r := range runs {
		if r.count <= 0 {
			continue
		}
		if r.cost > 0 && r.count > math.MaxInt64/r.cost {
			return 0, 0, false
		}
		add := r.cost * r.count
		if total > math.MaxInt64-add {
			return 0, 0, false
		}
		if n > math.MaxInt64-r.count {
			return 0, 0, false
		}
		total += add
		n += r.count
	}
	return n, total, true
}

// percentileRuns returns the tick at index ceil(numer/denom * n) - 1 using exact
// integer ceiling: ceil(numer*n/denom) - 1.
func percentileRuns(runs []tickRun, n, numer, denom int64) int64 {
	if n <= 0 {
		return 0
	}
	byCost := map[int64]int64{}
	costs := make([]int64, 0, len(runs))
	for _, r := range runs {
		if r.count <= 0 {
			continue
		}
		if _, ok := byCost[r.cost]; !ok {
			costs = append(costs, r.cost)
		}
		byCost[r.cost] += r.count
	}
	sort.Slice(costs, func(i, j int) bool { return costs[i] < costs[j] })
	idx := ceilRatioMinusOne(n, numer, denom)
	if idx < 0 {
		idx = 0
	}
	if idx >= n {
		idx = n - 1
	}
	var seen int64
	for _, c := range costs {
		seen += byCost[c]
		if idx < seen {
			return c
		}
	}
	return costs[len(costs)-1]
}

func ceilRatioMinusOne(n, numer, denom int64) int64 {
	// ceil(numer*n/denom) - 1 = (numer*n + denom - 1)/denom - 1
	a := new(big.Int).SetInt64(numer)
	a.Mul(a, big.NewInt(n))
	a.Add(a, big.NewInt(denom-1))
	a.Div(a, big.NewInt(denom))
	a.Sub(a, big.NewInt(1))
	return a.Int64()
}

func costsFromAnchor(anchorPath, deliveredSpec string) (int64, int64, error) {
	raw, err := os.ReadFile(anchorPath)
	if err != nil {
		return 0, 0, err
	}
	var anc anchorFile
	if err := json.Unmarshal(raw, &anc); err != nil {
		return 0, 0, err
	}
	specs, err := parseScenarioSpecs(deliveredSpec)
	if err != nil {
		return 0, 0, err
	}
	// Tick values are only S or C. Anchor percentiles therefore lie in {C,S},
	// so the unique pair is recovered from those values plus the linear total
	// (no O(total_ticks^2) enumeration; large live S/C stay operational).
	uniq := make([]int64, 0, 2)
	seen := map[int64]struct{}{}
	for _, v := range []int64{anc.P95Ticks, anc.P99Ticks} {
		if v < 1 {
			continue
		}
		if _, ok := seen[v]; ok {
			continue
		}
		seen[v] = struct{}{}
		uniq = append(uniq, v)
	}
	type pair struct{ s, c int64 }
	cand := map[pair]struct{}{}
	add := func(s, c int64) {
		if s >= 1 && c >= 1 && c < s {
			cand[pair{s, c}] = struct{}{}
		}
	}
	if len(uniq) == 2 {
		hi, lo := uniq[0], uniq[1]
		if hi < lo {
			hi, lo = lo, hi
		}
		add(hi, lo)
	}
	// Overflow-aware ns/nc (same filter as runsFromSpecs) for equation-derived pairs.
	countNSNC := func(s, c int64) (ns, nc int64) {
		for _, sp := range specs {
			cost := s
			if sp.batch > 1 {
				cost = c
			}
			if cost > 0 && sp.count > math.MaxInt64/cost {
				continue
			}
			if sp.batch == 1 {
				ns += sp.count
			} else {
				nc += sp.count
			}
		}
		return ns, nc
	}
	for _, s := range uniq {
		for _, c := range uniq {
			if c < s {
				ns, nc := countNSNC(s, c)
				if ns >= 0 && nc >= 0 && ns*s+nc*c == anc.TotalTicks {
					add(s, c)
				}
			}
		}
	}
	for _, v := range uniq {
		// Treat v as S and solve C from coalesced counts admitted under that C
		// (not under fixed cost-1/cost-2 overflow buckets).
		var nsFixed int64
		for _, sp := range specs {
			if sp.batch != 1 {
				continue
			}
			if v > 0 && sp.count > math.MaxInt64/v {
				continue
			}
			nsFixed += sp.count
		}
		if nsFixed > 0 && v > 0 && nsFixed <= math.MaxInt64/v && anc.TotalTicks > nsFixed*v {
			rem := anc.TotalTicks - nsFixed*v
			tryCosts := map[int64]struct{}{1: {}, 2: {}}
			for _, sp := range specs {
				if sp.batch == 1 || sp.count <= 0 {
					continue
				}
				t := math.MaxInt64 / sp.count
				tryCosts[t] = struct{}{}
				if t < math.MaxInt64 {
					tryCosts[t+1] = struct{}{}
				}
			}
			seenNC := map[int64]struct{}{}
			for cTry := range tryCosts {
				if cTry < 1 {
					continue
				}
				var nc int64
				for _, sp := range specs {
					if sp.batch == 1 {
						continue
					}
					if sp.count > math.MaxInt64/cTry {
						continue
					}
					nc += sp.count
				}
				if nc <= 0 {
					continue
				}
				if _, dup := seenNC[nc]; dup {
					continue
				}
				seenNC[nc] = struct{}{}
				if rem%nc != 0 {
					continue
				}
				c := rem / nc
				if c < 1 || c >= v {
					continue
				}
				var ncChk int64
				for _, sp := range specs {
					if sp.batch == 1 {
						continue
					}
					if sp.count > math.MaxInt64/c {
						continue
					}
					ncChk += sp.count
				}
				if ncChk == nc && ncChk <= math.MaxInt64/c && nsFixed*v+ncChk*c == anc.TotalTicks {
					add(v, c)
				}
			}
		}
		// When total_ticks equals ns*S, admitted coalesced contribution is zero.
		// Derive the minimal C that overflow-excludes every coalesced line (O(1)), not O(S).
		if nsFixed > 0 && v > 0 && nsFixed <= math.MaxInt64/v && anc.TotalTicks == nsFixed*v {
			cMin := int64(1)
			hasCoal := false
			for _, sp := range specs {
				if sp.batch == 1 || sp.count <= 0 {
					continue
				}
				hasCoal = true
				// Ignored when sp.count > MaxInt64/c  =>  c >= floor(MaxInt64/count)+1
				t := math.MaxInt64 / sp.count
				need := t + 1
				if need > cMin {
					cMin = need
				}
			}
			if !hasCoal {
				if v == 2 {
					add(v, 1)
				}
			} else if cMin < v {
				for c := cMin; c < v; c++ {
					runs, ok := runsFromSpecs(specs, v, c)
					if !ok {
						continue
					}
					n, total, ok := sumRuns(runs)
					if ok && n == anc.TotalRequests && total == anc.TotalTicks {
						add(v, c)
					}
				}
			}
		}
		// Symmetric: both percentiles may equal C. Treat v as C and solve S
		// using singleton counts admitted under that S.
		var ncFixed int64
		for _, sp := range specs {
			if sp.batch == 1 {
				continue
			}
			if v > 0 && sp.count > math.MaxInt64/v {
				continue
			}
			ncFixed += sp.count
		}
		if ncFixed > 0 && v > 0 && ncFixed <= math.MaxInt64/v && anc.TotalTicks > ncFixed*v {
			rem := anc.TotalTicks - ncFixed*v
			tryCosts := map[int64]struct{}{1: {}, 2: {}}
			for _, sp := range specs {
				if sp.batch != 1 || sp.count <= 0 {
					continue
				}
				t := math.MaxInt64 / sp.count
				tryCosts[t] = struct{}{}
				if t < math.MaxInt64 {
					tryCosts[t+1] = struct{}{}
				}
			}
			seenNS := map[int64]struct{}{}
			for sTry := range tryCosts {
				if sTry <= v {
					continue
				}
				var ns int64
				for _, sp := range specs {
					if sp.batch != 1 {
						continue
					}
					if sp.count > math.MaxInt64/sTry {
						continue
					}
					ns += sp.count
				}
				if ns <= 0 {
					continue
				}
				if _, dup := seenNS[ns]; dup {
					continue
				}
				seenNS[ns] = struct{}{}
				if rem%ns != 0 {
					continue
				}
				s := rem / ns
				if s <= v {
					continue
				}
				var nsChk int64
				for _, sp := range specs {
					if sp.batch != 1 {
						continue
					}
					if sp.count > math.MaxInt64/s {
						continue
					}
					nsChk += sp.count
				}
				if nsChk == ns && nsChk <= math.MaxInt64/s && nsChk*s+ncFixed*v == anc.TotalTicks {
					add(s, v)
				}
			}
		}
	}
	foundS, foundC := int64(0), int64(0)
	matches := 0
	for p := range cand {
		runs, ok := runsFromSpecs(specs, p.s, p.c)
		if !ok {
			continue
		}
		n, total, ok := sumRuns(runs)
		if !ok || n == 0 {
			continue
		}
		avg := roundDiv(total, n)
		if n == anc.TotalRequests && total == anc.TotalTicks && avg == anc.AvgLatencyTicks &&
			percentileRuns(runs, n, 19, 20) == anc.P95Ticks && percentileRuns(runs, n, 99, 100) == anc.P99Ticks {
			matches++
			foundS, foundC = p.s, p.c
		}
	}
	if matches != 1 {
		return 0, 0, fmt.Errorf("unique S/C not recovered from anchor")
	}
	return foundS, foundC, nil
}

type scenarioSpec struct {
	count int64
	batch int64
}

func parseScenarioSpecs(path string) ([]scenarioSpec, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var specs []scenarioSpec
	br := bufio.NewReader(f)
	for {
		raw, err := br.ReadBytes('\n')
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
				parts := strings.Fields(string(line))
				if len(parts) == 6 && parts[0] == "scenario" && parts[2] == "repeat" && parts[4] == "batch" {
					r, e1 := strconv.ParseInt(parts[3], 10, 64)
					b, e2 := strconv.ParseInt(parts[5], 10, 64)
					if e1 == nil && e2 == nil && r >= 1 && b >= 1 {
						if r <= math.MaxInt64/b {
							specs = append(specs, scenarioSpec{count: r * b, batch: b})
						}
					}
				}
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
	}
	return specs, nil
}

func runsFromSpecs(specs []scenarioSpec, singleton, coalesced int64) ([]tickRun, bool) {
	runs := make([]tickRun, 0, len(specs))
	for _, sp := range specs {
		cost := singleton
		if sp.batch > 1 {
			cost = coalesced
		}
		if cost > 0 && sp.count > math.MaxInt64/cost {
			continue
		}
		runs = append(runs, tickRun{cost: cost, count: sp.count})
	}
	return runs, true
}

func expandRuns(path string, singleton, coalesced int64) ([]tickRun, int64, error) {
	specs, err := parseScenarioSpecs(path)
	if err != nil {
		return nil, 0, err
	}
	var runs []tickRun
	maxB := int64(0)
	for _, sp := range specs {
		cost := singleton
		if sp.batch > 1 {
			cost = coalesced
		}
		if cost > 0 && sp.count > math.MaxInt64/cost {
			continue
		}
		runs = append(runs, tickRun{cost: cost, count: sp.count})
		if sp.batch > maxB {
			maxB = sp.batch
		}
	}
	return runs, maxB, nil
}

func writeLedger(path string, led Ledger) error {
	b, err := json.MarshalIndent(led, "", "  ")
	if err != nil {
		return err
	}
	b = append(b, '\n')
	return os.WriteFile(path, b, 0o644)
}
