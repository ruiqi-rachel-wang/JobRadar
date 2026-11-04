import json
import sqlite3
import unittest

from app.api.geography import (catalog, init_place_index, job_place_ids,
                               location_view, resolve_entry, sync_job_places)


class GeographyTests(unittest.TestCase):
    def test_city_state_country_ids_and_aliases_are_distinct(self):
        examples = {'NY':'region:US.NY','New York':'region:US.NY',
                    'New York State':'region:US.NY','NYC':'city:5128581',
                    'New York City':'city:5128581','US':'country:US',
                    'USA':'country:US','Canada':'country:CA','CA':'region:US.CA',
                    'GBR':'country:GB','California':'region:US.CA',
                    'Bayern':'region:DE.02','Cambridge, MA':'city:4931972',
                    'Springfield, Illinois':'city:4250542'}
        for query, expected in examples.items():
            with self.subTest(query=query):
                self.assertEqual(expected,catalog().resolve(query).id)
        self.assertIsNone(catalog().resolve('Springfield'))

    def test_screenshot_partial_foreign_addresses_cannot_become_new_york(self):
        examples = [('York-pennsylvania-', 'US', 'region:US.PA'),
                    ('Edmonton-Alberta-Canada', 'CA','region:CA.01'),
                    ('Olmec TX - Houston','US','region:US.TX'),
                    ('Mississauga, ON L5R 0G1, Canada','CA','region:CA.08'),
                    ('Freising, Bayern','DE','region:DE.02'),
                    ('Remote, IL','US','region:US.IL'),
                    ('Kosovo - Remote','XK',None)]
        for raw,country,region in examples:
            with self.subTest(raw=raw):
                value=resolve_entry(raw)
                self.assertEqual(country,value.get('country_iso'))
                self.assertEqual(region,value.get('region_id'))
                self.assertNotEqual('region:US.NY',value.get('region_id'))

    def test_physical_and_remote_scope_need_actual_location_evidence(self):
        for raw in ['',None,'Not listed','Remote','Multiple Locations','Unlisted City']:
            with self.subTest(raw=raw):
                ids=job_place_ids({'location_raw':raw})
                self.assertNotIn('region:US.NY',ids)
                self.assertNotIn('country:US',ids)
        self.assertEqual({'country:CA','workplace:remote'},job_place_ids({'location_raw':'Remote, Canada'}))

    def test_multi_location_matching_and_city_borough_hierarchy(self):
        row={'locations':[{'raw':'Queens, NY, US'},{'raw':'London, UK'}]}
        ids=job_place_ids(row)
        self.assertTrue({'city:5133273','city:5128581','region:US.NY','country:US','country:GB'}<=ids)
        buffalo=job_place_ids({'location_raw':'Buffalo, NY'})
        self.assertIn('region:US.NY',buffalo)
        self.assertNotIn('city:5128581',buffalo)

    def test_country_state_collisions_are_resolved_by_advertised_cities(self):
        for raw,country in [('IL-Tel Aviv','IL'),('IN, Bangalore','IN'),
                            ('IN, Indianapolis','US'),('CA, Toronto','CA'),
                            ('Bogotá, DC, CO','CO'),('Hanover, New Hampshire','US'),('Freising, Bayern, DE','DE'),
                            ('Wilmington, DE','US'),('Toronto, CA','CA'),('Mumbai, IN','IN')]:
            with self.subTest(raw=raw):
                self.assertEqual(country,resolve_entry(raw).get('country_iso'))
        self.assertNotIn('city_id',resolve_entry('New York State'))

    def test_country_only_information_cannot_grant_membership_in_a_state(self):
        self.assertEqual({'country:NL'},job_place_ids({'location_raw':'NL','country_iso':'NL'}))
        row={'location':None,'country_iso':'US'}
        self.assertEqual({'country:US'},job_place_ids(row))
        view=location_view(row)
        self.assertEqual('US',view['location'])
        self.assertIsNone(view['locations'][0]['region_id'])

    def test_description_fragments_are_not_used_as_location_evidence(self):
        raw='C++ Software Development And Software Architecture combined with a strong understanding of agile development methods in New York'
        self.assertEqual({},resolve_entry(raw))

    def test_display_uses_the_same_place_entities_and_preserves_raw_source(self):
        row={'locations':[{'raw':'Freising, Bayern'},{'raw':'Remote, IL'}]}
        view=location_view(row)
        self.assertEqual('Freising, Bavaria, Germany; Remote, Illinois, US',view['location'])
        self.assertEqual('Freising, Bayern',view['locations'][0]['raw'])
        self.assertEqual('city:2925034',view['locations'][0]['city_id'])
        self.assertEqual('region:US.IL',view['locations'][1]['region_id'])
        self.assertNotIn('Israel',view['location'])

    def test_index_replacement_removes_an_old_location(self):
        conn=sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        init_place_index(conn)
        sync_job_places(conn,[{'id':'job','location_raw':'New York, NY, US'}])
        self.assertIn(('region:US.NY',),conn.execute('SELECT place_id FROM job_places').fetchall())
        sync_job_places(conn,[{'id':'job','location_raw':'London, UK'}])
        values={r[0] for r in conn.execute('SELECT place_id FROM job_places')}
        self.assertNotIn('region:US.NY',values)
        self.assertIn('country:GB',values)

    def test_matched_office_is_shown_first_without_losing_other_offices(self):
        row={'locations':[{'raw':'London, UK'},{'raw':'Buffalo, NY, US'}]}
        view=location_view(row,'region:US.NY')
        self.assertEqual('Buffalo, NY, US; London, England, UK',view['location'])
        self.assertEqual(2,len(view['locations']))

    def test_place_suggestions_include_both_state_and_city_and_explicit_kind(self):
        suggestions=catalog().suggestions('New York')
        self.assertEqual('region:US.NY',suggestions[0]['id'])
        self.assertIn('city:5128581',{p['id'] for p in suggestions})
        self.assertTrue(all(p['kind'] in {'country','region','city'} for p in suggestions))
        self.assertTrue(all('latitude' in p for p in suggestions))


if __name__=='__main__':unittest.main()
