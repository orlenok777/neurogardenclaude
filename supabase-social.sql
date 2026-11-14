-- Explicit tree sharing. PREPARED ONLY: do not run against production without a separate release decision.
-- Requires schema 2; compatible with schemas 3 and 4. No existing player data is rewritten.
begin;
create table if not exists public.ng_garden_consent (
  player_id uuid primary key references public.ng_players(id) on delete cascade,
  shared boolean not null default false
);
alter table public.ng_garden_consent enable row level security;
revoke all on public.ng_garden_consent from public, anon, authenticated;

create or replace function public.ng_garden_status(p_key text, p_pid text) returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('ok', true, 'shared', coalesce((
    select c.shared from public.ng_players p join public.ng_garden_consent c on c.player_id=p.id
    where p.key_hash=public.ng_h(p_key) and p.pid=p_pid), false))
$$;

create or replace function public.ng_garden_share(p_key text, p_pid text, p_shared boolean) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare target uuid;
begin
  if p_shared is null then return jsonb_build_object('ok', false, 'error', 'invalid'); end if;
  select id into target from public.ng_players where key_hash=public.ng_h(p_key) and pid=p_pid;
  if target is null then return jsonb_build_object('ok', false, 'error', 'not_found'); end if;
  insert into public.ng_garden_consent(player_id,shared) values(target,p_shared)
    on conflict(player_id) do update set shared=excluded.shared;
  return jsonb_build_object('ok', true, 'shared', p_shared);
end $$;

-- Gate the EXISTING public RPC too, so older clients cannot bypass consent.
-- Default false: old hidden=false and ordinary ng_save payloads never count as consent.
create or replace function public.ng_public() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'nick', nick, 'style', style,
    'apples', apples, 'flowers', flowers, 'waters', waters, 'mon', mon, 'monKey', mon_key)
    order by apples desc, created_at), '[]'::jsonb)
  from (select p.* from public.ng_players p join public.ng_garden_consent c on c.player_id=p.id
    where c.shared and not p.hidden order by p.apples desc,p.created_at limit 500) t
$$;

revoke execute on function public.ng_garden_status(text,text),public.ng_garden_share(text,text,boolean)
  from public,anon,authenticated;
grant execute on function public.ng_garden_status(text,text),public.ng_garden_share(text,text,boolean)
  to anon,authenticated;
notify pgrst, 'reload schema';
commit;
