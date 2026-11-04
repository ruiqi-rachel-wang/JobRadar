"""Offline place resolution and indexed city/region/country job matching.

Place IDs refer to GeoNames cities/admin divisions and ISO countries. Missing
or ambiguous geography never grants membership in an unrelated place.
"""
import bisect
import gzip
import json
import re
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pycountry

CATALOG_FILE = Path(__file__).resolve().parents[1] / 'resources/geography/places.json.gz'


def folded(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.sub(r'[^\w]+', ' ', ''.join(c for c in value if not unicodedata.combining(c)), flags=re.UNICODE).split())


@dataclass(frozen=True, slots=True)
class Place:
    id: str
    name: str
    country: str
    region: str
    population: int
    latitude: float | None
    longitude: float | None

    @property
    def kind(self):
        return self.id.split(':', 1)[0]


class PlaceCatalog:
    def __init__(self):
        with gzip.open(CATALOG_FILE, 'rt') as stream:
            data = json.load(stream)
        self.places = {}
        self.names = defaultdict(set)
        self.search_names = defaultdict(set)
        self.country_names = {}
        self.region_names = defaultdict(set)
        for row in data['places']:
            place = Place(*row[:7])
            self.places[place.id] = place
            for alias in row[7]:
                key = folded(alias)
                if key:
                    self.names[key].add(place.id)
                    if place.kind != 'city' or alias in row[7][:2]:
                        self.search_names[key].add(place.id)
                    if place.kind == 'country':
                        self.country_names[key] = place.country
                    elif place.kind == 'region':
                        self.region_names[key].add(place.id)
        self._alias('country:US', 'US', 'USA', 'U.S.', 'U.S.A.', 'America')
        self._alias('country:GB', 'UK', 'United Kingdom', 'Great Britain')
        self._alias('country:CZ', 'Czechia', 'Czech Republic')
        self._alias('country:KR', 'South Korea', 'Korea, Republic of')
        self._alias('country:AE', 'UAE')
        self._alias('country:GE', 'Georgia (country)')
        self._alias('city:5128581', 'New York', 'New York City', 'NYC')
        self._alias('city:5391959', 'San Francisco', 'SF')
        self._alias('city:5368361', 'Los Angeles', 'LA')
        self._alias('city:1277333', 'Bangalore', 'Bengaluru')
        self._alias('city:1275339', 'Bombay', 'Mumbai')
        self._alias('city:1809858', 'Peking', 'Beijing')
        self._alias('region:DE.02', 'Bayern', 'Bavaria')
        self._alias('region:DE.16', 'Berlin', 'State of Berlin')
        self._alias('region:DE.05', 'Hessen', 'Hesse')
        self._alias('region:DE.06', 'Niedersachsen', 'Lower Saxony')
        self._alias('region:DE.07', 'Nordrhein-Westfalen', 'North Rhine-Westphalia')
        self._alias('region:DE.13', 'Sachsen', 'Saxony')
        self._alias('region:DE.15', 'Thüringen', 'Thuringia')
        self.region_codes = {}
        for place in list(self.places.values()):
            if place.kind == 'region' and place.country == 'US':
                code = place.region.split('.', 1)[1]
                self.region_codes[code] = place.id
                self._alias(place.id, code, place.name+' State')
        canadian = {'AB':'01','BC':'02','MB':'03','NB':'04','NL':'05','NS':'07',
                    'ON':'08','PE':'09','QC':'10','SK':'11','YT':'12','NT':'13','NU':'14'}
        for code, number in canadian.items():
            place_id = 'region:CA.'+number
            self.region_codes[code] = place_id
            self._alias(place_id, code)
        for place in list(self.places.values()):
            if place.kind == 'region':
                self._alias(place.id, self.public(place)['label'])
        self.aliases = sorted(self.search_names)
        self.city_parents = {city:'city:5128581' for city in (
            'city:5110266','city:5110302','city:5125771','city:5133273','city:5139568')}

    def _alias(self, place_id, *aliases):
        place = self.places.get(place_id)
        if not place:
            return
        for alias in aliases:
            key = folded(alias)
            self.names[key].add(place_id)
            self.search_names[key].add(place_id)
            if place.kind == 'country':
                self.country_names[key] = place.country
            elif place.kind == 'region':
                self.region_names[key].add(place_id)

    def public(self, place):
        country = self.places.get('country:'+place.country)
        region = self.places.get('region:'+place.region)
        country_name = country.name if country else place.country
        region_name = region.name if region else ''
        short_region = place.region.split('.', 1)[-1] if place.country == 'US' else region_name
        parts = [place.name]
        if place.kind == 'city' and short_region and folded(short_region) != folded(place.name):
            parts.append(short_region)
        if place.kind != 'country':
            parts.append(country_name)
        return {'id':place.id, 'name':place.name, 'label':', '.join(parts),
                'kind':place.kind, 'country_iso':place.country,
                'region_id':'region:'+place.region if place.region else None,
                'latitude':place.latitude, 'longitude':place.longitude}

    def suggestions(self, query, limit=10):
        key = folded(query)
        if not key:
            defaults = ['region:US.NY','city:5128581','country:US','country:CA','country:GB']
            return [self.public(self.places[p]) for p in defaults]+[
                {'id':'workplace:remote','name':'Remote','label':'Remote jobs','kind':'workplace'}]
        ids = set(self.search_names.get(key, ()))
        start = bisect.bisect_left(self.aliases, key)
        for alias in self.aliases[start:start+2500]:
            if not alias.startswith(key):
                break
            ids.update(self.search_names[alias])
        # Qualified city inputs ("Cambridge, MA" / "Paris, France") are
        # resolved by the same catalog and cannot match a title fragment.
        exact = self.resolve(query)
        if exact:
            ids.add(exact.id)
        def rank(place_id):
            p = self.places[place_id]
            exact_name = place_id in self.search_names.get(key, ())
            return (0 if exact and place_id == exact.id else 1,
                    0 if exact_name else 1, {'country':0,'region':1,'city':2}[p.kind],
                    -p.population, p.name, p.id)
        result = [self.public(self.places[p]) for p in sorted(ids, key=rank)[:limit]]
        if key.startswith('remote'):
            result.insert(0, {'id':'workplace:remote','name':'Remote','label':'Remote jobs', 'kind':'workplace'})
        return result[:limit]

    def resolve(self, query):
        if ':' in query:
            kind,identifier = query.split(':',1)
            if kind.lower() in {'region','country','city'}:
                query = kind.lower()+':'+(identifier.upper() if kind.lower() != 'city' else identifier)
        if query in self.places:
            return self.places[query]
        key = folded(query)
        exact = [self.places[p] for p in self.names.get(key, ())]
        # Short US state abbreviations and full state names have a stable
        # interpretation; the picker separately exposes identically named cities.
        states = [p for p in exact if p.kind == 'region' and p.country == 'US']
        if states:
            return states[0]
        countries = [p for p in exact if p.kind == 'country']
        if countries:
            return countries[0]
        regions = [p for p in exact if p.kind == 'region']
        if len(regions) == 1:
            return regions[0]
        cities = [p for p in exact if p.kind == 'city']
        if len(cities) == 1:
            return cities[0]
        ranked = sorted(cities,key=lambda p:p.population,reverse=True)
        if len(ranked) > 1 and ranked[0].population > 10 * max(1,ranked[1].population):
            return ranked[0]
        qualified = resolve_entry(query)
        if qualified.get('city_id'):
            return self.places[qualified['city_id']]
        if qualified.get('region_id'):
            return self.places[qualified['region_id']]
        return None


_catalog = None
_catalog_lock = threading.Lock()


def catalog():
    global _catalog
    if _catalog is None:
        with _catalog_lock:
            if _catalog is None:
                _catalog = PlaceCatalog()
    return _catalog


_GENERIC = {'remote','hybrid','onsite','on site','home','office','headquarters','global',
            'worldwide','anywhere','united','multiple locations','various locations','not listed',
            'not specified','unknown','country not listed','new','city','center','centre','main'}


@lru_cache(maxsize=65536)
def resolve_entry(raw, country_hint=None):
    """Resolve one advertised place; prose and ambiguous cities stay unresolved."""
    raw = str(raw or '').strip()
    key = folded(raw)
    if (not key or key in _GENERIC or len(raw) > 260 or len(key.split()) > 35
            or re.search(r'\b(?:qualifications|responsibilities|understanding|combined with|requirements)\b',key)):
        return {}
    cat = catalog()
    if raw.upper() == str(country_hint or '').upper() and 'country:'+raw.upper() in cat.places:
        return {'country_iso':raw.upper()}
    words = key.split()
    # Only explicit names/codes at address boundaries establish a country.
    country = None
    for width in range(min(7,len(words)),0,-1):
        for text in (' '.join(words[-width:]),' '.join(words[:width])):
            candidate = cat.country_names.get(text)
            if candidate and (width > 1 or len(text) > 3 or text in {'us','usa','uk','uae'}):
                country = candidate
                break
        if country:
            break
    code3 = re.match(r'^([A-Z]{3})(?:\s*[-,>–—]|\s+)',raw)
    tail3 = re.search(r'[,\s]([A-Z]{3})$',raw)
    for match in (code3,tail3):
        if match:
            iso = pycountry.countries.get(alpha_3=match.group(1))
            if iso:
                country = iso.alpha_2
    if key == 'georgia' and country_hint == 'US':
        country = 'US'
    # A leading ambiguous ISO2 code is a country when its advertised city
    # corroborates that country (IL–Tel Aviv, CA–Toronto, IN–Bangalore).
    prefix2 = re.match(r'^([A-Z]{2})[-,\s]+(.+)',raw)
    if not country and prefix2 and pycountry.countries.get(alpha_2=prefix2[1]):
        rest = folded(prefix2[2]).split()
        for width in range(min(5,len(rest)),0,-1):
            if any(cat.places[p].country == prefix2[1]
                   for offset in range(len(rest)-width+1)
                   for p in cat.names.get(' '.join(rest[offset:offset+width]),()) if p.startswith('city:')):
                country = prefix2[1]
                break
    # A final ISO2 country is explicit in a city/region/country triple;
    # in two-part US addresses CA/IN/etc remain state abbreviations.
    raw_parts = [p.strip() for p in raw.split(',') if p.strip()]
    if len(raw_parts) >= 3 and re.fullmatch(r'[A-Z]{2}',raw_parts[-1]):
        code = raw_parts[-1]
        if code not in cat.region_codes or re.fullmatch(r'[A-Z]{2}',raw_parts[-2]):
            if pycountry.countries.get(alpha_2=code) or code == 'XK':
                country = code
    # A final country/state collision is resolved by the named city. Freising,
    # DE is Germany; Wilmington, DE is Delaware. An ambiguous city stays coarse.
    tail2 = re.search(r'[,\s]([A-Z]{2})$', raw)
    if not country and tail2 and tail2[1] in cat.region_codes:
        iso = tail2[1]
        city_matches = {p for width in range(min(6,len(words)-1),0,-1)
            for offset in range(len(words)-width)
            for p in cat.names.get(' '.join(words[offset:offset+width]),()) if p.startswith('city:')}
        in_country = any(cat.places[p].country == iso for p in city_matches)
        in_region = any('region:'+cat.places[p].region == cat.region_codes[iso] for p in city_matches)
        if in_country and not in_region:
            country = iso
    if (not country and country_hint and 'country:'+str(country_hint) in cat.places
            and not any(code in cat.region_codes for code in re.findall(r'\b[A-Z]{2}\b',raw))):
        # Use a stored country to disambiguate a published city only when
        # that city/region exists there. A stale summary cannot move Bayern
        # to the US or override a clear state abbreviation.
        corroborated = any(cat.places[p].country == country_hint
            for width in range(min(6,len(words)),0,-1)
            for offset in range(len(words)-width+1)
            for p in cat.names.get(' '.join(words[offset:offset+width]),())
            if p.startswith(('city:','region:')))
        if corroborated:
            country = country_hint
    region_ids = set()
    for width in range(min(5,len(words)),0,-1):
        for offset in range(len(words)-width+1):
            text = ' '.join(words[offset:offset+width])
            if len(text) < 4:
                continue
            for region_id in cat.region_names.get(text, ()):
                place = cat.places[region_id]
                if not country or place.country == country:
                    region_ids.add(region_id)
        if region_ids:
            break
    code_regions = set()
    short_codes = re.findall(r'\b[A-Z]{2}\b',raw)
    tail_region = re.search(r'(?:^|,\s*)([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?$',raw)
    if tail_region:
        short_codes.append(tail_region[1].upper())
    for code in short_codes:
        region_id = cat.region_codes.get(code)
        if region_id and (not country or cat.places[region_id].country == country):
            code_regions.add(region_id)
    if code_regions:
        region_ids = code_regions
    if len(region_ids) == 1:
        region_id = next(iter(region_ids))
        country = cat.places[region_id].country
    else:
        region_id = None
    if not country and country_hint and 'country:'+str(country_hint) in cat.places:
        country = country_hint
    if region_id and re.search(r'\bstate\b',key):
        region_name = folded(cat.places[region_id].name)
        if re.fullmatch(r'(?:state of )?'+re.escape(region_name)+r'(?: state)?(?: us| usa| united states)?',key):
            return {'region_id':region_id,'country_iso':country}
    if country:
        country_only = re.sub(r'\b(?:remote|hybrid|onsite|on site|jobs|work|home)\b',' ',key)
        if cat.country_names.get(' '.join(country_only.split())) == country:
            return {'country_iso':country}
    # Find whole city names in the address, then require geographic context.
    # This is catalog entity recognition, never a match against job titles.
    candidates = []
    for width in range(min(6,len(words)),0,-1):
        for offset in range(len(words)-width+1):
            text = ' '.join(words[offset:offset+width])
            if text in _GENERIC or len(text) < 3:
                continue
            places = [cat.places[p] for p in cat.names.get(text, ()) if p.startswith('city:')]
            places = [p for p in places if (not country or p.country == country)
                      and (not region_id or 'region:'+p.region == region_id)]
            canonical_names = [p for p in places if folded(p.name) == text]
            if canonical_names:
                places = canonical_names
            if places:
                candidates.append((width,offset,places))
        if candidates:
            break
    city = None
    if candidates:
        _, _, cities = min(candidates,key=lambda item:item[1])
        unique = {p.id:p for p in cities}
        if len(unique) == 1:
            city = next(iter(unique.values()))
        elif country or region_id:
            ranked = sorted(unique.values(),key=lambda p:p.population,reverse=True)
            if ranked[0].population > 10 * max(1,ranked[1].population):
                city = ranked[0]
    if city:
        country = city.country
        region_id = 'region:'+city.region if 'region:'+city.region in cat.places else None
    return {k:v for k,v in {'city_id':city.id if city else None,
                            'region_id':region_id,'country_iso':country}.items() if v}


def source_location_values(row):
    raw_places = row.get('locations') or []
    if isinstance(raw_places,str):
        try:
            raw_places = json.loads(raw_places)
        except ValueError:
            raw_places = []
    source_values = []
    if isinstance(raw_places,list):
        for item in raw_places:
            if isinstance(item,dict):
                value = item.get('raw') or item.get('display')
                if value:
                    source_values.append((value,item.get('country_iso')))
    values = []
    for value,hint in dict.fromkeys(source_values):
        values.extend((part.strip(),hint) for part in re.split(r'[;|│]',value) if part.strip())
    for value in ([] if values else [row.get('location_raw') or row.get('location')]):
        if value:
            for part in re.split(r'[;|│]',value):
                if part.strip() and not any(p[0] == part.strip() for p in values):
                    values.append((part.strip(),row.get('country_iso')))
    return list(dict.fromkeys(values))


def job_geo_entries(row):
    output = []
    for raw, hint in source_location_values(row):
        entry = resolve_entry(raw,hint)
        if entry:
            output.append({'raw':raw,**entry})
    return output


def job_place_ids(row):
    ids = set()
    cat = catalog()
    for entry in job_geo_entries(row):
        if entry.get('city_id'):
            ids.add(entry['city_id'])
            if entry['city_id'] in cat.city_parents:
                ids.add(cat.city_parents[entry['city_id']])
        if entry.get('region_id'):
            ids.add(entry['region_id'])
        if entry.get('country_iso'):
            ids.add('country:'+entry['country_iso'])
    known_country = str(row.get('country_iso') or '').strip().upper()
    if not any(p.startswith('country:') for p in ids) and 'country:'+known_country in cat.places:
        ids.add('country:'+known_country)
    raw = str(row.get('location_raw') or row.get('location') or '')
    if (row.get('remote_mode') == 'remote' or re.search(r'\bremote\b|work.at.home|work.from.home',raw,re.I)) and not re.search(r'\b(?:not|non)[ -]?remote\b',raw,re.I):
        ids.add('workplace:remote')
    return ids


def init_place_index(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS job_places (job_id TEXT NOT NULL, place_id TEXT NOT NULL, PRIMARY KEY(job_id,place_id))')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_job_places_place ON job_places(place_id,job_id)')


def sync_job_places(conn, rows):
    rows = list(rows)
    conn.executemany('DELETE FROM job_places WHERE job_id=?',[(r['id'],) for r in rows])
    values = [(r['id'],place_id) for r in rows for place_id in sorted(job_place_ids(r))]
    if values:
        conn.executemany('INSERT INTO job_places(job_id,place_id) VALUES (?,?) ON CONFLICT(job_id,place_id) DO NOTHING',values)


def indexed_location_where(query):
    if not str(query or '').strip():
        return [], []
    if folded(query) in {'workplace remote','remote','remote jobs'}:
        place_id = 'workplace:remote'
    else:
        place = catalog().resolve(query)
        if not place:
            return ['1=0'], []
        place_id = place.id
    return ['id IN (SELECT job_id FROM job_places WHERE place_id=?)'], [place_id]


def location_view(row, selected_place_id=None):
    """Return displayed and structured places from the same entities search uses."""
    cat = catalog()
    sources = source_location_values(row)
    output = []
    seen = set()
    short_countries = {'US':'US','GB':'UK','KR':'South Korea','AE':'UAE','TW':'Taiwan'}
    for raw,hint in sources:
        geo = resolve_entry(raw,hint)
        city = cat.places.get(geo.get('city_id'))
        region = cat.places.get(geo.get('region_id'))
        country_iso = geo.get('country_iso')
        country = cat.places.get('country:'+country_iso) if country_iso else None
        country_name = short_countries.get(country_iso,country.name if country else None)
        region_name = region.name if region else None
        if region and region.country == 'US':
            region_name = region.region.split('.',1)[1]
        elif region and region.country == 'CA':
            region_name = next((code for code,pid in cat.region_codes.items() if pid == region.id),region_name)
        remote = bool(re.search(r'\bremote\b|work.at.home|work.from.home',raw,re.I))
        if city:
            parts = [city.name]
            if region_name and folded(region_name) != folded(city.name):
                parts.append(region_name)
            parts.append(country_name)
            label = ', '.join(p for p in parts if p)
            if remote:
                label = 'Remote, '+label
        elif region:
            label = ', '.join(p for p in [region.name,country_name] if p)
            if remote:
                label = 'Remote, '+label
        elif country:
            if remote:
                label = 'Remote, '+country_name
            elif folded(raw) in cat.country_names:
                label = country_name
            else:
                label = raw
                if not any(folded(raw).endswith(name) for name,code in cat.country_names.items() if code == country_iso):
                    label += ', '+country_name
        else:
            label = raw if len(raw) <= 160 and folded(raw) not in {'multiple locations','not listed','unknown','not specified'} else 'Location not specified'
        if label in seen:
            continue
        seen.add(label)
        output.append({'raw':raw,'display':label,'city':city.name if city else None,
                       'region':region_name,'country':country_name,'country_iso':country_iso,
                       'place_id':city.id if city else region.id if region else country.id if country else None,
                       'city_id':geo.get('city_id'),'region_id':geo.get('region_id'),
                       'remote_mode':'remote' if remote else row.get('remote_mode')})
    if any(p['place_id'] for p in output):
        output = [p for p in output if p['place_id'] or folded(p['raw']) not in {'multiple locations','not specified','unknown'}]
    known_country = str(row.get('country_iso') or '').strip().upper()
    if not any(p['country_iso'] for p in output) and 'country:'+known_country in cat.places:
        country_name = short_countries.get(known_country,cat.places['country:'+known_country].name)
        if not output:
            output.append({'raw':None,'display':country_name,'city':None,'region':None,
                           'country':country_name,'country_iso':known_country,
                           'place_id':'country:'+known_country,'city_id':None,'region_id':None,
                           'remote_mode':row.get('remote_mode')})
        else:
            for place in output:
                place.update(country=country_name,country_iso=known_country,place_id='country:'+known_country)
                place['display'] += ', '+country_name
    if selected_place_id:
        def matches(place):
            return (place['city_id'] == selected_place_id or place['region_id'] == selected_place_id
                    or cat.city_parents.get(place['city_id']) == selected_place_id
                    or ('country:'+place['country_iso'] if place['country_iso'] else None) == selected_place_id)
        output.sort(key=lambda place:not matches(place))
    countries = {p['country_iso'] for p in output if p['country_iso']}
    return {'location':'; '.join(p['display'] for p in output) or 'Location not specified',
            'locations':output,'country_iso':next(iter(countries)) if len(countries)==1 else None}


def location_filter_value(query):
    if not query:
        return None
    if folded(query) in {'workplace remote','remote','remote jobs'}:
        return {'id':'workplace:remote','name':'Remote','label':'Remote jobs','kind':'workplace'}
    place = catalog().resolve(query)
    return catalog().public(place) if place else None
