package main

import (
	"fmt"
	"sort"
	"strings"
)

var lastDate = day{2199, 12, 31}

// Paragraph 8.1.
func rateInForce(base int64, steps []change, at day) int64 {
	rate := base
	var best day
	found := false
	for _, step := range steps {
		if step.when.before(at) || step.when == at {
			if !found || best.before(step.when) {
				best, rate, found = step.when, step.rate, true
			}
		}
	}
	return rate
}

func stamp(when day) string { return fmt.Sprintf("%04d-%02d-%02d", when.y, when.m, when.d) }

// Sections 6 to 9, in the order 10.1 sets for the work as a whole.
func schedule(deal facility, steps []change, table map[string][2]string) ([]byte, error) {
	for _, key := range onceKeys {
		if !deal.held[key] {
			return nil, whole("missing key")
		}
	}
	ends := make([]day, deal.periods)
	for index := range ends {
		ends[index] = periodEnd(deal.drawn, index+1, deal.frequency)
	}
	finish := ends[len(ends)-1]
	for _, step := range steps {
		if !deal.drawn.before(step.when) || !step.when.before(finish) {
			return nil, whole("step out of term")
		}
	}
	for i := range steps {
		for j := 0; j < i; j++ {
			if steps[i].when == steps[j].when {
				return nil, whole("repeated step date")
			}
		}
	}
	if lastDate.before(finish) {
		return nil, whole("term past the calendar")
	}

	rule, year := pairingFor(deal.class)
	name := ""
	for label, pairing := range table {
		if pairing[0] == rule && pairing[1] == year {
			name = label
		}
	}
	yearDays := atoi(year)

	var out strings.Builder
	fmt.Fprintf(&out, "SCHEDULE %s %s %s %03d\n", deal.reference, deal.class, name, deal.periods)
	balance := deal.advance
	step := deal.advance / int64(deal.periods)
	remainder := deal.advance % int64(deal.periods)
	var carry, interestTotal, principalTotal int64
	begin := deal.drawn
	for index := 0; index < deal.periods; index++ {
		closing := ends[index]
		edges := []day{begin}
		inside := []day{}
		for _, one := range steps {
			if begin.before(one.when) && one.when.before(closing) {
				inside = append(inside, one.when)
			}
		}
		sort.Slice(inside, func(a, b int) bool { return inside[a].before(inside[b]) })
		edges = append(edges, inside...)
		edges = append(edges, closing)

		var days, interest int64
		// Paragraphs 8.3 to 8.5: 7.5 is taken on each part; only the last remainder leaves.
		for part := 0; part+1 < len(edges); part++ {
			span := dayCount(rule, edges[part], edges[part+1])
			days += span
			accrual := balance * rateInForce(deal.rate, steps, edges[part]) * span
			if part == 0 {
				accrual += carry
			}
			divisor := int64(10000) * yearDays
			if rule == "actual" && year == "365" && leapDayOn(edges[part], edges[part+1]) {
				divisor = int64(10000) * 366
			}
			interest += accrual / divisor
			if part+2 == len(edges) {
				carry = accrual % divisor
			}
		}
		principal := step
		if index == deal.periods-1 {
			principal += remainder
		}
		fmt.Fprintf(&out, "%04d %s %s %06d %015d %015d %015d\n",
			index+1, stamp(begin), stamp(closing), days, balance, interest, principal)
		interestTotal += interest
		principalTotal += principal
		balance -= principal
		begin = closing
	}
	fmt.Fprintf(&out, "TOTAL %015d %015d\n", interestTotal, principalTotal)
	return []byte(out.String()), nil
}

func atoi(token string) int64 {
	value, _ := plainNumber(token, 5, false)
	return value
}
