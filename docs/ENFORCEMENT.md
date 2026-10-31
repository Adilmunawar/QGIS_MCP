# Protecting authorship and acting on misuse

This page is for the project owner. It is practical guidance, not legal
advice; for a dispute with money at stake, involve a lawyer early.

## 1. Keep the proof current (do this before anything happens)

- **Dated public record.** Every commit on GitHub carries a date and the
  author name. Release tags (`v4.5.0` and later) and the GitHub release
  pages with SHA256 checksums fix what each version contained.
- **Sign your commits.** Enable commit signing (GPG or SSH key) on your GitHub
  account so "Verified" appears next to your commits; a copier cannot forge
  that history.
- **Archive each release.** Submit the repository URL to
  https://archive.org/web/ and to https://archive.softwareheritage.org/save/
  after each release; both give an independent timestamp. A DOI from
  https://zenodo.org (connect the GitHub repository once) adds a citable,
  permanent record.
- **Keep the attribution markers.** The copyright headers, `NOTICE`, the
  credit in the Data Explorer and the server panel, and the project identity
  in `get_server_info` are what a stripped copy will be missing. Their absence
  is the evidence.

## 2. What counts as a violation

Under the QGIS MCP Toolbox Source Available License 1.0 (from version 4.6.0):

- using the Software in or for an organisation, or hosting it for others,
  without written permission;
- distributing, publishing or forking the Software or a modified version
  without written permission;
- publishing it under another name or presenting it as someone else's work;
- removing or hiding the copyright notices, `NOTICE`, or the author credit;
- sublicensing it or building a competing product on it.

Version 4.5.0 is GPL-3.0-or-later (its rules: same licence, notices kept,
changes marked) and versions up to 4.4.0 are MIT (notice kept, renaming not
forbidden). Code that came from jjsantos01's bridge stays under MIT. A copier
who took an earlier version is bound by that version's licence, not this one.

## 3. Steps when you find a copy that breaks the terms

1. **Record it.** Save the URL, screenshots, and a Wayback Machine capture
   (https://web.archive.org/save/). Note the date, the name shown as author,
   and which notices are missing. Keep the copy's commit history if visible.
2. **Compare.** Point to specific files or functions that match yours and
   to the commit in your history where they first appeared. Identical
   comments, test names and strings are convincing.
3. **Write to them once.** State who you are, which licence clause they broke,
   what you require (restore the credit and the licence, or remove the copy)
   and a deadline (7 to 14 days). Keep it factual. Under section 8 of the licence their rights ended when they broke the terms;
   they can be restored if they cure the breach within 30 days, which is
   usually the outcome you want.
4. **Takedown notices.** If they do not respond:
   - GitHub: https://github.com/contact/dmca (DMCA notice; GitHub removes the
     repository after review, usually within days; the other party can file a
     counter notice).
   - QGIS plugin repository: write to the plugin repository maintainers
     through https://plugins.qgis.org/ with the evidence.
   - PyPI, other registries and app stores each have an abuse or DMCA form.
5. **Escalate** for a commercial copier who ignores all of the above: a
   lawyer's cease and desist letter, then a claim for copyright infringement
   in your jurisdiction or theirs.

## 4. What to avoid

- Do not put hidden instructions for AI tools or obfuscation in the code; it
  is not enforceable and it harms the project's reputation.
- Do not accuse publicly before writing to them privately; a short private
  notice resolves most cases and keeps your position strong if it escalates.
- Do not claim rights over the upstream MIT code or over "QGIS", which is a
  trademark of QGIS.org.

## 5. Granting permission

Keep a simple record (a spreadsheet or a folder of emails) of who asked, for
what use, and what you granted, with the date. A short reply such as "You may
use QGIS MCP at <organisation> for <purpose> under the licence terms, credit
kept, no redistribution" is enough. Permission is personal to the named party
and does not transfer.
