-- Nov 12 2026 — make every claim the app shows match the data behind it (agent-readiness audit).

-- 1. One situation per lead, chosen from the actual document types (was: from foreclosure stage, which sent every
--    divorce/probate owner the property-tax script and every trustee substitution the lien script).
create or replace function public.pp_lead_situation(t text[]) returns text language sql immutable as $$
  select case
    when 'nts' = any(t) then 'auction'
    when 'nod' = any(t) then 'default'
    when t && array['deceased_owner','probate_deed','death_affidavit','probate_filing'] then 'estate'
    when t && array['lis_pendens','judicial_foreclosure','civil_property'] then 'lawsuit'
    when 'trustee_substitution' = any(t) then 'preparing'
    when t && array['divorce_homeowner','divorce_transfer','divorce_filing'] then 'divorce'
    when 'tax_delinquency' = any(t) then 'tax'
    when t && array['lien_judgment','mechanics_lien','creditor_suit'] then 'lien'
    else 'general' end $$;

-- 2. Scripts for the situations that had none, and an honest 'preparing' script (0 of 43 Utah County trustee
--    substitutions were followed by a notice of default in our own records).
insert into pp_call_scripts(situation, opener, bridge, ask, objection, close) values
('divorce',
 'I help families in {county} County when a household is changing and the house needs a plan — keeping it, refinancing, or selling. I''m not calling about anything legal.',
 'When two people share a home, deciding what happens to it is usually the hardest part. Real numbers early keep it from becoming a fight.',
 'Has anyone talked through what the house is worth and what each of you would walk away with?',
 'If it''s too soon: "Completely understand. I''ll leave my number — if it ever helps to know the value, one call and I''ll pull it."',
 'I can put together a no-obligation value and net sheet you can both look at. Want me to send it?'),
('lawsuit',
 'A notice of a pending lawsuit was recorded on {property} — {entry}. I''m not involved in the case; I help owners understand their options while something like that is open.',
 'A pending case clouds the title until it''s settled, which can make refinancing or selling harder later. Knowing the numbers now keeps your options open.',
 'Is the case close to being resolved, or still early?',
 'If they say it''s handled: "Good to hear. If anything changes, I can tell you what the property would sell for and how a sale would fit with the case."',
 'Want me to pull a value on the property so you have it in hand?'),
('general',
 'I work with property owners in {county} County and saw a recent county record on {property} — {entry}. I wanted to introduce myself in case selling is ever on your mind.',
 'No pressure — most people just want to know what their place is worth today.',
 'Is selling something you''ve thought about in the next year or so?',
 'If not interested: "No problem at all. Would it be okay if I sent you a value update once a year?"',
 'I can send you a free value estimate. What''s the best way to reach you?')
on conflict do nothing;
update pp_call_scripts set
  opener = 'Your lender recorded a change of trustee on the loan for {property} — {entry}. Sometimes that''s routine paperwork, sometimes it''s an early step before a default. I wanted you to know your options either way.',
  bridge = 'If anything is behind, this is the stage where you still have every option. If it''s just paperwork, you lose nothing by knowing what the house is worth.',
  objection = 'If they say nothing''s wrong: "Glad to hear it — that filing is often routine. If it ever helps to know your numbers, I''m one call away."'
 where situation = 'preparing';

insert into pp_sms_templates(situation, step, day_offset, body) values
('divorce',1,0,'Hi {first}, {agent} with {brokerage}. I help families in {county} County figure out the house when a household is changing — keep, refinance or sell. Happy to send a free value if useful. Reply STOP to opt out.'),
('divorce',2,7,'{first}, just checking in — if a no-obligation value on {property} would help, I can send it. — {agent}'),
('divorce',3,14,'No pressure, {first}. {phone} whenever it''s useful. — {agent}'),
('lawsuit',1,0,'Hi {first}, {agent} with {brokerage}. A pending-lawsuit notice was recorded on {property} ({entry}). I''m not involved in the case — I can tell you what the property would sell for if that helps. Reply STOP to opt out.'),
('lawsuit',2,5,'{first} — following up on {property}. Happy to send the numbers, no obligation. — {agent}'),
('lawsuit',3,12,'Closing the loop, {first}. {phone} if it''s ever useful. — {agent}'),
('general',1,0,'Hi {first}, {agent} with {brokerage}. I work with owners in {county} County — if you ever want a free value on {property}, I''m happy to send one. Reply STOP to opt out.'),
('general',2,10,'{first}, just following up — free value on {property} any time. — {agent}'),
('general',3,21,'Last note, {first}. {phone} if it''s ever useful. — {agent}');

-- 3. Trustee substitutions score the same in every county (Wasatch/Summit had 88, Utah County 60).
update pp_scraper_signals set score = 60 where signal_type = 'trustee_substitution' and not is_legacy and score <> 60;

-- 4. mailing_confirmed: the mailing address came from the assessor record of a parcel named on the filing, or the
--    owner-name match returned exactly one mailing address. Name matches with conflicting addresses are not confirmed.
alter table pp_entity_conviction add column if not exists mailing_confirmed boolean default false;
alter table pp_entity_conviction add column if not exists has_phone boolean default false;
