package main

import (
	"bufio"
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha512"
	"crypto/tls"
	"crypto/x509"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	srp "github.com/ProtonMail/go-srp"
	"github.com/ProtonVPN/go-vpn-lib/localAgent"
	"github.com/ProtonVPN/go-vpn-lib/vpnPing"
	_ "golang.org/x/crypto/x509roots/fallback"
)

const version = "1.1.0"

var (
	apiBase    = "https://vpn-api.proton.me"
	etcDir     = "/etc/protonvpn"
	runDir     = "/var/run/protonvpn"
	appVersion = "Other"
	userAgent  = "ProtonVPN-Enigma2/" + version + " (Linux; Enigma2)"
)

//go:embed proton_ca.pem
var protonRootCA string

func p(name string) string    { return filepath.Join(etcDir, name) }
func runP(name string) string { return filepath.Join(runDir, name) }

func out(v interface{}) {
	b, _ := json.Marshal(v)
	os.Stdout.Write(append(b, '\n'))
}

func fail(code int, msg string) {
	out(map[string]interface{}{"ok": false, "code": code, "error": msg})
	os.Exit(1)
}

type Session struct {
	UID     string `json:"uid"`
	Access  string `json:"access"`
	Refresh string `json:"refresh"`
	User    string `json:"user"`
	Need2FA bool   `json:"need_2fa"`
}

func loadSession() *Session {
	b, err := os.ReadFile(p("session.json"))
	if err != nil {
		return nil
	}
	s := &Session{}
	if json.Unmarshal(b, s) != nil || s.UID == "" {
		return nil
	}
	return s
}

func saveJSON(path string, v interface{}) error {
	os.MkdirAll(filepath.Dir(path), 0700)
	b, _ := json.MarshalIndent(v, "", " ")
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

type APIError struct {
	HTTP    int
	Code    int
	Message string
	Details json.RawMessage
}

func (e *APIError) Error() string { return fmt.Sprintf("%s (%d/%d)", e.Message, e.HTTP, e.Code) }

var client *http.Client

func init() {
	client = &http.Client{Timeout: 30 * time.Second, Transport: &http.Transport{
		TLSClientConfig:   &tls.Config{MinVersion: tls.VersionTLS12},
		Proxy:             http.ProxyFromEnvironment,
		ForceAttemptHTTP2: true,

		DisableKeepAlives:   true,
		TLSHandshakeTimeout: 15 * time.Second,
	}}
}

func apiLog(format string, a ...interface{}) {
	os.MkdirAll(runDir, 0700)
	path := runP("api.log")
	if st, err := os.Stat(path); err == nil && st.Size() > 64*1024 {
		os.Rename(path, path+".old")
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "%s "+format+"\n", append([]interface{}{time.Now().Format("15:04:05")}, a...)...)
}

func isNetErr(err error) bool {
	var ae *APIError
	if err == nil || errors.As(err, &ae) {
		return false
	}
	m := err.Error()
	return errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) || strings.Contains(m, "EOF") ||
		strings.Contains(m, "connection reset") || strings.Contains(m, "broken pipe") ||
		strings.Contains(m, "timeout") || strings.Contains(m, "TLS handshake")
}

func call(sess *Session, method, path string, body interface{}, result interface{}) error {
	err := rawCall(sess, method, path, body, result)
	var ae *APIError
	if sess != nil && errors.As(err, &ae) && ae.HTTP == 401 && sess.Refresh != "" {
		if rerr := refresh(sess); rerr != nil {
			return rerr
		}
		return rawCall(sess, method, path, body, result)
	}
	return err
}

func rawCall(sess *Session, method, path string, body interface{}, result interface{}) error {
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, apiBase+path, rd)
	if err != nil {
		return err
	}
	req.Header.Set("x-pm-appversion", appVersion)
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "application/vnd.protonmail.v1+json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if sess != nil && sess.UID != "" {
		req.Header.Set("x-pm-uid", sess.UID)
		req.Header.Set("Authorization", "Bearer "+sess.Access)
	}
	resp, err := client.Do(req)
	if err != nil {
		apiLog("%s %s -> network error: %v", method, path, err)
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	var env struct {
		Code    int             `json:"Code"`
		Error   string          `json:"Error"`
		Details json.RawMessage `json:"Details"`
	}
	json.Unmarshal(data, &env)
	apiLog("%s %s -> HTTP %d code %d %s", method, path, resp.StatusCode, env.Code, env.Error)
	if resp.StatusCode >= 400 || (env.Code != 0 && env.Code != 1000 && env.Code != 1001) {
		msg := env.Error
		if msg == "" {
			msg = resp.Status
		}
		return &APIError{HTTP: resp.StatusCode, Code: env.Code, Message: msg, Details: env.Details}
	}
	if result != nil {
		return json.Unmarshal(data, result)
	}
	return nil
}

func refresh(sess *Session) error {
	var r struct {
		AccessToken  string
		RefreshToken string
	}
	err := rawCall(sess, "POST", "/auth/v4/refresh", map[string]string{
		"ResponseType": "token", "GrantType": "refresh_token",
		"RefreshToken": sess.Refresh, "RedirectURI": "http://protonmail.ch",
	}, &r)
	if err != nil {
		return err
	}
	sess.Access, sess.Refresh = r.AccessToken, r.RefreshToken
	return saveJSON(p("session.json"), sess)
}

func failAPI(err error) {
	var le loginErr
	if errors.As(err, &le) {
		fail(-1, le.msg)
	}
	var ae *APIError
	if errors.As(err, &ae) {
		if ae.Code == 9001 {
			out(map[string]interface{}{"ok": false, "code": 9001, "error": "captcha", "details": ae.Details})
			os.Exit(1)
		}
		if ae.HTTP == 401 || ae.Code == 10013 {
			os.Remove(p("session.json"))
			fail(401, "session expired - log in again")
		}
		fail(ae.Code, ae.Message)
	}
	fail(-1, err.Error())
}

func needSession() *Session {
	s := loadSession()
	if s == nil {
		fail(401, "not logged in")
	}
	if s.Need2FA {
		fail(2, "2FA code required")
	}
	return s
}

func cmdLogin(user string) {
	reader := bufio.NewReader(os.Stdin)
	pass, _ := reader.ReadString('\n')
	pass = strings.TrimRight(pass, "\r\n")
	if user == "" || pass == "" {
		fail(-1, "username and password required")
	}
	var err error
	for attempt := 1; attempt <= 3; attempt++ {
		err = tryLogin(user, pass)
		if err == nil || !isNetErr(err) {
			break
		}
		apiLog("login attempt %d failed (network), retrying", attempt)
		time.Sleep(time.Duration(attempt) * time.Second)
	}
	if err != nil {
		var ae *APIError
		if errors.As(err, &ae) && ae.Code == 8002 {
			fail(8002, "wrong username or password")
		}
		failAPI(err)
	}
}

type loginErr struct{ msg string }

func (e loginErr) Error() string { return e.msg }

func tryLogin(user, pass string) error {
	old := loadSession()
	var info struct {
		Modulus         string
		ServerEphemeral string
		Version         int
		Salt            string
		SRPSession      string
	}
	anon := anonSession()
	if err := rawCall(anon, "POST", "/auth/v4/info", map[string]string{"Username": user}, &info); err != nil {
		return err
	}
	auth, err := srp.NewAuth(info.Version, user, []byte(pass), info.Salt, info.Modulus, info.ServerEphemeral)
	if err != nil {
		return loginErr{"srp: " + err.Error()}
	}
	proofs, err := auth.GenerateProofs(2048)
	if err != nil {
		return loginErr{"srp proofs: " + err.Error()}
	}
	var res struct {
		UID          string
		AccessToken  string
		RefreshToken string
		ServerProof  string
		TwoFA        struct {
			Enabled int
			TOTP    int
		} `json:"2FA"`
	}
	err = rawCall(anon, "POST", "/auth/v4", map[string]string{
		"Username":        user,
		"ClientEphemeral": base64.StdEncoding.EncodeToString(proofs.ClientEphemeral),
		"ClientProof":     base64.StdEncoding.EncodeToString(proofs.ClientProof),
		"SRPSession":      info.SRPSession,
	}, &res)
	if err != nil {
		return err
	}
	sp, _ := base64.StdEncoding.DecodeString(res.ServerProof)
	if !bytes.Equal(sp, proofs.ExpectedServerProof) {
		return loginErr{"server proof mismatch - possible MITM, login aborted"}
	}
	sess := &Session{UID: res.UID, Access: res.AccessToken, Refresh: res.RefreshToken, User: user}
	if res.TwoFA.Enabled != 0 {
		if res.TwoFA.Enabled&1 == 0 && res.TwoFA.TOTP == 0 {
			return loginErr{"only security-key (FIDO2) 2FA is enabled - add an authenticator app (TOTP) to your account"}
		}
		sess.Need2FA = true
	}
	if err := saveJSON(p("session.json"), sess); err != nil {
		return loginErr{err.Error()}
	}
	os.Remove(p("anon.json"))
	if old != nil && old.UID != sess.UID {
		rawCall(old, "DELETE", "/auth/v4", nil, nil)
	}
	if sess.Need2FA {
		out(map[string]interface{}{"ok": true, "status": "2fa"})
		return nil
	}
	out(map[string]interface{}{"ok": true, "status": "ok", "user": user})
	return nil
}

func cmd2FA(code string) {
	s := loadSession()
	if s == nil {
		fail(401, "not logged in")
	}
	if err := call(s, "POST", "/auth/v4/2fa", map[string]string{"TwoFactorCode": strings.TrimSpace(code)}, nil); err != nil {
		var ae *APIError
		if errors.As(err, &ae) && ae.HTTP != 401 {
			fail(ae.Code, ae.Message)
		}
		failAPI(err)
	}
	s.Need2FA = false
	saveJSON(p("session.json"), s)
	out(map[string]interface{}{"ok": true, "status": "ok", "user": s.User})
}

func cmdLogout() {
	if s := loadSession(); s != nil {
		rawCall(s, "DELETE", "/auth/v4", nil, nil)
	}
	for _, f := range []string{"session.json", "cert.json", "account.json", "servers.json"} {
		os.Remove(p(f))
	}
	out(map[string]interface{}{"ok": true})
}

const forkURL = "https://account.proton.me/vpn/tv"

func newAnonSession() *Session {
	var r struct {
		UID          string
		AccessToken  string
		RefreshToken string
	}
	if err := rawCall(nil, "POST", "/auth/v4/sessions", map[string]interface{}{}, &r); err != nil || r.UID == "" {
		return nil
	}
	s := &Session{UID: r.UID, Access: r.AccessToken, Refresh: r.RefreshToken}
	saveJSON(p("anon.json"), s)
	return s
}

func anonSession() *Session {
	if b, err := os.ReadFile(p("anon.json")); err == nil {
		s := &Session{}
		if json.Unmarshal(b, s) == nil && s.UID != "" {
			return s
		}
	}
	return newAnonSession()
}

func anonCall(method, path string, result interface{}) error {
	s := anonSession()
	err := rawCall(s, method, path, nil, result)
	var ae *APIError
	if s != nil && errors.As(err, &ae) && ae.HTTP == 401 {
		os.Remove(p("anon.json"))
		return rawCall(newAnonSession(), method, path, nil, result)
	}
	return err
}

type Fork struct {
	Selector string `json:"selector"`
	Code     string `json:"code"`
	Expires  int64  `json:"expires"`
}

func cmdForkStart() {
	var r struct {
		Selector string
		UserCode string
	}
	os.Remove(p("anon.json"))
	if err := anonCall("GET", "/auth/v4/sessions/forks", &r); err != nil {
		failAPI(err)
	}
	f := Fork{Selector: r.Selector, Code: r.UserCode, Expires: time.Now().Add(10 * time.Minute).Unix()}
	if err := saveJSON(p("fork.json"), f); err != nil {
		fail(-1, err.Error())
	}
	out(map[string]interface{}{"ok": true, "code": f.Code, "url": forkURL, "expires": f.Expires})
}

func cmdForkPoll() {
	var f Fork
	b, err := os.ReadFile(p("fork.json"))
	if err != nil || json.Unmarshal(b, &f) != nil || f.Selector == "" {
		fail(-1, "no pending code - start again")
	}
	if time.Now().Unix() > f.Expires {
		os.Remove(p("fork.json"))
		out(map[string]interface{}{"ok": false, "code": 410, "error": "code expired"})
		os.Exit(1)
	}
	var r struct {
		UID          string
		AccessToken  string
		RefreshToken string
		UserID       string
	}
	client.Timeout = 12 * time.Second
	err = rawCall(anonSession(), "GET", "/auth/v4/sessions/forks/"+f.Selector, nil, &r)
	if err != nil {
		var ae *APIError
		if (errors.As(err, &ae) && ae.HTTP == 422) || isNetErr(err) {
			out(map[string]interface{}{"ok": true, "status": "pending", "expires": f.Expires})
			return
		}
		failAPI(err)
	}
	if r.UID == "" || r.AccessToken == "" {
		out(map[string]interface{}{"ok": true, "status": "pending", "expires": f.Expires})
		return
	}
	sess := &Session{UID: r.UID, Access: r.AccessToken, Refresh: r.RefreshToken}
	var u struct {
		User struct{ Name, Email string }
	}
	if rawCall(sess, "GET", "/core/v4/users", nil, &u) == nil {
		sess.User = u.User.Name
		if sess.User == "" {
			sess.User = u.User.Email
		}
	}
	if err := saveJSON(p("session.json"), sess); err != nil {
		fail(-1, err.Error())
	}
	os.Remove(p("fork.json"))
	os.Remove(p("anon.json"))
	out(map[string]interface{}{"ok": true, "status": "ok", "user": sess.User})
}

func cmdAccount() {
	s := needSession()
	var r struct {
		VPN struct {
			Name, Password, PlanTitle, PlanName string
			MaxTier, MaxConnect                 int
			ExpirationTime                      int64
		}
		Services int
	}
	if err := call(s, "GET", "/vpn/v2", nil, &r); err != nil {
		failAPI(err)
	}
	acc := map[string]interface{}{
		"ok": true, "user": s.User, "plan": r.VPN.PlanTitle, "plan_name": r.VPN.PlanName,
		"max_tier": r.VPN.MaxTier, "max_connect": r.VPN.MaxConnect,
		"ovpn_user": r.VPN.Name, "ovpn_pass": r.VPN.Password,
	}
	saveJSON(p("account.json"), acc)
	out(acc)
}

type LogicalServer struct {
	ID           string `json:"ID"`
	Name         string
	EntryCountry string
	ExitCountry  string
	HostCountry  string `json:",omitempty"`
	City         string
	Tier         int
	Features     int
	Load         int
	Score        float64
	Status       int
	Servers      []struct {
		EntryIP         string
		ExitIP          string
		Domain          string
		X25519PublicKey string
		Status          int
		Label           string `json:",omitempty"`
	}
}

func cmdServers() {
	s := needSession()
	var r struct{ LogicalServers []LogicalServer }
	if err := call(s, "GET", "/vpn/v1/logicals?SecureCoreFilter=all&WithState=true", nil, &r); err != nil {
		failAPI(err)
	}
	var loads struct {
		LogicalServers []struct {
			ID     string
			Load   int
			Score  float64
			Status int
		}
	}
	if call(s, "GET", "/vpn/v1/loads", nil, &loads) == nil {
		m := map[string]int{}
		for i, l := range loads.LogicalServers {
			m[l.ID] = i
		}
		for i := range r.LogicalServers {
			if j, ok := m[r.LogicalServers[i].ID]; ok {
				l := loads.LogicalServers[j]
				r.LogicalServers[i].Load, r.LogicalServers[i].Score, r.LogicalServers[i].Status = l.Load, l.Score, l.Status
			}
		}
	}

	rows := make([][]interface{}, 0, len(r.LogicalServers))
	for _, l := range r.LogicalServers {
		phys := make([][]interface{}, 0, 2)
		for _, sv := range l.Servers {
			if sv.X25519PublicKey == "" && sv.EntryIP == "" {
				continue
			}
			phys = append(phys, []interface{}{sv.EntryIP, sv.X25519PublicKey, sv.Domain, sv.Status, sv.ExitIP})
			if len(phys) >= 3 {
				break
			}
		}
		if len(phys) == 0 {
			continue
		}
		rows = append(rows, []interface{}{l.Name, l.EntryCountry, l.ExitCountry, l.City, l.Tier, l.Features,
			l.Load, float64(int(l.Score*1000)) / 1000, l.Status, phys})
	}
	doc := map[string]interface{}{"updated": time.Now().Unix(), "v": 1, "s": rows}
	b, _ := json.Marshal(doc)
	tmp := p("servers.json") + ".tmp"
	if err := os.WriteFile(tmp, b, 0600); err != nil {
		fail(-1, err.Error())
	}
	os.Rename(tmp, p("servers.json"))
	out(map[string]interface{}{"ok": true, "count": len(rows)})
}

func loadOrCreateKey() ed25519.PrivateKey {
	if b, err := os.ReadFile(p("key.pem")); err == nil {
		if blk, _ := pem.Decode(b); blk != nil {
			if k, err := x509.ParsePKCS8PrivateKey(blk.Bytes); err == nil {
				if ek, ok := k.(ed25519.PrivateKey); ok {
					return ek
				}
			}
		}
	}
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		fail(-1, err.Error())
	}
	der, _ := x509.MarshalPKCS8PrivateKey(priv)
	os.MkdirAll(etcDir, 0700)
	os.WriteFile(p("key.pem"), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0600)
	os.Remove(p("cert.json"))
	return priv
}

func x25519Private(k ed25519.PrivateKey) []byte {
	h := sha512.Sum512(k.Seed())
	x := h[:32]
	x[0] &= 248
	x[31] &= 127
	x[31] |= 64
	return x
}

type Cert struct {
	Certificate    string
	ExpirationTime int64
	RefreshTime    int64
	ClientKey      string
	Features       map[string]interface{} `json:"_features"`
}

func cmdCert(args []string) {
	s := needSession()
	feat := map[string]interface{}{}
	netshield := 0
	force := false
	for _, a := range args {
		switch {
		case strings.HasPrefix(a, "--netshield="):
			netshield, _ = strconv.Atoi(strings.TrimPrefix(a, "--netshield="))
		case a == "--no-accelerator":
			feat["SplitTCP"] = false
		case a == "--strict-nat":
			feat["RandomNAT"] = false
		case a == "--port-forwarding":
			feat["PortForwarding"] = true
		case a == "--force":
			force = true
		}
	}
	if netshield > 0 {
		feat["NetShieldLevel"] = netshield
	}
	key := loadOrCreateKey()
	pubDER, _ := x509.MarshalPKIXPublicKey(key.Public())
	pubPEM := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubDER}))

	var c Cert
	if b, err := os.ReadFile(p("cert.json")); err == nil && !force {
		json.Unmarshal(b, &c)
	}
	fb, _ := json.Marshal(feat)
	cb, _ := json.Marshal(c.Features)
	now := time.Now().Unix()
	if c.Certificate == "" || now >= c.RefreshTime || string(fb) != string(cb) || strings.TrimSpace(c.ClientKey) != strings.TrimSpace(pubPEM) {
		body := map[string]interface{}{"ClientPublicKey": pubPEM, "Duration": "10080 min"}
		if len(feat) > 0 {
			body["Features"] = feat
		}
		var nc Cert
		if err := call(s, "POST", "/vpn/v1/certificate", body, &nc); err != nil {
			failAPI(err)
		}
		if nc.ClientKey == "" {
			nc.ClientKey = pubPEM
		}
		nc.Features = feat
		c = nc
		saveJSON(p("cert.json"), c)
	}
	out(map[string]interface{}{
		"ok": true, "wg_private_key": base64.StdEncoding.EncodeToString(x25519Private(key)),
		"expires": c.ExpirationTime, "refresh": c.RefreshTime,
	})
}

func cmdPing() {
	type job struct {
		ip, key string
		port    int
		idx     int
	}
	var jobs []job
	sc := bufio.NewScanner(os.Stdin)
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 3 {
			continue
		}
		port, _ := strconv.Atoi(f[1])
		jobs = append(jobs, job{f[0], f[2], port, len(jobs)})
	}
	res := make(map[string]int, len(jobs))
	var mu sync.Mutex
	ch := make(chan job)
	var wg sync.WaitGroup
	for w := 0; w < 24; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range ch {
				t := time.Now()
				ok, _ := vpnPing.PingSyncWithError(j.ip, j.port, j.key, 1500)
				ms := -1
				if ok {
					ms = int(time.Since(t).Milliseconds())
					if ms < 1 {
						ms = 1
					}
				}
				mu.Lock()
				res[j.ip] = ms
				mu.Unlock()
			}
		}()
	}
	for _, j := range jobs {
		ch <- j
	}
	close(ch)
	wg.Wait()
	out(map[string]interface{}{"ok": true, "ping": res})
}

type agentClient struct {
	mu    sync.Mutex
	state string
	errc  int
	errm  string
	stats map[string]int64
	conn  *localAgent.AgentConnection
}

func (a *agentClient) write() {
	a.mu.Lock()
	defer a.mu.Unlock()
	saveJSON(runP("agent.json"), map[string]interface{}{
		"state": a.state, "error_code": a.errc, "error": a.errm, "stats": a.stats, "updated": time.Now().Unix(),
	})
}
func (a *agentClient) Log(text string) {
	f, err := os.OpenFile(runP("agent.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err == nil {
		fmt.Fprintf(f, "%s %s\n", time.Now().Format("15:04:05"), text)
		f.Close()
	}
}
func (a *agentClient) OnState(state string) {
	a.mu.Lock()
	a.state = state
	a.mu.Unlock()
	a.write()
}
func (a *agentClient) OnError(code int, description string) {
	a.mu.Lock()
	a.errc, a.errm = code, description
	a.mu.Unlock()
	a.write()
}
func (a *agentClient) OnStatusUpdate(st *localAgent.StatusMessage) {
	if st != nil && st.FeaturesStatistics != nil {
		ns := st.FeaturesStatistics.GetMap("netshield-level")
		if ns != nil {
			m := map[string]int64{}
			for _, k := range []string{"malware-blocked", "ads-blocked", "trackers-blocked", "bytes-saved"} {
				m[k] = ns.GetIntOrDefault(k, 0)
			}
			a.mu.Lock()
			a.stats = m
			a.mu.Unlock()
		}
	}
	a.write()
}
func (a *agentClient) OnTlsSessionStarted() {}
func (a *agentClient) OnTlsSessionEnded()   {}

func cmdAgent(args []string) {
	domain, host := "", "10.2.0.1:65432"
	netshield, accel, rnat, pf := int64(0), true, true, false
	for _, a := range args {
		switch {
		case strings.HasPrefix(a, "--domain="):
			domain = strings.TrimPrefix(a, "--domain=")
		case strings.HasPrefix(a, "--host="):
			host = strings.TrimPrefix(a, "--host=")
		case strings.HasPrefix(a, "--netshield="):
			netshield, _ = strconv.ParseInt(strings.TrimPrefix(a, "--netshield="), 10, 64)
		case a == "--no-accelerator":
			accel = false
		case a == "--strict-nat":
			rnat = false
		case a == "--port-forwarding":
			pf = true
		}
	}
	if domain == "" {
		fail(-1, "--domain required")
	}
	var c Cert
	b, err := os.ReadFile(p("cert.json"))
	if err != nil || json.Unmarshal(b, &c) != nil || c.Certificate == "" {
		fail(-1, "no certificate")
	}
	kb, err := os.ReadFile(p("key.pem"))
	if err != nil {
		fail(-1, "no key")
	}
	os.MkdirAll(runDir, 0700)
	feat := localAgent.NewFeatures()
	feat.SetInt("netshield-level", netshield)
	feat.SetBool("split-tcp", accel)
	feat.SetBool("randomized-nat", rnat)
	feat.SetBool("port-forwarding", pf)
	cl := &agentClient{state: "Connecting"}
	cl.write()
	conn, err := localAgent.NewAgentConnection(c.Certificate, string(kb), protonRootCA, host, domain, cl, feat, true, 0, 0)
	if err != nil {
		fail(-1, err.Error())
	}
	cl.conn = conn
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	tick := time.NewTicker(60 * time.Second)
	for {
		select {
		case <-sig:
			conn.Close()
			cl.OnState("Disconnected")
			return
		case <-tick.C:
			conn.SendGetStatus(true)
		}
	}
}

func main() {
	if v := os.Getenv("PROTONAPI_ETC"); v != "" {
		etcDir = v
	}
	if v := os.Getenv("PROTONAPI_BASE"); v != "" {
		apiBase = v
	}
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: protonapi login <user> (password on stdin) | 2fa <code> | logout | account | servers | cert [--netshield=N] | ping (stdin: ip port key) | agent --domain=D | version")
		os.Exit(2)
	}
	if os.Args[1] == "wg" {
		os.Setenv("WG_PROCESS_FOREGROUND", "1")
		os.Args = append([]string{"wireguard-go"}, os.Args[2:]...)
		wgMain()
		return
	}
	args := os.Args[2:]
	switch os.Args[1] {
	case "version", "--version":
		out(map[string]interface{}{"ok": true, "version": version})
	case "login":
		if len(args) < 1 {
			fail(-1, "username required")
		}
		cmdLogin(args[0])
	case "2fa":
		if len(args) < 1 {
			fail(-1, "code required")
		}
		cmd2FA(args[0])
	case "logout":
		cmdLogout()
	case "fork-start":
		cmdForkStart()
	case "fork-poll":
		cmdForkPoll()
	case "account":
		cmdAccount()
	case "servers":
		cmdServers()
	case "cert":
		cmdCert(args)
	case "ping":
		cmdPing()
	case "agent":
		cmdAgent(args)
	case "status":
		s := loadSession()
		out(map[string]interface{}{"ok": true, "logged_in": s != nil && !s.Need2FA, "need_2fa": s != nil && s.Need2FA,
			"user": func() string {
				if s != nil {
					return s.User
				}
				return ""
			}()})
	default:
		fail(-1, "unknown command")
	}
	_ = sort.Strings
}
