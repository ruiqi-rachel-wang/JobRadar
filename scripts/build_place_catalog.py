#!/usr/bin/env python3
"""Build JobDrop's offline place catalog from the official GeoNames dumps.

Download cities1000.zip, admin1CodesASCII.txt and countryInfo.txt into --source.
The compact derivative is CC BY 4.0; source hashes are retained for review.
"""
import argparse
import gzip
import hashlib
import json
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

import pycountry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('app/resources/geography/places.json.gz'))
    args = parser.parse_args()
    records = []
    for country in pycountry.countries:
        names = [country.name, getattr(country, 'common_name', country.name),
                 getattr(country, 'official_name', country.name), country.alpha_2, country.alpha_3]
        records.append(['country:'+country.alpha_2, getattr(country, 'common_name', country.name),
                        country.alpha_2, '', 0, None, None, sorted(set(names))])
    # Kosovo's XK identifier is used by official hiring feeds and GeoNames.
    records.append(['country:XK','Kosovo','XK','',0,None,None,['Kosovo','XK','XKX']])
    for line in (args.source/'admin1CodesASCII.txt').read_text().splitlines():
        code, name, ascii_name, _ = line.split('\t')
        records.append(['region:'+code,name,code[:2],code,0,None,None,list(set([name,ascii_name]))])
    with zipfile.ZipFile(args.source/'cities1000.zip') as archive:
        for line in archive.read('cities1000.txt').decode().splitlines():
            cols = line.split('\t')
            aliases = [cols[1], cols[2]]
            for alt in cols[3].split(','):
                if (len(aliases) < 8 and len(alt) >= 4 and len(alt) <= 60
                        and all(not ch.isalpha() or 'LATIN' in unicodedata.name(ch, '') for ch in alt)
                        and alt not in aliases):
                    aliases.append(alt)
            records.append(['city:'+cols[0],cols[1],cols[8],cols[8]+'.'+cols[10],
                            int(cols[14] or 0),float(cols[4]),float(cols[5]),aliases])
    # An alternative name occasionally points at a neighboring city. Do not
    # let that alias override another city's primary name in the same region.
    def key(text):
        return ''.join(c for c in unicodedata.normalize('NFKD',text).casefold() if c.isalnum())
    primary_names = defaultdict(set)
    for row in records:
        if row[0].startswith('city:'):
            primary_names[(row[2],row[3],key(row[1]))].add(row[0])
    for row in records:
        if row[0].startswith('city:'):
            row[7] = [alias for alias in row[7] if key(alias) == key(row[1])
                      or not (primary_names.get((row[2],row[3],key(alias)),set()) - {row[0]})]
    sources = {name:{'url':'https://download.geonames.org/export/dump/'+name,
                    'sha256':hashlib.sha256((args.source/name).read_bytes()).hexdigest()}
               for name in ['cities1000.zip','admin1CodesASCII.txt','countryInfo.txt']}
    payload = {'version':1,'license':'CC BY 4.0','attribution':'GeoNames (https://www.geonames.org/)',
               'sources':sources,'columns':['id','name','country','region','population','latitude','longitude','aliases'],
               'places':records}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('wb') as out, gzip.GzipFile(fileobj=out,mode='wb',mtime=0) as zipped:
        zipped.write(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())
    print(json.dumps({'places':len(records),'bytes':args.output.stat().st_size}))

if __name__ == '__main__':
    main()
