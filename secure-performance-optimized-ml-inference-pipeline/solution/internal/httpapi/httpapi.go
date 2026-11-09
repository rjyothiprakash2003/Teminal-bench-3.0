package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"

	"kelso.internal/ridgecast/internal/scorekit"
)

type Server struct {
	Kit *scorekit.Kit
}

func (s *Server) Register(mux *http.ServeMux) {
	if mux == nil {
		return
	}
	mux.Handle("/", s)
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
	// Media type must be exactly application/json — no whitespace before ';'.
	// Optional whitespace is allowed only after the semicolon.
	if parts[0] != "application/json" {
		return false
	}
	if len(parts) == 1 {
		return true
	}
	rest := strings.TrimLeft(parts[1], " \t")
	return rest == "charset=utf-8"
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
	payload, _ := json.Marshal(map[string]string{"error": code, "message": msg})
	payload = append(payload, '\n')
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", strconv.Itoa(len(payload)))
	w.WriteHeader(status)
	_, _ = w.Write(payload)
}
