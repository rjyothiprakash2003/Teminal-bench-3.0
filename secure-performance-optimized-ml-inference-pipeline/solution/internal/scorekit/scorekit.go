package scorekit

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"

	"kelso.internal/ridgecast/internal/ridgeio"
)

type Application struct {
	ID string
	X  []float64
}

type Kit struct {
	Schema ridgeio.Schema
	W      ridgeio.Weights
	Token  string
	TokenH string
	Limit  int
	mu     sync.Mutex
	cache  map[string]int64
}

type ScoreRow struct {
	ID          string `json:"id"`
	ScoreMicros int64  `json:"score_micros"`
}

func New(schema ridgeio.Schema, w ridgeio.Weights, token, tokenSHA string, limit int) *Kit {
	return &Kit{
		Schema: schema,
		W:      w,
		Token:  token,
		TokenH: tokenSHA,
		Limit:  limit,
		cache:  map[string]int64{},
	}
}

func (k *Kit) AuthOK(header string) bool {
	const p = "Bearer "
	if !strings.HasPrefix(header, p) {
		return false
	}
	got := header[len(p):]
	// Exact token match: rejects suffixes, tabs, and extra spaces after Bearer.
	if got != k.Token {
		return false
	}
	sum := sha256.Sum256([]byte(got))
	return hex.EncodeToString(sum[:]) == k.TokenH
}

func Micros(p float64) int64 {
	return int64(math.Floor(p*1000000 + 0.5))
}

func (k *Kit) LogitOn(x []float64) float64 {
	s := k.W.BiasScalar
	for i := range x {
		z := (x[i] - k.W.Mean[i]) / k.W.Std[i]
		s += k.W.W[i] * z
	}
	return s
}

func (k *Kit) Prob(x []float64) float64 {
	return 1.0 / (1.0 + math.Exp(-k.LogitOn(x)))
}

func (k *Kit) cacheKey(x []float64) string {
	parts := make([]string, len(k.Schema.Fields))
	for i, f := range k.Schema.Fields {
		// Lossless float64 key ('b') so distinct legal vectors never collide.
		parts[i] = f.Name + "=" + strconv.FormatFloat(x[i], 'b', -1, 64)
	}
	return strings.Join(parts, ",")
}

func (k *Kit) Parse(body []byte) (Application, error) {
	// Reject non-UTF-8 before decode: encoding/json would otherwise replace
	// invalid bytes (e.g. 0xff inside a string) with U+FFFD and accept the body.
	if !utf8.Valid(body) {
		return Application{}, fmt.Errorf("intake utf8")
	}
	// Decode the raw bytes. Do not TrimSpace first: non-JSON whitespace such as
	// vertical tab must keep the record illegal. Trailing JSON whitespace
	// (space/tab/LF/CR) after one object is allowed; any other trailing byte is not.
	dec := json.NewDecoder(bytes.NewReader(body))
	var raw map[string]json.RawMessage
	if err := dec.Decode(&raw); err != nil {
		return Application{}, fmt.Errorf("intake json")
	}
	rest := body[int(dec.InputOffset()):]
	for _, c := range rest {
		if c != ' ' && c != '\t' && c != '\n' && c != '\r' {
			return Application{}, fmt.Errorf("trailing")
		}
	}
	app := Application{X: make([]float64, len(k.Schema.Fields))}
	names := make([]string, 0, len(raw))
	for name := range raw {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if name == "id" {
			continue
		}
		if _, bad := k.Schema.Forbidden[name]; bad {
			return Application{}, fmt.Errorf("forbidden")
		}
		if _, ok := k.Schema.FieldIndex[name]; !ok {
			return Application{}, fmt.Errorf("unknown")
		}
	}
	idRaw, ok := raw["id"]
	if !ok {
		return Application{}, fmt.Errorf("id")
	}
	if err := json.Unmarshal(idRaw, &app.ID); err != nil || app.ID == "" {
		return Application{}, fmt.Errorf("id")
	}
	for i, f := range k.Schema.Fields {
		blob, ok := raw[f.Name]
		if !ok {
			return Application{}, fmt.Errorf("missing")
		}
		trimmed := strings.TrimSpace(string(blob))
		if trimmed == "" || trimmed == "null" || trimmed == "true" || trimmed == "false" {
			return Application{}, fmt.Errorf("field")
		}
		if strings.HasPrefix(trimmed, "[") || strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "\"") {
			return Application{}, fmt.Errorf("field")
		}
		// Range-check the exact JSON number text before float64 conversion so
		// values that round onto an endpoint (e.g. 2.5000000000000001 → 2.5)
		// or underflow (e.g. -1e-400 → -0) are still rejected.
		v, ok := exactInRange(trimmed, f.Min, f.Max)
		if !ok {
			return Application{}, fmt.Errorf("range")
		}
		app.X[i] = v
	}
	return app, nil
}

func exactInRange(tok string, min, max float64) (float64, bool) {
	// Exact rational compare: finite-precision binary floats can round a value
	// strictly above max onto the endpoint (e.g. 2.5 + 10^-82 → 2.5 at 256 bits).
	tok = strings.TrimSpace(tok)
	if tok == "" {
		return 0, false
	}
	minR := new(big.Rat).SetFloat64(min)
	maxR := new(big.Rat).SetFloat64(max)

	r := new(big.Rat)
	if _, ok := r.SetString(tok); ok {
		if r.Cmp(minR) < 0 || r.Cmp(maxR) > 0 {
			return 0, false
		}
		// Direct rational → binary64 (no intermediate 64-bit big.Float rounding).
		v, _ := r.Float64()
		if math.IsInf(v, 0) || math.IsNaN(v) {
			return 0, false
		}
		if v < min || v > max {
			return 0, false
		}
		return v, true
	}

	// SetString rejects some scientific forms (extreme exponents). Normalize the
	// mantissa to an integer × 10^exp and compare without substituting a capped exp.
	mant, exp, ok := parseSciMantExp(tok)
	if !ok {
		return 0, false
	}
	if cmpIntPow10(mant, exp, minR) < 0 || cmpIntPow10(mant, exp, maxR) > 0 {
		return 0, false
	}
	rr, ok := ratFromMantExp(mant, exp)
	if !ok {
		if mant.Sign() < 0 {
			return math.Copysign(0, -1), true
		}
		return 0, true
	}
	v, _ := rr.Float64()
	if math.IsInf(v, 0) || math.IsNaN(v) {
		return 0, false
	}
	if v < min || v > max {
		return 0, false
	}
	return v, true
}

// maxMaterializeExp bounds 10^|e| construction for intake-sized numbers.
var maxMaterializeExp = big.NewInt(2000000)

func parseSciMantExp(tok string) (*big.Int, *big.Int, bool) {
	i := strings.IndexAny(tok, "eE")
	if i <= 0 || i == len(tok)-1 {
		return nil, nil, false
	}
	expStr := tok[i+1:]
	sign := 1
	if expStr[0] == '+' {
		expStr = expStr[1:]
	} else if expStr[0] == '-' {
		sign = -1
		expStr = expStr[1:]
	}
	if expStr == "" {
		return nil, nil, false
	}
	exp, ok := new(big.Int).SetString(expStr, 10)
	if !ok {
		return nil, nil, false
	}
	if sign < 0 {
		exp.Neg(exp)
	}
	intStr, exp, ok := normalizeDecimalMantissa(tok[:i], exp)
	if !ok {
		return nil, nil, false
	}
	if intStr == "0" || intStr == "-0" {
		return big.NewInt(0), big.NewInt(0), true
	}
	mant, ok := new(big.Int).SetString(intStr, 10)
	if !ok {
		return nil, nil, false
	}
	return mant, exp, true
}

// normalizeDecimalMantissa folds the decimal point and trailing zeros into exp
// so the mantissa is a minimal integer digit string.
func normalizeDecimalMantissa(mantStr string, exp *big.Int) (string, *big.Int, bool) {
	e := new(big.Int).Set(exp)
	sign := ""
	body := mantStr
	if body != "" && (body[0] == '+' || body[0] == '-') {
		if body[0] == '-' {
			sign = "-"
		}
		body = body[1:]
	}
	if body == "" {
		return "", nil, false
	}
	dot := strings.IndexByte(body, '.')
	var intDigits, frac string
	if dot < 0 {
		intDigits, frac = body, ""
	} else {
		intDigits, frac = body[:dot], body[dot+1:]
		if strings.ContainsRune(frac, '.') {
			return "", nil, false
		}
	}
	for _, c := range intDigits + frac {
		if c < '0' || c > '9' {
			return "", nil, false
		}
	}
	digits := intDigits + frac
	if len(frac) > 0 {
		e.Sub(e, big.NewInt(int64(len(frac))))
	}
	for len(digits) > 1 && digits[0] == '0' {
		digits = digits[1:]
	}
	for len(digits) > 1 && digits[len(digits)-1] == '0' {
		digits = digits[:len(digits)-1]
		e.Add(e, big.NewInt(1))
	}
	if digits == "" || digits == "0" {
		return "0", big.NewInt(0), true
	}
	return sign + digits, e, true
}

func ratFromMantExp(mant, exp *big.Int) (*big.Rat, bool) {
	if mant.Sign() == 0 {
		return big.NewRat(0, 1), true
	}
	abs := new(big.Int).Abs(exp)
	if abs.Cmp(maxMaterializeExp) > 0 {
		return nil, false
	}
	pow := new(big.Int).Exp(big.NewInt(10), abs, nil)
	out := new(big.Rat).SetInt(mant)
	if exp.Sign() >= 0 {
		out.Mul(out, new(big.Rat).SetInt(pow))
	} else {
		out.Mul(out, new(big.Rat).SetFrac(big.NewInt(1), pow))
	}
	return out, true
}

// cmpIntPow10 compares mant×10^exp with bound (as Rat). Returns -1/0/1.
func cmpIntPow10(mant, exp *big.Int, bound *big.Rat) int {
	if mant.Sign() == 0 {
		return big.NewRat(0, 1).Cmp(bound)
	}
	p := new(big.Int).Set(bound.Num())
	q := new(big.Int).Set(bound.Denom())
	leftCoeff := new(big.Int).Mul(new(big.Int).Set(mant), q)
	leftExp := new(big.Int).Set(exp)
	rightCoeff := new(big.Int).Set(p)
	rightExp := big.NewInt(0)
	if leftExp.Sign() < 0 {
		rightExp.Sub(rightExp, leftExp)
		leftExp.SetInt64(0)
	}
	if rightExp.Sign() < 0 {
		leftExp.Sub(leftExp, rightExp)
		rightExp.SetInt64(0)
	}
	if leftExp.Cmp(rightExp) >= 0 {
		leftExp.Sub(leftExp, rightExp)
		rightExp.SetInt64(0)
	} else {
		rightExp.Sub(rightExp, leftExp)
		leftExp.SetInt64(0)
	}
	left := scaleByPow10(leftCoeff, leftExp)
	right := scaleByPow10(rightCoeff, rightExp)
	if left == nil || right == nil {
		return cmpHugePow(leftCoeff, leftExp, rightCoeff, rightExp)
	}
	return left.Cmp(right)
}

func scaleByPow10(coeff, e *big.Int) *big.Int {
	if e.Sign() == 0 {
		return new(big.Int).Set(coeff)
	}
	if e.Sign() < 0 || e.Cmp(maxMaterializeExp) > 0 {
		return nil
	}
	if coeff.Sign() == 0 {
		return big.NewInt(0)
	}
	pow := new(big.Int).Exp(big.NewInt(10), e, nil)
	return new(big.Int).Mul(coeff, pow)
}

func cmpHugePow(leftCoeff, leftExp, rightCoeff, rightExp *big.Int) int {
	leftHuge := leftExp.Sign() > 0 && leftExp.Cmp(maxMaterializeExp) > 0
	rightHuge := rightExp.Sign() > 0 && rightExp.Cmp(maxMaterializeExp) > 0
	if leftHuge && !rightHuge {
		if leftCoeff.Sign() == 0 {
			return -signOf(rightCoeff)
		}
		return leftCoeff.Sign()
	}
	if rightHuge && !leftHuge {
		if rightCoeff.Sign() == 0 {
			return signOf(leftCoeff)
		}
		return -rightCoeff.Sign()
	}
	// Both huge or both residual — compare coefficients if exps equal, else the
	// larger exp dominates when coeffs share sign.
	if leftExp.Cmp(rightExp) == 0 {
		return leftCoeff.Cmp(rightCoeff)
	}
	if leftExp.Cmp(rightExp) > 0 {
		if leftCoeff.Sign() == 0 {
			return -signOf(rightCoeff)
		}
		return leftCoeff.Sign()
	}
	if rightCoeff.Sign() == 0 {
		return signOf(leftCoeff)
	}
	return -rightCoeff.Sign()
}

func signOf(z *big.Int) int {
	if z == nil {
		return 0
	}
	return z.Sign()
}

func (k *Kit) ScoreOne(app Application) int64 {
	key := k.cacheKey(app.X)
	k.mu.Lock()
	defer k.mu.Unlock()
	if v, ok := k.cache[key]; ok {
		return v
	}
	m := Micros(k.Prob(app.X))
	k.cache[key] = m
	return m
}

func (k *Kit) ScoreChunk(apps []Application) []int64 {
	out := make([]int64, len(apps))
	for i, app := range apps {
		local := Application{ID: app.ID, X: append([]float64(nil), app.X...)}
		out[i] = k.ScoreOne(local)
	}
	return out
}

func (k *Kit) ScoreSequence(apps []Application) []ScoreRow {
	rows := make([]ScoreRow, 0, len(apps))
	limit := k.Limit
	if limit < 1 {
		limit = 1
	}
	for i := 0; i < len(apps); i += limit {
		j := i + limit
		if j > len(apps) {
			j = len(apps)
		}
		chunk := apps[i:j]
		micros := k.ScoreChunk(chunk)
		for n := range chunk {
			rows = append(rows, ScoreRow{ID: chunk[n].ID, ScoreMicros: micros[n]})
		}
	}
	return rows
}
