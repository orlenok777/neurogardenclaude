-- НейроСад 2.0: база данных для игры под именем без регистрации.
-- Выполните этот файл целиком в Supabase: SQL Editor -> New query -> вставить -> Run.
-- Файл можно запускать повторно: он ничего не удаляет.
--
-- Как это устроено:
--   * Таблицы закрыты от прямого доступа (RLS включён, политик нет).
--   * Сайт работает только через функции ниже.
--   * Игрок опознаётся случайным ключом, который хранится в его браузере.
--   * Администратор опознаётся паролем; в базе лежит только его хеш.

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.ng_players (
  id uuid primary key default gen_random_uuid(),
  key_hash text not null,
  pid text not null,
  nick text not null default '',
  style int not null default 0,
  apples int not null default 0,
  flowers int not null default 0,
  waters int not null default 0,
  mon int not null default 0,
  mon_key text not null default '',
  hidden boolean not null default false,
  state jsonb not null default '{}'::jsonb,
  cheer jsonb,
  seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (key_hash, pid)
);
create index if not exists ng_players_seen on public.ng_players (seen_at);

create table if not exists public.ng_words (
  key_hash text primary key,
  id uuid not null default gen_random_uuid(),
  nick text not null,
  body text not null,
  at timestamptz not null default now()
);

create table if not exists public.ng_config (
  k text primary key,
  v text not null
);

alter table public.ng_players enable row level security;
alter table public.ng_words enable row level security;
alter table public.ng_config enable row level security;
revoke all on public.ng_players, public.ng_words, public.ng_config from anon, authenticated;

-- ---------- внутренние функции ----------

create or replace function public.ng_h(p text) returns text
language sql immutable set search_path = public, extensions as $$
  select encode(digest(coalesce(p, ''), 'sha256'), 'hex')
$$;

create or replace function public.ng_int(j jsonb, k text) returns int
language sql immutable set search_path = public as $$
  select case when jsonb_typeof(j -> k) = 'number'
    then least(greatest(floor((j ->> k)::numeric), 0), 1000000)::int else 0 end
$$;

create or replace function public.ng_admin_ok(p_pass text) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
declare h text;
begin
  select v into h from ng_config where k = 'admin';
  if h is null or p_pass is null or h <> crypt(p_pass, h) then
    perform pg_sleep(1);
    return false;
  end if;
  return true;
end $$;

create or replace function public.ng_meta(r public.ng_players) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('id', r.id, 'cheer', r.cheer,
    'online', (select count(*) from ng_players where seen_at > now() - interval '75 seconds'))
$$;

-- ---------- игрок ----------

create or replace function public.ng_load(p_key text) returns jsonb
language sql stable security definer set search_path = public, extensions as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'pid', pid, 'state', state, 'cheer', cheer) order by created_at), '[]'::jsonb)
  from ng_players where key_hash = ng_h(p_key) and length(coalesce(p_key, '')) >= 16
$$;

create or replace function public.ng_save(p_key text, p_pid text, p_state jsonb) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare h text := ng_h(p_key); r ng_players; n int; nk text;
begin
  if length(coalesce(p_key, '')) < 16 or length(coalesce(p_pid, '')) not between 1 and 40 then
    raise exception 'bad key';
  end if;
  if p_state is null or jsonb_typeof(p_state) <> 'object' or octet_length(p_state::text) > 80000 then
    raise exception 'bad state';
  end if;
  nk := case when jsonb_typeof(p_state -> 'nick') = 'string' then btrim(p_state ->> 'nick') else '' end;
  if length(nk) not between 1 and 24 then
    raise exception 'bad nick';
  end if;
  select count(*) into n from ng_players where key_hash = h and pid <> p_pid;
  if n >= 8 then
    raise exception 'too many names';
  end if;
  insert into ng_players (key_hash, pid, nick, style, apples, flowers, waters, mon, mon_key, hidden, state, seen_at)
  values (h, p_pid, nk, least(ng_int(p_state, 'style'), 3), ng_int(p_state, 'apples'), ng_int(p_state, 'flowers'),
          ng_int(p_state, 'waters'), ng_int(p_state, '_mon'),
          case when jsonb_typeof(p_state -> '_monKey') = 'string' then left(p_state ->> '_monKey', 7) else '' end,
          coalesce(jsonb_typeof(p_state -> 'hidden') = 'boolean' and (p_state ->> 'hidden')::boolean, false),
          p_state - '_mon' - '_monKey', now())
  on conflict (key_hash, pid) do update set
    nick = excluded.nick, style = excluded.style, apples = excluded.apples, flowers = excluded.flowers,
    waters = excluded.waters, mon = excluded.mon, mon_key = excluded.mon_key, hidden = excluded.hidden,
    state = excluded.state, seen_at = now()
  returning * into r;
  return ng_meta(r);
end $$;

create or replace function public.ng_ping(p_key text, p_pid text) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare r ng_players;
begin
  update ng_players set seen_at = now() where key_hash = ng_h(p_key) and pid = p_pid returning * into r;
  if r.id is null then return null; end if;
  return ng_meta(r);
end $$;

create or replace function public.ng_delete(p_key text, p_pid text) returns void
language sql security definer set search_path = public, extensions as $$
  delete from ng_players where key_hash = ng_h(p_key) and pid = p_pid
$$;

create or replace function public.ng_cheer_ack(p_key text, p_pid text) returns void
language sql security definer set search_path = public, extensions as $$
  update ng_players set cheer = null where key_hash = ng_h(p_key) and pid = p_pid
$$;

-- ---------- общий сад ----------

create or replace function public.ng_public() returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'nick', nick, 'style', style, 'apples', apples,
    'flowers', flowers, 'waters', waters, 'mon', mon, 'monKey', mon_key) order by apples desc, created_at), '[]'::jsonb)
  from (select * from ng_players where not hidden order by apples desc, created_at limit 500) t
$$;

create or replace function public.ng_words(p_key text) returns jsonb
language sql stable security definer set search_path = public, extensions as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'nick', nick, 'text', body,
    'at', (extract(epoch from at) * 1000)::bigint, 'mine', key_hash = ng_h(p_key)) order by at desc), '[]'::jsonb)
  from (select * from ng_words order by at desc limit 50) t
$$;

create or replace function public.ng_word(p_key text, p_nick text, p_text text) returns void
language plpgsql security definer set search_path = public, extensions as $$
declare h text := ng_h(p_key); t text := btrim(coalesce(p_text, '')); nk text := btrim(coalesce(p_nick, ''));
begin
  if length(t) not between 1 and 140 or length(nk) not between 1 and 24 then
    raise exception 'bad text';
  end if;
  if not exists (select 1 from ng_players where key_hash = h) then
    raise exception 'no tree';
  end if;
  insert into ng_words (key_hash, nick, body, at) values (h, nk, t, now())
  on conflict (key_hash) do update set nick = excluded.nick, body = excluded.body, at = now();
end $$;

create or replace function public.ng_word_del(p_key text) returns void
language sql security definer set search_path = public, extensions as $$
  delete from ng_words where key_hash = ng_h(p_key)
$$;

-- ---------- администратор ----------

create or replace function public.ng_admin_list(p_pass text) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not ng_admin_ok(p_pass) then raise exception 'forbidden'; end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'pid', pid, 'dev', left(key_hash, 10), 'state', state,
      'seen', (extract(epoch from seen_at) * 1000)::bigint,
      'age', floor(extract(epoch from now() - seen_at))::int) order by created_at), '[]'::jsonb)
    from ng_players);
end $$;

create or replace function public.ng_admin_delete(p_pass text, p_id uuid) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not ng_admin_ok(p_pass) then raise exception 'forbidden'; end if;
  delete from ng_players where id = p_id;
end $$;

create or replace function public.ng_admin_cheer(p_pass text, p_id uuid, p_text text) returns void
language plpgsql security definer set search_path = public, extensions as $$
declare t text := btrim(coalesce(p_text, ''));
begin
  if not ng_admin_ok(p_pass) then raise exception 'forbidden'; end if;
  if length(t) not between 1 and 140 then raise exception 'bad text'; end if;
  update ng_players set cheer = jsonb_build_object('text', t, 'at', (extract(epoch from now()) * 1000)::bigint) where id = p_id;
end $$;

create or replace function public.ng_admin_word_del(p_pass text, p_id uuid) returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not ng_admin_ok(p_pass) then raise exception 'forbidden'; end if;
  delete from ng_words where id = p_id;
end $$;

-- ---------- права ----------

revoke execute on function
  public.ng_h(text), public.ng_int(jsonb, text), public.ng_admin_ok(text), public.ng_meta(public.ng_players),
  public.ng_load(text), public.ng_save(text, text, jsonb), public.ng_ping(text, text), public.ng_delete(text, text),
  public.ng_cheer_ack(text, text), public.ng_public(), public.ng_words(text), public.ng_word(text, text, text),
  public.ng_word_del(text), public.ng_admin_list(text), public.ng_admin_delete(text, uuid),
  public.ng_admin_cheer(text, uuid, text), public.ng_admin_word_del(text, uuid)
from public, anon, authenticated;

grant execute on function
  public.ng_load(text), public.ng_save(text, text, jsonb), public.ng_ping(text, text), public.ng_delete(text, text),
  public.ng_cheer_ack(text, text), public.ng_public(), public.ng_words(text), public.ng_word(text, text, text),
  public.ng_word_del(text), public.ng_admin_list(text), public.ng_admin_delete(text, uuid),
  public.ng_admin_cheer(text, uuid, text), public.ng_admin_word_del(text, uuid)
to anon, authenticated;

notify pgrst, 'reload schema';
