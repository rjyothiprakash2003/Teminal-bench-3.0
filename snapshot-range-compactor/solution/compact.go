package main

import "sort"

// Active sequence numbers, with multiplicity for overlapping equal-sequence
// tombstones. The Fenwick tree indexes presence, not multiplicity.
type rangeIndex struct {
	seqs   []uint64
	counts []int
	tree   []int
}

func (f *rangeIndex) change(seq uint64, delta int) {
	i := sort.Search(len(f.seqs), func(i int) bool { return f.seqs[i] >= seq })
	before := f.counts[i] > 0
	f.counts[i] += delta
	after := f.counts[i] > 0
	if before == after {
		return
	}
	n := -1
	if after {
		n = 1
	}
	for j := i + 1; j < len(f.tree); j += j & -j {
		f.tree[j] += n
	}
}
func (f *rangeIndex) predecessor(cut uint64) uint64 {
	n := sort.Search(len(f.seqs), func(i int) bool { return f.seqs[i] > cut })
	rank := 0
	for j := n; j > 0; j -= j & -j {
		rank += f.tree[j]
	}
	if rank == 0 {
		return 0
	}
	// Locate the rank-th present element, whose sequence is the predecessor.
	i, step := 0, 1
	for step < len(f.tree) {
		step <<= 1
	}
	for ; step > 0; step >>= 1 {
		j := i + step
		if j < len(f.tree) && f.tree[j] < rank {
			rank -= f.tree[j]
			i = j
		}
	}
	return f.seqs[i]
}

type event struct {
	seq   uint64
	delta int
}

func sameStack(a, b []uint64) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
type histEvent struct {
	seq   uint64
	point *Point
}

// Newest covering range at or below cut is the only range event a newest-first
// read can reach: every range terminates, so older covering sequences are dead.
func readAtIndexed(points []Point, maxRangeSeq uint64, cut uint64) (terminates, present bool, value int64) {
	var events []histEvent
	seen := map[uint64]bool{}
	for i := range points {
		p := &points[i]
		if p.Seq <= cut && !seen[p.Seq] {
			seen[p.Seq] = true
			events = append(events, histEvent{p.Seq, p})
		}
	}
	if maxRangeSeq > 0 && maxRangeSeq <= cut {
		events = append(events, histEvent{maxRangeSeq, nil})
	}
	sort.Slice(events, func(i, j int) bool {
		if events[i].seq != events[j].seq {
			return events[i].seq > events[j].seq
		}
		return events[i].point == nil && events[j].point != nil
	})
	var total int64
	merged := false
	for _, e := range events {
		if e.point == nil || e.point.Kind == "delete" {
			return true, merged, total
		}
		if e.point.Kind == "put" {
			return true, true, total + *e.point.Value
		}
		merged = true
		total += *e.point.Value
	}
	return false, merged, total
}

type sweepEdge struct {
	key   string
	seq   uint64
	delta int
}

func maxCoveringByKey(keys []string, ranges []Range, cut uint64) map[string]uint64 {
	var edges []sweepEdge
	for _, r := range ranges {
		if r.Seq <= cut {
			edges = append(edges, sweepEdge{r.Start, r.Seq, 1}, sweepEdge{r.End, r.Seq, -1})
		}
	}
	sort.Slice(edges, func(i, j int) bool {
		if edges[i].key != edges[j].key {
			return edges[i].key < edges[j].key
		}
		return edges[i].delta > edges[j].delta
	})
	ordered := append([]string(nil), keys...)
	sort.Strings(ordered)
	active := map[uint64]int{}
	var current uint64
	recompute := func() {
		current = 0
		for seq, n := range active {
			if n > 0 && seq > current {
				current = seq
			}
		}
	}
	out := make(map[string]uint64, len(ordered))
	ei := 0
	for _, key := range ordered {
		for ei < len(edges) && edges[ei].key <= key {
			e := edges[ei]
			active[e.seq] += e.delta
			if e.delta > 0 {
				if e.seq > current {
					current = e.seq
				}
			} else if e.seq == current && active[e.seq] <= 0 {
				recompute()
			}
			if active[e.seq] <= 0 {
				delete(active, e.seq)
			}
			ei++
		}
		if current > 0 {
			out[key] = current
		}
	}
	return out
}

func lowerCovered(keys []string, lower []Span) map[string]bool {
	var edges []sweepEdge
	for _, span := range lower {
		edges = append(edges, sweepEdge{span.Start, 0, 1}, sweepEdge{span.End, 0, -1})
	}
	sort.Slice(edges, func(i, j int) bool {
		if edges[i].key != edges[j].key {
			return edges[i].key < edges[j].key
		}
		return edges[i].delta > edges[j].delta
	})
	ordered := append([]string(nil), keys...)
	sort.Strings(ordered)
	out := make(map[string]bool, len(ordered))
	count, ei := 0, 0
	for _, key := range ordered {
		for ei < len(edges) && edges[ei].key <= key {
			count += edges[ei].delta
			ei++
		}
		if count > 0 {
			out[key] = true
		}
	}
	return out
}

func applyCutoff(req Request) Request {
	if req.Cutoff == 0 {
		return req
	}
	cutSet := map[uint64]bool{req.Latest: true}
	for _, c := range req.Snapshots {
		cutSet[c] = true
	}
	var floor uint64
	for c := range cutSet {
		if c < req.Cutoff && c > floor {
			floor = c
		}
	}
	byKey := map[string][]Point{}
	var ranges []Range
	for _, table := range req.Tables {
		for _, p := range table.Points {
			byKey[p.Key] = append(byKey[p.Key], p)
		}
		ranges = append(ranges, table.Ranges...)
	}
	keys := make([]string, 0, len(byKey))
	for key := range byKey {
		keys = append(keys, key)
	}
	covering := maxCoveringByKey(keys, ranges, req.Cutoff)
	covered := lowerCovered(keys, req.Lower)
	var kept []Point
	for key, ps := range byKey {
		var outside, window []Point
		for _, p := range ps {
			if floor < p.Seq && p.Seq <= req.Cutoff {
				window = append(window, p)
			} else {
				outside = append(outside, p)
			}
		}
		kept = append(kept, outside...)
		if len(window) == 0 {
			continue
		}
		terminates, present, value := readAtIndexed(ps, covering[key], req.Cutoff)
		if !terminates && covered[key] {
			kept = append(kept, window...)
			continue
		}
		if present {
			v := value
			kept = append(kept, Point{Key: key, Seq: req.Cutoff, Kind: "put", Value: &v})
		} else {
			kept = append(kept, Point{Key: key, Seq: req.Cutoff, Kind: "delete"})
		}
	}
	out := req
	out.Tables = []Table{{Points: kept, Ranges: ranges}}
	return out
}

func overlapsLower(lower []Span, start, end string) bool {
	for _, span := range lower {
		if start < span.End && span.Start < end {
			return true
		}
	}
	return false
}

func applyRelease(req Request) Request {
	if req.Release == 0 {
		return req
	}
	cutSet := map[uint64]bool{req.Latest: true}
	for _, c := range req.Snapshots {
		cutSet[c] = true
	}
	var floor uint64
	for c := range cutSet {
		if c < req.Release && c > floor {
			floor = c
		}
	}
	var kept []Range
	for _, table := range req.Tables {
		for _, r := range table.Ranges {
			if floor < r.Seq && r.Seq <= req.Release && !overlapsLower(req.Lower, r.Start, r.End) {
				continue
			}
			kept = append(kept, r)
		}
	}
	var points []Point
	for _, table := range req.Tables {
		points = append(points, table.Points...)
	}
	out := req
	out.Tables = []Table{{Points: points, Ranges: kept}}
	return out
}

func applyLift(req Request) Request {
	if req.Lift == 0 {
		return req
	}
	cutSet := map[uint64]bool{req.Latest: true}
	for _, c := range req.Snapshots {
		cutSet[c] = true
	}
	var floor uint64
	for c := range cutSet {
		if c < req.Lift && c > floor {
			floor = c
		}
	}
	var points []Point
	for _, table := range req.Tables {
		points = append(points, table.Points...)
	}
	type rangeKey struct {
		Start, End string
		Seq        uint64
	}
	// Deduplicate before the point-win scan so duplicate ranges cannot multiply work.
	unique := map[rangeKey]bool{}
	var ranges []Range
	for _, table := range req.Tables {
		for _, r := range table.Ranges {
			key := rangeKey{r.Start, r.End, r.Seq}
			if unique[key] {
				continue
			}
			unique[key] = true
			ranges = append(ranges, r)
		}
	}
	var maxBlocker uint64
	var blockers []Point
	for _, p := range points {
		if p.Seq <= req.Lift && p.Seq > maxBlocker {
			maxBlocker = p.Seq
		}
		if p.Seq <= req.Lift {
			blockers = append(blockers, p)
		}
	}
	sort.Slice(blockers, func(i, j int) bool { return blockers[i].Key < blockers[j].Key })
	seen := map[rangeKey]bool{}
	var kept []Range
	for _, r := range ranges {
		seq := r.Seq
		if floor < seq && seq <= req.Lift {
			blocked := false
			if maxBlocker > seq {
				lo := sort.Search(len(blockers), func(i int) bool { return blockers[i].Key >= r.Start })
				hi := sort.Search(len(blockers), func(i int) bool { return blockers[i].Key >= r.End })
				for _, p := range blockers[lo:hi] {
					if seq < p.Seq && p.Seq <= req.Lift {
						blocked = true
						break
					}
				}
			}
			if !blocked {
				seq = req.Lift
			}
		}
		key := rangeKey{r.Start, r.End, seq}
		if seen[key] {
			continue
		}
		seen[key] = true
		kept = append(kept, Range{Start: r.Start, End: r.End, Seq: seq})
	}
	out := req
	out.Tables = []Table{{Points: points, Ranges: kept}}
	return out
}

func Compact(req Request) Result {
	req = applyCutoff(req)
	req = applyRelease(req)
	req = applyLift(req)
	out := Result{Points: []Point{}, Ranges: []Fragment{}}
	cutSet := map[uint64]bool{req.Latest: true}
	for _, c := range req.Snapshots {
		cutSet[c] = true
	}
	cuts := make([]uint64, 0, len(cutSet))
	for c := range cutSet {
		cuts = append(cuts, c)
	}
	sort.Slice(cuts, func(i, j int) bool { return cuts[i] < cuts[j] })
	oldest := cuts[0]
	points := map[string]map[uint64]Point{}
	ranges := map[Range]bool{}
	keys := map[string]bool{}
	seqSet := map[uint64]bool{}
	events := map[string][]event{}
	lowerEvents := map[string]int{}
	for _, t := range req.Tables {
		for _, p := range t.Points {
			if points[p.Key] == nil {
				points[p.Key] = map[uint64]Point{}
			}
			points[p.Key][p.Seq] = p
			keys[p.Key] = true
		}
		for _, r := range t.Ranges {
			ranges[r] = true
		}
	}
	for r := range ranges {
		keys[r.Start], keys[r.End] = true, true
		seqSet[r.Seq] = true
		events[r.Start] = append(events[r.Start], event{r.Seq, 1})
		events[r.End] = append(events[r.End], event{r.Seq, -1})
	}
	for _, span := range req.Lower {
		keys[span.Start], keys[span.End] = true, true
		lowerEvents[span.Start]++
		lowerEvents[span.End]--
	}
	positions := make([]string, 0, len(keys))
	for key := range keys {
		positions = append(positions, key)
	}
	sort.Strings(positions) // Lowercase even-length hex preserves byte ordering.
	seqs := make([]uint64, 0, len(seqSet))
	for seq := range seqSet {
		seqs = append(seqs, seq)
	}
	sort.Slice(seqs, func(i, j int) bool { return seqs[i] < seqs[j] })
	active := rangeIndex{seqs, make([]int, len(seqs)), make([]int, len(seqs)+1)}
	lowerCount := 0
	for pos, key := range positions {
		for _, e := range events[key] {
			active.change(e.seq, e.delta)
		}
		lowerCount += lowerEvents[key]
		barriers := make([]uint64, len(cuts))
		for i, cut := range cuts {
			barriers[i] = active.predecessor(cut)
		}
		versions := make([]Point, 0, len(points[key]))
		for _, p := range points[key] {
			versions = append(versions, p)
		}
		sort.Slice(versions, func(i, j int) bool { return versions[i].Seq > versions[j].Seq })
		live := make([]bool, len(versions))
		for ci, cut := range cuts {
			start := sort.Search(len(versions), func(i int) bool { return versions[i].Seq <= cut })
			for j := start; j < len(versions); j++ {
				if versions[j].Seq <= barriers[ci] {
					break
				}
				live[j] = true
				if versions[j].Kind != "merge" {
					break
				}
			}
		}
		previousStripe, mergeOutput := -1, -1
		var previousSeq uint64
		for j, p := range versions {
			if !live[j] {
				mergeOutput = -1
				continue
			}
			stripe := sort.Search(len(cuts), func(i int) bool { return cuts[i] >= p.Seq })
			if p.Kind == "merge" {
				if mergeOutput >= 0 && stripe == previousStripe && active.predecessor(previousSeq) < p.Seq {
					sum := *out.Points[mergeOutput].Value + *p.Value
					out.Points[mergeOutput].Value = &sum
				} else {
					value := *p.Value
					p.Value = &value
					out.Points = append(out.Points, p)
					mergeOutput = len(out.Points) - 1
				}
				previousStripe, previousSeq = stripe, p.Seq
			} else {
				mergeOutput = -1
				if p.Kind == "delete" && p.Seq <= oldest && lowerCount == 0 {
					continue
				}
				out.Points = append(out.Points, p)
			}
		}
		if pos+1 == len(positions) {
			continue
		}
		stack := []uint64{}
		for ci := len(cuts) - 1; ci >= 0; ci-- {
			seq := barriers[ci]
			if seq == 0 || (seq <= oldest && lowerCount == 0) {
				continue
			}
			if len(stack) == 0 || stack[len(stack)-1] != seq {
				stack = append(stack, seq)
			}
		}
		if len(stack) > 0 {
			n := len(out.Ranges)
			if n > 0 && out.Ranges[n-1].End == key && sameStack(out.Ranges[n-1].Seqs, stack) {
				out.Ranges[n-1].End = positions[pos+1]
			} else {
				out.Ranges = append(out.Ranges, Fragment{key, positions[pos+1], stack})
			}
		}
	}
	return out
}
