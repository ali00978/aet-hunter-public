# GitHub openbaar is Plan A: AET Hunter zelfstandig in Actions

**Stand op 23 oktober 2026:** De zelfstandige workflow is ingesteld op automatische starts op de openbare GitHub-repository. De eigenaar heeft opdracht gegeven de GitHub-hosted uitvoering te activeren. Controleer dat de eerste run succesvol is en dat de versleutelde cache wordt opgeslagen. De uitvoer blijft afhankelijk van de GitHub-voorwaarden.
De originele privé-repository en haar Actions blijven ongemoeid.

## Hoe gaat een run?

- Marktplaats cron: `17 */2 * * *` (UTC), eens per twee uur.
- Facebook/Apify cron: `47 2,10,18 * * *` (UTC), maximaal 3 geplande
  starts per dag, onder een vast $0,05/$4,65 veiligheidsprofiel.
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

## Beveiliging tegen accountbeperkingen — standaard uit

Op **23 oktober 2026** heeft de eigenaar gevraagd zijn GitHub-account te
beschermen. De commerciële productie-workflow staat daarom **standaard uit**.
Er is geen automatische productiestart bij een push naar `main`. De
aanwezige cron-regels starten geen runner zolang de voorwaarden niet
zijn ingevuld; de jobs worden dan overgeslagen.

GitHub's actuele [Additional Product Terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features)
verbieden bepaalde vormen van commerciële serverless workloads op Actions,
en verbieden op GitHub-hosted runners ook activiteiten die niet bij
ontwikkeling, testen, deployment of publicatie van de repository horen.
GitHub noemt expliciet risico op stopzetting van jobs, beperkingen,
repo-deactivering en in sommige gevallen accountschorsing. **Een
lagere scanfrequentie, vermomming van verkeer of een publiek repo
verleent geen toestemming en is geen bescherming tegen deze voorwaarden.**

Pas na schriftelijke GitHub-bevestiging voor **dit concrete gebruik**:
stel onder [Actions Variables](https://github.com/ali00978/aet-hunter-public/settings/variables/actions)
`AET_GITHUB_TERMS_APPROVED=true` en `AET_PUBLIC_HUNTER_ENABLED=true`
in. De runner controleert **beide** waarden; de bestaande cron draait
anders niet. Als een waarde ontbreekt, faalt de autorisatie.

De gepubliceerde code, AES-256-GCM-cache, AET Pro-handshake en
offline tests blijven intact. Developmenttests kunnen gewoon draaien.
De laatste geslaagde live integratietest controleerde 37 zoekpagina's en
85 advertentiedetails zonder fout. Dat is **geen productietoestemming**.

**Direct stoppen:** als een productiejob al bezig is, open
[Actions](https://github.com/ali00978/aet-hunter-public/actions),
kies de betreffende run en klik **Cancel workflow**. Codewijzigingen
of de toestemming-schakerlaar annuleren een reeds lopende job niet.

### Technische limieten na toegestane activering

- Geen concurrerende Marktplaats- en Facebook-jobs.
- De bestaande requestlimiet, vertraging en verplichte 403/429-cooldown
  blijven gelden; geen proxyrotatie, CAPTCHA-omzeiling of blokkadebypass.
- Facebook uitsluitend bij expliciete Apify budgetlimieten; bij fouten
  blijft de reservering tellen.
- Alleen versleutelde scanstatus in GitHub-cache, nooit advertenties,
  foto's, tokens of privégegevens in openbare commits/logs.
- GitHub-cache is niet gegarandeerd blijvend; bij ontbrekende of ongeldige
  cache stoppen geplande scans automatisch zonder opnieuw te bootstrappen.

## Eerste activering en herstel

1. Na toestemming van GitHub en instelling van beide variabelen: open
   [AET Hunter PUBLIC standalone scanners](https://github.com/ali00978/aet-hunter-public/actions/workflows/public-original-hunter.yml)
   en kies `Run workflow` op main, `platform=marktplaats`.
2. Zet `bootstrap_state=true` **uitsluitend voor de allereerste scan**
   als er echt geen eerdere historie of Apify-budget bestaat. Controleer
   bij herstel eerst of er nog een geldige versleutelde cache is; een
   onnodige reset zou je geschiedenis en uitgavenbewaking beschadigen.
3. Controleer in de logs of de scan `synced` meldt en of de
   `actions/cache/save@v4` stap werkelijk de versleutelde cache heeft
   opgeslagen. Een groen resultaat zonder cache-save telt niet als
   duurzame uitvoering.
4. De daaropvolgende geplande Marktplaats-scans herstellen de nieuwste
   encrypted cache. Bij verdwenen/geëvict cache stoppen ze *veilig*:
   nooit automatisch opnieuw scannen of Apify-budget resetten.
   Inspecteer oorzaak en herstel geschiedenis voor een bewuste bootstrap.
5. Facebook vereist daarnaast `APIFY_TOKEN` als Secret en
   `AET_FACEBOOK_ENABLED=true` als Actions Variable. De defaults zijn
   `AET_APIFY_MONTHLY_BUDGET_USD=4.65` en
   `AET_APIFY_MAX_CHARGE_USD=0.05`, beide in USD. Deze waarden staan
   direct in de geplande workflow. Oude optionele GitHub-variabelen
   worden genegeerd om niet per ongeluk het vorige $4-profiel te gebruiken.
   De Python-code begrenst de bedragen onafhankelijk ook zelf.
   De eerste Facebook-run vereist reeds bestaande state uit Marktplaats;
   ontbrekende quota-geschiedenis mag niet worden genegeerd.

## Apify Free: budget- en stabiliteitsstrategie (oktober 2026)

De officiële Apify Free-tier heeft **$5 gebruikstegoed per maand**;
het platform stopt als de inbegrepen credits opgebruikt zijn.
De HydeData Facebook-actor vermeldt een **vanafprijs van $0,30
per 1.000 listings**. Dat is géén garantie dat elke run alleen
$0,00030 per advertentie kost: de Actor kan minimum-/run-/computekosten
of afwijkende prijzen hebben.

De openbare Hunter past daarom strengere grenzen toe:
- Maximaal **80 opgevraagde advertenties per Facebook-scan**, gevolgd
  door filters voor volledige auto's, vraagprijs €350–€3.000, km ≤330.000,
  particuliere verkoper en de leeftijd van advertenties.
- Maximaal **3 geplande Facebook-scans per dag**, **93 starts per
  kalendermaand**, met een harde grens van **$0,05 per start**.
- Maximaal **$4,65 in de opgeslagen maandreserveringen**. Een volledig
  ongebruikt Free-tegoed van $5 houdt theoretisch $0,35 over.
  Eerdere $0,06-reserveringen en ander Apify-gebruik verminderen de
  daadwerkelijk beschikbare ruimte; soms worden dus minder starts gedaan.
- **Vóór elke potentieel betaalde Actor-aanroep** controleert de
  scanner gratis via GET de echte inbegrepen credits en de bestedingen.
  De controle weigert nieuwe starts boven $4,65 werkelijke
  maandbesteding en bewaart minstens $0,30 speelruimte onder zowel
  de inbegrepen credits als de Apify-accountlimiet. Onbekend saldo
  of een API-fout betekent geen nieuw betaalverzoek.
- HTTP 401/402/403/429 zorgt voor 24 uur provider-cooldown.
  Bij tijdelijke fouten stopt de provider minimaal 2 uur.
- Lege en uitsluitend onderdelenbevattende resultaten verwijderen
  de laatst opgeslagen geldige advertenties niet.
- Budget, cooldown, nieuwe resultaten en geschiedenis worden alleen
  versleuteld opgeslagen. Bij verloren/verlopen GitHub-cache
  wordt het budget **nooit automatisch naar nul teruggezet**.

Vier zoekvragen en zestien uitgebreide advertenties kosten volgens
de gepubliceerde tarieven bij volledige oplevering circa $0,049 per
run ($0,003 × 4 + $0,002 × 16 + $0,0003 × 16 + $0,00005).
Extra betaalbare events kunnen een run eerder stoppen op $0,05.
De limiet per run en het maandplafond blijven beide strikt van kracht.
De Hunter start geen Facebook-Actor zonder
`AET_FACEBOOK_ENABLED=true` en de overige productiegates.

Actuele bronnen:
- https://apify.com/pricing
- https://apify.com/hydedata/facebook-marketplace-scraper/pricing
- https://docs.apify.com/account/subscriptions

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
