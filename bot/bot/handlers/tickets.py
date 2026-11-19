"""Создание, списки, поиск и действия по заявкам (этап 2)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import (
    REPLACEMENT_TICKET_TYPES,
    Ticket,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
)
from bot.keyboards.mass import mass_ticket_card_keyboard
from bot.keyboards.menus import main_menu_keyboard
from bot.keyboards.replacement import replacement_ticket_card_keyboard
from bot.keyboards.tickets import (
    TicketCB,
    cancel_back_keyboard,
    comment_keyboard,
    initial_state_keyboard,
    initiator_keyboard,
    preview_keyboard,
    ticket_card_keyboard,
    tickets_list_keyboard,
    transfer_type_keyboard,
    weekday_keyboard,
)
from bot.middlewares.auth import user_has_role
from bot.services.tickets import (
    add_ticket_comment,
    can_manage_ticket,
    can_view_ticket,
    change_ticket_status,
    create_ticket,
    format_preview_from_fsm,
    format_ticket_card,
    format_ticket_list_text,
    get_ticket_by_id,
    list_active_tickets,
    list_tickets_by_creator,
    list_waiting_reply_tickets,
    parse_date_ru,
    search_tickets,
    ticket_payload_from_fsm,
)

router = Router(name="tickets")


class CreateTicketFSM(StatesGroup):
    choosing_type = State()
    teacher = State()
    student_name = State()
    student_id = State()
    # one_time
    original_datetime = State()
    new_datetime = State()
    # permanent
    current_weekday = State()
    new_weekday = State()
    lesson_time = State()
    schedule_start_date = State()
    nearest_lesson_date = State()
    # common
    initiator = State()
    comment = State()
    initial_state = State()
    preview = State()


class SearchTicketFSM(StatesGroup):
    waiting_query = State()


class CommentTicketFSM(StatesGroup):
    waiting_text = State()


# Порядок шагов для «Назад» (после выбора типа). ID ученика не спрашиваем.
COMMON_TAIL = ("initiator", "comment", "initial_state", "preview")
ONE_TIME_STEPS = (
    "teacher",
    "student_name",
    "original_datetime",
    "new_datetime",
) + COMMON_TAIL
PERMANENT_STEPS = (
    "teacher",
    "student_name",
    "current_weekday",
    "new_weekday",
    "lesson_time",
    "schedule_start_date",
    "nearest_lesson_date",
) + COMMON_TAIL

STEP_TO_STATE: dict[str, State] = {
    "choosing_type": CreateTicketFSM.choosing_type,
    "teacher": CreateTicketFSM.teacher,
    "student_name": CreateTicketFSM.student_name,
    "original_datetime": CreateTicketFSM.original_datetime,
    "new_datetime": CreateTicketFSM.new_datetime,
    "current_weekday": CreateTicketFSM.current_weekday,
    "new_weekday": CreateTicketFSM.new_weekday,
    "lesson_time": CreateTicketFSM.lesson_time,
    "schedule_start_date": CreateTicketFSM.schedule_start_date,
    "nearest_lesson_date": CreateTicketFSM.nearest_lesson_date,
    "initiator": CreateTicketFSM.initiator,
    "comment": CreateTicketFSM.comment,
    "initial_state": CreateTicketFSM.initial_state,
    "preview": CreateTicketFSM.preview,
}


def _can_create(user: User) -> bool:
    return user_has_role(
        user, UserRole.CURATOR, UserRole.SENIOR_TEACHER, UserRole.ADMIN
    )


def _can_list_active(user: User) -> bool:
    return user_has_role(user, UserRole.CURATOR, UserRole.ADMIN)


def _can_list_mine(user: User) -> bool:
    return user_has_role(user, UserRole.SENIOR_TEACHER, UserRole.ADMIN, UserRole.CURATOR)


def _can_search(user: User) -> bool:
    return user_has_role(
        user, UserRole.CURATOR, UserRole.SENIOR_TEACHER, UserRole.ADMIN
    )


async def _prompt_step(message: Message, state: FSMContext, step: str) -> None:
    """Показать вопрос текущего шага."""
    await state.update_data(current_step=step)
    await state.set_state(STEP_TO_STATE[step])

    prompts: dict[str, tuple[str, object]] = {
        "teacher": (
            "Введите <b>имя преподавателя</b>:",
            cancel_back_keyboard(show_back=True),
        ),
        "student_name": (
            "Введите <b>ФИО ученика</b>:",
            cancel_back_keyboard(show_back=True),
        ),
        "original_datetime": (
            "Введите <b>исходное</b> время занятия\n"
            "(например: 20.11.2026 14:00 или «чт 14:00»):",
            cancel_back_keyboard(show_back=True),
        ),
        "new_datetime": (
            "Введите <b>новое</b> время занятия:",
            cancel_back_keyboard(show_back=True),
        ),
        "current_weekday": (
            "Выберите <b>текущий</b> день недели:",
            weekday_keyboard(field="current"),
        ),
        "new_weekday": (
            "Выберите <b>новый</b> день недели:",
            weekday_keyboard(field="new"),
        ),
        "lesson_time": (
            "Введите <b>время</b> занятия (например 14:00):",
            cancel_back_keyboard(show_back=True),
        ),
        "schedule_start_date": (
            "Дата <b>начала нового расписания</b> (ДД.ММ.ГГГГ):",
            cancel_back_keyboard(show_back=True),
        ),
        "nearest_lesson_date": (
            "Дата <b>ближайшего затронутого занятия</b> (ДД.ММ.ГГГГ):",
            cancel_back_keyboard(show_back=True),
        ),
        "initiator": (
            "Кто <b>инициатор</b> переноса?",
            initiator_keyboard(),
        ),
        "comment": (
            "Комментарий (или «Пропустить»):\n"
            "<i>Заметку о выбранной замене можно добавить позже кнопкой "
            "«Заметка о замене» на карточке.</i>",
            comment_keyboard(),
        ),
        "initial_state": (
            "Начальное состояние заявки:",
            initial_state_keyboard(),
        ),
    }

    if step == "preview":
        data = await state.get_data()
        text = format_preview_from_fsm(data)
        await message.answer(text, parse_mode="HTML", reply_markup=preview_keyboard())
        return

    if step == "choosing_type":
        await message.answer(
            "Выберите тип переноса:",
            reply_markup=transfer_type_keyboard(),
        )
        return

    text, markup = prompts[step]
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


def _steps_for(ticket_type: str) -> tuple[str, ...]:
    if ticket_type == TicketType.ONE_TIME.value:
        return ONE_TIME_STEPS
    return PERMANENT_STEPS


async def _goto_next(message: Message, state: FSMContext, after_step: str) -> None:
    data = await state.get_data()
    steps = _steps_for(str(data["ticket_type"]))
    idx = steps.index(after_step)
    if idx + 1 >= len(steps):
        await _prompt_step(message, state, "preview")
        return
    await _prompt_step(message, state, steps[idx + 1])


# ---------------------------------------------------------------------------
# Старт создания
# ---------------------------------------------------------------------------


@router.message(F.text.in_({"Добавить перенос", "Создать заявку"}))
async def start_create_ticket(
    message: Message, state: FSMContext, db_user: User
) -> None:
    if not _can_create(db_user):
        await message.answer("Недостаточно прав для создания заявки.")
        return
    await state.clear()
    await state.set_state(CreateTicketFSM.choosing_type)
    await state.update_data(current_step="choosing_type")
    await message.answer(
        "🥑 Создание заявки на перенос.\nВыберите тип:",
        reply_markup=transfer_type_keyboard(),
    )


@router.callback_query(CreateTicketFSM.choosing_type, F.data.startswith(TicketCB.TYPE))
async def choose_type(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _can_create(db_user):
        await callback.answer("Нет прав", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(TicketCB.TYPE)
    if raw not in (TicketType.ONE_TIME.value, TicketType.PERMANENT.value):
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(ticket_type=raw, current_step="teacher")
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "teacher")


# ---------------------------------------------------------------------------
# Навигация Назад / Отмена
# ---------------------------------------------------------------------------


@router.callback_query(F.data == f"{TicketCB.NAV}cancel")
async def cancel_flow(
    callback: CallbackQuery, state: FSMContext, db_user: User
) -> None:
    current = await state.get_state() or ""
    await state.clear()
    await callback.answer("Отменено")
    if not callback.message:
        return
    if "CreateTicketFSM" in current:
        text = "Создание заявки отменено. Уже сохранённые заявки не затронуты."
    elif "SearchTicketFSM" in current:
        text = "Поиск отменён."
    elif "CommentTicketFSM" in current:
        text = "Ввод комментария отменён."
    else:
        text = "Действие отменено."
    await callback.message.answer(text, reply_markup=main_menu_keyboard(db_user))


@router.callback_query(F.data == f"{TicketCB.NAV}back")
async def go_back(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    _ = db_user
    data = await state.get_data()
    current = data.get("current_step", "choosing_type")
    ticket_type = data.get("ticket_type")

    if current == "choosing_type" or not ticket_type:
        await state.set_state(CreateTicketFSM.choosing_type)
        await state.update_data(current_step="choosing_type")
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Выберите тип переноса:",
                reply_markup=transfer_type_keyboard(),
            )
        return

    if current == "teacher":
        await state.set_state(CreateTicketFSM.choosing_type)
        await state.update_data(current_step="choosing_type")
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Выберите тип переноса:",
                reply_markup=transfer_type_keyboard(),
            )
        return

    steps = _steps_for(str(ticket_type))
    if current not in steps:
        await callback.answer()
        return
    idx = steps.index(current)
    prev_step = steps[idx - 1] if idx > 0 else "choosing_type"
    await callback.answer()
    if callback.message:
        if prev_step == "choosing_type":
            await state.set_state(CreateTicketFSM.choosing_type)
            await state.update_data(current_step="choosing_type")
            await callback.message.answer(
                "Выберите тип переноса:",
                reply_markup=transfer_type_keyboard(),
            )
        else:
            await _prompt_step(callback.message, state, prev_step)


# ---------------------------------------------------------------------------
# Текстовые шаги формы
# ---------------------------------------------------------------------------


@router.message(CreateTicketFSM.teacher)
async def step_teacher(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Слишком короткое имя. Введите ещё раз.")
        return
    await state.update_data(teacher_name=value)
    await _goto_next(message, state, "teacher")


@router.message(CreateTicketFSM.student_name)
async def step_student_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Слишком короткое имя. Введите ещё раз.")
        return
    await state.update_data(student_name=value, student_external_id=None)
    await _goto_next(message, state, "student_name")


@router.message(CreateTicketFSM.original_datetime)
async def step_original_dt(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите исходное время.")
        return
    await state.update_data(original_datetime=value)
    await _goto_next(message, state, "original_datetime")


@router.message(CreateTicketFSM.new_datetime)
async def step_new_dt(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите новое время.")
        return
    await state.update_data(new_datetime=value)
    await _goto_next(message, state, "new_datetime")


@router.callback_query(
    CreateTicketFSM.current_weekday, F.data.startswith(f"{TicketCB.WEEKDAY}current:")
)
async def step_current_weekday_cb(
    callback: CallbackQuery, state: FSMContext
) -> None:
    raw = (callback.data or "").split(":")[-1]
    await state.update_data(current_weekday=int(raw))
    await callback.answer()
    if callback.message:
        await _goto_next(callback.message, state, "current_weekday")


@router.message(CreateTicketFSM.current_weekday)
async def step_current_weekday_text(message: Message, state: FSMContext) -> None:
    from bot.services.tickets import parse_weekday

    try:
        value = parse_weekday(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(current_weekday=value)
    await _goto_next(message, state, "current_weekday")


@router.callback_query(
    CreateTicketFSM.new_weekday, F.data.startswith(f"{TicketCB.WEEKDAY}new:")
)
async def step_new_weekday_cb(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").split(":")[-1]
    await state.update_data(new_weekday=int(raw))
    await callback.answer()
    if callback.message:
        await _goto_next(callback.message, state, "new_weekday")


@router.message(CreateTicketFSM.new_weekday)
async def step_new_weekday_text(message: Message, state: FSMContext) -> None:
    from bot.services.tickets import parse_weekday

    try:
        value = parse_weekday(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(new_weekday=value)
    await _goto_next(message, state, "new_weekday")


@router.message(CreateTicketFSM.lesson_time)
async def step_lesson_time(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 1:
        await message.answer("Укажите время.")
        return
    await state.update_data(lesson_time=value)
    await _goto_next(message, state, "lesson_time")


@router.message(CreateTicketFSM.schedule_start_date)
async def step_schedule_start(message: Message, state: FSMContext) -> None:
    try:
        value = parse_date_ru(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(schedule_start_date=value.isoformat())
    await _goto_next(message, state, "schedule_start_date")


@router.message(CreateTicketFSM.nearest_lesson_date)
async def step_nearest_lesson(message: Message, state: FSMContext) -> None:
    try:
        value = parse_date_ru(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(nearest_lesson_date=value.isoformat())
    await _goto_next(message, state, "nearest_lesson_date")


@router.callback_query(CreateTicketFSM.initiator, F.data.startswith(TicketCB.INIT))
async def step_initiator_cb(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").removeprefix(TicketCB.INIT)
    try:
        TicketInitiator(raw)
    except ValueError:
        await callback.answer("Неизвестный инициатор", show_alert=True)
        return
    await state.update_data(initiator=raw)
    await callback.answer()
    if callback.message:
        await _goto_next(callback.message, state, "initiator")


@router.callback_query(CreateTicketFSM.comment, F.data == f"{TicketCB.SKIP}comment")
async def skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment="")
    await callback.answer()
    if callback.message:
        await _goto_next(callback.message, state, "comment")


@router.message(CreateTicketFSM.comment)
async def step_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=(message.text or "").strip())
    await _goto_next(message, state, "comment")


@router.callback_query(CreateTicketFSM.initial_state, F.data.startswith(TicketCB.STATE))
async def step_initial_state(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").removeprefix(TicketCB.STATE)
    if raw not in ("not_written", "written", "done"):
        await callback.answer("Неизвестное состояние", show_alert=True)
        return
    await state.update_data(initial_state=raw)
    await callback.answer()
    if callback.message:
        await _goto_next(callback.message, state, "initial_state")


# ---------------------------------------------------------------------------
# Превью
# ---------------------------------------------------------------------------


@router.callback_query(CreateTicketFSM.preview, F.data == f"{TicketCB.PREVIEW}edit")
async def preview_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Вернуться к первому полю формы, сохранив уже введённые данные."""
    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _prompt_step(callback.message, state, "teacher")


@router.callback_query(F.data == f"{TicketCB.PREVIEW}edit")
async def preview_edit_stale(callback: CallbackQuery) -> None:
    await callback.answer(
        "Форма устарела. Начните создание заявки заново.",
        show_alert=True,
    )


@router.callback_query(F.data == f"{TicketCB.PREVIEW}create")
async def preview_create(
    callback: CallbackQuery,
    state: FSMContext,
    session: Session,
    db_user: User,
) -> None:
    """Создать заявку: сначала answer(), потом БД (иначе кнопка «молчит»)."""
    if not _can_create(db_user):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("ticket_type") or not data.get("teacher_name"):
        await callback.answer(
            "Форма устарела или неполная. Начните создание заявки заново.",
            show_alert=True,
        )
        return

    await callback.answer("Создаю…")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    try:
        payload = ticket_payload_from_fsm(data)
        ticket = create_ticket(session, creator=db_user, **payload)
        session.flush()
    except (ValueError, KeyError) as exc:
        if callback.message:
            await callback.message.answer(f"⚠️ Не удалось создать: {exc}")
        return
    except Exception as exc:
        if callback.message:
            await callback.message.answer(f"⚠️ Не удалось создать: {exc}")
        return

    await state.clear()
    if callback.message:
        ticket = get_ticket_by_id(session, ticket.id) or ticket
        from bot.services.notify import notify_new_ticket

        notify_status = await notify_new_ticket(
            callback.bot, session, ticket=ticket, creator=db_user
        )
        await callback.message.answer(
            "✅ Заявка создана.\n"
            f"{notify_status}\n\n"
            + format_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=_card_markup_for(ticket),
        )
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu_keyboard(db_user),
        )


# ---------------------------------------------------------------------------
# Списки и поиск
# ---------------------------------------------------------------------------


async def _send_ticket_list(
    message: Message,
    tickets: list[Ticket],
    *,
    title: str,
    empty: str,
) -> None:
    if not tickets:
        await message.answer(empty)
        return
    await message.answer(
        format_ticket_list_text(tickets[:30], title=title),
        parse_mode="HTML",
        reply_markup=tickets_list_keyboard(tickets),
    )


async def _notify_comment_recipients(bot, recipients, text: str) -> int:
    notified = 0
    for user in recipients:
        try:
            await bot.send_message(user.telegram_id, text, parse_mode="HTML")
            notified += 1
        except Exception:
            continue
    return notified


@router.message(F.text == "Активные заявки")
async def menu_active(
    message: Message, session: Session, db_user: User, state: FSMContext
) -> None:
    if not _can_list_active(db_user):
        await message.answer("Раздел доступен кураторам и администратору.")
        return
    await state.clear()
    tickets = list_active_tickets(session)
    await _send_ticket_list(
        message,
        tickets,
        title="Активные заявки",
        empty="Активных заявок нет.",
    )


@router.callback_query(F.data == f"{TicketCB.PREFIX}auto:active")
async def callback_open_active(
    callback: CallbackQuery, session: Session, db_user: User, state: FSMContext
) -> None:
    """Кнопка «Открыть задачи» из утренней/вечерней сводки."""
    if not _can_list_active(db_user):
        await callback.answer("Недоступно для вашей роли.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    tickets = list_active_tickets(session)
    if callback.message:
        await _send_ticket_list(
            callback.message,
            tickets,
            title="Активные заявки",
            empty="Активных заявок нет.",
        )


@router.message(F.text == "Мои заявки")
async def menu_my(
    message: Message, session: Session, db_user: User, state: FSMContext
) -> None:
    if not _can_list_mine(db_user):
        await message.answer("Раздел недоступен для вашей роли.")
        return
    await state.clear()
    tickets = list_tickets_by_creator(session, db_user)
    await _send_ticket_list(
        message,
        tickets,
        title="Мои заявки",
        empty="Вы ещё не создавали заявок.",
    )


@router.message(F.text == "На контроле")
async def menu_on_control(
    message: Message, session: Session, db_user: User, state: FSMContext
) -> None:
    if not _can_list_active(db_user):
        await message.answer("Раздел доступен кураторам и администратору.")
        return
    await state.clear()
    tickets = list_waiting_reply_tickets(session)
    await _send_ticket_list(
        message,
        tickets,
        title="На контроле (ждём ответа)",
        empty="Заявок на контроле нет.",
    )


@router.message(F.text.in_({"Поиск заявки", "Найти заявку"}))
async def menu_search_start(
    message: Message, state: FSMContext, db_user: User
) -> None:
    if not _can_search(db_user):
        await message.answer("Поиск недоступен для вашей роли.")
        return
    await state.set_state(SearchTicketFSM.waiting_query)
    await message.answer(
        "🔍 Введите номер заявки, имя ученика или преподавателя:",
        reply_markup=cancel_back_keyboard(show_back=False),
    )


@router.message(SearchTicketFSM.waiting_query)
async def menu_search_query(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите строку поиска.")
        return
    await state.clear()
    tickets = search_tickets(session, query)
    # Старший: свои + переносы/замены, к которым есть доступ (заметка о замене)
    if db_user.role == UserRole.SENIOR_TEACHER:
        tickets = [t for t in tickets if can_view_ticket(db_user, t)]
    await _send_ticket_list(
        message,
        tickets,
        title=f"Результаты по «{query}»",
        empty=f"Ничего не найдено по запросу «{query}».",
    )


# ---------------------------------------------------------------------------
# Карточка и действия
# ---------------------------------------------------------------------------


def _card_markup_for(ticket: Ticket, *, detailed: bool = False):
    if ticket.ticket_type == TicketType.MASS:
        return mass_ticket_card_keyboard(ticket, detailed=detailed)
    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        return replacement_ticket_card_keyboard(ticket, detailed=detailed)
    return ticket_card_keyboard(ticket, detailed=detailed)


@router.callback_query(F.data.startswith(TicketCB.OPEN))
async def open_ticket_card(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    raw = (callback.data or "").removeprefix(TicketCB.OPEN)
    if not raw.isdigit():
        await callback.answer("Некорректная заявка", show_alert=True)
        return
    ticket = get_ticket_by_id(session, int(raw))
    if ticket is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not can_view_ticket(db_user, ticket):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            format_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=_card_markup_for(ticket, detailed=False),
        )


@router.callback_query(F.data.startswith(TicketCB.DETAILS))
async def open_ticket_details(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    raw = (callback.data or "").removeprefix(TicketCB.DETAILS)
    if not raw.isdigit():
        await callback.answer("Некорректная заявка", show_alert=True)
        return
    ticket = get_ticket_by_id(session, int(raw))
    if ticket is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not can_view_ticket(db_user, ticket):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            format_ticket_card(ticket, with_history=True),
            parse_mode="HTML",
            reply_markup=_card_markup_for(ticket, detailed=True),
        )


async def _apply_status_action(
    callback: CallbackQuery,
    session: Session,
    db_user: User,
    *,
    ticket_id: int,
    new_status: TicketStatus,
    comment: str | None,
) -> None:
    ticket = get_ticket_by_id(session, ticket_id)
    if ticket is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not can_manage_ticket(db_user, ticket):
        await callback.answer("Нет прав", show_alert=True)
        return
    try:
        change_ticket_status(
            session,
            ticket=ticket,
            new_status=new_status,
            actor=db_user,
            comment=comment,
        )
    except PermissionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    ticket = get_ticket_by_id(session, ticket_id) or ticket
    await callback.answer("Обновлено")
    if callback.message:
        await callback.message.answer(
            format_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=ticket_card_keyboard(ticket),
        )


@router.callback_query(F.data.startswith(f"{TicketCB.ACTION}wrote:"))
async def action_wrote(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    tid = int((callback.data or "").split(":")[-1])
    await _apply_status_action(
        callback,
        session,
        db_user,
        ticket_id=tid,
        new_status=TicketStatus.WAITING_REPLY,
        comment="Написали ученику",
    )


@router.callback_query(F.data.startswith(f"{TicketCB.ACTION}done:"))
async def action_done(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    tid = int((callback.data or "").split(":")[-1])
    await _apply_status_action(
        callback,
        session,
        db_user,
        ticket_id=tid,
        new_status=TicketStatus.DONE,
        comment="Отмечено выполненным",
    )


@router.callback_query(F.data.startswith(f"{TicketCB.ACTION}reject:"))
async def action_reject(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    tid = int((callback.data or "").split(":")[-1])
    await _apply_status_action(
        callback,
        session,
        db_user,
        ticket_id=tid,
        new_status=TicketStatus.REJECTED,
        comment="Отказ",
    )


@router.callback_query(F.data.startswith(f"{TicketCB.ACTION}no_reply:"))
async def action_no_reply(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    tid = int((callback.data or "").split(":")[-1])
    # Остаёмся в waiting_reply — не авто-закрываем
    await _apply_status_action(
        callback,
        session,
        db_user,
        ticket_id=tid,
        new_status=TicketStatus.WAITING_REPLY,
        comment="Ответа пока нет",
    )


@router.callback_query(F.data.startswith(f"{TicketCB.ACTION}other_time:"))
async def action_other_time(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    tid = int((callback.data or "").split(":")[-1])
    await _apply_status_action(
        callback,
        session,
        db_user,
        ticket_id=tid,
        new_status=TicketStatus.WAITING_REPLY,
        comment="Предложили другое время",
    )


@router.callback_query(F.data.startswith(f"{TicketCB.ACTION}comment:"))
async def action_comment_start(
    callback: CallbackQuery, state: FSMContext, session: Session, db_user: User
) -> None:
    from bot.services.notify import can_comment_ticket

    tid = int((callback.data or "").split(":")[-1])
    ticket = get_ticket_by_id(session, tid)
    if ticket is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not can_comment_ticket(db_user, ticket):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CommentTicketFSM.waiting_text)
    await state.update_data(comment_ticket_id=tid)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"📌 Заметка о замене / комментарий к заявке №{ticket.number}\n"
            "(куратор увидит текст на карточке):",
            reply_markup=cancel_back_keyboard(show_back=False),
        )


@router.message(CommentTicketFSM.waiting_text)
async def action_comment_save(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    data = await state.get_data()
    ticket_id = int(data.get("comment_ticket_id") or 0)
    await state.clear()
    ticket = get_ticket_by_id(session, ticket_id)
    if ticket is None:
        await message.answer("Заявка не найдена.")
        return
    try:
        ticket, recipients, notify_text = add_ticket_comment(
            session,
            ticket=ticket,
            actor=db_user,
            comment=message.text or "",
        )
    except (ValueError, PermissionError) as exc:
        await message.answer(f"⚠️ {exc}")
        return
    session.commit()
    notified = await _notify_comment_recipients(message.bot, recipients, notify_text)
    ticket = get_ticket_by_id(session, ticket_id) or ticket
    note = f"\nУведомлены: {notified}" if recipients else ""
    await message.answer(
        "Комментарий сохранён."
        + note
        + "\n\n"
        + format_ticket_card(ticket, with_history=False),
        parse_mode="HTML",
        reply_markup=_card_markup_for(ticket),
    )
