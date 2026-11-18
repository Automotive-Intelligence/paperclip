"""services/slipstream_ai_detect.py -- the Slipstream AI-DETECTION SOFT SIGNAL.

Scores a long-form post with the open-source Desklib detector
(desklib/ai-text-detector-v1.01: DeBERTa-v3-large fine-tuned on the RAID
benchmark, MIT license, runs on CPU, no API key, no per-word cost).

THIS IS NOT A GATE. No detector is reliable enough to block on. The flag means
"reads machine-made, needs a human edit pass". It never means "AI, reject".

  * Publishing NEVER waits on this check and is never held by it.
  * If the check is down (torch not installed, model not downloadable, timeout,
    crash, out of memory) the result says so and publishing proceeds.
  * Only long-form is scored (min_words, default 250). Social posts and ad copy
    are too short for any detector to judge and are never sent here; the
    anti-slop guardrail for those stays the editorial checklist.

How it scores: the model reads about 768 tokens at a time, so the body is cut
into chunks of `chunk_tokens` (default 600) with `overlap_tokens` (default 50)
of overlap. Each chunk gets a probability from 0 (reads human) to 1 (reads
machine-made). The post score is the plain average of the chunk scores. The
post is FLAGGED when that average is at or above `threshold`, and every chunk
at or above the threshold is listed for the editor.

The threshold comes from calibration (scripts/slipstream_ai_detect_calibrate.py)
and lives in config/slipstream_brands.yaml under `ai_detect:`. With no
threshold set the check reports status "uncalibrated" and flags nothing.

Process model: check_post() runs the model in a SHORT-LIVED CHILD PROCESS
(`python -m services.slipstream_ai_detect`). The model needs roughly 2 to 3 GB
of RAM while loaded; a child process hands that memory back the moment it
exits, and if it is killed for memory or hangs, the web service that runs every
other engine is untouched. torch and transformers are imported only inside the
child, so the main app starts and runs without them installed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MODEL_ID = "desklib/ai-text-detector-v1.01"
MODEL_MAX_TOKENS = 768          # the detector's context window
FLAG_MEANING = "reads machine-made, needs a human edit pass"

DEFAULTS: Dict[str, Any] = {
    "enabled": False,           # OFF until the calibration report is accepted
    "threshold": None,          # set from the calibration run
    "min_words": 250,
    "chunk_tokens": 600,
    "overlap_tokens": 50,
    "timeout_s": 240,
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORD_RE = re.compile(r"\S+")


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

def resolve_ai_detect_cfg(full_cfg: Dict[str, Any], brand_key: str) -> Dict[str, Any]:
    """Merge the top-level `ai_detect:` block of slipstream_brands.yaml into one
    per-brand dict: built-in defaults, then the block's own keys, then
    `ai_detect.brands.<key>` overrides."""
    block = dict((full_cfg or {}).get("ai_detect") or {})
    brands = block.pop("brands", None) or {}
    block.pop("defaults", None)
    out = {**DEFAULTS, **{k: v for k, v in block.items() if k in DEFAULTS}}
    out.update({k: v for k, v in (brands.get(brand_key) or {}).items() if k in DEFAULTS})
    return out


def _cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {**DEFAULTS, **((cfg or {}).get("ai_detect") or {})}


# ----------------------------------------------------------------------------
# Text + chunking (pure, no model needed)
# ----------------------------------------------------------------------------

def post_text(post: Dict[str, Any]) -> str:
    """The visible ARTICLE BODY of a generated post as plain prose, from either
    shape (body_mdx string or body block array). Title, alt text, FAQ, link
    labels and the social drafts are left out on purpose: only long-form body
    copy is scored."""
    from services.slipstream_factcheck import extract_post
    units, _ = extract_post(post)
    return " ".join(u.text for u in units if u.surface == "body" and u.text.strip())


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def chunk_spans(n_tokens: int, chunk_tokens: int = 600, overlap_tokens: int = 50) -> List[Tuple[int, int]]:
    """Token windows [start, end) covering n_tokens.

    Windows are `chunk_tokens` long and step forward by chunk_tokens minus
    overlap_tokens. The LAST window is pulled back so it ends exactly at the
    end of the text, which keeps every window full length: a detector judges a
    40-token leftover badly, and a plain average would let that scrap count as
    much as a full chunk."""
    chunk_tokens = max(50, min(int(chunk_tokens), MODEL_MAX_TOKENS - 2))
    overlap_tokens = max(0, min(int(overlap_tokens), chunk_tokens // 2))
    if n_tokens <= 0:
        return []
    if n_tokens <= chunk_tokens:
        return [(0, n_tokens)]
    step = chunk_tokens - overlap_tokens
    spans: List[Tuple[int, int]] = []
    start = 0
    while start + chunk_tokens < n_tokens:
        spans.append((start, start + chunk_tokens))
        start += step
    spans.append((n_tokens - chunk_tokens, n_tokens))
    return spans


def _excerpt(s: str, n: int = 240) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def verdict(chunk_scores: Sequence[float], chunk_texts: Sequence[str],
            threshold: Optional[float]) -> Dict[str, Any]:
    """Python decides: turn raw chunk scores into the result. Average the chunk
    scores; flag at or above the threshold; list the chunks that are at or above
    it. With no threshold nothing can be flagged."""
    scores = [float(s) for s in chunk_scores]
    # Rounded BEFORE the comparison so the number shown is the number judged.
    avg = round(sum(scores) / len(scores), 4) if scores else 0.0
    chunks = []
    for i, (s, t) in enumerate(zip(scores, chunk_texts), 1):
        chunks.append({"i": i, "score": round(s, 4),
                       "flagged": threshold is not None and s >= threshold,
                       "excerpt": _excerpt(t)})
    return {"score": avg,
            "flagged": threshold is not None and bool(scores) and avg >= threshold,
            "chunks": chunks,
            "flagged_chunks": [c["i"] for c in chunks if c["flagged"]]}


# ----------------------------------------------------------------------------
# The model (child process only: imports torch + transformers)
# ----------------------------------------------------------------------------

_MODEL: Dict[str, Any] = {}


def model_source() -> str:
    """A local directory holding the model files if SLIPSTREAM_AI_DETECT_MODEL_DIR
    is set, else the Hugging Face model id (downloaded once into the HF cache)."""
    return os.getenv("SLIPSTREAM_AI_DETECT_MODEL_DIR", "").strip() or MODEL_ID


def _load():
    if _MODEL:
        return _MODEL["tok"], _MODEL["model"], _MODEL["torch"]
    import torch
    import torch.nn as nn
    from transformers import AutoConfig, AutoModel, AutoTokenizer, PreTrainedModel

    class DesklibAIDetectionModel(PreTrainedModel):
        """The architecture from the Desklib model card: DeBERTa-v3-large, mean
        pooling over the attention mask, one linear unit, sigmoid."""
        config_class = AutoConfig

        def __init__(self, config):
            super().__init__(config)
            self.model = AutoModel.from_config(config)
            self.classifier = nn.Linear(config.hidden_size, 1)
            self.init_weights()

        def forward(self, input_ids, attention_mask=None):
            last = self.model(input_ids, attention_mask=attention_mask)[0]
            mask = attention_mask.unsqueeze(-1).expand(last.size()).float()
            pooled = torch.sum(last * mask, dim=1) / torch.clamp(mask.sum(dim=1), min=1e-9)
            return self.classifier(pooled)

    src = model_source()
    tok = AutoTokenizer.from_pretrained(src)
    model = DesklibAIDetectionModel.from_pretrained(src)
    model.eval()
    _MODEL.update(tok=tok, model=model, torch=torch)
    return tok, model, torch


def score_chunks(text: str, chunk_tokens: int = 600, overlap_tokens: int = 50
                 ) -> Tuple[List[float], List[str]]:
    """Run the detector over `text`. Returns (chunk_scores, chunk_texts).
    Imports torch: call it in the child process, or in an offline script."""
    tok, model, torch = _load()
    enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = enc["input_ids"], enc["offset_mapping"]
    scores: List[float] = []
    texts: List[str] = []
    for a, b in chunk_spans(len(ids), chunk_tokens, overlap_tokens):
        piece = [tok.cls_token_id] + ids[a:b] + [tok.sep_token_id]
        input_ids = torch.tensor([piece])
        with torch.no_grad():
            logit = model(input_ids, attention_mask=torch.ones_like(input_ids))
        scores.append(float(torch.sigmoid(logit).item()))
        texts.append(text[offsets[a][0]:offsets[b - 1][1]])
    return scores, texts


def _child_main() -> int:
    """`python -m services.slipstream_ai_detect`: JSON in on stdin
    ({"text", "chunk_tokens", "overlap_tokens"}), one JSON line out on stdout."""
    try:
        req = json.loads(sys.stdin.read() or "{}")
        scores, texts = score_chunks(str(req.get("text") or ""),
                                     int(req.get("chunk_tokens") or 600),
                                     int(req.get("overlap_tokens") or 50))
        out = {"ok": True, "scores": scores, "texts": texts, "model": MODEL_ID}
    except BaseException as e:  # noqa: BLE001 - report every failure to the parent
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    return 0 if out.get("ok") else 1


def run_in_child(text: str, chunk_tokens: int, overlap_tokens: int, timeout_s: float
                 ) -> Dict[str, Any]:
    """Default runner: score in a short-lived child process. Raises on any
    failure; check_post turns that into status 'unavailable'."""
    proc = subprocess.run(
        [sys.executable, "-m", "services.slipstream_ai_detect"],
        input=json.dumps({"text": text, "chunk_tokens": chunk_tokens,
                          "overlap_tokens": overlap_tokens}),
        capture_output=True, text=True, timeout=timeout_s, cwd=_REPO_ROOT)
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("{")]
    if not lines:
        tail = " ".join((proc.stderr or "").split())[-300:]
        raise RuntimeError(f"detector process exited {proc.returncode} with no result: {tail}")
    out = json.loads(lines[-1])
    if not out.get("ok"):
        raise RuntimeError(out.get("error") or "detector process failed")
    return out


# ----------------------------------------------------------------------------
# The engine step
# ----------------------------------------------------------------------------

def check_text(text: str, cfg: Optional[Dict[str, Any]] = None, *,
               runner: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Score one piece of long-form text. NEVER raises and never blocks anything.

    status:
      off            ai_detect.enabled is not true for this brand
      skipped_short  under min_words: too short for any detector to judge
      uncalibrated   scored, but no threshold is configured, so nothing can flag
      unavailable    the check could not run (see `error`); publishing proceeds
      ok             scored against the threshold; see `flagged`
    """
    c = _cfg(cfg)
    out: Dict[str, Any] = {"ran": False, "status": "off", "flagged": False, "score": None,
                           "threshold": c.get("threshold"), "words": 0, "chunks": [],
                           "flagged_chunks": [], "model": MODEL_ID, "error": None,
                           "meaning": FLAG_MEANING}
    try:
        if c.get("enabled") is not True:
            return out
        words = word_count(text)
        out["words"] = words
        if words < int(c.get("min_words") or 0):
            out["status"] = "skipped_short"
            return out
        raw = (runner or run_in_child)(text, int(c["chunk_tokens"]), int(c["overlap_tokens"]),
                                      float(c.get("timeout_s") or 240))
        thr = c.get("threshold")
        thr = float(thr) if thr is not None else None
        out.update(verdict(raw["scores"], raw["texts"], thr))
        if not out["chunks"]:
            raise RuntimeError("detector returned no chunk scores")
        out["ran"] = True
        out["status"] = "ok" if thr is not None else "uncalibrated"
        return out
    except BaseException as e:  # noqa: BLE001 - a soft signal must never sink a publish
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        out.update(ran=False, status="unavailable", flagged=False,
                   error=f"{type(e).__name__}: {e}"[:300])
        return out


def check_post(post: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None, *,
               runner: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The engine step: score the body of a generated post. NEVER raises."""
    if _cfg(cfg).get("enabled") is not True:
        return check_text("", cfg)                       # status "off", no work done
    try:
        text = post_text(post)
    except Exception as e:  # noqa: BLE001
        r = check_text("", {"ai_detect": {"enabled": False}})
        r.update(status="unavailable", error=f"could not read the post body: {type(e).__name__}: {e}"[:300])
        return r
    return check_text(text, cfg, runner=runner)


def log_result(brand_key: str, slug: str, r: Dict[str, Any]) -> None:
    """One log line per outcome, at the level it deserves. 'unavailable' is loud
    on purpose: a soft signal that silently stops running looks like a clean run."""
    s = r.get("status")
    if s == "off":
        return
    if s == "unavailable":
        logger.error("[slipstream] %s (%s) AI-detection check DID NOT RUN: %s. Publishing "
                     "proceeds; this post was not scored.", brand_key, slug, r.get("error"))
    elif r.get("flagged"):
        logger.warning("[slipstream] %s (%s) AI-detection FLAG: score %.2f at threshold %.2f, "
                       "chunks %s. Meaning: %s. NOT a hold; publishing proceeds.",
                       brand_key, slug, r.get("score") or 0, r.get("threshold") or 0,
                       r.get("flagged_chunks"), FLAG_MEANING)
    else:
        logger.info("[slipstream] %s (%s) AI-detection: status=%s score=%s words=%s",
                    brand_key, slug, s, r.get("score"), r.get("words"))


def pr_section(r: Optional[Dict[str, Any]]) -> str:
    """Markdown block for the PR body, so the editor sees the flagged chunks."""
    if not r or r.get("status") in (None, "off"):
        return ""
    head = "\n\n### AI-detection check (soft signal, never a hold)\n\n"
    s = r.get("status")
    if s == "skipped_short":
        return head + (f"Not scored: {r.get('words', 0)} words is too short for a detector "
                       "to judge.\n")
    if s == "unavailable":
        return head + (f"**The check did not run** (`{r.get('error')}`). The post was not "
                       "scored. Publishing was not held.\n")
    lines = [f"- model: `{r.get('model')}` (open source, runs locally, no API)",
             f"- score: **{r.get('score'):.2f}** averaged over {len(r.get('chunks') or [])} "
             f"chunk(s), {r.get('words', 0)} words"]
    if s == "uncalibrated":
        lines.append("- no threshold is configured yet, so nothing is flagged")
    elif r.get("flagged"):
        lines.append(f"- **FLAGGED** at threshold {r.get('threshold'):.2f}: {FLAG_MEANING}. "
                     "This is not \"AI, reject\" and it does not hold the post.")
    else:
        lines.append(f"- not flagged (threshold {r.get('threshold'):.2f})")
    body = head + "\n".join(lines) + "\n"
    hot = [c for c in (r.get("chunks") or []) if c.get("flagged")]
    if hot:
        body += "\nChunks to edit first:\n\n| # | Score | Starts with |\n|---|---|---|\n"
        for c in hot:
            body += f"| {c['i']} | {c['score']:.2f} | {c['excerpt'].replace('|', chr(92) + '|')} |\n"
    return body


if __name__ == "__main__":
    sys.exit(_child_main())
