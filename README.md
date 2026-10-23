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
