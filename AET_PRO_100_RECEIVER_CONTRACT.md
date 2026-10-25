# AET Pro Hunter synchronisatie — veilige uitbreiding van 30 naar 100

## Implementatiestatus (25 oktober 2026)

- De **publieke Hunter-zender** kan maximaal 100 bestaande, al verzamelde
  Marktplaats-kandidaten voorbereiden. Er worden daarvoor geen extra
  Marktplaats-verzoeken uitgevoerd.
- De huidige AET Pro Worker adverteert nog niet aantoonbaar die capaciteit;
  de zender valt automatisch terug op maximaal 30.
- **Niet claimen dat de bestaande AET Pro Worker of UI is aangepast**: de
  Worker-broncode staat niet in de gekoppelde publieke Hunter GitHub-repo.
- Facebook blijft maximaal 30 kandidaat-ID's per verzending gebruiken.
- Het verhogen van capaciteit verandert **niet** de frequentie of maximaal
  aantal werkelijk gevonden aanbiedingen per scan.

## Vereisten voor wijziging aan de AET Pro Worker

1. Bewaar de bestaande POST `/api/integrations/hunter/sync` met authenticatie
   en schema `aet-pro.hunter.sync.v1`.
2. Accepteer maximaal **100 Marktplaats**-records of maximaal **30 Facebook**-
   records uit de bron van `scan.platform`. Weiger niet-overeenkomende
   `scan.candidate_ids`, dubbele ID's, aantallen boven de limiet en ongeldige
   `batch.candidates` (HTTP 4xx, zonder succesbevestiging).
3. Voer een **atomaire, duurzame database-transactie** uit: upsert per stabiel
   `candidate.id`, scanlog per `scan.id`, en associaties tussen scan en
   kandidaat. Herhaalde identieke `scan.id` mag geen dubbele records,
   favorieten of meldingen veroorzaken. Een herhaalde scan met dezelfde ID
   maar andere inhoud moet worden geweigerd of veilig idempotent afgehandeld.
4. Scheid **geautomatiseerde onderzoeksgegevens** van **handmatige
   gebruikersvelden** zoals favorieten, interne notities, inspectiestatus,
   kostencalculatie en afleverdossier. Updates mogen geen favorieten wissen.
5. Antwoord pas **na succesvolle commit**:
   ```json
   {"received":true,"scan_id":"marktplaats:123456:1","accepted_candidate_count":100}
   ```
   `accepted_candidate_count` is het aantal **geaccepteerde records in dit
   verzoek** (ook bij idempotente herhaling), niet het totale database-aantal.
   Doe nooit alsof alle 100 zijn verwerkt wanneer slechts 30 zijn opgeslagen.
6. Publiceer na volledige implementatie een actuele GET `/api/health`
   met `status="ok"`, `database="connected"` en
   `hunter_max_marktplaats_candidates=100`. Zet dit veld **pas** aan nadat
   de API-transactie en respons getest zijn. De publieke Hunter schakelt dan
   vanzelf om; geen onveilige geforceerde 100-switch.
7. Toets 0, 1, 30, 31 en 100 records, 101 weigeren; 100 records herhalen;
   gedeeltelijke databasefout (rollback); ongeldige token; verschillende
   bron- en scan-ID's; gelijktijdige scans; behoud van bestaande favorieten.

## Mobiele Hunter-UI en favorieten

- Gebruik de stabiele sleutel `candidate.id` (inclusief `marktplaats:` /
  `facebook:`-prefix), nooit lijstpositie of scan-ID.
- Render een kaart per auto met eerste geldige `photo_urls`-link of
  placeholder, titel, kilometerstand, bouwjaar, regio, vraagprijs/type,
  inspectielabel, bron en datum laatste controle.
- Onderscheid `asking_price_eur` van `search_card_hint_eur` en
  `minimum_bid_eur`. Een aanwijzing/bod is **geen bewezen vraagprijs**.
- Exporteer `confirmed_profit_eur=null` als onbekend; bereken geen
  gegarandeerde winst zonder onderbouwde marktwaarde, herstelkosten en
  inspectie.
- Toon **nieuwe / gewijzigde / reeds bekende** advertenties apart, bewaar
  favorieten na syncs en laat dubbele meldingen achterwege.
- De foto-overlay voor eigen voorraadfotografie en documentmodules horen
  bij de app-UI en staan buiten deze Hunter-repository.

## Onderzoek bij 30 nieuw per scan

De teller `nieuw` in publieke CI meet **niet eerder succesvol afgeleverde
bron-ID's**; het is geen bewijs van 30 nieuwe Marktplaats-advertenties.
De werkelijke scanresultaten en leverhistorie zitten versleuteld in de
cache. Vergelijk versleuteld opgeslagen opeenvolgende bron-ID's en
`hunter_delivery_checkpoint.json` **zonder privédata te loggen**.
Een verlopen of ontbrekende cache moet productiescans veilig stoppen;
reset de historie niet automatisch. De checkpunten worden pas na
een aantoonbare AET Pro-bevestiging bijgewerkt.
