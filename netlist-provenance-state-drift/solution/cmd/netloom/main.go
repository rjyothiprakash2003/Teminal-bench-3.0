package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"
	"unsafe"
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

type freezeMark struct {
	ckpt string
	on   time.Time
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
	ordBody, err := os.ReadFile(*ordinance)
	if err != nil || !ordinanceOK(string(ordBody)) {
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
		} else {
			fmt.Fprintln(os.Stderr, "ordinance is not the Kilnbridge desk contract")
		}
		os.Exit(2)
	}

	matrix, err := loadMatrix(*matrixPath)
	must(err)
	events, err := loadJournal(*journal)
	must(err)
	moduleList, err := loadModules(*modules)
	must(err)

	if outputAliasesProtected(*graph, *attest, *journal, *ordinance, *matrixPath) {
		fmt.Fprintln(os.Stderr, "output path aliases a protected input")
		os.Exit(2)
	}

	findings := audit(events, moduleList, matrix)
	g := buildGraph(findings, matrix)
	must(publishOutputs(*graph, *attest, g, events, findings))
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func logicalPath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		abs = p
	}
	cur := filepath.Clean(abs)
	seen := map[string]struct{}{}
	for i := 0; i < 64; i++ {
		if _, ok := seen[cur]; ok {
			return cur
		}
		seen[cur] = struct{}{}
		fi, err := os.Lstat(cur)
		if err != nil || fi.Mode()&os.ModeSymlink == 0 {
			if os.IsNotExist(err) {
				parent := filepath.Dir(cur)
				if parent == cur {
					return cur
				}
				return filepath.Clean(filepath.Join(logicalPath(parent), filepath.Base(cur)))
			}
			if err == nil {
				if resolved, rerr := filepath.EvalSymlinks(cur); rerr == nil {
					return filepath.Clean(resolved)
				}
			}
			return cur
		}
		target, err := os.Readlink(cur)
		if err != nil {
			return cur
		}
		if filepath.IsAbs(target) {
			cur = filepath.Clean(target)
		} else {
			cur = filepath.Clean(filepath.Join(filepath.Dir(cur), target))
		}
	}
	return cur
}

func pathsAlias(a, b string) bool {
	if logicalPath(a) == logicalPath(b) {
		return true
	}
	ia, errA := os.Stat(a)
	ib, errB := os.Stat(b)
	if errA == nil && errB == nil && os.SameFile(ia, ib) {
		return true
	}
	return false
}

func collectFiles(dir string) []string {
	var out []string
	entries, err := os.ReadDir(dir)
	if err != nil {
		return out
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		out = append(out, filepath.Join(dir, e.Name()))
	}
	return out
}

func outputAliasesProtected(graph, attest, journal, ordinance, matrix string) bool {
	if pathsAlias(graph, attest) {
		return true
	}
	outs := []string{graph, attest}
	ins := []string{
		journal,
		ordinance,
		matrix,
		"/app/history/synth-epochs.ndjson",
		"/app/canon/synth-ordinance.txt",
		"/app/tables/liberty-eligibility.csv",
	}
	ins = append(ins, collectFiles("/app/netlist/modules")...)
	for _, out := range outs {
		for _, in := range ins {
			if pathsAlias(out, in) {
				return true
			}
		}
	}
	return false
}

func ordinanceOK(body string) bool {
	title := false
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSuffix(line, "\r")
		if line == "Kilnbridge Netlist Provenance Ordinance" {
			title = true
			break
		}
	}
	if !title {
		return false
	}
	for _, code := range []string{"GHOST_MAP", "LIB_MISBIND", "ANCESTOR_GAP", "AREA_REGRESS", "STALE_LIB", "CLKGT_SPLIT"} {
		if !strings.Contains(body, code) {
			return false
		}
	}
	return true
}

func readPhysicalLine(br *bufio.Reader) (string, error) {
	var b strings.Builder
	for {
		chunk, err := br.ReadSlice('\n')
		b.Write(chunk)
		if err == nil || err == io.EOF {
			line := strings.TrimSuffix(b.String(), "\n")
			line = strings.TrimSuffix(line, "\r")
			if err == io.EOF && len(chunk) == 0 && b.Len() == 0 {
				return "", io.EOF
			}
			return line, err
		}
		if err != bufio.ErrBufferFull {
			return "", err
		}
	}
}

func loadMatrix(path string) (Matrix, error) {
	f, err := os.Open(path)
	if err != nil {
		return Matrix{}, err
	}
	defer f.Close()
	m := Matrix{Incremental: map[string]bool{}, Verbs: map[string]string{}}
	settleSet := false
	br := bufio.NewReader(f)
	header := true
	for {
		line, err := readPhysicalLine(br)
		if len(line) == 0 && err == io.EOF {
			break
		}
		if err != nil && err != io.EOF {
			return Matrix{}, err
		}
		line = strings.TrimSpace(line)
		if line != "" {
			if header {
				header = false
			} else {
				parts := strings.SplitN(line, ",", 3)
				if len(parts) >= 3 {
					switch parts[0] {
					case "SETTLE":
						if !settleSet {
							fmt.Sscanf(parts[2], "%d", &m.Settle)
							settleSet = true
						}
					case "ROLE":
						m.Incremental[parts[1]] = parts[2] == "incremental"
					case "REPAIR":
						m.Verbs[parts[1]] = parts[2]
					case "LIB":
						rev := 0
						fmt.Sscanf(parts[1], "%d", &rev)
						m.Lib = append(m.Lib, LibRow{Rev: rev, Date: parts[2]})
					}
				}
			}
		}
		if err == io.EOF {
			break
		}
	}
	return m, nil
}

func loadJournal(path string) ([]EpochEvent, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []EpochEvent
	dec := json.NewDecoder(f)
	for {
		var ev EpochEvent
		if err := dec.Decode(&ev); err != nil {
			if err == io.EOF {
				break
			}
			return nil, err
		}
		ev.Clk = strings.ToLower(ev.Clk)
		out = append(out, ev)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Epoch < out[j].Epoch })
	return out, nil
}

func loadModules(dir string) ([]Module, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var out []Module
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".nlg") {
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
	sort.Slice(out, func(i, j int) bool { return out[i].ModuleID < out[j].ModuleID })
	return out, nil
}

func parseDay(s string) time.Time {
	t, _ := time.Parse("2006-01-02", s)
	return t
}

func governingLib(on string, rows []LibRow) (int, bool) {
	day := parseDay(on)
	var best int
	found := false
	for _, r := range rows {
		if parseDay(r.Date).After(day) {
			continue
		}
		if !found || r.Rev > best {
			best = r.Rev
			found = true
		}
	}
	return best, found
}

func settleReady(freezeOn, when string, settle int) bool {
	limit := parseDay(freezeOn).AddDate(0, 0, settle)
	return !parseDay(when).Before(limit)
}

var (
	hexRun     = regexp.MustCompile(`[0-9a-fA-F]{32,}`)
	b64Run     = regexp.MustCompile(`[A-Za-z0-9+/]{32,}={0,2}`)
	groupedHex = regexp.MustCompile(`[0-9a-fA-F](?:[^A-Za-z0-9]*[0-9a-fA-F]){63}`)
)

func clipToken(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		ch := s[i]
		if ch >= 32 && ch <= 126 {
			b.WriteByte(ch)
		}
	}
	s = hexRun.ReplaceAllString(b.String(), "redacted")
	s = b64Run.ReplaceAllString(s, "redacted")
	s = groupedHex.ReplaceAllString(s, "redacted")
	if len(s) > 24 {
		s = s[:24]
	}
	return s
}

func issueNote(parts ...string) string {
	out := strings.Join(parts, "")
	out = groupedHex.ReplaceAllString(out, "redacted")
	if len(out) > 160 {
		out = out[:160]
	}
	if strings.TrimSpace(out) == "" {
		return "unsafe transition"
	}
	return out
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
			if cur == nil {
				cur = &Slot{Part: ev.Part}
				state[ev.Part] = cur
			}
			cur.Site = ev.Site
			cur.Clk = ev.Clk
			cur.Role = ev.Role
			cur.Gen = ev.Gen
			cur.Phase = "live"
			cur.LastEpoch = ev.Epoch
		case "techmap":
			if cur == nil {
				cur = &Slot{Part: ev.Part}
				state[ev.Part] = cur
			}
			if gov, ok := governingLib(ev.On, matrix.Lib); ok && ev.Lib < gov {
				add("STALE_LIB", ev.Part, "techmap used a stale liberty revision", ev.Epoch)
			}
			cur.Site = ev.Site
			cur.Role = ev.Role
			cur.Phase = "live"
			cur.LastEpoch = ev.Epoch
		case "opt":
			if cur == nil {
				cur = &Slot{Part: ev.Part}
				state[ev.Part] = cur
			}
			if cur.HasOpt && ev.Cells > cur.LastCells && ev.Lib == cur.LastLib {
				add("AREA_REGRESS", ev.Part, "mapped cell count grew at unchanged liberty revision", ev.Epoch)
			}
			cur.HasOpt = true
			cur.LastCells = ev.Cells
			cur.LastLib = ev.Lib
			cur.Site = ev.Site
			cur.Role = ev.Role
			cur.Phase = "live"
			cur.LastEpoch = ev.Epoch
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
			cur.Role = ev.Role
			cur.LastEpoch = ev.Epoch
		case "recover":
			if cur != nil && ev.Gen < cur.Gen {
				add("GHOST_MAP", ev.Part, "recovered mapping generation is older than live mapping", ev.Epoch)
			}
			if cur == nil {
				cur = &Slot{Part: ev.Part}
				state[ev.Part] = cur
			}
			cur.Site = ev.Site
			cur.Clk = ev.Clk
			cur.Role = ev.Role
			cur.Gen = ev.Gen
			cur.Phase = "live"
			cur.LastEpoch = ev.Epoch
		}
		if st := state[ev.Part]; st != nil {
			st.LastEpoch = ev.Epoch
		}
	}

	freezes := map[string][]freezeMark{}
	lastEpoch := map[string]int{}
	for _, ev := range events {
		lastEpoch[ev.Part] = ev.Epoch
		if ev.Kind != "freeze" {
			continue
		}
		on := ev.On
		if ev.FreezeOn != nil {
			on = *ev.FreezeOn
		}
		freezes[ev.Part] = append(freezes[ev.Part], freezeMark{ckpt: ev.Ckpt, on: parseDay(on)})
	}

	seenLane := map[string]bool{}
	for _, rg := range modules {
		st := state[rg.Part]
		if st == nil {
			continue
		}
		if rg.Lane != "incremental" {
			continue
		}
		if !seenLane[rg.Part] {
			seenLane[rg.Part] = true
			if !matrix.Incremental[st.Role] {
				add("LIB_MISBIND", rg.Part, issueNote("incremental module on ineligible role ", clipToken(st.Role)), st.LastEpoch)
			}
		}
		if strings.ToLower(rg.Icg) != st.Clk {
			add("CLKGT_SPLIT", rg.Part, issueNote("incremental module clock-gate disagrees with live icg ", clipToken(st.Clk)), st.LastEpoch)
		}
		legal := "-"
		var best freezeMark
		found := false
		for _, fz := range freezes[rg.Part] {
			if !settleReady(fz.on.Format("2006-01-02"), rg.When, matrix.Settle) {
				continue
			}
			if !found || fz.on.After(best.on) || (fz.on.Equal(best.on) && fz.ckpt < best.ckpt) {
				best = fz
				found = true
			}
		}
		if found {
			legal = best.ckpt
		}
		if rg.Parent != legal {
			add("ANCESTOR_GAP", rg.Part, "incremental module ancestor is not the eligible freeze checkpoint", st.LastEpoch)
		}
	}

	sort.Slice(findings, func(i, j int) bool {
		if findings[i].Epoch != findings[j].Epoch {
			return findings[i].Epoch < findings[j].Epoch
		}
		if findings[i].Code != findings[j].Code {
			return findings[i].Code < findings[j].Code
		}
		return findings[i].Part < findings[j].Part
	})
	deduped := []Finding{}
	seenKey := map[string]bool{}
	for _, iss := range findings {
		key := fmt.Sprintf("%s|%s|%d", iss.Code, iss.Part, iss.Epoch)
		if seenKey[key] {
			continue
		}
		seenKey[key] = true
		deduped = append(deduped, iss)
	}
	return deduped
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

func marshalGraph(g Graph) ([]byte, error) {
	b, err := json.MarshalIndent(g, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}

func marshalAttest(events []EpochEvent, findings []Finding) []byte {
	state := map[string]*Slot{}
	for _, ev := range events {
		st := state[ev.Part]
		if st == nil {
			st = &Slot{Part: ev.Part}
			state[ev.Part] = st
		}
		st.Site = ev.Site
		st.Role = ev.Role
		st.LastEpoch = ev.Epoch
		switch ev.Kind {
		case "map", "recover":
			st.Clk = ev.Clk
			st.Phase = "live"
		case "freeze":
			st.Phase = "frozen"
		default:
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
	for _, a := range parts {
		st := state[a]
		body = append(body, fmt.Sprintf("NODE|%s|%s|%s|%s", st.Part, st.Site, st.Clk, st.Phase))
	}
	for _, iss := range findings {
		body = append(body, fmt.Sprintf("FIND|%s|%s|%d", iss.Code, iss.Part, iss.Epoch))
	}
	payload := strings.Join(body, "\n") + "\n"
	sum := sha256.Sum256([]byte(payload))
	return []byte(payload + "SEAL|" + hex.EncodeToString(sum[:]) + "\n")
}

func writeTempNeighbor(dir, pattern string, body []byte) (string, error) {
	f, err := os.CreateTemp(dir, pattern)
	if err != nil {
		return "", err
	}
	name := f.Name()
	if _, err := f.Write(body); err != nil {
		f.Close()
		os.Remove(name)
		return "", err
	}
	if err := f.Close(); err != nil {
		os.Remove(name)
		return "", err
	}
	return name, nil
}

func atCWD() uintptr {
	dirfd := int32(-100)
	return uintptr(dirfd)
}

func renameExchange(oldpath, newpath string) error {
	oldp, err := syscall.BytePtrFromString(oldpath)
	if err != nil {
		return err
	}
	newp, err := syscall.BytePtrFromString(newpath)
	if err != nil {
		return err
	}
	_, _, errno := syscall.Syscall6(
		syscall.SYS_RENAMEAT2,
		atCWD(),
		uintptr(unsafe.Pointer(oldp)),
		atCWD(),
		uintptr(unsafe.Pointer(newp)),
		2,
		0,
	)
	if errno != 0 {
		return errno
	}
	return nil
}

func linkNoFollow(oldpath, newpath string) error {
	oldp, err := syscall.BytePtrFromString(oldpath)
	if err != nil {
		return err
	}
	newp, err := syscall.BytePtrFromString(newpath)
	if err != nil {
		return err
	}
	_, _, errno := syscall.Syscall6(
		syscall.SYS_LINKAT,
		atCWD(),
		uintptr(unsafe.Pointer(oldp)),
		atCWD(),
		uintptr(unsafe.Pointer(newp)),
		0,
		0,
	)
	if errno != 0 {
		return errno
	}
	return nil
}

func isDirectory(path string) bool {
	fi, err := os.Lstat(path)
	return err == nil && fi.IsDir()
}

func parentWritable(dir string) error {
	f, err := os.CreateTemp(dir, ".probe-*")
	if err != nil {
		return err
	}
	name := f.Name()
	f.Close()
	return os.Remove(name)
}

var errCloneSibling = errors.New("clone sibling")

func cloneTree(src, dst string, skip map[string]bool) error {
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if skip[entry.Name()] {
			continue
		}
		from := filepath.Join(src, entry.Name())
		to := filepath.Join(dst, entry.Name())
		info, err := entry.Info()
		if err != nil {
			return err
		}
		mode := info.Mode()
		if mode&os.ModeSymlink != 0 {
			target, err := os.Readlink(from)
			if err != nil {
				return err
			}
			if err := os.Symlink(target, to); err != nil {
				return err
			}
			continue
		}
		if mode.IsDir() {
			if err := os.Mkdir(to, 0o755); err != nil {
				return err
			}
			if err := cloneTree(from, to, nil); err != nil {
				return err
			}
			continue
		}
		body, err := os.ReadFile(from)
		if err != nil {
			return fmt.Errorf("read %s: %w", from, err)
		}
		if err := os.WriteFile(to, body, 0o644); err != nil {
			return fmt.Errorf("write %s: %w", to, err)
		}
	}
	return nil
}

func liftDirectory(parent string) error {
	grand := filepath.Dir(parent)
	fresh, err := os.MkdirTemp(grand, ".born-*")
	if err != nil {
		return err
	}
	if err := cloneTree(parent, fresh, nil); err != nil {
		os.RemoveAll(fresh)
		return err
	}
	if err := os.RemoveAll(parent); err != nil {
		os.RemoveAll(fresh)
		return err
	}
	if err := os.Rename(fresh, parent); err != nil {
		os.RemoveAll(fresh)
		return err
	}
	return nil
}

func publishExchange(parent, graphBase, attestBase string, graphBytes, attestBytes []byte) error {
	grand := filepath.Dir(parent)
	stage, err := os.MkdirTemp(grand, ".gen-*")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	if err := cloneTree(parent, stage, map[string]bool{graphBase: true, attestBase: true}); err != nil {
		return fmt.Errorf("%w: %v", errCloneSibling, err)
	}
	if err := os.WriteFile(filepath.Join(stage, graphBase), graphBytes, 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(stage, attestBase), attestBytes, 0o644); err != nil {
		return err
	}
	if err := renameExchange(stage, parent); err != nil {
		if liftErr := liftDirectory(parent); liftErr != nil {
			return err
		}
		if err = renameExchange(stage, parent); err != nil {
			return err
		}
	}
	return nil
}

type keptGraph struct {
	path      string
	existed   bool
	priorLink string
	symlink   string
	regular   []byte
}

func keepGraph(graphPath, dir string) (keptGraph, error) {
	var kept keptGraph
	kept.path = graphPath
	fi, err := os.Lstat(graphPath)
	if os.IsNotExist(err) {
		return kept, nil
	}
	if err != nil {
		return kept, err
	}
	kept.existed = true
	hold, holdErr := os.CreateTemp(dir, ".prior-*")
	if holdErr != nil {
		return kept, holdErr
	}
	holdName := hold.Name()
	hold.Close()
	os.Remove(holdName)
	if fi.Mode()&os.ModeSymlink != 0 {
		target, readErr := os.Readlink(graphPath)
		if readErr != nil {
			return kept, readErr
		}
		kept.symlink = target
		if err := linkNoFollow(graphPath, holdName); err != nil {
			return kept, err
		}
		kept.priorLink = holdName
		return kept, nil
	}
	if fi.Mode().IsRegular() {
		body, readErr := os.ReadFile(graphPath)
		if readErr != nil {
			return kept, readErr
		}
		kept.regular = body
		if err := os.Link(graphPath, holdName); err != nil {
			return kept, err
		}
		kept.priorLink = holdName
	}
	return kept, nil
}

func (kept keptGraph) restore() {
	if kept.priorLink != "" {
		if err := os.Rename(kept.priorLink, kept.path); err != nil {
			os.Remove(kept.path)
			if kept.symlink != "" {
				_ = os.Symlink(kept.symlink, kept.path)
			} else if kept.regular != nil {
				_ = os.WriteFile(kept.path, kept.regular, 0o644)
			}
			os.Remove(kept.priorLink)
		}
		return
	}
	if !kept.existed {
		_ = os.Remove(kept.path)
	}
}

func (kept keptGraph) discard() {
	if kept.priorLink != "" {
		os.Remove(kept.priorLink)
	}
}

func publishOutputs(graphPath, attestPath string, g Graph, events []EpochEvent, findings []Finding) error {
	graphBytes, err := marshalGraph(g)
	if err != nil {
		return err
	}
	attestBytes := marshalAttest(events, findings)
	if isDirectory(graphPath) || isDirectory(attestPath) {
		return fmt.Errorf("output path is a directory")
	}
	gDir := filepath.Dir(graphPath)
	aDir := filepath.Dir(attestPath)
	gAbs, err := filepath.Abs(gDir)
	if err != nil {
		return err
	}
	aAbs, err := filepath.Abs(aDir)
	if err != nil {
		return err
	}
	gReal, gErr := filepath.EvalSymlinks(gAbs)
	aReal, aErr := filepath.EvalSymlinks(aAbs)
	if gErr == nil && aErr == nil && gReal == aReal {
		if err := parentWritable(gReal); err != nil {
			return err
		}
		graphBase := filepath.Base(graphPath)
		attestBase := filepath.Base(attestPath)
		if graphBase == attestBase {
			return fmt.Errorf("output paths alias")
		}
		if err = publishExchange(gReal, graphBase, attestBase, graphBytes, attestBytes); err == nil {
			return nil
		}
	}
	if err := os.MkdirAll(gDir, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(aDir, 0o755); err != nil {
		return err
	}
	gTmp, err := writeTempNeighbor(gDir, ".graph-*", graphBytes)
	if err != nil {
		return err
	}
	aTmp, err := writeTempNeighbor(aDir, ".attest-*", attestBytes)
	if err != nil {
		os.Remove(gTmp)
		return err
	}
	kept, err := keepGraph(graphPath, gDir)
	if err != nil {
		os.Remove(gTmp)
		os.Remove(aTmp)
		return err
	}
	if err := os.Rename(gTmp, graphPath); err != nil {
		os.Remove(gTmp)
		os.Remove(aTmp)
		kept.discard()
		return err
	}
	if err := os.Rename(aTmp, attestPath); err != nil {
		os.Remove(aTmp)
		kept.restore()
		return err
	}
	kept.discard()
	return nil
}
