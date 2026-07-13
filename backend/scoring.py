"""Расчет итогового score-профиля и индекса компетентности.

BKT в проекте отвечает за адаптивный выбор заданий и динамическую вероятность
владения компетенцией. Этот модуль считает отдельный итоговый слой: профиль по
score и общий индекс, которые удобнее использовать для сравнения кандидатов.
"""

from typing import Any


DIFFICULTY_WEIGHTS = {
    "easy": 1.00,
    "medium": 1.25,
    "hard": 1.50,
}


def difficulty_weight(difficulty: str | None) -> float:
    """Возвращает вес задания по сложности для итоговой агрегации score."""

    return DIFFICULTY_WEIGHTS.get(str(difficulty or "").lower(), 1.0)


def competency_level(score: float | None) -> str:
    """Интерпретирует score компетенции в проектной шкале MVP."""

    if score is None:
        return "not_checked"
    if score < 0.40:
        return "low"
    if score < 0.75:
        return "medium"
    return "high"


def build_session_scoring(
    bkt_profile: list[dict[str, Any]],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Собирает итоговую оценку сессии по компетенциям.

    Итоговый индекс считается не по бинарному `is_correct`, а по `score`,
    взвешенному по сложности задания. BKT-вероятность сохраняется рядом как
    дополнительная динамическая характеристика компетенции.
    """

    answers_by_competency: dict[int, list[dict[str, Any]]] = {}
    for answer in answers:
        competency_id = int(answer["competency_id"])
        answers_by_competency.setdefault(competency_id, []).append(answer)

    competency_profile = [
        _build_competency_score(item, answers_by_competency.get(int(item["competency_id"]), []))
        for item in bkt_profile
    ]
    evaluated_competencies = [
        item for item in competency_profile if item["score"] is not None
    ]

    if evaluated_competencies:
        overall_index = sum(float(item["score"]) for item in evaluated_competencies) / len(evaluated_competencies)
    else:
        overall_index = 0.0

    return {
        "overall_index": round(overall_index, 4),
        "competency_profile": competency_profile,
        "task_trajectory": build_task_trajectory(answers),
        "evaluated_competencies_count": len(evaluated_competencies),
    }


def build_task_trajectory(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Готовит расширенную траекторию заданий для итогового отчета."""

    trajectory = []
    for answer in answers:
        score = answer.get("score")
        trajectory.append(
            {
                "position": int(answer.get("position_in_session") or 0),
                "task_id": answer.get("task_id"),
                "task_title": answer.get("task_title"),
                "competency_id": answer.get("competency_id"),
                "competency_code": answer.get("competency_code"),
                "competency_title": answer.get("competency_title"),
                "task_type": answer.get("task_type"),
                "difficulty": answer.get("difficulty"),
                "difficulty_weight": difficulty_weight(answer.get("difficulty")),
                "score": None if score is None else round(float(score), 4),
                "is_correct": answer.get("is_correct"),
                "llm_feedback": answer.get("llm_feedback"),
                "bkt_before": _optional_float(answer.get("bkt_before")),
                "bkt_after": _optional_float(answer.get("bkt_after")),
                "anomaly_probability": _optional_float(answer.get("anomaly_probability")),
                "anomaly_risk": answer.get("anomaly_risk"),
                "response_time_sec": answer.get("response_time_sec"),
                "evaluation_status": (
                    "evaluation_failed"
                    if score is None or answer.get("is_correct") is None
                    else "evaluated"
                ),
            }
        )
    return trajectory


def _build_competency_score(
    bkt_item: dict[str, Any],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Считает score одной компетенции с учетом сложности ее заданий."""

    evaluated_answers = [answer for answer in answers if answer.get("score") is not None]
    weighted_sum = 0.0
    weight_sum = 0.0
    raw_sum = 0.0

    for answer in evaluated_answers:
        score = float(answer["score"])
        weight = difficulty_weight(answer.get("difficulty"))
        weighted_sum += score * weight
        weight_sum += weight
        raw_sum += score

    score_value = round(weighted_sum / weight_sum, 4) if weight_sum else None
    average_score = round(raw_sum / len(evaluated_answers), 4) if evaluated_answers else None
    probability = float(bkt_item.get("probability") or 0.0)

    return {
        "competency_id": int(bkt_item["competency_id"]),
        "code": bkt_item["code"],
        "title": bkt_item["title"],
        "group_name": bkt_item["group_name"],
        "score": score_value,
        "average_score": average_score,
        "score_level": competency_level(score_value),
        "difficulty_weighted_sum": round(weighted_sum, 4),
        "difficulty_weight_sum": round(weight_sum, 4),
        "bkt_probability": round(probability, 4),
        # Старое поле оставлено для совместимости frontend-а и сохраненных JSON.
        "probability": round(probability, 4),
        "answered_count": int(bkt_item.get("answered_count") or 0),
        "evaluated_answers_count": len(evaluated_answers),
        "bkt_status": bkt_item.get("status"),
        "status": bkt_item.get("status"),
        "is_low_saturated": bool(bkt_item.get("is_low_saturated")),
    }


def _optional_float(value: Any) -> float | None:
    """Нормализует необязательное числовое поле для JSON-ответа API."""

    if value is None:
        return None
    return round(float(value), 4)
