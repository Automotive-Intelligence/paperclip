# tools/media_worker/smoke.py
"""In-image self-check: the things most likely to break in a Linux port.

Runs each binary (not just a PATH check) so a binary that cannot load its
shared libraries fails HERE instead of silently at render time. A bare
`shutil.which` check once reported OK while whisper-cli could not load
libwhisper.so at all."""
import shutil, subprocess, sys
from PIL import ImageFont


def _runs(binary, args):
    """False if the binary is missing or cannot exec/load (exit 127 or a
    'error while loading shared libraries' loader failure)."""
    if not shutil.which(binary):
        print(f"MISSING binary: {binary}")
        return False
    try:
        r = subprocess.run([binary, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"{binary} failed to exec: {e}")
        return False
    if r.returncode == 127 or "error while loading shared libraries" in (r.stderr or ""):
        print(f"{binary} cannot load its libraries: {r.stderr.strip()[:140]}")
        return False
    return True


def main() -> int:
    ok = True
    ok &= _runs("ffmpeg", ["-version"])
    ok &= _runs("ffprobe", ["-version"])
    ok &= _runs("whisper-cli", ["--help"])
    try:
        f = ImageFont.truetype("/opt/media/fonts/InterTight.ttf", 78)
        f.set_variation_by_name("Black")  # variable-font support (FreeType)
    except Exception as e:
        print(f"variable font FAILED: {e}"); ok = False
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
