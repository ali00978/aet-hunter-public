# AET Hunter — Public development code

This repository is a **clean, history-free copy of selected development source** from the AET CARS AI Hunter project. It is not the production scanner.

## What is public here
- Scanner/parser and vehicle valuation logic for **Marktplaats**.
- Facebook candidate filtering and vehicle-photo validation, reading synthetic/offline data only.
- Offline Python regression tests for both platforms, run with network access disabled.

## What remains private
- The original AET CARS repository and the Facebook/Apify provider collector.
- Real adverts, scan snapshots, pricing history, favorites, customers, and any database contents.
- AET Pro, Cloudflare D1, API credentials, synchronization secrets, and production scheduled workflows.

Run tests on Python 3.12: `python run_offline_tests.py`.

**Anyone can read the published scanner and pricing code.** No live scraping, commercial scheduled workflows, database access, or production sync is configured here. The actual two-hour scans require separate authorized hosting; public GitHub Actions are used exclusively for software-development CI.

## Owner-gated original Hunter on public GitHub Actions

A second, opt-in workflow `.github/workflows/public-original-hunter.yml` is
scheduled every two hours, but its entire job is **skipped by default** unless
the repository owner explicitly sets the GitHub Actions variable
`AET_PUBLIC_HUNTER_ENABLED=true`. It then executes the same original private
Marktplaats and Facebook scanner code via a short-lived clone. The state,
provider credentials, history, and AET Pro sync stay private; GitHub Actions
logs and workflow definitions are public. Four encrypted Actions secrets must
be configured before enabling the workflow. See `PUBLIC_GITHUB_HUNTER_SETUP.md`.

**Policy caveat:** GitHub's Additional Product Terms limit Actions to
software-development purposes and restrict burdensome/non-project usage.
Long-running recurring commercial scanner workloads may be stopped or restricted,
even though standard hosted Actions minutes in public repositories are free.
