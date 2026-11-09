package main

import "bytes"

var classes = []string{"BRIDGE", "INVOICE", "PLANT", "TRADE", "WORKING"}
var dayRules = []string{"actual", "thirty-us", "thirty-e"}
var onceKeys = []string{"facility", "class", "advance", "rate", "drawn", "periods", "frequency"}

type facility struct {
	reference string
	class     string
	advance   int64
	rate      int64
	drawn     day
	periods   int
	frequency int
	held      map[string]bool
}

type change struct {
	when day
	rate int64
}

// Paragraph 2.1. Ignored lines keep their place so that 1.5 can count them.
func significant(blob []byte) [][2]interface{} {
	text := bytes.TrimSuffix(blob, []byte("\n"))
	out := make([][2]interface{}, 0, 16)
	if len(blob) == 0 {
		return out
	}
	for index, line := range bytes.Split(text, []byte("\n")) {
		if len(line) == 0 || line[0] == '#' {
			continue
		}
		out = append(out, [2]interface{}{index + 1, string(line)})
	}
	return out
}

func allDigits(token string) bool {
	if token == "" {
		return false
	}
	for i := 0; i < len(token); i++ {
		if token[i] < '0' || token[i] > '9' {
			return false
		}
	}
	return true
}

// A plain decimal figure: no leading zero, and a bare zero only where it is allowed.
func plainNumber(token string, mostDigits int, bareZero bool) (int64, bool) {
	if !allDigits(token) || len(token) > mostDigits {
		return 0, false
	}
	if len(token) > 1 && token[0] == '0' {
		return 0, false
	}
	if token == "0" && !bareZero {
		return 0, false
	}
	var value int64
	for i := 0; i < len(token); i++ {
		value = value*10 + int64(token[i]-'0')
	}
	return value, true
}

// Paragraph 3.5.
func readDate(token string) (day, bool) {
	if len(token) != 10 || token[4] != '-' || token[7] != '-' {
		return day{}, false
	}
	if !allDigits(token[0:4]) || !allDigits(token[5:7]) || !allDigits(token[8:10]) {
		return day{}, false
	}
	y, _ := plainDigits(token[0:4])
	m, _ := plainDigits(token[5:7])
	d, _ := plainDigits(token[8:10])
	if y < 1970 || y > 2199 || m < 1 || m > 12 {
		return day{}, false
	}
	if d < 1 || d > monthLength(y, m) {
		return day{}, false
	}
	return day{y, m, d}, true
}

func plainDigits(token string) (int, bool) {
	value := 0
	for i := 0; i < len(token); i++ {
		value = value*10 + int(token[i]-'0')
	}
	return value, true
}

// Paragraph 3.4.
func readRate(token string) (int64, bool) {
	value, ok := plainNumber(token, 5, true)
	if !ok || value > 20000 {
		return 0, false
	}
	return value, true
}

func member(list []string, want string) bool {
	for _, item := range list {
		if item == want {
			return true
		}
	}
	return false
}

// Paragraphs 2.2 to 2.4 and section 3, in the order 10.1 sets for one line.
func readFacility(blob []byte) (facility, []change, error) {
	deal := facility{held: map[string]bool{}}
	steps := []change{}
	for _, row := range significant(blob) {
		number := row[0].(int)
		line := row[1].(string)
		cut := -1
		for i := 0; i < len(line); i++ {
			if line[i] == ' ' {
				cut = i
				break
			}
		}
		if cut < 0 {
			return deal, nil, atFacility(number, "malformed entry")
		}
		key, value := line[:cut], line[cut+1:]
		if key == "" || len(key) > 12 || value == "" {
			return deal, nil, atFacility(number, "malformed entry")
		}
		for i := 0; i < len(key); i++ {
			if key[i] < 'a' || key[i] > 'z' {
				return deal, nil, atFacility(number, "malformed entry")
			}
		}
		if key != "step" && !member(onceKeys, key) {
			return deal, nil, atFacility(number, "unknown key")
		}
		if key != "step" && deal.held[key] {
			return deal, nil, atFacility(number, "repeated key")
		}
		switch key {
		case "facility":
			if len(value) > 16 || !referenceBytes(value) {
				return deal, nil, atFacility(number, "bad reference")
			}
			deal.reference = value
		case "class":
			if !member(classes, value) {
				return deal, nil, atFacility(number, "unknown class")
			}
			deal.class = value
		case "advance":
			amount, ok := plainNumber(value, 10, false)
			if !ok {
				return deal, nil, atFacility(number, "bad advance")
			}
			deal.advance = amount
		case "rate":
			rate, ok := readRate(value)
			if !ok {
				return deal, nil, atFacility(number, "bad rate")
			}
			deal.rate = rate
		case "drawn":
			when, ok := readDate(value)
			if !ok {
				return deal, nil, atFacility(number, "bad date")
			}
			deal.drawn = when
		case "periods":
			count, ok := plainNumber(value, 3, false)
			if !ok || count > 480 {
				return deal, nil, atFacility(number, "bad period count")
			}
			deal.periods = int(count)
		case "frequency":
			if value != "1" && value != "3" && value != "6" && value != "12" {
				return deal, nil, atFacility(number, "bad frequency")
			}
			months, _ := plainNumber(value, 2, false)
			deal.frequency = int(months)
		case "step":
			if len(value) < 11 || value[10] != ' ' {
				return deal, nil, atFacility(number, "bad step")
			}
			when, dateOK := readDate(value[:10])
			rate, rateOK := readRate(value[11:])
			if !dateOK || !rateOK {
				return deal, nil, atFacility(number, "bad step")
			}
			steps = append(steps, change{when, rate})
			continue
		}
		deal.held[key] = true
	}
	return deal, steps, nil
}

// Paragraph 3.1.
func referenceBytes(value string) bool {
	if value == "" {
		return false
	}
	for i := 0; i < len(value); i++ {
		b := value[i]
		if (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9') || b == '-' {
			continue
		}
		return false
	}
	return true
}

// Section 4.
func readBasis(blob []byte) (map[string][2]string, error) {
	table := map[string][2]string{}
	for _, row := range significant(blob) {
		number := row[0].(int)
		fields := splitExact(row[1].(string))
		if fields == nil {
			return nil, atBasis(number, "malformed entry")
		}
		name, rule, year := fields[0], fields[1], fields[2]
		shape := len(name) == 2 && name[0] >= 'A' && name[0] <= 'Z' &&
			name[1] >= '0' && name[1] <= '9' &&
			member(dayRules, rule) && (year == "360" || year == "365")
		if !shape {
			return nil, atBasis(number, "malformed entry")
		}
		if _, seen := table[name]; seen {
			return nil, atBasis(number, "duplicate identifier")
		}
		table[name] = [2]string{rule, year}
	}
	// Paragraph 4.3: one line for each of the six pairings, so every pairing stands once.
	pairings := map[[2]string]int{}
	for _, pairing := range table {
		pairings[pairing]++
	}
	if len(table) != 6 || len(pairings) != 6 {
		return nil, whole("basis table incomplete")
	}
	return table, nil
}

func splitExact(line string) []string {
	fields := []string{}
	start := 0
	for i := 0; i <= len(line); i++ {
		if i == len(line) || line[i] == ' ' {
			fields = append(fields, line[start:i])
			start = i + 1
		}
	}
	if len(fields) != 3 {
		return nil
	}
	for _, field := range fields {
		if field == "" {
			return nil
		}
	}
	return fields
}
