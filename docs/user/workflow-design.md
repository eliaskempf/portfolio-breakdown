# Designing portfolio workflows

Use this guide when adding a screen, moving an action, or presenting a familiar figure in a new place. Design for the investor who understands their holdings and has never seen this menu, then try the same task as someone doing it for the twentieth time. The [interface design contract](development.md#interface-design) defines the app's chart, dialog, rendering and financial presentation details.

## Finding and finishing a task

| Decision | Product convention | Walkthrough check |
| --- | --- | --- |
| Where an action belongs | Start with the object the investor is looking at. Offer a contextual shortcut to its existing editor. Retain scope and selection across the handoff. | Open a holding from Overview. Can you tell which holding and account will be edited? Where does closing take you? |
| Naming a destination | Use the investor's task or object. Use the same term in navigation, empty states, confirmations, tour and guide. Use **category** for the user's allocation grouping. | Can someone predict the destination for editing a category target from the labels alone? |
| Selecting a view | Exactly one active choice for mutually exclusive views, including after a repeat click. Keep the choice next to the content it governs. Give a genuine optional filter an explicit clear state. | Select an import mode twice. The visible selection, uploader and save action must still agree. |
| Finding secondary controls | Put routine actions alongside results. Keep methodology and infrequent configuration in contextual help or a secondary panel. Make a prerequisite actionable where it blocks a task. | On an empty workspace, follow the available action to its usable form. Check the same screen after the prerequisite exists. |
| Entering, correcting and leaving | Identify the affected holding, account or statement in human terms. Preserve correct fields after validation fails. Offer a way back from a pending destructive action. Make any draft loss apparent before it occurs. | Correct a single row, back out of review, then resume. Can you identify what persisted and what still needs applying? |
| Repeated use | Preserve applicable scope, filters and period when returning to a view. Revalidate choices when the portfolio changes. Keep inactive views inexpensive. | Return to a filtered list after a detail view. Switch workspace and check that a record from the old workspace cannot become a write target. |

## Menus and button placement

The header exposes the active portfolio, live price refresh where available, notifications and one **App menu**. The menu has three destinations. **Preferences** holds appearance, printing, reporting currency and display conventions. **Workspace** holds folder, backup, restore and shutdown. **Help** holds the guide and tour. An error can open the relevant destination directly. Keep actions on individual holdings, filters, rules and targets with the objects they affect.

| Scope or state | Placement and availability |
| --- | --- |
| Page, table or chart | Put a create action beside the collection it adds to. Put a refresh, filter or reset beside the results it changes. A selected-row action belongs with the selection. Name the object when several are present. |
| Form or review | Put completion after the inputs and any review evidence. Use one primary action for the current step. Group it with Cancel or Back in one wrapping row, with the ordinary completion first in reading and keyboard order. Keep alternative destinations separate from Save. |
| Consequential confirmation | Identify the operation, affected object and effect. Put the safe exit first when confirming a deletion, loss of unsaved work or shutdown. Give the committing button a specific verb. Opening a menu or navigating to a detail must be safe. |
| Availability | Omit actions outside the current task or workspace. Keep a normal next step visible when the user can meet a prerequisite here. Show the missing requirement beside the relevant input. For typed forms, allow a submit to validate the last edit. Disable a review or commit when its prerequisite is visible and its status updates reliably. Explain an unavailable cross-workspace operation at its usual destination. |
| Read-only detail | Provide a predictable close or return path. Place edit and follow-up actions with the detail they act on. Verify Escape and return focus when an overlay closes. |

Positions opens on its holdings list. Keep **Add position** and **Import** beside it. Put portfolio maintenance under **More actions** and give each task a named return to the list. Returning must preserve its filter, order and row. Select a journal row by click or keyboard to see its facts before offering correction or reversal.

For every changed surface, check the first action a user encounters, the action beside the relevant object, and the final action after completing the inputs. Try an empty, selected and invalid state. At a narrow width, the same actions must remain reachable in the same meaningful order. These conventions draw on the [GOV.UK button guidance](https://design-system.service.gov.uk/components/button/) and the [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).

## Financial meaning on screen

- **Position**, **transaction** and **exposure** answer different questions. A saved holding is the editable position. A transaction records a dated event. Exposure may include several positions and ETF constituents. A constituent drilldown should identify its source and lead to the appropriate level of detail.
- Put the basis beside results wherever confusion would change a decision. A selected category's weight within a subset, its weight in the portfolio, funded capital and signed market exposure may all have different denominators. Use distinct labels when these results appear together. Preserve the taxonomy hierarchy.
- Show the reporting period and currency where they apply. Identify an all-history annualized return when a neighboring chart covers a selected period. Avoid presenting a current holding's cost-based gain as a personal historical return. Use the same precision and units when the same metric recurs in its chart, table and detail view.
- Separate zero, missing, estimated, stale and partial. An ETF residual retains its unassigned share. A missing observation should identify the affected result and, where possible, lead to the relevant position, date or source. Keep the status local and concise. Use **Unavailable** for a headline return with insufficient evidence and **N/A · Cash balance** when the measure does not apply. Attach the reason to that metric. When a dated observation can be corrected, put the action beside the metric and preserve the holding, scope and period. Keep explanatory prose in on-demand help.
- Use verbs that predict the effect. **Preview** computes a result. **Save** persists the named inputs or evidence. **Apply history** changes the authoritative balance after review. **Calculate plan** produces proposed trades. Identify the subject of a consequential action at confirmation. Keep ordinary repeat saves short. Changed source records need individual decisions. Remember an explicitly declined set of facts, and ask again when the source changes. Edit a given target weight through the same parent-relative editor from every entry point.

## Interaction and accessibility

Use visible text or an accessible name for icon actions. A keyboard user must be able to identify the active view, open a detail, reach its help, recover from an error and close it with focus in a sensible place. Provide an equivalent route to essential chart and hover information. Check focus visibility, zoom, smaller desktop windows, and color-independent status cues. Keep a named object, its status and its next action together as the layout narrows.

Show response to an interaction promptly. Replace the old view before drawing incompatible actions for the next one. Keep a form usable during ordinary background refreshes. Test first visits and returns in the running app with realistic synthetic histories.

## Reviewing a change

1. Write one task in the investor's words and specify its starting workspace and expected outcome. Attempt it through normal navigation before consulting the guide.
2. Look for the same concept elsewhere. Check names, units, defaults, action placement, draft handling and the destination after save, cancel or back.
3. Walk a populated case and the relevant exceptional case. Verify durable state as well as what the user sees. Exercise keyboard-only input on changed controls.
4. Verify affected tabs in the restarted preview, including a return visit. Update the guide, contextual help and tour wherever the path changed. Extend this specification when a new reusable interaction convention is introduced.

These checks adapt general principles of recognition, consistency, user control and error recovery, together with accessible interaction criteria. [Nielsen Norman Group usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) · [WCAG 2.2](https://www.w3.org/TR/WCAG22/)

Two comparable products illustrate specific decisions. Portfolio Performance makes a statement's effective date, currency and portfolio filter available in its toolbar. Finanzfluss Copilot offers an identifiable import entry and guides the user to a method. For this app the corresponding tests are whether investors can identify the basis for a displayed value and discover the import method that accepts their evidence. [Portfolio Performance statement of assets](https://help.portfolio-performance.info/en/reference/view/reports/statement/) · [Finanzfluss Copilot import flow](https://www.finanzfluss.de/copilot/hilfe/import-flow/)
