# Vacancy agent

A bot that checks company career pages on a schedule, keeps the vacancies that match a job search
(QA / LQA (RU/UK) / Tester / Compliance / platform certification and similar roles, with relocation
or remote work from Spain/EU), and reports only what is new.

It runs on GitHub Actions, reads pages with its own headless browser
([web_reader](https://github.com/AlexeyTatarinov/web_reader)), pre-filters titles with a role
dictionary, decides obvious cases by rules, and asks Gemini only for the uncertain ones.
Companies rooted in Russia or Belarus are excluded.

## Schedule

Every Monday and Thursday at 22:00 Madrid time. The workflow triggers twice in UTC (20:00 and 21:00)
to cover winter and summer time, and the program runs only when the local hour is 22
(`run_hour` in `config/settings.json`). A manual run ignores the hour.

## Where the results are

- Telegram: a message with the new matching vacancies, the number of open ones, and the companies
  that could not be read.
- `reports/latest.md`: the current report (readable directly on GitHub).
- `reports/YYYY-MM-DD.md`: daily archive.
- `docs/index.html`: the same report as a page for GitHub Pages (Settings → Pages → Branch main, folder /docs).
- `state/state.json`: remembered vacancies, verdicts, reading methods and contacts.

## Setup

1. Create a repository and upload all files.
2. Settings → Secrets and variables → Actions → New secret:
   - `GEMINI_API_KEY`: vacancy evaluation.
   - `TELEGRAM_BOT_TOKEN`: the Telegram bot.
   - `TELEGRAM_CHAT_ID`: the channel or chat id (the bot must be an administrator of a channel).
   - `SCRAPEDO_TOKEN`: optional, only for the fallback mode `READER=scrapedo`.
3. Actions → Daily vacancies → Run workflow. Set `telegram` to `false` for the first run so the
   existing vacancies are recorded without being sent.
4. After that the schedule runs it by itself.

## How to teach the agent to read a company (`config/overrides.json`)

Nothing else needs to be configured:

- `careers_url`: direct link to the page with the vacancy list (a search link such as `?q=QA` is fine).
  Example: `"Sandbox VR": "https://jobs.lever.co/sandboxvr"`.
- `rss`: a vacancy feed (RSS/Atom); it is checked first.
- `needs_link`: companies whose detected address turned out to be wrong; they wait for a link
  (the wait ends automatically once `careers_url` is set).
- `skip` and `skip_notes`: companies that are not needed, with the reason.
- `no_openings`: companies the owner confirmed have no vacancies right now.
- `open_applications`: companies that only accept a CV by e-mail or a general form.
- `applied`: companies the owner has already applied to.

After a link is changed, the agent tries it immediately instead of waiting for the 7-day pause.
If a page works only through JavaScript or is protected, the report contains a diagnosis.

## What can be changed

- Company list: `config/companies.md` (a table; the column "Where to look for vacancies" is the domain).
- Role dictionary (English, Ukrainian, Russian): `config/roles.json`.
- Gemini models, run hour, limits: `config/settings.json` (or the `GEMINI_MODELS` variable).
- Fit / not-fit criteria for Gemini: the `CRITERIA` text in `agent/judge.py`.
- Rule-based verdicts for obvious cases: `agent/roles.py`.
- Reader limits (time per page, scrolls, list pages, pages per run): `config/reader.json`.

## How pages are read

The paid scrape.do service is no longer needed. HTML pages are read by the `web_reader/` package
(a copy of the web_reader repository, updated with `scripts/sync_web_reader.sh`): a real headless
browser that runs JavaScript, closes cookie banners (consent is never given), expands "Show more",
reads embedded frames and paginated lists, and intercepts the JSON that the page loads itself.

Order of attempts: RSS, then the hiring-system API (Greenhouse, Lever, Ashby, Workable,
SmartRecruiters, Recruitee, Teamtailor, Personio), then a plain HTML request (free and fast), and
only if nothing is found, the browser. If a site answers "Access Denied", the report says so;
protection is not bypassed.

## How it works

1. For each company the link comes from the table, from `overrides.json`, or from a search
   (cached in `state/careers.json`).
2. If the page uses a known hiring system, vacancies are taken from its API. Otherwise the page
   links and JSON-LD are read, and each vacancy is opened and checked for "closed".
3. The title passes the role dictionary. Obvious cases are decided by rules; the rest go to Gemini
   in batches, which decides fit / not fit and writes a reason. Decisions are cached by link.
4. A vacancy that disappears from a successfully read page is considered closed.
   Vacancies from Russia and Belarus are discarded.
5. Companies that could not be opened are listed in the "Could not be read" section.
   Companies that only accept e-mail or a general form are listed in a separate section.

## Discovery of new companies (free)

`agent/discover.py` collects candidates from Wikipedia categories and Wikidata, verifies each one
(site, careers link, hiring system), and writes `state/candidates.json` and `docs/candidates.md`.
Gemini suggestions are off by default to save the free quota. Run it with Actions → Discover
companies. Promote verified candidates into the company list with `--promote`.

## Tests and checks

- Offline tests (no network): `python -m pytest tests`
- Run without a Telegram message: Actions → Daily vacancies → Run workflow → `telegram` = `false`.
- Telegram connection check: Actions → Telegram check.
- Diagnose one page with the browser reader: Actions → Diagnose page.

## Public copy

This repository is the public, code-only version of the project: it contains no run state, reports,
contacts or personal notes. `config/overrides.json` ships empty (only generic careers links); the
personal version of the project keeps its own overrides and state privately.
