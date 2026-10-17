package main

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

// ---- geometry -------------------------------------------------------------------

func (m *Model) sidebarWidth() int {
	if m.width < 72 {
		if m.focus == focusList {
			return m.width
		}
		return 0
	}
	w := m.width / 4
	if w < 26 {
		w = 26
	}
	if w > 36 {
		w = 36
	}
	return w
}

func (m *Model) paneWidth() int {
	sw := m.sidebarWidth()
	if sw >= m.width {
		return m.width
	}
	if sw == 0 {
		return m.width
	}
	return m.width - sw - 1
}

func (m *Model) composerHeight() int {
	h := m.ta.LineCount()
	if h < 1 {
		h = 1
	}
	if h > 6 {
		h = 6
	}
	extra := 2 // border
	if m.replyTo != nil || m.editing != nil {
		extra++
	}
	if len(m.emoji) > 0 {
		extra++
	}
	return h + extra
}

func (m *Model) layout() {
	if m.width == 0 {
		return
	}
	pw := m.paneWidth()
	lines := m.ta.LineCount()
	if lines > 6 {
		lines = 6
	}
	if lines < 1 {
		lines = 1
	}
	m.ta.SetWidth(pw - 4)
	m.ta.SetHeight(lines)
	m.vp.Width = pw
	h := m.height - 3 /*header*/ - m.composerHeight() - 1 /*status*/
	if h < 3 {
		h = 3
	}
	m.vp.Height = h
}

// ---- scrolling -------------------------------------------------------------------

func (m *Model) maxOffset() int {
	o := m.vp.TotalLineCount() - m.vp.Height
	if o < 0 {
		return 0
	}
	return o
}

func (m *Model) animateTo(target int) {
	if target < 0 {
		target = 0
	}
	if mx := m.maxOffset(); target > mx {
		target = mx
	}
	if !m.animating {
		m.scrollPos = float64(m.vp.YOffset)
		m.scrollVel = 0
	}
	m.scrollTarget = target
	m.animating = m.scrollTarget != m.vp.YOffset
	m.stickToBottom = target >= m.maxOffset()
}

func (m *Model) scrollBy(d int) {
	base := m.vp.YOffset
	if m.animating {
		base = m.scrollTarget
	}
	m.animateTo(base + d)
}

func (m *Model) jumpBottom() {
	m.vp.GotoBottom()
	m.animating = false
	m.scrollPos = float64(m.vp.YOffset)
	m.scrollTarget = m.vp.YOffset
}

func (m *Model) ensureVisible(i int) {
	if i < 0 || i >= len(m.starts) {
		return
	}
	top, bottom := m.starts[i], m.ends[i]
	off := m.vp.YOffset
	if m.animating {
		off = m.scrollTarget
	}
	if top < off {
		m.animateTo(top - 1)
	} else if bottom >= off+m.vp.Height {
		m.animateTo(bottom - m.vp.Height + 2)
	}
}

// rebuild re-renders the conversation into the viewport; with animate the
// view glides down to new messages instead of jumping.
func (m *Model) rebuild(animate bool) {
	if m.width == 0 {
		return
	}
	content, starts, ends := m.renderMessages(m.vp.Width)
	m.starts, m.ends = starts, ends
	m.vp.SetContent(content)
	if m.stickToBottom {
		if animate {
			m.animateTo(m.maxOffset())
		} else {
			m.jumpBottom()
		}
	}
}

// ---- messages ---------------------------------------------------------------------

func dayLabel(t time.Time) string {
	now := time.Now()
	y, mo, d := now.Date()
	ty, tmo, td := t.Date()
	switch {
	case y == ty && mo == tmo && d == td:
		return "Today"
	case now.AddDate(0, 0, -1).Day() == td && now.AddDate(0, 0, -1).Month() == tmo:
		return "Yesterday"
	case y == ty:
		return t.Format("Mon 2 Jan")
	}
	return t.Format("2 Jan 2006")
}

func human(n int64) string {
	f := float64(n)
	for _, u := range []string{"B", "KB", "MB"} {
		if f < 1024 {
			return fmt.Sprintf("%.0f %s", f, u)
		}
		f /= 1024
	}
	return fmt.Sprintf("%.1f GB", f)
}

func progressBar(p float64, w int) string {
	full := int(p * float64(w))
	if full > w {
		full = w
	}
	return sAccent.Render(strings.Repeat("━", full)) + sFaint.Render(strings.Repeat("━", w-full))
}

func (m *Model) renderMessages(width int) (string, []int, []int) {
	if m.active == "" {
		return m.emptyState(width, "🐦", "No conversation open",
			"Ctrl+K to find someone · Ctrl+N / Ctrl+P switch chats · F1 help"), nil, nil
	}
	if len(m.msgs) == 0 {
		return m.emptyState(width, "👋", "Say hi to "+m.header.Title,
			"Messages are end-to-end encrypted and delivered as soon as they're in range — directly or through other people's devices."), nil, nil
	}
	group := strings.HasPrefix(m.active, "group:")
	var b strings.Builder
	line := 0
	write := func(s string) {
		b.WriteString(s)
		b.WriteString("\n")
		line += strings.Count(s, "\n") + 1
	}
	starts := make([]int, len(m.msgs))
	ends := make([]int, len(m.msgs))
	write("")
	var lastDay string
	maxW := width * 7 / 10
	if maxW > 72 {
		maxW = 72
	}
	if maxW < 20 {
		maxW = width - 4
	}
	for i, x := range m.msgs {
		t := time.Unix(x.Ts, 0)
		if d := dayLabel(t); d != lastDay {
			lastDay = d
			pill := fillBg(" "+sMuted.Render(d)+" ", cRaised, lipgloss.Width(d)+2)
			write(lipgloss.PlaceHorizontal(width, lipgloss.Center, pill))
			write("")
		}
		starts[i] = line
		selected := m.focus == focusMessages && i == m.msgSel
		if x.Kind == "system" {
			s := sFaint.Italic(true).Render(oneLine(x.Text, width-4))
			write(lipgloss.PlaceHorizontal(width, lipgloss.Center, s))
			ends[i] = line - 1
			write("")
			continue
		}
		write(m.bubble(x, group, maxW, width, selected))
		ends[i] = line - 1
		if i+1 < len(m.msgs) && m.msgs[i+1].Sender == x.Sender && m.msgs[i+1].Ts-x.Ts < 120 {
			continue // tight grouping for bursts
		}
		write("")
	}
	if m.typingHere() != "" {
		dots := []string{"·  ", "·· ", "···", " ··", "  ·", "   "}[m.frame%6]
		write("  " + sAccent.Render(m.typingHere()+" "+dots))
	}
	return strings.TrimRight(b.String(), "\n"), starts, ends
}

func (m *Model) typingHere() string {
	for _, c := range m.convs {
		if c.Conv == m.active && c.Typing && strings.Contains(m.header.Subtitle, "typing") {
			return strings.TrimSuffix(m.header.Subtitle, "…")
		}
	}
	return ""
}

func (m *Model) bubble(x Msg, group bool, maxW, width int, selected bool) string {
	bg := cIncoming
	if x.Mine {
		bg = cAccentBg
	}
	inner := maxW - 4
	var parts []string
	if group && !x.Mine {
		parts = append(parts, lipgloss.NewStyle().Foreground(avatarColor(x.Name)).Bold(true).Render(x.Name))
	}
	if x.Reply != nil {
		q := lipgloss.NewStyle().Foreground(cAccent).Bold(true).Render(x.Reply.Name) + "\n" +
			sMuted.Render(oneLine(x.Reply.Text, inner-4))
		parts = append(parts, lipgloss.NewStyle().Border(lipgloss.ThickBorder(), false, false, false, true).
			BorderForeground(cAccent).PaddingLeft(1).Render(q))
	}
	switch {
	case x.Deleted:
		parts = append(parts, sFaint.Italic(true).Render("message deleted"))
	case x.File != nil:
		f := x.File
		icon := "📎"
		if strings.HasPrefix(f.Mime, "image/") {
			icon = "🖼 "
		}
		head := icon + " " + sBold.Render(f.Name) + sMuted.Render("  "+human(f.Size))
		var st string
		switch f.State {
		case "complete":
			st = sSuccess.Render("✓ saved") + sFaint.Render("  · o open")
		case "downloading":
			st = progressBar(f.Progress, 16) + sMuted.Render(fmt.Sprintf(" %d%%", int(f.Progress*100)))
		default:
			if x.Mine {
				st = sMuted.Render("sent")
			} else {
				st = sFaint.Render("s to download")
			}
		}
		parts = append(parts, head, st)
		if x.Text != "" {
			parts = append(parts, lipgloss.NewStyle().Width(inner).Render(x.Text))
		}
	default:
		parts = append(parts, lipgloss.NewStyle().Foreground(cText).Width(min(inner, lipgloss.Width(x.Text)+1)).Render(x.Text))
	}
	meta := time.Unix(x.Ts, 0).Format("15:04")
	if x.Edited && !x.Deleted {
		meta = "edited · " + meta
	}
	if x.Mine {
		switch x.State {
		case "read":
			meta += " " + lipgloss.NewStyle().Foreground(cSuccess).Render("✓✓")
		case "delivered":
			meta += " ✓✓"
		default:
			meta += " ✓"
		}
	}
	bodyW := 0
	for _, p := range parts {
		bodyW = max(bodyW, lipgloss.Width(p))
	}
	metaS := sFaint.Render(meta)
	bodyW = max(bodyW, lipgloss.Width(metaS))
	parts = append(parts, lipgloss.PlaceHorizontal(bodyW, lipgloss.Right, metaS))
	body := lipgloss.JoinVertical(lipgloss.Left, parts...)
	if selected {
		bg = cSelected
	}
	padded := strings.Split(body, "\n")
	for i, l := range padded {
		padded[i] = " " + l
	}
	box := fillBg(strings.Join(padded, "\n"), bg, bodyW+2)
	if selected { // an accent bar beside the picked message
		bar := sAccent.Render(strings.TrimSuffix(strings.Repeat("▌\n", lipgloss.Height(box)), "\n"))
		box = lipgloss.JoinHorizontal(lipgloss.Top, bar, box)
	}
	if len(x.Reactions) > 0 {
		var rs []string
		for _, r := range x.Reactions {
			s := r.Emoji
			if r.Count > 1 {
				s += fmt.Sprintf(" %d", r.Count)
			}
			fg := cMuted
			if r.Mine {
				fg = cAccent
			}
			rs = append(rs, lipgloss.NewStyle().Foreground(fg).Background(cRaised).Padding(0, 1).Render(s))
		}
		box = lipgloss.JoinVertical(map[bool]lipgloss.Position{true: lipgloss.Right, false: lipgloss.Left}[x.Mine],
			box, strings.Join(rs, " "))
	}
	if x.Mine {
		return lipgloss.PlaceHorizontal(width-1, lipgloss.Right, box)
	}
	return " " + strings.ReplaceAll(box, "\n", "\n ") // a gutter on every line, not just the first
}

func (m *Model) emptyState(width int, icon, title, sub string) string {
	h := m.vp.Height
	block := lipgloss.JoinVertical(lipgloss.Center,
		lipgloss.NewStyle().Render(icon), "",
		sBold.Render(title),
		lipgloss.NewStyle().Foreground(cMuted).Width(min(56, width-4)).Align(lipgloss.Center).Render(sub))
	return lipgloss.Place(width, h, lipgloss.Center, lipgloss.Center, block)
}

// ---- sidebar -------------------------------------------------------------------------

func (m *Model) renderSidebar(w, h int) string {
	var rows []string
	title := sBold.Render(" Chats")
	if m.focus == focusList {
		title = lipgloss.NewStyle().Foreground(cAccent).Bold(true).Render(" Chats")
	}
	rows = append(rows, title)
	for i, c := range m.convs {
		rows = append(rows, m.convRow(c, w, i == m.listSel && m.focus == focusList, c.Conv == m.active))
	}
	if len(m.convs) == 0 {
		rows = append(rows, "", sFaint.Width(w-1).PaddingLeft(1).Render("Nobody here yet — Ctrl+K → /find looks for people nearby"))
	}
	out := strings.Join(rows, "\n")
	return lipgloss.NewStyle().Width(w).Height(h).MaxHeight(h).Render(out)
}

func (m *Model) convRow(c Conv, w int, cursor, active bool) string {
	initial := "#"
	if !c.Group {
		r := []rune(strings.TrimSpace(c.Title))
		if len(r) > 0 {
			initial = strings.ToUpper(string(r[0]))
		}
	}
	av := lipgloss.NewStyle().Foreground(lipgloss.Color("#ffffff")).Background(avatarColor(c.Title)).Bold(true).Padding(0, 1).Render(initial)
	dot := lipgloss.NewStyle().Foreground(statusColor(c.Status)).Render("●")
	if c.Group {
		dot = " "
	}
	nameW := w - 12
	name := oneLine(c.Title, nameW)
	nameStyle := sText
	if c.Unread > 0 {
		nameStyle = sBold
	}
	right := ""
	if c.Unread > 0 {
		n := fmt.Sprint(c.Unread)
		if c.Unread > 99 {
			n = "99+"
		}
		right = lipgloss.NewStyle().Foreground(cBadgeText).Background(cAccent).Bold(true).Padding(0, 1).Render(n)
	} else if c.Pending > 0 {
		right = sFaint.Render(fmt.Sprintf("◌%d", c.Pending))
	} else if c.Ts > 0 {
		right = sFaint.Render(shortTime(c.Ts))
	}
	if c.Muted {
		right = sFaint.Render("🔕") + right
	}
	gap := w - 7 - lipgloss.Width(name) - lipgloss.Width(right)
	if gap < 1 {
		gap = 1
	}
	line1 := av + dot + " " + nameStyle.Render(name) + strings.Repeat(" ", gap) + right
	sub := c.Preview
	subStyle := sMuted
	if c.Typing {
		sub, subStyle = "typing…", sAccent
	} else if sub == "" {
		sub = c.Subtitle
		subStyle = sFaint
	}
	line2 := "      " + subStyle.Render(oneLine(sub, w-8))
	mark := " "
	if active {
		mark = sAccent.Render("▌")
	}
	row := mark + line1 + "\n " + line2
	switch {
	case cursor:
		row = fillBg(row, cSelected, w)
	case active:
		row = fillBg(row, cRaised, w)
	}
	return row + "\n"
}

func shortTime(ts int64) string {
	t := time.Unix(ts, 0)
	if time.Since(t) < 20*time.Hour && t.Day() == time.Now().Day() {
		return t.Format("15:04")
	}
	if time.Since(t) < 6*24*time.Hour {
		return t.Format("Mon")
	}
	return t.Format("2 Jan")
}

// ---- composer, header, status ------------------------------------------------------------

func (m *Model) renderHeader(w int) string {
	title, sub := m.header.Title, m.header.Subtitle
	if m.active == "" {
		title, sub = "Muninn", "encrypted Bluetooth mesh chat"
	}
	left := sBold.Render(title) + "\n" + sMuted.Render(oneLine(sub, w-24))
	hint := sFaint.Render("Ctrl+K ") + sMuted.Render("go to…")
	gap := w - lipgloss.Width(sBold.Render(title)) - lipgloss.Width(hint) - 2
	if gap < 1 {
		gap = 1
	}
	top := " " + sBold.Render(title) + strings.Repeat(" ", gap) + hint
	_ = left
	return top + "\n " + sMuted.Render(oneLine(sub, w-2)) + "\n" + sFaint.Render(strings.Repeat("─", w))
}

func (m *Model) renderComposer(w int) string {
	var top []string
	if m.replyTo != nil {
		top = append(top, sAccent.Render("↪ replying to "+m.replyTo.Name+": ")+sMuted.Render(oneLine(m.replyTo.Text, w-30))+sFaint.Render("  esc cancels"))
	}
	if m.editing != nil {
		top = append(top, sAccent.Render("✎ editing your message")+sFaint.Render("  enter saves · esc cancels"))
	}
	if len(m.emoji) > 0 {
		var hs []string
		for i, h := range m.emoji {
			if i >= 8 {
				break
			}
			s := h.Emoji + " " + sFaint.Render(":"+h.Code+":")
			if i == 0 {
				s = h.Emoji + " " + sAccent.Render(":"+h.Code+":") + sFaint.Render(" ⇥")
			}
			hs = append(hs, s)
		}
		top = append(top, oneLineANSI(strings.Join(hs, "   "), w-2))
	}
	border := cFaint
	if m.focus == focusComposer {
		border = cAccent
	}
	box := lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(border).Padding(0, 1).Width(w - 2).Render(m.ta.View())
	if len(top) > 0 {
		return " " + strings.Join(top, "\n ") + "\n" + box
	}
	return box
}

func oneLineANSI(s string, w int) string {
	return lipgloss.NewStyle().MaxWidth(w).Render(s)
}

func (m *Model) renderStatus(w int) string {
	name := m.hello.Name
	if name == "" {
		name = "…"
	}
	online := 0
	for _, c := range m.convs {
		if c.Status == "direct" || c.Status == "relay" {
			online++
		}
	}
	dot := sFaint.Render("●")
	if online > 0 {
		dot = sSuccess.Render("●")
	}
	left := " " + sAccent.Render("◆ ") + sBold.Render(name) + "  " + dot + sMuted.Render(fmt.Sprintf(" %d in range", online))
	if m.hello.Devices > 1 {
		left += sMuted.Render(fmt.Sprintf(" · %d devices", m.hello.Devices))
	}
	if m.hello.Sim {
		left += sFaint.Render(" · SIM")
	}
	var keys [][2]string
	switch m.focus {
	case focusList:
		keys = [][2]string{{"↑↓", "move"}, {"enter", "open"}, {"tab", "next"}, {"F1", "help"}}
	case focusMessages:
		keys = [][2]string{{"↑↓", "pick"}, {"r", "reply"}, {"1-6", "react"}, {"e", "edit"}, {"d", "delete"}, {"o", "open"}, {"esc", "back"}}
	default:
		keys = [][2]string{{"enter", "send"}, {"↑", "messages"}, {"^N/^P", "chats"}, {"^K", "go to"}, {"F1", "help"}}
	}
	var ks []string
	for _, k := range keys {
		ks = append(ks, sKey.Render(k[0])+" "+sMuted.Render(k[1]))
	}
	right := strings.Join(ks, sFaint.Render("  ")) + " "
	gap := w - lipgloss.Width(left) - lipgloss.Width(right)
	if gap < 1 {
		right = ""
		gap = w - lipgloss.Width(left)
		if gap < 0 {
			gap = 0
		}
	}
	return fillBg(left+strings.Repeat(" ", gap)+right, cSurface, w)
}

// ---- overlays ---------------------------------------------------------------------------------

func overlay(base, box string, w, h int) string {
	// center box over base (base lines kept left and right of it)
	bl := strings.Split(base, "\n")
	for len(bl) < h {
		bl = append(bl, "")
	}
	ol := strings.Split(box, "\n")
	bw := lipgloss.Width(box)
	x := (w - bw) / 2
	y := (h - len(ol)) / 3
	if x < 0 {
		x = 0
	}
	if y < 0 {
		y = 0
	}
	for i, l := range ol {
		if y+i >= len(bl) {
			break
		}
		row := bl[y+i]
		left := truncateANSI(row, x)
		right := skipANSI(row, x+bw)
		bl[y+i] = left + strings.Repeat(" ", max(0, x-lipgloss.Width(left))) + l + right
	}
	return strings.Join(bl[:h], "\n")
}

func (m *Model) panelBox(title string, body string, w int) string {
	if title != "" {
		title = strings.ToUpper(title[:1]) + title[1:]
	}
	inner := fillBg(" "+sBold.Render(title)+"\n "+strings.ReplaceAll(body, "\n", "\n "), cSurface, w-2)
	return lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cAccent).
		BorderBackground(cSurface).Render(inner)
}

func helpPanel() *infoPanel {
	return &infoPanel{title: "Keys", lines: []string{
		"Anywhere",
		"  Ctrl+K          go to a chat, start one with someone, run a command",
		"  Ctrl+N / Ctrl+P next / previous chat   (also Alt+↓ / Alt+↑)",
		"  Tab / Shift+Tab move between chats, composer and messages",
		"  PgUp / PgDn     scroll · mouse wheel scrolls, click a chat to open it",
		"  Ctrl+C          quit",
		"Composer",
		"  Enter send · Alt+Enter or Ctrl+J new line · ↑ (empty) pick a message",
		"  :fire  → emoji suggestions, Tab completes · /command runs a command",
		"Messages (after ↑ or Tab)",
		"  ↑ ↓ pick · r reply · e edit · d d delete · 1-6 👍❤️😂😮😢🔥 · + any emoji",
		"  o open / download a file · s download · c copy text · Esc back",
		"Commands (type them, or find them in Ctrl+K)",
		"  /find  /nick <name>  /new <room> <people…>  /add  /leave  /file <path>",
		"  /link  /devices  /doctor  /ping <name>  /mute  /search <words>  /help",
	}}
}
