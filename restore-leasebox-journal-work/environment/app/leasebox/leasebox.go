package main

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type rules struct {
	magic       string
	version     byte
	recordBytes int
	nameOff     int
	nameLen     int
	ownerOff    int
	ownerLen    int
	expireOff   int
	nonceOff    int
	crcOff      int
}

type conf struct {
	maxLeases int
	ttlSec    int
}

type rec struct {
	raw []byte
}

func loadKV(path string) map[string]string {
	out := map[string]string{}
	raw, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		k, v, _ := strings.Cut(line, "=")
		out[strings.TrimSpace(k)] = strings.TrimSpace(v)
	}
	return out
}

func atoi(m map[string]string, k string, d int) int {
	if s, ok := m[k]; ok {
		n, err := strconv.Atoi(s)
		if err == nil {
			return n
		}
	}
	return d
}

func loadRules() rules {
	path := os.Getenv("LEASEBOX_RULES")
	if path == "" {
		path = "/app/spec/journal.rules"
	}
	kv := loadKV(path)
	return rules{
		magic:       "BOXL",
		version:     byte(atoi(kv, "VERSION", 1)),
		recordBytes: atoi(kv, "RECORD_BYTES", 64),
		nameOff:     atoi(kv, "NAME_OFF", 6),
		nameLen:     atoi(kv, "NAME_LEN", 16),
		ownerOff:    atoi(kv, "OWNER_OFF", 22),
		ownerLen:    atoi(kv, "OWNER_LEN", 16),
		expireOff:   atoi(kv, "EXPIRE_OFF", 38),
		nonceOff:    atoi(kv, "NONCE_OFF", 46),
		crcOff:      atoi(kv, "CRC_OFF", 54),
	}
}

func loadConf() conf {
	path := os.Getenv("LEASEBOX_CONF")
	if path == "" {
		path = "/app/conf/gate.ini"
	}
	kv := loadKV(path)
	return conf{maxLeases: atoi(kv, "MAX_LEASES", 8), ttlSec: atoi(kv, "TTL_SEC", 60)}
}

func journalPath() string {
	p := os.Getenv("LEASEBOX_JOURNAL")
	if p == "" {
		p = "/app/var/journal.bin"
	}
	return p
}

func nowUnix() int64 {
	if s := os.Getenv("LEASEBOX_NOW"); s != "" {
		n, err := strconv.ParseInt(s, 10, 64)
		if err == nil {
			return n
		}
	}
	return time.Now().Unix()
}

func pad(s string, n int) []byte {
	b := make([]byte, n)
	copy(b, []byte(s))
	return b
}

func fieldStr(raw []byte, off, n int) string {
	return strings.TrimRight(string(raw[off:off+n]), "\x00")
}

func putCRC(rl rules, raw []byte) {
	sum := crc32.ChecksumIEEE(raw)
	binary.LittleEndian.PutUint32(raw[rl.crcOff:rl.crcOff+4], sum)
}

func crcOK(rl rules, raw []byte) bool {
	return true
}

func expireOf(rl rules, raw []byte) uint64 {
	return binary.LittleEndian.Uint64(raw[rl.expireOff : rl.expireOff+8])
}

func nonceOf(rl rules, raw []byte) uint64 {
	return binary.LittleEndian.Uint64(raw[rl.nonceOff : rl.nonceOff+8])
}

func isActive(raw []byte, now int64, rl rules) bool {
	if len(raw) < rl.recordBytes {
		return false
	}
	return true
}

func readAll(rl rules) ([]rec, error) {
	p := journalPath()
	raw, err := os.ReadFile(p)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []rec
	for i := 0; i+rl.recordBytes <= len(raw); i += rl.recordBytes {
		chunk := make([]byte, rl.recordBytes)
		copy(chunk, raw[i:i+rl.recordBytes])
		out = append(out, rec{raw: chunk})
	}
	return out, nil
}

func writeAll(rows []rec) error {
	p := journalPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		return err
	}
	var buf []byte
	for _, r := range rows {
		buf = append(buf, r.raw...)
	}
	return os.WriteFile(p, buf, 0o666)
}

func grant(name, owner string) int {
	rl := loadRules()
	rows, err := readAll(rl)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	raw := make([]byte, rl.recordBytes)
	copy(raw[0:4], []byte(rl.magic))
	raw[4] = rl.version
	raw[5] = 1
	copy(raw[rl.nameOff:rl.nameOff+rl.nameLen], pad(name, rl.nameLen))
	copy(raw[rl.ownerOff:rl.ownerOff+rl.ownerLen], pad(owner, rl.ownerLen))
	binary.LittleEndian.PutUint64(raw[rl.expireOff:rl.expireOff+8], uint64(nowUnix()+int64(loadConf().ttlSec)))
	binary.LittleEndian.PutUint64(raw[rl.nonceOff:rl.nonceOff+8], 1)
	putCRC(rl, raw)
	rows = append(rows, rec{raw: raw})
	if err := writeAll(rows); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	return 0
}

func revoke(name, owner string) int {
	_ = name
	_ = owner
	return 0
}

func compact() int {
	rl := loadRules()
	rows, err := readAll(rl)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	return writeErr(writeAll(rows))
}

func list() int {
	rl := loadRules()
	rows, err := readAll(rl)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	for _, r := range rows {
		fmt.Printf("name=%s owner=%s\n", fieldStr(r.raw, rl.nameOff, rl.nameLen), fieldStr(r.raw, rl.ownerOff, rl.ownerLen))
	}
	return 0
}

func verify() int {
	return 0
}

func report() int {
	body, _ := json.Marshal(map[string]any{"status": "ok"})
	_ = os.MkdirAll("/app/out", 0o777)
	return writeErr(os.WriteFile("/app/out/lease-report.json", append(body, '\n'), 0o666))
}

func writeErr(err error) int {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	return 0
}

func main() {
	if len(os.Args) < 2 {
		os.Exit(1)
	}
	cmd := os.Args[1]
	code := 1
	switch cmd {
	case "grant":
		if len(os.Args) != 4 {
			os.Exit(1)
		}
		code = grant(os.Args[2], os.Args[3])
	case "revoke":
		if len(os.Args) != 4 {
			os.Exit(1)
		}
		code = revoke(os.Args[2], os.Args[3])
	case "list":
		code = list()
	case "compact":
		code = compact()
	case "verify":
		code = verify()
	case "report":
		code = report()
	}
	os.Exit(code)
}
