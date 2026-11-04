# Place catalog

Derived from [GeoNames](https://www.geonames.org/), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The compressed catalog contains 171,771 populated places plus countries and first-level administrative divisions. It was downloaded on 2026-11-04. Source URLs and SHA-256 hashes are embedded in the file.

Rebuild with `scripts/build_place_catalog.py --source <directory>` after downloading `cities1000.zip`, `admin1CodesASCII.txt`, and `countryInfo.txt` from the [official GeoNames download directory](https://download.geonames.org/export/dump/). Country names/codes use the pinned pycountry package; Kosovo is included with the XK identifier used by GeoNames and hiring feeds.

JobDrop stores stable place IDs for search. The catalog is a place-name reference, not evidence that an employer has a job in a particular place. Ambiguous or absent job locations are excluded from a selected geographic filter until the official posting supplies enough information.
