# Where the area list comes from

`backend/home/area_catalog.py` holds the valid neighbourhoods of the six launch metros, and `backend/home/city_names.py` the Persian city names and the city repairs. Checked 2026-11-04.

The official list decides that a place exists. Real posts on production decide which places get a row: fine divisions nobody writes are left out, or kept only as an alias of the area around them. Rows from before this list keep their canonical name, so links and alerts still hold.

## Divisions

| Metro | Division | Source |
|---|---|---|
| Toronto | 158 city neighbourhoods | https://www.toronto.ca/city-government/data-research-maps/neighbourhoods-communities/neighbourhood-profiles/ |
| Toronto | The 158 names, listed | https://en.wikipedia.org/wiki/List_of_city-designated_neighbourhoods_in_Toronto |
| Montreal | 19 boroughs | https://montreal.ca/arrondissements |
| Montreal | The borough list as data | https://www.donneesquebec.ca/recherche/dataset/vmtl-arros-liste |
| Montreal | Boroughs and their quartiers | https://en.wikipedia.org/wiki/Boroughs_of_Montreal |
| Vancouver | 22 local planning areas | https://vancouver.ca/news-calendar/areas-of-the-city.aspx |
| Vancouver | The 22 names, listed | https://en.wikipedia.org/wiki/List_of_neighbourhoods_in_Vancouver |
| Berlin | 12 Bezirke and their Ortsteile | https://de.wikipedia.org/wiki/Liste_der_Bezirke_und_Ortsteile_Berlins |
| Hamburg | 7 Bezirke and 104 Stadtteile | https://de.wikipedia.org/wiki/Liste_der_Bezirke_und_Stadtteile_Hamburgs |
| Hamburg | The seven Bezirke, from the city | https://www.hamburg.de/politik-und-verwaltung/behoerden/finanzbehoerde/themen/bezirke-und-bezirksverwaltung |
| Cologne | 9 Stadtbezirke and their Stadtteile | https://de.wikipedia.org/wiki/Liste_der_Stadtbezirke_und_Stadtteile_K%C3%B6lns |

vancouver.ca refused the automated check (HTTP 403), so the Vancouver names were read from the Wikipedia list, which cites that page. Cologne's own overview page answered HTTP 410 when checked, so Cologne rests on the Wikipedia list.

## Points

Every point comes from OpenStreetMap Nominatim, https://nominatim.openstreetmap.org/ (data © OpenStreetMap contributors, ODbL), one request a second, and is one pin per area. Each result's name was read and checked against the metro. One was set by hand: Mount Royal, where Nominatim returns the mountain and the row means the town.

## Persian names and other spellings

Persian names are the transliterations Persian speakers use, kept from the first seed where one existed. Corrected: Surrey is now «ساری», as Persian Wikipedia writes it. Aliases come from what posts on production really say, read on 2026-11-04.

## Corrected from the first seed

- Places that were aliases of a neighbour now have their own row: NDG (was under Côte-des-Neiges), Moabit, Wilmersdorf, Zehlendorf, Schöneberg, Prenzlauer Berg, Hellersdorf, Köpenick, Tegel, Deutz, Sülz and others.
- Berlin's Treptow pin sat on Köpenick and now sits in Alt-Treptow.
- Every point was replaced by the Nominatim point; those that moved more than 5 km are Vaughan, Brampton, Laval and Treptow.

## Running the sync

Back up first, then apply, on the production database. Writes area rows and listing fields only, and deletes nothing.

```bash
pg_dump "$DB" -t home_homearea -t home_homelisting -Fc -f areas-before.dump
python manage.py home_areas_sync --dry-run --report dry-run.md
python manage.py home_areas_sync --apply --report applied.md
```

`--dry-run` runs in a read-only transaction. A second `--apply` changes nothing.
