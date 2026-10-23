# Banking interface design

The authored Northwind Bank interfaces use the Langfuse website reference captured
on 20 September 2026: neutral outer canvas (`--surface-1`), paper content
(`--surface-bg`), separate structural and corner ink, square guide frames with
8 px curved corner masks, and 2 px controls. The fictional bank name, customer
selection, bilingual assistant and evaluation workflow remain the demo identity.

`portal/static/langfuse-tokens.css` in the banking subtree is the unchanged
stylesheet supplied by the `langfuse-brand-assets` skill. `app.css` applies its
`lf-*` recipes to the customer, voice, presenter, history, judges, reviews,
application experiments, judge comparison, provider comparison, releases and
governance interfaces. Native Langfuse, n8n and Jaeger interfaces retain their own
styles. The chosen theme is light. The four SVG corner masks retain the exact reference paths
and are served as local files so the preview retains its existing same-origin
Content Security Policy; no security policy is broadened.

## Font files and licences

All fonts load from the Companion's own static route, including when served below
a Depot live prefix. There are no external font requests. The repository includes
Inter variable, Geist Mono variable and Space Grotesk variable under their SIL
Open Font Licences. Each licence is beside its font in `portal/static/fonts`;
`sources.json` records official Google Fonts URLs, Git blob identities and file
SHA-256 hashes. The runtime image copies the complete banking subtree, including
these assets and licences.

F37 Analog is not bundled because a locally licensed copy was unavailable.
**Space Grotesk is the deliberate display-face substitution.** Inter retains the
website's 430 body and 450 control weights; Geist Mono supplies code and metadata.
This is an application adaptation, not an exact typography match to langfuse.com.

## Application adaptations

- Data-heavy presenter pages retain an 1100 px maximum width. Narrow evidence
  tables scroll inside their frames rather than splitting column headings.
- Customer choices form a two-column grid on narrow screens. The message field
  reserves enough space for its two-line placeholder. No customer is omitted.
- Controls have 32 px minimum height and can wrap longer action labels. Controls
  without shortcut keys keep balanced horizontal padding; no shortcut is implied.
- Keyboard focus uses 2 px of strong corner ink with 3 px offset. This explicit
  accessibility adaptation is more distinct than the captured neutral ring.
- Semantic success and warning text uses contrast-adjusted ink (`#3d6921` and
  `#765200`) rather than the raw callout colours. All states retain text labels.
- Existing banking avatars remain circular; message and control geometry uses
  2 px corners. The console retains the website's dark code surface within the
  light application. Reduced-motion preferences suppress decorative transitions.

## Verification boundary

The styling was inspected in Chromium at 1440 px and 390 px through the delivered
HTTP application using local target/provider fixtures. Checks cover all nine page
routes, customer selection, hover, keyboard focus, disabled actions, loaded font
files and absence of external browser requests. The existing voice browser
rehearsal also covers synthetic microphone recording, upload, playback, Spanish
confirmation, feedback and reset below a live prefix.

These fixtures establish rendering and application behaviour. They do not prove
live provider access, native governance permissions, a human review or a final
Depot admission. Published runbook captures must retain their explicit fixture
labels until replaced with evidence from the qualified deployed candidate.
