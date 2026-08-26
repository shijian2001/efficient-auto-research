# Arbor — Project Page

Dark, cinematic project page for **Arbor: Toward Generalist Autonomous Research via
Hypothesis-Tree Refinement**. Built with Vite + React, with animated UI sourced from
[react-bits](https://www.reactbits.dev/).

## Develop

```bash
npm install
npm run dev        # http://localhost:5173
```

## Build & preview

```bash
npm run build      # -> dist/  (static, self-contained)
npm run preview    # serves the production build locally
```

## Deploy (GitHub Pages)

`vite.config.js` sets `base: './'`, so the built `dist/` works at any path — a project
subpath (`user.github.io/Arbor/`) or a domain root. Two common options:

- Push the contents of `dist/` to a `gh-pages` branch (e.g. via `gh-pages` or an Action), or
- Copy `dist/` into the repo's `docs/` folder and point Pages at `docs/`.

## Structure

```
index.html              Vite entry
src/
  App.jsx               composes the sections
  main.jsx
  styles/theme.css      dark-cinematic design system (tokens + layout)
  sections/             Header, Hero, ProofStrip, Problem, Method, Results,
                        CaseStudy, Demo, WhyItMatters, Resources, Footer
  components/           Reveal, Counter, ErrorBoundary, icons, useReducedMotion
  bits/                 react-bits components (Threads, SpotlightCard, Magnet)
public/assets/          figures, paper PDF, and the live demo dashboards (iframes)
```

### Animation & robustness notes

- **Threads** (react-bits, WebGL/OGL) is the hero background; it's wrapped in an
  `ErrorBoundary` so a missing WebGL context never blanks the page.
- Section reveals (`Reveal`) and the metric counters (`Counter`) use progressive
  enhancement: content (and the **real** numbers) render by default and animation is
  layered on top, so nothing depends on an animation firing. All effects honor
  `prefers-reduced-motion`.

## Content

Copy and metrics are drawn from the paper (`paper/final_paper/main.tex`). The demo
dashboards under `public/assets/demo/` are real Arbor run exports embedded via iframes.
