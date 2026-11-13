-- НейроСад 2.0: база данных для игры под именем без регистрации. Версия схемы: 2.
-- Выполните этот файл целиком в Supabase: SQL Editor -> New query -> вставить -> Run.
-- Файл рассчитан на новый пустой проект. Повторный запуск данные не удаляет.
--
-- Как это устроено:
--   * Таблицы закрыты от прямого доступа (RLS включён, политик нет, права отозваны).
--   * Сайт работает только через функции ниже (SECURITY DEFINER, пустой search_path, все имена со схемой).
--   * Игрок опознаётся случайным ключом, который хранится в его браузере; в базе лежит только хеш ключа.
--   * Администратор опознаётся паролем; в базе лежит только его bcrypt-хеш.
--     После 5 неверных паролей с одного адреса вход с этого адреса закрывается на 15 минут.
--   * Дерево и история раундов хранятся в ng_players.state (jsonb), ободрение в ng_players.cheer,
--     добрые слова в ng_words. Удалённые имена помнит ng_gone, чтобы старое сохранение их не вернуло.

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
  rev int not null default 1,
  seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (key_hash, pid)
);
create index if not exists ng_players_seen on public.ng_players (seen_at);

create table if not exists public.ng_gone (
  key_hash text not null,
  pid text not null,
  at timestamptz not null default now(),
  primary key (key_hash, pid)
);

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

create table if not exists public.ng_auth (
  ip text primary key,
  fails int not null default 0,
  first_at timestamptz not null default now(),
  locked_until timestamptz
);

alter table public.ng_players enable row level security;
alter table public.ng_gone enable row level security;
alter table public.ng_words enable row level security;
alter table public.ng_config enable row level security;
alter table public.ng_auth enable row level security;
revoke all on public.ng_players, public.ng_gone, public.ng_words, public.ng_config, public.ng_auth from public, anon, authenticated;

-- Функции версии 1 с другими подписями (на новом проекте их нет).
drop function if exists public.ng_save(text, text, jsonb);
drop function if exists public.ng_admin_ok(text);
drop function if exists public.ng_admin_list(text);
drop function if exists public.ng_admin_delete(text, uuid);
drop function if exists public.ng_admin_cheer(text, uuid, text);
drop function if exists public.ng_admin_word_del(text, uuid);

-- ---------- внутренние функции (снаружи недоступны) ----------

create or replace function public.ng_h(p text) returns text
language sql immutable set search_path = '' as $$
  select encode(extensions.digest(coalesce(p, ''), 'sha256'), 'hex')
$$;

create or replace function public.ng_int(j jsonb, k text) returns int
language sql immutable set search_path = '' as $$
  select case when jsonb_typeof(j -> k) = 'number'
    then least(greatest(floor((j ->> k)::numeric), 0), 1000000)::int else 0 end
$$;

-- Возвращает 'ok', 'forbidden' или 'locked'. Не бросает исключений: счётчик попыток должен сохраниться.
create or replace function public.ng_admin_check(p_pass text) returns text
language plpgsql security definer set search_path = '' as $$
declare
  h text; hdr json; addr text; a public.ng_auth; good boolean;
begin
  begin
    hdr := nullif(current_setting('request.headers', true), '')::json;
  exception when others then
    hdr := null;
  end;
  addr := coalesce(nullif(btrim(hdr ->> 'cf-connecting-ip'), ''),
                   nullif(btrim(reverse(split_part(reverse(coalesce(hdr ->> 'x-forwarded-for', '')), ',', 1))), ''),
                   '-');
  addr := left(addr, 64);
  insert into public.ng_auth (ip) values (addr) on conflict (ip) do nothing;
  select * into a from public.ng_auth where ip = addr for update;
  if a.locked_until is not null and a.locked_until > now() then
    return 'locked';
  end if;
  select v into h from public.ng_config where k = 'admin';
  good := h is not null and p_pass is not null and length(p_pass) between 1 and 200 and h = extensions.crypt(p_pass, h);
  if good then
    delete from public.ng_auth where ip = addr;
    return 'ok';
  end if;
  if a.first_at < now() - interval '15 minutes' then
    update public.ng_auth set fails = 1, first_at = now(), locked_until = null where ip = addr;
  elsif a.fails + 1 >= 5 then
    update public.ng_auth set fails = 0, first_at = now(), locked_until = now() + interval '15 minutes' where ip = addr;
  else
    update public.ng_auth set fails = a.fails + 1 where ip = addr;
  end if;
  delete from public.ng_auth where first_at < now() - interval '1 day' and (locked_until is null or locked_until < now());
  return 'forbidden';
end $$;

create or replace function public.ng_meta(r public.ng_players) returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('id', r.id, 'rev', r.rev, 'cheer', r.cheer,
    'online', (select count(*) from public.ng_players where seen_at > now() - interval '75 seconds'))
$$;

-- ---------- игрок ----------

create or replace function public.ng_load(p_key text) returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'pid', pid, 'rev', rev, 'state', state, 'cheer', cheer) order by created_at), '[]'::jsonb)
  from public.ng_players where key_hash = public.ng_h(p_key) and length(coalesce(p_key, '')) >= 32
$$;

-- p_rev: версия строки, которую браузер видел последней (null для нового имени).
-- Ответы: {id, rev, cheer, online} при успехе; {conflict:true, state, rev}, если строка изменилась в другой вкладке;
-- {gone:true}, если имя было удалено.
create or replace function public.ng_save(p_key text, p_pid text, p_state jsonb, p_rev int default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  h text := public.ng_h(p_key); r public.ng_players; n bigint; nk text; mk text; hid boolean; st jsonb;
begin
  if length(coalesce(p_key, '')) not between 32 and 128 or coalesce(p_pid, '') !~ '^[A-Za-z0-9_-]{1,40}$' then
    raise exception 'bad key';
  end if;
  if p_state is null or jsonb_typeof(p_state) <> 'object' or octet_length(p_state::text) > 80000 then
    raise exception 'bad state';
  end if;
  nk := case when jsonb_typeof(p_state -> 'nick') = 'string' then btrim(p_state ->> 'nick') else '' end;
  if length(nk) not between 1 and 24 then
    raise exception 'bad nick';
  end if;
  if p_state ? 'hist' then
    if jsonb_typeof(p_state -> 'hist') <> 'array' or jsonb_array_length(p_state -> 'hist') > 200 then
      raise exception 'bad hist';
    end if;
    if exists (select 1 from jsonb_array_elements(p_state -> 'hist') e
               where jsonb_typeof(e) is distinct from 'object' or jsonb_typeof(e -> 'at') is distinct from 'number'
                  or jsonb_typeof(e -> 'g') is distinct from 'string') then
      raise exception 'bad hist';
    end if;
  end if;
  if exists (select 1 from public.ng_gone where key_hash = h and pid = p_pid) then
    return jsonb_build_object('gone', true);
  end if;
  mk := case when jsonb_typeof(p_state -> '_monKey') = 'string' then left(p_state ->> '_monKey', 7) else '' end;
  hid := case when jsonb_typeof(p_state -> 'hidden') = 'boolean' then (p_state ->> 'hidden')::boolean else false end;
  st := p_state - '_mon' - '_monKey';

  select * into r from public.ng_players where key_hash = h and pid = p_pid for update;
  if found then
    if p_rev is null or p_rev <> r.rev then
      return jsonb_build_object('conflict', true, 'state', r.state, 'rev', r.rev);
    end if;
    update public.ng_players set
      nick = nk, style = least(public.ng_int(p_state, 'style'), 3), apples = public.ng_int(p_state, 'apples'),
      flowers = public.ng_int(p_state, 'flowers'), waters = public.ng_int(p_state, 'waters'),
      mon = public.ng_int(p_state, '_mon'), mon_key = mk, hidden = hid, state = st, rev = r.rev + 1, seen_at = now()
    where id = r.id returning * into r;
    return public.ng_meta(r);
  end if;

  select count(*) into n from public.ng_players where key_hash = h;
  if n >= 8 then
    raise exception 'too many names';
  end if;
  select count(*) into n from public.ng_players;
  if n >= 20000 then
    raise exception 'garden is full';
  end if;
  insert into public.ng_players (key_hash, pid, nick, style, apples, flowers, waters, mon, mon_key, hidden, state)
  values (h, p_pid, nk, least(public.ng_int(p_state, 'style'), 3), public.ng_int(p_state, 'apples'),
          public.ng_int(p_state, 'flowers'), public.ng_int(p_state, 'waters'), public.ng_int(p_state, '_mon'), mk, hid, st)
  on conflict (key_hash, pid) do nothing
  returning * into r;
  if r.id is null then
    select * into r from public.ng_players where key_hash = h and pid = p_pid;
    return jsonb_build_object('conflict', true, 'state', r.state, 'rev', r.rev);
  end if;
  return public.ng_meta(r);
end $$;

create or replace function public.ng_ping(p_key text, p_pid text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r public.ng_players;
begin
  update public.ng_players set seen_at = now() where key_hash = public.ng_h(p_key) and pid = p_pid returning * into r;
  if r.id is null then return null; end if;
  return public.ng_meta(r);
end $$;

create or replace function public.ng_delete(p_key text, p_pid text) returns void
language plpgsql security definer set search_path = '' as $$
declare h text := public.ng_h(p_key);
begin
  delete from public.ng_players where key_hash = h and pid = p_pid;
  if found then
    insert into public.ng_gone (key_hash, pid) values (h, p_pid) on conflict do nothing;
  end if;
end $$;

create or replace function public.ng_cheer_ack(p_key text, p_pid text) returns void
language sql security definer set search_path = '' as $$
  update public.ng_players set cheer = null where key_hash = public.ng_h(p_key) and pid = p_pid
$$;

-- ---------- общий сад ----------

create or replace function public.ng_public() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'nick', nick, 'style', style, 'apples', apples,
    'flowers', flowers, 'waters', waters, 'mon', mon, 'monKey', mon_key) order by apples desc, created_at), '[]'::jsonb)
  from (select * from public.ng_players where not hidden order by apples desc, created_at limit 500) t
$$;

create or replace function public.ng_words(p_key text) returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'nick', nick, 'text', body,
    'at', (extract(epoch from at) * 1000)::bigint, 'mine', key_hash = public.ng_h(p_key)) order by at desc), '[]'::jsonb)
  from (select * from public.ng_words order by at desc limit 50) t
$$;

create or replace function public.ng_word(p_key text, p_nick text, p_text text) returns void
language plpgsql security definer set search_path = '' as $$
declare h text := public.ng_h(p_key); t text := btrim(coalesce(p_text, '')); nk text := btrim(coalesce(p_nick, ''));
begin
  if length(t) not between 1 and 140 or length(nk) not between 1 and 24 then
    raise exception 'bad text';
  end if;
  if not exists (select 1 from public.ng_players where key_hash = h) then
    raise exception 'no tree';
  end if;
  insert into public.ng_words (key_hash, nick, body, at) values (h, nk, t, now())
  on conflict (key_hash) do update set nick = excluded.nick, body = excluded.body, at = now();
end $$;

create or replace function public.ng_word_del(p_key text) returns void
language sql security definer set search_path = '' as $$
  delete from public.ng_words where key_hash = public.ng_h(p_key)
$$;

-- ---------- администратор ----------
-- Все функции отвечают {ok:true, ...} или {ok:false, error:'forbidden'|'locked'|...} и не бросают исключений при неверном пароле.

create or replace function public.ng_admin_list(p_pass text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c text := public.ng_admin_check(p_pass);
begin
  if c <> 'ok' then return jsonb_build_object('ok', false, 'error', c); end if;
  return jsonb_build_object('ok', true, 'rows',
    (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'pid', pid, 'dev', left(key_hash, 10), 'state', state,
        'seen', (extract(epoch from seen_at) * 1000)::bigint,
        'age', floor(extract(epoch from now() - seen_at))::int) order by created_at), '[]'::jsonb)
      from public.ng_players));
end $$;

create or replace function public.ng_admin_delete(p_pass text, p_id uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c text := public.ng_admin_check(p_pass); r public.ng_players;
begin
  if c <> 'ok' then return jsonb_build_object('ok', false, 'error', c); end if;
  delete from public.ng_players where id = p_id returning * into r;
  if r.id is not null then
    insert into public.ng_gone (key_hash, pid) values (r.key_hash, r.pid) on conflict do nothing;
  end if;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_admin_cheer(p_pass text, p_id uuid, p_text text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c text := public.ng_admin_check(p_pass); t text := btrim(coalesce(p_text, ''));
begin
  if c <> 'ok' then return jsonb_build_object('ok', false, 'error', c); end if;
  if length(t) not between 1 and 140 then return jsonb_build_object('ok', false, 'error', 'bad text'); end if;
  update public.ng_players set cheer = jsonb_build_object('text', t, 'at', (extract(epoch from now()) * 1000)::bigint) where id = p_id;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_admin_word_del(p_pass text, p_id uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare c text := public.ng_admin_check(p_pass);
begin
  if c <> 'ok' then return jsonb_build_object('ok', false, 'error', c); end if;
  delete from public.ng_words where id = p_id;
  return jsonb_build_object('ok', true);
end $$;

-- ---------- права ----------

revoke execute on function
  public.ng_h(text), public.ng_int(jsonb, text), public.ng_admin_check(text), public.ng_meta(public.ng_players),
  public.ng_load(text), public.ng_save(text, text, jsonb, int), public.ng_ping(text, text), public.ng_delete(text, text),
  public.ng_cheer_ack(text, text), public.ng_public(), public.ng_words(text), public.ng_word(text, text, text),
  public.ng_word_del(text), public.ng_admin_list(text), public.ng_admin_delete(text, uuid),
  public.ng_admin_cheer(text, uuid, text), public.ng_admin_word_del(text, uuid)
from public, anon, authenticated;

grant execute on function
  public.ng_load(text), public.ng_save(text, text, jsonb, int), public.ng_ping(text, text), public.ng_delete(text, text),
  public.ng_cheer_ack(text, text), public.ng_public(), public.ng_words(text), public.ng_word(text, text, text),
  public.ng_word_del(text), public.ng_admin_list(text), public.ng_admin_delete(text, uuid),
  public.ng_admin_cheer(text, uuid, text), public.ng_admin_word_del(text, uuid)
to anon, authenticated;

notify pgrst, 'reload schema';
