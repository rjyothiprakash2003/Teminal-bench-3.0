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

type anchorFile struct {
	TotalRequests   int64 `json:"total_requests"`
	TotalTicks      int64 `json:"total_ticks"`
	AvgLatencyTicks int64 `json:"avg_latency_ticks"`
	P95Ticks        int64 `json:"p95_ticks"`
	P99Ticks        int64 `json:"p99_ticks"`
	MemoryUnits     int64 `json:"memory_units"`
}

type tickRun struct {
	cost  *big.Int
	count *big.Int
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
	n, total, ok := sumRunsBig(runs)
	if !ok || n.Sign() == 0 {
		return fmt.Errorf("empty loadspec")
	}
	mem := new(big.Int).Add(big.NewInt(64), new(big.Int).Mul(big.NewInt(4), big.NewInt(maxB)))
	fields := map[string]*big.Int{
		"total_requests":    n,
		"total_ticks":       total,
		"throughput":        roundMulDivBig(big.NewInt(1000000), n, total),
		"avg_latency_ticks": roundDivBig(total, n),
		"p95_ticks":         percentileRunsBig(runs, n, 19, 20),
		"p99_ticks":         percentileRunsBig(runs, n, 99, 100),
		"cpu_units":         new(big.Int).Set(total),
		"memory_units":      mem,
	}
	return writeLedgerBig(outPath, fields)
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

func roundDivBig(num, den *big.Int) *big.Int {
	if den.Sign() <= 0 {
		return big.NewInt(0)
	}
	n := new(big.Int).Lsh(new(big.Int).Set(num), 1)
	n.Add(n, den)
	d := new(big.Int).Lsh(new(big.Int).Set(den), 1)
	return n.Div(n, d)
}

func roundMulDivBig(a, b, den *big.Int) *big.Int {
	if den.Sign() <= 0 {
		return big.NewInt(0)
	}
	n := new(big.Int).Mul(a, b)
	n.Lsh(n, 1)
	n.Add(n, den)
	d := new(big.Int).Lsh(new(big.Int).Set(den), 1)
	return n.Div(n, d)
}

func sumRunsBig(runs []tickRun) (n, total *big.Int, ok bool) {
	n, total = big.NewInt(0), big.NewInt(0)
	for _, r := range runs {
		if r.count == nil || r.count.Sign() <= 0 || r.cost == nil {
			continue
		}
		add := new(big.Int).Mul(r.cost, r.count)
		total.Add(total, add)
		n.Add(n, r.count)
	}
	return n, total, n.Sign() > 0
}

func percentileRunsBig(runs []tickRun, n *big.Int, numer, denom int64) *big.Int {
	if n.Sign() <= 0 {
		return big.NewInt(0)
	}
	type bucket struct {
		cost  *big.Int
		count *big.Int
	}
	var buckets []bucket
	for _, r := range runs {
		if r.count == nil || r.count.Sign() <= 0 || r.cost == nil {
			continue
		}
		merged := false
		for i := range buckets {
			if buckets[i].cost.Cmp(r.cost) == 0 {
				buckets[i].count.Add(buckets[i].count, r.count)
				merged = true
				break
			}
		}
		if !merged {
			buckets = append(buckets, bucket{
				cost:  new(big.Int).Set(r.cost),
				count: new(big.Int).Set(r.count),
			})
		}
	}
	if len(buckets) == 0 {
		return big.NewInt(0)
	}
	sort.Slice(buckets, func(i, j int) bool {
		return buckets[i].cost.Cmp(buckets[j].cost) < 0
	})
	idx := new(big.Int).Mul(n, big.NewInt(numer))
	idx.Add(idx, big.NewInt(denom-1))
	idx.Div(idx, big.NewInt(denom))
	idx.Sub(idx, big.NewInt(1))
	if idx.Sign() < 0 {
		idx = big.NewInt(0)
	}
	last := new(big.Int).Sub(n, big.NewInt(1))
	if idx.Cmp(last) > 0 {
		idx = last
	}
	seen := big.NewInt(0)
	for _, b := range buckets {
		seen.Add(seen, b.count)
		if idx.Cmp(seen) < 0 {
			return new(big.Int).Set(b.cost)
		}
	}
	return new(big.Int).Set(buckets[len(buckets)-1].cost)
}

func sumRuns(runs []tickRun) (n, total int64, ok bool) {
	for _, r := range runs {
		if r.count == nil || r.count.Sign() <= 0 {
			continue
		}
		if r.cost == nil || !r.cost.IsInt64() || !r.count.IsInt64() {
			return 0, 0, false
		}
		cost := r.cost.Int64()
		cnt := r.count.Int64()
		if cost > 0 && cnt > math.MaxInt64/cost {
			return 0, 0, false
		}
		add := cost * cnt
		if total > math.MaxInt64-add {
			return 0, 0, false
		}
		if n > math.MaxInt64-cnt {
			return 0, 0, false
		}
		total += add
		n += cnt
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
		if r.count == nil || r.count.Sign() <= 0 {
			continue
		}
		if r.cost == nil || !r.cost.IsInt64() || !r.count.IsInt64() {
			return 0
		}
		cost := r.cost.Int64()
		cnt := r.count.Int64()
		if _, ok := byCost[cost]; !ok {
			costs = append(costs, cost)
		}
		byCost[cost] += cnt
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

func specCountInt64(sp scenarioSpec) (int64, bool) {
	if sp.count == nil || sp.count.Sign() <= 0 || !sp.count.IsInt64() {
		return 0, false
	}
	return sp.count.Int64(), true
}

func jsonNumber(n json.Number) (*big.Int, error) {
	z, ok := new(big.Int).SetString(string(n), 10)
	if !ok {
		return nil, fmt.Errorf("anchor number")
	}
	return z, nil
}

func recoverBigPair(num map[string]json.Number, specs []scenarioSpec) (*big.Int, *big.Int, error) {
	nReq, err := jsonNumber(num["total_requests"])
	if err != nil {
		return nil, nil, err
	}
	total, err := jsonNumber(num["total_ticks"])
	if err != nil {
		return nil, nil, err
	}
	avg, err := jsonNumber(num["avg_latency_ticks"])
	if err != nil {
		return nil, nil, err
	}
	p95, err := jsonNumber(num["p95_ticks"])
	if err != nil {
		return nil, nil, err
	}
	p99, err := jsonNumber(num["p99_ticks"])
	if err != nil {
		return nil, nil, err
	}
	ns, nc := big.NewInt(0), big.NewInt(0)
	for _, sp := range specs {
		if sp.count == nil {
			continue
		}
		if sp.batch == 1 {
			ns.Add(ns, sp.count)
		} else {
			nc.Add(nc, sp.count)
		}
	}
	if ns.Sign() <= 0 && nc.Sign() <= 0 {
		return nil, nil, fmt.Errorf("unique S/C not recovered from anchor")
	}
	two := big.NewInt(2)
	one := big.NewInt(1)
	try := func(s, c *big.Int) bool {
		if s.Cmp(two) < 0 || c.Cmp(one) < 0 || c.Cmp(s) >= 0 {
			return false
		}
		runs, ok := runsFromSpecs(specs, s, c)
		if !ok {
			return false
		}
		n, tot, ok := sumRunsBig(runs)
		if !ok {
			return false
		}
		if n.Cmp(nReq) != 0 || tot.Cmp(total) != 0 {
			return false
		}
		if roundDivBig(tot, n).Cmp(avg) != 0 {
			return false
		}
		return percentileRunsBig(runs, n, 19, 20).Cmp(p95) == 0 && percentileRunsBig(runs, n, 99, 100).Cmp(p99) == 0
	}
	type pair struct{ s, c *big.Int }
	cand := map[string]pair{}
	// Singleton-only recovery: total fixes S; when S==2 the unique C with 0<C<S is 1.
	if ns.Sign() > 0 && nc.Sign() == 0 {
		sBig, mod := new(big.Int).DivMod(new(big.Int).Set(total), ns, new(big.Int))
		if mod.Sign() == 0 && sBig.Cmp(two) == 0 {
			cOne := big.NewInt(1)
			if try(sBig, cOne) {
				return new(big.Int).Set(sBig), cOne, nil
			}
		}
		return nil, nil, fmt.Errorf("unique S/C not recovered from anchor")
	}
	if ns.Sign() <= 0 || nc.Sign() <= 0 {
		return nil, nil, fmt.Errorf("unique S/C not recovered from anchor")
	}
	vals := make([]*big.Int, 0, 2)
	seenV := map[string]struct{}{}
	for _, z := range []*big.Int{p95, p99} {
		if z == nil || z.Sign() <= 0 {
			continue
		}
		k := z.String()
		if _, ok := seenV[k]; ok {
			continue
		}
		seenV[k] = struct{}{}
		vals = append(vals, new(big.Int).Set(z))
	}
	// Percentile value is S: solve C from total = ns*S + nc*C.
	for _, s := range vals {
		rem := new(big.Int).Sub(total, new(big.Int).Mul(ns, s))
		if rem.Sign() <= 0 {
			continue
		}
		cBig, mod := new(big.Int).DivMod(new(big.Int).Set(rem), nc, new(big.Int))
		if mod.Sign() != 0 {
			continue
		}
		if try(s, cBig) {
			key := s.String() + "|" + cBig.String()
			cand[key] = pair{s: new(big.Int).Set(s), c: new(big.Int).Set(cBig)}
		}
	}
	// Percentile value is C (including both percentiles equal C): solve S.
	for _, c := range vals {
		rem := new(big.Int).Sub(total, new(big.Int).Mul(nc, c))
		if rem.Sign() <= 0 {
			continue
		}
		sBig, mod := new(big.Int).DivMod(new(big.Int).Set(rem), ns, new(big.Int))
		if mod.Sign() != 0 {
			continue
		}
		if try(sBig, c) {
			key := sBig.String() + "|" + c.String()
			cand[key] = pair{s: new(big.Int).Set(sBig), c: new(big.Int).Set(c)}
		}
	}
	if len(cand) != 1 {
		return nil, nil, fmt.Errorf("unique S/C not recovered from anchor")
	}
	for _, p := range cand {
		return p.s, p.c, nil
	}
	return nil, nil, fmt.Errorf("unique S/C not recovered from anchor")
}

func numericAnchor(raw []byte) (map[string]json.Number, error) {
	var blob map[string]json.RawMessage
	if err := json.Unmarshal(raw, &blob); err != nil {
		return nil, err
	}
	num := make(map[string]json.Number, len(blob))
	for k, v := range blob {
		var n json.Number
		if err := json.Unmarshal(v, &n); err == nil {
			num[k] = n
		}
	}
	return num, nil
}

func costsFromAnchor(anchorPath, deliveredSpec string) (*big.Int, *big.Int, error) {
	raw, err := os.ReadFile(anchorPath)
	if err != nil {
		return nil, nil, err
	}
	num, err := numericAnchor(raw)
	if err != nil {
		return nil, nil, err
	}
	specs, err := parseScenarioSpecs(deliveredSpec)
	if err != nil {
		return nil, nil, err
	}
	needBig := false
	if total, err := jsonNumber(num["total_ticks"]); err == nil && (total.BitLen() > 63 || !total.IsInt64()) {
		needBig = true
	}
	for _, k := range []string{"p95_ticks", "p99_ticks", "avg_latency_ticks", "total_requests"} {
		if z, err := jsonNumber(num[k]); err == nil && (z.BitLen() > 63 || !z.IsInt64()) {
			needBig = true
			break
		}
	}
	if needBig {
		return recoverBigPair(num, specs)
	}
	var anc anchorFile
	if err := json.Unmarshal(raw, &anc); err != nil {
		return nil, nil, err
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
			cnt, ok := specCountInt64(sp)
			if !ok {
				continue
			}
			cost := s
			if sp.batch > 1 {
				cost = c
			}
			if cost > 0 && cnt > math.MaxInt64/cost {
				continue
			}
			if sp.batch == 1 {
				ns += cnt
			} else {
				nc += cnt
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
			cnt, ok := specCountInt64(sp)
			if !ok {
				continue
			}
			if v > 0 && cnt > math.MaxInt64/v {
				continue
			}
			nsFixed += cnt
		}
		if nsFixed > 0 && v > 0 && nsFixed <= math.MaxInt64/v && anc.TotalTicks > nsFixed*v {
			rem := anc.TotalTicks - nsFixed*v
			tryCosts := map[int64]struct{}{1: {}, 2: {}}
			for _, sp := range specs {
				cnt, ok := specCountInt64(sp)
				if !ok || sp.batch == 1 || cnt <= 0 {
					continue
				}
				t := math.MaxInt64 / cnt
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
					cnt, ok := specCountInt64(sp)
					if !ok || cnt > math.MaxInt64/cTry {
						continue
					}
					nc += cnt
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
					cnt, ok := specCountInt64(sp)
					if !ok || cnt > math.MaxInt64/c {
						continue
					}
					ncChk += cnt
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
				cnt, ok := specCountInt64(sp)
				if !ok || sp.batch == 1 || cnt <= 0 {
					continue
				}
				hasCoal = true
				t := math.MaxInt64 / cnt
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
					runs, ok := runsFromSpecs(specs, big.NewInt(v), big.NewInt(c))
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
			cnt, ok := specCountInt64(sp)
			if !ok {
				continue
			}
			if v > 0 && cnt > math.MaxInt64/v {
				continue
			}
			ncFixed += cnt
		}
		if ncFixed > 0 && v > 0 && ncFixed <= math.MaxInt64/v && anc.TotalTicks > ncFixed*v {
			rem := anc.TotalTicks - ncFixed*v
			tryCosts := map[int64]struct{}{1: {}, 2: {}}
			for _, sp := range specs {
				cnt, ok := specCountInt64(sp)
				if !ok || sp.batch != 1 || cnt <= 0 {
					continue
				}
				t := math.MaxInt64 / cnt
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
					cnt, ok := specCountInt64(sp)
					if !ok || cnt > math.MaxInt64/sTry {
						continue
					}
					ns += cnt
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
					cnt, ok := specCountInt64(sp)
					if !ok || cnt > math.MaxInt64/s {
						continue
					}
					nsChk += cnt
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
		runs, ok := runsFromSpecs(specs, big.NewInt(p.s), big.NewInt(p.c))
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
		return nil, nil, fmt.Errorf("unique S/C not recovered from anchor")
	}
	return big.NewInt(foundS), big.NewInt(foundC), nil
}

type scenarioSpec struct {
	count *big.Int
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
					r := new(big.Int)
					if _, ok := r.SetString(parts[3], 10); !ok || r.Sign() < 1 {
						continue
					}
					b, e2 := strconv.ParseInt(parts[5], 10, 64)
					if e2 == nil && b >= 1 {
						cnt := new(big.Int).Mul(r, big.NewInt(b))
						specs = append(specs, scenarioSpec{count: cnt, batch: b})
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

func runsFromSpecs(specs []scenarioSpec, singleton, coalesced *big.Int) ([]tickRun, bool) {
	runs := make([]tickRun, 0, len(specs))
	for _, sp := range specs {
		cost := singleton
		if sp.batch > 1 {
			cost = coalesced
		}
		if sp.count == nil || cost == nil {
			continue
		}
		runs = append(runs, tickRun{cost: new(big.Int).Set(cost), count: new(big.Int).Set(sp.count)})
	}
	return runs, true
}

func expandRuns(path string, singleton, coalesced *big.Int) ([]tickRun, int64, error) {
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
		if sp.count == nil || cost == nil {
			continue
		}
		runs = append(runs, tickRun{cost: new(big.Int).Set(cost), count: new(big.Int).Set(sp.count)})
		if sp.batch > maxB {
			maxB = sp.batch
		}
	}
	return runs, maxB, nil
}

func writeLedgerBig(path string, fields map[string]*big.Int) error {
	order := []string{
		"total_requests", "total_ticks", "throughput", "avg_latency_ticks",
		"p95_ticks", "p99_ticks", "cpu_units", "memory_units",
	}
	var b strings.Builder
	b.WriteString("{\n")
	for i, k := range order {
		fmt.Fprintf(&b, "  %q: %s", k, fields[k].String())
		if i+1 < len(order) {
			b.WriteString(",\n")
		} else {
			b.WriteString("\n")
		}
	}
	b.WriteString("}\n")
	// Remove first so a prior root-owned ledger cannot block a non-root rewrite
	// that only has directory write permission on out/.
	_ = os.Remove(path)
	return os.WriteFile(path, []byte(b.String()), 0o644)
}
