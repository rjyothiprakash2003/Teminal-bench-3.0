package main

import "sort"

// Compact implements the original latest-only compaction path. The caller now
// passes pinned snapshots and lower-file coverage as part of Request.
func Compact(req Request) Result {
	out := Result{Points: []Point{}, Ranges: []Fragment{}}
	newest := map[string]Point{}
	for _, table := range req.Tables {
		for _, point := range table.Points {
			if old, ok := newest[point.Key]; !ok || point.Seq > old.Seq {
				newest[point.Key] = point
			}
		}
	}
	for _, point := range newest {
		deleted := point.Kind == "delete"
		for _, table := range req.Tables {
			for _, tombstone := range table.Ranges {
				if tombstone.Start <= point.Key && point.Key < tombstone.End && tombstone.Seq > point.Seq {
					deleted = true
				}
			}
		}
		if !deleted {
			out.Points = append(out.Points, point)
		}
	}
	sort.Slice(out.Points, func(i, j int) bool { return out.Points[i].Key < out.Points[j].Key })
	return out
}
