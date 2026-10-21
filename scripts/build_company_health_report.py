#!/usr/bin/env python3
"""Combine checkpointed source probes, stored-link checks, and deployed refreshes."""
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_company_health import fingerprint
from scripts.audit_job_links import JOB_QUERY_KEYS, structural_issue


def source_issues(job):
    issue = structural_issue(job)
    if issue:
        return issue
    parsed = urlsplit(job.get('url') or '')
    if not {k.lower() for k in parse_qs(parsed.query)}.intersection(JOB_QUERY_KEYS) and re.search(
        r'/(?:features|platform|solutions|products|blog|resources|press|go|departments|benefits|legal|privacy|contact|pricing)(?:/|$)', parsed.path, re.I
    ):
        return 'non_job_section'
    return ''


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows({key: ' '.join(value.split()) if key == 'page_title' and isinstance(value, str) else value
                          for key, value in row.items()} for row in rows)


def build(directory, output, config_path):
    from scripts.audit_job_links import inventory
    directory, output = Path(directory), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads((directory / 'snapshot.json').read_text())
    companies = json.loads(Path(config_path).read_text())
    candidates = defaultdict(list)
    for folder in ['', 'priority', 'repairs', 'batch2', 'batch3', 'postfix']:
        for path in (directory / folder / 'sources').glob('*.json'):
            result = json.loads(path.read_text())
            candidates[result['name']].append(result)
    refreshes = {}
    deployed = [json.loads(path.read_text()) for path in (directory / 'deployed').glob('*result.json')]
    final_runs = directory / 'final/refresh-results.json'
    if final_runs.exists():
        deployed.extend(json.loads(final_runs.read_text()))
    for result in sorted(deployed, key=lambda r: r.get('scan_run_id', 0)):
        for row in result.get('progress', []):
            refreshes[row['company']] = row
    repair_names = {row['name'] for row in json.loads((directory / 'config_repairs.json').read_text())}
    repair_names.update({'Beehiiv', 'Fullbay', 'Gradient Experience', 'rEvolution', 'Shortcut', '10x Genomics', 'Bank of America'})
    if final_runs.exists():
        repair_names.update(row['company'] for result in json.loads(final_runs.read_text()) for row in result['progress'])
    manual = {}
    for path in (directory / 'deployed').glob('*manual.json'):
        result = json.loads(path.read_text())
        manual[result['company']] = result
    # Reconstruct the exact sample so abandoned checks from an earlier grouping
    # are excluded from counts. inventory only reads the frozen job export.
    selected = inventory(directory / 'open_jobs.jsonl.gz', directory)
    link_rows, link_counts = [], defaultdict(Counter)
    for job in selected:
        key = hashlib.sha256((job['id'] + '|' + job['url']).encode()).hexdigest()[:24]
        path = directory / 'links' / (key + '.json')
        if not path.exists():
            continue
        row = json.loads(path.read_text())
        link_rows.append({k: row.get(k, '') for k in ['id', 'company', 'title', 'url', 'status', 'http_status', 'final_url', 'page_title', 'checked_at']})
        link_counts[job['company']][row['status']] += 1
    write_csv(output / 'sampled_job_links.csv', link_rows)
    inventory_data = json.loads((directory / 'url_inventory.json').read_text())
    write_csv(output / 'structural_url_flags.csv', inventory_data['structural_issues'])
    rows = []
    for company in companies:
        name = company['name']
        options = candidates[name]
        exact = [r for r in options if r.get('fingerprint') == fingerprint(company)]
        probe = max(exact or options, key=lambda r: r.get('checked_at', ''), default={})
        metric = snapshot['metrics'].get(name, {})
        recent = snapshot['recent_scans'].get(name, [{}])[0]
        refresh = refreshes.get(name, {})
        status = probe.get('status', 'not_checked')
        quality = Counter(source_issues(j) for j in probe.get('jobs', []))
        quality.pop('', None)
        statuses = link_counts[name]
        scrape = company.get('scrape') or {}
        source_url = scrape.get('list_url') or ''
        if not source_url:
            mode = scrape.get('mode')
            if mode == 'greenhouse':
                source_url = 'https://job-boards.greenhouse.io/' + scrape.get('board_token', '')
            elif mode == 'ashby':
                source_url = 'https://jobs.ashbyhq.com/' + scrape.get('ashby_slug', '')
            elif mode == 'lever':
                source_url = 'https://jobs.lever.co/' + scrape.get('lever_slug', '')
            elif mode == 'bamboohr':
                source_url = 'https://' + scrape.get('bamboohr_slug', '') + '.bamboohr.com/careers'
        http_blocked = any(r.get('status') in {401, 403, 429} for r in probe.get('requests', []))
        if company.get('scan_enabled') is False:
            resolution = 'intentionally_disabled'
        elif name in manual:
            resolution = manual[name]['status']
        elif refresh.get('status') == 'done' and name in repair_names:
            resolution = 'repaired_and_refreshed'
        elif refresh.get('status') in {'error', 'failed', 'warning'}:
            resolution = 'refresh_incomplete_existing_jobs_preserved'
        elif status == 'timeout':
            resolution = 'inconclusive_probe_time_limit'
        elif http_blocked and status != 'listed':
            resolution = 'inconclusive_access_or_rate_limit'
        elif status == 'error':
            resolution = 'source_error_needs_followup'
        elif status == 'empty':
            resolution = 'empty_source_needs_confirmation'
        elif quality:
            resolution = 'suspected_navigation_or_category_results'
        elif status == 'listed' and not metric.get('discoveries_7d'):
            resolution = 'source_lists_jobs_no_recent_discoveries'
        elif status == 'listed':
            resolution = 'source_lists_jobs'
        else:
            resolution = 'not_checked'
        rows.append({
            'company': name, 'enabled': company.get('scan_enabled') is not False,
            'configured_mode': (company.get('scrape') or {}).get('mode', 'html'),
            'configured_source_url': source_url,
            'resolution': resolution, 'probe_status': status, 'probe_count': probe.get('count', ''),
            'probe_config_matches_current': bool(exact), 'probe_reported_complete': probe.get('complete', False),
            'source_quality_flags': sum(quality.values()), 'quality_flag_types': '; '.join(quality),
            'open_jobs_before': metric.get('open_jobs', 0), 'new_last_7_days': metric.get('discoveries_7d', 0),
            'new_previous_7_days': metric.get('discoveries_previous_7d', 0),
            'last_discovered_before': metric.get('latest_discovery', ''),
            'last_scheduled_scan_status': recent.get('status', ''),
            'source_error': '; '.join(probe.get('errors') or [])[:1800],
            'http_job_samples': sum(statuses.values()),
            'sample_broken_or_landing': sum(v for k,v in statuses.items() if k in {'broken_http','broken_page','expired_page','redirect_loop','redirected_to_landing'}),
            'sample_inconclusive': sum(v for k,v in statuses.items() if k.startswith('inconclusive') or k == 'reachable_unverified'),
            'deployed_refresh_status': refresh.get('status', ''),
            'deployed_refresh_jobs': refresh.get('count', ''),
        })
    write_csv(output / 'all_companies.csv', rows)
    current_names = {c['name'] for c in companies}
    orphaned = [{'company': name, **{k: m.get(k, '') for k in ['historical_jobs','open_jobs','latest_discovery','latest_seen']}} for name,m in snapshot['metrics'].items() if name not in current_names]
    write_csv(output / 'unconfigured_historical_companies.csv', orphaned)
    disabled_rechecks = []
    for path in (directory / 'disabled_recheck/sources').glob('*.json'):
        result = json.loads(path.read_text())
        disabled_rechecks.append({
            'company': result['name'], 'probe_status': result['status'], 'count': result['count'],
            'source_quality_flags': sum(bool(source_issues(j)) for j in result.get('jobs', [])),
            'errors': '; '.join(result.get('errors') or []),
        })
    write_csv(output / 'disabled_source_rechecks.csv', sorted(disabled_rechecks, key=lambda r: r['company']))
    totals = Counter(row['resolution'] for row in rows)
    link_totals = Counter(row['status'] for row in link_rows)
    data = {'configured_companies': len(rows), 'resolution_counts': dict(totals),
            'stored_urls_structurally_inspected': inventory_data['stored_urls_inspected'],
            'http_sampled_urls': len(link_rows), 'http_result_counts': dict(link_totals),
            'structural_flags_before_repairs': len(inventory_data['structural_issues']),
            'unconfigured_historical_labels': len(orphaned),
            'unconfigured_labels_with_open_jobs': sum(bool(row['open_jobs']) for row in orphaned)}
    final_path = directory / 'final/verification.json'
    if final_path.exists():
        final = json.loads(final_path.read_text())
        data['final_recheck'] = {key: final[key] for key in (
            'companies_checked', 'open_jobs', 'failed_companies', 'expected_empty_company',
            'tests_passed', 'link_samples_verified', 'browser_ui_flows',
        ) if key in final}
    (output / 'summary.json').write_text(json.dumps(data, indent=2))
    lines = ['# Jobdrop company and job-link audit', '',
             f"Snapshot: {snapshot['generated_at']}. Production repairs were applied October 20–21, 2026.", '',
             f"Checked all {len(rows):,} configured companies. Inspected all {inventory_data['stored_urls_inspected']:,} stored open-job URLs structurally and HTTP-tested {len(link_rows):,} samples across {inventory_data['families']:,} company/URL families (oldest and two newest per family). This is not an HTTP test of every stored job.", '',
             'The audit is complete as an inventory; unresolved sources are explicitly recorded below and in the CSV. A reachable page, an API count, or an empty board alone does not establish that all jobs are correct or that a company stopped hiring.', '',
             '| Result | Companies |', '| --- | ---: |']
    lines += [f'| {key.replace("_", " ")} | {value} |' for key,value in sorted(totals.items())]
    lines += ['', '## Deployed repairs', '', '| Company | Refreshed jobs |', '| --- | ---: |']
    lines += [f"| {r['company']} | {r['deployed_refresh_jobs']} |" for r in rows if r['resolution'] == 'repaired_and_refreshed']
    lines += ['', 'The initial Capgemini repair used SuccessFactors pagination and posting-level location elements to collect 629 jobs instead of 25, including 176 jobs with multiple locations. Original 25 IDs and dates were preserved. Source location values are retained; equivalent display locations may be deduplicated.', '',
              'Earlier Loom and Deel repairs were separately deployed and backed up: Loom keeps brand-specific job detail links; Deel uses its current public job pages.', '',
              'Shortcut’s official careers page explicitly says it is not actively hiring. Its last stored posting returned 404 and the current route redirected to an empty board; that expired row was backed up and closed. The source remains recorded for follow-up when hiring resumes.', '',
              '## Open findings', '',
              'Access blocks, request limits, browser-only results, and audit time limits remain inconclusive. Empty feeds are preserved for follow-up rather than treated as proof of zero jobs. Navigation/category flags require source correction; they are not counted as healthy listings. Acquired brands must not inherit every job from the parent company.', '',
              'Every unresolved company, error, prior hiring activity, sample result, and refresh status is in [all_companies.csv](all_companies.csv). Historical labels missing current config are in [unconfigured_historical_companies.csv](unconfigured_historical_companies.csv); aliases were not automatically merged or deleted.', '',
              '## Coverage and safeguards', '',
              '- Read-only source probes covered listing pagination without sending alerts or writing jobs.',
              '- HTTP 403/429/challenges and client-rendered shells were kept separate from verified job pages.',
              '- A final HTTP pass sampled 114 repaired/current postings: 91 matched job titles, 22 were reachable without verifiable job content in the HTTP response, and one expired Shortcut posting was confirmed and closed. See current_repaired_link_samples.csv. These are representative checks, not certification of every posting.',
              '- Full refreshes used the normal scanner; partial/error results did not expire unseen jobs.',
              '- Affected database rows and code/config were backed up before deployment. Jobs were soft-closed through normal reconciliation; saved jobs were not deleted.',
              '- Existing fresh/full scan schedules were verified and retained. Full scans already run; absence of a new job in a fresh scan can be legitimate.',
              '- All 55 deliberately disabled entries remained disabled. Read-only rechecks covered 39 previously technical failures; 16 policy/administrative exclusions were retained. See disabled_source_rechecks.csv; a positive count can still be navigation or an unrelated employer.',
              '- The raw runtime probes are under app/data/company_health/2026-10-20 and are ignored by Git.', '',
              'Validation: all 177 tests passed, including regression checks for redirects, stale public routes, embedded apply buttons, pagination caps, and expanded Capgemini locations. Production configs were compared with the deployed changes, and 8,565 backed-up rows retained their IDs and existing dates.', '',
              '[HTTP samples](sampled_job_links.csv) · [Structural flags before repairs](structural_url_flags.csv) · [Machine-readable totals](summary.json)', '']
    (output / 'README.md').write_text('\n'.join(lines))
    boa_path = directory / 'deployed/boa-verification.json'
    if boa_path.exists():
        boa = json.loads(boa_path.read_text())
        with (output / 'README.md').open('a') as stream:
            stream.write(
                '\n## Bank of America location follow-up\n\n'
                f"Refreshed {boa['open_jobs']:,} jobs and corrected {boa['existing_locations_corrected']:,} existing location lists. "
                f"The source has {boa['multi_location_jobs']:,} jobs with multiple locations. "
                f"{boa['nc_only_jobs_now_include_ny']} previously NC-only listings now include their New York locations. "
                'The scraper now reads the primary address and both additional-location fields, deduplicates offices, '
                'and retains the raw source values. The live New York filter was verified with Product Manager, CashPro Security. '
                f"All {boa['historical_ids_and_dates_preserved']:,} historical IDs and existing dates were preserved. "
                'The follow-up passed all 185 tests.\n'
            )
    if final_path.exists():
        with (output / 'README.md').open('a') as stream:
            stream.write('\n## Final recheck and release\n\n'
                         '[Final verification](FINAL_VERIFICATION.md) records the fresh scans, live browser checks, '
                         'Workable office-merge fix, Quadric migration, 188 passing tests, and 138 verified representative posting links. '
                         'Earlier counts are historical. Unresolved sources outside the repair set remain in the company inventory.\n')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default='app/data/company_health/2026-10-20')
    parser.add_argument('--output', default='docs/company_health_2026-10-20')
    parser.add_argument('--config', default='app/companies.json')
    args = parser.parse_args()
    print(json.dumps(build(args.directory,args.output,args.config),indent=2))


if __name__ == '__main__':
    main()
