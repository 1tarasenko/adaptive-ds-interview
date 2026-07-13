"""Основная бизнес-логика backend-а.

main.py принимает HTTP-запросы, а этот файл выполняет действия:
создает сессию интервью, выбирает задания, сохраняет ответы, обновляет BKT,
считает риск аномального поведения и завершает сессию.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg2.extras import Json

from . import bkt
from .behavior import normalize_behavior, score_anomaly_risk
from .config import settings
from .db import execute_one, fetch_all, fetch_one, get_connection
from .evaluation import EvaluationResult, EvaluationUnavailable, evaluate_answer, generate_session_report
from .scoring import build_session_scoring
from .schemas import StartSessionRequest, SubmitAnswerRequest


def healthcheck() -> dict[str, Any]:
    """Проверяет, что backend может подключиться к базе данных."""

    conn = get_connection()
    try:
        row = fetch_one(conn, "SELECT 1 AS ok")
        return {"status": "ok", "database": bool(row and row["ok"] == 1)}
    finally:
        conn.close()


def list_competencies() -> list[dict[str, Any]]:
    """Возвращает активные компетенции для frontend-а и отладки."""

    conn = get_connection()
    try:
        return fetch_all(
            conn,
            """
            SELECT id, code, title, group_name, description, initial_probability
            FROM competencies
            WHERE is_active = TRUE
            ORDER BY group_name, title
            """,
        )
    finally:
        conn.close()


def list_tasks() -> list[dict[str, Any]]:
    """Возвращает активные задания из банка вопросов."""

    conn = get_connection()
    try:
        return fetch_all(
            conn,
            """
            SELECT
                t.id,
                t.title,
                t.question_text,
                t.task_type,
                t.difficulty,
                t.options,
                c.code AS competency_code,
                c.title AS competency_title
            FROM tasks t
            JOIN competencies c ON c.id = t.competency_id
            WHERE t.is_active = TRUE
            ORDER BY t.id
            """,
        )
    finally:
        conn.close()


def start_session(payload: StartSessionRequest) -> dict[str, Any]:
    """Создает кандидата, сессию и стартовые BKT-состояния.

    Это первый шаг пользовательского сценария: кандидат ввел ФИО/email, после
    чего backend готовит интервью и сразу подбирает первое задание.
    """

    conn = get_connection()
    try:
        candidate = execute_one(
            conn,
            """
            INSERT INTO candidates (full_name, email)
            VALUES (%s, %s)
            ON CONFLICT (email) DO UPDATE SET
                full_name = EXCLUDED.full_name
            RETURNING id, full_name, email
            """,
            (payload.full_name, payload.email),
        )

        session = execute_one(
            conn,
            """
            INSERT INTO interview_sessions (candidate_id)
            VALUES (%s)
            RETURNING id, candidate_id, status, started_at
            """,
            (candidate["id"],),
        )

        execute_one(
            conn,
            """
            WITH inserted AS (
                INSERT INTO bkt_states (session_id, competency_id, probability)
                SELECT %s, id, initial_probability
                FROM competencies
                WHERE is_active = TRUE
                RETURNING id
            )
            SELECT COUNT(*) AS inserted_count FROM inserted
            """,
            (session["id"],),
        )

        next_task = _select_next_task(conn, session["id"])
        states = _get_bkt_states(conn, session["id"])
        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "candidate": candidate,
        "session": session,
        "bkt_states": states,
        "next_task": next_task,
        "max_tasks": settings.max_tasks_per_session,
    }


def get_session(session_id: int) -> dict[str, Any]:
    """Возвращает полное состояние сессии интервью."""

    conn = get_connection()
    try:
        session = fetch_one(
            conn,
            """
            SELECT
                s.id,
                s.status,
                s.started_at,
                s.finished_at,
                s.final_score,
                s.final_bkt_profile,
                s.anomaly_probability,
                s.anomaly_risk,
                s.report_text,
                c.full_name,
                c.email,
                COUNT(a.id) AS answers_count
            FROM interview_sessions s
            JOIN candidates c ON c.id = s.candidate_id
            LEFT JOIN answers a ON a.session_id = s.id
            WHERE s.id = %s
            GROUP BY s.id, c.id
            """,
            (session_id,),
        )
        if not session:
            raise LookupError("Сессия не найдена.")

        session["bkt_states"] = _get_bkt_states(conn, session_id)

        session["answers"] = fetch_all(
            conn,
            """
            SELECT
                a.id,
                a.position_in_session,
                a.task_id,
                t.title AS task_title,
                t.task_type,
                t.difficulty,
                c.title AS competency_title,
                c.code AS competency_code,
                a.is_correct,
                a.score,
                a.bkt_before,
                a.bkt_after,
                a.response_time_sec,
                bf.anomaly_probability,
                bf.anomaly_risk,
                CASE
                    WHEN a.score IS NULL OR a.is_correct IS NULL THEN 'evaluation_failed'
                    ELSE 'evaluated'
                END AS evaluation_status,
                a.created_at
            FROM answers a
            JOIN tasks t ON t.id = a.task_id
            JOIN competencies c ON c.id = a.competency_id
            LEFT JOIN behavior_features bf ON bf.answer_id = a.id
            WHERE a.session_id = %s
            ORDER BY a.position_in_session
            """,
            (session_id,),
        )
        session["score_profile"] = session["final_bkt_profile"]
        return session
    finally:
        conn.close()


def get_next_task(session_id: int) -> dict[str, Any] | None:
    """Возвращает следующее задание для активной сессии."""

    conn = get_connection()
    try:
        _require_in_progress_session(conn, session_id)
        return _select_next_task(conn, session_id)
    finally:
        conn.close()


def submit_answer(session_id: int, payload: SubmitAnswerRequest) -> dict[str, Any]:
    """Обрабатывает один ответ кандидата end-to-end.

    Здесь находится главный сценарий backend-а:
    1. достать задание;
    2. оценить ответ;
    3. обновить BKT;
    4. сохранить поведенческие признаки;
    5. вернуть следующее задание или завершить сессию.
    """

    conn = get_connection()
    try:
        # Блокируем строку сессии через FOR UPDATE, чтобы два параллельных
        # запроса не записали два ответа одновременно в одну позицию.
        _require_in_progress_session(conn, session_id, for_update=True)

        expected_task = _select_next_task(conn, session_id)
        if not expected_task:
            raise ValueError("Для сессии больше нет доступных заданий.")
        if int(expected_task["id"]) != payload.task_id:
            raise ValueError(
                "Можно отправить ответ только на текущее задание сессии."
            )

        task = _get_task_for_answer(conn, payload.task_id)

        if _answer_exists(conn, session_id, payload.task_id):
            raise ValueError(
                "Это задание уже было отправлено в рамках текущей сессии."
            )

        behavior = normalize_behavior(payload.behavior, payload.answer_text)

        bkt_state = _get_or_create_bkt_state(conn, session_id, task)
        bkt_before = float(bkt_state["probability"])

        try:
            evaluation = evaluate_answer(task, payload.answer_text, payload.answer_payload)
        except EvaluationUnavailable as exc:
            # Техническая ошибка LLM не должна превращаться в неверный ответ:
            # сохраняем попытку как неоцененную и не меняем BKT.
            evaluation = EvaluationResult.failed(str(exc))

        # BKT меняется только если оценка действительно получена.
        if evaluation.evaluation_status == "evaluated":
            bkt_after = bkt.update_probability(bkt_before, bool(evaluation.is_correct))
        else:
            bkt_after = bkt_before

        position = _next_answer_position(conn, session_id)

        # Если frontend не передал started_at/submitted_at, backend сам
        # восстанавливает временные значения.
        response_time = int(behavior.response_time_sec or 0)
        submitted_at = payload.submitted_at or datetime.now(timezone.utc)
        started_at = payload.started_at or submitted_at - timedelta(seconds=response_time)

        answer = execute_one(
            conn,
            """
            INSERT INTO answers (
                session_id,
                task_id,
                competency_id,
                position_in_session,
                answer_text,
                answer_payload,
                is_correct,
                score,
                llm_feedback,
                bkt_before,
                bkt_after,
                started_at,
                submitted_at,
                response_time_sec
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, position_in_session, is_correct, score, llm_feedback, bkt_before, bkt_after
            """,
            (
                session_id,
                task["id"],
                task["competency_id"],
                position,
                payload.answer_text,
                Json(payload.answer_payload),
                evaluation.is_correct,
                evaluation.score,
                evaluation.feedback,
                bkt_before,
                bkt_after,
                started_at,
                submitted_at,
                response_time,
            ),
        )
        answer["evaluation_status"] = evaluation.evaluation_status

        if evaluation.evaluation_status == "evaluated":
            # Обновляем текущее состояние BKT по этой компетенции только после
            # валидной оценки ответа.
            execute_one(
                conn,
                """
                UPDATE bkt_states
                SET probability = %s,
                    updated_at = now()
                WHERE session_id = %s
                  AND competency_id = %s
                RETURNING id
                """,
                (bkt_after, session_id, task["competency_id"]),
            )

        anomaly_probability, anomaly_risk = score_anomaly_risk(behavior)

        behavior_row = _save_behavior_features(
            conn,
            session_id,
            answer["id"],
            behavior,
            anomaly_probability,
            anomaly_risk,
        )

        answers_count = _answers_count(conn, session_id)
        next_task = None if answers_count >= settings.max_tasks_per_session else _select_next_task(conn, session_id)

        finished = next_task is None
        session_summary = _finish_session(conn, session_id) if finished else None
        bkt_states = _get_bkt_states(conn, session_id)

        # Все изменения по ответу сохраняются одной транзакцией.
        conn.commit()
    except Exception:
        # Если ошибка возникла в середине сценария, rollback отменит вставку
        # ответа, обновление BKT и запись поведенческих признаков.
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "answer": answer,
        "evaluation": {
            "is_correct": evaluation.is_correct,
            "score": evaluation.score,
            "feedback": evaluation.feedback,
            "status": evaluation.evaluation_status,
        },
        "behavior": behavior_row,
        "bkt_states": bkt_states,
        "next_task": next_task,
        "session_finished": finished,
        "session_summary": session_summary,
    }


def finish_session(session_id: int, report_text: str | None = None) -> dict[str, Any]:
    """Завершает активную сессию вручную."""

    conn = get_connection()
    try:
        _require_in_progress_session(conn, session_id, for_update=True)

        summary = _finish_session(conn, session_id, report_text)
        conn.commit()
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _require_in_progress_session(conn, session_id: int, for_update: bool = False) -> dict[str, Any]:
    """Проверяет, что сессия существует и находится в статусе in_progress."""

    lock = "FOR UPDATE" if for_update else ""
    session = fetch_one(
        conn,
        f"""
        SELECT id, status
        FROM interview_sessions
        WHERE id = %s
        {lock}
        """,
        (session_id,),
    )
    if not session:
        raise LookupError("Сессия не найдена.")
    if session["status"] != "in_progress":
        raise ValueError("Сессия уже завершена или остановлена.")
    return session


def _select_next_task(conn, session_id: int) -> dict[str, Any] | None:
    """Выбирает следующее задание на основе BKT-состояний."""

    # Берем BKT-состояния по всем компетенциям сессии.
    # Компетенции, которые уже несколько раз проверялись и остались ниже
    # порога, считаются зафиксированными слабыми областями и не получают
    # приоритет в дальнейшей выдаче.
    states = fetch_all(
        conn,
        """
        SELECT
            b.competency_id,
            b.probability,
            c.code AS competency_code,
            c.title AS competency_title,
            COUNT(a.id) AS answered_count
        FROM bkt_states b
        JOIN competencies c ON c.id = b.competency_id
        LEFT JOIN answers a
          ON a.session_id = b.session_id
         AND a.competency_id = b.competency_id
        WHERE b.session_id = %s
          AND c.is_active = TRUE
        GROUP BY b.competency_id, b.probability, c.code, c.title, c.id
        ORDER BY b.probability ASC, c.id ASC
        """,
        (session_id,),
    )

    # Этот SQL-фрагмент исключает задания, на которые кандидат уже отвечал в
    # текущей сессии.
    answered_filter = """
        AND NOT EXISTS (
            SELECT 1
            FROM answers a
            WHERE a.session_id = %s
              AND a.task_id = t.id
        )
    """

    selectable_states = [
        state
        for state in states
        if not _is_low_competency_saturated(state)
    ]

    # Сначала пробуем дать задачу по компетенции с минимальной вероятностью,
    # которая еще не была зафиксирована как слабая после нескольких попыток.
    for state in selectable_states:
        # По текущей вероятности знания выбираем желаемую сложность.
        preferred_difficulty = bkt.difficulty_for_probability(float(state["probability"]))

        # Сначала ищем задание нужной сложности по конкретной компетенции.
        task = _find_task(conn, session_id, state["competency_id"], preferred_difficulty, answered_filter)
        if task:
            return task

        # Если задания нужной сложности нет, берем любое активное задание по
        # этой компетенции.
        task = _find_task(conn, session_id, state["competency_id"], None, answered_filter)
        if task:
            return task

    # Если по незаблокированным BKT-состояниям ничего не найдено, берем любое
    # активное задание из общего банка, но только по компетенциям, которые не
    # были уже зафиксированы как слабые.
    selectable_competency_ids = [int(state["competency_id"]) for state in selectable_states]
    if not selectable_competency_ids:
        return None

    placeholders = ", ".join(["%s"] * len(selectable_competency_ids))
    return fetch_one(
        conn,
        f"""
        SELECT
            t.id,
            t.title,
            t.question_text,
            t.task_type,
            t.difficulty,
            t.options,
            c.code AS competency_code,
            c.title AS competency_title
        FROM tasks t
        JOIN competencies c ON c.id = t.competency_id
        WHERE t.is_active = TRUE
          AND t.competency_id IN ({placeholders})
        {answered_filter}
        ORDER BY t.id
        LIMIT 1
        """,
        (*selectable_competency_ids, session_id),
    )


def _find_task(conn, session_id: int, competency_id: int, difficulty: str | None, answered_filter: str):
    """Ищет одно активное задание по компетенции и, если нужно, сложности."""

    difficulty_filter = "AND t.difficulty = %s" if difficulty else ""

    params: tuple[Any, ...] = (
        (competency_id, session_id, difficulty)
        if difficulty
        else (competency_id, session_id)
    )
    return fetch_one(
        conn,
        f"""
        SELECT
            t.id,
            t.title,
            t.question_text,
            t.task_type,
            t.difficulty,
            t.options,
            c.code AS competency_code,
            c.title AS competency_title
        FROM tasks t
        JOIN competencies c ON c.id = t.competency_id
        WHERE t.is_active = TRUE
          AND t.competency_id = %s
          {answered_filter}
          {difficulty_filter}
        ORDER BY t.id
        LIMIT 1
        """,
        params,
    )


def _is_low_competency_saturated(state: dict[str, Any]) -> bool:
    """Проверяет, нужно ли перестать давить на слабую компетенцию."""

    probability = float(state["probability"])
    answered_count = int(state.get("answered_count") or 0)
    return (
        answered_count >= settings.max_low_competency_attempts
        and probability < settings.low_competency_threshold
    )


def _get_task_for_answer(conn, task_id: int) -> dict[str, Any]:
    """Возвращает задание, на которое кандидат отправляет ответ."""

    task = fetch_one(
        conn,
        """
        SELECT
            t.id,
            t.competency_id,
            t.title,
            t.question_text,
            t.task_type,
            t.difficulty,
            t.options,
            t.correct_answer,
            t.rubric,
            c.initial_probability
        FROM tasks t
        JOIN competencies c ON c.id = t.competency_id
        WHERE t.id = %s
          AND t.is_active = TRUE
        """,
        (task_id,),
    )
    if not task:
        raise LookupError("Задание не найдено.")
    return task


def _answer_exists(conn, session_id: int, task_id: int) -> bool:
    """Проверяет, отвечал ли кандидат уже на это задание в текущей сессии."""

    row = fetch_one(
        conn,
        "SELECT 1 AS exists_flag FROM answers WHERE session_id = %s AND task_id = %s",
        (session_id, task_id),
    )
    return bool(row)


def _get_or_create_bkt_state(conn, session_id: int, task: dict[str, Any]) -> dict[str, Any]:
    """Возвращает BKT-состояние по компетенции задания или создает его."""

    # Обычно BKT-состояния создаются при старте сессии. Этот fallback нужен,
    # чтобы backend не сломался, если в сессии не оказалось нужной строки.
    state = fetch_one(
        conn,
        """
        SELECT id, probability
        FROM bkt_states
        WHERE session_id = %s
          AND competency_id = %s
        FOR UPDATE
        """,
        (session_id, task["competency_id"]),
    )
    if state:
        return state

    return execute_one(
        conn,
        """
        INSERT INTO bkt_states (session_id, competency_id, probability)
        VALUES (%s, %s, %s)
        RETURNING id, probability
        """,
        (session_id, task["competency_id"], task["initial_probability"]),
    )


def _next_answer_position(conn, session_id: int) -> int:
    """Считает следующий порядковый номер ответа внутри сессии."""

    row = fetch_one(
        conn,
        "SELECT COALESCE(MAX(position_in_session), 0) + 1 AS position FROM answers WHERE session_id = %s",
        (session_id,),
    )
    return int(row["position"])


def _save_behavior_features(
    conn,
    session_id: int,
    answer_id: int,
    behavior,
    anomaly_probability: float,
    anomaly_risk: str,
) -> dict[str, Any]:
    """Сохраняет поведенческие признаки ответа в behavior_features."""

    return execute_one(
        conn,
        """
        INSERT INTO behavior_features (
            session_id,
            answer_id,
            response_time_sec,
            tab_switch_count,
            total_hidden_time_sec,
            hidden_time_ratio,
            paste_count,
            pasted_chars_total,
            paste_ratio,
            input_event_count,
            delete_count,
            max_pause_sec,
            max_input_burst_chars,
            copy_count,
            answer_length_chars,
            anomaly_probability,
            anomaly_risk
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, anomaly_probability, anomaly_risk
        """,
        (
            session_id,
            answer_id,
            behavior.response_time_sec,
            behavior.tab_switch_count,
            behavior.total_hidden_time_sec,
            behavior.hidden_time_ratio,
            behavior.paste_count,
            behavior.pasted_chars_total,
            behavior.paste_ratio,
            behavior.input_event_count,
            behavior.delete_count,
            behavior.max_pause_sec,
            behavior.max_input_burst_chars,
            behavior.copy_count,
            behavior.answer_length_chars,
            anomaly_probability,
            anomaly_risk,
        ),
    )


def _answers_count(conn, session_id: int) -> int:
    """Считает, сколько ответов уже есть в сессии."""

    row = fetch_one(conn, "SELECT COUNT(*) AS count FROM answers WHERE session_id = %s", (session_id,))
    return int(row["count"])


def _finish_session(conn, session_id: int, report_text: str | None = None) -> dict[str, Any]:
    """Считает итоги интервью и переводит сессию в статус finished."""

    # Считаем только служебную статистику по ответам. Сам итоговый балл ниже
    # считается не как простое среднее по всем заданиям, а как индекс общей
    # компетентности на основе score-профиля по компетенциям.
    stats = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS answers_count,
            COUNT(score) AS evaluated_answers_count
        FROM answers
        WHERE session_id = %s
        """,
        (session_id,),
    )

    bkt_profile = _get_bkt_states(conn, session_id)
    report_answers = _get_answers_for_report(conn, session_id)
    scoring = build_session_scoring(bkt_profile, report_answers)
    competency_profile = scoring["competency_profile"]
    task_trajectory = scoring["task_trajectory"]
    final_score = float(scoring["overall_index"])

    # Для риска берем максимальную вероятность аномалии по всем ответам.
    # Если хотя бы один ответ выглядел подозрительно, это должно попасть в итог.
    risk_stats = fetch_one(
        conn,
        """
        SELECT
            COALESCE(MAX(anomaly_probability), 0) AS anomaly_probability
        FROM behavior_features
        WHERE session_id = %s
        """,
        (session_id,),
    )

    anomaly_probability = float(risk_stats["anomaly_probability"])
    anomaly_risk = "low" if anomaly_probability < 0.31 else "medium" if anomaly_probability < 0.71 else "high"

    # Если frontend не передал готовый отчет, пробуем сформировать краткую
    # итоговую обратную связь через локальную LLM. Если LLM недоступна,
    # используем детерминированный fallback, чтобы завершение сессии не падало.
    if not report_text:
        try:
            report_text = generate_session_report(
                competency_profile,
                task_trajectory,
                final_score,
                anomaly_probability,
                anomaly_risk,
            )
        except EvaluationUnavailable as exc:
            print(f"[WARN] Итоговый LLM-отчет не сформирован, используется fallback: {exc}")
            report_text = _build_report_text(
                final_score,
                competency_profile,
                anomaly_probability,
                anomaly_risk,
                int(stats["answers_count"]),
                int(stats["evaluated_answers_count"]),
            )

    # Финальные значения сохраняются в interview_sessions, чтобы frontend мог
    # быстро показать результат без пересчета.
    summary = execute_one(
        conn,
        """
        UPDATE interview_sessions
        SET status = 'finished',
            finished_at = now(),
            final_score = %s,
            final_bkt_profile = %s,
            anomaly_probability = %s,
            anomaly_risk = %s,
            report_text = %s
        WHERE id = %s
        RETURNING
            id,
            status,
            final_score,
            final_bkt_profile,
            anomaly_probability,
            anomaly_risk,
            report_text,
            finished_at
        """,
        (
            final_score,
            Json(_profile_for_storage(competency_profile)),
            anomaly_probability,
            anomaly_risk,
            report_text,
            session_id,
        ),
    )
    summary["answers_count"] = int(stats["answers_count"])
    summary["evaluated_answers_count"] = int(stats["evaluated_answers_count"])
    summary["evaluated_competencies_count"] = int(scoring["evaluated_competencies_count"])
    summary["bkt_profile"] = competency_profile
    summary["score_profile"] = competency_profile
    summary["task_trajectory"] = task_trajectory
    return summary


def _build_report_text(
    final_score: float,
    competency_profile: list[dict[str, Any]],
    anomaly_probability: float,
    anomaly_risk: str,
    answers_count: int,
    evaluated_answers_count: int,
) -> str:
    """Собирает простой итоговый отчет, если LLM недоступна."""

    strongest, weakest = _profile_extremes(competency_profile)
    total_competencies = len(competency_profile)
    checked_competencies = sum(
        1 for item in competency_profile if item.get("score") is not None
    )
    final_score_text = _format_percent(final_score)
    anomaly_probability_text = _format_percent(anomaly_probability)
    index_level_text = _competency_index_level_text(final_score)
    anomaly_level_text = _anomaly_probability_level_text(anomaly_probability)
    strong_text = (
        f"Сильнее всего проявлена компетенция «{strongest['title']}» "
        f"({_format_percent(float(strongest['score']))}, BKT {_format_percent(float(strongest['bkt_probability']))})."
        if strongest
        else "Сильные стороны требуют дополнительной проверки."
    )
    weak_text = (
        f"Зона развития: «{weakest['title']}» "
        f"({_format_percent(float(weakest['score']))}, BKT {_format_percent(float(weakest['bkt_probability']))})."
        if weakest
        else "Слабые стороны требуют дополнительной проверки."
    )

    return (
        f"Интервью завершено: отправлено ответов {answers_count}, "
        f"оценено {evaluated_answers_count}. "
        f"Итоговый индекс компетентности по проверенным компетенциям составил {final_score_text}; "
        f"это соответствует уровню: {index_level_text}. "
        f"Проверено компетенций: {checked_competencies} из {total_competencies}; "
        "непроверенные компетенции не включались в расчет индекса. "
        f"{strong_text} {weak_text} "
        f"Вероятность аномального поведения составила {anomaly_probability_text}; "
        f"{anomaly_level_text}. Этот показатель следует рассматривать как индикатор для "
        "дополнительной проверки результата."
    )


def _format_percent(value: float) -> str:
    """Форматирует долю 0..1 как целый процент для текста отчета."""

    return f"{round(float(value) * 100)}%"


def _competency_index_level_text(score: float) -> str:
    """Интерпретирует итоговый индекс компетентности для фронтового отчета."""

    if score < 0.40:
        return "низкая сформированность компетенций, связанных с анализом данных"
    if score < 0.60:
        return "базовая сформированность компетенций, связанных с анализом данных"
    if score < 0.75:
        return "хорошая сформированность компетенций, связанных с анализом данных"
    return "высокая сформированность компетенций, связанных с анализом данных"


def _anomaly_probability_level_text(probability: float) -> str:
    """Интерпретирует вероятность аномального поведения в русской шкале."""

    if probability < 0.31:
        return "вероятность аномального поведения низкая"
    if probability < 0.71:
        return "вероятность аномального поведения умеренная"
    return "вероятность аномального поведения высокая"


def _profile_extremes(competency_profile: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Возвращает самую сильную и самую слабую компетенции по score."""

    checked = [item for item in competency_profile if item.get("score") is not None]
    if not checked:
        return None, None
    ordered = sorted(checked, key=lambda item: float(item["score"]))
    return ordered[-1], ordered[0]


def _get_answers_for_report(conn, session_id: int) -> list[dict[str, Any]]:
    """Возвращает компактные данные по ответам для итоговой обратной связи."""

    return fetch_all(
        conn,
        """
        SELECT
            a.id AS answer_id,
            a.position_in_session,
            a.task_id,
            t.title AS task_title,
            t.task_type,
            t.difficulty,
            c.id AS competency_id,
            c.code AS competency_code,
            c.title AS competency_title,
            a.is_correct,
            a.score,
            a.llm_feedback,
            a.bkt_before,
            a.bkt_after,
            a.response_time_sec,
            bf.anomaly_probability,
            bf.anomaly_risk
        FROM answers a
        JOIN tasks t ON t.id = a.task_id
        JOIN competencies c ON c.id = a.competency_id
        LEFT JOIN behavior_features bf ON bf.answer_id = a.id
        WHERE a.session_id = %s
        ORDER BY a.position_in_session
        """,
        (session_id,),
    )


def _profile_for_storage(bkt_profile: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Очищает итоговый профиль от значений, которые неудобно хранить в JSONB."""

    return [
        {
            "competency_id": int(item["competency_id"]),
            "code": item["code"],
            "title": item["title"],
            "group_name": item["group_name"],
            "score": item.get("score"),
            "average_score": item.get("average_score"),
            "score_level": item.get("score_level"),
            "difficulty_weighted_sum": float(item.get("difficulty_weighted_sum") or 0),
            "difficulty_weight_sum": float(item.get("difficulty_weight_sum") or 0),
            "bkt_probability": float(item.get("bkt_probability") or item.get("probability") or 0),
            # Поле probability оставлено для обратной совместимости с прежним
            # frontend-кодом, который ожидал чистый BKT-профиль.
            "probability": float(item["probability"]),
            "answered_count": int(item.get("answered_count") or 0),
            "evaluated_answers_count": int(item.get("evaluated_answers_count") or 0),
            "bkt_status": item.get("bkt_status") or item.get("status"),
            "status": item.get("status"),
            "is_low_saturated": bool(item.get("is_low_saturated")),
        }
        for item in bkt_profile
    ]


def _get_bkt_states(conn, session_id: int) -> list[dict[str, Any]]:
    """Возвращает BKT-состояния с названиями компетенций."""

    # Используется после старта сессии и при просмотре сессии.
    # Сортировка по probability ASC показывает самые слабые компетенции первыми.
    rows = fetch_all(
        conn,
        """
        SELECT
            c.id AS competency_id,
            c.code,
            c.title,
            c.group_name,
            b.probability,
            COUNT(a.id) AS answered_count,
            COUNT(a.score) AS evaluated_answers_count,
            b.updated_at
        FROM bkt_states b
        JOIN competencies c ON c.id = b.competency_id
        LEFT JOIN answers a
          ON a.session_id = b.session_id
         AND a.competency_id = b.competency_id
        WHERE b.session_id = %s
        GROUP BY c.id, c.code, c.title, c.group_name, b.probability, b.updated_at
        ORDER BY b.probability ASC, c.title
        """,
        (session_id,),
    )
    for row in rows:
        row["probability"] = float(row["probability"])
        row["answered_count"] = int(row["answered_count"])
        row["evaluated_answers_count"] = int(row["evaluated_answers_count"])
        row["is_low_saturated"] = _is_low_competency_saturated(row)
        row["status"] = _competency_status(row)
    return rows


def _competency_status(state: dict[str, Any]) -> str:
    """Дает интерпретируемый статус компетенции для итогового профиля."""

    probability = float(state["probability"])
    answered_count = int(state.get("answered_count") or 0)
    if answered_count == 0:
        return "not_checked"
    if _is_low_competency_saturated(state):
        return "weak_fixed"
    if probability < settings.low_competency_threshold:
        return "weak"
    if probability < 0.75:
        return "developing"
    return "strong"
