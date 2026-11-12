package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/http/httputil"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

const fixtureID = "10000000-0000-4000-8000-00000000000"
const coursePath = "/api/v1/courses/" + fixtureID + "6/sections"
const enrollPath = "/api/v1/sections/" + fixtureID + "8/enrollments"
const termQuery = "termId=" + fixtureID + "2"

var withdrawPath = regexp.MustCompile(`^/api/v1/enrollments/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)

func permitted(method, path, query string) bool {
	switch method {
	case http.MethodGet:
		switch path {
		case "/api/v1/auth/csrf", "/api/v1/me":
			return query == ""
		case coursePath, "/api/v1/me/enrollments", "/api/v1/me/schedule":
			return query == termQuery
		}
	case http.MethodPost:
		return query == "" && (path == "/api/v1/auth/login" || path == enrollPath)
	case http.MethodDelete:
		return query == "" && withdrawPath.MatchString(path)
	}
	return false
}

var benchmarkEnroll = regexp.MustCompile(`^/api/v1/sections/90000000-0000-4000-8000-0000000000(0[1-9]|1[0-9]|20)/enrollments$`)

const requestLimit = 80000
const responseLimit = 65536
const payloadLimit = 256 * 1024 * 1024

var errGatewayBudget = errors.New("benchmark gateway budget exhausted")
var correlationID = regexp.MustCompile(`^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)

const correlationHeader = "X-Correlation-Id"

func trace(request *http.Request, event string, code int, reason string) {
	if id := request.Header.Get(correlationHeader); correlationID.MatchString(id) {
		log.Printf("trace %s %s %d %s %d", id, event, code, reason, time.Now().UnixMilli())
	}
}

func transportReason(err error) string {
	var timeout net.Error
	switch {
	case errors.Is(err, context.Canceled):
		return "canceled"
	case errors.Is(err, io.EOF):
		return "eof"
	case errors.Is(err, io.ErrUnexpectedEOF):
		return "truncated"
	case errors.As(err, &timeout) && timeout.Timeout():
		return "timeout"
	default:
		return "transport"
	}
}

func gateway(key string) http.Handler {
	return makeGateway(key, false)
}

func makeGateway(key string, benchmark bool) http.Handler {
	backend := &url.URL{Scheme: "http", Host: "127.0.0.1:8081"}
	return benchmarkProxy(key, benchmark, backend)
}

func benchmarkProxy(key string, benchmark bool, backend *url.URL) http.Handler {
	return boundedProxy(key, benchmark, backend, requestLimit, payloadLimit)
}

func boundedProxy(key string, benchmark bool, backend *url.URL, maxRequests, maxPayload int64) http.Handler {
	return upstreamProxy(key, benchmark, backend, maxRequests, maxPayload, nil, "")
}

// Tomcat can have 80 requests in flight in the benchmark. Keep a bounded
// pool across bursts instead of discarding all but two of those connections.
func loopbackHTTPTransport() *http.Transport {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.MaxIdleConns = 256
	transport.MaxIdleConnsPerHost = 256
	return transport
}

type observedTransport struct {
	next   http.RoundTripper
	fresh  atomic.Int64
	reused atomic.Int64
}

func (t *observedTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	start := time.Now()
	var acquired, written atomic.Int64
	var reused atomic.Bool
	hooks := &httptrace.ClientTrace{
		GotConn: func(info httptrace.GotConnInfo) {
			acquired.Store(time.Since(start).Microseconds() + 1)
			reused.Store(info.Reused)
			if info.Reused {
				t.reused.Add(1)
			} else {
				t.fresh.Add(1)
			}
		},
		WroteRequest: func(info httptrace.WroteRequestInfo) {
			if info.Err == nil {
				written.Store(time.Since(start).Microseconds() + 1)
			}
		},
	}
	response, err := t.next.RoundTrip(request.WithContext(httptrace.WithClientTrace(request.Context(), hooks)))
	id := request.Header.Get(correlationHeader)
	if err != nil && correlationID.MatchString(id) {
		log.Printf("sdu_upstream_failure %s %d %d %d %t", id,
			acquired.Load()-1, written.Load()-1, time.Since(start).Microseconds(), reused.Load())
	}
	return response, err
}

func (t *observedTransport) logCounts() {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for now := range ticker.C {
		log.Printf("sdu_upstream_pool %d %d %d", now.UnixMilli(), t.fresh.Load(), t.reused.Load())
	}
}

func upstreamProxy(key string, benchmark bool, backend *url.URL, maxRequests, maxPayload int64, transport http.RoundTripper, upstreamKey string) http.Handler {
	var requests, payload atomic.Int64
	proxy := &httputil.ReverseProxy{
		Transport: transport,
		Rewrite: func(request *httputil.ProxyRequest) {
			request.SetURL(backend)
			for _, name := range []string{"Authorization", "X-Sdu-Benchmark-Key", "X-Verified-User-Data", "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto"} {
				request.Out.Header.Del(name)
			}
			if upstreamKey != "" {
				request.Out.Header.Set("X-SDU-Benchmark-Key", upstreamKey)
			}
		},
		ErrorHandler: func(w http.ResponseWriter, request *http.Request, err error) {
			if errors.Is(err, errGatewayBudget) {
				trace(request, "gateway", 429, "budget")
				budgetRejected(w)
				return
			}
			if benchmark {
				w.Header().Set("X-Sdu-Benchmark-Hop", "gateway-error")
				trace(request, "gateway", 502, transportReason(err))
			}
			http.Error(w, "Backend unavailable", http.StatusBadGateway)
		},
	}
	if benchmark {
		proxy.ModifyResponse = func(response *http.Response) error {
			trace(response.Request, "backend", response.StatusCode, "headers")
			body, err := io.ReadAll(io.LimitReader(response.Body, responseLimit+1))
			response.Body.Close()
			if err != nil {
				return err
			}
			if len(body) > responseLimit || payload.Add(int64(len(body))) > maxPayload {
				return errGatewayBudget
			}
			response.Body = io.NopCloser(bytes.NewReader(body))
			response.Header.Set("X-Sdu-Benchmark-Hop", "backend")
			if upstreamKey != "" {
				response.Header.Set("X-SDU-Backend-Instance", "app1")
			}
			trace(response.Request, "gateway", response.StatusCode, "body")
			return nil
		}
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-SDU-Benchmark-Key")), []byte(key)) != 1 {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		allowed := permitted(r.Method, r.URL.Path, r.URL.RawQuery)
		if benchmark && r.Method == http.MethodPost && r.URL.RawQuery == "" {
			allowed = allowed || benchmarkEnroll.MatchString(r.URL.Path)
		}
		if benchmark && r.Method == http.MethodGet && r.URL.Path == "/api/v1/courses" && r.URL.RawQuery == termQuery {
			allowed = true
		}
		if r.URL.IsAbs() || r.URL.RawPath != "" || !allowed {
			http.NotFound(w, r)
			return
		}
		if r.ContentLength < 0 || r.ContentLength > 2048 || r.Header.Get("Upgrade") != "" {
			http.Error(w, "Unsupported request", http.StatusBadRequest)
			return
		}
		if benchmark && requests.Add(1) > maxRequests {
			budgetRejected(w)
			return
		}
		if benchmark {
			trace(r, "ingress", 0, "accepted")
		}
		proxy.ServeHTTP(w, r)
	})
}

func budgetRejected(w http.ResponseWriter) {
	w.Header().Set("X-SDU-Gateway-Limit", "1")
	http.Error(w, "Benchmark gateway limit", http.StatusTooManyRequests)
}

var metricsAllowlist = map[string]bool{
	"portal_memory_http_active": true, "portal_memory_projector_sql": true,
	"portal_memory_pending": true,
	"portal_memory_ready": true,
	"portal_memory_projected_total": true,
	"portal_memory_confirmed_total": true,
	"portal_memory_projection_failures_total": true,
	"portal_memory_confirm_seconds_count": true,
	"portal_memory_confirm_seconds_sum": true,
	"portal_memory_confirm_seconds_max": true,
	"portal_memory_projection_seconds_count": true,
	"portal_memory_projection_seconds_sum": true,
	"portal_memory_projection_seconds_max": true,
	"portal_memory_lock_wait_seconds_count": true,
	"portal_memory_lock_wait_seconds_sum": true,
	"portal_memory_lock_wait_seconds_max": true,
	"portal_memory_lock_hold_seconds_count": true,
	"portal_memory_lock_hold_seconds_sum": true,
	"portal_memory_lock_hold_seconds_max": true,

	"session_jdbc_seconds_count": true, "session_jdbc_seconds_sum": true,
	"session_redis_seconds_count": true, "session_redis_seconds_sum": true, "session_redis_seconds_bucket": true,
	"session_redis_errors_total": true, "lettuce_command_completion_seconds_bucket": true,
	"tomcat_connections_current_connections": true, "tomcat_connections_config_max_connections": true,
	"tomcat_connections_keepalive_current_connections": true, "tomcat_threads_busy_threads": true,
	"tomcat_threads_current_threads": true, "tomcat_threads_config_max_threads": true,
	"process_cpu_usage": true, "jvm_memory_used_bytes": true,
	"jvm_gc_pause_seconds_count": true, "jvm_gc_pause_seconds_sum": true,
	"hikaricp_connections_active": true, "hikaricp_connections_idle": true, "hikaricp_connections_pending": true,
	"hikaricp_connections_max": true, "hikaricp_connections_timeout_total": true,
	"hikaricp_connections_acquire_seconds_sum": true, "hikaricp_connections_acquire_seconds_count": true,
	"hikaricp_connections_usage_seconds_sum": true, "hikaricp_connections_usage_seconds_count": true,
	"enrollment_admission_wait_seconds_sum": true, "enrollment_admission_wait_seconds_count": true,
	"enrollment_connection_begin_enroll_seconds_sum": true, "enrollment_connection_begin_enroll_seconds_count": true,
	"enrollment_connection_begin_withdraw_seconds_sum": true, "enrollment_connection_begin_withdraw_seconds_count": true,
	"enrollment_connection_transaction_enroll_seconds_sum": true, "enrollment_connection_transaction_enroll_seconds_count": true,
	"enrollment_connection_transaction_withdraw_seconds_sum": true, "enrollment_connection_transaction_withdraw_seconds_count": true,
	"enrollment_section_lock_wait_seconds_sum": true, "enrollment_section_lock_wait_seconds_count": true,
	"enrollment_section_lock_hold_seconds_sum": true, "enrollment_section_lock_hold_seconds_count": true,
	"enrollment_student_lock_wait_seconds_sum": true, "enrollment_student_lock_wait_seconds_count": true,
	"enrollment_commit_seconds_sum": true, "enrollment_commit_seconds_count": true,
	"enrollment_idempotency_claim_seconds_sum": true, "enrollment_idempotency_claim_seconds_count": true,
	"enrollment_academic_checks_seconds_sum": true, "enrollment_academic_checks_seconds_count": true,
	"enrollment_decision_clock_seconds_sum": true, "enrollment_decision_clock_seconds_count": true,
	"enrollment_decision_window_seconds_sum": true, "enrollment_decision_window_seconds_count": true,
	"enrollment_withdraw_recheck_seconds_sum": true, "enrollment_withdraw_recheck_seconds_count": true,
	"enrollment_seat_write_seconds_sum": true, "enrollment_seat_write_seconds_count": true,
	"enrollment_episode_write_seconds_sum": true, "enrollment_episode_write_seconds_count": true,
	"enrollment_audit_write_seconds_sum": true, "enrollment_audit_write_seconds_count": true,
	"enrollment_idempotency_complete_seconds_sum": true, "enrollment_idempotency_complete_seconds_count": true,
	"enrollment_transaction_seconds_sum": true, "enrollment_transaction_seconds_count": true,
}

func selectedMetrics(reader io.Reader, output io.Writer) error {
	scanner := bufio.NewScanner(reader)
	for scanner.Scan() {
		line := scanner.Text()
		end := strings.IndexAny(line, "{ ")
		if end < 0 || !metricsAllowlist[line[:end]] {
			continue
		}
		if line[:end] == "jvm_memory_used_bytes" && !strings.Contains(line, `area="heap"`) {
			continue
		}
		if _, err := io.WriteString(output, line+"\n"); err != nil {
			return err
		}
	}
	return scanner.Err()
}

func scrape(port string) error {
	password := os.Getenv("PORTAL_METRICS_PASSWORD")
	if password == "" {
		return errors.New("Metrics password unavailable")
	}
	request, err := http.NewRequest(http.MethodGet, "http://127.0.0.1:"+port+"/actuator/prometheus", nil)
	if err != nil {
		return err
	}
	request.SetBasicAuth("metrics", password)
	client := &http.Client{Timeout: 2 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return errors.New("Metrics scrape failed")
	}
	return selectedMetrics(io.LimitReader(response.Body, 2*1024*1024), os.Stdout)
}

func probe(port string) error {
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get("http://127.0.0.1:" + port + "/actuator/health/readiness")
	if err != nil {
		return err
	}
	defer response.Body.Close()
	var payload struct{ Status string }
	if response.StatusCode != 200 || json.NewDecoder(response.Body).Decode(&payload) != nil || payload.Status != "UP" {
		return errors.New("Spring Boot readiness is not UP")
	}
	return nil
}

func directTransport() *http.Transport {
	pem, err := os.ReadFile("/tmp/sdu-cloud/http-ca.crt")
	roots := x509.NewCertPool()
	if err != nil || !roots.AppendCertsFromPEM(pem) {
		log.Fatal("Invalid private TLS CA")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	config := &tls.Config{RootCAs: roots, ServerName: "localhost", MinVersion: tls.VersionTLS12}
	dialer := &net.Dialer{KeepAlive: 30 * time.Second}
	transport.DialTLSContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
		defer cancel()
		socket, err := dialer.DialContext(ctx, network, address)
		if err != nil {
			return nil, err
		}
		conn := tls.Client(socket, config)
		if err := conn.HandshakeContext(ctx); err != nil {
			socket.Close()
			return nil, err
		}
		return conn, nil
	}
	transport.ForceAttemptHTTP2 = false
	transport.Protocols = new(http.Protocols)
	transport.Protocols.SetHTTP1(true)
	transport.MaxIdleConns = 256
	transport.MaxIdleConnsPerHost = 256
	transport.IdleConnTimeout = 8 * time.Second
	return transport
}

func observeTLS(server *http.Server) {
	var accepted, closed, hello, verified, resumed, activeTransitions atomic.Int64
	server.ConnState = func(_ net.Conn, state http.ConnState) {
		switch state {
		case http.StateNew:
			accepted.Add(1)
		case http.StateClosed, http.StateHijacked:
			closed.Add(1)
		case http.StateActive:
			activeTransitions.Add(1)
		}
	}
	server.TLSConfig.GetConfigForClient = func(*tls.ClientHelloInfo) (*tls.Config, error) {
		hello.Add(1)
		return nil, nil
	}
	server.TLSConfig.VerifyConnection = func(state tls.ConnectionState) error {
		verified.Add(1)
		if state.DidResume {
			resumed.Add(1)
		}
		return nil
	}
	go func() {
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			log.Printf("sdu_listener %d %d %d %d %d %d %d", time.Now().UnixMilli(), accepted.Load(),
				closed.Load(), hello.Load(), verified.Load(), resumed.Load(), activeTransitions.Load())
		}
	}()
}

func main() {
	port := "9090"
	if len(os.Args) == 3 {
		port = os.Args[2]
		if port != "9090" && port != "9091" && port != "9092" {
			os.Exit(1)
		}
	}
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "probe":
			if probe(port) == nil {
				return
			}
		case "metrics":
			if scrape(port) == nil {
				return
			}
		}
		os.Exit(1)
	}
	key := os.Getenv("SDU_GATE_KEY")
	if len(key) < 32 {
		log.Fatal("Benchmark gateway key unavailable")
	}
	benchmark := os.Getenv("SDU_BENCHMARK") == "1"
	localTLS := os.Getenv("SDU_PRIVATE_GATEWAY_TLS") == "1"
	address, upstream := ":8080", "127.0.0.1:8081"
	if localTLS {
		if !benchmark {
			log.Fatal("Private TLS gateway requires benchmark mode")
		}
		address, upstream = "127.0.0.1:8443", "127.0.0.1:8082"
	}
	handler := makeGateway(key, benchmark)
	if benchmark {
		requests := configuredLimit("SDU_BENCH_REQUEST_LIMIT", requestLimit, 1_425_000)
		mib := configuredLimit("SDU_BENCH_RESPONSE_MIB", 256, 2560)
		if os.Getenv("SDU_DIRECT_UPSTREAM") == "1" && !localTLS {
			nodeKey := os.Getenv("SDU_NODE_GATE_KEY")
			if len(nodeKey) < 32 || nodeKey == key {
				log.Fatal("Direct route requires a distinct node key")
			}
			handler = upstreamProxy(key, true, &url.URL{Scheme: "https", Host: "127.0.0.1:8443"},
				requests, mib*1024*1024, directTransport(), nodeKey)
		} else if localTLS && os.Getenv("SDU_HTTP_EXPERIMENT") == "direct" {
			transport := &observedTransport{next: loopbackHTTPTransport()}
			go transport.logCounts()
			handler = upstreamProxy(key, true, &url.URL{Scheme: "http", Host: upstream},
				requests, mib*1024*1024, transport, "")
		} else {
			handler = boundedProxy(key, true, &url.URL{Scheme: "http", Host: upstream}, requests, mib*1024*1024)
		}
	}
	server := http.Server{
		Addr: address, Handler: handler,
		ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second,
		WriteTimeout: 22 * time.Second, IdleTimeout: 10 * time.Second, MaxHeaderBytes: 16384,
	}
	if localTLS {
		server.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
		if os.Getenv("SDU_HTTP_EXPERIMENT") != "" {
			observeTLS(&server)
		}
		log.Fatal(server.ListenAndServeTLS("/tmp/sdu-cloud/http-node.crt", "/tmp/sdu-cloud/http-node.key"))
	}
	log.Fatal(server.ListenAndServe())
}

func configuredLimit(name string, fallback, maximum int64) int64 {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value < fallback || value > maximum {
		log.Fatal("Invalid benchmark resource limit")
	}
	return value
}
