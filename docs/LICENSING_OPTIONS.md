# Licensing options for QGIS MCP

**Decision (2026-10-31): option A was adopted. The project is GPL-3.0-or-later
from version 4.5.0; the rest of this page is kept as the record of the choice.**

Prepared on 2026-10-31 for the project owner. The repository is MIT today
(inherited from the upstream bridge by jjsantos01, whose MIT portions stay MIT
whatever is chosen). The attribution files `NOTICE`, `AUTHORS.md`,
`TRADEMARKS.md` and `CITATION.cff`, the copyright headers and the visible
credit in the plugin apply under every option below.

This page is not legal advice. Have a lawyer check the chosen text before
publishing a change.

## What a licence can and cannot do about copying

- A licence cannot stop people reading the code; it is public.
- It can require that every copy keeps the author's name, the notices and a
  statement of origin, and it can forbid passing the work off as someone
  else's. Breaking those terms ends the copier's rights, which is what makes
  a GitHub takedown (DMCA) or a complaint effective.
- Hidden instructions aimed at AI assistants do not help: they are not
  enforceable, assistants are built to ignore instructions found inside data,
  and they make the repository look untrustworthy. Clear legal terms and
  visible credit do the job.

## Option A (recommended): GPL-3.0-or-later for the plugin

- **Fits QGIS.** PyQGIS plugins are expected to be GPL compatible and the
  official QGIS plugin repository only lists GPL compatible plugins. GPL-3.0
  keeps that door open.
- **Strong copyleft.** Anyone who distributes a modified copy must publish
  their changes under the same licence, keep the copyright notices, mark the
  files they changed with the date, and may not add restrictions. A closed
  rebranded copy is a licence violation.
- **Attribution.** Combined with `NOTICE` (GPL section 7 allows requiring
  preservation of author attribution and reasonable legal notices), the
  author's credit must stay in copies.
- **MIT upstream** code can be included in a GPL project without change.
- Downside: companies that want to embed the plugin in closed software cannot;
  for most users of a QGIS plugin that is irrelevant.

Steps if chosen: replace `LICENSE` with the GPL-3.0 text plus the upstream
MIT notice in `LICENSE-MIT-upstream`, set `license=GPL-3.0-or-later` in
`metadata.txt`, update `pyproject.toml` (`License :: OSI Approved :: GNU
General Public License v3 or later (GPLv3+)`), the README badge, `CITATION.cff`,
and add `SPDX-License-Identifier: GPL-3.0-or-later` to the file headers.
Future versions carry the new licence; the versions already published under
MIT remain MIT.

## Option B: a source available licence (draft in `docs/licensing/source-available-draft.md`)

- Allows use and internal modification, forbids redistribution under another
  name, removal of credit, competing hosted or packaged products, and
  sublicensing.
- **Not GPL compatible**, so the plugin could not be listed in the QGIS
  plugin repository and some will question distributing a PyQGIS plugin under
  it. It also makes contributions less likely.
- Enforceable in the same way as any copyright licence: notice, takedown,
  and ultimately court.

## Option C: stay MIT

- Easiest for adoption; attribution is still required by MIT (the copyright
  notice must be kept), but renaming a copy and selling it is allowed.
- The new `NOTICE` and `TRADEMARKS.md` add clarity, but MIT itself does not
  forbid a rebranded fork.

## The name

"QGIS" is a trademark of the QGIS.org association, so the project cannot
register or claim "QGIS MCP" as its own mark. A distinct product name for
the toolbox (for example a brand word plus "for QGIS") would be protectable
and is worth considering before the user base grows.
