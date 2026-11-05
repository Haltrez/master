-- Two campaigns by who reads the email: leaders (founders, directors, CEOs, managers) and sales people.
alter table outreach_queue drop constraint if exists outreach_queue_campaign_role_check;
update outreach_queue set campaign_role = 'leaders' where campaign_role in ('main', 'fallback');
alter table outreach_queue alter column campaign_role set default 'leaders';
alter table outreach_queue add constraint outreach_queue_campaign_role_check check (campaign_role in ('leaders', 'sales'));
insert into app_settings (key, value)
  select 'outreach_leaders_campaign_id', value from app_settings where key = 'outreach_main_campaign_id'
on conflict (key) do nothing;
