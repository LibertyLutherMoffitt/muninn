#!/usr/bin/env python3
"""Generate Muninn's bundled offline animated stickers (GIFs).

No internet on a plane → no GIF search. Instead, ship a pack of small
animated stickers rendered from Noto Color Emoji (SIL OFL 1.1) with simple
procedural animations. Output: python/src/muninn/assets/stickers/*.gif
(the Android build packs the same directory as assets).

    nix develop --command python tools/make_stickers.py
"""

from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "python/src/muninn/assets/stickers"
SIZE = 160
FRAMES = 16
MS = 70


def font_path(family: str) -> str:
    return subprocess.check_output(["fc-match", "-f", "%{file}", family], text=True)


EMOJI_FONT = ImageFont.truetype(font_path("Noto Color Emoji"), 109)
TEXT_FONT = ImageFont.truetype(font_path("DejaVu Sans:bold"), 30)
_cache: dict[str, Image.Image] = {}


def glyph(e: str) -> Image.Image:
    if e not in _cache:
        im = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((0, 0), e, font=EMOJI_FONT, embedded_color=True)
        _cache[e] = im.crop(im.getbbox())
    return _cache[e]


def put(
    frame: Image.Image, e: str, cx: float, cy: float, scale=1.0, angle=0.0, alpha=1.0
):
    g = glyph(e)
    w = max(1, int(g.width * scale * 0.85))
    h = max(1, int(g.height * scale * 0.85))
    g = g.resize((w, h), Image.LANCZOS)
    if angle:
        g = g.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 1:
        a = g.getchannel("A").point(lambda v: int(v * alpha))
        g.putalpha(a)
    frame.alpha_composite(g, (int(cx - g.width / 2), int(cy - g.height / 2)))


def caption(frame: Image.Image, text: str, y: int = 128, color=(255, 255, 255)):
    d = ImageDraw.Draw(frame)
    size = 30
    font = TEXT_FONT
    while d.textlength(text, font=font) > SIZE - 12 and size > 12:
        size -= 2
        font = ImageFont.truetype(font_path("DejaVu Sans:bold"), size)
    w = d.textlength(text, font=font)
    x = (SIZE - w) / 2
    for dx in (-2, 0, 2):
        for dy in (-2, 0, 2):
            d.text((x + dx, y + dy), text, font=font, fill=(20, 20, 30, 255))
    d.text((x, y), text, font=font, fill=color + (255,))


def save(name: str, frames: list[Image.Image], ms: int = MS) -> None:
    out = []
    for f in frames:
        # GIF has 1-bit alpha: threshold, then palettize with index 255 = clear.
        alpha = f.getchannel("A").point(lambda v: 255 if v > 110 else 0)
        rgb = f.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
        rgb.paste(255, mask=Image.eval(alpha, lambda v: 255 - v))
        out.append(rgb)
    out[0].save(
        OUT / f"{name}.gif",
        save_all=True,
        append_images=out[1:],
        duration=ms,
        loop=0,
        transparency=255,
        disposal=2,
        optimize=True,
    )


def anim(name: str, draw, frames: int = FRAMES, ms: int = MS) -> None:
    fs = []
    for i in range(frames):
        f = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw(f, i / frames, i)
        fs.append(f)
    save(name, fs, ms)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(7)
    c = SIZE / 2
    T = 2 * math.pi

    anim(
        "takeoff",
        lambda f, t, i: (
            put(f, "☁️", 40 + 10 * math.sin(t * T), 120, 0.55),
            put(f, "✈️", -30 + t * 220, 130 - t * 120, 0.8, 0),
        ),
    )
    anim(
        "lol",
        lambda f, t, i: (
            put(
                f,
                "😂",
                c + 6 * math.sin(t * T * 4),
                64 + 4 * math.cos(t * T * 4),
                1.0,
                8 * math.sin(t * T * 4),
            ),
            caption(f, "LOL"),
        ),
    )
    anim(
        "heartbeat",
        lambda f, t, i: put(
            f, "❤️", c, c, 1.0 + 0.18 * max(0, math.sin(t * T * 2)) ** 4
        ),
    )
    anim(
        "thumbsup",
        lambda f, t, i: put(
            f, "👍", c, c + 18 - 30 * abs(math.sin(t * math.pi * 2)), 1.0
        ),
    )

    confetti = [
        (rnd.uniform(0, SIZE), rnd.uniform(-SIZE, 0), rnd.choice(["🎊", "✨", "🎉"]))
        for _ in range(9)
    ]
    anim(
        "party",
        lambda f, t, i: (
            [
                put(f, e, x, (y + t * SIZE * 2) % (SIZE + 30) - 15, 0.25)
                for x, y, e in confetti
            ],
            put(f, "🥳", c, c + 6 * math.sin(t * T * 2), 1.0, 10 * math.sin(t * T)),
        ),
    )
    anim(
        "facepalm", lambda f, t, i: put(f, "🤦", c, c, 1.0, -15 + 15 * math.sin(t * T))
    )
    anim(
        "sleepy",
        lambda f, t, i: (
            put(f, "😴", 70, 95, 0.9),
            [
                put(
                    f,
                    "💤",
                    115 + 10 * k,
                    70 - ((t + k / 3) % 1) * 60,
                    0.3 + 0.1 * k,
                    0,
                    1 - ((t + k / 3) % 1),
                )
                for k in range(3)
            ],
        ),
    )
    anim(
        "fire",
        lambda f, t, i: put(
            f, "🔥", c, c, 0.95 + 0.08 * math.sin(t * T * 3), 4 * math.sin(t * T * 5)
        ),
    )
    anim("wave", lambda f, t, i: put(f, "👋", c, c, 1.0, 25 * math.sin(t * T * 2)))
    anim(
        "clap",
        lambda f, t, i: put(f, "👏", c, c, 0.85 + 0.2 * abs(math.sin(t * T * 2))),
        frames=12,
        ms=60,
    )
    anim(
        "mindblown",
        lambda f, t, i: put(
            f, "🤯", c + rnd.uniform(-5, 5), c + rnd.uniform(-5, 5), 0.8 + 0.3 * t
        ),
    )
    anim(
        "coffee",
        lambda f, t, i: (
            put(f, "☕", c, 100, 0.9),
            [
                put(
                    f,
                    "💨",
                    70 + 20 * k + 6 * math.sin((t + k) * T),
                    45 - ((t + k / 2) % 1) * 30,
                    0.3,
                    90,
                    1 - ((t + k / 2) % 1),
                )
                for k in range(2)
            ],
        ),
    )
    anim(
        "turbulence",
        lambda f, t, i: (
            put(
                f,
                "😬",
                c + 10 * math.sin(t * T * 6),
                64 + 8 * math.cos(t * T * 5),
                0.95,
                10 * math.sin(t * T * 6),
            ),
            caption(f, "bumpy!"),
        ),
    )
    anim("pizza", lambda f, t, i: put(f, "🍕", c, c, 0.95, t * 360), frames=18)
    anim(
        "cool",
        lambda f, t, i: (
            put(f, "😐", c, c, 1.0),
            put(f, "🕶️", c, min(c - 12, -20 + t * 200), 0.75)
            if t < 0.85
            else (put(f, "😎", c, c, 1.0)),
        ),
    )
    anim(
        "cry",
        lambda f, t, i: (
            put(f, "😭", c, 70 + 4 * math.sin(t * T * 3), 0.95),
            [
                put(
                    f,
                    "💧",
                    45 + 70 * k,
                    95 + ((t * 2 + k / 2) % 1) * 55,
                    0.25,
                    0,
                    1 - ((t * 2 + k / 2) % 1),
                )
                for k in range(2)
            ],
        ),
    )
    anim("eyes", lambda f, t, i: put(f, "👀", c + 25 * math.sin(t * T), c, 1.0))
    anim(
        "hundred",
        lambda f, t, i: put(
            f, "💯", c, c, 0.6 + 0.5 * abs(math.sin(t * math.pi)), -10 + 20 * t
        ),
    )
    anim(
        "rocket",
        lambda f, t, i: (
            put(
                f,
                "🚀",
                c + 3 * math.sin(t * T * 8),
                SIZE + 20 - t * (SIZE + 60),
                0.8,
                0,
            ),
            put(f, "✨", c - 30, (SIZE - t * SIZE * 0.6) % SIZE, 0.3),
        ),
    )
    anim(
        "arewethereyet",
        lambda f, t, i: (
            put(f, "🕐", c, 62, 0.8, -t * 360),
            caption(f, "there yet?", 122),
        ),
        frames=18,
    )
    anim(
        "dice",
        lambda f, t, i: put(
            f,
            "🎲",
            c + 20 * math.sin(t * T),
            c - 25 * abs(math.sin(t * T * 2)),
            0.9,
            t * 720,
        ),
        frames=18,
    )
    anim("ok", lambda f, t, i: put(f, "👌", c, c, 1.0, 10 * math.sin(t * T * 2)))
    anim(
        "sos",
        lambda f, t, i: put(f, "🆘", c, c, 0.9, 0, 0.35 + 0.65 * abs(math.sin(t * T))),
    )
    anim(
        "snack",
        lambda f, t, i: (
            put(f, "🥜", c - 30, c + 10 * math.sin(t * T * 2), 0.6),
            put(f, "🥨", c + 30, c - 10 * math.sin(t * T * 2), 0.6),
        ),
    )
    anim(
        "goodnight",
        lambda f, t, i: (
            put(f, "🌙", c, c, 0.9, 10 * math.sin(t * T)),
            [
                put(
                    f,
                    "⭐",
                    30 + 100 * ((k * 0.37) % 1),
                    30 + 90 * ((k * 0.61) % 1),
                    0.2,
                    0,
                    0.3 + 0.7 * abs(math.sin((t + k / 5) * T)),
                )
                for k in range(5)
            ],
        ),
    )

    total = sum(p.stat().st_size for p in OUT.glob("*.gif"))
    print(f"{len(list(OUT.glob('*.gif')))} stickers, {total / 1024:.0f} KB in {OUT}")


if __name__ == "__main__":
    main()
