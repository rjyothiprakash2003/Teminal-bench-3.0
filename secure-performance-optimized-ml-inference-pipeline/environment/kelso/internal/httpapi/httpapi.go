package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"kelso.internal/ridgecast/internal/scorekit"
)

type Server struct {
	Kit *scorekit.Kit
}

func (s *Server) Register(mux *http.ServeMux) {
	_ = mux
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.route(w, r)
}

func (s *Server) route(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/v1/health":
		s.health(w, r)
	case "/v1/score":
		s.score(w, r)
	default:
		s.fail(w, http.StatusNotFound, "NOT_FOUND", "no such route")
	}
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.fail(w, http.StatusMethodNotAllowed, "METHOD", "GET required")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func jsonContentType(h string) bool {
	parts := strings.SplitN(h, ";", 2)
	media := strings.Trim(parts[0], " \t")
	if media != "application/json" {
		return false
	}
	if len(parts) == 1 {
		return true
	}
	// Remainder after the first semicolon must be parameters (name=value), not
	// an arbitrary non-parameter suffix such as "; garbage". Semicolons inside
	// quoted parameter values are not separators.
	rest := strings.Trim(parts[1], " \t")
	if rest == "" {
		return false
	}
	for _, param := range splitHeaderParams(rest) {
		param = strings.Trim(param, " \t")
		if param == "" || !strings.Contains(param, "=") {
			return false
		}
		name, _, _ := strings.Cut(param, "=")
		name = strings.Trim(name, " \t")
		if name == "" || !tokenHeaderName(name) {
			return false
		}
	}
	return true
}

// tokenHeaderName reports whether s is an RFC 7230 token (no interior spaces).
func tokenHeaderName(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
			continue
		case strings.ContainsRune("!#$%&'*+-.^_`|~", rune(c)):
			continue
		default:
			return false
		}
	}
	return true
}

// splitHeaderParams splits on ';' outside double quotes; honors quoted-pair escapes.
func splitHeaderParams(rest string) []string {
	var out []string
	var b strings.Builder
	inQuote := false
	escape := false
	for i := 0; i < len(rest); i++ {
		c := rest[i]
		if escape {
			b.WriteByte(c)
			escape = false
			continue
		}
		if inQuote && c == '\\' {
			b.WriteByte(c)
			escape = true
			continue
		}
		if c == '"' {
			inQuote = !inQuote
			b.WriteByte(c)
			continue
		}
		if c == ';' && !inQuote {
			out = append(out, b.String())
			b.Reset()
			continue
		}
		b.WriteByte(c)
	}
	// Always append the final segment — a trailing ';' yields an empty
	// component which jsonContentType rejects (P598).
	out = append(out, b.String())
	return out
}

func (s *Server) score(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		s.fail(w, http.StatusMethodNotAllowed, "METHOD", "POST required")
		return
	}
	if !s.Kit.AuthOK(r.Header.Get("Authorization")) {
		s.fail(w, http.StatusUnauthorized, "UNAUTHORIZED", "bearer required")
		return
	}
	if !jsonContentType(r.Header.Get("Content-Type")) {
		s.fail(w, http.StatusBadRequest, "INVALID_INTAKE", "json content type required")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 1048576+1))
	if err != nil {
		s.fail(w, http.StatusBadRequest, "INVALID_INTAKE", "unreadable body")
		return
	}
	if len(body) > 1048576 {
		s.fail(w, http.StatusBadRequest, "INVALID_INTAKE", "body too large")
		return
	}
	app, err := s.Kit.Parse(body)
	if err != nil {
		s.fail(w, http.StatusBadRequest, "INVALID_INTAKE", "intake rejected")
		return
	}
	row := s.Kit.ScoreSequence([]scorekit.Application{app})[0]
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(map[string]any{"id": row.ID, "score_micros": row.ScoreMicros})
}

func (s *Server) fail(w http.ResponseWriter, status int, code, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": code, "message": msg})
}
