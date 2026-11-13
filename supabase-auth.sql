-- НейроСад: схема 3. Вход владельца в «Панель Ромы» через Supabase Auth (Google).
-- Применяется ОДИН раз поверх схемы 2 (supabase.sql). Ничего не удаляет и не меняет данные игроков.
-- Повторный запуск безопасен.
--
-- Перед применением исполнитель может убедиться, что владелец функций читает таблицу сессий:
--   select has_table_privilege(current_user, 'auth.sessions', 'select');   -- ожидается true
-- Если прав нет, функции панели будут отвечать ошибкой, то есть панель останется закрытой.
--
-- Что меняется:
--   * Новая таблица ng_owner хранит UID единственного владельца. Пока она пуста, панель закрыта для всех.
--   * Новые функции ng_owner_* доступны только вошедшим пользователям (роль authenticated) и
--     выполняют действие, только если auth.uid() совпадает с UID владельца. Проверка идёт на сервере
--     по подписанному токену Supabase; почта и любые флаги из браузера не учитываются.
--   * Действие выполняется только пока сессия входа жива: при выходе Supabase удаляет строку auth.sessions,
--     и уже выданный токен перестаёт давать доступ к панели, не дожидаясь истечения своего срока.
--   * Старый вход по паролю (ng_admin_*) этой миграцией НЕ меняется: функции, запись о пароле и таблица
--     блокировок ng_auth остаются как были, действующая блокировка продолжает действовать. Новый вход с ней
--     не связан и её не обходит: он не принимает паролей вообще. Отключение парольного входа вынесено в
--     отдельный файл supabase-retire-password.sql и применяется только по отдельному решению.
--   * Поток игроков (ng_save, ng_load, общий сад, добрые слова) не меняется.
--
-- Владелец назначается отдельно, файлом supabase-owner.sql, после его подтверждённого личного входа.

create table if not exists public.ng_owner (
  one boolean primary key default true check (one),   -- не больше одной строки
  uid uuid not null unique,
  added_at timestamptz not null default now()
);
alter table public.ng_owner enable row level security;
revoke all on public.ng_owner from public, anon, authenticated;

-- Внутренняя проверка: сессия из токена существует в auth.sessions, принадлежит этому пользователю и не истекла.
-- Подписанный токен сам по себе действует до конца своего срока даже после выхода; эта проверка закрывает
-- такой токен сразу, как только сессия удалена (выход, «выйти везде», удаление пользователя).
create or replace function public.ng_session_live() returns boolean
language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null
     and exists (select 1 from auth.sessions s
                 where s.id::text = (auth.jwt() ->> 'session_id')
                   and s.user_id = auth.uid()
                   and (s.not_after is null or s.not_after > now()))
$$;

-- Внутренняя проверка: вошёл не анонимно, сессия жива, UID записан как владелец.
create or replace function public.ng_is_owner() returns boolean
language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null
     and (auth.jwt() ->> 'is_anonymous') is distinct from 'true'
     and public.ng_session_live()
     and exists (select 1 from public.ng_owner o where o.uid = auth.uid())
$$;

-- Кто я для панели. Не раскрывает, кто владелец; сообщает, жива ли сессия и назначен ли владелец вообще.
create or replace function public.ng_owner_whoami() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('ok', true, 'session', public.ng_session_live(), 'owner', public.ng_is_owner(),
                            'assigned', exists (select 1 from public.ng_owner))
$$;

create or replace function public.ng_owner_list() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  return jsonb_build_object('ok', true, 'rows',
    (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'pid', pid, 'dev', left(key_hash, 10), 'state', state,
        'seen', (extract(epoch from seen_at) * 1000)::bigint,
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
  end if;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_owner_cheer(p_id uuid, p_text text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare t text := btrim(coalesce(p_text, ''));
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  if length(t) not between 1 and 140 then return jsonb_build_object('ok', false, 'error', 'bad text'); end if;
  update public.ng_players set cheer = jsonb_build_object('text', t, 'at', (extract(epoch from now()) * 1000)::bigint) where id = p_id;
  return jsonb_build_object('ok', true);
end $$;

create or replace function public.ng_owner_word_del(p_id uuid) returns jsonb
language plpgsql security definer set search_path = '' as $$
begin
  if not public.ng_is_owner() then return jsonb_build_object('ok', false, 'error', 'forbidden'); end if;
  delete from public.ng_words where id = p_id;
  return jsonb_build_object('ok', true);
end $$;

-- ---------- права ----------

revoke execute on function
  public.ng_session_live(), public.ng_is_owner(), public.ng_owner_whoami(), public.ng_owner_list(), public.ng_owner_delete(uuid),
  public.ng_owner_cheer(uuid, text), public.ng_owner_word_del(uuid)
from public, anon, authenticated;

-- Только вошедшие пользователи могут вызвать функции панели; права владельца проверяются внутри.
grant execute on function
  public.ng_owner_whoami(), public.ng_owner_list(), public.ng_owner_delete(uuid),
  public.ng_owner_cheer(uuid, text), public.ng_owner_word_del(uuid)
to authenticated;

notify pgrst, 'reload schema';
