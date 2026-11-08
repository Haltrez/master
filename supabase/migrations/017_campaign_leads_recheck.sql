-- Stricter agency check and email-domain check: every campaign lead is looked at again once.
update campaign_leads set niche = null, hub = null, search_id = null, is_agency = null, state = 'new', reason = null
where state <> 'filled' or sl_status = 'STARTED' or sl_status is null;
