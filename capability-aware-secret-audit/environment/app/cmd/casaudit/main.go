package main

import (
	"encoding/json"
	"os"
	"path/filepath"
)

type audienceFile struct {
	Entries  []map[string]any `json:"entries"`
	Subjects []map[string]any `json:"subjects"`
}

func refuse() {
	os.Stderr.WriteString("refuse: bad args\n")
	os.Exit(2)
}

func main() {
	var root, audience, reportPath string
	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--root":
			if i+1 >= len(args) {
				refuse()
			}
			root = args[i+1]
			i++
		case "--audience":
			if i+1 >= len(args) {
				refuse()
			}
			audience = args[i+1]
			i++
		case "--report":
			if i+1 >= len(args) {
				refuse()
			}
			reportPath = args[i+1]
			i++
		default:
			refuse()
		}
	}
	if root == "" || audience == "" || reportPath == "" {
		refuse()
	}

	raw, err := os.ReadFile(audience)
	if err != nil {
		refuse()
	}
	var af audienceFile
	if err := json.Unmarshal(raw, &af); err != nil {
		refuse()
	}

	findings := []map[string]any{}
	skipped := []map[string]any{}

	for _, e := range af.Entries {
		path, _ := e["path"].(string)
		full, err := filepath.EvalSymlinks(filepath.Join(root, path))
		if err != nil {
			skipped = append(skipped, map[string]any{"path": path, "reason": "missing"})
			continue
		}
		st, err := os.Stat(full)
		if err != nil {
			skipped = append(skipped, map[string]any{"path": path, "reason": "missing"})
			continue
		}
		mode := st.Mode().Perm()
		exp := []string{}
		for _, s := range af.Subjects {
			uidF, _ := s["uid"].(float64)
			uid := int(uidF)
			if mode&0o004 != 0 || mode&0o040 != 0 {
				exp = append(exp, "uid:"+itoa(uid))
			}
		}
		if len(exp) == 0 {
			continue
		}
		findings = append(findings, map[string]any{
			"path":        path,
			"exposure":    exp,
			"remediation": map[string]any{"mode": "0000"},
		})
	}

	out := map[string]any{"findings": findings, "skipped": skipped}
	text, _ := json.MarshalIndent(out, "", "  ")
	_ = os.MkdirAll(filepath.Dir(reportPath), 0o755)
	_ = os.WriteFile(reportPath, text, 0o644)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [32]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
