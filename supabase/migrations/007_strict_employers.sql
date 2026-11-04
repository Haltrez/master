-- Strict "real companies only": Companies House verification of hot-lead
-- employers, recruitment SIC codes (78xxx) mark a company as an agency.
alter table lead_contacts add column if not exists company_status text;
alter table lead_contacts add column if not exists sic_codes text[];
alter table lead_contacts add column if not exists is_agency boolean not null default false;
alter table lead_contacts add column if not exists verified_company boolean not null default false;
alter table lead_contacts add column if not exists contacts_checked boolean not null default false;
update lead_contacts set contacts_checked = true where contact_name is not null or email is not null;

insert into app_settings (key, value) values
  ('verified_companies_only', 'true'),
  ('lead_ch_checks_per_run', '300')
on conflict (key) do nothing;
