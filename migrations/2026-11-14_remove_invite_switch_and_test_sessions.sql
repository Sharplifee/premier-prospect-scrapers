-- 2026-11-14 remove_invite_switch_and_test_sessions_2026_11_14 (approved by Connor)
-- No login wall, invite gate or access lock anywhere: every new account is active,
-- the invite switch and the admin function that could turn it back on are gone.
-- pp_public_config stays (builds 98-99 call it) but always answers invite_only=false.

create or replace function public.pp_ensure_agent(p_display_name text default null::text)
 returns json language plpgsql security definer set search_path to 'public', 'pg_temp'
as $function$
declare u uuid := pp_uid(); claims json := current_setting('request.jwt.claims', true)::json; em text; anon boolean; r pp_agents; first boolean;
begin
  if u is null then raise exception 'not signed in'; end if;
  em := claims->>'email'; anon := coalesce((claims->>'is_anonymous')::boolean, false);
  first := not exists (select 1 from pp_agents where not coalesce(is_anonymous,false));
  insert into pp_agents(user_id, agent_id, display_name, email, is_admin, active, default_mode, is_anonymous)
  values (u, u::text,
          coalesce(nullif(p_display_name,''), case when anon then 'This device' else split_part(coalesce(em,'agent'),'@',1) end),
          em, (first and not anon), true, 'work', anon)
  on conflict (user_id) do update set email = coalesce(excluded.email, pp_agents.email), display_name = coalesce(nullif(p_display_name,''), pp_agents.display_name)
  returning * into r; return row_to_json(r); end $function$;

create or replace function public.pp_public_config()
 returns json language sql stable security definer set search_path to 'public'
as $function$ select json_build_object('invite_only', false) $function$;

drop function if exists public.pp_set_invite_only(boolean);
delete from public.pp_settings where key = 'invite_only';

-- The 13 anonymous test accounts created during the Nov 13 audit (sessions were already revoked).
delete from public.pp_agents where user_id in (
 'd3ca9827-ac2b-4df3-baae-8a14ecc4f055','cc28e652-6a2c-40ef-aee4-d1bf4d98fde5','75c717d6-e021-4a69-b568-1d6190d6384f',
 '443ea63b-aa19-4ecf-9356-02757a00bdda','36dd8089-a8fc-480b-8483-920a057f1cfa','d4882cf0-0ec1-42d2-82cc-b2e90005dec1',
 '1af76bcc-2743-42c5-bf6d-7755885cc6e8','93e5a346-7b21-49b4-85f8-58a4dcd7533c','643b0f83-e44f-4145-aaeb-48d879afd817',
 'd8e965c4-70cb-4e2c-9115-09c52fb3396a','2d65820f-57a0-49c8-a676-061f21351de7','69a692b2-9d2f-486c-b342-d520be771e56',
 '29cc74b9-8670-4991-ac02-d8f3a3ab7c11');
delete from auth.users where is_anonymous and id in (
 'd3ca9827-ac2b-4df3-baae-8a14ecc4f055','cc28e652-6a2c-40ef-aee4-d1bf4d98fde5','75c717d6-e021-4a69-b568-1d6190d6384f',
 '443ea63b-aa19-4ecf-9356-02757a00bdda','36dd8089-a8fc-480b-8483-920a057f1cfa','d4882cf0-0ec1-42d2-82cc-b2e90005dec1',
 '1af76bcc-2743-42c5-bf6d-7755885cc6e8','93e5a346-7b21-49b4-85f8-58a4dcd7533c','643b0f83-e44f-4145-aaeb-48d879afd817',
 'd8e965c4-70cb-4e2c-9115-09c52fb3396a','2d65820f-57a0-49c8-a676-061f21351de7','69a692b2-9d2f-486c-b342-d520be771e56',
 '29cc74b9-8670-4991-ac02-d8f3a3ab7c11');
