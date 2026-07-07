"""Generate cinematic 16:9 page heroes for AIPG inner pages on fal.ai Nano Banana Pro.

Same golden-hour Texas documentary world as the homepage hero. Composition keeps
the LEFT side darker/quieter so the charcoal left-gradient + white headline read
clean. Run with: doppler run -p paperclip -c prd -- python3 scripts/aipg_gen_page_heroes.py
"""
import sys, pathlib
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.fal_image import generate_nano_banana_image

OUT = pathlib.Path.home() / "ai-phone-guy-site" / "public" / "redesign" / "heroes"

DIR = (
    "Cinematic editorial documentary photograph, warm golden-hour Texas light, "
    "shallow depth of field, filmic teal-and-amber grade, candid and authentic "
    "(not stock-posed), real skin and material texture, 35mm lens look. No text, "
    "no logos, no watermarks. Composition keeps the LEFT third darker and simpler "
    "so a charcoal gradient and white headline can sit over it; the subject and "
    "visual interest sit on the RIGHT side of the frame."
)

JOBS = [
    ("how-it-works.png",
     "A confident tradesperson in a work shirt standing at the open back of his "
     "service van, calmly taking a phone call, slight reassured smile, in control."),
    ("playbook.png",
     "A weathered tradesperson's hands at the back of a truck tailgate reviewing a "
     "worn notebook and a smartphone showing a call log, a coffee cup beside it, "
     "late-afternoon light. Close, tactile, planning a strategy."),
    ("contact.png",
     "A friendly tradesperson standing in a suburban driveway at the end of a "
     "service call, relaxed and approachable, looking toward camera with an easy "
     "smile, his service van softly out of focus behind him."),
    ("blog.png",
     "An overhead flat-lay on a worn wooden truck tailgate: a notebook and pen, a "
     "smartphone, a coffee cup, a tape measure and keys, arranged loosely, warm "
     "directional light. Editorial 'field notes' still life."),
]

for name, scene in JOBS:
    res = generate_nano_banana_image(f"{scene} {DIR}", aspect_ratio="16:9", pro=True)
    if isinstance(res, str):
        print(f"FAIL {name}: {res}")
        continue
    dest = OUT / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = requests.get(res["urls"][0], timeout=180)
    img.raise_for_status()
    dest.write_bytes(img.content)
    print(f"OK   heroes/{name}  <- {res['model']} {res['aspect_ratio']}")
print("DONE")
