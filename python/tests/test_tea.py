"""End to end: the Bubble Tea client (tea/) driving the core over --api,
in a real terminal (tmux). Skipped without go or tmux."""

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from muninn.mesh import dm_conv
from muninn.sim.lab import wait_until

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not (shutil.which("go") and shutil.which("tmux")), reason="needs go and tmux"
)


@pytest.fixture
def tea(lab, tmp_path):
    binary = tmp_path / "muninn-tea"
    subprocess.run(
        ["go", "build", "-o", str(binary), "."], cwd=ROOT / "tea", check=True
    )
    sock = f"muninn-test-{uuid.uuid4().hex[:8]}"
    env = {
        "MUNINN_SIM": lab.addr,
        "MUNINN_DATA": str(tmp_path / "me"),
        "MUNINN_NAME": "ann",
        "MUNINN_CORE": f"{sys.executable} -m muninn.cli",
        "PYTHONPATH": str(ROOT / "python/src"),
        "TERM": "xterm-256color",
    }
    cmd = " ".join(f"{k}='{v}'" for k, v in env.items()) + f" {binary}"
    subprocess.run(
        [
            "tmux",
            "-L",
            sock,
            "new-session",
            "-d",
            "-s",
            "t",
            "-x",
            "110",
            "-y",
            "30",
            cmd,
        ],
        check=True,
    )

    class T:
        def keys(self, *k):
            subprocess.run(["tmux", "-L", sock, "send-keys", "-t", "t", *k], check=True)

        def screen(self):
            return subprocess.run(
                ["tmux", "-L", sock, "capture-pane", "-p", "-t", "t"],
                capture_output=True,
                text=True,
            ).stdout

        def wait(self, text, timeout=30):
            wait_until(
                lambda: text in self.screen(),
                timeout,
                msg=f"{text!r} on screen:\n{self.screen()}",
            )

    yield T()
    subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)


def test_tea_chat_end_to_end(lab, tea):
    bob = lab.node("bob", (1, 0))
    tea.wait("bob")  # bob shows up in the chat list
    tea.wait("Say hi to bob")  # …and is opened
    tea.keys("hello from the terminal :fire")
    tea.wait(":fire:")  # emoji suggestion strip
    tea.keys("Tab")
    tea.keys("Enter")

    def ann():
        for p in bob.mesh.active_peers():
            hist = bob.storage.load_history(dm_conv(p.node_id))
            if any(m.text.startswith("hello from the terminal 🔥") for m in hist):
                return p.node_id
        return None

    wait_until(lambda: ann() is not None, 20)
    me = ann()
    tea.wait("✓✓")  # delivered
    bob.mesh.send_text(dm_conv(me), "hi ann, gate 23")
    tea.wait("hi ann, gate 23")
    # pick bob's message, react with 👍 (key 1), then reply with r
    tea.keys("Up")
    tea.wait("1-6 react")
    tea.keys("1")
    wait_until(
        lambda: any(
            m.reactions
            for m in bob.storage.load_history(dm_conv(me))
            if m.text == "hi ann, gate 23"
        ),
        15,
    )
    tea.keys("r")
    tea.wait("replying to bob")
    tea.keys("see you there")
    tea.keys("Enter")
    wait_until(
        lambda: any(
            m.text == "see you there" and m.reply_to
            for m in bob.storage.load_history(dm_conv(me))
        ),
        15,
    )
    # palette → a command's output in a panel
    tea.keys("C-k")
    tea.wait("chat, person, or command")
    tea.keys("/doctor")
    tea.keys("Enter")
    tea.wait("Version")
    tea.keys("Escape")
    tea.keys("C-c")
    wait_until(lambda: "Chats" not in tea.screen(), 10)
