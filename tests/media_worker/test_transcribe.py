import os, shutil, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from tools.media_worker.transcribe import wav_extract_argv, whisper_argv, transcribe


def test_wav_extract_argv_is_16k_mono_pcm():
    a = wav_extract_argv("take.mp4", "take.16k.wav")
    assert a[0] == "ffmpeg" and "-ar" in a and a[a.index("-ar") + 1] == "16000"
    assert a[a.index("-ac") + 1] == "1" and "pcm_s16le" in a and a[-1] == "take.16k.wav"


def test_whisper_argv_has_word_timestamp_flags():
    a = whisper_argv("m.bin", "take.16k.wav", "out")
    for flag in ("-m", "-ml", "-sow", "-oj", "-f", "-of"):
        assert flag in a, flag
    assert a[a.index("-ml") + 1] == "1"
    assert a[a.index("-of") + 1] == "out"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("whisper-cli")),
                    reason="ffmpeg + whisper-cli required (present in the worker image)")
def test_transcribe_emits_consumable_schema(tmp_path):
    model = os.environ.get("WHISPER_MODEL",
                           os.path.expanduser("~/stock_library/.whisper_models/ggml-small.en.bin"))
    if not os.path.exists(model):
        pytest.skip("whisper model not present locally")
    # 1s tone as a stand-in take; asserts schema shape, not transcript accuracy.
    take = tmp_path / "tone.wav"
    os.system(f'ffmpeg -v error -y -f lavfi -i "sine=frequency=440:duration=1" -ar 44100 "{take}"')
    out = transcribe(str(take), model, str(tmp_path))
    d = json.load(open(out))
    assert "transcription" in d
    assert isinstance(d["transcription"], list)
    for seg in d["transcription"]:
        assert "text" in seg and "offsets" in seg
        assert "from" in seg["offsets"] and "to" in seg["offsets"]
