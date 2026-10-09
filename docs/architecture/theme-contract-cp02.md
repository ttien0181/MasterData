# CP02: Theme token contract

The application exposes two visual modes through `html[data-theme="light"]` and `html[data-theme="dark"]`. `frontend/app/themes.css` is the only stylesheet that defines theme-dependent colors and shared visual scales. Component stylesheets describe layout and use semantic custom properties. The light primary is a contrast-adjusted cyan based on the supplied `#0891b2` design reference.

## Runtime behavior

- `frontend/app/layout.tsx` imports base styles, then `themes.css`, then component styles.
- A before-interactive script checks `masterone.theme`; if there is no valid saved choice, it uses `prefers-color-scheme` before the page renders.
- `ThemeToggle` changes the root `data-theme` attribute without navigation and stores the user's manual choice. While no choice is saved, system preference changes remain active.
- Theme state is independent of session, selected run, filters, and workflow drafts.
- Both workspace and public/auth pages use the same root tokens. Workflow colors, data-source colors, highlights, chart colors, backdrops, and overlays have theme-aware semantic variables.
- The application shell is composed from separate `ShellTopBar`, `SessionDock`, and `SessionStatusBar` components.
- `NEXT_PUBLIC_MASTERONE_SHELL=legacy` selects the flat, non-glass shell styling for side-by-side rollout comparison. The default value keeps the current glass shell; both modes retain the same navigation and workflow behavior.

## Manual verification

1. Clear `localStorage["masterone.theme"]`; set the operating system to light and dark in turn; reload and confirm the initial palette follows the system.
2. Toggle each direction; confirm there is no navigation or loss of the active run, current step, selected filters, or pending form values. Reload and confirm the manual choice persists.
3. With a manual choice saved, change the operating-system preference and confirm the page stays on the saved choice.
4. Check `/`, `/login`, `/register`, and `/app` in both modes. Include focus, hover, selected, disabled, loading, empty, error, toast, dialog, table highlight, source badge, and chart states.
5. Check 1440x900, 1280x720, 768x1024, and 390x844, then 200% zoom and keyboard-only navigation. Confirm no horizontal page overflow and that the menu/dialog backdrops remain legible.
6. Emulate reduced motion and verify the interface remains usable without transitions.

Visual screenshots and browser checks have not been run for this CP02 change.
