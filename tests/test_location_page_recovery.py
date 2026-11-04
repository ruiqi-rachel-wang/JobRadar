import json
import unittest

from app.api.ats.base import parse_detail_description
from app.api.ats.workday import _workday_detail_location
from app.api.geography import job_place_ids
from app.api.utils import normalize_locations


class OfficialLocationRecoveryTests(unittest.TestCase):
    def test_deloitte_location_list_retains_every_office(self):
        html='<div class="article__header--locations">'+''.join(
            '<p class="paragraph">'+label+'</p>' for label in [
                'Atlanta, Georgia, United States','Charlotte, North Carolina, United States',
                'New York, New York, United States'])+'</div>'
        detail=parse_detail_description(html)
        self.assertEqual(3,len(detail['locations_raw']))
        fields=normalize_locations(detail['locations_raw'])
        self.assertIn('region:US.NY',job_place_ids(fields))
        self.assertIn('region:US.GA',job_place_ids(fields))
        self.assertIn('region:US.NC',job_place_ids(fields))

    def test_siemens_explicit_locations_keep_both_cities(self):
        html='<div class="tf_locations"><ul><li class="list__item">Bangalore - Karnataka - India</li>'
        html+='<li class="list__item">Pune - Maharashtra - India</li></ul></div>'
        detail=parse_detail_description(html)
        self.assertEqual(2,len(detail['locations_raw']))
        self.assertTrue(all('India' in label for label in detail['locations_raw']))

    def test_ashby_embedded_posting_is_read_when_jsonld_address_is_empty(self):
        html='<script>window.__appData = '+json.dumps({'posting':{'locationName':'Remote, Global'}})+';</script>'
        html+='<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','description':'Build things.',
            'jobLocation':{'address':{}}})+'</script>'
        self.assertEqual(['Remote, Global'],parse_detail_description(html)['locations_raw'])

    def test_applicant_country_restrictions_are_not_an_unknown_ny_location(self):
        posting={'@type':'JobPosting','description':'Build things.','jobLocationType':'TELECOMMUTE',
                 'applicantLocationRequirements':[{'@type':'Country','name':'United States'},
                                                   {'@type':'Country','name':'United Kingdom'}]}
        detail=parse_detail_description('<script type="application/ld+json">'+json.dumps(posting)+'</script>')
        ids=job_place_ids(normalize_locations(detail['locations_raw']))
        self.assertTrue({'country:US','country:GB','workplace:remote'}<=ids)
        self.assertNotIn('region:US.NY',ids)

    def test_contact_headquarters_are_not_used_as_job_locations(self):
        detail=parse_detail_description('<footer>Headquarters: New York, NY</footer>'
            '<div class="job-description">'+'Build and support software. '*12+'</div>')
        self.assertNotIn('locations_raw',detail)

    def test_workday_facility_code_does_not_drop_explicit_country(self):
        location=_workday_detail_location({'location':'WARSAW PLANT & GO','country':{'descriptor':'Poland'}})
        self.assertIn('Poland',location)
        self.assertEqual('PL',normalize_locations(location)['country_iso'])

    def test_remote_us_state_abbreviation_does_not_become_israel(self):
        value=normalize_locations('Remote - IL')
        self.assertEqual('Remote, IL, US',value['location'])
        self.assertEqual('US',value['country_iso'])
        self.assertEqual('IL',normalize_locations('Remote, Israel')['country_iso'])

    def test_oracle_keeps_country_without_assigning_it_to_secondary_office(self):
        from app.api.ats.oraclecloud import _oraclecloud_locations
        locations=_oraclecloud_locations({'PrimaryLocation':'Freising, Bayern',
            'PrimaryLocationCountry':'DE','secondaryLocations':[{'Name':'Dallas, TX, United States'}],
            'workLocation':[{'TownOrCity':'Freising','Country':'DE'}]})
        ids=job_place_ids(normalize_locations(locations))
        self.assertTrue({'country:DE','country:US','region:US.TX'}<=ids)
        self.assertNotIn('region:US.NY',ids)

    def test_workday_typed_country_overrides_ambiguous_city(self):
        location=_workday_detail_location({'location':'New York','country':{'descriptor':'Canada'}})
        self.assertEqual('CA',normalize_locations(location)['country_iso'])


if __name__=='__main__':unittest.main()
