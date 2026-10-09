# Candidate acceptance record

Copy this checklist into the candidate's manual acceptance notes. Use synthetic
data. A checked checklist is specific to one build, not a blanket approval.

- Candidate run URL/ID:
- Source commit:
- Version:
- Windows archive SHA256:
- Linux archive SHA256:
- Tester/date and OS versions:

## Automated gates

During the pre-release billing pause, repository workflows are manual-only.
CI, Documentation, Dependency audit and Experimental desktop candidates are also
disabled on GitHub until their manual-only definitions reach `main`. Keep local
checks and documentation review; do not enable them against the older automatic
definitions. Dependabot's own update service remains enabled and does not consume
included Actions minutes on standard GitHub-hosted runners.

- [ ] Finish source/docs review locally, merge the approved source into `main`,
      then manually run **Build candidate** once on that commit. No tag-triggered
      build is needed: publication creates `v0.1.0` at the tested commit.
- [ ] For a transient failure at the same source SHA, choose **Re-run failed jobs**;
      successful platform artifacts can be reused across attempts of that run.
      If source must change, run a new candidate and repeat affected acceptance.
- [ ] Publish with **Publish tested candidate** only after acceptance. This copies
      existing artifacts and does not rebuild installers. Do not also run ordinary
      CI/docs workflows just to duplicate gates already covered by the candidate.
- [ ] Restore automatic CI/docs/audit triggers explicitly after the billing pause;
      enabling a workflow alone keeps the new manual-only definition manual.

- [ ] Install the locked tooling with `uv sync --locked --all-groups`, then run
      `uv run python tools/release.py preflight` and the synthetic source
      smoke (`uv run python tools/package_smoke.py`) before spending hosted build
      minutes. Use the locked dependencies and a clean final commit for candidates.
- [ ] Windows/Linux unit and AppTest suites pass.
- [ ] Required Chromium browser suites run with no skipped modules.
- [ ] Package content/privacy and source-install checks pass.
- [ ] Candidate archives contain no private workspace data, local installation
      provenance or personal build paths; archive owner metadata is neutral.
- [ ] Extracted binaries pass lifecycle and browser navigation checks on both OSes.
- [ ] Linux binary tested on Ubuntu 22.04 and 24.04.

For paid runs, dispatch **Build candidate** and **Experimental desktop candidates**
once each on the same final source SHA; leave diagnostics-only disabled for the
installer build. The existing timeouts cap configured execution at 45 Windows,
145 Linux and 85 macOS runner-minutes, excluding unrelated CI/publication jobs
and reruns. Do not raise timeouts or add automatic test retries to obtain a green
candidate. Investigate failures locally before another paid attempt.

The supported publisher requires both platform artifacts from the same run
attempt. After a failed supported candidate, dispatch a fresh complete candidate
run; do not combine selectively rebuilt artifacts or reuse old manifests.
Experimental desktop artifacts remain separately reviewed and are not attached
automatically by the supported publisher.

## Manual gates (clean machines, no Python or uv)

- [ ] Extract, launch, create shortcut, relaunch after reboot. On Windows use
      Portfolio Breakdown.exe and verify no terminal flashes; the console
      portfolio-app.exe remains available for CLI diagnostics.
- [ ] Repeat launch opens existing app; occupied port and startup failure are understandable.
- [ ] Stop/restart leaves no server behind; closing only the browser is documented.
- [ ] Intro completes before the welcome dialog; reduced motion and automatic timeout
      on a failed component work, with no Skip button.
      Normal reruns/workspace switches do not replay it.
- [ ] Empty workspace offers Explore demo and Start my portfolio. Manual entry
      and holdings import remain in Positions; cancel writes nothing, existing
      portfolios skip welcome, and synthetic position edits/save/restart work.
- [ ] Guided setup starts with an empty category form; examples are help only.
      Enter adds a row and focuses/highlights the next empty name. Complete targets
      totaling 100% open Continue/Keep editing; editing preserves the draft and
      does not immediately reopen the same confirmation. Tab/Shift+Tab preserve the input sequence, contextual help is accessible,
      and All set? shows an aligned table.
- [ ] Guided setup saves optional categories and optional whole-portfolio targets;
      blank/partial targets stay unknown or retain their entered percentages.
      Skip setup and Finish later work. The first position can be assigned to a
      category; within-category targets stay separate. Existing allocations are
      retained and concurrent edits are rejected.
- [ ] EUR/USD/GBP selection survives guided setup, Skip setup, Finish later and
      restart. New buy-ins default to the reporting currency; existing originals stay intact.
- [ ] Portfolio settings currency review supports cancellation, exclusions and explicit
      per-position FX-estimate confirmation. Portfolio settings closes before the review dialog.
      Estimates remain labelled and fixed across refreshes; reverting currency restores
      original-currency costs. Missing purchase FX affects gains, not current-value totals.
- [ ] Mixed-currency purchase batches, per-row dates/rates and supplied converted
      totals survive save/reopen. Balance replacements invalidate incompatible costs.
      Monetary planning inputs reset on currency change; navigation and targets remain.
- [ ] Physical gold spot valuation gives equal values for equivalent troy-ounce,
      gram and kilogram weights; USD quotes convert to the selected reporting currency. Quote timestamps,
      missing prices/FX and cached fallback stay visible. Manual pricing remains
      available and existing manual holdings stay unchanged until explicitly switched.
      Unit changes clear draft amounts; editing preserves the stored unit.
- [ ] Header workspace dropdown, question-mark help and Portfolio settings work at desktop
      and narrow widths; Portfolio settings retains recovery controls with invalid inputs.
- [ ] Demo targets (60/25/10/5), equity 70/30 split, mixed gains/losses, both
      equity ETF issuer breakdowns, real price history and rebalancing work with
      live public data. Retry/missing-data states are honest; quantities initialize
      once and survive refreshes. Explicit --offline-demo works without network.
      Demo changes never affect the persistent workspace and reset on restart.
- [ ] Demo, all main tabs/modes, dialogs, charts and lists work.
- [ ] Optional tour invitation, Not now and Help replay work. All 15 steps,
      chart interaction, chapter navigation and light/dark spotlights work in
      the Windows native window and supported Linux browser. Finish, Skip tour
      and Escape restore the original workspace/view without changing holdings
      or targets; dismissal survives restart. Verify separately from the intro.

- [ ] Position entry shows compact quantities, linked average/total buy-in and
      adjacent category/target controls. Changing quantity retains the last edited
      cost as authoritative; zero holdings and unknown costs remain supported.
- [ ] Search has explicit selection actions, grouped verified listings and manual
      entry, without investment-specific suggestion chips. Keyboard selection works.
- [ ] Optional purchase rows calculate quantity and fee-inclusive cost; invalid
      rows cannot save. Switching modes and dismiss/resume retain session drafts;
      saving writes the holding and purchase records together once.
- [ ] Action and column help is available on hover and keyboard focus. Native
      Light/Dark/System themes work; normal launches hide developer menu options.
- [ ] Futures sandbox absent from package, navigation and CLI.
- [ ] CSV and Excel imports reviewed and saved; cancel writes nothing, workspace
      switching clears drafts, navigation retains uploads/mappings/edits, restart
      retains positions and live-price linking works.
- [ ] Extended ETF discovery works for synthetic newly added/imported positions;
      official-URL setup and refresh, normalized CSV fallback, failure retention,
      and unsupported-fund states work without changing positions or prices.
- [ ] Bond summaries and security details render; missing metadata remains unknown;
      overnight economic exposure stays separate from its substitute basket in
      allocations, saved snapshots and workspace backups/restoration.
- [ ] Spaces and non-ASCII paths work.
- [ ] Offline/stale/missing data states are understandable.
- [ ] Migration, backup and restoration preserve an invented complete workspace.
- [ ] Portfolio settings creates a `.portfolio-backup.zip` with currency settings,
      purchase-cost/FX metadata, categories/targets, classifications, ETF holdings
      and baskets, overrides, caches and document backups. Verify downloaded bytes.
- [ ] In browser and Windows WebView2 modes, native upload/Save dialogs work and
      cancel cleanly. Invalid/unsafe/incompatible archives and existing destinations
      are refused before activation. Review cancellation creates no workspace.
- [ ] Restore to an edited new path containing spaces/non-ASCII characters; confirm
      review, switch in the same window/tab, visit all tabs, and restart through
      the same shortcut. The restored portfolio stays selected; the old folder
      remains intact. Repeated launch focuses the same instance; Stop/close works.
- [ ] Backup during ETF/quote refresh and a portfolio save produces a complete
      consistent snapshot. Old tabs and pending workers cannot save after a switch.
- [ ] Activation failure preserves both folders and offers retry. Missing remembered
      targets fail visibly; `--ignore-workspace-selection` opens the original after
      stopping the app. Native acceptance must use newly built candidate bytes.
- [ ] Upgrade from an earlier candidate preserves files and shortcuts are refreshed.
- [ ] Uninstall removes application files/shortcuts only, leaving portfolio data.
- [ ] Final icon approved and checked as favicon, shortcut and executable icon.
- [ ] GPL corresponding source and third-party notices included.
- [ ] Checksums verified; exact candidate approved for publication.

No manual acceptance has been performed or release approved merely by adding
this checklist. Artwork is integrated; its appearance on each target desktop and
the untested operating systems still need acceptance.

## Integrated window / Setup candidate

- [ ] Record Setup EXE SHA256 as well as portable archive hashes.
- [ ] Windows 11: install as a standard user, accept or skip the optional WebView2
      prerequisite, launch from Start Menu and optional desktop shortcut.
- [ ] No terminal flash; intro stays visible until the initial app/charts render.
- [ ] F11 works with focus in the app and its controls; borderless edges fit the
      monitor exactly, Exit works, returning to windowed mode restores maximize.
- [ ] Native file upload and download/save dialogs work with synthetic CSV/Excel.
- [ ] External links open the system browser; bundled help opens the matching
      offline guide. No external navigation replaces the app window.
- [ ] Repeated launch focuses one existing instance; close/Stop ends its server;
      relaunch succeeds and the browser fallback works independently.
- [ ] Run from outside the checkout with Python/uv absent from PATH. Record whether
      Python/uv are installed on the host; that is not a clean-machine test.
- [ ] Upgrade/reinstall preserves an invented workspace and leaves one usable
      shortcut; uninstall removes app/shortcuts but preserves that workspace.
- [ ] All allocation charts use the icon palette, largest allocations first;
      navigation retains consistent category colors within the current portfolio.
- [ ] Candidate docs identify the same source SHA as both binary manifests.
      Docs build, internal links, search, mobile layout and task walkthrough pass.
- [ ] Pages publication and official release publication remain separately approved.

## Optional experimental Linux/macOS installers

These checks are independent of supported Windows-window and Linux-browser
acceptance. Defer an experimental artifact if it cannot be verified; it must not
block the supported release or inherit acceptance from an older installer.

- [ ] Build from the final integrated source and record its commit, lock hash,
      installer SHA256 and native reports. Earlier experimental artifacts are
      not final v1 builds.
- [ ] Keep `.deb`/DMG artifacts explicitly experimental and outside automatic
      supported-candidate promotion. Publication still requires approval.
- [ ] Ubuntu 22.04/24.04: verify installed X11/Wayland rendering, file dialogs,
      exact download bytes, focus/restore, repeated launch and complete shutdown.
      Upgrade/removal preserves the synthetic workspace.
- [ ] Apple Silicon macOS: verify the exact final DMG's native workflows and
      document ad-hoc signing, Gatekeeper rejection and any unverified interactive
      approval/download-quarantine behavior. No paid signing/notarization is
      planned for v1; do not describe the app as notarized or routinely trusted.
- [ ] Rerun hosted checks when the billing block is resolved. Local unit/browser
      results do not replace native acceptance of the new packaged artifacts.
