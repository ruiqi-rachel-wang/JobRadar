import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for browser search race checks')
class SearchResponseOrderTests(unittest.TestCase):
    def run_scenario(self, scenario):
        template = (ROOT / 'app/templates/user.html').read_text()
        function = template.split('async function applyFilters(', 1)[1].split('\nfunction filterDOM()', 1)[0]
        script = r'''
const assert = require('node:assert/strict');
let jobSearchGeneration = 0;
let currentPage, currentLimit, currentTotal, currentTotalLowerBound, currentTotalExact, allResults;
const COMPANIES = [{name:'First'}, {name:'Second'}];
let activeSidebarCat = 'All', activeSidebarKind = 'category';
const rendered = [], errors = [], calls = [];
const document = { getElementById: () => ({ value:'', style:{}, textContent:'' }) };
const showEmptyMessage = message => errors.push(message);
const selectedCompanyNames = () => ['First'];
const resultsVisible = () => true;
const renderResults = jobs => rendered.push(jobs[0].company);
const jobdropLocationPicker = { value: async () => '' };
const fetchJobSearch = params => new Promise(resolve => calls.push({params, resolve}));
const response = company => ({ok:true, json:async () => ({jobs:[{company}], total:1})});
const flush = () => new Promise(resolve => setImmediate(resolve));
''' + '\nasync function applyFilters(' + function + '\n' + scenario
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_late_initial_response_cannot_replace_latest_company_search(self):
        self.run_scenario(r'''
(async () => {
  const older = applyFilters(['First']); await flush();
  const newer = applyFilters(['Second']); await flush();
  assert.equal(calls[1].params.get('company'), 'Second');
  calls[1].resolve(response('Second')); await newer;
  calls[0].resolve(response('First')); await older;
  assert.deepEqual(rendered, ['Second']);
  assert.deepEqual(errors, []);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_slow_location_resolution_cannot_start_an_obsolete_search(self):
        self.run_scenario(r'''
(async () => {
  let resolveLocation;
  jobdropLocationPicker.value = () => new Promise(resolve => { resolveLocation = resolve; });
  const older = applyFilters(['First']); await flush();
  jobdropLocationPicker.value = async () => '';
  const newer = applyFilters(['Second']); await flush();
  calls[0].resolve(response('Second')); await newer;
  resolveLocation('region:US.NY'); await older;
  assert.equal(calls.length, 1);
  assert.deepEqual(rendered, ['Second']);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
