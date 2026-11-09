package main

import (
	"bufio"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
)

type token struct {
	word string
	stop bool
}

type document struct {
	tokens []token
}

type index map[string]*document

func (ix index) clone() index {
	out := make(index, len(ix))
	for id, doc := range ix {
		cp := &document{tokens: make([]token, len(doc.tokens))}
		copy(cp.tokens, doc.tokens)
		out[id] = cp
	}
	return out
}

func isWordByte(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
}

func lowerByte(c byte) byte {
	if c >= 'A' && c <= 'Z' {
		return c + ('a' - 'A')
	}
	return c
}

func tokenize(text string) []string {
	var out []string
	i := 0
	for i < len(text) {
		if !isWordByte(text[i]) {
			i++
			continue
		}
		j := i
		var b strings.Builder
		for j < len(text) && isWordByte(text[j]) {
			b.WriteByte(lowerByte(text[j]))
			j++
		}
		out = append(out, b.String())
		i = j
	}
	return out
}

func analyze(text string, stops map[string]bool) []token {
	words := tokenize(text)
	out := make([]token, 0, len(words))
	for _, w := range words {
		out = append(out, token{word: w, stop: stops[w]})
	}
	return out
}

type queryWord struct {
	word string
	pos  int
}

func queryWords(text string, stops map[string]bool) []queryWord {
	var out []queryWord
	for pos, t := range analyze(text, stops) {
		if t.stop {
			continue
		}
		out = append(out, queryWord{word: t.word, pos: pos})
	}
	return out
}

func editDistance(a, b string) int {
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

func feasible(cand [][]int, words []queryWord, lo, hi int) bool {
	owner := map[int]int{}
	var try func(j int, seen map[int]bool) bool
	try = func(j int, seen map[int]bool) bool {
		for _, p := range cand[j] {
			off := p - words[j].pos
			if off < lo || off > hi || seen[p] {
				continue
			}
			seen[p] = true
			prev, taken := owner[p]
			if !taken || try(prev, seen) {
				owner[p] = j
				return true
			}
		}
		return false
	}
	for j := range words {
		if !try(j, map[int]bool{}) {
			return false
		}
	}
	return true
}

func leastSpread(cand [][]int, words []queryWord) (int, []int, bool) {
	var offs []int
	for j := range words {
		for _, p := range cand[j] {
			offs = append(offs, p-words[j].pos)
		}
	}
	if len(offs) == 0 {
		return 0, nil, false
	}
	sort.Ints(offs)
	uniq := offs[:1]
	for _, o := range offs[1:] {
		if o != uniq[len(uniq)-1] {
			uniq = append(uniq, o)
		}
	}
	best, found := 0, false
	h := 0
	for l := range uniq {
		if h < l {
			h = l
		}
		for h < len(uniq) && !feasible(cand, words, uniq[l], uniq[h]) {
			h++
		}
		if h == len(uniq) {
			break
		}
		if !found || uniq[h]-uniq[l] < best {
			best, found = uniq[h]-uniq[l], true
		}
	}
	return best, uniq, found
}

func leastCost(doc *document, cand [][]int, words []queryWord, lo, hi int) int {
	best := -1
	used := map[int]bool{}
	var walk func(j, total int)
	walk = func(j, total int) {
		if best >= 0 && total >= best {
			return
		}
		if j == len(words) {
			best = total
			return
		}
		for _, p := range cand[j] {
			off := p - words[j].pos
			if off < lo || off > hi || used[p] {
				continue
			}
			used[p] = true
			walk(j+1, total+editDistance(words[j].word, doc.tokens[p].word))
			used[p] = false
		}
	}
	walk(0, 0)
	return best
}

func distance(doc *document, words []queryWord, fuzz int) (int, int, bool) {
	exact := make([][]int, len(words))
	near := make([][]int, len(words))
	for j, qw := range words {
		for p, t := range doc.tokens {
			if t.stop {
				continue
			}
			if t.word == qw.word {
				exact[j] = append(exact[j], p)
			}
			if editDistance(qw.word, t.word) <= fuzz {
				near[j] = append(near[j], p)
			}
		}
	}
	if d, _, ok := leastSpread(exact, words); ok {
		return d, 0, true
	}
	for j := range words {
		if len(near[j]) == 0 {
			return 0, 0, false
		}
	}
	d, uniq, ok := leastSpread(near, words)
	if !ok {
		return 0, 0, false
	}
	edits := -1
	for _, lo := range uniq {
		if !feasible(near, words, lo, lo+d) {
			continue
		}
		if c := leastCost(doc, near, words, lo, lo+d); c >= 0 && (edits < 0 || c < edits) {
			edits = c
		}
	}
	return d, edits, true
}

type hit struct {
	id    string
	dist  int
	edits int
}

func search(committed index, slop, fuzz int, words []queryWord) string {
	var hits []hit
	for id, doc := range committed {
		d, edits, ok := distance(doc, words, fuzz)
		if ok && d <= slop {
			hits = append(hits, hit{id: id, dist: d, edits: edits})
		}
	}
	if len(hits) == 0 {
		return "-"
	}
	sort.Slice(hits, func(a, b int) bool {
		if hits[a].dist != hits[b].dist {
			return hits[a].dist < hits[b].dist
		}
		if hits[a].edits != hits[b].edits {
			return hits[a].edits < hits[b].edits
		}
		return hits[a].id < hits[b].id
	})
	parts := make([]string, len(hits))
	for i, h := range hits {
		parts[i] = h.id + "@" + strconv.Itoa(h.dist)
	}
	return strings.Join(parts, " ")
}

func splitField(rest string) (string, string) {
	i := strings.IndexByte(rest, ' ')
	if i < 0 {
		return rest, ""
	}
	return rest[:i], rest[i+1:]
}

func run(in io.Reader, out io.Writer) {
	data, _ := io.ReadAll(in)
	lines := strings.Split(string(data), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	w := bufio.NewWriter(out)
	defer w.Flush()
	if len(lines) == 0 {
		return
	}
	stops := map[string]bool{}
	for _, s := range strings.Fields(strings.TrimPrefix(lines[0], "stopwords")) {
		stops[s] = true
	}
	committed := index{}
	var working index
	for _, line := range lines[1:] {
		verb, rest := splitField(line)
		switch verb {
		case "begin":
			working = committed.clone()
		case "commit":
			committed = working
			working = nil
		case "rollback":
			working = nil
		case "add":
			id, text := splitField(rest)
			working[id] = &document{tokens: analyze(text, stops)}
		case "append":
			id, text := splitField(rest)
			doc := working[id]
			doc.tokens = append(doc.tokens, analyze(text, stops)...)
		case "delete":
			delete(working, rest)
		case "query":
			slopText, rest := splitField(rest)
			fuzzText, text := splitField(rest)
			slop, _ := strconv.Atoi(slopText)
			fuzz, _ := strconv.Atoi(fuzzText)
			w.WriteString(search(committed, slop, fuzz, queryWords(text, stops)))
			w.WriteString("\n")
		}
	}
}

func main() {
	run(os.Stdin, os.Stdout)
}
