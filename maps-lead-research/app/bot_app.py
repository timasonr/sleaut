from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.access import (
    display_from_user,
    is_approved,
    is_super_admin,
    list_users,
    set_approved,
    upsert_user,
)
from app.ai_notes import build_note
from app.collector.factory import MissingPlacesApiKey
from app.config import get_settings
from app.csv_export import listings_csv_bytes
from app.database import db_lock, get_session_factory, init_db
from app.limits import LISTING_LIMIT_MAX, LISTING_LIMIT_MIN, parse_listing_limit
from app.models import Job, Listing
from app.services import enqueue_job, get_or_create_category, get_or_create_city, request_stop
from app.worker import start_background_worker, stop_worker

log = logging.getLogger(__name__)
router = Router()

BOT_COMMANDS = [
    BotCommand(command="start", description="Меню бота"),
    BotCommand(command="help", description="Справка и границы"),
    BotCommand(command="new", description="Новое исследование: город и категория"),
    BotCommand(command="progress", description="Прогресс последних задач"),
    BotCommand(command="stop", description="Остановить активную задачу"),
    BotCommand(command="listings", description="Листать собранные карточки"),
    BotCommand(command="export", description="Скачать CSV публичных полей"),
]

STATUS_RU = {
    "public_listing": "есть публичная карточка Maps",
    "incomplete": "неполная публичная карточка",
    "no_listing": "нет публичной карточки Maps",
}

SKIP_CATEGORY = {"все", "all", "-", "нет", "без категории", "skip"}


class Research(StatesGroup):
    city = State()
    category = State()
    limit = State()


def _session():
    return get_session_factory()()


def _user_meta(message_user) -> tuple[int | None, str, str]:
    if message_user is None:
        return None, "", ""
    uname, display = display_from_user(
        message_user.username,
        " ".join(p for p in [message_user.first_name, message_user.last_name] if p),
    )
    return message_user.id, uname, display


def _authorized(telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    if is_super_admin(telegram_id):
        return True
    with db_lock:
        s = _session()
        try:
            return is_approved(s, telegram_id)
        finally:
            s.close()


def _deny_text() -> str:
    return (
        "Нет доступа. Напишите /start — заявка уйдёт супер-админу. "
        "После одобрения откроется бот."
    )


def _places_missing_text() -> str:
    return MissingPlacesApiKey.MESSAGE


def menu_kb(telegram_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Новое исследование", callback_data="menu:new")],
        [
            InlineKeyboardButton(text="Прогресс", callback_data="menu:progress"),
            InlineKeyboardButton(text="Остановить", callback_data="menu:stop"),
        ],
        [
            InlineKeyboardButton(text="Результаты", callback_data="menu:results"),
            InlineKeyboardButton(text="CSV", callback_data="menu:export"),
        ],
    ]
    if telegram_id is not None and is_super_admin(telegram_id):
        rows.append([InlineKeyboardButton(text="Заявки", callback_data="menu:users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def results_kb(page: int, total_pages: int, listing_ids: list[int]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"Открыть #{lid}", callback_data=f"open:{lid}")]
        for lid in listing_ids
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="page:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="CSV", callback_data="menu:export"),
            InlineKeyboardButton(text="Меню", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_cat_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все категории", callback_data="cat:all")],
            [InlineKeyboardButton(text="Отмена", callback_data="menu:home")],
        ]
    )


def _welcome_text() -> str:
    settings = get_settings()
    source = "Google Places API" if settings.places_configured else "ключ Places не задан"
    return (
        "Исследование публичных карточек Maps.\n"
        f"Источник: {source}.\n"
        "Город, категорию и число карточек вводите текстом. Демо-пресеты отключены.\n"
        "Рассылки и контакт с владельцами не поддерживаются."
    )


def _help_text() -> str:
    return (
        "/start — меню\n"
        "/new — город → категория → число карточек → запуск\n"
        "/progress — прогресс задач\n"
        "/stop — остановить активную задачу\n"
        "/listings — листать публичные карточки\n"
        "/export — файл CSV (UTF-8 с BOM, только реальные карточки)\n"
        "/help — эта справка\n\n"
        "Классификация: есть карточка / неполная / нет карточки.\n"
        "Без рассылок, без питчей, без персональных данных владельцев.\n"
        "Без демо-данных: если нет GOOGLE_PLACES_API_KEY, задача не стартует."
    )


def _touch_user(telegram_id: int | None, username: str, display: str):
    if telegram_id is None:
        return None
    with db_lock:
        s = _session()
        try:
            user = upsert_user(s, telegram_id, username=username, display_name=display)
            approved = bool(user.approved or is_super_admin(telegram_id))
            uid = user.id
            s.commit()
            return approved, uid
        finally:
            s.close()


async def _notify_super_admins(bot: Bot, text: str, reply_markup=None) -> None:
    for tid in get_settings().super_admin_ids:
        try:
            await bot.send_message(tid, text, reply_markup=reply_markup)
        except Exception:
            log.warning("failed to notify super-admin")


async def _show_new_research(message: Message, state: FSMContext) -> None:
    if not get_settings().places_configured:
        await message.answer(_places_missing_text(), reply_markup=menu_kb(message.chat.id))
        return
    await state.set_state(Research.city)
    await message.answer(
        "Введите город текстом (например: Новосибирск). Список городов не подставляется."
    )


def _progress_text() -> str:
    with db_lock:
        s = _session()
        try:
            jobs = list(
                s.scalars(
                    select(Job)
                    .options(selectinload(Job.city), selectinload(Job.category))
                    .order_by(Job.id.desc())
                    .limit(5)
                ).all()
            )
            if not jobs:
                return "Задач пока нет."
            lines = []
            for job in jobs:
                cat = job.category.name if job.category else "все"
                city_name = job.city.name if job.city else "?"
                lines.append(
                    f"#{job.id} {city_name} / {cat}\n"
                    f"{job.status} {job.progress_done}/{job.progress_total}\n"
                    f"{job.message}"
                )
            return "\n\n".join(lines)
        finally:
            s.close()


def _request_stop() -> str:
    with db_lock:
        s = _session()
        try:
            job = s.scalar(
                select(Job)
                .where(Job.status.in_(("pending", "running")))
                .order_by(Job.id.desc())
            )
            if job is None:
                return "Нет активной задачи."
            request_stop(s, job.id)
            s.commit()
            return f"Остановка задачи #{job.id} запрошена."
        finally:
            s.close()


def _csv_bytes() -> tuple[bytes | None, int]:
    with db_lock:
        s = _session()
        try:
            rows = list(
                s.scalars(
                    select(Listing)
                    .options(selectinload(Listing.city))
                    .order_by(Listing.id.desc())
                    .limit(5000)
                ).all()
            )
            if not rows:
                return None, 0
            body, count = listings_csv_bytes(rows)
            if count == 0:
                return None, 0
            return body, count
        finally:
            s.close()


async def _send_csv(message: Message) -> None:
    body, count = _csv_bytes()
    if not body:
        await message.answer(
            "Нет реальных карточек для выгрузки. Демо-строки в CSV не попадают. "
            "Запустите исследование с ключом Places.",
            reply_markup=menu_kb(),
        )
        return
    await message.answer_document(
        BufferedInputFile(body, filename="public-listings.csv"),
        caption=(
            f"Публичные поля {count} карточек Maps. UTF-8 с BOM. "
            "Без демо-строк, без данных владельцев и без рассылок."
        ),
        reply_markup=menu_kb(),
    )


async def _ask_limit(message: Message) -> None:
    await message.answer(
        f"Сколько карточек собрать? Число от {LISTING_LIMIT_MIN} до {LISTING_LIMIT_MAX}."
    )


def _users_text_and_kb():
    with db_lock:
        s = _session()
        try:
            users = list_users(s)
            if not users:
                return "Пользователей пока нет.", menu_kb()
            lines = ["Заявки и доступ:"]
            rows = []
            for u in users:
                mark = "супер" if u.is_super else ("одобрен" if u.approved else "ожидает")
                who = u.display_name or u.username or str(u.telegram_id)
                lines.append(f"{u.telegram_id} {who} — {mark}")
                if not u.is_super:
                    if u.approved:
                        rows.append(
                            [
                                InlineKeyboardButton(
                                    text=f"Отозвать {u.telegram_id}",
                                    callback_data=f"deny:{u.telegram_id}",
                                )
                            ]
                        )
                    else:
                        rows.append(
                            [
                                InlineKeyboardButton(
                                    text=f"Одобрить {u.telegram_id}",
                                    callback_data=f"approve:{u.telegram_id}",
                                )
                            ]
                        )
            rows.append([InlineKeyboardButton(text="Меню", callback_data="menu:home")])
            return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
        finally:
            s.close()


async def _ensure_access(message: Message) -> bool:
    telegram_id, username, display = _user_meta(message.from_user)
    if telegram_id is None:
        await message.answer(_deny_text())
        return False
    approved, _ = _touch_user(telegram_id, username, display)
    if approved:
        return True
    await message.answer(_deny_text())
    return False


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    telegram_id, username, display = _user_meta(message.from_user)
    if telegram_id is None:
        await message.answer(_deny_text())
        return
    was_known = False
    with db_lock:
        s = _session()
        try:
            from app.models import AppUser

            was_known = (
                s.scalar(select(AppUser).where(AppUser.telegram_id == telegram_id)) is not None
            )
        finally:
            s.close()
    approved, _ = _touch_user(telegram_id, username, display)
    if not approved:
        await message.answer(_deny_text())
        if not was_known:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Одобрить",
                            callback_data=f"approve:{telegram_id}",
                        ),
                        InlineKeyboardButton(
                            text="Отклонить",
                            callback_data=f"deny:{telegram_id}",
                        ),
                    ]
                ]
            )
            who = display or username or str(telegram_id)
            await _notify_super_admins(
                message.bot,
                f"Заявка на доступ: {who} (id {telegram_id})",
                reply_markup=kb,
            )
        return
    await message.answer(_welcome_text(), reply_markup=menu_kb(telegram_id))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not await _ensure_access(message):
        return
    await message.answer(_help_text(), reply_markup=menu_kb(message.from_user.id if message.from_user else None))


@router.message(Command("new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    if not await _ensure_access(message):
        return
    await _show_new_research(message, state)


@router.message(Command("progress"))
async def cmd_progress(message: Message) -> None:
    if not await _ensure_access(message):
        return
    await message.answer(_progress_text(), reply_markup=menu_kb(message.from_user.id if message.from_user else None))


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    if not await _ensure_access(message):
        return
    await message.answer(_request_stop(), reply_markup=menu_kb(message.from_user.id if message.from_user else None))


@router.message(Command("listings"))
async def cmd_listings(message: Message) -> None:
    if not await _ensure_access(message):
        return
    text, kb = _results_page(1)
    await message.answer(text, reply_markup=kb)


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    if not await _ensure_access(message):
        return
    await _send_csv(message)


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    tid = message.from_user.id if message.from_user else None
    if not is_super_admin(tid):
        await message.answer(_deny_text())
        return
    text, kb = _users_text_and_kb()
    await message.answer(text, reply_markup=kb)


@router.message(Research.city)
async def typed_city(message: Message, state: FSMContext) -> None:
    if not await _ensure_access(message):
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.answer("Нужно название города текстом, без пресетов.")
        return
    await state.update_data(city_name=text)
    await state.set_state(Research.category)
    await message.answer(
        "Категория текстом (кафе, автосервис, …) или нажмите «Все категории».",
        reply_markup=skip_cat_kb(),
    )


async def _enqueue_from_state(
    *,
    city_name: str,
    category_name: str | None,
    max_results: int,
    telegram_id: int,
    message: Message,
    state: FSMContext,
) -> None:
    if not get_settings().places_configured:
        await state.clear()
        await message.answer(_places_missing_text(), reply_markup=menu_kb(telegram_id))
        return
    with db_lock:
        s = _session()
        try:
            city = get_or_create_city(s, city_name)
            category = get_or_create_category(s, category_name) if category_name else None
            job = enqueue_job(
                s,
                city_id=city.id,
                category_id=category.id if category else None,
                source="google_places",
                created_via="telegram",
                created_by=str(telegram_id),
                max_results=max_results,
            )
            s.commit()
            job_id = job.id
            city_label = city.name
            cat_label = category.name if category else "все"
        except Exception as exc:
            s.rollback()
            await state.clear()
            await message.answer(f"Не удалось создать задачу: {exc}", reply_markup=menu_kb(telegram_id))
            return
        finally:
            s.close()
    await state.clear()
    await message.answer(
        f"Задача #{job_id} запущена.\nГород: {city_label}\nКатегория: {cat_label}\n"
        f"Карточек: {max_results}\nИсточник: Places API.",
        reply_markup=menu_kb(telegram_id),
    )


@router.message(Research.category)
async def typed_category(message: Message, state: FSMContext) -> None:
    if not await _ensure_access(message):
        return
    raw = (message.text or "").strip()
    data = await state.get_data()
    city_name = data.get("city_name")
    if not city_name:
        await state.clear()
        await message.answer("Сначала введите город.", reply_markup=menu_kb(message.from_user.id if message.from_user else None))
        return
    category_name = None if raw.lower() in SKIP_CATEGORY else raw
    await state.update_data(category_name=category_name)
    await state.set_state(Research.limit)
    await _ask_limit(message)


@router.callback_query(F.data == "cat:all")
async def pick_all_cats(cb: CallbackQuery, state: FSMContext) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    data = await state.get_data()
    city_name = data.get("city_name")
    if not city_name:
        await cb.message.answer("Сначала введите город.", reply_markup=menu_kb(cb.from_user.id))
        await cb.answer()
        return
    await state.update_data(category_name=None)
    await state.set_state(Research.limit)
    await _ask_limit(cb.message)
    await cb.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(cb: CallbackQuery, state: FSMContext) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    await state.clear()
    await cb.message.answer("Меню", reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data == "menu:new")
async def menu_new(cb: CallbackQuery, state: FSMContext) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    await _show_new_research(cb.message, state)
    await cb.answer()


@router.callback_query(F.data == "menu:progress")
async def menu_progress(cb: CallbackQuery) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    await cb.message.answer(_progress_text(), reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data == "menu:stop")
async def menu_stop(cb: CallbackQuery) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    await cb.message.answer(_request_stop(), reply_markup=menu_kb(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data == "menu:export")
async def menu_export(cb: CallbackQuery) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    await _send_csv(cb.message)
    await cb.answer()


@router.message(Research.limit)
async def typed_limit(message: Message, state: FSMContext) -> None:
    if not await _ensure_access(message):
        return
    limit = parse_listing_limit(message.text or "")
    if limit is None:
        await _ask_limit(message)
        return
    data = await state.get_data()
    city_name = data.get("city_name")
    if not city_name:
        await state.clear()
        await message.answer("Сначала введите город.", reply_markup=menu_kb(message.from_user.id if message.from_user else None))
        return
    tid = message.from_user.id if message.from_user else 0
    await _enqueue_from_state(
        city_name=city_name,
        category_name=data.get("category_name"),
        max_results=limit,
        telegram_id=tid,
        message=message,
        state=state,
    )


@router.callback_query(F.data == "menu:users")
async def menu_users(cb: CallbackQuery) -> None:
    if not is_super_admin(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    text, kb = _users_text_and_kb()
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("approve:"))
async def approve_cb(cb: CallbackQuery) -> None:
    if not is_super_admin(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    tid = int(cb.data.split(":")[1])
    with db_lock:
        s = _session()
        try:
            user = set_approved(s, tid, approved=True, by=str(cb.from_user.id))
            s.commit()
        finally:
            s.close()
    if user is None:
        await cb.answer("пользователь не найден", show_alert=True)
        return
    await cb.message.answer(f"Одобрен {tid}.")
    try:
        await cb.bot.send_message(tid, "Доступ одобрен. /start")
    except Exception:
        log.warning("failed to notify approved user")
    await cb.answer("одобрено")


@router.callback_query(F.data.startswith("deny:"))
async def deny_cb(cb: CallbackQuery) -> None:
    if not is_super_admin(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    tid = int(cb.data.split(":")[1])
    with db_lock:
        s = _session()
        try:
            set_approved(s, tid, approved=False, by=str(cb.from_user.id))
            s.commit()
        finally:
            s.close()
    await cb.message.answer(f"Доступ {tid} отозван.")
    await cb.answer("отозвано")


def _results_page(page: int) -> tuple[str, InlineKeyboardMarkup]:
    per = 5
    with db_lock:
        s = _session()
        try:
            from app.csv_export import is_real_collected

            stmt = select(Listing).options(selectinload(Listing.city)).order_by(Listing.id.desc())
            all_rows = [row for row in s.scalars(stmt.limit(500)).all() if is_real_collected(row)]
            total = len(all_rows)
            if not total:
                return (
                    "Карточек пока нет. Демо-строки скрыты. Запустите исследование с ключом Places.",
                    menu_kb(),
                )
            total_pages = max(1, (total + per - 1) // per)
            page = min(max(1, page), total_pages)
            rows = all_rows[(page - 1) * per : page * per]
            short_ru = {
                "public_listing": "есть карточка",
                "incomplete": "неполная",
                "no_listing": "нет карточки",
            }
            lines = [f"Публичные карточки, стр. {page}/{total_pages}:"]
            listing_ids: list[int] = []
            for item in rows:
                listing_ids.append(item.id)
                city = item.city.name if item.city else ""
                lines.append(
                    f"#{item.id} {item.name} — {item.category_label or '—'}, {city}\n"
                    f"{short_ru.get(item.maps_status, item.maps_status)}"
                )
            return "\n\n".join(lines), results_kb(page, total_pages, listing_ids)
        finally:
            s.close()


@router.callback_query(F.data == "menu:results")
async def menu_results(cb: CallbackQuery) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    text, kb = _results_page(1)
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("page:"))
async def page_results(cb: CallbackQuery) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    token = cb.data.split(":")[1]
    if token == "noop":
        await cb.answer()
        return
    text, kb = _results_page(int(token))
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("open:"))
async def open_listing(cb: CallbackQuery) -> None:
    if not _authorized(cb.from_user.id):
        await cb.answer("нет доступа", show_alert=True)
        return
    listing_id = int(cb.data.split(":")[1])
    maps_url = ""
    text = ""
    with db_lock:
        s = _session()
        try:
            item = s.scalar(
                select(Listing).options(selectinload(Listing.city)).where(Listing.id == listing_id)
            )
            if item is None:
                await cb.answer("не найдено", show_alert=True)
                return
            from app.csv_export import is_real_collected

            if not is_real_collected(item):
                await cb.answer("демо-строка скрыта", show_alert=True)
                return
            status_ru = STATUS_RU.get(item.maps_status, item.maps_status)
            note = build_note(item)
            maps_url = item.maps_url
            lines = [
                item.name,
                f"Категория: {item.category_label or '—'}",
                f"Город: {item.city.name if item.city else '—'}",
                f"Адрес: {item.address or '—'}",
                f"Телефон на карточке: {item.public_phone or 'не указан'}",
                f"Сайт: {item.website or 'не указан'}",
                f"Оценка: {item.rating if item.rating is not None else '—'} ({item.ratings_count})",
                f"Фото: {item.photos_count}",
                f"Профиль Maps: {'есть' if item.maps_profile_exists else 'нет'}",
                f"Статус: {status_ru}",
                f"Часы: {'есть' if item.has_hours else 'нет'}; публикации: "
                + ("есть" if item.has_posts else "нет" if item.has_posts is False else "неизвестно"),
                "Источник: Places API",
            ]
            if note:
                lines.extend(["", note])
            text = "\n".join(lines)
        finally:
            s.close()
    kb_rows: list[list[InlineKeyboardButton]] = []
    if maps_url:
        kb_rows.append([InlineKeyboardButton(text="Открыть карточку Maps", url=maps_url)])
    kb_rows.append([InlineKeyboardButton(text="К результатам", callback_data="menu:results")])
    await cb.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()


async def _idle_forever() -> None:
    log.warning("Telegram bot disabled: set TELEGRAM_BOT_TOKEN")
    while True:
        await asyncio.sleep(3600)


async def _run_bot() -> None:
    settings = get_settings()
    init_db()
    start_background_worker()
    bot = Bot(settings.telegram_bot_token)
    await bot.set_my_commands(BOT_COMMANDS)
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Telegram bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        stop_worker()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if not settings.telegram_enabled:
        asyncio.run(_idle_forever())
        return
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
