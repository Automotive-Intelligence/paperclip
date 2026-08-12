"""Don's gate must FAIL CLOSED. The Iris gate it replaces ends in an
unconditional `return 0`; these tests exist so this one cannot drift there."""
from unittest import mock

from services import don_ad_gate as G


def _measured(**over):
    m = {"width": 1080, "height": 1920, "aspect": 1080 / 1920,
         "fps": 24.0, "duration": 15.0, "has_audio": True}
    m.update(over)
    return m


def test_mechanical_accepts_the_three_meta_placements():
    for w, h in ((1080, 1920), (1080, 1080), (1080, 1350)):
        assert G.mechanical_findings(_measured(width=w, height=h, aspect=w / h)) == []


def test_mechanical_rejects_landscape():
    out = G.mechanical_findings(_measured(width=1920, height=1080, aspect=1920 / 1080))
    assert any("placement" in f for f in out)


def test_mechanical_rejects_missing_audio_and_bad_length():
    assert any("audio" in f for f in G.mechanical_findings(_measured(has_audio=False)))
    assert any("ceiling" in f for f in G.mechanical_findings(_measured(duration=75)))
    assert any("too short" in f for f in G.mechanical_findings(_measured(duration=2)))


def test_end_card_is_always_sampled():
    """Trimming the sample once cut the end card, and Don reported 'no CTA' on a
    piece whose end card carries the phone number."""
    calls = []
    with mock.patch.object(G.subprocess, "run",
                           side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
         mock.patch.object(G.pathlib.Path, "exists", return_value=False):
        G.grab_frames(G.pathlib.Path("x.mp4"), 15.0, n=3)
    stamps = [float(c[c.index("-ss") + 1]) for c in calls if "-ss" in c]
    assert stamps[0] < 1.0, "opening frame missing"
    assert stamps[-1] > 14.0, "END CARD missing from the sample"


def test_missing_file_exits_nonzero():
    assert G.main(["/tmp/definitely-not-here.mp4"]) == 1


def test_api_failure_holds():
    with mock.patch.object(G, "review", side_effect=RuntimeError("boom")), \
         mock.patch.object(G.pathlib.Path, "exists", return_value=True):
        assert G.main(["x.mp4"]) == 1


def test_hold_verdict_exits_nonzero_and_pass_exits_zero():
    with mock.patch.object(G.pathlib.Path, "exists", return_value=True):
        with mock.patch.object(G, "review", return_value={"verdict": "HOLD"}):
            assert G.main(["x.mp4"]) == 1
        with mock.patch.object(G, "review", return_value={"verdict": "PASS"}):
            assert G.main(["x.mp4"]) == 0


def test_unparseable_verdict_holds():
    with mock.patch.object(G.pathlib.Path, "exists", return_value=True), \
         mock.patch.object(G, "review", return_value={}):
        assert G.main(["x.mp4"]) == 1


def test_mechanical_failure_overrides_a_model_pass():
    """A model that says PASS cannot override a measured defect."""
    with mock.patch.object(G, "probe", return_value=_measured(width=1920, height=1080,
                                                              aspect=1920 / 1080)), \
         mock.patch.object(G, "grab_frames", return_value=[b"x"]), \
         mock.patch("services.studio_social_llm.llm_json",
                    return_value={"verdict": "PASS"}):
        res = G.review(G.pathlib.Path("x.mp4"), "f", "QUALITY_CALL")
    assert res["verdict"] == "HOLD"
