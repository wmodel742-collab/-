"""Массовый перенос: FSM создания и действия по подзадачам (этап 3)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import (
    MassSubtaskAction,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
)
from bot.keyboards.mass import (
    MassCB,
    mass_after_student_keyboard,
    mass_cancel_back_keyboard,
    mass_comment_keyboard,
    mass_initiator_keyboard,
    mass_preview_keyboard,
    mass_required_action_keyboard,
    mass_student_comment_keyboard,
    mass_ticket_card_keyboard,
    subtask_card_keyboard,
)
from bot.keyboards.menus import main_menu_keyboard
from bot.middlewares.auth import user_has_role
from bot.services.mass import (
    add_subtask_comment,
    change_subtask_status,
    create_mass_ticket,
    format_mass_preview,
    format_mass_ticket_card,
    format_subtask_card,
    get_subtask_by_id,
)
from bot.services.tickets import can_manage_ticket, can_view_ticket, get_ticket_by_id

router = Router(name="mass")


class CreateMassFSM(StatesGroup):
    teacher = State()
    reason = State()
    absence_period = State()
    initiator = State()
    comment = State()
    # цикл учеников
    student_name = State()
    student_id = State()
    lesson_datetime = State()
    desired_new_datetime = State()
    required_action = State()
    student_comment = State()
    after_student = State()
    preview = State()


class CommentSubtaskFSM(StatesGroup):
    waiting_text = State()


HEADER_STEPS = (
    "teacher",
    "reason",
    "absence_period",
    "initiator",
    "comment",
)
STUDENT_STEPS = (
    "student_name",
    "lesson_datetime",
    "desired_new_datetime",
    "required_action",
    "student_comment",
    "after_student",
)

STEP_TO_STATE: dict[str, State] = {
    "teacher": CreateMassFSM.teacher,
    "reason": CreateMassFSM.reason,
    "absence_period": CreateMassFSM.absence_period,
    "initiator": CreateMassFSM.initiator,
    "comment": CreateMassFSM.comment,
    "student_name": CreateMassFSM.student_name,
    "lesson_datetime": CreateMassFSM.lesson_datetime,
    "desired_new_datetime": CreateMassFSM.desired_new_datetime,
    "required_action": CreateMassFSM.required_action,
    "student_comment": CreateMassFSM.student_comment,
    "after_student": CreateMassFSM.after_student,
    "preview": CreateMassFSM.preview,
}


def _can_create_mass(user: User) -> bool:
    return user_has_role(user, UserRole.CURATOR, UserRole.ADMIN)


async def _prompt_step(message: Message, state: FSMContext, step: str) -> None:
    await state.update_data(current_step=step)
    await state.set_state(STEP_TO_STATE[step])
    data = await state.get_data()
    students = list(data.get("students") or [])
    n = len(students) + 1

    prompts: dict[str, tuple[str, object]] = {
        "teacher": (
            "Массовый перенос.\nВведите <b>имя преподавателя</b>:",
            mass_cancel_back_keyboard(show_back=False),
        ),
        "reason": (
            "Укажите <b>причину</b> (больничный, отпуск и т.п.):",
            mass_cancel_back_keyboard(show_back=True),
        ),
        "absence_period": (
            "Укажите <b>период отсутствия</b>\n"
            "(например: 20.11.2026 – 25.11.2026):",
            mass_cancel_back_keyboard(show_back=True),
        ),
        "initiator": (
            "Кто <b>инициатор</b>?",
            mass_initiator_keyboard(),
        ),
        "comment": (
            "Общий комментарий к заявке (или «Пропустить»):\n"
            "<i>Заметку о выбранной замене можно добавить позже кнопкой "
            "«Заметка о замене» на карточке.</i>",
            mass_comment_keyboard(),
        ),
        "student_name": (
            f"Ученик #{n}. Введите <b>ФИО</b>:",
            mass_cancel_back_keyboard(show_back=True),
        ),
        "lesson_datetime": (
            f"Ученик #{n}. <b>Даты/время занятий</b>\n"
            "(например: 21.11 14:00, 23.11 14:00):",
            mass_cancel_back_keyboard(show_back=True),
        ),
        "desired_new_datetime": (
            f"Ученик #{n}. <b>Желаемые новые даты</b>\n"
            "(или «—» если неизвестно):",
            mass_cancel_back_keyboard(show_back=True),
        ),
        "required_action": (
            f"Ученик #{n}. Выберите <b>необходимое действие</b>:",
            mass_required_action_keyboard(),
        ),
        "student_comment": (
            f"Ученик #{n}. Комментарий (или «Пропустить»):",
            mass_student_comment_keyboard(),
        ),
    }

    if step == "after_student":
        await message.answer(
            f"Ученик добавлен. Всего в заявке: <b>{len(students)}</b>.\n"
            "Можно добавить ещё или перейти к превью.",
            parse_mode="HTML",
            reply_markup=mass_after_student_keyboard(students_count=len(students)),
        )
        return

    if step == "preview":
        text = format_mass_preview(data)
        await message.answer(
            text, parse_mode="HTML", reply_markup=mass_preview_keyboard()
        )
        return

    text, markup = prompts[step]
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


async def _goto_next_header(message: Message, state: FSMContext, after: str) -> None:
    idx = HEADER_STEPS.index(after)
    if idx + 1 >= len(HEADER_STEPS):
        await _prompt_step(message, state, "student_name")
        return
    await _prompt_step(message, state, HEADER_STEPS[idx + 1])


async def _goto_next_student(message: Message, state: FSMContext, after: str) -> None:
    idx = STUDENT_STEPS.index(after)
    await _prompt_step(message, state, STUDENT_STEPS[idx + 1])


async def _commit_draft_student(state: FSMContext) -> int:
    """Перенести draft_student в students. Возвращает число учеников."""
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


@router.message(F.text == "Массовый перенос")
async def start_mass(
    message: Message, state: FSMContext, db_user: User
) -> None:
    if not _can_create_mass(db_user):
        await message.answer("Массовый перенос доступен кураторам и администратору.")
        return
    await state.clear()
    await state.update_data(students=[], draft_student={}, current_step="teacher")
    await _prompt_step(message, state, "teacher")


# ---------------------------------------------------------------------------
# Навигация
# ---------------------------------------------------------------------------


@router.callback_query(F.data == f"{MassCB.NAV}cancel")
async def mass_cancel(
    callback: CallbackQuery, state: FSMContext, db_user: User
) -> None:
    current = await state.get_state() or ""
    await state.clear()
    await callback.answer("Отменено")
    if not callback.message:
        return
    if "CommentSubtaskFSM" in current:
        text = "Ввод комментария отменён."
    else:
        text = "Создание массовой заявки отменено."
    await callback.message.answer(text, reply_markup=main_menu_keyboard(db_user))


@router.callback_query(F.data == f"{MassCB.NAV}back")
async def mass_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("current_step", "teacher")
    await callback.answer()
    if not callback.message:
        return

    if current == "teacher":
        await callback.message.answer(
            "Вы в начале формы. Нажмите «Отмена», чтобы выйти."
        )
        await _prompt_step(callback.message, state, "teacher")
        return

    if current in HEADER_STEPS:
        idx = HEADER_STEPS.index(current)
        prev = HEADER_STEPS[idx - 1] if idx > 0 else "teacher"
        await _prompt_step(callback.message, state, prev)
        return

    if current == "student_name":
        students = list(data.get("students") or [])
        if students:
            # вернуться к меню после последнего ученика
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

    await _prompt_step(callback.message, state, "teacher")


# ---------------------------------------------------------------------------
# Шапка заявки
# ---------------------------------------------------------------------------


@router.message(CreateMassFSM.teacher)
async def mass_teacher(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Слишком короткое имя. Введите ещё раз.")
        return
    await state.update_data(teacher_name=value)
    await _goto_next_header(message, state, "teacher")


@router.message(CreateMassFSM.reason)
async def mass_reason(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите причину.")
        return
    await state.update_data(reason=value)
    await _goto_next_header(message, state, "reason")


@router.message(CreateMassFSM.absence_period)
async def mass_period(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите период отсутствия.")
        return
    await state.update_data(absence_period=value)
    await _goto_next_header(message, state, "absence_period")


@router.callback_query(CreateMassFSM.initiator, F.data.startswith(MassCB.INIT))
async def mass_initiator_cb(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").removeprefix(MassCB.INIT)
    try:
        TicketInitiator(raw)
    except ValueError:
        await callback.answer("Неизвестный инициатор", show_alert=True)
        return
    await state.update_data(initiator=raw)
    await callback.answer()
    if callback.message:
        await _goto_next_header(callback.message, state, "initiator")


@router.callback_query(CreateMassFSM.comment, F.data == f"{MassCB.SKIP}comment")
async def mass_skip_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(comment="")
    await callback.answer()
    if callback.message:
        await _goto_next_header(callback.message, state, "comment")


@router.message(CreateMassFSM.comment)
async def mass_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=(message.text or "").strip())
    await _goto_next_header(message, state, "comment")


# ---------------------------------------------------------------------------
# Ученики
# ---------------------------------------------------------------------------


@router.message(CreateMassFSM.student_name)
async def mass_student_name(message: Message, state: FSMContext) -> None:
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


@router.message(CreateMassFSM.lesson_datetime)
async def mass_lesson_dt(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("Укажите даты/время занятий.")
        return
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["lesson_datetime"] = value
    await state.update_data(draft_student=draft)
    await _goto_next_student(message, state, "lesson_datetime")


@router.message(CreateMassFSM.desired_new_datetime)
async def mass_desired_dt(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value in {"—", "-", "нет", "Нет", "неизвестно"}:
        value = ""
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["desired_new_datetime"] = value
    await state.update_data(draft_student=draft)
    await _goto_next_student(message, state, "desired_new_datetime")


@router.callback_query(
    CreateMassFSM.required_action, F.data.startswith(MassCB.ACTION)
)
async def mass_required_action(callback: CallbackQuery, state: FSMContext) -> None:
    raw = (callback.data or "").removeprefix(MassCB.ACTION)
    try:
        MassSubtaskAction(raw)
    except ValueError:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["required_action"] = raw
    await state.update_data(draft_student=draft)
    await callback.answer()
    if callback.message:
        await _goto_next_student(callback.message, state, "required_action")


@router.callback_query(
    CreateMassFSM.student_comment, F.data == f"{MassCB.SKIP}stu_comment"
)
async def mass_skip_stu_comment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["comment"] = ""
    await state.update_data(draft_student=draft)
    await _commit_draft_student(state)
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "after_student")


@router.message(CreateMassFSM.student_comment)
async def mass_stu_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    draft = dict(data.get("draft_student") or {})
    draft["comment"] = (message.text or "").strip()
    await state.update_data(draft_student=draft)
    await _commit_draft_student(state)
    await _prompt_step(message, state, "after_student")


@router.callback_query(CreateMassFSM.after_student, F.data == f"{MassCB.STUDENT}add")
async def mass_add_another(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if len(data.get("students") or []) >= 30:
        await callback.answer("Достигнут лимит 30 учеников", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "student_name")


@router.callback_query(CreateMassFSM.after_student, F.data == f"{MassCB.STUDENT}done")
async def mass_students_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("students"):
        await callback.answer("Добавьте хотя бы одного ученика", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await _prompt_step(callback.message, state, "preview")


@router.callback_query(CreateMassFSM.preview, F.data == f"{MassCB.STUDENT}add")
async def mass_preview_add(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if len(data.get("students") or []) >= 30:
        await callback.answer("Достигнут лимит 30 учеников", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _prompt_step(callback.message, state, "student_name")


@router.callback_query(CreateMassFSM.preview, F.data == f"{MassCB.PREVIEW}edit")
async def mass_preview_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _prompt_step(callback.message, state, "teacher")


@router.callback_query(F.data == f"{MassCB.PREVIEW}edit")
async def mass_preview_edit_stale(callback: CallbackQuery) -> None:
    await callback.answer(
        "Форма устарела. Начните «Массовый перенос» заново.",
        show_alert=True,
    )

@router.callback_query(F.data == f"{MassCB.PREVIEW}create")
async def mass_preview_create(
    callback: CallbackQuery,
    state: FSMContext,
    session: Session,
    db_user: User,
) -> None:
    """Создать массовую заявку.

    Важно: сначала callback.answer() — иначе Telegram крутит кнопку, пока идёт запись в БД
    (Отмена/Изменить шапку отвечают сразу и поэтому «работают»).
    """
    if not _can_create_mass(db_user):
        await callback.answer("Нет прав", show_alert=True)
        return
    data = await state.get_data()
    required = ("teacher_name", "reason", "absence_period", "initiator")
    if any(not data.get(key) for key in required) or not data.get("students"):
        await callback.answer(
            "Форма устарела или неполная. Начните «Массовый перенос» заново.",
            show_alert=True,
        )
        return

    # Сразу гасим «часики» у кнопки — до тяжёлой работы с БД
    await callback.answer("Создаю…")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    try:
        ticket = create_mass_ticket(
            session,
            creator=db_user,
            teacher_name=str(data["teacher_name"]),
            reason=str(data["reason"]),
            absence_period=str(data["absence_period"]),
            initiator=TicketInitiator(data["initiator"]),
            students=list(data.get("students") or []),
            comment=data.get("comment") or None,
        )
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
            "✅ Массовая заявка создана.\n"
            f"{notify_status}\n\n"
            + format_mass_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=mass_ticket_card_keyboard(ticket),
        )
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu_keyboard(db_user),
        )


# ---------------------------------------------------------------------------
# Карточка подзадачи и действия
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith(MassCB.OPEN_SUB))
async def open_subtask(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    raw = (callback.data or "").removeprefix(MassCB.OPEN_SUB)
    if not raw.isdigit():
        await callback.answer("Некорректная подзадача", show_alert=True)
        return
    subtask = get_subtask_by_id(session, int(raw))
    if subtask is None:
        await callback.answer("Подзадача не найдена", show_alert=True)
        return
    ticket = subtask.ticket
    if ticket is None or not can_view_ticket(db_user, ticket):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            format_subtask_card(subtask),
            parse_mode="HTML",
            reply_markup=subtask_card_keyboard(subtask),
        )


@router.callback_query(F.data.startswith(MassCB.BACK_TICKET))
async def back_to_mass_ticket(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    raw = (callback.data or "").removeprefix(MassCB.BACK_TICKET)
    if not raw.isdigit():
        await callback.answer("Некорректная заявка", show_alert=True)
        return
    ticket = get_ticket_by_id(session, int(raw))
    if ticket is None or ticket.ticket_type != TicketType.MASS:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not can_view_ticket(db_user, ticket):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            format_mass_ticket_card(ticket, with_history=False),
            parse_mode="HTML",
            reply_markup=mass_ticket_card_keyboard(ticket),
        )


async def _apply_sub_status(
    callback: CallbackQuery,
    session: Session,
    db_user: User,
    *,
    subtask_id: int,
    new_status: TicketStatus,
    comment: str | None,
) -> None:
    subtask = get_subtask_by_id(session, subtask_id)
    if subtask is None:
        await callback.answer("Подзадача не найдена", show_alert=True)
        return
    ticket = subtask.ticket
    if ticket is None or not can_manage_ticket(db_user, ticket):
        await callback.answer("Нет прав", show_alert=True)
        return
    try:
        change_subtask_status(
            session,
            subtask=subtask,
            new_status=new_status,
            actor=db_user,
            comment=comment,
        )
    except PermissionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    subtask = get_subtask_by_id(session, subtask_id) or subtask
    await callback.answer("Обновлено")
    if callback.message:
        await callback.message.answer(
            format_subtask_card(subtask),
            parse_mode="HTML",
            reply_markup=subtask_card_keyboard(subtask),
        )


@router.callback_query(F.data.startswith(f"{MassCB.SUB_ACTION}wrote:"))
async def sub_wrote(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    await _apply_sub_status(
        callback,
        session,
        db_user,
        subtask_id=sid,
        new_status=TicketStatus.WAITING_REPLY,
        comment="Написали ученику",
    )


@router.callback_query(F.data.startswith(f"{MassCB.SUB_ACTION}done:"))
async def sub_done(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    await _apply_sub_status(
        callback,
        session,
        db_user,
        subtask_id=sid,
        new_status=TicketStatus.DONE,
        comment="Отмечено выполненным",
    )


@router.callback_query(F.data.startswith(f"{MassCB.SUB_ACTION}reject:"))
async def sub_reject(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    await _apply_sub_status(
        callback,
        session,
        db_user,
        subtask_id=sid,
        new_status=TicketStatus.REJECTED,
        comment="Отказ",
    )


@router.callback_query(F.data.startswith(f"{MassCB.SUB_ACTION}no_reply:"))
async def sub_no_reply(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    await _apply_sub_status(
        callback,
        session,
        db_user,
        subtask_id=sid,
        new_status=TicketStatus.WAITING_REPLY,
        comment="Ответа пока нет",
    )


@router.callback_query(F.data.startswith(f"{MassCB.SUB_ACTION}other_time:"))
async def sub_other_time(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    sid = int((callback.data or "").split(":")[-1])
    await _apply_sub_status(
        callback,
        session,
        db_user,
        subtask_id=sid,
        new_status=TicketStatus.WAITING_REPLY,
        comment="Предложили другое время",
    )


@router.callback_query(F.data.startswith(f"{MassCB.SUB_ACTION}comment:"))
async def sub_comment_start(
    callback: CallbackQuery, state: FSMContext, session: Session, db_user: User
) -> None:
    from bot.services.notify import can_comment_ticket

    sid = int((callback.data or "").split(":")[-1])
    subtask = get_subtask_by_id(session, sid)
    if subtask is None:
        await callback.answer("Подзадача не найдена", show_alert=True)
        return
    ticket = subtask.ticket
    if ticket is None or not can_comment_ticket(db_user, ticket):
        await callback.answer("Нет прав", show_alert=True)
        return
    await state.set_state(CommentSubtaskFSM.waiting_text)
    await state.update_data(comment_subtask_id=sid)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"Введите заметку / комментарий к ученику {subtask.student_name}\n"
            "(куратор увидит текст на карточке):",
            reply_markup=mass_cancel_back_keyboard(show_back=False),
        )


@router.message(CommentSubtaskFSM.waiting_text)
async def sub_comment_save(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    data = await state.get_data()
    sid = int(data.get("comment_subtask_id") or 0)
    await state.clear()
    subtask = get_subtask_by_id(session, sid)
    if subtask is None:
        await message.answer("Подзадача не найдена.")
        return
    try:
        subtask, recipients, notify_text = add_subtask_comment(
            session,
            subtask=subtask,
            actor=db_user,
            comment=message.text or "",
        )
    except (ValueError, PermissionError) as exc:
        await message.answer(f"⚠️ {exc}")
        return
    session.commit()
    notified = 0
    for user in recipients:
        try:
            await message.bot.send_message(
                user.telegram_id, notify_text, parse_mode="HTML"
            )
            notified += 1
        except Exception:
            continue
    subtask = get_subtask_by_id(session, sid) or subtask
    note = f"\nУведомлены админы/старшие: {notified}" if recipients else ""
    await message.answer(
        "Комментарий сохранён." + note + "\n\n" + format_subtask_card(subtask),
        parse_mode="HTML",
        reply_markup=subtask_card_keyboard(subtask),
    )
