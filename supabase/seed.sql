-- Repost Radar seed data: outreach templates.
-- Plain text only. No links or images in first-touch emails.
-- Every email-channel template carries the sender footer and an opt-out.

insert into templates (name, channel, subject, body) values
(
  'Cold email, first touch',
  'email',
  '{lead1_count}x reposted {niche} role in {city}',
  $t$Hi {first_name},

Saw {agency} places {niche} roles around {city}.

{lead1_employer} has posted the same {lead1_role} role {lead1_count} times since {lead1_since}. Nobody's filled it yet. {lead2_employer} is on {lead2_count} posts for {lead2_role}.

I track companies like this across the UK every day. Want 20 more in your niche? Free, no call needed.

{sender_name}, SmartEmploy
SmartEmploy is a trading name of SIA Impresso, Latvia
Not interested? Reply "stop" and I won't email again.$t$
),
(
  'Follow-up, day 3',
  'email',
  're: {lead1_count}x reposted {niche} role in {city}',
  $t$Hi {first_name}, one more for you: {lead3_employer} has posted {lead3_role} {lead3_count} times. Want the full list of 20?

{sender_name}, SmartEmploy
SmartEmploy is a trading name of SIA Impresso, Latvia
Not interested? Reply "stop" and I won't email again.$t$
),
(
  'Follow-up, day 7',
  'email',
  'close the loop?',
  $t$Hi {first_name}, I'll stop here. If new clients in {niche} aren't a priority right now, no worries. If they are, reply "leads" and I'll send 20 this week.

{sender_name}, SmartEmploy
SmartEmploy is a trading name of SIA Impresso, Latvia
Not interested? Reply "stop" and I won't email again.$t$
),
(
  'LinkedIn DM',
  'linkedin',
  null,
  $t$Hey {first_name}, quick one. {lead1_employer} has posted {lead1_role} {lead1_count} times since {lead1_since} and still hasn't filled it. I track these for {niche} agencies in {city}. Want the list? Free, no strings.$t$
),
(
  'Client opener to hiring manager',
  'client_hm_email',
  null,
  $t$Hi {first_name},

I noticed {company} has advertised the {hm_role} role {posted_since}. That usually means the right person hasn't come through yet.

We specialise in {niche} recruitment around {city} and have candidates who could interview this week. Worth a quick call?$t$
);
