"""Fetcher registry and dispatch for JobRadar ATS sources."""

from .utils import _parse_posted, extract_experience, make_id, normalize_location

from .ats.greenhouse import fetch_greenhouse
from .ats.lever import fetch_lever
from .ats.ashby import fetch_ashby
from .ats.workday import fetch_workday
from .ats.bamboohr import fetch_bamboohr
from .ats.icims import fetch_icims
from .ats.apple import fetch_apple
from .ats.google import fetch_google
from .ats.microsoft import fetch_microsoft
from .ats.pinpoint import fetch_pinpoint
from .ats.eightfold import fetch_eightfold
from .ats.smartrecruiters import fetch_smartrecruiters
from .ats.oraclecloud import fetch_oraclecloud, fetch_oracle
from .ats.avature import fetch_avature
from .ats.successfactors import fetch_successfactors
from .ats.workable import fetch_workable
from .ats.jazzhr import fetch_jazzhr
from .ats.jobvite import fetch_jobvite
from .ats.rippling import fetch_rippling
from .ats.talentbrew import fetch_talentbrew
from .ats.careerpuck import fetch_careerpuck
from .ats.playwright import (
    fetch_adp, fetch_brassring, fetch_gr8people, fetch_internal, fetch_paradox,
    fetch_phenom, fetch_playwright, fetch_proprietary, fetch_recooty,
    fetch_salesforce, fetch_taleo, fetch_talemetry, _fetch_playwright_with_source,
)
from .ats.html import fetch_html
from .ats.jobsapi import fetch_jobsapi
from .ats.tiktok import fetch_tiktok
from .ats.companies.amazon import fetch_amazon
from .ats.companies.americanexpress import fetch_americanexpress
from .ats.companies.bain import fetch_bain
from .ats.companies.boozallen import fetch_boozallen
from .ats.companies.dhl import fetch_dhl
from .ats.companies.dominos import fetch_dominos
from .ats.companies.etsy import fetch_etsy
from .ats.companies.johnsoncontrols import fetch_johnsoncontrols
from .ats.companies.kpmg import fetch_kpmg
from .ats.companies.nike import fetch_nike
from .ats.companies.starbucks import fetch_starbucks
from .ats.companies.unitedhealth import fetch_unitedhealth
from .ats.companies.vimeo import fetch_vimeo
from .ats.companies.adobe import fetch_adobe

FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "bamboohr": fetch_bamboohr,
    "icims": fetch_icims,
    "apple": fetch_apple,
    "google": fetch_google,
    "microsoft": fetch_microsoft,
    "pinpoint": fetch_pinpoint,
    "eightfold": fetch_eightfold,
    "smartrecruiters": fetch_smartrecruiters,
    "oraclecloud": fetch_oraclecloud,
    "oracle": fetch_oracle,
    "avature": fetch_avature,
    "successfactors": fetch_successfactors,
    "workable": fetch_workable,
    "jazzhr": fetch_jazzhr,
    "jobvite": fetch_jobvite,
    "rippling": fetch_rippling,
    "talentbrew": fetch_talentbrew,
    "careerpuck": fetch_careerpuck,
    "brassring": fetch_brassring,
    "taleo": fetch_taleo,
    "paradox": fetch_paradox,
    "recooty": fetch_recooty,
    "phenom": fetch_phenom,
    "talemetry": fetch_talemetry,
    "adp": fetch_adp,
    "salesforce": fetch_salesforce,
    "bambusdev": fetch_salesforce,
    "internal": fetch_internal,
    "proprietary": fetch_proprietary,
    "gr8people": fetch_gr8people,
    "amazon": fetch_amazon,
    "americanexpress": fetch_americanexpress,
    "bain": fetch_bain,
    "boozallen": fetch_boozallen,
    "dhl": fetch_dhl,
    "dominos": fetch_dominos,
    "etsy": fetch_etsy,
    "johnsoncontrols": fetch_johnsoncontrols,
    "kpmg": fetch_kpmg,
    "nike": fetch_nike,
    "starbucks": fetch_starbucks,
    "unitedhealth": fetch_unitedhealth,
    "vimeo": fetch_vimeo,
    "adobe": fetch_adobe,
    "jobsapi": fetch_jobsapi,
    "tiktok": fetch_tiktok,
    "playwright": fetch_playwright,
    "html": fetch_html,
}

_API_MODES = set(FETCHERS) - {"html"}

def fetch_jobs(company, seen):
    scrape = company.get("scrape") or {}
    mode = scrape.get("mode", "html")
    fn = FETCHERS.get(mode)
    if not fn:
        return [], f"unknown mode: {mode}"
    jobs, err = fn(scrape, seen, company["name"])
    if (err or not jobs) and mode not in {"playwright", "html"} and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        fallback_jobs, fallback_err = _fetch_playwright_with_source(scrape, seen, company["name"], mode)
        if fallback_jobs or not jobs:
            jobs, err = fallback_jobs, fallback_err
    for j in jobs or []:
        j["location"] = normalize_location(j.get("location", ""))
        j["posted"] = _parse_posted(j.get("posted", ""))
        existing = None
        if isinstance(seen, dict):
            existing = seen.get(make_id(company["name"], j.get("url", ""), j.get("title", "")))
        if not j.get("description") and isinstance(existing, dict) and existing.get("exp_label"):
            j["exp_min"] = existing.get("exp_min")
            j["exp_max"] = existing.get("exp_max")
            j["exp_label"] = existing.get("exp_label", "")
            j["exp_source"] = existing.get("exp_source", "")
        else:
            exp = extract_experience(j.get("title", ""), j.get("description", ""))
            j["exp_min"] = exp.get("min")
            j["exp_max"] = exp.get("max")
            j["exp_label"] = exp.get("label", "")
            j["exp_source"] = exp.get("source", "")
    include_text = scrape.get("text_include")
    if include_text and jobs:
        needles = include_text if isinstance(include_text, list) else [include_text]
        needles = [str(n).lower() for n in needles if str(n).strip()]
        if needles:
            jobs = [
                j for j in jobs
                if any(
                    n in " ".join([j.get("title", ""), j.get("location", ""), j.get("url", "")]).lower()
                    for n in needles
                )
            ]
    return jobs, err
