# GitHub openbaar is Plan A: AET Hunter zelfstandig in Actions

**Stand op 23 oktober 2026:** De zelfstandige workflow is ingesteld op automatische starts op de openbare GitHub-repository. De eigenaar heeft opdracht gegeven de GitHub-hosted uitvoering te activeren. Controleer dat de eerste run succesvol is en dat de versleutelde cache wordt opgeslagen. De uitvoer blijft afhankelijk van de GitHub-voorwaarden.
De originele privé-repository en haar Actions blijven ongemoeid.

## Hoe gaat een run?

- Marktplaats cron: `17 */2 * * *` (UTC), eens per twee uur.
- Facebook/Apify cron: `47 2,14 * * *` (UTC), maximaal 2 geplande starts
  per dag en alleen met expliciete budgetvariabelen.
- Beide jobs draaien rechtstreeks de code van `ali00978/aet-hunter-public`;
  ze gebruiken **nooit** een GitHub-token voor de privé-repository.
- De scanner gebruikt dezelfde filters en marktvergelijking als de geteste
  openbare code en verzendt uitsluitend gezonde batches naar de bestaande
  HTTPS AET Pro Worker.
- Scanresultaten en Apify-quotum worden clientside met AES-256-GCM
  versleuteld en als privé-inhoud in een *versleutelde GitHub cache* bewaard.
  De cache is zelf potentieel leesbaar voor forkmakers; er mogen dus geen
  plaintext kandidaten of geheimen in staan. De sleutel wordt uit de lange,
  willekeurige `AET_HUNTER_SYNC_TOKEN` afgeleid, is geen publiek bestand
  en het wijzigen van die token vereist bewuste herstel/migratie van cache.
- Er worden geen car-advertenties, snapshots of logs met volledige
  kentekens, foto's of URLs als open artifacts gepubliceerd.

## GitHub-hosted risico en pauzeren

De eigenaar kiest uitdrukkelijk GitHub-hosted als plan A. Let op: GitHub's
[Additional Product Terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
beperken hosted runners tot softwareontwikkeling, testen, bouwen,
deployen en publiceren. Terugkerend commercieel scannen kan buiten deze
voorwaarden vallen. GitHub heeft voor deze specifieke productieactiviteiten
**geen goedkeuring bevestigd** en kan workflows of het account beperken.
De workflow doet daarom geen onjuiste claim van GitHub-toestemming.

**Direct pauzeren:** maak onder
[Actions Variables](https://github.com/ali00978/aet-hunter-public/settings/variables/actions)
`AET_PUBLIC_HUNTER_ENABLED=false`. Dit stopt toekomstige geplande jobs;
een al gestarte run moet apart worden gestopt.

AET Pro Secrets `AET_PRO_SYNC_URL` en `AET_HUNTER_SYNC_TOKEN` zijn eerder
gecontroleerd met HTTP 422 op een bewust ongeldig bericht en HTTP 401
zonder token. Bewaar deze Secrets privé. Er is geen token nodig
voor de originele privé-repository.

## Eerste activering en herstel

1. De eenmalige activerings-commit triggert automatisch de eerste
   Marktplaats-run op de openbare repository, zonder gebruik van privé-Actions.
2. Als dit niet gebeurt, inspecteer [AET Hunter PUBLIC standalone scanners](https://github.com/ali00978/aet-hunter-public/actions/workflows/public-original-hunter.yml)
   voordat je opnieuw probeert. Bij herstel mag `bootstrap_state=true` alleen
   worden gebruikt als zeker is dat er nog geen geldige historie of Apify-budget
   bestaat; anders kan de historie verloren gaan.
3. Controleer in de logs of de scan `synced` meldt en of de
   `actions/cache/save@v4` stap werkelijk de versleutelde cache heeft
   opgeslagen. Een groen resultaat zonder cache-save telt niet als
   duurzame uitvoering.
4. De daaropvolgende geplande Marktplaats-scans herstellen de nieuwste
   encrypted cache. Bij verdwenen/geëvict cache stoppen ze *veilig*:
   nooit automatisch opnieuw scannen of Apify-budget resetten.
   Inspecteer oorzaak en herstel geschiedenis voor een bewuste bootstrap.
5. Facebook vereist daarnaast `APIFY_TOKEN` als Secret,
   `AET_FACEBOOK_ENABLED=true` en de Actions Variables
   `AET_APIFY_MONTHLY_BUDGET_USD` en
   `AET_APIFY_MAX_CHARGE_USD` (maximaal 0,50 per start).
   De eerste Facebook-run vereist reeds bestaande state uit Marktplaats;
   ontbrekende quota-geschiedenis mag niet worden genegeerd.

## Risico's

GitHub caches zijn **niet gegarandeerd blijvende opslag**: ze kunnen
worden geëvict en soms mislukt een save. Het budget mag daarom nooit
zonder bevestiging worden gereset. De machine kan door Marktplaats
worden gelimiteerd (403/429); de scanner houdt verplichte cooldowns aan.
Een geplande workflow kan vertraagd worden of wegvallen. GitHub zelf kan
terugkerende productieactiviteiten beperken of beëindigen.

De oude privé-repository wordt niet veranderd. AET Pro ontvangen data
blijft privé bij de reeds bestaande Worker. Eventuele in de app
gewenste automatische favorieten moeten als appfunctionaliteit apart
worden gecontroleerd; de sync kan dat niet garanderen.

**Plan B** wanneer GitHub niet kan of mag: zie
[Modal-setup](PUBLIC_STANDALONE_SETUP.md).
