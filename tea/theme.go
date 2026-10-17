package main

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Same palette as the desktop GUI (python/src/muninn/gui/main.py THEMES),
// picked automatically for dark or light terminals.
var (
	cBg        = lipgloss.AdaptiveColor{Light: "#f6f7fb", Dark: "#0f1115"}
	cSurface   = lipgloss.AdaptiveColor{Light: "#ffffff", Dark: "#151820"}
	cRaised    = lipgloss.AdaptiveColor{Light: "#eceef5", Dark: "#1b1f2a"}
	cText      = lipgloss.AdaptiveColor{Light: "#1f2330", Dark: "#e5e7eb"}
	cMuted     = lipgloss.AdaptiveColor{Light: "#6b7280", Dark: "#9ca3af"}
	cFaint     = lipgloss.AdaptiveColor{Light: "#9ca3af", Dark: "#4b5563"}
	cAccent    = lipgloss.AdaptiveColor{Light: "#7c3aed", Dark: "#a78bfa"}
	cAccentBg  = lipgloss.AdaptiveColor{Light: "#ddd6fe", Dark: "#3b2a6a"}
	cIncoming  = lipgloss.AdaptiveColor{Light: "#eceef5", Dark: "#1f2330"}
	cSelected  = lipgloss.AdaptiveColor{Light: "#e4dcfd", Dark: "#2a1f4a"}
	cSuccess   = lipgloss.AdaptiveColor{Light: "#059669", Dark: "#10b981"}
	cWarning   = lipgloss.AdaptiveColor{Light: "#d97706", Dark: "#f59e0b"}
	cError     = lipgloss.AdaptiveColor{Light: "#dc2626", Dark: "#ef4444"}
	cNearby    = lipgloss.AdaptiveColor{Light: "#3b82f6", Dark: "#7fa7d9"}
	cOnAccent  = lipgloss.AdaptiveColor{Light: "#ffffff", Dark: "#ffffff"}
	cBadgeText = lipgloss.AdaptiveColor{Light: "#ffffff", Dark: "#0f1115"}
)

var (
	sMuted   = lipgloss.NewStyle().Foreground(cMuted)
	sFaint   = lipgloss.NewStyle().Foreground(cFaint)
	sAccent  = lipgloss.NewStyle().Foreground(cAccent)
	sBold    = lipgloss.NewStyle().Foreground(cText).Bold(true)
	sText    = lipgloss.NewStyle().Foreground(cText)
	sError   = lipgloss.NewStyle().Foreground(cError)
	sSuccess = lipgloss.NewStyle().Foreground(cSuccess)
	sKey     = lipgloss.NewStyle().Foreground(cAccent).Bold(true)
)

func statusColor(s string) lipgloss.TerminalColor {
	switch s {
	case "direct":
		return cSuccess
	case "relay":
		return cWarning
	case "nearby":
		return cNearby
	case "group":
		return cAccent
	}
	return cFaint
}

// avatar colours, chosen by name so a person keeps theirs
var avatarColors = []string{"#7c3aed", "#db2777", "#2563eb", "#0d9488", "#ea580c", "#65a30d", "#9333ea", "#0891b2"}

func avatarColor(name string) lipgloss.Color {
	h := 0
	for _, r := range name {
		h = h*31 + int(r)
	}
	if h < 0 {
		h = -h
	}
	return lipgloss.Color(avatarColors[h%len(avatarColors)])
}

// fillBg paints bg behind every line of s (padded to width), surviving the
// resets inside already-styled spans — lipgloss' own Background stops at the
// first inner reset.
func fillBg(s string, bg lipgloss.TerminalColor, width int) string {
	seq := strings.TrimSuffix(lipgloss.NewStyle().Background(bg).Render("x"), "x\x1b[0m")
	if seq == lipgloss.NewStyle().Background(bg).Render("x") { // no colours
		seq = ""
	}
	lines := strings.Split(s, "\n")
	for i, l := range lines {
		if pad := width - lipgloss.Width(l); pad > 0 {
			l += strings.Repeat(" ", pad)
		}
		if seq != "" {
			l = seq + strings.ReplaceAll(l, "\x1b[0m", "\x1b[0m"+seq) + "\x1b[0m"
		}
		lines[i] = l
	}
	return strings.Join(lines, "\n")
}
