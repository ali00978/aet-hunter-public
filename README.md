# AET CARS AI Hunter — openbare, zelfstandige broncode

De openbare `ali00978/aet-hunter-public` repository bevat de zelfstandige
Marktplaats-scanner, Facebook/Apify-verwerking, marktwaardebeoordeling en het
bestaande AET Pro-importcontract.

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

**Apify Free-optimalisatie:** maximaal 80 Facebook-listings per scan,
maximaal 2 geplande starts per dag, en een harde limiet van $0,06
per Actor-start en $4 per kalendermaand. De Actor start alleen met
voldoende geverifieerd Free-tegoed, op expliciete activering;
HTTP-429-cooldowns en geldige oude resultaten blijven beschermd.
De gepubliceerde Actor-prijs is een vanafprijs, geen
gegarandeerde maandkost.

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
