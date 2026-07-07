"""One-off: regenerate AIPG trade-hover + closing images on fal.ai Nano Banana Pro.

Replaces the earlier Canva placeholders with cinematic, golden-hour documentary
photography matching the live hero (tradesperson at the work van). 6 trade hovers
at 4:3, closing CTA at 16:9. Run with: doppler run -p paperclip -c prd -- python3 ...
"""
import sys, pathlib
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.fal_image import generate_nano_banana_image

OUT = pathlib.Path.home() / "ai-phone-guy-site" / "public" / "redesign"

# Shared visual direction = the live hero's look so the whole page is one world.
DIR = (
    "Cinematic editorial documentary photograph, real working tradesperson in a "
    "Texas suburban setting, warm golden-hour light, shallow depth of field, "
    "filmic teal-and-amber color grade, candid and authentic (not stock-posed), "
    "weathered real skin and material texture, 35mm lens look. No text, no logos, "
    "no watermarks, no on-screen graphics. Composition leaves the lower third "
    "darker and quieter so a dark gradient and white text can sit over it."
)

JOBS = [
    ("trades/hvac.png", "4:3",
     "An HVAC technician kneeling beside an outdoor AC condenser unit next to a "
     "suburban home on a blazing hot summer afternoon, wiping sweat, glancing at "
     "the phone in his hand."),
    ("trades/plumbing.png", "4:3",
     "A plumber under a kitchen sink with a pipe wrench, both hands full, work van "
     "visible through the doorway behind him, a phone lighting up on the counter."),
    ("trades/roofing.png", "4:3",
     "A roofer standing on a residential shingle roof the morning after a hailstorm, "
     "inspecting storm damage, early soft light, a neighborhood of rooftops behind."),
    ("trades/electrical.png", "4:3",
     "An electrician at an open residential electrical panel wearing a headlamp, "
     "focused on the wiring, tools on his belt, dim garage interior."),
    ("trades/garage-door.png", "4:3",
     "A garage-door technician repairing a residential garage door track in a "
     "driveway in the early morning, spring and opener parts laid out, suburban home."),
    ("trades/other-business.png", "4:3",
     "A friendly small-business owner at a front counter of a clean modern med spa "
     "or boutique clinic, taking a call on a headset, warm and welcoming reception area."),
    ("closing.png", "16:9",
     "Wide cinematic shot at dusk: a tradesperson leaning against the back of his "
     "work van in a driveway at the end of the day, relaxed, smiling slightly at his "
     "phone after booking a job, warm tailgate and porch light glow, long Texas "
     "twilight sky. The center of the frame is calm and uncluttered."),
]

for rel, ar, scene in JOBS:
    prompt = f"{scene} {DIR}"
    res = generate_nano_banana_image(prompt, aspect_ratio=ar, pro=True)
    if isinstance(res, str):
        print(f"FAIL {rel}: {res}")
        continue
    url = res["urls"][0]
    dest = OUT / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = requests.get(url, timeout=180)
    img.raise_for_status()
    dest.write_bytes(img.content)
    print(f"OK   {rel}  <- {res['model']} {res['aspect_ratio']}")
print("DONE")
