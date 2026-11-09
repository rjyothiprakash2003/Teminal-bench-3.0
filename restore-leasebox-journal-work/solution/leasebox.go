package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
	"math/big"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
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
	expireLen   int
	nonceOff    int
	nonceLen    int
	crcOff      int
	crcLen      int
	exitDup     int
	exitFull    int
	exitCRC     int
}

type conf struct {
	maxLeases int
	ttlSec    int
}

type rec struct {
	raw []byte
}

var errTrunc = errors.New("truncated journal")

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
		magic:       kv["MAGIC"],
		version:     byte(atoi(kv, "VERSION", 1)),
		recordBytes: atoi(kv, "RECORD_BYTES", 64),
		nameOff:     atoi(kv, "NAME_OFF", 6),
		nameLen:     atoi(kv, "NAME_LEN", 16),
		ownerOff:    atoi(kv, "OWNER_OFF", 22),
		ownerLen:    atoi(kv, "OWNER_LEN", 16),
		expireOff:   atoi(kv, "EXPIRE_OFF", 38),
		expireLen:   atoi(kv, "EXPIRE_LEN", 8),
		nonceOff:    atoi(kv, "NONCE_OFF", 46),
		nonceLen:    atoi(kv, "NONCE_LEN", 8),
		crcOff:      atoi(kv, "CRC_OFF", 54),
		crcLen:      atoi(kv, "CRC_LEN", 4),
		exitDup:     atoi(kv, "EXIT_DUP", 3),
		exitFull:    atoi(kv, "EXIT_FULL", 4),
		exitCRC:     atoi(kv, "EXIT_CRC", 2),
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

func seqPath() string {
	return journalPath() + ".seq"
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

func stored(s string, n int) string {
	return strings.TrimRight(string(pad(s, n)), "\x00")
}

func fieldStr(raw []byte, off, n int) string {
	return strings.TrimRight(string(raw[off:off+n]), "\x00")
}

func putBE(raw []byte, off, width int, v uint64) {
	for i := width - 1; i >= 0; i-- {
		raw[off+i] = byte(v)
		v >>= 8
	}
}

func putSignedMod(raw []byte, off, width int, v int64) {
	putBigMod(raw, off, width, new(big.Int).SetInt64(v))
}

func putBigMod(raw []byte, off, width int, v *big.Int) {
	if width <= 0 || off < 0 || off+width > len(raw) || v == nil {
		return
	}
	bi := new(big.Int).Set(v)
	mod := new(big.Int).Lsh(big.NewInt(1), uint(8*width))
	bi.Mod(bi, mod)
	if bi.Sign() < 0 {
		bi.Add(bi, mod)
	}
	copy(raw[off:off+width], bi.FillBytes(make([]byte, width)))
}

func fieldInt(raw []byte, off, width int) *big.Int {
	if width <= 0 || off < 0 || off+width > len(raw) {
		return big.NewInt(0)
	}
	return new(big.Int).SetBytes(raw[off : off+width])
}

func getBE(raw []byte, off, width int) uint64 {
	n := fieldInt(raw, off, width)
	if n.BitLen() > 64 {
		return ^uint64(0)
	}
	return n.Uint64()
}

func putCRC(rl rules, raw []byte) {
	sum := crc32.ChecksumIEEE(raw[:rl.crcOff])
	putBE(raw, rl.crcOff, rl.crcLen, uint64(sum))
}

func crcOK(rl rules, raw []byte) bool {
	if len(raw) < rl.crcOff+rl.crcLen {
		return false
	}
	want := new(big.Int).SetUint64(uint64(crc32.ChecksumIEEE(raw[:rl.crcOff])))
	return fieldInt(raw, rl.crcOff, rl.crcLen).Cmp(want) == 0
}

func magicBytes(rl rules) []byte {
	return []byte(rl.magic)
}

func verOff(rl rules) int {
	return len(magicBytes(rl))
}

func flagOff(rl rules) int {
	return len(magicBytes(rl)) + 1
}

func putHeader(rl rules, raw []byte, active byte) {
	mb := magicBytes(rl)
	copy(raw, mb)
	vo, fo := verOff(rl), flagOff(rl)
	if vo < len(raw) && vo < rl.nameOff {
		raw[vo] = rl.version
	}
	if fo < len(raw) && fo < rl.nameOff {
		raw[fo] = active
	}
}

func magicOK(rl rules, raw []byte) bool {
	mb := magicBytes(rl)
	if len(mb) == 0 || len(raw) < len(mb) {
		return false
	}
	return string(raw[:len(mb)]) == rl.magic
}

func versionOK(rl rules, raw []byte) bool {
	vo := verOff(rl)
	if vo >= len(raw) {
		return false
	}
	if vo >= rl.nameOff {
		return true
	}
	return raw[vo] == rl.version
}

func expireOf(rl rules, raw []byte) uint64 {
	return getBE(raw, rl.expireOff, rl.expireLen)
}

func nonceKey(raw []byte, off, width int) string {
	if width <= 0 || off < 0 || off+width > len(raw) {
		return ""
	}
	return string(raw[off : off+width])
}

func nonceMod(width int) *big.Int {
	if width <= 0 {
		return big.NewInt(0)
	}
	return new(big.Int).Lsh(big.NewInt(1), uint(8*width))
}

func isActive(raw []byte, now int64, rl rules) bool {
	if len(raw) < rl.recordBytes {
		return false
	}
	fo := flagOff(rl)
	if fo < rl.nameOff {
		if fo >= len(raw) || raw[fo] != 1 {
			return false
		}
	}
	return fieldInt(raw, rl.expireOff, rl.expireLen).Cmp(big.NewInt(now)) > 0
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
	if rl.recordBytes <= 0 {
		return nil, errTrunc
	}
	if len(raw)%rl.recordBytes != 0 {
		return nil, errTrunc
	}
	var out []rec
	for i := 0; i < len(raw); i += rl.recordBytes {
		chunk := make([]byte, rl.recordBytes)
		copy(chunk, raw[i:i+rl.recordBytes])
		out = append(out, rec{raw: chunk})
	}
	return out, nil
}

func syncDir(path string) error {
	d, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

func publish(tmp, dest string, f *os.File, durable bool) error {
	if durable {
		if err := f.Sync(); err != nil {
			f.Close()
			return err
		}
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, dest); err != nil {
		return err
	}
	if durable {
		return syncDir(dest)
	}
	return nil
}

func writeAll(rows []rec, durable bool) error {
	p := journalPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		return err
	}
	var buf []byte
	for _, r := range rows {
		buf = append(buf, r.raw...)
	}
	tmp := p + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o666)
	if err != nil {
		return err
	}
	if _, err := f.Write(buf); err != nil {
		f.Close()
		return err
	}
	return publish(tmp, p, f, durable)
}

func bitsetBytes(width int) int {
	if width <= 0 || width > 2 {
		return 0
	}
	return 1 << (8*width - 3)
}

func loadBits(rl rules) []byte {
	n := bitsetBytes(rl.nonceLen)
	if n <= 0 {
		return nil
	}
	buf := make([]byte, n)
	raw, err := os.ReadFile(seqPath())
	if err == nil && len(raw) > 0 {
		copy(buf, raw[:min(len(raw), n)])
	}
	return buf
}

func issued(rl rules, rows []rec) map[string]struct{} {
	used := map[string]struct{}{}
	w := rl.nonceLen
	if n := bitsetBytes(w); n > 0 {
		bits := loadBits(rl)
		space := 1 << (8 * w)
		for i := 0; i < space && i/8 < len(bits); i++ {
			if bits[i/8]&(1<<(uint(i)%8)) != 0 {
				used[string(big.NewInt(int64(i)).FillBytes(make([]byte, w)))] = struct{}{}
			}
		}
	} else {
		raw, err := os.ReadFile(seqPath())
		if err == nil && w > 0 {
			if len(raw)%w != 0 {
				return used
			}
			for i := 0; i+w <= len(raw); i += w {
				used[string(raw[i : i+w])] = struct{}{}
			}
		}
	}
	for _, r := range rows {
		used[nonceKey(r.raw, rl.nonceOff, rl.nonceLen)] = struct{}{}
	}
	return used
}

func writeSeq(buf []byte, durable bool) error {
	p := seqPath()
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		return err
	}
	tmp := p + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o666)
	if err != nil {
		return err
	}
	if _, err := f.Write(buf); err != nil {
		f.Close()
		return err
	}
	return publish(tmp, p, f, durable)
}

func seqBad(rl rules) error {
	raw, err := os.ReadFile(seqPath())
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if n := bitsetBytes(rl.nonceLen); n > 0 {
		if len(raw) != 0 && len(raw) != n {
			return errTrunc
		}
		return nil
	}
	if rl.nonceLen <= 0 {
		return nil
	}
	if len(raw)%rl.nonceLen != 0 {
		return errTrunc
	}
	return nil
}

func persistIssued(rl rules, rows []rec, extra *big.Int, durable bool) error {
	if n := bitsetBytes(rl.nonceLen); n > 0 {
		bits := loadBits(rl)
		space := uint64(1 << (8 * rl.nonceLen))
		add := func(v *big.Int) {
			if v == nil || !v.IsUint64() {
				return
			}
			idx := v.Uint64()
			if idx < space {
				bits[idx/8] |= 1 << (idx % 8)
			}
		}
		for _, r := range rows {
			add(fieldInt(r.raw, rl.nonceOff, rl.nonceLen))
		}
		add(extra)
		return writeSeq(bits, durable)
	}
	if rl.nonceLen <= 0 {
		return nil
	}
	raw, err := os.ReadFile(seqPath())
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if rem := len(raw) % rl.nonceLen; rem != 0 {
		return errTrunc
	}
	seen := map[string]struct{}{}
	for i := 0; i+rl.nonceLen <= len(raw); i += rl.nonceLen {
		seen[string(raw[i:i+rl.nonceLen])] = struct{}{}
	}
	addWide := func(v *big.Int) {
		if v == nil {
			return
		}
		key := string(v.FillBytes(make([]byte, rl.nonceLen)))
		if _, ok := seen[key]; ok {
			return
		}
		seen[key] = struct{}{}
		raw = append(raw, []byte(key)...)
	}
	for _, r := range rows {
		addWide(fieldInt(r.raw, rl.nonceOff, rl.nonceLen))
	}
	addWide(extra)
	if len(raw) == 0 && extra == nil && len(rows) == 0 {
		return nil
	}
	return writeSeq(raw, true)
}

func rememberNonce(rl rules, v *big.Int) error {
	if rl.nonceLen <= 0 || v == nil {
		return nil
	}
	if n := bitsetBytes(rl.nonceLen); n > 0 {
		return persistIssued(rl, nil, v, true)
	}
	if err := os.MkdirAll(filepath.Dir(seqPath()), 0o777); err != nil {
		return err
	}
	raw, err := os.ReadFile(seqPath())
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	if rem := len(raw) % rl.nonceLen; rem != 0 {
		return errTrunc
	}
	raw = append(raw, v.FillBytes(make([]byte, rl.nonceLen))...)
	return writeSeq(raw, true)
}

func nextNonce(rl rules, rows []rec) (*big.Int, bool) {
	if rl.nonceLen <= 0 {
		return nil, false
	}
	if n := bitsetBytes(rl.nonceLen); n > 0 {
		bits := loadBits(rl)
		space := 1 << (8 * rl.nonceLen)
		for _, r := range rows {
			v := fieldInt(r.raw, rl.nonceOff, rl.nonceLen)
			if v.IsUint64() {
				idx := v.Uint64()
				if idx < uint64(space) {
					bits[idx/8] |= 1 << (idx % 8)
				}
			}
		}
		for i := 0; i < len(bits) && i*8 < space; i++ {
			if bits[i] == 0xff {
				continue
			}
			for b := 0; b < 8 && i*8+b < space; b++ {
				if bits[i]&(1<<uint(b)) == 0 {
					return big.NewInt(int64(i*8 + b)), true
				}
			}
		}
		return nil, false
	}
	used := issued(rl, rows)
	limit := nonceMod(rl.nonceLen)
	if limit.Sign() <= 0 {
		return nil, false
	}
	if limit.IsInt64() && int64(len(used)) >= limit.Int64() {
		return nil, false
	}
	cand := big.NewInt(int64(len(used)))
	if cand.Cmp(limit) < 0 {
		key := string(cand.FillBytes(make([]byte, rl.nonceLen)))
		if _, ok := used[key]; !ok {
			return cand, true
		}
	}
	cand.SetInt64(0)
	one := big.NewInt(1)
	for cand.Cmp(limit) < 0 {
		key := string(cand.FillBytes(make([]byte, rl.nonceLen)))
		if _, ok := used[key]; !ok {
			return new(big.Int).Set(cand), true
		}
		cand.Add(cand, one)
	}
	return nil, false
}

func journalBad(rl rules, rows []rec, err error) int {
	if err != nil {
		if errors.Is(err, errTrunc) {
			return rl.exitCRC
		}
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	for _, r := range rows {
		if !crcOK(rl, r.raw) || !magicOK(rl, r.raw) || !versionOK(rl, r.raw) {
			return rl.exitCRC
		}
	}
	return 0
}

func locked(fn func() int) int {
	path := journalPath() + ".lock"
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o666)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer f.Close()
	// Honor the instruction's 20s per-command limit: never block forever on LOCK_EX.
	deadline := time.Now().Add(20 * time.Second)
	for {
		if !time.Now().Before(deadline) {
			fmt.Fprintln(os.Stderr, "lock timeout")
			return 1
		}
		err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			if time.Now().After(deadline) {
				_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
				fmt.Fprintln(os.Stderr, "lock timeout")
				return 1
			}
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	return fn()
}

func grant(name, owner string) int {
	return locked(func() int {
		rl := loadRules()
		cf := loadConf()
		now := nowUnix()
		rows, err := readAll(rl)
		if code := journalBad(rl, rows, err); code != 0 {
			return code
		}
		if err := seqBad(rl); err != nil {
			return 1
		}
		wantName := stored(name, rl.nameLen)
		active := 0
		for _, r := range rows {
			if isActive(r.raw, now, rl) {
				if fieldStr(r.raw, rl.nameOff, rl.nameLen) == wantName {
					return rl.exitDup
				}
				active++
			}
		}
		if active >= cf.maxLeases {
			return rl.exitFull
		}
		nonce, ok := nextNonce(rl, rows)
		if !ok {
			return 1
		}
		raw := make([]byte, rl.recordBytes)
		putHeader(rl, raw, 1)
		copy(raw[rl.nameOff:rl.nameOff+rl.nameLen], pad(name, rl.nameLen))
		copy(raw[rl.ownerOff:rl.ownerOff+rl.ownerLen], pad(owner, rl.ownerLen))
		exp := new(big.Int).SetInt64(now)
		exp.Add(exp, big.NewInt(int64(cf.ttlSec)))
		putBigMod(raw, rl.expireOff, rl.expireLen, exp)
		putBigMod(raw, rl.nonceOff, rl.nonceLen, nonce)
		putCRC(rl, raw)
		rows = append(rows, rec{raw: raw})
		if err := writeAll(rows, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		_ = persistIssued(rl, rows, nonce, true)
		return 0
	})
}

func revoke(name, owner string) int {
	_ = owner
	return locked(func() int {
		rl := loadRules()
		rows, err := readAll(rl)
		if code := journalBad(rl, rows, err); code != 0 {
			return code
		}
		if err := seqBad(rl); err != nil {
			return 1
		}
		if err := persistIssued(rl, rows, nil, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		wantName := stored(name, rl.nameLen)
		keep := make([]rec, 0, len(rows))
		for _, r := range rows {
			if fieldStr(r.raw, rl.nameOff, rl.nameLen) != wantName {
				keep = append(keep, r)
			}
		}
		if err := writeAll(keep, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		return 0
	})
}

func compact() int {
	return locked(func() int {
		rl := loadRules()
		now := nowUnix()
		rows, err := readAll(rl)
		if code := journalBad(rl, rows, err); code != 0 {
			return code
		}
		if err := seqBad(rl); err != nil {
			return 1
		}
		if err := persistIssued(rl, rows, nil, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		keep := make([]rec, 0, len(rows))
		for _, r := range rows {
			if isActive(r.raw, now, rl) {
				keep = append(keep, r)
			}
		}
		if err := writeAll(keep, true); err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		return 0
	})
}

func list() int {
	return locked(func() int {
		rl := loadRules()
		now := nowUnix()
		rows, err := readAll(rl)
		if code := journalBad(rl, rows, err); code != 0 {
			return code
		}
		type item struct {
			Name    string      `json:"name"`
			Owner   string      `json:"owner"`
			Expires json.Number `json:"expires"`
			Nonce   json.Number `json:"nonce"`
		}
		var live []item
		for _, r := range rows {
			if isActive(r.raw, now, rl) {
				live = append(live, item{
					Name:    fieldStr(r.raw, rl.nameOff, rl.nameLen),
					Owner:   fieldStr(r.raw, rl.ownerOff, rl.ownerLen),
					Expires: json.Number(fieldInt(r.raw, rl.expireOff, rl.expireLen).String()),
					Nonce:   json.Number(fieldInt(r.raw, rl.nonceOff, rl.nonceLen).String()),
				})
			}
		}
		if len(live) == 0 {
			if _, err := os.Stdout.WriteString("[]\n"); err != nil {
				return 1
			}
			return 0
		}
		payload := struct {
			Leases []item `json:"leases"`
		}{Leases: live}
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		if err := enc.Encode(payload); err != nil {
			return 1
		}
		return 0
	})
}

func verify() int {
	return locked(func() int {
		rl := loadRules()
		rows, err := readAll(rl)
		if err != nil {
			return rl.exitCRC
		}
		for _, r := range rows {
			if !crcOK(rl, r.raw) || !magicOK(rl, r.raw) || !versionOK(rl, r.raw) {
				return rl.exitCRC
			}
		}
		return 0
	})
}

func report() int {
	return locked(func() int {
		rl := loadRules()
		now := nowUnix()
		rows, err := readAll(rl)
		if code := journalBad(rl, rows, err); code != 0 {
			return code
		}
		active := 0
		for _, r := range rows {
			if isActive(r.raw, now, rl) {
				active++
			}
		}
		body, err := json.Marshal(map[string]any{
			"magic":   rl.magic,
			"records": len(rows),
			"active":  active,
		})
		if err != nil {
			return 1
		}
		if err := os.MkdirAll("/app/out", 0o777); err != nil {
			return 1
		}
		return writeErr(os.WriteFile("/app/out/lease-report.json", append(body, '\n'), 0o666))
	})
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
		fmt.Fprintln(os.Stderr, "usage: leasebox grant|revoke|list|compact|verify|report")
		os.Exit(1)
	}
	cmd := os.Args[1]
	code := 1
	switch cmd {
	case "grant":
		if len(os.Args) != 4 {
			fmt.Fprintln(os.Stderr, "usage: leasebox grant NAME OWNER")
			os.Exit(1)
		}
		code = grant(os.Args[2], os.Args[3])
	case "revoke":
		if len(os.Args) != 4 {
			fmt.Fprintln(os.Stderr, "usage: leasebox revoke NAME OWNER")
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
	default:
		fmt.Fprintln(os.Stderr, "unknown command")
	}
	os.Exit(code)
}
