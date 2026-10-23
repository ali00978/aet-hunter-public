# AET Hunter — Public development code

This repository is a **clean, history-free copy of selected development source** from the AET CARS AI Hunter project. It is not the production scanner.

## What is public here
- Scanner/parser and vehicle valuation logic for **Marktplaats** (source code is readable to everyone).
- Synthetic/offline Python regression tests.
- A network-blocked development test workflow.

## What remains private
- The original AET CARS repository, Facebook/Apify provider access and commercial operations.
- All real advert candidates, pricing history, favorites, scan snapshots, customers and database data.
- AET Pro, Cloudflare D1, API access keys, synchronization credentials and production scheduled workflows.

Run tests on Python 3.12: `python run_offline_tests.py`.

**The public source exposes searching and pricing algorithms.** This source copy does not contain any active scheduled scraping workflow, service credentials, or data synchronization. The actual two-hour production scans require separate authorized hosting; free GitHub-hosted Actions here are for software development CI, not a private Actions quota workaround.
