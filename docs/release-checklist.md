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

- [ ] Windows/Linux unit and AppTest suites pass.
- [ ] Required Chromium browser suites run with no skipped modules.
- [ ] Package content/privacy and source-install checks pass.
- [ ] Candidate archives contain no private workspace data, local installation
      provenance or personal build paths; archive owner metadata is neutral.
- [ ] Extracted binaries pass lifecycle and browser navigation checks on both OSes.
- [ ] Linux binary tested on Ubuntu 22.04 and 24.04.

## Manual gates (clean machines, no Python or uv)

- [ ] Extract, launch, create shortcut, relaunch after reboot.
- [ ] Repeat launch opens existing app; occupied port and startup failure are understandable.
- [ ] Stop/restart leaves no server behind; closing only the browser is documented.
- [ ] Empty workspace offers demo, manual entry and holdings import; each route
      works, cancel writes nothing, existing portfolios skip welcome, and
      synthetic position edits/save/restart work.
- [ ] Demo targets (60/25/10/5), equity 70/30 split, mixed gains/losses, both
      equity ETF breakdowns and rebalancing work offline; demo changes never
      affect the persistent workspace and reset on application restart.
- [ ] Demo, all main tabs/modes, dialogs, charts and lists work.
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
- [ ] Upgrade from an earlier candidate preserves files and shortcuts are refreshed.
- [ ] Uninstall removes application files/shortcuts only, leaving portfolio data.
- [ ] Final icon approved and checked as favicon, shortcut and executable icon.
- [ ] GPL corresponding source and third-party notices included.
- [ ] Checksums verified; exact candidate approved for publication.

No manual acceptance has been performed or release approved merely by adding
this checklist. Artwork is integrated; its appearance on each target desktop and
the untested operating systems still need acceptance.
