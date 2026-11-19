"""Подбор замены: FSM создания и действия по подзадачам (этап 4)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import (
    REPLACEMENT_TICKET_TYPES,
    TicketType,
    User,
    UserRole,
)
from bot.keyboards.menus import main_menu_keyboard
from bot.keyboards.replacement import (
    ReplCB,
    repl_after_student_keyboard,
    repl_cancel_back_keyboard,
    repl_comment_keyboard,
    repl_kind_keyboard,
    repl_preview_keyboard,
    repl_student_comment_keyboard,
    replacement_subtask_card_keyboard,
    replacement_ticket_card_keyboard,
)
from bot.middlewares.auth import user_has_role
from bot.services.replacement import (
    close_student_not_continuing,
    create_replacement_ticket,
    format_replacement_preview,
    format_replacement_subtask_card,
    format_replacement_ticket_card,
    format_senior_help_message,
    get_replacement_subtask,
    mark_offered_to_parent,
    mark_replacement_done,
    request_senior_help,
    search_another_candidate,
    set_candidate_found,
)
from bot.services.tickets import can_manage_ticket, can_view_ticket, get_ticket_by_id, parse_date_ru

router = Router(name="replacement")


class CreateReplacementFSM(StatesGroup):
    choosing_kind = State()
    teacher = State()
    absence_start = State()
    return_date = State()
    comment = State()
    student_name = State()
    student_id = State()
    subject = State()
    schedule = State()
    student_comment = State()
    after_student = State()
    preview = State()


class CandidateNameFSM(StatesGroup):
    waiting_name = State()


class CloseReasonFSM(StatesGroup):
    waiting_reason = State()


HEADER_STEPS_DISMISSAL = ("teacher", "absence_start", "comment")
HEADER_STEPS_TEMP = ("teacher", "absence_start", "return_date", "comment")
STUDENT_STEPS = (
    "student_name",
    "subject",
    "schedule",
    "student_comment",
    "after_student",
)

STEP_TO_STATE: dict[str, State] = {
    "choosing_kind": CreateReplacementFSM.choosing_kind,
    "teacher": CreateReplacementFSM.teacher,
    "absence_start": CreateReplacementFSM.absence_start,
    "return_date": CreateReplacementFSM.return_date,
    "comment": CreateReplacementFSM.comment,
    "student_name": CreateReplacementFSM.student_name,
    "subject": CreateReplacementFSM.subject,
    "schedule": CreateReplacementFSM.schedule,
    "student_comment": CreateReplacementFSM.student_comment,
    "after_student": CreateReplacementFSM.after_student,
    "preview": CreateReplacementFSM.preview,
}


def _can_create_replacement(user: User) -> bool:
    return user_has_role(
        user, UserRole.CURATOR, UserRole.ADMIN, UserRole.SENIOR_TEACHER
    )


def _header_steps(kind: str) -> tuple[str, ...]:
    if kind == TicketType.TEMPORARY_ABSENCE.value:
        return HEADER_STEPS_TEMP
    return HEADER_STEPS_DISMISSAL


async def _prompt_step(message: Message, state: FSMContext, step: str) -> None:
    await state.update_data(current_step=step)
    await state.set_state(STEP_TO_STATE[step])
    data = await state.get_data()
    students = list(data.get("students") or [])
    n = len(students) + 1
    kind = data.get("kind", TicketType.DISMISSAL.value)

    if step == "choosing_kind":
        await message.answer(
            "Подбор замены.\nВыберите тип заявки:",
            reply_markup=repl_kind_keyboard(),
        )
        return

    date_label = (
        "последний день работы"
        if kind == TicketType.DISMISSAL.value
        else "начало отсутствия"
    )

    prompts: dict[str, tuple[str, object]] = {
        "teacher": (
            "Введите <b>ФИО преподавателя</b>:",
            repl_cancel_back_keyboard(show_back=True),
        ),
        "absence_start": (
            f"Укажите <b>{date_label}</b> (ДД.ММ.ГГГГ):",
            repl_cancel_back_keyboard(show_back=True),
        ),
        "return_date": (
            "Укажите <b>дату возвращения</b> (ДД.ММ.ГГГГ):",
            repl_cancel_back_keyboard(show_back=True),
        ),
        "comment": (
            "Комментарий к заявке (или «Пропустить»):",
            repl_comment_keyboard(),
        ),
        "student_name": (
            f"Ученик #{n}. Введите <b>ФИО</b>:",
            repl_cancel_back_keyboard(show_back=True),
        ),
        "subject": (
            f"Ученик #{n}. Введите <b>предмет</b>:",
            repl_cancel_back_keyboard(show_back=True),
        ),
        "schedule": (
            f"Ученик #{n}. Введите <b>расписание</b>\n"
            "(например: пн/ср 15:00):",
            repl_cancel_back_keyboard(show_back=True),
        ),
        "student_comment": (
            f"Ученик #{n}. Комментарий (или «Пропустить»):",
            repl_student_comment_keyboard(),
        ),
    }

    if step == "after_student":
        await message.answer(
            f"Ученик добавлен. Всего в заявке: <b>{len(students)}</b>.\n"
            "Можно добавить ещё или перейти к превью.",
            parse_mode="HTML",
            reply_markup=repl_after_student_keyboard(students_count=len(students)),
        )
        return

    if step == "preview":
        text = format_replacement_preview(data)
        await message.answer(
            text, parse_mode="HTML", reply_markup=repl_preview_keyboard()
        )
        return

    text, markup = prompts[step]
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


async def _goto_next_header(message: Message, state: FSMContext, after: str) -> None:
    data = await state.get_data()
    steps = _header_steps(str(data.get("kind", TicketType.DISMISSAL.value)))
    idx = steps.index(after)
    if idx + 1 >= len(steps):
        await _prompt_step(message, state, "student_name")
        return
    await _prompt_step(message, state, steps[idx + 1])


async def _goto_next_student(message: Message, state: FSMContext, after: str) -> None:
    idx = STUDENT_STEPS.index(after)
    await _prompt_step(message, state, STUDENT_STEPS[idx + 1])


async def _commit_draft_student(state: FSMContext) -> int:
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    students = list(data.get("students") or [])
    if draft.get("student_name"):
        draft.setdefault("student_external_id", "")
        students.append(draft)
    await state.update_data(students=students, draft_student={})
    return len(students)


# ---------------------------------------------------------------------------
# Старт
# ---------------------------------------------------------------------------


@router.message(F.text == "Подбор замены")
async def start_replacement(
    message: Message, state: FSMContext, db_user: User
) -> None:
    if not _can_create_replacement(db_user):
        await message.answer(
            "Подбор замены доступен кураторам, старшим преподавателям и администратору."
        )
        return
    await state.clear()
    await state.update_data(
        students=[], draft_student={}, current_step="choosing_kind"
    )
    await _prompt_step(message, state, "choosing_kind")


# ---------------------------------------------------------------------------
# Навигация
# ---------------------------------------------------------------------------


@router.callback_query(F.data == f"{ReplCB.NAV}cancel")
async def repl_cancel(
    callback: CallbackQuery, state: FSMContext, db_user: User
) -> None:
    current = await state.get_state() or ""
    await state.clear()
    await callback.answer("Отменено")
    if not callback.message:
        return
    if "CandidateNameFSM" in current:
        text = "Ввод кандидата отменён."
    elif "CloseReasonFSM" in current:
        text = "Закрытие отменено."
    else:
        text = "Создание заявки на подбор замены отменено."
    await callback.message.answer(text, reply_markup=main_menu_keyboard(db_user))


@router.callback_query(F.data == f"{ReplCB.NAV}back")
async def repl_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("current_step", "choosing_kind")
    kind = str(data.get("kind", TicketType.DISMISSAL.value))
    header = _header_steps(kind)
    await callback.answer()
    if not callback.message:
        return

    if current == "choosing_kind":
        await callback.message.answer(
            "Вы в начале формы. Нажмите «Отмена», чтобы выйти."
        )
        await _prompt_step(callback.message, state, "choosing_kind")
        return

    if current == "teacher":
        await _prompt_step(callback.message, state, "choosing_kind")
        return

    if current in header:
        idx = header.index(current)
        prev = header[idx - 1] if idx > 0 else "choosing_kind"
        await _prompt_step(callback.message, state, prev)
        return

    if current == "student_name":
        students = list(data.get("students") or [])
        if students:
            await _prompt_step(callback.message, state, "after_student")
        else:
            await _prompt_step(callback.message, state, "comment")
        return

    if current in STUDENT_STEPS:
        idx = STUDENT_STEPS.index(current)
        if idx == 0:
            await _prompt_step(callback.message, state, "comment")
        else:
            await _prompt_step(callback.message, state, STUDENT_STEPS[idx - 1])
        return

    if current == "preview":
        await _prompt_step(callback.message, state, "after_student")
        return

    await _prompt_step(callback.message, state, "choosing_kind")


# ---------------------------------------------------------------------------
# Шапка
# ---------------------------------------------------------------------------


@router.callback_query(
    CreateReplacementFSM.choosing_kind, F.data.startswith(ReplCB.KIND)
)
async def repl_kind_cb(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").removeprefix(ReplCB.KIND)
    try:
        kind = TicketType(raw)
    except ValueError:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    if kind not in REPLACEMENT_TICKET_TYPES:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(kind=kind.value)
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "teacher")


@router.message(CreateReplacementFSM.teacher)
async def repl_teacher(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Слишком короткое ФИО. Введите ещё раз.")
        return
    await state.update_data(teacher_name=value)
    await _goto_next_header(message, state, "teacher")


@router.message(CreateReplacementFSM.absence_start)
async def repl_absence_start(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        parsed = parse_date_ru(raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(absence_start_date=parsed.strftime("%d.%m.%Y"))
    await _goto_next_header(message, state, "absence_start")


@router.message(CreateReplacementFSM.return_date)
async def repl_return_date(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        parsed = parse_date_ru(raw)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(return_date=parsed.strftime("%d.%m.%Y"))
    await _goto_next_header(message, state, "return_date")


@router.callback_query(CreateReplacementFSM.comment, F.data == f"{ReplCB.SKIP}comment")
async def repl_skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment="")
    await callback.answer()
    if callback.message:
        await _goto_next_header(callback.message, state, "comment")


@router.message(CreateReplacementFSM.comment)
async def repl_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=(message.text or "").strip())
    await _goto_next_header(message, state, "comment")


# ---------------------------------------------------------------------------
# Ученики
# ---------------------------------------------------------------------------


@router.message(CreateReplacementFSM.student_name)
async def repl_student_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Слишком короткое имя.")
        return
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["student_name"] = value
    draft["student_external_id"] = ""
    await state.update_data(draft_student=draft)
    await _goto_next_student(message, state, "student_name")


@router.message(CreateReplacementFSM.subject)
async def repl_subject(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 1:
        await message.answer("Укажите предмет.")
        return
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["subject"] = value
    await state.update_data(draft_student=draft)
    await _goto_next_student(message, state, "subject")


@router.message(CreateReplacementFSM.schedule)
async def repl_schedule(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите расписание.")
        return
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["schedule"] = value
    await state.update_data(draft_student=draft)
    await _goto_next_student(message, state, "schedule")


@router.callback_query(
    CreateReplacementFSM.student_comment, F.data == f"{ReplCB.SKIP}stu_comment"
)
async def repl_skip_stu_comment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["comment"] = None
    await state.update_data(draft_student=draft)
    await callback.answer()
    if callback.message:
        await _commit_draft_student(state)
        await _prompt_step(callback.message, state, "after_student")


@router.message(CreateReplacementFSM.student_comment)
async def repl_stu_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["comment"] = (message.text or "").strip() or None
    await state.update_data(draft_student=draft)
    await _commit_draft_student(state)
    await _prompt_step(message, state, "after_student")


@router.callback_query(CreateReplacementFSM.after_student, F.data == f"{ReplCB.STUDENT}add")
async def repl_add_student(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "student_name")


@router.callback_query(
    CreateReplacementFSM.after_student, F.data == f"{ReplCB.STUDENT}done"
)
async def repl_students_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("students"):
        await callback.answer("Добавьте хотя бы одного ученика", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "preview")


@router.callback_query(CreateReplacementFSM.preview, F.data == f"{ReplCB.STUDENT}add")
async def repl_preview_add_student(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "student_name")


@router.callback_query(CreateReplacementFSM.preview, F.data == f"{ReplCB.PREVIEW}edit")
async def repl_preview_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "teacher")


@router.callback_query(
    CreateReplacementFSM.preview, F.data == f"{ReplCB.PREVIEW}create"
)
async def repl_preview_create(
    callback: CallbackQuery, state: FSMContext, session: Session, db_user: User
) -> None:
    data = await state.get_data()
    try:
        ticket = create_replacement_ticket(
            session,
            creator=db_user,
            kind=TicketType(data["kind"]),
            teacher_name=str(data["teacher_name"]),
            absence_start_date=str(data["absence_start_date"]),
            return_date=data.get("return_date"),
            students=list(data.get("students") or []),
            comment=data.get("comment") or None,
        )
        session.commit()
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    ticket = get_ticket_by_id(session, ticket.id) or ticket
    await state.clear()
    await callback.answer("Создано")
    if callback.message:
        await callback.message.answer(
            "✅ Заявка на подбор замены создана.\n\n"
            + format_replacement_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=replacement_ticket_card_keyboard(ticket),
        )
        await callback.message.answer(
            "Главное меню:", reply_markup=main_menu_keyboard(db_user)
        )


# ---------------------------------------------------------------------------
# Карточка подзадачи и действия
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith(ReplCB.OPEN_SUB))
async def open_repl_subtask(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    raw = (callback.data or "").removeprefix(ReplCB.OPEN_SUB)
    if not raw.isdigit():
        await callback.answer("Некорректная подзадача", show_alert=True)
        return
    sub = get_replacement_subtask(session, int(raw))
    if sub is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    ticket = sub.ticket
    if ticket is None or not can_view_ticket(db_user, ticket):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            format_replacement_subtask_card(sub),
            parse_mode="HTML",
            reply_markup=replacement_subtask_card_keyboard(sub),
        )


@router.callback_query(F.data.startswith(ReplCB.BACK_TICKET))
async def back_to_repl_ticket(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    raw = (callback.data or "").removeprefix(ReplCB.BACK_TICKET)
    if not raw.isdigit():
        await callback.answer("Некорректная заявка", show_alert=True)
        return
    ticket = get_ticket_by_id(session, int(raw))
    if ticket is None or not can_view_ticket(db_user, ticket):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            format_replacement_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=replacement_ticket_card_keyboard(ticket),
        )


async def _reload_sub_card(
    callback: CallbackQuery, session: Session, subtask_id: int
) -> None:
    sub = get_replacement_subtask(session, subtask_id)
    if sub is None or not callback.message:
        return
    await callback.message.answer(
        format_replacement_subtask_card(sub),
        parse_mode="HTML",
        reply_markup=replacement_subtask_card_keyboard(sub),
    )


@router.callback_query(F.data.startswith(f"{ReplCB.SUB_ACTION}found:"))
async def action_found_start(
    callback: CallbackQuery, state: FSMContext, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    sub = get_replacement_subtask(session, sid)
    if sub is None or sub.ticket is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    if not can_manage_ticket(db_user, sub.ticket):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CandidateNameFSM.waiting_name)
    await state.update_data(repl_subtask_id=sid)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите <b>ФИО найденного преподавателя</b>:",
            parse_mode="HTML",
            reply_markup=repl_cancel_back_keyboard(show_back=False),
        )


@router.message(CandidateNameFSM.waiting_name)
async def action_found_name(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    data = await state.get_data()
    sid = int(data.get("repl_subtask_id") or 0)
    sub = get_replacement_subtask(session, sid)
    if sub is None:
        await state.clear()
        await message.answer("Подзадача не найдена.")
        return
    try:
        set_candidate_found(
            session,
            subtask=sub,
            actor=db_user,
            candidate_name=(message.text or "").strip(),
        )
        session.commit()
    except (ValueError, PermissionError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    sub = get_replacement_subtask(session, sid)
    assert sub is not None
    await message.answer(
        format_replacement_subtask_card(sub),
        parse_mode="HTML",
        reply_markup=replacement_subtask_card_keyboard(sub),
    )


@router.callback_query(F.data.startswith(f"{ReplCB.SUB_ACTION}offered:"))
async def action_offered(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    sub = get_replacement_subtask(session, sid)
    if sub is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    try:
        mark_offered_to_parent(session, subtask=sub, actor=db_user)
        session.commit()
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Обновлено")
    await _reload_sub_card(callback, session, sid)


@router.callback_query(F.data.startswith(f"{ReplCB.SUB_ACTION}done:"))
async def action_done(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    sub = get_replacement_subtask(session, sid)
    if sub is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    try:
        mark_replacement_done(session, subtask=sub, actor=db_user)
        session.commit()
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Выполнено")
    await _reload_sub_card(callback, session, sid)


@router.callback_query(F.data.startswith(f"{ReplCB.SUB_ACTION}another:"))
async def action_another(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    sub = get_replacement_subtask(session, sid)
    if sub is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    try:
        search_another_candidate(session, subtask=sub, actor=db_user)
        session.commit()
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Ищем другого")
    await _reload_sub_card(callback, session, sid)


@router.callback_query(F.data.startswith(f"{ReplCB.SUB_ACTION}help:"))
async def action_senior_help(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    sub = get_replacement_subtask(session, sid)
    if sub is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    try:
        sub, seniors = request_senior_help(session, subtask=sub, actor=db_user)
        session.commit()
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    text = format_senior_help_message(sub, db_user)
    notified = 0
    bot = callback.bot
    for senior in seniors:
        if senior.telegram_id == db_user.telegram_id:
            continue
        try:
            await bot.send_message(senior.telegram_id, text, parse_mode="HTML")
            notified += 1
        except Exception:
            # чат недоступен — не валим действие
            continue

    await callback.answer(
        f"Старшим отправлено: {notified}" if notified else "Помечено; доставить некому",
        show_alert=True,
    )
    await _reload_sub_card(callback, session, sid)
    if callback.message and notified == 0 and seniors:
        await callback.message.answer(
            "Помощь старшего помечена в заявке. "
            "Уведомления не доставлены (нет чата со старшими / админами)."
        )


@router.callback_query(F.data.startswith(f"{ReplCB.SUB_ACTION}nocont:"))
async def action_no_continue_start(
    callback: CallbackQuery, state: FSMContext, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    sub = get_replacement_subtask(session, sid)
    if sub is None or sub.ticket is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    if not can_manage_ticket(db_user, sub.ticket):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CloseReasonFSM.waiting_reason)
    await state.update_data(repl_subtask_id=sid)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Укажите <b>причину</b>, почему ученик не продолжает:",
            parse_mode="HTML",
            reply_markup=repl_cancel_back_keyboard(show_back=False),
        )


@router.message(CloseReasonFSM.waiting_reason)
async def action_no_continue_reason(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    data = await state.get_data()
    sid = int(data.get("repl_subtask_id") or 0)
    sub = get_replacement_subtask(session, sid)
    if sub is None:
        await state.clear()
        await message.answer("Подзадача не найдена.")
        return
    try:
        close_student_not_continuing(
            session,
            subtask=sub,
            actor=db_user,
            reason=(message.text or "").strip(),
        )
        session.commit()
    except (ValueError, PermissionError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    sub = get_replacement_subtask(session, sid)
    assert sub is not None
    await message.answer(
        format_replacement_subtask_card(sub),
        parse_mode="HTML",
        reply_markup=replacement_subtask_card_keyboard(sub),
    )
