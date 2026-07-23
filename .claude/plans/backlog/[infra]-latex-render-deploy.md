# [infra] latex-render-deploy (backlog)

> **Backlog stub — activate when the app moves off the dev Mac.** Captures the TeX toolchain the
> server-side LaTeX render (`[fullstack]-latex-render`) needs in the Docker image. Not a runnable guide
> yet: no verified base image, no measured image size, no resource-limit numbers. Activate = pick the
> base, pin the package set, add the red/verification steps ([[backlog-guides-need-activation]]).

## Why

`jac.latex.render_application_pdf` shells out to **`lualatex`** to compile the gold-standard `moderncv`
document (letter + CV + `\includepdf` attachments). Dev relies on the `basictex` install on Lukas's Mac
(`/Library/TeX/texbin/lualatex`). The container has no TeX — the render endpoint 502s until this lands.

## The package set (proven on dev basictex 2026)

`basictex` ships `moderncv`, `fontawesome6`, `pdfpages`, `setspace`, `tweaklist`, `ngerman`/`german`
babel, `geometry`, `microtype`, `lmodern`, `xcolor`, `hyperref`, `fancyhdr`, `epstopdf`. The gold
standard additionally needed, on top of a fresh basictex:

```bash
sudo tlmgr update --self
sudo tlmgr install moderncv geometry setspace pdfpages fontawesome6 xkeyval etoolbox cm-super
```

## Docker options (decide at activation)

1. **`texlive-full` (apt) base** — simplest, biggest (~4–5 GB). `lualatex` + everything present; no
   `tlmgr`. `LATEX_BIN=/usr/bin/lualatex`.
2. **Minimal TeX Live + `tlmgr install`** — a `texlive-base` / `texlive-luatex` layer + the package set
   above via `tlmgr`. Much smaller; pin a TeX Live year and the package list. Preferred if image size
   matters.
3. **A dedicated render sidecar** — a small TeX container the web app calls; isolates the (executable,
   resource-hungry) compile from the Django process. Best for the public-showcase endgame (sandbox +
   independent scaling), most infra work.

## Must-not-forget at activation

- **`LATEX_BIN`** env → `/usr/bin/lualatex` (or wherever the base puts it). Settings already read it.
- **Writable `TEXMFVAR`** — luaotfload builds a font cache on first run; a read-only home dir wedges the
  first compile. Pre-warm the cache in the image build (`luaotfload-tool --update`) or point `TEXMFVAR`
  at a writable volume.
- **Temp dir** — `jac.latex.compile_pdf` uses `tempfile.TemporaryDirectory`; mount a tmpfs and set
  `TMPDIR` so compiles don't hit the persistent disk. Cleanup is automatic (context manager).
- **Resource limits** — `-no-shell-escape` blocks `\write18` but not a pathological template looping;
  the wall-clock `LATEX_TIMEOUT_S` bounds one run. Add a CPU/mem cgroup cap on the render worker (or the
  sidecar) so a bad template can't starve the box.
- **Fonts** — `fontawesome6`'s OTFs ship with the package (no system font needed under lualatex). If the
  template later switches to a branded system font via `fontspec`, that font must be installed in the
  image too.
- **Signature asset** — `LATEX_ASSETS_DIR` (`signature.pdf`) must be present in the image/volume, kept
  out of git.

## Related

- Guide: `plans/to-do/[fullstack]-latex-render.md`.
- Endgame: exposing LaTeX render on the public showcase needs a hard sandbox (a template is arbitrary
  code) — [[public-site-posture]]. Option 3 (sidecar) is the natural host for that.
