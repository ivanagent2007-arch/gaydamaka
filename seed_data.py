"""
Seed-данные: группы и состав участников.

Используется двумя способами:
  1. Автоматически при старте бота (вызывается из main.py после init_db).
     INSERT OR IGNORE — повторный запуск безопасен, дубликатов не создаёт.
  2. Вручную: railway run python seed_data.py
"""
import asyncio
import logging
from sqlalchemy import text

log = logging.getLogger(__name__)

GROUPS = [
    {'id': 1, 'name': 'БИ25-2', 'join_code': 'QCZXVPSG', 'ruz_group_search': 'БИ25-2', 'creator_user_id': 1, 'semester_number': 1, 'corporate_email': 'bi25-2@yandex.ru', 'imap_password': 'ogkpnjcromnxnqjm', 'ruz_base_url': None, 'org_cookies': None},
    {'id': 2, 'name': '18-ла', 'join_code': 'RQ61EDX6', 'ruz_group_search': None, 'creator_user_id': None, 'semester_number': 1, 'corporate_email': None, 'imap_password': None, 'ruz_base_url': None, 'org_cookies': None},
]

USERS = [
    {'id': 1, 'telegram_id': 760348254, 'full_name': 'эщкерескибидипапа', 'group_name': 'БИ25-2', 'role': 'elder', 'study_group_id': 1, 'birthday_month': 7, 'birthday_day': 26, 'birth_year': 2007},
    {'id': 2, 'telegram_id': 850353673, 'full_name': 'Богдан', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 3, 'telegram_id': 5849054344, 'full_name': 'E', 'group_name': 'ПИ19-5', 'role': 'student', 'study_group_id': None, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 4, 'telegram_id': 1995355624, 'full_name': 'kisel', 'group_name': 'ПИ19-5', 'role': 'student', 'study_group_id': None, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 5, 'telegram_id': 1140031955, 'full_name': 'Iku', 'group_name': 'ПИ19-5', 'role': 'student', 'study_group_id': None, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 6, 'telegram_id': 5699774431, 'full_name': 'Григорий', 'group_name': '', 'role': 'student', 'study_group_id': None, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 7, 'telegram_id': 9990000000, 'full_name': 'Абдыкааров Эмир Бахтиярович', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 8, 'telegram_id': 9990000001, 'full_name': 'Роман', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 9, 'telegram_id': 9990000002, 'full_name': 'Иван Вершинин', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 10, 'telegram_id': 9990000003, 'full_name': 'Илья Волков', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 11, 'telegram_id': 9990000004, 'full_name': 'Арсений', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 12, 'telegram_id': 9990000005, 'full_name': 'Ираклий', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 13, 'telegram_id': 9990000006, 'full_name': 'Никита', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 14, 'telegram_id': 9990000007, 'full_name': 'Евгений', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 15, 'telegram_id': 9990000008, 'full_name': 'mariya_off', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 16, 'telegram_id': 9990000009, 'full_name': 'Матвей Иванов', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 17, 'telegram_id': 9990000010, 'full_name': 'Владислав Киселёв', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 18, 'telegram_id': 9990000011, 'full_name': 'Тимофей Козулин', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 19, 'telegram_id': 9990000012, 'full_name': 'Владислав', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 20, 'telegram_id': 9990000013, 'full_name': 'kirill_k', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 21, 'telegram_id': 9990000014, 'full_name': 'Лелеков Роман Дмитриевич', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 22, 'telegram_id': 9990000015, 'full_name': 'Богдан Мавлютов', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 23, 'telegram_id': 9990000016, 'full_name': 'Даниил Меркулов', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 24, 'telegram_id': 9990000017, 'full_name': 'Новиков Арсений Валерьевич', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 25, 'telegram_id': 9990000018, 'full_name': 'vadim_p', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 26, 'telegram_id': 9990000019, 'full_name': 'Захар', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 27, 'telegram_id': 9990000020, 'full_name': 'Александр Плосков', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 28, 'telegram_id': 9990000021, 'full_name': 'Алексей Рязанов', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 29, 'telegram_id': 9990000022, 'full_name': 'Екатерина', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 30, 'telegram_id': 9990000023, 'full_name': 'Маргарита', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 31, 'telegram_id': 9990000024, 'full_name': 'Турукин Арсений Сергеевич', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 32, 'telegram_id': 9990000025, 'full_name': 'Минь Чан', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 33, 'telegram_id': 9990000026, 'full_name': 'polina77', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 34, 'telegram_id': 9990000027, 'full_name': 'Хань', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 35, 'telegram_id': 9990000028, 'full_name': 'Ярослав', 'group_name': 'БИ25-2', 'role': 'student', 'study_group_id': 1, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 36, 'telegram_id': 9990000029, 'full_name': 'София', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 37, 'telegram_id': 9990000030, 'full_name': '@oliviya_kiseleva', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 38, 'telegram_id': 9990000031, 'full_name': 'Ксения', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 39, 'telegram_id': 9990000032, 'full_name': 'Арина Нечаева', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 40, 'telegram_id': 9990000033, 'full_name': 'Константин Богомолов', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 41, 'telegram_id': 9990000034, 'full_name': 'Ширяев Василий Тимофеевич', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 42, 'telegram_id': 9990000035, 'full_name': 'Милана Кочеткова', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 43, 'telegram_id': 9990000036, 'full_name': 'dmitx', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 44, 'telegram_id': 9990000037, 'full_name': 'Алёна Фомина', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 45, 'telegram_id': 9990000038, 'full_name': 'Ксения Клюева', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 46, 'telegram_id': 9990000039, 'full_name': 'Артём Самсонов', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 47, 'telegram_id': 9990000040, 'full_name': 'Дарья', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 48, 'telegram_id': 9990000041, 'full_name': 'Леон Токарев', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 49, 'telegram_id': 9990000042, 'full_name': '@georgiy_korneev', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 50, 'telegram_id': 9990000043, 'full_name': 'Олеся', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 51, 'telegram_id': 9990000044, 'full_name': 'Ярослав Васильев', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 52, 'telegram_id': 9990000045, 'full_name': 'Татьяна', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 53, 'telegram_id': 9990000046, 'full_name': 'borodin.timofey', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 54, 'telegram_id': 9990000047, 'full_name': 'Елизавета Пономарева', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 55, 'telegram_id': 9990000048, 'full_name': 'ivankun', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 56, 'telegram_id': 9990000049, 'full_name': 'Элина Комарова', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 57, 'telegram_id': 9990000050, 'full_name': 'ponomarev.ilya', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 58, 'telegram_id': 9990000051, 'full_name': '@aleksandr_bogdanov', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 59, 'telegram_id': 9990000052, 'full_name': 'Дмитрий Третьяков', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 60, 'telegram_id': 9990000053, 'full_name': 'kozlova', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 61, 'telegram_id': 9990000054, 'full_name': 'Леонова Татьяна Михайловна', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 62, 'telegram_id': 9990000055, 'full_name': 'Владислава', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 63, 'telegram_id': 9990000056, 'full_name': 'Артур', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 64, 'telegram_id': 9990000057, 'full_name': 'Милана Сахарова', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
    {'id': 65, 'telegram_id': 9990000058, 'full_name': '@ivan_makarov', 'group_name': '18-ла', 'role': 'student', 'study_group_id': 2, 'birthday_month': None, 'birthday_day': None, 'birth_year': None},
]

async def seed(session_maker=None) -> None:
    """Вставляет GROUPS и USERS через INSERT OR IGNORE.

    session_maker — async_session_maker из database.py.
    Если не передан — создаётся собственный (для запуска скриптом напрямую).
    """
    if session_maker is None:
        from database import init_db, async_session_maker
        await init_db()
        session_maker = async_session_maker

    async with session_maker() as session:
        for g in GROUPS:
            await session.execute(text("""
                INSERT OR IGNORE INTO study_groups
                  (id, name, join_code, ruz_group_search, creator_user_id,
                   semester_number, corporate_email, imap_password, ruz_base_url, org_cookies)
                VALUES
                  (:id, :name, :join_code, :ruz_group_search, :creator_user_id,
                   :semester_number, :corporate_email, :imap_password, :ruz_base_url, :org_cookies)
            """), g)
        for u in USERS:
            await session.execute(text("""
                INSERT OR IGNORE INTO users
                  (id, telegram_id, full_name, group_name, role, study_group_id,
                   birthday_month, birthday_day, birth_year)
                VALUES
                  (:id, :telegram_id, :full_name, :group_name, :role, :study_group_id,
                   :birthday_month, :birthday_day, :birth_year)
            """), u)
        await session.commit()
    log.info("Seed: вставлено групп=%d, пользователей=%d", len(GROUPS), len(USERS))


if __name__ == "__main__":
    asyncio.run(seed())
