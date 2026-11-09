package edit

func Distance(a, b string) int {
	inf := len(a) + len(b)
	d := make([][]int, len(a)+2)
	for i := range d {
		d[i] = make([]int, len(b)+2)
	}
	d[0][0] = inf
	for i := 0; i <= len(a); i++ {
		d[i+1][0] = inf
		d[i+1][1] = i
	}
	for j := 0; j <= len(b); j++ {
		d[0][j+1] = inf
		d[1][j+1] = j
	}
	last := map[byte]int{}
	for i := 1; i <= len(a); i++ {
		db := 0
		for j := 1; j <= len(b); j++ {
			k, l := last[b[j-1]], db
			cost := 1
			if a[i-1] == b[j-1] {
				cost, db = 0, j
			}
			best := d[i][j] + cost
			if v := d[i+1][j] + 1; v < best {
				best = v
			}
			if v := d[i][j+1] + 1; v < best {
				best = v
			}
			if v := d[k][l] + (i - k - 1) + 1 + (j - l - 1); v < best {
				best = v
			}
			d[i+1][j+1] = best
		}
		last[a[i-1]] = i
	}
	return d[len(a)+1][len(b)+1]
}

