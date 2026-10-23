# AET CARS AI Hunter — openbare, zelfstandige broncode

De openbare `ali00978/aet-hunter-public` repository bevat de zelfstandige
Marktplaats-scanner, Facebook/Apify-verwerking, marktwaardebeoordeling en het
bestaande AET Pro-importcontract.

**AET Pro-overdracht:** de ontvangende Worker moet voortaan binnen een
begrensd JSON-antwoord expliciet `received: true` teruggeven. Onjuiste,
te grote of niet-leesbare antwoorden worden niet als succes geteld.
Openbare Actions-logboeken tonen uitsluitend de bevestiging, zonder
de ongefilterde serverrespons.

**Laatste kwaliteitscontrole:** een reeds bevestigde Apify-run vereist
alleen het uitlezen van het eigen run-ID, niet telkens extra Actor-metadata.
Bij HTTP 429 of wegvallende netwerkverbinding tijdens het opvragen van
de runstatus blijven de betaalreservering, het bekende run-ID en de
versleutelde geschiedenis behouden. Een accountbeperking activeert
een read-only cooldown; geen tweede betaalde POST. Na afloop
herstellen modules hun oorspronkelijke private directorypaden om
lekken tussen scans en offline tests te voorkomen.

**Facebook herstel na een afgebroken providerverbinding:** de betaalde
aanroep gebruikt nu het officieel ondersteunde asynchrone Apify-startpunt
en slaat het door Apify bevestigde run-ID direct versleuteld op.
Daarna volgen alleen gratis GET-statuscontroles en dataset-opvragingen;
geen tweede betaalde POST. Ook een oude onzekere synchrone run
wordt eerst via een begrensd tijdvenster gecontroleerd. De bestaande
maximale $0,05 per run en $4,65 per maand blijven gehandhaafd.
Eerdere advertenties en budgetgeschiedenis blijven beschermd.
Een nog actieve of onduidelijke run wordt als pending gemeld,
niet als succesvolle import.

**Stabiliteit (23 oktober 2026):** vóór iedere geplande Marktplaats- en
Facebook-scan draaien offline regressietests. Bij een onzekere Apify-POST
worden de betaalreservering én een versleuteld markerbestand opgeslagen
vóór de netwerkactie. De volgende scan voert eerst uitsluitend
geauthenticeerde Apify-GET-verzoeken uit om precies één bestaande actor-run
te herstellen. Bij ontbrekende, onduidelijke of nog lopende resultaten
start de Hunter **geen tweede betaalde Actor**. Bij een succesvol
herstel worden bestaande resultaten direct beoordeeld en aan AET Pro
doorgestuurd, zonder extra Apify-start. De scan-ID in AET Pro gebruikt
het stabiele GitHub-runnummer en -poging. Onzekere providerresponsen
blijven zichtbaar als niet-geslaagde scan, niet als fictief succes.
Bestaande budgetlimieten blijven intact.

**Voortgang tijdens Marktplaats-scans:** de scanner bewaart de vertraging
van vijf seconden tussen externe verzoeken, om onnodig veel belasting en
beperkingsrisico te vermijden. GitHub krijgt om de twee minuten een
anonieme melding dat de scan nog bezig is. Bij afronding wordt alleen
een telling van zoek-/detailpagina's en verzoeken gelogd; géén URLs,
verkopersnamen, advertentietekst, tokens of prijslijsten. Daardoor lijkt
een langere, normaal draaiende scan niet meer vastgelopen. De eigenlijke
selectie, cooldowns, requestlimieten en AET Pro-sync zijn niet veranderd.

**Plan A:** GitHub Actions in deze **openbare** repository (geen privé-repository).
De workflow `.github/workflows/public-original-hunter.yml` heeft
een twee-uursplanning voor Marktplaats en een afzonderlijke Facebook-planning.
De eigenaar heeft daarna gevraagd zijn account niet onnodig bloot te
stellen aan accountbeperkingen. Daarom is de terugkerende commerciële
scan standaard gepauzeerd totdat expliciete toestemming van GitHub
voor deze workload is bevestigd. Ontwikkeling en tests blijven actief;
een mislukte of overgeslagen run is geen productiebewijs.

**GitHub-gebruiksvoorwaarden:** GitHub-hosted Actions mag volgens de
Additional Product Terms niet voor willekeurige commerciële workloads worden
gebruikt. Er is **geen GitHub-goedkeuring aangetoond** voor deze scans.
De gebruiker loopt het risico dat GitHub de Actions, repository of account
beperkt. Bij problemen: Plan B, een afzonderlijke host.

**Privé-informatie:** de Actions-code gebruikt AES-256-GCM om historie, advertentie-
en providergegevens vóór GitHub-caching te versleutelen. De encryptiesleutel
wordt afgeleid van de bestaande geheime AET Pro-token en wordt nooit opgeslagen.
GitHub-caches zijn toegankelijk voor anderen met leestoegang/forks; daarom
mogen onversleutelde advertenties, identificeerbare data of credentials er
**nooit** in. Het automatisch hergebruiken van de cache is niet gegarandeerd;
bij een ontbrekende cache stoppen geplande runs tot een bewuste herstelactie.

**Facebook handelskwaliteit (versie oktober 2026):** De prijs alleen bewijst
geen koopkans. De scanner rolt vier roulerende modelgerichte zoekgroepen
uit (Corsa, Clio, Aygo, Polo en twaalf andere populaire modellen) en gebruikt
een kleine, betaalplafond-begrensde hoeveelheid extra voertuigdetails.
Hij verwerkt kilometerstanden in **mijlen correct naar kilometers** en
geeft particuliere verkopers, goede bouwjaren, aanwezige foto's en lage km
voorrang. Oudere auto's met een hoge vraagprijs en technische risico's
krijgen een lagere score. Andere dan de zestien toegestane merken worden
niet aan AET Pro aanbevolen. Ontbrekende jaar/km/verkoopinformatie blijven
zichtbaar als onbevestigd; geen enkel Facebook-resultaat wordt als
geverifieerde winst van EUR 500 gepresenteerd.

**Kostenafweging:** het nieuwe maximum van $0,05 per geplande
Facebook-Actor-run is expliciet begrensd en de oude $0,06-tests
blijven in de historische maandreservering meetellen. Met vier zoekvragen van maximaal vier advertenties met
extra details worden minder auto's per scan verwerkt dan de eerdere
80 brede, ongedetailleerde resultaten. Dat is bewust om kwaliteit te
verbeteren binnen de gratis credits.

**Apify Free-optimalisatie:** maximaal 80 Facebook-listings per scan,
**3 geplande starts per dag** (02:47, 10:47, 18:47 UTC), maximaal
**93 starts per kalendermaand**, en een harde limiet van **$0,05 per
Actor-start en $4,65 per kalendermaand**. Er blijft theoretisch
$0,35 over van $5 gratis tegoed wanneer geen andere Apify-kosten worden
gemaakt. De bestaande versleutelde budgethistorie wordt behouden.
De actuele inbegrepen credits worden vóór iedere betaalde aanvraag
geverifieerd; de controle bewaakt daarnaast $0,30 vrije ruimte.
Dit is een kostenplafond, geen garantie dat alle scans of voertuigdetails
beschikbaar zijn. De Actor start alleen met
voldoende geverifieerd Free-tegoed, op expliciete activering;
HTTP-429-cooldowns en geldige oude resultaten blijven beschermd.
De gepubliceerde Actor-prijs is een vanafprijs, geen
gegarandeerde maandkost.

**Eenmalige hersteluitzondering (23 oktober 2026):** één poging mag
alleen de eerdere generieke twee-uurscooldown na HTTP 400 negeren.
Een 401/403/429-accountbeperking blijft van kracht. De uitzondering wordt
vóór het nieuwe Actor-verzoek versleuteld vastgelegd en is maar één keer
bruikbaar, met het bestaande limiet van $0,06.

**Eenmalige ontwikkeltest:** `.github/workflows/facebook-one-time-integration.yml`
is geen terugkerende scraper. Alleen de expliciete test-commit kan één
Facebook Actor-start van maximaal $0,06 proberen met opgeslagen,
versleutelde scanstatus, en daarna AET Pro synchroniseren als er geldige
auto's zijn. Er zijn geen handmatige/periodieke triggers voor deze test;
`GITHUB_RUN_ATTEMPT=1` beschermt tegen onbedoelde reruns. Bij een fout
blijft een gereserveerde Actor-aanroep meetellen en wordt niet opnieuw
gestart. Dit is alleen voor integratieverificatie.

**Herstel zonder nieuwe Actor-aanroep:** na de eenmalige Facebook-poging
die eindigde in `request_failed` kan de afzonderlijke workflow
`facebook-recover-existing.yml` uitsluitend de **bestaande** Actor-run
uit het korte tijdvenster van 23 oktober 2026 tussen 16:20 en 16:26 UTC
via geauthenticeerde **GET** opzoeken. Als die run compleet is,
kan hij reeds betaalde datasetrecords via het normale filter naar AET Pro
synchroniseren. Er wordt **geen** Apify Actor gestart en er is geen
tweede commerciële scan. Kandidaten en een herstelmarker blijven versleuteld.

**Kwaliteitscontrole bestaande Facebook-resultaten:** 
`.github/workflows/facebook-quality-replay.yml` leest uitsluitend reeds
opgeslagen advertenties uit de versleutelde historie, herordent ze op de
nieuwe handelscore en kan eenmalig maximaal 30 auto's naar AET Pro
synchroniseren. Dit start geen Apify-actor en voert geen Facebook-verzoeken
uit. De code houdt de originele scantijd aan en weigert een AET Pro-import
wanneer de opgeslagen gegevens ouder zijn dan vier uur.

**Plan B:** Modal met privé-volume staat voorbereid in
`ops/modal/public_hunter_modal.py`. Een externe host is niet verplicht voor
de developmenttests. De oude privé-repository, privé-Actions en haar opgeslagen
data worden niet gewijzigd of gebruikt door de zelfstandige GitHub-workflow.

## Startvoorwaarden en testen

Zie [GitHub Plan A](PUBLIC_GITHUB_HUNTER_SETUP.md) voor de voorwaarden,
de activeringsschakelaars en een veilige eerste scan.
Voor een offline regressietest: `python run_offline_tests.py`
(met package `cryptography` voor de cache-encryptietests).
De eenmalige Marktplaats live softwaretest blijft los van de productiejob.

Een eerdere volledige openbare Marktplaats-integratietest (23 oktober 2026)
vond 77 kandidaten, testte 85 detailpagina's en meldde nul zoek/detailfouten.
Deze test bewijst niet dat de geplande commerciële workflow nu geactiveerd is.
