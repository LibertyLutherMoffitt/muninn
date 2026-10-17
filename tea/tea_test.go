package main

import (
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"
)

func TestScoreRanksPrefixAndSubstringOverScattered(t *testing.T) {
	if score("", "anything") <= 0 {
		t.Fatal("empty query matches everything")
	}
	if score("xyz", "bob") != 0 {
		t.Fatal("no match must score 0")
	}
	if !(score("bo", "bob") > score("bo", "a bob") && score("bo", "a bob") > score("bb", "bob")) {
		t.Fatal("prefix > substring > subsequence")
	}
}

func TestFillBgSurvivesInnerResets(t *testing.T) {
	lipgloss.SetColorProfile(termenv.TrueColor)
	defer lipgloss.SetColorProfile(termenv.Ascii)
	s := fillBg(sBold.Render("a")+" b", lipgloss.Color("#123456"), 6)
	if got := ansi.Strip(s); got != "a b   " {
		t.Fatalf("padded to width: %q", got)
	}
	bg := "\x1b[48;2;18;52;86m"
	if strings.Count(s, bg) < 2 { // once at the start, again after the bold span resets
		t.Fatalf("background not re-applied after reset: %q", s)
	}
}

func TestRenderMessagesLayout(t *testing.T) {
	m := NewModel(nil)
	m.width, m.height = 100, 30
	m.layout()
	m.active = "dm:AA"
	m.header = Header{Title: "bob"}
	now := time.Now().Unix()
	m.msgs = []Msg{
		{ID: "1", Name: "bob", Ts: now - 60, Kind: "text", Text: "hi there"},
		{ID: "2", Name: "You", Mine: true, Ts: now, Kind: "text", Text: "hello", State: "read",
			Reactions: []Reaction{{Emoji: "👍", Count: 2}}, Reply: &Reply{Name: "bob", Text: "hi there"}},
		{ID: "3", Name: "bob", Ts: now, Kind: "file", File: &File{Name: "seat.pdf", Size: 2048, State: "downloading", Progress: 0.5}},
	}
	out, starts, ends := m.renderMessages(m.vp.Width)
	plain := ansi.Strip(out)
	for _, want := range []string{"Today", "hi there", "hello", "✓✓", "👍 2", "seat.pdf", "2 KB", "50%"} {
		if !strings.Contains(plain, want) {
			t.Errorf("missing %q in:\n%s", want, plain)
		}
	}
	if len(starts) != 3 || starts[1] <= ends[0] || ends[2] < starts[2] {
		t.Fatalf("message line spans wrong: %v %v", starts, ends)
	}
	for _, l := range strings.Split(plain, "\n") {
		if lipgloss.Width(l) > m.vp.Width {
			t.Fatalf("line wider than the pane (%d > %d): %q", lipgloss.Width(l), m.vp.Width, l)
		}
	}
	// my message sits on the right
	for _, l := range strings.Split(plain, "\n") {
		if strings.Contains(l, "hello") && strings.Index(l, "hello") < m.vp.Width/2 {
			t.Fatalf("own message not right-aligned: %q", l)
		}
	}
}

func TestOneLine(t *testing.T) {
	if got := oneLine("a\nb", 10); got != "a b" {
		t.Fatal(got)
	}
	if got := oneLine("abcdefghij", 5); got != "abcd…" {
		t.Fatal(got)
	}
}
