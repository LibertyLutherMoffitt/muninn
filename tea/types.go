package main

// Shapes returned by the core (python/src/muninn/api.py).

type Hello struct {
	Version string `json:"version"`
	Name    string `json:"name"`
	Me      string `json:"me"`
	Node    string `json:"node"`
	Devices int    `json:"devices"`
	Sim     bool   `json:"sim"`
}

type Conv struct {
	Conv     string `json:"conv"`
	Title    string `json:"title"`
	Group    bool   `json:"group"`
	Preview  string `json:"preview"`
	Ts       int64  `json:"ts"`
	Unread   int    `json:"unread"`
	Status   string `json:"status"` // direct | relay | nearby | offline | group
	Subtitle string `json:"subtitle"`
	Typing   bool   `json:"typing"`
	Pending  int    `json:"pending"`
	Muted    bool   `json:"muted"`
}

type Reaction struct {
	Emoji string   `json:"emoji"`
	Count int      `json:"count"`
	Mine  bool     `json:"mine"`
	Names []string `json:"names"`
}

type Reply struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Text string `json:"text"`
}

type File struct {
	ID       string  `json:"id"`
	Name     string  `json:"name"`
	Size     int64   `json:"size"`
	Mime     string  `json:"mime"`
	State    string  `json:"state"`
	Progress float64 `json:"progress"`
	Path     string  `json:"path"`
}

type Msg struct {
	ID        string     `json:"id"`
	Sender    string     `json:"sender"`
	Name      string     `json:"name"`
	Mine      bool       `json:"mine"`
	Ts        int64      `json:"ts"`
	Kind      string     `json:"kind"` // text | file | system
	Text      string     `json:"text"`
	Edited    bool       `json:"edited"`
	Deleted   bool       `json:"deleted"`
	State     string     `json:"state"` // sent | delivered | read (mine only)
	Reactions []Reaction `json:"reactions"`
	Reply     *Reply     `json:"reply"`
	File      *File      `json:"file"`
}

type Header struct {
	Title    string `json:"title"`
	Subtitle string `json:"subtitle"`
}

type Person struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Status   string `json:"status"`
	Subtitle string `json:"subtitle"`
}

type CmdResult struct {
	Lines []string `json:"lines"`
	Conv  string   `json:"conv"`
	Quit  bool     `json:"quit"`
}

type EmojiHit struct {
	Code  string `json:"code"`
	Emoji string `json:"emoji"`
}
