# AET Hunter — Alternative Modal runner (prepared, not deployed)

Modal Starter currently offers $30 monthly compute credit and 5 scheduled
crons. This project uses 1 cron every two hours: 12 Marktplaats runs/day and\nonly 2 Facebook provider runs/day (02:17 and 14:17 UTC). Compute in excess of the
credit, or services outside included allowance, may be charged. Confirm
your account's current prices and spending controls before activation.

WHAT CHANGES: Only where the original Python code runs. A Modal container
privately clones the original AET CARS repo, executes scanner.py and
apify_test.py without changing them, pushes JSON state to the PRIVATE
repo, and uses the original AET Pro sync endpoint. Every two hours it
starts Marktplaats then Facebook in sequence to avoid Git push races.

WHAT DOES NOT CHANGE: private scan results, filters, candidate calculations,
photos, deduplication, AET Pro database, and core scanner scripts.

IMPORTANT: This public repository only runs offline tests and a one-time
deployment workflow on GitHub-hosted runners. Actual live scheduled
scans are never executed on GitHub-hosted machines.

ONE-TIME SETUP IN YOUR OWN ACCOUNT:
1. Create a Modal Starter account at https://modal.com and check budgets.
2. In Modal create a Secret named aet-hunter-runtime with:
   AET_GITHUB_TOKEN — fine-grained token scoped only to the PRIVATE
   ali00978/aet-cars-ai-hunter repository with Contents read/write;
   AET_PRO_SYNC_URL — existing private AET Pro synchronization HTTPS URL;
   AET_HUNTER_SYNC_TOKEN — existing production Hunter sync secret;
   APIFY_TOKEN — existing private Apify provider secret;
   AET_MODAL_LIVE_ENABLED — initially the text 0, not enabled.
   Never post these values in chat, public GitHub code or public logs.
3. Add Modal deployment credentials MODAL_TOKEN_ID and
   MODAL_TOKEN_SECRET as encrypted GitHub Actions repository secrets
   in ali00978/aet-hunter-public.
4. Use GitHub Actions, choose Deploy owner-controlled Modal scanner,
   Run workflow. This only deploys the app: live requests remain disabled
   until the Modal Secret key AET_MODAL_LIVE_ENABLED is explicitly
   changed to I_ACCEPT_LIVE_SCANS.
5. In Modal confirm disabled mode and billing controls, then enable
   carefully and inspect individual Marktplaats/Facebook run logs.
6. Verify multiple real scan snapshots, cooldowns, photos, favorites
   and the existing private AET Pro synchronization before relying on it.
7. Disable original hosted scan schedules after the new scans succeed;
   otherwise both services may run and duplicate website/provider calls.

ROLLBACK: Set AET_MODAL_LIVE_ENABLED back to 0 and restore the original
private GitHub schedules when sufficient hosted Actions minutes are
available. Never merge the separate Oracle runner PR until an Oracle
self-hosted runner is actually online.

LIMITATIONS:
- Modal is prepared but NOT DEPLOYED until owner adds Modal credentials and\n  enables the private deployment. Neither the public Action nor this file\n  runs production scans without the user's configured hosting.\n- Not yet deployed: no Modal account, tokens, or secret is connected here.
- The host must allow access to Marktplaats and Apify; their restrictions
  and cooldowns continue to apply.
- Apify's original 100-calls/month cap does NOT support 360 fresh
  Facebook provider calls every month without changing the provider plan.
- Same Python code and filters are reused, but cloud timings may differ.
- Existing private data never enters this public repository; it is
  handled by your personal Modal workspace and the original private repo.
