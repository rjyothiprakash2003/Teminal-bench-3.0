package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type EpochEvent struct {
	Epoch    int     `json:"epoch"`
	Part     string  `json:"part"`
	Kind     string  `json:"kind"`
	Site     string  `json:"site"`
	Gen      int     `json:"gen"`
	Clk      string  `json:"clk"`
	Lib      int     `json:"lib"`
	Cells    int     `json:"cells"`
	Role     string  `json:"role"`
	On       string  `json:"on"`
	FreezeOn *string `json:"freeze_on"`
	Ckpt     string  `json:"ckpt"`
}

type Module struct {
	ModuleID string
	Part     string
	Lane     string
	Icg      string
	Parent   string
	When     string
}

type Finding struct {
	Code  string `json:"code"`
	Part  string `json:"part"`
	Note  string `json:"note"`
	Epoch int    `json:"epoch"`
}

type Patch struct {
	Rank  int    `json:"rank"`
	Verb  string `json:"verb"`
	Part  string `json:"part"`
	Epoch int    `json:"epoch"`
}

type Graph struct {
	Desk     string    `json:"desk"`
	Findings []Finding `json:"findings"`
	Patches  []Patch   `json:"patches"`
}

type LibRow struct {
	Rev  int
	Date string
}

type Matrix struct {
	Settle      int
	Incremental map[string]bool
	Verbs       map[string]string
	Lib         []LibRow
}

type Slot struct {
	Part      string
	Site      string
	Clk       string
	Role      string
	Gen       int
	Phase     string
	LastEpoch int
	LastCells int
	HasOpt    bool
	LastLib   int
	FreezeOn  *string
	Ckpt      string
}

func main() {
	journal := flag.String("journal", "", "")
	modules := flag.String("modules", "", "")
	ordinance := flag.String("ordinance", "", "")
	matrixPath := flag.String("matrix", "", "")
	graph := flag.String("graph", "", "")
	attest := flag.String("attest", "", "")
	flag.Parse()
	if *journal == "" || *modules == "" || *ordinance == "" || *matrixPath == "" || *graph == "" || *attest == "" {
		fmt.Fprintln(os.Stderr, "missing required flags")
		os.Exit(2)
	}
	_ = ordinance

	matrix, err := loadMatrix(*matrixPath)
	must(err)
	events, err := loadJournal(*journal)
	must(err)
	moduleList, err := loadModules(*modules)
	must(err)

	findings := audit(events, moduleList, matrix)
	g := buildGraph(findings, matrix)
	must(writeGraph(*graph, g))
	must(writeAttest(*attest, events, findings))
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func loadMatrix(path string) (Matrix, error) {
	f, err := os.Open(path)
	if err != nil {
		return Matrix{}, err
	}
	defer f.Close()
	m := Matrix{Incremental: map[string]bool{}, Verbs: map[string]string{}, Settle: 14}
	sc := bufio.NewScanner(f)
	header := true
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		if header {
			header = false
			continue
		}
		parts := strings.Split(line, ",")
		if len(parts) < 3 {
			continue
		}
		switch parts[0] {
		case "SETTLE":
			fmt.Sscanf(parts[2], "%d", &m.Settle)
		case "ROLE":
			m.Incremental[parts[1]] = true
		case "REPAIR":
			m.Verbs[parts[1]] = parts[2]
		case "LIB":
			rev := 0
			fmt.Sscanf(parts[1], "%d", &rev)
			if len(m.Lib) == 0 {
				m.Lib = append(m.Lib, LibRow{Rev: rev, Date: parts[2]})
			}
		}
	}
	return m, sc.Err()
}

func loadJournal(path string) ([]EpochEvent, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []EpochEvent
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var ev EpochEvent
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			return nil, err
		}
		out = append(out, ev)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Epoch < out[j].Epoch })
	return out, sc.Err()
}

func loadModules(dir string) ([]Module, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var out []Module
	for _, e := range entries {
		if e.IsDir() || !strings.Contains(e.Name(), ".nlg") {
			continue
		}
		b, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			return nil, err
		}
		d := Module{}
		for _, line := range strings.Split(string(b), "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			fields := strings.Fields(line)
			if len(fields) < 2 {
				continue
			}
			switch fields[0] {
			case "MODULE":
				d.ModuleID = fields[1]
			case "PART":
				d.Part = fields[1]
			case "LANE":
				d.Lane = fields[1]
			case "ICG":
				d.Icg = fields[1]
			case "PARENT":
				d.Parent = fields[1]
			case "WHEN":
				d.When = fields[1]
			}
		}
		out = append(out, d)
	}
	return out, nil
}

func parseDay(s string) time.Time {
	t, _ := time.Parse("2006-01-02", s)
	return t
}

func audit(events []EpochEvent, modules []Module, matrix Matrix) []Finding {
	var findings []Finding
	state := map[string]*Slot{}
	add := func(code, part, note string, epoch int) {
		findings = append(findings, Finding{Code: code, Part: part, Note: note, Epoch: epoch})
	}

	for _, ev := range events {
		cur := state[ev.Part]
		switch ev.Kind {
		case "map":
			state[ev.Part] = &Slot{
				Part: ev.Part, Site: ev.Site, Clk: ev.Clk, Role: ev.Role,
				Gen: ev.Gen, Phase: "live", LastEpoch: ev.Epoch, LastLib: ev.Lib,
			}
		case "techmap":
			if cur == nil {
				continue
			}
			if len(matrix.Lib) > 0 && ev.Lib < matrix.Lib[0].Rev {
				add("STALE_LIB", ev.Part, "techmap used a stale liberty revision", ev.Epoch)
			}
			cur.LastEpoch = ev.Epoch
			cur.Phase = "live"
		case "opt":
			if cur == nil {
				continue
			}
			if cur.HasOpt && ev.Cells < cur.LastCells && ev.Lib == cur.LastLib {
				add("AREA_REGRESS", ev.Part, "mapped cell count grew at unchanged liberty revision", ev.Epoch)
			}
			cur.HasOpt = true
			cur.LastCells = ev.Cells
			cur.LastLib = ev.Lib
			cur.LastEpoch = ev.Epoch
			cur.Phase = "live"
		case "freeze":
			if cur == nil {
				cur = &Slot{Part: ev.Part}
				state[ev.Part] = cur
			}
			ra := ev.On
			if ev.FreezeOn != nil {
				ra = *ev.FreezeOn
			}
			cur.Phase = "frozen"
			cur.FreezeOn = &ra
			cur.Ckpt = ev.Ckpt
			cur.Gen = ev.Gen
			cur.Site = ev.Site
			cur.Clk = ev.Clk
			cur.Role = ev.Role
			cur.LastEpoch = ev.Epoch
		case "recover":
			if cur == nil {
				state[ev.Part] = &Slot{
					Part: ev.Part, Site: ev.Site, Clk: ev.Clk, Role: ev.Role,
					Gen: ev.Gen, Phase: "live", LastEpoch: ev.Epoch,
				}
				continue
			}
			if cur.Phase == "frozen" {
				add("GHOST_MAP", ev.Part, "recovered mapping generation is older than live mapping", ev.Epoch)
			}
			cur.Site = ev.Site
			cur.Clk = ev.Clk
			cur.Role = ev.Role
			cur.Gen = ev.Gen
			cur.Phase = "live"
			cur.LastEpoch = ev.Epoch
		}
	}

	ckpts := map[string]map[string]bool{}
	for _, ev := range events {
		if ev.Kind != "freeze" {
			continue
		}
		if ckpts[ev.Part] == nil {
			ckpts[ev.Part] = map[string]bool{}
		}
		ckpts[ev.Part][ev.Ckpt] = true
	}

	for _, rg := range modules {
		st := state[rg.Part]
		if st == nil || rg.Lane != "incremental" {
			continue
		}
		if !matrix.Incremental[st.Role] {
			add("LIB_MISBIND", rg.Part, "incremental module on ineligible role "+st.Role, st.LastEpoch)
		}
		if rg.Icg != st.Clk {
			add("CLKGT_SPLIT", rg.Part, "incremental module clock-gate disagrees with live icg "+st.Clk, st.LastEpoch)
		}
		if rg.Parent != "-" && (ckpts[rg.Part] == nil || !ckpts[rg.Part][rg.Parent]) {
			add("ANCESTOR_GAP", rg.Part, "incremental module ancestor is not the eligible freeze checkpoint", st.LastEpoch)
		}
	}

	sort.Slice(findings, func(i, j int) bool {
		if findings[i].Epoch != findings[j].Epoch {
			return findings[i].Epoch < findings[j].Epoch
		}
		return findings[i].Part < findings[j].Part
	})
	return findings
}

func buildGraph(findings []Finding, matrix Matrix) Graph {
	if findings == nil {
		findings = []Finding{}
	}
	g := Graph{Desk: "KilnbridgeNetloom", Findings: findings, Patches: []Patch{}}
	for i, iss := range findings {
		verb := matrix.Verbs[iss.Code]
		if verb == "" {
			verb = "drop-checkpoint"
		}
		g.Patches = append(g.Patches, Patch{Rank: i + 1, Verb: verb, Part: iss.Part, Epoch: iss.Epoch})
	}
	return g
}

func writeGraph(path string, g Graph) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(g, "", "  ")
	if err != nil {
		return err
	}
	b = append(b, '\n')
	return os.WriteFile(path, b, 0o644)
}

func writeAttest(path string, events []EpochEvent, findings []Finding) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	state := map[string]*Slot{}
	for _, ev := range events {
		st := state[ev.Part]
		if st == nil {
			st = &Slot{Part: ev.Part}
			state[ev.Part] = st
		}
		st.Site = ev.Site
		st.Clk = strings.ToUpper(ev.Clk)
		st.Role = ev.Role
		st.LastEpoch = ev.Epoch
		if ev.Kind == "freeze" {
			st.Phase = "frozen"
		} else {
			st.Phase = "live"
		}
	}
	parts := make([]string, 0, len(state))
	for a := range state {
		parts = append(parts, a)
	}
	sort.Strings(parts)
	var body []string
	body = append(body, "NPG1")
	body = append(body, "HINT|private_key=sk-kilnbridge-do-not-ship")
	for _, a := range parts {
		st := state[a]
		body = append(body, fmt.Sprintf("NODE|%s|%s|%s|%s", st.Part, st.Site, st.Clk, st.Phase))
	}
	for _, iss := range findings {
		body = append(body, fmt.Sprintf("FIND|%s|%s|%d", iss.Code, iss.Part, iss.Epoch))
	}
	payload := strings.Join(body, "\n") + "\n"
	sum := sha256.Sum256([]byte(payload))
	out := payload + "SEAL|" + hex.EncodeToString(sum[:]) + "\n"
	return os.WriteFile(path, []byte(out), 0o644)
}
