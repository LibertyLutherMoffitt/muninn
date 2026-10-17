package main

// The Python core (`muninn --api`) as a child process: newline-delimited
// JSON on its stdin/stdout (python/src/muninn/api.py). Requests carry "req";
// anything without it is an event.

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

type Event struct {
	Ev     string `json:"ev"`
	Conv   string `json:"conv"`
	Name   string `json:"name"`
	Text   string `json:"text"`
	What   string `json:"what"`
	Detail string `json:"detail"`
	Me     string `json:"me"`
}

type reply struct {
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  string          `json:"error"`
}

type Core struct {
	cmd     *exec.Cmd
	in      io.WriteCloser
	mu      sync.Mutex
	next    int
	pending map[int]chan reply
	Events  chan Event
	stderr  lockedBuf
}

// lockedBuf: the process writes stderr from its own goroutine.
type lockedBuf struct {
	mu sync.Mutex
	b  strings.Builder
}

func (l *lockedBuf) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.b.Len() > 64<<10 { // keep the tail only
		s := l.b.String()
		l.b.Reset()
		l.b.WriteString(s[len(s)-32<<10:])
	}
	return l.b.Write(p)
}

func (l *lockedBuf) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}

// findCore: $MUNINN_CORE (a command line, e.g. "python -m muninn.cli"),
// else a `muninn` next to this binary, else `muninn` on PATH.
func findCore() ([]string, error) {
	if c := strings.TrimSpace(os.Getenv("MUNINN_CORE")); c != "" {
		return strings.Fields(c), nil
	}
	exe := "muninn"
	if runtime.GOOS == "windows" {
		exe = "muninn.exe"
	}
	if self, err := os.Executable(); err == nil {
		// release downloads: muninn-linux-x86_64 / muninn-windows-x86_64.exe
		names := []string{exe, "muninn-" + runtime.GOOS + "-x86_64" + filepath.Ext(exe)}
		for _, name := range names {
			p := filepath.Join(filepath.Dir(self), name)
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				return []string{p}, nil
			}
		}
	}
	if p, err := exec.LookPath(exe); err == nil {
		return []string{p}, nil
	}
	return nil, errors.New("can't find the Muninn core (`muninn`): install it next to this program, put it on PATH, or set MUNINN_CORE")
}

func StartCore() (*Core, error) {
	argv, err := findCore()
	if err != nil {
		return nil, err
	}
	cmd := exec.Command(argv[0], append(argv[1:], "--api")...)
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	c := &Core{cmd: cmd, in: in, pending: map[int]chan reply{}, Events: make(chan Event, 256)}
	cmd.Stderr = &c.stderr
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("starting %s: %w", argv[0], err)
	}
	go c.read(out)
	return c, nil
}

func (c *Core) read(out io.Reader) {
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		line := sc.Bytes()
		var probe struct {
			Req *int `json:"req"`
		}
		if json.Unmarshal(line, &probe) != nil {
			continue
		}
		if probe.Req != nil {
			var r reply
			_ = json.Unmarshal(line, &r)
			c.mu.Lock()
			ch := c.pending[*probe.Req]
			delete(c.pending, *probe.Req)
			c.mu.Unlock()
			if ch != nil {
				ch <- r
			}
			continue
		}
		var ev Event
		if json.Unmarshal(line, &ev) == nil {
			c.Events <- ev
		}
	}
	msg := strings.TrimSpace(c.stderr.String())
	if msg == "" {
		msg = "the Muninn core stopped"
	}
	c.Events <- Event{Ev: "exit", Text: lastLines(msg, 6)}
	close(c.Events)
}

// Call sends one request and decodes its result into out (may be nil).
func (c *Core) Call(cmd string, params map[string]any, out any) error {
	return c.call(cmd, params, out, 60*time.Second)
}

func (c *Core) call(cmd string, params map[string]any, out any, timeout time.Duration) error {
	c.mu.Lock()
	c.next++
	id := c.next
	ch := make(chan reply, 1)
	c.pending[id] = ch
	req := map[string]any{"req": id, "cmd": cmd}
	for k, v := range params {
		req[k] = v
	}
	b, _ := json.Marshal(req)
	_, err := c.in.Write(append(b, '\n'))
	c.mu.Unlock()
	if err != nil {
		return err
	}
	select {
	case r := <-ch:
		if !r.OK {
			return errors.New(r.Error)
		}
		if out != nil && len(r.Result) > 0 {
			return json.Unmarshal(r.Result, out)
		}
		return nil
	case <-time.After(timeout):
		c.mu.Lock()
		delete(c.pending, id)
		c.mu.Unlock()
		return errors.New("the core didn't answer")
	}
}

func (c *Core) Close() {
	_ = c.call("quit", nil, nil, 3*time.Second)
	_ = c.in.Close()
	done := make(chan struct{})
	go func() { _ = c.cmd.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		_ = c.cmd.Process.Kill()
	}
}

func lastLines(s string, n int) string {
	lines := strings.Split(s, "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n")
}
