# home_stage — assets baked into the worker image at HOME-relative paths

The container runs as root (HOME=/root), and the ported pipeline scripts read
assets from HOME-relative paths with no env override. This tree is COPY'd to
/root/ so the image renders with no mounts.

- `avo-telemetry/assets/fonts/*.ttf` — the brands' display faces (verified, final).
- BRAND LOGOS = PLACEHOLDERS pending Iris. The logos here were copied from the site
  public folders and are NOT confirmed brand-canonical. Iris owns the real marks
  (see studio_state.md flag "CANONICAL BRAND LOGOS for the video cloud-worker",
  2026-07-20). Known issue: the BAE crown "never matched buildagentempire.com"
  (build_short.py). On Iris handoff: replace the files here, rebuild, re-verify the
  self-sufficiency render, then finalize.
