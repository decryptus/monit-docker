// Optional Docker API proxy for the monit-docker Compose example.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"os"
	"regexp"
	"strings"
	"time"
)

const socket = "/var/run/docker.sock"
const maxJSON = 64 << 10

var id = regexp.MustCompile("^[0-9a-f]{64}$")
var version = regexp.MustCompile("^v1\\.[1-9][0-9]{1,2}$")

type policy struct {
	exec, lifecycle map[string]bool
	client *http.Client
}

func targets(key string) map[string]bool {
	result := make(map[string]bool)
	for _, item := range strings.Fields(os.Getenv(key)) {
		if !id.MatchString(item) { log.Fatalf("%s requires space-separated full container IDs", key) }
		result[item] = true
	}
	return result
}

// Docker addresses start/inspect by exec ID, not container ID. Resolve the
// immutable owner on the same daemon before forwarding either request.
func (p policy) ownsExec(r *http.Request, apiVersion, execID string) bool {
	if len(p.exec) == 0 || !id.MatchString(execID) { return false }
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", "http://docker/"+apiVersion+"/exec/"+execID+"/json", nil)
	if err != nil { return false }
	resp, err := p.client.Do(req)
	if err != nil { return false }
	defer resp.Body.Close()
	var info struct {
		ContainerID string
		ProcessConfig *struct { Privileged bool }
	}
	return resp.StatusCode == 200 && json.NewDecoder(io.LimitReader(resp.Body,maxJSON)).Decode(&info)==nil &&
		p.exec[info.ContainerID] && info.ProcessConfig != nil && !info.ProcessConfig.Privileged
}

func safeExecCreate(r *http.Request) bool {
	body, err := io.ReadAll(io.LimitReader(r.Body,maxJSON+1))
	if err != nil || len(body)>maxJSON { return false }
	var opts struct { Privileged bool }
	if json.Unmarshal(body,&opts)!=nil || opts.Privileged { return false }
	r.Body = io.NopCloser(bytes.NewReader(body))
	return true
}

func (p policy) allowed(r *http.Request) bool {
	if r.URL.RawPath != "" || r.URL.IsAbs() { return false }
	path := strings.Split(strings.TrimPrefix(r.URL.Path,"/"),"/")
	get := r.Method == "GET"
	post := r.Method == "POST"
	if len(path)==1 { return get && (path[0]=="version" || path[0]=="_ping") }
	if !version.MatchString(path[0]) { return false }
	switch len(path) {
	case 2:
		return get && (path[1]=="events" || path[1]=="version" || path[1]=="_ping")
	case 3:
		return get && path[1]=="containers" && path[2]=="json"
	case 4:
		target, operation := path[2],path[3]
		switch path[1] {
		case "images":
			return get && operation=="json" && strings.HasPrefix(target,"sha256:") && id.MatchString(strings.TrimPrefix(target,"sha256:"))
		case "exec":
			return ((get && operation=="json") || (post && operation=="start")) && p.ownsExec(r,path[0],target)
		case "containers":
			if !id.MatchString(target) { return false }
			if get { return operation=="json" || operation=="stats" }
			if !post { return false }
			if operation=="exec" { return p.exec[target] && r.URL.RawQuery=="" && safeExecCreate(r) }
			if !p.lifecycle[target] || r.ContentLength!=0 || len(r.TransferEncoding)!=0 { return false }
			switch operation {
			case "start": return r.URL.RawQuery==""
			case "stop","restart": return r.URL.RawQuery=="" || stopTimeout.MatchString(r.URL.RawQuery)
			}
		}
	}
	return false
}

var stopTimeout = regexp.MustCompile("^t=[0-9]{1,6}$")

func main() {
	dialer := &net.Dialer{Timeout: 3*time.Second}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string)(net.Conn,error){
		return dialer.DialContext(ctx,"unix",socket)
	}, ResponseHeaderTimeout: 30*time.Second}
	p := policy{targets("EXEC_TARGETS"), targets("LIFECYCLE_TARGETS"), &http.Client{Transport:transport,CheckRedirect:func(*http.Request,[]*http.Request)error{return http.ErrUseLastResponse}}}
	proxy := &httputil.ReverseProxy{Transport:transport, FlushInterval:-1, Rewrite:func(pr *httputil.ProxyRequest){
		pr.Out.URL.Scheme="http"; pr.Out.URL.Host="docker"; pr.Out.Host="docker"
		// Forward only headers used by Docker's JSON and hijacked exec protocols.
		h:=make(http.Header)
		if ct:=pr.In.Header.Get("Content-Type"); ct!="" { h.Set("Content-Type",ct) }
		if strings.HasSuffix(pr.In.URL.Path,"/start") && strings.Contains(pr.In.URL.Path,"/exec/") && strings.EqualFold(pr.In.Header.Get("Upgrade"),"tcp") {
			h.Set("Connection","Upgrade");h.Set("Upgrade","tcp")
		}
		pr.Out.Header=h
	}}
	server:= &http.Server{Addr:":2375",ReadHeaderTimeout:5*time.Second, ReadTimeout:15*time.Second,IdleTimeout:30*time.Second,MaxHeaderBytes:16<<10,Handler:http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
		if !p.allowed(r) { http.Error(w,"Docker operation denied",http.StatusForbidden);return }
		proxy.ServeHTTP(w,r)
	})}
	log.Fatal(server.ListenAndServe())
}
