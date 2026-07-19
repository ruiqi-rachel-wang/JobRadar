# JobDrop Build Week Submission Draft

## Submission fields

**Project title:** JobDrop

**Track:** Apps for Your Life

**Elevator pitch:** Find fresh jobs directly from company career pages—all in
one place.

## Project description

Job searching is fragmented. The same candidate repeatedly checks hundreds of
company career pages, sees stale reposts mixed with genuinely new openings, and
loses track of what they intended to apply for.

JobDrop brings direct-company openings into one freshness-first workspace. It
searches across broad occupational families, supports location and experience
filters, separates company industries from useful company attributes, and
orders every result by its source-posted date, falling back to JobDrop's added
date when needed and using discovery time within the same date. Users can
inspect the original posting, save a search, receive a daily alert, and track a
job from discovery through application.

For OpenAI Build Week, an existing JobDrop project was meaningfully extended
into the Fresh Discovery & Alerts experience. The extension adds comprehensive
role-family matching, canonical company discovery, consistent posted/added-date
ordering, reliable saved-search delivery, and a more resilient ingestion
pipeline. Failed or partial scans cannot silently expire valid jobs, and
scheduled scans survive contention or restarts.

Codex with GPT-5.6 Sol was the primary engineering collaborator. It helped audit
the pre-event codebase, trace behavior across SQL and browser paths, implement
and verify cross-layer changes, verify the product's ordering contract, and
build the regression suite. Human product decisions defined the date-ordering
contract, search-recall goals, source-safety boundaries, and the decision not to
auto-apply on a user's behalf.

## What is new versus the pre-event project

Pre-event baseline: `3c2ce52`

Build Week work to demonstrate:

1. Search “software engineer” and show related application, platform, backend,
   frontend, infrastructure, data, ML, and reliability engineering results.
2. Show industry and company-tag discovery without misleading mixed taxonomy.
3. Show that results use source-posted date first, added date when posted date is
   missing, and added time within the same date—without hidden relevance ranking.
4. Save the search, add a result to the application list, and show the daily
   alert control.
5. Briefly show the 54-test suite and the source/scheduling reliability work.

## Demonstration video script

Target length: 2 minutes 35 seconds. Hard limit: under 3 minutes.

### 0:00–0:20 — Problem

“Job seekers should not have to revisit hundreds of company career pages or
guess whether a listing is actually new. JobDrop collects direct-company jobs
and puts genuinely fresh opportunities first.”

### 0:20–0:45 — Product

Open JobDrop. Show the company and industry filters, then show the job table.
Explain that the source link always returns to the company's own posting.

### 0:45–1:15 — Comprehensive search

Search for “software engineer.” Point out related titles such as application,
platform, backend, data, or reliability engineer. Open one result and show its
location, experience, skills, description, and source URL.

### 1:15–1:35 — Date-ordering contract

Point to “Jobs by posted date” and the Posted column. Explain that JobDrop uses
the source-posted date first, falls back to the date it first discovered a job,
and uses precise discovery time to order jobs within the same date.

### 1:35–1:55 — Save and act

Save the search, enable the daily alert control, and add one job to the
application tracker. Explain that alerts run after the final fresh scan and use
per-search delivery cursors to avoid duplicates while retaining failed sends for
retry.

### 1:55–2:25 — Codex and GPT-5.6

Show the Build Week Git commits or a concise diff and the test output.

“I used Codex with GPT-5.6 Sol as my primary engineering collaborator. It traced
the search flow from SQL through fallbacks and both interfaces, verified my
posted-date and added-time ordering decision, aligned the documentation and
tests, and helped verify 54 scenarios across search, alerts, scheduling, and
source reliability. I made the product decisions; Codex accelerated the audit,
implementation, and verification.”

### 2:25–2:35 — Close

“JobDrop makes direct-company job discovery fresher, broader, and easier to act
on.”

## Repository and judge access checklist

- [ ] Push the `build-week-2026` branch and identify the final commit.
- [ ] Decide whether the judging repository will be public or private.
- [ ] If public, add an appropriate license after ownership review.
- [ ] If private, share it with `testing@devpost.com` and
      `build-week-event@openai.com`.
- [ ] Verify `https://jobdrop.site/` from a signed-out browser.
- [ ] Provide a free test account if the video demonstrates signed-in or Pro
      functionality that judges cannot otherwise access.
- [ ] Keep the working deployment available through judging and winner
      verification.
- [ ] Remove secrets, private data, copied proprietary text, and unauthorized
      assets from the judging repository and video.
- [ ] Record audio in English and do not use copyrighted music.
- [ ] Upload a public YouTube video shorter than three minutes.
- [ ] Run `/feedback` in the primary GPT-5.6 Sol build thread and paste the
      resulting session ID into Devpost.
- [ ] Submit before the internal deadline: July 21 at 6:00 PM ET.
- [ ] Confirm final submission before the official deadline: July 21 at
      8:00 PM ET / 5:00 PM PT.
