package main

import (
	"sort"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

type palItem struct {
	icon, label, sub string
	conv             string // open this chat
	person           string // start a DM
	command          string // run (or prefill when it ends in a space)
	emoji            string // react with it
}

type palette struct {
	open     bool
	input    textinput.Model
	items    []palItem
	sel      int
	people   []Person
	emojiFor string // message id when picking a reaction
	emojis   []EmojiHit
}

var paletteCommands = []palItem{
	{icon: "🔍", label: "Find people nearby", sub: "/find · look for new people for 5 minutes", command: "/find"},
	{icon: "✎", label: "Set your name", sub: "/nick <name>", command: "/nick "},
	{icon: "#", label: "New room", sub: "/new <name> <people…>", command: "/new "},
	{icon: "+", label: "Add people to this room", sub: "/add <people…>", command: "/add "},
	{icon: "👥", label: "Members of this room", sub: "/members", command: "/members"},
	{icon: "↩", label: "Leave this room", sub: "/leave", command: "/leave"},
	{icon: "📎", label: "Send a file or photo", sub: "/file <path>", command: "/file "},
	{icon: "🔗", label: "Link another device", sub: "/link · shows a code (or /link <device> <code>)", command: "/link"},
	{icon: "💻", label: "Your linked devices", sub: "/devices", command: "/devices"},
	{icon: "✈", label: "Check before the flight", sub: "/doctor", command: "/doctor"},
	{icon: "⏱", label: "Round-trip time", sub: "/ping <name>", command: "/ping "},
	{icon: "🔕", label: "Mute this chat", sub: "/mute · /unmute", command: "/mute"},
	{icon: "🔎", label: "Search messages", sub: "/search <words>", command: "/search "},
	{icon: "📊", label: "Who's around", sub: "/peers", command: "/peers"},
	{icon: "🗄", label: "Back up your chats", sub: "/export [file] [pw=…]", command: "/export"},
	{icon: "🖼", label: "Photo quality", sub: "/photos compressed|original", command: "/photos "},
	{icon: "🛡", label: "Privacy", sub: "/privacy · read receipts and typing", command: "/privacy"},
	{icon: "🧹", label: "Delete this chat on this device", sub: "/clear (twice)", command: "/clear"},
	{icon: "?", label: "All commands", sub: "/help", command: "/help"},
}

func newPalette() palette {
	ti := textinput.New()
	ti.Placeholder = "chat, person, or command…"
	ti.Prompt = "❯ "
	ti.PromptStyle = sAccent
	ti.CharLimit = 120
	return palette{input: ti}
}

func (p *palette) show(m *Model) {
	p.open, p.emojiFor, p.sel = true, "", 0
	p.input.SetValue("")
	p.input.Placeholder = "chat, person, or command…"
	p.input.Focus()
	p.filter(m)
}

func (p *palette) showEmoji(m *Model, msgID string) {
	p.open, p.emojiFor, p.sel = true, msgID, 0
	p.input.SetValue("")
	p.input.Placeholder = "react with… (fire, heart, plane)"
	p.input.Focus()
	p.filter(m)
}

// fuzzy: every query rune in order; earlier and tighter matches score higher
func score(q, s string) int {
	if q == "" {
		return 1
	}
	q, s = strings.ToLower(q), strings.ToLower(s)
	if i := strings.Index(s, q); i >= 0 {
		return 1000 - i
	}
	si, sc, last := 0, 500, -1
	for _, r := range q {
		j := strings.IndexRune(s[si:], r)
		if j < 0 {
			return 0
		}
		if last >= 0 && j > 0 {
			sc -= j
		}
		si += j + len(string(r))
		last = si
	}
	return sc
}

type scored struct {
	it palItem
	s  int
}

func (p *palette) filter(m *Model) {
	q := strings.TrimSpace(p.input.Value())
	var out []scored
	add := func(it palItem, hay string) {
		if s := score(q, hay); s > 0 {
			out = append(out, scored{it, s})
		}
	}
	if p.emojiFor != "" {
		for _, e := range quickReactions {
			if q == "" {
				out = append(out, scored{palItem{icon: e, label: e, emoji: e}, 2000})
			}
		}
		for _, h := range p.emojis {
			out = append(out, scored{palItem{icon: h.Emoji, label: ":" + h.Code + ":", emoji: h.Emoji}, 100})
		}
	} else {
		known := map[string]bool{}
		for _, c := range m.convs {
			icon := "●"
			if c.Group {
				icon = "#"
			}
			add(palItem{icon: icon, label: c.Title, sub: c.Subtitle, conv: c.Conv}, c.Title)
			known[c.Conv] = true
		}
		for _, pe := range p.people {
			if !known["dm:"+pe.ID] {
				add(palItem{icon: "○", label: pe.Name, sub: "start a chat · " + pe.Subtitle, person: pe.ID}, pe.Name)
			}
		}
		for _, c := range paletteCommands {
			s := score(q, c.label+" "+c.sub)
			if strings.HasPrefix(q, "/") {
				s = score(q, c.sub)
			}
			if s > 0 {
				out = append(out, scored{c, s / 2})
			}
		}
		if strings.HasPrefix(q, "/") {
			out = append([]scored{{palItem{icon: "⏎", label: "Run " + q, command: q}, 5000}}, out...)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].s > out[j].s })
	p.items = p.items[:0]
	for i, s := range out {
		if i >= 12 {
			break
		}
		p.items = append(p.items, s.it)
	}
	if p.sel >= len(p.items) {
		p.sel = max(0, len(p.items)-1)
	}
}

func (p *palette) onKey(m *Model, k tea.KeyMsg) tea.Cmd {
	switch k.String() {
	case "esc", "ctrl+k":
		p.open = false
		return nil
	case "up", "ctrl+p":
		if p.sel > 0 {
			p.sel--
		}
		return nil
	case "down", "ctrl+n", "tab":
		if p.sel < len(p.items)-1 {
			p.sel++
		}
		return nil
	case "enter":
		if p.sel >= len(p.items) {
			return nil
		}
		it := p.items[p.sel]
		p.open = false
		switch {
		case it.emoji != "":
			return m.call("react", map[string]any{"conv": m.active, "id": p.emojiFor, "emoji": it.emoji}, func() tea.Msg { return sentMsg{} })
		case it.conv != "":
			m.setFocus(focusComposer)
			return m.switchTo(it.conv)
		case it.person != "":
			m.setFocus(focusComposer)
			return m.switchTo("dm:" + it.person)
		case strings.HasSuffix(it.command, " "):
			m.setFocus(focusComposer)
			m.ta.SetValue(it.command)
			m.ta.CursorEnd()
			m.layout()
			return nil
		case it.command != "":
			return runCommand(m.core, it.command, m.active)
		}
		return nil
	}
	var c tea.Cmd
	before := p.input.Value()
	p.input, c = p.input.Update(k)
	if p.input.Value() != before {
		p.sel = 0
		if p.emojiFor != "" {
			return tea.Batch(c, loadEmojiFor(m, p.input.Value()))
		}
		p.filter(m)
	}
	return c
}

func loadEmojiFor(m *Model, q string) tea.Cmd {
	core := m.core
	return func() tea.Msg {
		var hits []EmojiHit
		_ = core.Call("emoji", map[string]any{"q": q}, &hits)
		return paletteEmojiMsg{q: q, hits: hits}
	}
}

type paletteEmojiMsg struct {
	q    string
	hits []EmojiHit
}

func (p *palette) view(w int) string {
	var rows []string
	rows = append(rows, p.input.View(), sFaint.Render(strings.Repeat("─", w-4)))
	for i, it := range p.items {
		label := sText.Render(it.label)
		sub := sMuted.Render(ansi.Truncate(it.sub, w-lipgloss.Width(it.label)-10, "…"))
		row := " " + it.icon + "  " + label + "  " + sub
		if i == p.sel {
			row = fillBg(sAccent.Render("▌")+row[1:], cSelected, w-4)
		}
		rows = append(rows, row)
	}
	if len(p.items) == 0 {
		rows = append(rows, sFaint.Render(" nothing matches"))
	}
	rows = append(rows, sFaint.Render(" ↑↓ choose · enter go · esc close"))
	inner := fillBg(" "+strings.Join(rows, "\n "), cSurface, w-2)
	return lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cAccent).
		BorderBackground(cSurface).Render(inner)
}
