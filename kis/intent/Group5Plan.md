# Group 5 — Project files (approved 2026-08-30, Phase mode)

PRD item 5: browse/edit, project-scoped or global. Owner decisions 2026-08-30: **A** two roots only (projects root + a project's `primary_path`, realpath-contained); **B** full edit set (write/mkdir/rename/delete/upload, delete behind confirm); **A** CodeMirror 6 editor with markdown preview; **A** one page `/files` with root switcher + deep links + Project-detail link + artifacts group; **B** phone = bottom-sheet tree over a full-screen editor; **A** lazy per-directory listing with hidden/excluded toggle; **A** 1 MB text cap, inline image preview, PDF via view, others download-only, mtime conflict check on save. Reference (allowed in lineage): `../hermes-workspace/src/routes/files.tsx`, `api/files.ts`, `components/file-explorer/*`.

## 5-1 Backend file API
1. [ ] `backend/files.py` router: roots = `projects` (`WM_PROJECTS_ROOT` or `<hq home>/../projects`, i.e. the `writes.py:73` default) and `project:<slug>` (`primary_path`). `_resolve(root, path)` → realpath containment (pattern of `wm_store.py:227`), symlinks escaping the root refused with 403 "outside root".
2. [ ] `GET /api/files/roots` (projects root + projects with `primary_path`, exists flag); `GET /api/files/list?root=&path=&hidden=0` one level, dirs first, `{name, is_dir, size, mtime, excluded}` with `EXCLUDE_DIRS` hidden unless `hidden=1`; `GET /api/files/read` (text ≤ 1 MB → `{content, mtime, kind: text|image|pdf|binary, size}`, larger/binary → metadata only); `GET /api/files/raw` (view/download, `Content-Disposition` by `?download=1`).
3. [ ] `POST /api/files/write {root, path, content, mtime}` — 409 when disk mtime ≠ given; `mkdir`, `rename`, `delete` (dirs must be empty unless `recursive=1`; refuse when the target is any project's `primary_path`), `upload` (multipart, several files, ≤ 20 MB each). Artifacts: `GET /api/project/{slug}/artifacts` from the existing `result_paths` join in `readers.project_files`; `_walk_tree`/`file_preview` removed once nothing calls them.
4. [ ] `tests/backend/test_files.py` with a scratch root: containment (`..`, absolute, symlink out), list order + hidden toggle, read kinds and cap, write happy/409, mkdir/rename/delete/upload, primary_path guard.
Proof: pytest green; curl of list/read/write against a real project on the live server.

## 5-2 Files page (desktop)
1. [ ] `frontend/src/pages/Files.tsx` replaces the placeholder; root switcher (Projects root ▾ / project names), breadcrumb, tree pane (~280 px, lazy expand, skeleton on load, hidden toggle), row menu (rename/delete/download), New file / New folder / Upload (`<input multiple>`), deep link `?root=&path=`.
2. [ ] Editor pane: `@uiw/react-codemirror` + `@codemirror/lang-{markdown,javascript,python,json,html,css}` + oneDark/light via existing theme; Save (Ctrl/Cmd-S, `Btn busy`), dirty indicator, 409 → "Reload / Overwrite" modal; markdown Preview toggle using `components/chat/Markdown.tsx`; image inline; PDF "Open" via raw; binary/large → size note + Download.
3. [ ] Project detail: **Files** link → `/files?root=project:<slug>`; Artifacts group above the tree when a project root is selected (outside-root ones as download-only).
Proof: Playwright 1440: open a project, edit + save a file, reload shows the change; rename + delete round-trip; upload appears.

## 5-3 Phone
1. [ ] 390 px: editor fills the card; tree in a bottom sheet (pattern of the chat sessions sheet), breadcrumb button opens it; action row (Save/menu) above the tab bar with safe areas; 16 px fields; no horizontal overflow (`min-w-0`).
Proof: Playwright 390×844 `isMobile` screenshots of tree sheet, editor, save.

Out of scope: search-in-files, git status/diff, "open in chat", drag-drop upload, editing artifacts outside every root, arbitrary filesystem paths, multi-user locks.
Risks: CodeMirror bundle (~300 KB gz, lazy-loaded route); an agent writing the same file mid-edit (mitigated by the mtime 409); project 15 (`/opt/data`) exposes hq's own data dir through its project root — accepted, owner's own project; large uploads over Tailscale are slow, single request each.
