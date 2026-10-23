# GitHub-only public runner: original Hunter

**Status: prepared, NOT enabled.** The public repository contains the scheduled
workflow, but the entire job is skipped until you explicitly enable it.
No credentials were copied and no commercial live scan was started.

## How it works

- GitHub public Actions runs one job at 17 minutes past every second hour.
- That job clones the **original private** AET CARS AI Hunter repository into
  an ephemeral container, using a fine-grained, private GitHub token.
- It runs the original Marktplaats scanner, historical-state updates, filters,
  report builders and AET Pro sync. Then it runs the original Facebook Apify
  scanner and AET Pro sync. These are sequential rather than concurrent.
- The same private JSON files are committed ONLY back to your original PRIVATE
  repository; not to the public repo, workflow logs, or downloadable artifacts.
- The user-facing AET Pro app is not modified.
- No local phone, tablet, external server, Oracle or Modal is needed.

## GitHub account setup (done privately; never put values in chat)

In https://github.com/ali00978/aet-hunter-public/settings/secrets/actions
create these **repository secrets**:

1. `AET_GITHUB_TOKEN` — a fine-grained token with Contents: Read/write
   only for `ali00978/aet-cars-ai-hunter`. A GitHub App repository token or
   suitable fine-grained PAT is needed to read and update the private state.
2. `AET_PRO_SYNC_URL` — your existing HTTPS AET Pro Hunter sync endpoint.
3. `AET_HUNTER_SYNC_TOKEN` — your existing private sync token.
4. `APIFY_TOKEN` — your existing private Facebook provider API token.

Keep these values private. **Public Actions logs are visible to everyone**, so
this runner deliberately suppresses raw scanner output and errors containing
provider data. The public workflow has read-only permissions in this public
repository. Secrets are not available to fork pull-request workflows.

When all four secrets have been set and access has been verified, create
repository variable `AET_PUBLIC_HUNTER_ENABLED` with value `true` in
https://github.com/ali00978/aet-hunter-public/settings/variables/actions .
Until then scheduled and manual actions are skipped, not failed.

Then inspect the job on GitHub Actions and confirm private scan state,
Marktplaats and Facebook candidates, photo URLs, deduplication, cooldowns,
AET Pro sync and favorites. Disable the two original private scheduled
workflows to prevent duplicate marketplace/provider requests when the private
quota becomes available again.

To pause scans, change variable `AET_PUBLIC_HUNTER_ENABLED=false`.
Existing private data will not be deleted. The original private workflows can
be re-enabled for rollback.

## Limitations and risks

- No external host is required, but public Actions runs are visible to anyone.
- The source code for price/scoring rules already published in the public repo
  is readable to everyone. The original repo and private state remain private.
- Although standard hosted Actions are **free** for public repositories,
  Github's additional product terms restrict use to software development and
  prohibit disproportionate server workloads. Continuous commercial scanning
  can be suspended, including account restrictions. This is not guaranteed
  compliant or indefinite hosting.
- The existing Apify code has a **100-provider-call monthly cap**: 12 scans per
  day do NOT imply 12 new Facebook API calls indefinitely. Keep provider limits.
- GitHub-hosted addresses may be rate-limited by Marktplaats, and retries must
  respect cooldowns and websites' permitted access.
- No actual production run has occurred: until Secrets + Variable are set, this
  is a tested deployment preparation, not a restored scanner.

## Risico op GitHub-uitschakeling zo laag mogelijk houden

De automatisch ingeroosterde commerciële scans staan **uit** totdat de eigenaar
ze bewust activeert; daarmee starten we geen mogelijk strijdig gebruik zonder
een voorafgaande beslissing. Een korte looptijd, weinig verzoeken en het
afschermen van gegevens zijn verstandig, maar zijn **geen omweg** langs
GitHubs voorwaarden: ook licht commercieel productiegebruik kan buiten het
toegestane gebruik van GitHub-hosted Actions vallen.

Controleer zo nodig vooraf bij GitHub Support of de beoogde terugkerende
scanwerkzaamheden zijn toegestaan: https://support.github.com/ .
Bied daarbij de werkelijke werking en frequentie aan; niet verbergen wat de
workflow doet. GitHub kan de definitieve interpretatie bepalen.

Extra technische beveiligingen:
- Ontwikkeltests lopen alleen op de hoofdbranch en vertrouwde pull requests:
  geen onnodige dubbele tests na iedere feature-push of fork.
- De optionele productiejob heeft een maximum van 30 minuten en één
  concurrency-groep; de twee-uursplanning blijft inhoudelijk gelijk.
- De uitvoer gebruikt alleen vooraf ingestelde, versleutelde secrets; nooit
  een token in broncode, commit of publieke Actions-log.
- De originele privé-repository en AET Pro blijven onaangeroerd tot de
  daadwerkelijke synchronisatie veilig is geverifieerd.
- Een ongeldige HTTPS-synchronisatie-URL, niet-goedgekeurde branch, verkeerde
  eigenaar, of ontbrekende sleutel stopt de job vóór uitvoering.
- Zet de variabele AET_PUBLIC_HUNTER_ENABLED op false om onmiddellijk verdere
  uitvoeringen te blokkeren. Reeds gestarte runs kunnen apart gestopt worden.

Als **zo klein mogelijk risico voor het GitHub-account** zwaarder weegt dan
€0-hosting op GitHub, gebruik het bestaande *private GitHub Actions*-project
met voldoende uitvoerbudget, of een eigen self-hosted GitHub Actions runner.
Deze opties omzeilen de voorwaarden voor gratis publieke hosted runners niet.

## Extra activeringsbeveiliging — expliciete GitHub-bevestiging

Een publieke productiejob wordt alleen gestart als BEIDE Actions Variables
`AET_PUBLIC_HUNTER_ENABLED=true` EN `AET_GITHUB_TERMS_APPROVED=true` staan.
De tweede variabele mag pas worden ingesteld na een duidelijke bevestiging van
GitHub dat dit specifieke terugkerende commerciële werk is toegestaan. De
standaardwaarde van beide is niet-ingesteld: de job wordt volledig overgeslagen.

De enige veilige manier om te weten of GitHub deze toepassing toestaat, is
het gebruik open en nauwkeurig voorleggen aan GitHub Support:
https://support.github.com/ . De gebruiker moet zelf die bevestiging
ontvangen; dit project kan GitHub-beleid niet veranderen of omzeilen.

Een groen testresultaat betekent dat de softwaretests werken, niet dat
GitHub commerciële hosting voor deze workflow heeft goedgekeurd.
