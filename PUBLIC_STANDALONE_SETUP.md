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

## Eenmalige activering — twee ontbrekende secrets

De actuele openbare GitHub-deployment (23 oktober 2026) bevestigde dat
`AET_PRO_SYNC_URL` en `AET_HUNTER_SYNC_TOKEN` aanwezig zijn in GitHub.
**Alleen de twee Modal hostingsecrets ontbreken.**

1. Maak bij https://modal.com/ een account aan. Controleer hoeveel
   gratis tegoed je ontvangt, de kostenlimieten en eventuele betaalvoorwaarden.
   Maak in de accountinstellingen een Modal API tokenpaar aan.
2. Ga naar https://github.com/ali00978/aet-hunter-public/settings/secrets/actions
   en voeg exact deze Repository secrets toe:
   `MODAL_TOKEN_ID` en `MODAL_TOKEN_SECRET`.
   Plak geheime waarden **nooit in een GitHub-codebestand of een chatbericht**.
3. Open https://github.com/ali00978/aet-hunter-public/actions,
   kies **Deploy standalone PUBLIC Hunter to Modal (GitHub deploys only)**,
   klik **Run workflow** op branch `main`.
4. De workflow controleert eerst de AET Pro-token zonder voertuigen
   te importeren, zet de bestaande syncgeheimen over naar een
   APARTE Modal Secret `aet-public-hunter-runtime`,
   activeert automatisch de **openbare Marktplaats-scanner** en
   maakt de privé-opslag `aet-cars-public-hunter-state`.
   De originele privé-repository en bestaande privé-Modal Secret
   blijven onaangeroerd.
5. Controleer in Modal app `aet-cars-public-hunter` de geplande jobs,
   de eerste scan-health en de AET Pro syncbevestiging.

Facebook blijft financieel beveiligd en standaard uit. Als je later bewust
Facebook wilt activeren, zet dan `APIFY_TOKEN` als Actions Secret en
`AET_FACEBOOK_ENABLED=true`,
`AET_APIFY_MONTHLY_BUDGET_USD` (eigen maximaal maandbudget),
`AET_APIFY_MAX_CHARGE_USD` (maximaal 0,50 per run) als Actions Variables.
Herhaal dan de deployment. Zonder deze budgetinstellingen start geen
Facebook Apify-run.

De benodigde GitHub-runner doet alleen softwaretests en de eenmalige deployment,
nooit geplande commerciële advertentieverzoeken.
Eventuele Modal- of Apify-uitgaven vallen onder je eigen abonnement,
tegoed en ingestelde limieten.

## Wat deze route niet kan

GitHub Actions kan deze Modal-app alleen deployen als de benodigde Modal-tokens
in de Actions Secrets staan. Zonder die accounttoegang kan geen AI
eigenstandig hostingaccounts en bijbehorende kostenlimieten activeren.
GitHub-hosted Actions wordt bewust niet omgebouwd tot periodieke commerciële
scanner. De originele privé-repository en privé-Actions blijven onaangeraakt.
