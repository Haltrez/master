-- Role Match seed data. Mirrors engine/app/role_seed.py. Safe to re-run.

insert into role_families (name, exact_titles, related_titles) values
  ('Electrical Estimator',
   array['electrical estimator', 'senior electrical estimator', 'm&e estimator', 'estimator (electrical)']::text[],
   array['mechanical estimator', 'quantity surveyor (m&e)', 'pre-construction engineer (electrical)']::text[]),
  ('Mechanical Pre-Construction Manager',
   array['mechanical pre-construction manager', 'pre-construction manager (mechanical)', 'mechanical precon manager', 'mechanical pre-contracts manager']::text[],
   array['pre-construction manager', 'mep preconstruction manager', 'mechanical design manager', 'mechanical estimating manager']::text[]),
  ('Electrical Pre-Construction Manager',
   array['electrical pre-construction manager', 'pre-construction manager (electrical)', 'electrical precon manager', 'electrical pre-contracts manager']::text[],
   array['pre-construction manager', 'mep preconstruction manager', 'electrical design manager', 'electrical estimating manager']::text[]),
  ('Mechanical Design Engineer',
   array['mechanical design engineer', 'mechanical building services engineer', 'mechanical engineer (building services)', 'hvac design engineer']::text[],
   array['senior mechanical engineer (building services)', 'mechanical design manager', 'building services design engineer']::text[]),
  ('Electrical Site Manager',
   array['electrical site manager', 'm&e site manager', 'electrical site supervisor']::text[],
   array['electrical supervisor', 'm&e supervisor', 'electrical contracts manager']::text[]),
  ('Electrical Project Manager',
   array['electrical project manager', 'm&e project manager']::text[],
   array['electrical contracts manager', 'trainee electrical project manager', 'electrical project engineer']::text[])
on conflict (name) do nothing;

insert into employers (name, name_norm, website, careers_url, ats_type, town, source, status) values
  ('DL M&E Building Services Ltd', 'dl m and e building services', 'https://www.dl-bsl.co.uk', 'https://www.dl-bsl.co.uk/job-board', 'html', 'Westhoughton, Bolton', 'manual', 'active'),
  ('Central Electrical & Mechanical NW Ltd', 'central electrical and mechanical nw', 'https://celectrical.com', 'https://celectrical.com/careers/', 'html', 'Manchester', 'manual', 'active'),
  ('B-engineering (NW) Ltd', 'b engineering nw', 'https://b-enggroup.co.uk', 'https://b-enggroup.co.uk/jobs/', 'html', 'Warrington', 'manual', 'active')
on conflict (name_norm) do nothing;

insert into templates (name, channel, subject, body)
select 'Role Match DM', 'linkedin', null, 'Hey {first_name}, quick one. Got any candidates you''re sitting on right now? Send me the roles and locations (no names needed) and I''ll find direct employers hiring those exact roles nearby. Free, no strings.'
where not exists (select 1 from templates where name = 'Role Match DM');
