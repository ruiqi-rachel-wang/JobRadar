# Y Combinator company tag

The **Y Combinator** attribute identifies 444 verified YC employers in the existing JobDrop catalog, including 440 currently public employers. Select **Attributes → Y Combinator** to filter the company sidebar and job search. The tag also works with title, city/state/country, experience, date, and saved-search filters.

Membership is based on primary YC company profiles and employer identity, independent of the company's job-board provider. The group includes all 248 employers using JobDrop's native YC collector, verified YC employers using other collectors, and established alumni such as Airbnb, Stripe, Coinbase, and Dropbox.

[Verification evidence](verified-companies.csv) records the employer, primary YC profile, stable ID, batch, official website, check time, and verification method. The November 5 primary-profile verification was reused for the exact approved employers; additional profiles were checked on November 6. Directory name matches alone were not accepted. Same-named unrelated employers, including JobDrop's Cohere, Runway, and Stream, are not tagged based on those matches.

This changes only company tags. The catalog still contains 2,605 employers, and existing industries, sources, other attributes, jobs, and account data are preserved. Filtering uses JobDrop's existing tag handling; no database migration is required. Membership can be expanded when more employer identities are confirmed.

Validation: existing taxonomy and company/tag search tests, catalog invariants, and live API/browser checks. Exact results are recorded in [validation.json](validation.json).
