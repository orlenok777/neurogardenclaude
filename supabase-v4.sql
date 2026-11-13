-- НейроСад: схема 4. Серверная проверка урожая, журнал действий в «Панели Ромы», дата посадки в панели.
-- Применяется ОДИН раз поверх схем 2 (supabase.sql) и 3 (supabase-auth.sql). Повторный запуск безопасен.
-- Ничего не удаляет и не меняет существующие данные игроков: числа в уже сохранённых строках остаются как были.
--
-- Что меняется:
--   * ng_save (та же подпись и те же ответы) теперь сам проверяет урожай. Каждый раунд опознаётся
--     неповторимым ключом «время начала | игра». Число спелых яблок может вырасти только на число раундов,
--     которые пришли завершёнными (результат 60% и выше) и которых сервер ещё не видел завершёнными.
--     Повторная отправка того же раунда, перезагрузка страницы и вторая вкладка яблок не добавляют.
--     Лишнее не отклоняется, а срезается до допустимого: игрок ничего не теряет, запись попадает в журнал.
--     В журнале это запись clamp с пометкой «прислано>принято».
--     Для нового имени яблок не больше, чем завершённых раундов в присланной истории.
--     Цветы и поливы: не больше одного в сутки с небольшим запасом на смену даты.
--   * Новая таблица ng_log: журнал. В неё пишутся действия владельца (ободрение, удаление участника,
--     удаление пожелания) и срезанные сохранения. Хранятся последние 2000 записей.
--   * ng_owner_list дополнительно отдаёт дату посадки дерева (created).
--   * Новая функция ng_owner_log: журнал для панели, только владельцу с живой сессией.
--   * Вход владельца, парольные функции ng_admin_*, общий сад и добрые слова не меняются.
--
-- Сам результат раунда по-прежнему считает браузер игрока: сервер не играет в игру и не может проверить,
-- что ответы были верными. Эта миграция закрывает подделку счётчиков и повторное начисление, а не подделку
-- самого результата раунда.

create table if not exists public.ng_log (
  id bigint generated always as identity primary key,
  at timestamptz not null default now(),
  act text not null,
  target uuid,
  nick text not null default '',
  note text not null default ''
);
alter table public.ng_log enable row level security;
revoke all on public.ng_log from public, anon, authenticated;

-- ---------- внутренние функции (снаружи недоступны) ----------

create or replace function public.ng_log_add(p_act text, p_target uuid, p_nick text, p_note text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.ng_log (act, target, nick, note)
  values (left(p_act, 20), p_target, left(coalesce(p_nick, ''), 24), left(coalesce(p_note, ''), 200));
  delete from public.ng_log where id <= (select max(id) from public.ng_log) - 2000;
end $$;

-- Ключи раундов из истории: p_only_ok = true — только завершённые с результатом 60% и выше (спелое яблоко),
-- false — все завершённые (есть числовой результат).
create or replace function public.ng_round_keys(p_state jsonb, p_only_ok boolean) returns setof text
language sql immutable set search_path = '' as $$
  select distinct (e ->> 'at') || '|' || (e ->> 'g')
  from jsonb_array_elements(case when jsonb_typeof(p_state -> 'hist') = 'array' then p_state -> 'hist' else '[]'::jsonb end) e
  where jsonb_typeof(e) = 'object' and jsonb_typeof(e -> 's') = 'number'
    and (not p_only_ok or (e -> 'ok' = 'true'::jsonb and (e ->> 's')::numeric >= 60))
$$;

-- ---------- игрок ----------

create or replace function public.ng_save(p_key text, p_pid text, p_state jsonb, p_rev int default null) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  h text := public.ng_h(p_key); r public.ng_players; n bigint; nk text; mk text; hid boolean; st jsonb;
  ap int; fl int; wa int; mo int; ap0 int; fresh int; days int;
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
  ap0 := public.ng_int(p_state, 'apples'); ap := ap0;
  fl := public.ng_int(p_state, 'flowers'); wa := public.ng_int(p_state, 'waters'); mo := public.ng_int(p_state, '_mon');

  select * into r from public.ng_players where key_hash = h and pid = p_pid for update;
  if found then
    if p_rev is null or p_rev <> r.rev then
      return jsonb_build_object('conflict', true, 'state', r.state, 'rev', r.rev);
    end if;
    -- спелые раунды, которых сервер ещё не видел завершёнными
    select count(*) into fresh from public.ng_round_keys(p_state, true) k
     where k not in (select public.ng_round_keys(r.state, false));
    days := greatest(0, floor(extract(epoch from now() - r.seen_at) / 86400))::int;
    ap := least(ap, r.apples + fresh);
    fl := least(fl, r.flowers + days + 2);
    wa := least(wa, r.waters + days + 2);
    mo := least(mo, ap);
    st := (p_state - '_mon' - '_monKey') || jsonb_build_object('apples', ap, 'flowers', fl, 'waters', wa);
    update public.ng_players set
      nick = nk, style = least(public.ng_int(p_state, 'style'), 3), apples = ap, flowers = fl, waters = wa,
      mon = mo, mon_key = mk, hidden = hid, state = st, rev = r.rev + 1, seen_at = now()
    where id = r.id returning * into r;
    if ap < ap0 then
      perform public.ng_log_add('clamp', r.id, nk, ap0 || '>' || ap);
    end if;
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
  select count(*) into fresh from public.ng_round_keys(p_state, true);
  ap := least(ap, fresh); fl := least(fl, 31); wa := least(wa, 31); mo := least(mo, ap);
  st := (p_state - '_mon' - '_monKey') || jsonb_build_object('apples', ap, 'flowers', fl, 'waters', wa);
  insert into public.ng_players (key_hash, pid, nick, style, apples, flowers, waters, mon, mon_key, hidden, state)
  values (h, p_pid, nk, least(public.ng_int(p_state, 'style'), 3), ap, fl, wa, mo, mk, hid, st)
  on conflict (key_hash, pid) do nothing
  returning * into r;
  if r.id is null then
    select * into r from public.ng_players where key_hash = h and pid = p_pid;
    return jsonb_build_object('conflict', true, 'state', r.state, 'rev', r.rev);
  end if;
  if ap < ap0 then
    perform public.ng_log_add('clamp', r.id, nk, ap0 || '>' || ap);
  end if;
  return public.ng_meta(r);
end $$;

-- ---------- владелец ----------

create or replace function public.ng_owner_list() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  return jsonb_build_object('ok', true, 'rows',
    (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'pid', pid, 'dev', left(key_hash, 10), 'state', state,
        'seen', (extract(epoch from seen_at) * 1000)::bigint,
        'created', (extract(epoch from created_at) * 1000)::bigint,
        'age', floor(extract(epoch from now() - seen_at))::int) order by created_at), '[]'::jsonb)
      from public.ng_players));
end $$;

create or replace function public.ng_owner_delete(p_id uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare r public.ng_players;
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  delete from public.ng_players where id = p_id returning * into r;
  if r.id is not null then
    insert into public.ng_gone (key_hash, pid) values (r.key_hash, r.pid) on conflict do nothing;
    perform public.ng_log_add('delete', r.id, r.nick, r.apples::text);
  end if;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_owner_cheer(p_id uuid, p_text text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare t text := btrim(coalesce(p_text, '')); r public.ng_players;
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  if length(t) not between 1 and 140 then return jsonb_build_object('ok', false, 'error', 'bad text'); end if;
  update public.ng_players set cheer = jsonb_build_object('text', t, 'at', (extract(epoch from now()) * 1000)::bigint)
   where id = p_id returning * into r;
  if r.id is not null then
    perform public.ng_log_add('cheer', r.id, r.nick, t);
  end if;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_owner_word_del(p_id uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare w public.ng_words;
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  delete from public.ng_words where id = p_id returning * into w;
  if w.id is not null then
    perform public.ng_log_add('word_del', null, w.nick, w.body);
  end if;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_owner_log() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  return jsonb_build_object('ok', true, 'rows',
    (select coalesce(jsonb_agg(jsonb_build_object('at', (extract(epoch from at) * 1000)::bigint, 'act', act,
        'nick', nick, 'note', note) order by id desc), '[]'::jsonb)
      from (select * from public.ng_log order by id desc limit 200) t));
end $$;

-- ---------- права ----------

revoke execute on function
  public.ng_log_add(text, uuid, text, text), public.ng_round_keys(jsonb, boolean),
  public.ng_save(text, text, jsonb, int), public.ng_owner_list(), public.ng_owner_delete(uuid),
  public.ng_owner_cheer(uuid, text), public.ng_owner_word_del(uuid), public.ng_owner_log()
from public, anon, authenticated;

grant execute on function public.ng_save(text, text, jsonb, int) to anon, authenticated;

grant execute on function
  public.ng_owner_list(), public.ng_owner_delete(uuid), public.ng_owner_cheer(uuid, text),
  public.ng_owner_word_del(uuid), public.ng_owner_log()
to authenticated;

notify pgrst, 'reload schema';
