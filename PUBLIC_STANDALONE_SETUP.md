# AET Hunter — zelfstandig op de openbare code, buiten betaalde privé-Actions

**Status:** broncode, scanning, de Apify-collector, AET Pro v2-exports, Modal-planning
en privé-opslag staan gereed. Dit document is **geen bewijs dat Modal is gedeployed**.

## Zo werkt de zelfstandige versie

- De originele `ali00978/aet-cars-ai-hunter` wordt **niet gekloond, gelezen of
  bijgewerkt** tijdens de uitvoering. Alleen de openbare repository is runtimebron.
- Modal draait de Marktplaats-scanner elke 2 uur: UTC 00:17, 02:17, enzovoort.
  Facebook is een aparte taak om 02:47 en 14:47 UTC.
- Scannerbestanden, historie, advertenties, foto's, deduplicatie, quota en
  gecombineerde AET Pro-export blijven in een **niet-openbare Modal Volume**.
- Er wordt geen advertentiesnapshot naar openbare GitHub commits, logs of artifacts gestuurd.
- De gecombineerde sync gebruikt het bestaande AET Pro-contract:
  `aet-pro.hunter.sync.v1` met een `aet-pro.hunter.v2` batch. Geldige
  kandidaten bevatten foto's als de leverancier die aanlevert.
- Eén Apify-run reserveert een vooraf ingestelde maximale uitgave op schijf
  **voor** het uitvoeren. Bij timeout of fout telt hij nog steeds mee.
  Facebook is standaard uit tot er expliciet een maandlimiet is ingesteld.
- Wordt Marktplaats beperkt (HTTP 403/429), dan blijft de cooldown privé
  bewaard; er wordt niet geprobeerd blokkades te omzeilen.
- Geen enkele verkoopmarge wordt gegarandeerd; de AET Pro-import en
  automatische favorieten moeten na deployment nog end-to-end geverifieerd worden.

## Eenmalige activering

1. Maak een account aan op https://modal.com/ en controleer het gratis
   tegoed, limieten en eventuele betaalgegevens. De dienst kan geld kosten
   buiten het gratis tegoed.
2. Maak in Modal een Secret `aet-hunter-runtime` aan met:
   `AET_PRO_SYNC_URL` (bestaande HTTPS AET Pro-endpoint),
   `AET_HUNTER_SYNC_TOKEN` (bestaande geheime token),
   `AET_MODAL_LIVE_ENABLED=0` (aanvankelijk uit).
   Voor Facebook aanvullend `APIFY_TOKEN`,
   `AET_FACEBOOK_ENABLED=true`, `AET_APIFY_MONTHLY_BUDGET_USD`
   (jouw expliciet goedgekeurde budget, bv. `3.00`) en
   `AET_APIFY_MAX_CHARGE_USD` (maximaal 0,50 per run; bv. `0.10`).
   Bij gratis gebruik / onbekende prijslimiet: Facebook UIT laten.
   **Nooit tokens in chats of publieke bestanden plakken.**
3. Maak in de openbare GitHub-repo onder Settings > Secrets and variables >
   Actions de geheime waarden `MODAL_TOKEN_ID` en `MODAL_TOKEN_SECRET`
   aan. Ga naar Actions > **Deploy standalone PUBLIC Hunter to Modal**
   > Run workflow. GitHub doet uitsluitend de deployment, geen terugkerende scans.
4. Controleer op Modal dat de nieuwe app
   `aet-cars-public-hunter` is gedeployed, inclusief beide crons.
   Zet pas daarna in de Modal Secret
   `AET_MODAL_LIVE_ENABLED=I_ACCEPT_LIVE_SCANS` om daadwerkelijke scans
   te starten. Controleer dat de uitvoering de gewijzigde Secret gebruikt.
5. Controleer de privé-opslag, scan-health, Marktplaats-kandidaten,
   Facebook-quotum, foto's en HTTP 200/received van AET Pro.
   Controleer favorieten in AET Pro afzonderlijk: sync is niet automatisch
   hetzelfde als favoriet.

## Wat deze route niet kan

GitHub Actions kan deze Modal-app alleen deployen als de benodigde Modal-tokens
in de Actions Secrets staan. Zonder die accounttoegang kan geen AI
eigenstandig hostingaccounts en bijbehorende kostenlimieten activeren.
GitHub-hosted Actions wordt bewust niet omgebouwd tot periodieke commerciële
scanner. De originele privé-repository en privé-Actions blijven onaangeraakt.
