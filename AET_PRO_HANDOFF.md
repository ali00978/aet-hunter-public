# AET Pro – overdracht na stabiliteitsronde Hunter

Stand: 23 oktober 2026. Deze notitie bevat **geen** toegangstokens, advertenties of persoons-/voertuiggegevens.

## Status en bewezen werking

- Marktplaats: laatste geverifieerde geplande run op 23 oktober slaagde; 38 zoekpagina's, 79 advertentiedetails, 30 kandidaten bevestigd door AET Pro; versleutelde staat opgeslagen.
- Facebook: eerdere geplande run gaf `request_failed`; herstelpoging was succesvol met 14 kandidaat-ID's bevestigd door AET Pro en versleutelde cache opgeslagen.
- De nieuwe **asynchrone** Apify-start, idempotente run-ID's, fijnere GET-herstelstappen en correctie van last_checked versus first_seen zijn offline getest. Succes op een volgende geplande productie-scan is **nog niet** bevestigd.
- Offline regressies en publieke codeguard draaien vóór elke productie-scan; een testresultaat is geen vervanging voor live productiebewijs.

## Huidige synchronisatiecontracten

- Alleen het HTTPS-pad `/api/integrations/hunter/sync` van de bestaande AET Pro Worker; credential blijft uitsluitend in Secrets.
- Envelope: `schema=aet-pro.hunter.sync.v1`, met `scan` en `batch`.
- Batch: `schema=aet-pro.hunter.v2`, inclusief `revision`, `generated_at`, `platform_counts`, `platform_status`, `candidates`.
- Bronidentiteit: `marktplaats:<listing_id>` of `facebook:<listing_id>`; dezelfde bron-ID is geschikt om favorieten over opvolgende scans te bewaren.
- Scanidentiteit: `<source>:<github_run_id>:1`; herhaalde pogingen van dezelfde GitHub-run dienen dezelfde scan-ID te gebruiken. AET Pro moet het veld `scan.id` idempotent verwerken.
- Eén batch bevat maximaal 30 Marktplaats- en 30 Facebook-kandidaten; records kunnen eerder opgeslagen advertenties bevatten en zijn niet noodzakelijk allemaal nieuw.
- Foto's: `photo_urls` (lijst); gebruik een placeholder voor lege, gebroken of verlopen URL's.
- Handmatige status: `needs_inspection=true`, `confirmed_profit_eur=null` en `availability` niet als gegarandeerd 'beschikbaar' presenteren.
- `asking_price_eur` geldt uitsluitend als advertentie-prijs daadwerkelijk bekend is; `search_card_hint_eur` is een **onbevestigde** prijsindicatie, ook op Facebook.
- `median_comparable_asking_price_eur` en `indicative_resale_eur` zijn geen bewezen verkoopwaarden. Als data ontbreekt, houd waarden leeg in plaats van een winstbedrag te verzinnen.
- `content_hash` en `revision` ondersteunen gerichte updates. De bron kan URL-slugs wijzigen terwijl de ID gelijk blijft.

## Scanplanning en kosten

- Marktplaats: iedere 2 uur, met bestaande vertraging/cooldown bij beperkingen.
- Facebook: 3× per dag; maximaal USD 0,05 voor een Actor-start, 93 starts en USD 4,65 gereserveerd per kalendermaand, met daadwerkelijke saldo-check.
- Eerste vinddatum en laatste gecontroleerde datum zijn verschillend. Een **geslaagde** providerinspectie kan de controledatum vernieuwen; een providerfout niet.
- Onzekere Apify-run: versleutelde pending-ID bewaren; uitsluitend GET-herstel; geen tweede betaalde start zolang afronding onbekend is.
- GitHub-cache kan verlopen of ontbreken. Geplande productiescans stoppen dan veilig; nooit automatisch state of maandbudget resetten.

## AET Pro volgende fase (prioriteitsvolgorde)

1. **Hunter-inbox:** mobile-first overzicht van beide bronnen, filters op prijs, merk, bouwjaar, km, locatie en minimumwinst; duidelijke badges 'onbevestigde prijs', 'privéverkoper nog niet bevestigd' en 'historisch'.
2. **Foto's en favorieten:** thumbnails op kandidatenkaarten, placeholder bij fout; favorieten koppelen aan `source + listing_id` en bewaren onafhankelijk van scans.
3. **Onderbouwde waardebepaling:** vraagprijzen vergelijken met hetzelfde model, bouwjaar, uitvoering, kilometrage, regio en staat. Altijd bronvermelding, aantallen en onzekerheid laten zien; technische reparatiereserve meenemen.
4. **RDW-kentekencheck:** voertuigdata, kilometerstandsinformatie waar beschikbaar en opties alleen indien daadwerkelijk geleverd; ontbrekende uitrusting niet raden.
5. **Advertentiemodus:** foto-checklist, tekstvoorstel, voertuigopties en kwaliteitscontrole vóór publiceren op de eigen website.
6. **Mobiele meldingen:** alleen nieuwe of duidelijk verbeterde koopkansen; geen dubbele meldingen bij dezelfde scan-ID of advertentie.
7. **Dashboard-betrouwbaarheid:** laatste succesvolle scan *per bron*, vorige fouten, versleutelde state-herstelstatus, handmatige controlelijst en scanverval zichtbaar.

## Acceptatie voor de app-uitbreiding

- Een geïmporteerde auto blijft bestaan en behoudt favorieten na opvolgende syncs.
- Dezelfde `scan.id` tweemaal aanbieden leidt niet tot dubbele meldingen of records.
- Geen verkoopclaim van minstens EUR 500 winst zonder de vereiste reële vergelijkingen, kosten en inspectie.
- Onbekende kilometerstand, verkopersstatus, optielijst, locatie en foto's worden als onbekend behandeld.
- Tests blijven offline zonder Marktplaats-/Facebook-verzoeken of Apify-starts.
- Controleer na deze codewijziging minimaal één nieuwe geplande Marktplaats-run én één geplande Facebook-run vóór claims over blijvende volledig automatische werking.

**Buiten scope van deze ronde:** wijzigingen in de eigen AET Pro-codebase, betaalde proefruns en het omzeilen van provider- of GitHub-accountbeperkingen.
