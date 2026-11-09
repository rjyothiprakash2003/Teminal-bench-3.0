package main

// A calendar date held as its three fields, which is what 3.5 and 6.6 both work on.
type day struct{ y, m, d int }

func leap(y int) bool { return y%4 == 0 }

// Paragraph 3.5.
func monthLength(y, m int) int {
	switch m {
	case 2:
		if leap(y) {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}

func (a day) before(b day) bool {
	if a.y != b.y {
		return a.y < b.y
	}
	if a.m != b.m {
		return a.m < b.m
	}
	return a.d < b.d
}

// Days from a fixed origin, used only by the actual rule of 6.5. February's
// length follows 3.5 (a year has a twenty-ninth of February when divisible by four).
func (a day) serial() int64 {
	n := int64(0)
	for y := 1970; y < a.y; y++ {
		if leap(y) {
			n += 366
		} else {
			n += 365
		}
	}
	for m := 1; m < a.m; m++ {
		n += int64(monthLength(a.y, m))
	}
	return n + int64(a.d)
}

// Paragraphs 6.4 to 6.7.
func dayCount(rule string, first, second day) int64 {
	if rule == "actual" {
		return second.serial() - first.serial()
	}
	d1, d2 := first.d, second.d
	if rule == "thirty-us" {
		if d1 == 31 {
			d1 = 30
		}
		if d2 == 31 && d1 == 30 {
			d2 = 30
		}
	} else {
		if d1 == 31 {
			d1 = 30
		}
		if d2 == 31 {
			d2 = 30
		}
	}
	return int64(360*(second.y-first.y) + 30*(second.m-first.m) + (d2 - d1))
}

// Paragraph 6.1. The month is counted from the drawdown month, not from the period before.
func periodEnd(drawn day, index, frequency int) day {
	total := (drawn.m - 1) + index*frequency
	y := drawn.y + total/12
	m := total%12 + 1
	d := drawn.d
	if limit := monthLength(y, m); d > limit {
		d = limit
	}
	return day{y, m, d}
}

// Paragraph 7.5: 29 February of a leap year on the span, including the second date.
func leapDayOn(first, second day) bool {
	for year := first.y; year <= second.y; year++ {
		if !leap(year) {
			continue
		}
		when := day{year, 2, 29}
		if (first.before(when) || first == when) && (when.before(second) || when == second) {
			return true
		}
	}
	return false
}
