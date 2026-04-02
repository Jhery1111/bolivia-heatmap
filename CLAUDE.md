# CLAUDE.md — bolivia-heatmap

This file provides guidance for AI assistants (Claude Code and similar) working in this repository. Keep it updated as the project evolves.

---

## Project Overview

**bolivia-heatmap** is a geographic data visualization project focused on rendering heatmap overlays for Bolivia. The repository is in its initial setup phase; this document will grow as the codebase is established.

---

## Repository State (as of 2026-04-02)

- Fresh git repository — no source files committed yet.
- Remote: `Jhery1111/bolivia-heatmap` (proxied via local git proxy).
- Single active branch: `claude/add-claude-documentation-1oxCg`.

Update this section once the project scaffolding is in place.

---

## Branch & Git Conventions

| Purpose | Branch pattern |
|---|---|
| Feature work | `feature/<short-description>` |
| Bug fixes | `fix/<short-description>` |
| Claude-initiated tasks | `claude/<task-slug>` |
| Documentation | `docs/<short-description>` |

### Commit messages

- Use the imperative mood: `Add heatmap layer`, not `Added heatmap layer`.
- Keep the subject line under 72 characters.
- Reference issue numbers when applicable: `Fix color scale (#42)`.

### Push workflow

Always push with tracking set:

```bash
git push -u origin <branch-name>
```

Never push directly to `main`/`master`. Open a pull request instead.

---

## Development Workflow

> Fill in this section once the tech stack and tooling are decided.

Expected workflow once the project is scaffolded:

1. **Install dependencies** — `npm install` (or the relevant package manager).
2. **Run dev server** — `npm run dev` (or equivalent).
3. **Run tests** — `npm test`.
4. **Build for production** — `npm run build`.
5. **Lint** — `npm run lint` (fix issues before committing).

---

## Project Structure (anticipated)

```
bolivia-heatmap/
├── src/
│   ├── components/     # UI components
│   ├── data/           # Static datasets or data-fetching utilities
│   ├── map/            # Map/heatmap rendering logic
│   └── utils/          # Shared helpers
├── public/             # Static assets
├── tests/              # Test files mirroring src/ structure
├── CLAUDE.md           # This file
└── README.md           # User-facing documentation
```

Update this tree to reflect the actual structure once files are added.

---

## Key Conventions

### Data

- Geographic data for Bolivia should use **WGS 84** (EPSG:4326) coordinates.
- Prefer GeoJSON for vector data; use well-known open datasets (e.g., GADM, OpenStreetMap) and document their source and license.
- Large data files (>1 MB) should **not** be committed to git — use `.gitignore` and document where to obtain them.

### Map rendering

- Document which mapping library is used (e.g., Leaflet, Mapbox GL JS, deck.gl) and its version.
- Heatmap color scales should be accessible — avoid red/green-only palettes; prefer sequential or diverging scales (e.g., viridis, plasma).

### Code style

- Follow the linter/formatter configured in the project (e.g., ESLint + Prettier for JS/TS, Black for Python).
- Do not commit code that fails linting or tests.
- Keep components/functions focused — one responsibility per unit.

### Environment variables

- Store secrets and API keys in `.env` (never commit this file).
- Document all required variables in `.env.example` with placeholder values.

---

## AI Assistant Guidelines

- **Read before editing**: always read a file before modifying it.
- **Minimal changes**: only change what the task requires; do not refactor unrelated code.
- **No speculative features**: implement exactly what is requested.
- **Confirm destructive actions**: deleting files, force-pushing, or dropping data should always be confirmed with the user first.
- **Security**: do not introduce command injection, XSS, SQL injection, or other OWASP vulnerabilities. Validate only at system boundaries (user input, external APIs).
- **Branch hygiene**: develop on the designated feature branch, commit with clear messages, push when done, and open a PR — do not merge your own PRs.

---

## Updating This File

Update CLAUDE.md whenever:
- The tech stack or major dependency is decided or changed.
- A new development workflow step is added.
- Key architectural decisions are made.
- The directory structure changes significantly.
