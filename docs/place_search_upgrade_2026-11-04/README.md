# Geographic location search — November 4, 2026

The previous search treated incomplete locations as matches for any place. That
behavior is removed. A job needs an identified city, state/region, or country
belonging to the selected place. Missing country data does not make a known
foreign city match New York; country-only data cannot establish a state or city.
Jobs without a resolved place remain available when no geographic filter is set.

## Search behavior

- The picker offers actual cities, states/regions, and countries, with distinct
  labels and stable place IDs. `NY` means New York State; `NYC` means New York
  City. Buffalo belongs to the state, not the city. NYC includes its boroughs.
- State/country abbreviations, accented names, and selected common aliases are
  resolved against an offline GeoNames catalog. Ambiguous input requires a
  qualified place or a picker selection. It is never a substring search.
- Multiple advertised offices are preserved. A matching office appears first.
  A country-qualified remote role matches that country; generic remote does not
  establish a city/state. A separate Remote option filters remote roles.
- SQL filters derived `job_places` memberships before pagination. Scans and
  location repairs replace memberships in the same transaction as job updates.
  Lists, details, saved searches, and alerts use the same entities.
- The catalog contains 175,886 geographic entities, including 171,771 cities.
  See [catalog provenance and license](../../app/resources/geography/README.md).
  Small settlements absent from the catalog retain source labels and any known
  region/country; this version does not offer geographic radius searches.

## Source review and prevention

The review rechecked 11,976 stored records, focusing on missing/generic
locations and examples from the reported searches. It used official job documents,
public ATS APIs, labelled office lists, and JobPosting fields. Company footer
addresses and arbitrary description keywords were not location evidence.

Explicit location fields were found for 5,343 reviewed records. The final review
also includes 1,066 records on blocked sources, 78 request failures, 182 missing
pages, 216 records on disabled feeds, and 5,091 records without explicit fields.
Those results do not establish a new physical location. Access challenges were
not bypassed. Aggregate results and public source URLs are in the adjacent
review artifacts; full private database backups are kept only on the server.

Provider fixes read advertised Avature/Siemens office lists and embedded Ashby locations,
recover Workday primary countries and secondary offices, and distinguish the US
state Illinois from Israel in `Remote - IL`. Texas Instruments now uses its
public Oracle API: 903 advertised roles were read, including explicit locations
for all 355 stored TI records reviewed, including the initial 132 missing/partial examples.

Five overly broad HTML feeds had imported navigation/product/article URLs.
GitHub now uses its official iCIMS/Jibe feed, Klaviyo and Lob their current
Greenhouse boards, and Kinsta/Sakari only individual career paths. The reviewed
313 non-job rows are closed with private backups and guards; no rows or saved
job identities are deleted. Sakari currently advertises no individual postings.

A follow-up rechecked 194 changed Siemens documents and recovered explicit
locations from all 194. It also restored 336 lists that exactly matched reviewed
source paragraphs and 159 additional Deloitte lists with explicit city, state,
and country for every office. Paragraph splitting requires a complete sequence
of US offices, valid state entities, and no residual prose; arbitrary description
text is rejected. These recovery paths address stored lists that had been
flattened into a single value by older scans.

## Validation

All 295 Python tests pass, including screenshot regressions, entity hierarchy,
country/state code collisions, source-page extraction, strict exclusion of
unknowns, persisted-index replacement, pagination, and saved-search behavior.
Browser checks cover city/state distinction, keyboard selection, invalid-input
validation, the reported NY false positives, and JavaScript errors. Data repairs
check identity/status/current location, back up full rows, and verify that dates,
job IDs, descriptions, and other unrelated columns remain unchanged.

Live deployment applied 4,929 location repairs, closed 313 non-job pages, and
imported 256 genuine postings from corrected feeds. The final consistency audit
checked all 34,116 open jobs indexed for NY, with no mismatches. All 3,119 results
for engineer / NY / 2 years had a New York office, shown first. These are checks
of stored source geography and search behavior, not fresh visits to every NY
job page. Details are in `deployment.json` and the browser check artifacts. Source availability and job inventories continue to change;
this audit does not assert that every employer page can always be retrieved.
