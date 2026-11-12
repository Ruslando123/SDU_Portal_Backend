package main

import (
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestLoopbackTransportReusesConnectionsAcrossBursts(t *testing.T) {
	const concurrency = 16
	for _, tc := range []struct {
		name            string
		transport       *http.Transport
		wantConnections int64
	}{
		{"default", http.DefaultTransport.(*http.Transport).Clone(), 2*concurrency - http.DefaultMaxIdleConnsPerHost},
		{"benchmark loopback", loopbackHTTPTransport(), concurrency},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var connections atomic.Int64
			var wave sync.WaitGroup
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				wave.Done()
				wave.Wait()
				w.WriteHeader(http.StatusNoContent)
			}))
			server.Config.ConnState = func(_ net.Conn, state http.ConnState) {
				if state == http.StateNew {
					connections.Add(1)
				}
			}
			server.Start()
			defer server.Close()
			defer tc.transport.CloseIdleConnections()
			client := &http.Client{Transport: tc.transport}
			for range 2 {
				wave.Add(concurrency)
				var clients sync.WaitGroup
				for range concurrency {
					clients.Add(1)
					go func() {
						defer clients.Done()
						response, err := client.Get(server.URL)
						if err != nil {
							t.Error(err)
							return
						}
						response.Body.Close()
					}()
				}
				clients.Wait()
			}
			if got := connections.Load(); got != tc.wantConnections {
				t.Fatalf("opened %d TCP connections, want %d", got, tc.wantConnections)
			}
		})
	}
}

func TestDirectTLSRouteChecksBothGatewayBoundaries(t *testing.T) {
	edgeKey, nodeKey := strings.Repeat("e", 43), strings.Repeat("n", 43)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.TLS == nil || r.Header.Get("X-SDU-Benchmark-Key") != nodeKey || r.Header.Get("Authorization") != "" {
			t.Error("direct TLS request lost the upstream security boundary")
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	target, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	handler := upstreamProxy(edgeKey, true, target, requestLimit, payloadLimit, server.Client().Transport, nodeKey)
	for _, tc := range []struct {
		path, key string
		want      int
	}{
		{"/api/v1/me", nodeKey, http.StatusForbidden},
		{"/actuator/prometheus", edgeKey, http.StatusNotFound},
		{"/api/v1/me", edgeKey, http.StatusOK},
	} {
		req := httptest.NewRequest(http.MethodGet, tc.path, nil)
		req.Header.Set("X-SDU-Benchmark-Key", tc.key)
		req.Header.Set("Authorization", "must-be-stripped")
		reply := httptest.NewRecorder()
		handler.ServeHTTP(reply, req)
		if reply.Code != tc.want {
			t.Errorf("%s returned %d, want %d", tc.path, reply.Code, tc.want)
		}
	}
}

func TestGatewayTraceDistinguishesUpstreamResponseFromProxyFailure(t *testing.T) {
	const id = "20000000-0000-4000-8000-000000000001"
	key := strings.Repeat("k", 48)
	for _, upstream := range []bool{true, false} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get(correlationHeader) != id || r.Header.Get("X-SDU-Benchmark-Key") != "" {
				t.Error("expected correlation ID and stripped gateway key")
			}
			if upstream {
				w.Header().Set(correlationHeader, id)
				w.WriteHeader(502)
				return
			}
			conn, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			conn.Close()
		}))
		defer server.Close()
		target, err := url.Parse(server.URL)
		if err != nil {
			t.Fatal(err)
		}
		request := httptest.NewRequest(http.MethodGet, "/api/v1/me", nil)
		request.Header.Set(correlationHeader, id)
		request.Header.Set("X-SDU-Benchmark-Key", key)
		response := httptest.NewRecorder()
		var traces strings.Builder
		oldLog := log.Writer()
		log.SetOutput(&traces)
		benchmarkProxy(key, true, target).ServeHTTP(response, request)
		log.SetOutput(oldLog)
		if response.Code != 502 || !strings.Contains(traces.String(), "trace "+id+" ingress 0 accepted") {
			t.Fatalf("missing correlated 502: %d, %s", response.Code, traces.String())
		}
		if upstream {
			if response.Header().Get("X-Sdu-Benchmark-Hop") != "backend" || !strings.Contains(traces.String(), "backend 502 headers") {
				t.Fatal("upstream HTTP 502 was misattributed")
			}
		} else if response.Header().Get("X-Sdu-Benchmark-Hop") != "gateway-error" || !strings.Contains(traces.String(), "gateway 502 eof") {
			t.Fatal("gateway transport failure was misattributed")
		}
		if strings.Contains(traces.String(), key) || strings.Contains(traces.String(), "/api/") {
			t.Fatal("trace includes request secrets or raw route")
		}
	}
}

func TestGatewayScope(t *testing.T) {
	cases := []struct {
		method, path, query string
		want                bool
	}{
		{"GET", coursePath, termQuery, true},
		{"POST", enrollPath, "", true},
		{"GET", "/api/v1/me/schedule", termQuery, true},
		{"GET", "/api/v1/me/schedule", termQuery + "&other=true", false},
		{"DELETE", "/api/v1/enrollments/12345678-1234-4234-8234-123456789abc", "", true},
		{"DELETE", "/api/v1/enrollments/not-a-uuid", "", false},
		{"GET", "/actuator/prometheus", "", false},
		{"GET", "/actuator/health/readiness", "", false},
		{"POST", "/api/v1/admin/config", "", false},
		{"POST", "/api/v1/auth/login", "_modal_connect_token=unexpected", false},
	}
	for _, tc := range cases {
		if got := permitted(tc.method, tc.path, tc.query); got != tc.want {
			t.Errorf("%s %s got %v, want %v", tc.method, tc.path, got, tc.want)
		}
	}
}

func TestGatewayDenials(t *testing.T) {
	key := strings.Repeat("x", 48)
	for _, tc := range []struct {
		path, auth string
		want       int
	}{
		{"/api/v1/auth/csrf", "", http.StatusForbidden},
		{"/api/v1/auth/csrf", key + "a", http.StatusForbidden},
		{"/actuator/prometheus", key, http.StatusNotFound},
		{"/api/v1/auth/%63srf", key, http.StatusNotFound},
	} {
		request := httptest.NewRequest("GET", tc.path, nil)
		request.Header.Set("X-SDU-Benchmark-Key", tc.auth)
		response := httptest.NewRecorder()
		gateway(key).ServeHTTP(response, request)
		if response.Code != tc.want {
			t.Errorf("%s got %d, want %d", tc.path, response.Code, tc.want)
		}
	}
}

func TestBenchmarkRoutesAndMetricsScope(t *testing.T) {
	for _, path := range []string{
		"/api/v1/sections/90000000-0000-4000-8000-000000000000/enrollments",
		"/api/v1/sections/90000000-0000-4000-8000-000000000021/enrollments",
	} {
		if benchmarkEnroll.MatchString(path) {
			t.Errorf("allowed section outside the mixed fixture: %s", path)
		}
	}
	if !benchmarkEnroll.MatchString("/api/v1/sections/90000000-0000-4000-8000-000000000020/enrollments") {
		t.Fatal("last mixed section is not permitted")
	}
	var selected strings.Builder
	metrics := "process_cpu_usage 0.4\njvm_memory_used_bytes{area=\"heap\"} 42\njvm_memory_used_bytes{area=\"nonheap\"} 7\nsecret_metric 1\n"
	if err := selectedMetrics(strings.NewReader(metrics), &selected); err != nil {
		t.Fatal(err)
	}
	if want := "process_cpu_usage 0.4\njvm_memory_used_bytes{area=\"heap\"} 42\n"; selected.String() != want {
		t.Fatalf("unexpected private scrape projection: %s", selected.String())
	}
}

func TestPublishedCatalogBenchmarkScope(t *testing.T) {
	key := strings.Repeat("k", 48)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	target, err := url.Parse(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		benchmark bool
		query     string
		want      int
	}{
		{true, termQuery, http.StatusNoContent},
		{true, termQuery + "&available=true", http.StatusNotFound},
		{true, "termId=other", http.StatusNotFound},
		{false, termQuery, http.StatusNotFound},
	} {
		request := httptest.NewRequest(http.MethodGet, "/api/v1/courses?"+tc.query, nil)
		request.Header.Set("X-SDU-Benchmark-Key", key)
		response := httptest.NewRecorder()
		benchmarkProxy(key, tc.benchmark, target).ServeHTTP(response, request)
		if response.Code != tc.want {
			t.Errorf("benchmark=%t query=%q: got %d, want %d", tc.benchmark, tc.query, response.Code, tc.want)
		}
	}
}
