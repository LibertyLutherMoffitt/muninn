// muninn-tea: a polished terminal client for Muninn, built with Bubble Tea.
//
// It drives the Python core (`muninn --api`, python/src/muninn/api.py) over
// stdio — the protocol, Bluetooth and storage stay in the one tested core.
// Same data directory and environment as the other clients (MUNINN_DATA,
// MUNINN_SIM, MUNINN_NAME); MUNINN_CORE picks the core command.
package main

import (
	"fmt"
	"os"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

var version = "dev"

func main() {
	for _, a := range os.Args[1:] {
		switch a {
		case "-v", "--version":
			fmt.Println("muninn-tea", version)
			return
		case "-h", "--help":
			fmt.Println("muninn-tea — Muninn in your terminal (F1 inside for keys).\n\n" +
				"Needs the Muninn core: `muninn` next to this program or on PATH, or MUNINN_CORE=\"<command>\".\n" +
				"Environment: MUNINN_DATA (data dir), MUNINN_SIM=host:port (simulator), MUNINN_NAME.")
			return
		}
	}
	core, err := StartCore()
	if err != nil {
		fmt.Fprintln(os.Stderr, "muninn-tea:", err)
		os.Exit(1)
	}
	p := tea.NewProgram(NewModel(core), tea.WithAltScreen(), tea.WithMouseCellMotion())
	_, err = p.Run()
	core.Close()
	if err != nil {
		fmt.Fprintln(os.Stderr, "muninn-tea:", err)
		os.Exit(1)
	}
}

func truncateANSI(s string, w int) string { return ansi.Truncate(s, w, "") }
func skipANSI(s string, w int) string     { return ansi.TruncateLeft(s, w, "") }

func (m Model) View() string {
	if m.width == 0 {
		return ""
	}
	if m.fatal != "" {
		box := lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cError).Padding(1, 2).Width(min(70, m.width-4)).
			Render(sError.Bold(true).Render("Muninn can't run") + "\n\n" + sText.Render(m.fatal) + "\n\n" + sFaint.Render("any key quits"))
		return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, box)
	}
	if !m.ready {
		return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center,
			sAccent.Render("◆ ")+sBold.Render("Muninn")+sMuted.Render("  starting…"))
	}
	if m.hello.Me != "" && m.hello.Name == "" {
		box := lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cAccent).Padding(1, 3).Width(min(60, m.width-4)).
			Render(sAccent.Render("◆ ") + sBold.Render("Welcome to Muninn") + "\n\n" +
				sMuted.Render("Chat with people around you over Bluetooth — no internet, no accounts. What should people call you?") +
				"\n\n" + m.naming.View() + "\n\n" + sFaint.Render("enter to continue"))
		return lipgloss.Place(m.width, m.height, lipgloss.Center, lipgloss.Center, box)
	}
	sw := m.sidebarWidth()
	bodyH := m.height - 1
	var body string
	if sw >= m.width { // narrow terminal: the list takes the whole screen
		body = m.renderSidebar(m.width, bodyH)
	} else {
		pw := m.paneWidth()
		pane := lipgloss.JoinVertical(lipgloss.Left,
			m.renderHeader(pw),
			lipgloss.NewStyle().Width(pw).Height(m.vp.Height).Render(m.vp.View()),
			m.renderComposer(pw))
		pane = lipgloss.NewStyle().Height(bodyH).MaxHeight(bodyH).Render(pane)
		if sw > 0 {
			sep := sFaint.Render(strings.TrimSuffix(strings.Repeat("│\n", bodyH), "\n"))
			body = lipgloss.JoinHorizontal(lipgloss.Top, m.renderSidebar(sw, bodyH), sep, pane)
		} else {
			body = pane
		}
	}
	body = lipgloss.NewStyle().Height(bodyH).MaxHeight(bodyH).Render(body)
	switch {
	case m.pal.open:
		body = overlay(body, m.pal.view(min(76, m.width-4)), m.width, bodyH)
	case m.info != nil:
		lines := m.info.lines
		if max := bodyH - 6; len(lines) > max && max > 0 {
			lines = append(lines[:max], "…")
		}
		box := m.panelBox(strings.TrimPrefix(m.info.title, "/"), strings.Join(lines, "\n")+"\n"+sFaint.Render("esc / enter close"), min(96, m.width-4))
		body = overlay(body, box, m.width, bodyH)
	}
	if len(m.toasts) > 0 {
		var ts []string
		for _, t := range m.toasts {
			st := lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(cAccent).Padding(0, 1).Foreground(cText)
			if t.err {
				st = st.BorderForeground(cError)
			}
			ts = append(ts, st.Render(oneLine(t.text, min(70, m.width-8))))
		}
		box := lipgloss.JoinVertical(lipgloss.Right, ts...)
		lines := strings.Split(body, "\n")
		bl := strings.Split(box, "\n")
		y := len(lines) - len(bl) - m.composerHeight() - 1
		x := m.width - lipgloss.Width(box) - 2
		for i, l := range bl {
			if y+i >= 0 && y+i < len(lines) && x > 0 {
				row := lines[y+i]
				lines[y+i] = truncateANSI(row, x) + strings.Repeat(" ", max(0, x-lipgloss.Width(truncateANSI(row, x)))) + l + skipANSI(row, x+lipgloss.Width(l))
			}
		}
		body = strings.Join(lines, "\n")
	}
	return body + "\n" + m.renderStatus(m.width)
}
