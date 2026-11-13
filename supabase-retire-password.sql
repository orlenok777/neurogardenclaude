-- НейроСад: отключение старого входа в панель по паролю. НЕ входит в схему 3.
-- Применять только по отдельному согласованному решению Ромы, одним исполнителем, после того как
-- вход владельца через Google проверен на настоящем проекте.
--
-- Что делает: закрывает функции ng_admin_* для внешних вызовов. Сами функции, запись о пароле в ng_config
-- и таблица блокировок ng_auth не удаляются и не изменяются.
-- Откат: тот же список функций с командой grant execute ... to anon, authenticated.

revoke execute on function
  public.ng_admin_list(text), public.ng_admin_delete(text, uuid),
  public.ng_admin_cheer(text, uuid, text), public.ng_admin_word_del(text, uuid)
from public, anon, authenticated;

notify pgrst, 'reload schema';
