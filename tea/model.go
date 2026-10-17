package main

import (
	"encoding/base64"
	"fmt"
	"math"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/harmonica"
)

type focus int

const (
	focusComposer focus = iota
	focusList
	focusMessages
)

var quickReactions = []string{"👍", "❤️", "😂", "😮", "😢", "🔥"}

// ---- messages ---------------------------------------------------------------

type (
	coreEventMsg Event
	helloMsg     Hello
	convsMsg     []Conv
	historyMsg   struct {
		conv   string
		msgs   []Msg
		header Header
	}
	cmdResultMsg struct {
		line string
		res  CmdResult
	}
	errMsg   struct{ err error }
	emojiMsg struct {
		q    string
		hits []EmojiHit
	}
	peopleMsg  []Person
	frameMsg   time.Time
	refreshMsg struct{}
	sentMsg    struct{}
)

type toast struct {
	text  string
	err   bool
	until time.Time
}

type infoPanel struct {
	title string
	lines []string
}

type Model struct {
	core  *Core
	hello Hello
	ready bool
	fatal string

	width, height int
	focus         focus

	convs   []Conv
	listSel int
	active  string
	header  Header
	msgs    []Msg
	msgSel  int // index into msgs while focusMessages
	starts  []int
	ends    []int

	vp       viewport.Model
	ta       textarea.Model
	replyTo  *Msg
	editing  *Msg
	armedDel string

	// smooth scrolling
	spring          harmonica.Spring
	scrollPos       float64
	scrollVel       float64
	scrollTarget    int
	animating       bool
	stickToBottom   bool
	frame           int
	framesScheduled bool

	pal     palette
	info    *infoPanel
	toasts  []toast
	emoji   []EmojiHit
	emojiQ  string
	naming  textinput.Model // first run: what should people call you?
	pending struct {
		convs, history bool
		scheduled      bool
	}
	lastTyping time.Time
}

func NewModel(core *Core) Model {
	ta := textarea.New()
	ta.Placeholder = "Message  ·  Enter send · Alt+Enter newline · :emoji: · / commands"
	ta.ShowLineNumbers = false
	ta.Prompt = ""
	ta.CharLimit = 8000
	ta.SetHeight(1)
	ta.KeyMap.InsertNewline.SetKeys("alt+enter", "ctrl+j")
	ta.FocusedStyle.CursorLine = ta.FocusedStyle.CursorLine.UnsetBackground()
	ta.FocusedStyle.Placeholder = sFaint
	ta.BlurredStyle.Placeholder = sFaint
	ta.Focus()
	ni := textinput.New()
	ni.Placeholder = "your name"
	ni.CharLimit = 32
	return Model{
		core:          core,
		ta:            ta,
		vp:            viewport.New(80, 20),
		spring:        harmonica.NewSpring(harmonica.FPS(60), 7.0, 1.0),
		stickToBottom: true,
		naming:        ni,
		pal:           newPalette(),
	}
}

// ---- core calls (run off the UI goroutine) ------------------------------------

func (m Model) call(cmd string, params map[string]any, wrap func() tea.Msg) tea.Cmd {
	return func() tea.Msg {
		if err := m.core.Call(cmd, params, nil); err != nil {
			return errMsg{err}
		}
		if wrap != nil {
			return wrap()
		}
		return nil
	}
}

func waitEvent(c *Core) tea.Cmd {
	return func() tea.Msg {
		ev, ok := <-c.Events
		if !ok {
			return coreEventMsg{Ev: "exit"}
		}
		return coreEventMsg(ev)
	}
}

func loadHello(c *Core) tea.Cmd {
	return func() tea.Msg {
		var h Hello
		if err := c.Call("hello", nil, &h); err != nil {
			return errMsg{err}
		}
		return helloMsg(h)
	}
}

func loadConvs(c *Core) tea.Cmd {
	return func() tea.Msg {
		var cs []Conv
		if err := c.Call("convs", nil, &cs); err != nil {
			return errMsg{err}
		}
		return convsMsg(cs)
	}
}

func loadHistory(c *Core, conv string) tea.Cmd {
	return func() tea.Msg {
		if conv == "" {
			return historyMsg{}
		}
		var ms []Msg
		var h Header
		if err := c.Call("history", map[string]any{"conv": conv, "n": 300}, &ms); err != nil {
			return errMsg{err}
		}
		_ = c.Call("header", map[string]any{"conv": conv}, &h)
		return historyMsg{conv: conv, msgs: ms, header: h}
	}
}

func runCommand(c *Core, line, conv string) tea.Cmd {
	return func() tea.Msg {
		var r CmdResult
		if err := c.Call("command", map[string]any{"line": line, "conv": conv}, &r); err != nil {
			return errMsg{err}
		}
		return cmdResultMsg{line: line, res: r}
	}
}

func loadEmoji(c *Core, q string) tea.Cmd {
	return func() tea.Msg {
		var hits []EmojiHit
		_ = c.Call("emoji", map[string]any{"q": q}, &hits)
		return emojiMsg{q: q, hits: hits}
	}
}

func loadPeople(c *Core) tea.Cmd {
	return func() tea.Msg {
		var ps []Person
		_ = c.Call("people", nil, &ps)
		return peopleMsg(ps)
	}
}

func frameTick() tea.Cmd {
	return tea.Tick(time.Second/60, func(t time.Time) tea.Msg { return frameMsg(t) })
}

// ---- Bubble Tea --------------------------------------------------------------

func (m Model) Init() tea.Cmd {
	return tea.Batch(waitEvent(m.core), textarea.Blink)
}

func (m *Model) toast(text string, isErr bool) {
	m.toasts = append(m.toasts, toast{text: text, err: isErr, until: time.Now().Add(4 * time.Second)})
	if len(m.toasts) > 3 {
		m.toasts = m.toasts[len(m.toasts)-3:]
	}
}

func (m *Model) scheduleRefresh(convs, history bool) tea.Cmd {
	m.pending.convs = m.pending.convs || convs
	m.pending.history = m.pending.history || history
	if m.pending.scheduled {
		return nil
	}
	m.pending.scheduled = true
	return tea.Tick(80*time.Millisecond, func(time.Time) tea.Msg { return refreshMsg{} })
}

func (m *Model) ensureFrames() tea.Cmd {
	if m.framesScheduled {
		return nil
	}
	m.framesScheduled = true
	return frameTick()
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
		m.layout()
		m.rebuild(false)

	case coreEventMsg:
		cmds = append(cmds, m.onEvent(Event(msg)))
		if msg.Ev != "exit" && msg.Ev != "fatal" {
			cmds = append(cmds, waitEvent(m.core))
		}

	case helloMsg:
		m.hello = Hello(msg)
		if m.hello.Name == "" {
			m.naming.Focus()
		}

	case convsMsg:
		m.convs = msg
		if m.active == "" && len(m.convs) > 0 && m.focus != focusList {
			m.open(m.convs[0].Conv)
			cmds = append(cmds, loadHistory(m.core, m.active), m.markRead())
		}
		m.syncListSel()

	case historyMsg:
		if msg.conv == m.active {
			wasBottom := m.vp.AtBottom() || m.stickToBottom
			grew := len(msg.msgs) > len(m.msgs)
			m.msgs, m.header = msg.msgs, msg.header
			if m.msgSel >= len(m.msgs) {
				m.msgSel = len(m.msgs) - 1
			}
			m.rebuild(wasBottom && grew)
			if wasBottom && !grew {
				m.jumpBottom()
			}
			cmds = append(cmds, m.ensureFrames())
		}

	case refreshMsg:
		m.pending.scheduled = false
		if m.pending.convs {
			cmds = append(cmds, loadConvs(m.core))
		}
		if m.pending.history && m.active != "" {
			cmds = append(cmds, loadHistory(m.core, m.active))
		}
		m.pending.convs, m.pending.history = false, false

	case cmdResultMsg:
		cmds = append(cmds, m.onCommand(msg))

	case errMsg:
		m.toast(msg.err.Error(), true)
		cmds = append(cmds, m.ensureFrames())

	case emojiMsg:
		if msg.q == m.emojiQ {
			m.emoji = msg.hits
			m.layout()
		}

	case paletteEmojiMsg:
		if msg.q == m.pal.input.Value() {
			m.pal.emojis = msg.hits
			m.pal.filter(&m)
		}

	case peopleMsg:
		m.pal.people = msg
		m.pal.filter(&m)

	case sentMsg:
		cmds = append(cmds, m.scheduleRefresh(true, true))

	case frameMsg:
		m.framesScheduled = false
		m.frame++
		now := time.Now()
		kept := m.toasts[:0]
		for _, t := range m.toasts {
			if now.Before(t.until) {
				kept = append(kept, t)
			}
		}
		m.toasts = kept
		if m.animating {
			m.scrollPos, m.scrollVel = m.spring.Update(m.scrollPos, m.scrollVel, float64(m.scrollTarget))
			if math.Abs(m.scrollPos-float64(m.scrollTarget)) < 0.5 && math.Abs(m.scrollVel) < 0.5 {
				m.scrollPos, m.animating = float64(m.scrollTarget), false
			}
			m.vp.SetYOffset(int(math.Round(m.scrollPos)))
		}
		if m.animating || len(m.toasts) > 0 || m.anyTyping() {
			// typing dots and toasts animate slower than scrolling
			if m.animating {
				cmds = append(cmds, m.ensureFrames())
			} else {
				m.framesScheduled = true
				cmds = append(cmds, tea.Tick(time.Second/6, func(t time.Time) tea.Msg { return frameMsg(t) }))
			}
		}

	case tea.MouseMsg:
		cmds = append(cmds, m.onMouse(msg))

	case tea.KeyMsg:
		cmds = append(cmds, m.onKey(msg))
	}
	return m, tea.Batch(cmds...)
}

func (m *Model) anyTyping() bool {
	for _, c := range m.convs {
		if c.Typing {
			return true
		}
	}
	return false
}

func (m *Model) onEvent(ev Event) tea.Cmd {
	switch ev.Ev {
	case "ready":
		m.ready = true
		return tea.Batch(loadHello(m.core), loadConvs(m.core))
	case "fatal", "exit":
		m.fatal = ev.Text
		if m.fatal == "" {
			m.fatal = "the Muninn core stopped"
		}
		return nil
	case "message":
		var cmds []tea.Cmd
		if ev.Conv == m.active {
			cmds = append(cmds, m.markRead())
		} else {
			cmds = append(cmds, m.notifyNew(ev.Conv))
		}
		cmds = append(cmds, m.scheduleRefresh(true, ev.Conv == m.active))
		return tea.Batch(cmds...)
	case "update":
		return m.scheduleRefresh(true, true)
	case "peers":
		return m.scheduleRefresh(true, true)
	case "typing":
		return tea.Batch(m.scheduleRefresh(true, ev.Conv == m.active), m.ensureFrames(),
			tea.Tick(7*time.Second, func(time.Time) tea.Msg { return refreshMsg{} }))
	case "link":
		if ev.What == "ok" {
			m.toast("Linked — this and your other device are one person now; history is syncing", false)
		} else {
			m.toast("Link: "+ev.Detail, true)
		}
		return tea.Batch(m.scheduleRefresh(true, true), m.ensureFrames(), loadHello(m.core))
	case "notice":
		if strings.TrimSpace(ev.Text) != "" {
			m.toast(strings.TrimLeft(ev.Text, "!+- "), strings.HasPrefix(ev.Text, "!"))
			return m.ensureFrames()
		}
	}
	return nil
}

// a new message elsewhere: a toast (the list shows the unread badge too)
func (m *Model) notifyNew(conv string) tea.Cmd {
	return func() tea.Msg {
		var ms []Msg
		if err := m.core.Call("history", map[string]any{"conv": conv, "n": 1}, &ms); err != nil || len(ms) == 0 {
			return nil
		}
		x := ms[len(ms)-1]
		if x.Mine || x.Kind == "system" {
			return nil
		}
		text := x.Text
		if x.File != nil {
			text = "📎 " + x.File.Name
		}
		return coreEventMsg{Ev: "notice", Text: "✉ " + x.Name + ": " + oneLine(text, 60)}
	}
}

func (m *Model) markRead() tea.Cmd {
	if m.active == "" {
		return nil
	}
	return m.call("read", map[string]any{"conv": m.active}, nil)
}

func (m *Model) open(conv string) {
	if conv == m.active {
		return
	}
	m.active = conv
	m.msgs, m.header = nil, Header{}
	m.replyTo, m.editing, m.armedDel = nil, nil, ""
	m.stickToBottom = true
	m.msgSel = -1
	for _, c := range m.convs {
		if c.Conv == conv {
			m.header = Header{Title: c.Title, Subtitle: c.Subtitle}
		}
	}
	m.syncListSel()
	m.rebuild(false)
}

func (m *Model) syncListSel() {
	for i, c := range m.convs {
		if c.Conv == m.active && m.focus != focusList {
			m.listSel = i
		}
	}
	if m.listSel >= len(m.convs) {
		m.listSel = len(m.convs) - 1
	}
	if m.listSel < 0 {
		m.listSel = 0
	}
}

func (m *Model) switchTo(conv string) tea.Cmd {
	m.open(conv)
	return tea.Batch(loadHistory(m.core, conv), m.markRead(), m.scheduleRefresh(true, false))
}

func (m *Model) cycle(delta int) tea.Cmd {
	if len(m.convs) == 0 {
		return nil
	}
	i := 0
	for j, c := range m.convs {
		if c.Conv == m.active {
			i = j
		}
	}
	i = (i + delta + len(m.convs)) % len(m.convs)
	m.listSel = i
	return m.switchTo(m.convs[i].Conv)
}

func (m *Model) setFocus(f focus) {
	m.focus = f
	if f == focusComposer {
		m.ta.Focus()
	} else {
		m.ta.Blur()
	}
	if f == focusMessages {
		if m.msgSel < 0 || m.msgSel >= len(m.msgs) {
			m.msgSel = m.lastSelectable()
		}
		m.ensureVisible(m.msgSel)
	}
	m.rebuild(false)
}

func (m *Model) lastSelectable() int {
	for i := len(m.msgs) - 1; i >= 0; i-- {
		if m.msgs[i].Kind != "system" {
			return i
		}
	}
	return len(m.msgs) - 1
}

// ---- sending ---------------------------------------------------------------------

func (m *Model) submit() tea.Cmd {
	text := strings.TrimRight(m.ta.Value(), "\n ")
	if strings.TrimSpace(text) == "" {
		return nil
	}
	m.ta.Reset()
	m.emoji, m.emojiQ = nil, ""
	m.layout()
	if strings.HasPrefix(text, "/") && m.editing == nil {
		return runCommand(m.core, text, m.active)
	}
	if m.active == "" {
		m.toast("Pick a conversation first (Ctrl+K)", true)
		return m.ensureFrames()
	}
	conv := m.active
	if m.editing != nil {
		id := m.editing.ID
		m.editing = nil
		return m.call("edit", map[string]any{"conv": conv, "id": id, "text": text}, func() tea.Msg { return sentMsg{} })
	}
	params := map[string]any{"conv": conv, "text": text}
	if m.replyTo != nil {
		params["reply"] = m.replyTo.ID
		m.replyTo = nil
	}
	m.stickToBottom = true
	return m.call("send", params, func() tea.Msg { return sentMsg{} })
}

func (m *Model) onCommand(r cmdResultMsg) tea.Cmd {
	var cmds []tea.Cmd
	if r.res.Quit {
		return tea.Quit
	}
	if r.res.Conv != "" && r.res.Conv != m.active {
		cmds = append(cmds, m.switchTo(r.res.Conv))
	}
	lines := r.res.Lines
	switch {
	case len(lines) == 1 && !strings.Contains(lines[0], "\n"):
		l := strings.TrimSpace(lines[0])
		m.toast(strings.TrimLeft(l, "! "), strings.HasPrefix(l, "!"))
		cmds = append(cmds, m.ensureFrames())
	case len(lines) > 1:
		var out []string
		for _, l := range lines {
			out = append(out, strings.Split(l, "\n")...)
		}
		m.info = &infoPanel{title: strings.Fields(r.line)[0], lines: out}
	}
	cmds = append(cmds, m.scheduleRefresh(true, true), loadHello(m.core))
	return tea.Batch(cmds...)
}

// ---- keys --------------------------------------------------------------------------

func (m *Model) onKey(k tea.KeyMsg) tea.Cmd {
	key := k.String()
	if m.fatal != "" {
		return tea.Quit
	}
	if key == "ctrl+c" || key == "ctrl+q" {
		return tea.Quit
	}
	if m.ready && m.hello.Name == "" && m.hello.Me != "" {
		return m.onNamingKey(k)
	}
	if m.info != nil {
		switch key {
		case "esc", "enter", "q", " ":
			m.info = nil
		}
		return nil
	}
	if m.pal.open {
		return m.pal.onKey(m, k)
	}
	switch key {
	case "ctrl+k":
		m.pal.show(m)
		return loadPeople(m.core)
	case "ctrl+n", "alt+down":
		return m.cycle(1)
	case "ctrl+p", "alt+up":
		return m.cycle(-1)
	case "tab":
		if m.focus == focusComposer && len(m.emoji) > 0 {
			m.completeEmoji(m.emoji[0])
			return nil
		}
		m.setFocus((m.focus + 1) % 3)
		return nil
	case "shift+tab":
		m.setFocus((m.focus + 2) % 3)
		return nil
	case "f1":
		m.info = helpPanel()
		return nil
	case "pgup":
		m.scrollBy(-(m.vp.Height - 2))
		return m.ensureFrames()
	case "pgdown":
		m.scrollBy(m.vp.Height - 2)
		return m.ensureFrames()
	}
	switch m.focus {
	case focusList:
		return m.onListKey(key)
	case focusMessages:
		return m.onMessagesKey(key)
	}
	return m.onComposerKey(k)
}

func (m *Model) onNamingKey(k tea.KeyMsg) tea.Cmd {
	if k.String() == "enter" {
		name := strings.TrimSpace(m.naming.Value())
		if name == "" {
			return nil
		}
		m.hello.Name = name
		m.naming.Blur()
		return runCommand(m.core, "/nick "+quote(name), "")
	}
	var c tea.Cmd
	m.naming, c = m.naming.Update(k)
	return c
}

func (m *Model) onListKey(key string) tea.Cmd {
	switch key {
	case "up", "k":
		if m.listSel > 0 {
			m.listSel--
		}
	case "down", "j":
		if m.listSel < len(m.convs)-1 {
			m.listSel++
		}
	case "home":
		m.listSel = 0
	case "end":
		m.listSel = len(m.convs) - 1
	case "enter", "right":
		if m.listSel < len(m.convs) {
			c := m.switchTo(m.convs[m.listSel].Conv)
			m.setFocus(focusComposer)
			return c
		}
	case "esc":
		m.setFocus(focusComposer)
	case "?":
		m.info = helpPanel()
	}
	return nil
}

func (m *Model) onMessagesKey(key string) tea.Cmd {
	if len(m.msgs) == 0 {
		if key == "esc" {
			m.setFocus(focusComposer)
		}
		return nil
	}
	sel := m.selected()
	if key != "d" {
		m.armedDel = ""
	}
	switch key {
	case "up", "k":
		m.moveSel(-1)
	case "down", "j":
		m.moveSel(1)
	case "home":
		m.msgSel = 0
		m.moveSel(0)
	case "end":
		m.msgSel = m.lastSelectable()
		m.moveSel(0)
	case "esc":
		m.setFocus(focusComposer)
	case "r", "enter":
		if sel != nil && sel.Kind != "system" {
			cp := *sel
			m.replyTo, m.editing = &cp, nil
			m.setFocus(focusComposer)
			m.layout()
		}
	case "e":
		if sel != nil && sel.Mine && sel.Kind == "text" && !sel.Deleted {
			cp := *sel
			m.editing, m.replyTo = &cp, nil
			m.ta.SetValue(sel.Text)
			m.setFocus(focusComposer)
			m.layout()
		}
	case "d", "delete":
		if sel != nil && sel.Mine && !sel.Deleted {
			if m.armedDel == sel.ID {
				m.armedDel = ""
				return m.call("delete", map[string]any{"conv": m.active, "id": sel.ID}, func() tea.Msg { return sentMsg{} })
			}
			m.armedDel = sel.ID
			m.toast("Press d again to delete for everyone", false)
			return m.ensureFrames()
		}
	case "1", "2", "3", "4", "5", "6":
		if sel != nil && sel.Kind != "system" {
			e := quickReactions[int(key[0]-'1')]
			return m.call("react", map[string]any{"conv": m.active, "id": sel.ID, "emoji": e}, func() tea.Msg { return sentMsg{} })
		}
	case "+":
		if sel != nil && sel.Kind != "system" {
			m.pal.showEmoji(m, sel.ID)
			return loadEmojiFor(m, "")
		}
	case "o":
		if sel != nil && sel.File != nil {
			if sel.File.Path == "" {
				return m.call("download", map[string]any{"file": sel.File.ID}, nil)
			}
			openPath(sel.File.Path)
		}
	case "s":
		if sel != nil && sel.File != nil && sel.File.State != "complete" {
			m.toast("Downloading "+sel.File.Name, false)
			return tea.Batch(m.ensureFrames(), m.call("download", map[string]any{"file": sel.File.ID}, nil))
		}
	case "c", "y":
		if sel != nil {
			copyToClipboard(sel.Text)
			m.toast("Copied", false)
			return m.ensureFrames()
		}
	case "?":
		m.info = helpPanel()
	}
	return nil
}

func (m *Model) selected() *Msg {
	if m.msgSel >= 0 && m.msgSel < len(m.msgs) {
		return &m.msgs[m.msgSel]
	}
	return nil
}

func (m *Model) moveSel(d int) {
	i := m.msgSel + d
	for i >= 0 && i < len(m.msgs) && m.msgs[i].Kind == "system" && d != 0 {
		i += d
	}
	if i < 0 || i >= len(m.msgs) {
		return
	}
	m.msgSel = i
	m.rebuild(false)
	m.ensureVisible(i)
}

func (m *Model) onComposerKey(k tea.KeyMsg) tea.Cmd {
	key := k.String()
	switch key {
	case "enter":
		return m.submit()
	case "esc":
		if m.replyTo != nil || m.editing != nil {
			if m.editing != nil {
				m.ta.Reset()
			}
			m.replyTo, m.editing = nil, nil
			m.layout()
			return nil
		}
		if len(m.emoji) > 0 {
			m.emoji, m.emojiQ = nil, ""
			m.layout()
			return nil
		}
		m.setFocus(focusList)
		return nil
	case "up":
		if m.ta.Value() == "" && len(m.msgs) > 0 {
			m.setFocus(focusMessages)
			return nil
		}
	}
	before := m.ta.Value()
	var c tea.Cmd
	m.ta, c = m.ta.Update(k)
	cmds := []tea.Cmd{c}
	if v := m.ta.Value(); v != before {
		m.layout()
		cmds = append(cmds, m.emojiLookup())
		if m.active != "" && !strings.HasPrefix(v, "/") && time.Since(m.lastTyping) > 3*time.Second {
			m.lastTyping = time.Now()
			cmds = append(cmds, m.call("typing", map[string]any{"conv": m.active}, nil))
		}
	}
	return tea.Batch(cmds...)
}

// ":fir" at the cursor → suggestions above the composer (Tab completes)
func (m *Model) emojiLookup() tea.Cmd {
	v := m.ta.Value()
	i := strings.LastIndexAny(v, " \n")
	word := v[i+1:]
	if len(word) >= 3 && strings.HasPrefix(word, ":") && !strings.HasSuffix(word[1:], ":") {
		q := word[1:]
		if q != m.emojiQ {
			m.emojiQ = q
			return loadEmoji(m.core, q)
		}
		return nil
	}
	if m.emoji != nil {
		m.emoji, m.emojiQ = nil, ""
		m.layout()
	}
	return nil
}

func (m *Model) completeEmoji(h EmojiHit) {
	v := m.ta.Value()
	i := strings.LastIndexAny(v, " \n")
	m.ta.SetValue(v[:i+1] + h.Emoji + " ")
	m.emoji, m.emojiQ = nil, ""
	m.layout()
}

func (m *Model) onMouse(e tea.MouseMsg) tea.Cmd {
	switch e.Button {
	case tea.MouseButtonWheelUp:
		m.scrollBy(-3)
		return m.ensureFrames()
	case tea.MouseButtonWheelDown:
		m.scrollBy(3)
		return m.ensureFrames()
	case tea.MouseButtonLeft:
		if e.Action != tea.MouseActionPress {
			return nil
		}
		sw := m.sidebarWidth()
		if sw > 0 && e.X < sw && e.Y >= 1 {
			i := (e.Y - 1) / 3
			if i >= 0 && i < len(m.convs) {
				m.listSel = i
				return m.switchTo(m.convs[i].Conv)
			}
		} else if e.Y >= m.height-1-m.composerHeight() {
			m.setFocus(focusComposer)
		}
	}
	return nil
}

// ---- helpers ----------------------------------------------------------------------

func oneLine(s string, n int) string {
	s = strings.ReplaceAll(s, "\n", " ")
	r := []rune(s)
	if len(r) > n {
		return string(r[:n-1]) + "…"
	}
	return s
}

func quote(s string) string {
	if strings.ContainsAny(s, " \"'") {
		return "\"" + strings.ReplaceAll(s, "\"", "\\\"") + "\""
	}
	return s
}

func openPath(p string) {
	var c *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		c = exec.Command("rundll32", "url.dll,FileProtocolHandler", p)
	case "darwin":
		c = exec.Command("open", p)
	default:
		c = exec.Command("xdg-open", p)
	}
	_ = c.Start()
}

// OSC 52: works over ssh and in most terminals, no extra dependencies.
func copyToClipboard(s string) {
	fmt.Fprintf(os.Stdout, "\x1b]52;c;%s\a", base64.StdEncoding.EncodeToString([]byte(s)))
}
