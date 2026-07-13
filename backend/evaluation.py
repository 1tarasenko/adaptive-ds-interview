"""Проверка ответов кандидата.

Тестовые типы заданий проверяются по эталону из базы.
Открытые текстовые ответы оцениваются локальной LLM Qwen2.5-Coder через
Ollama. Если модель недоступна, ответ сохраняется как неоцененный и не меняет BKT.
"""

import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from .config import settings


class EvaluationUnavailable(RuntimeError):
    """Ошибка технической недоступности LLM-оценки."""


@dataclass(slots=True) # slots=True для экономии памяти
class EvaluationResult:
    """Результат проверки ответа кандидата."""

    is_correct: bool | None
    score: float | None
    feedback: str
    evaluation_status: str = "evaluated"

    @classmethod #декоратор для метода класса
    def failed(cls, feedback: str) -> "EvaluationResult":
        """Создает результат для ответа, который не удалось оценить."""

        return cls(
            is_correct=None,
            score=None,
            feedback=feedback,
            evaluation_status="evaluation_failed",
        )


def evaluate_answer(
    task: dict[str, Any],
    answer_text: str | None,
    answer_payload: dict[str, Any],
) -> EvaluationResult:
    """Оценивает ответ кандидата.

    """

    task_type = task["task_type"]
    correct_answer = task.get("correct_answer") or {}

    if task_type == "single_choice":
        return _evaluate_single_choice(answer_payload, correct_answer)

    if task_type == "multiple_choice":
        return _evaluate_multiple_choice(answer_payload, correct_answer)

    if task_type == "matching":
        return _evaluate_matching(answer_payload, correct_answer)

    return _evaluate_open_answer(task, answer_text or "")


def _evaluate_single_choice(
    answer_payload: dict[str, Any],
    correct_answer: dict[str, Any],
) -> EvaluationResult:
    """Проверяет задание с одним правильным вариантом."""

    selected = answer_payload.get("option_id")
    expected = correct_answer.get("option_id")
    is_correct = bool(selected and selected == expected)
    feedback = (
        "Выбран правильный вариант."
        if is_correct
        else "Выбран неверный вариант."
    )

    return EvaluationResult(is_correct, 1.0 if is_correct else 0.0, feedback)


def _evaluate_multiple_choice(
    answer_payload: dict[str, Any],
    correct_answer: dict[str, Any],
) -> EvaluationResult:
    """Проверяет задание с несколькими правильными вариантами."""

    selected = set(answer_payload.get("option_ids") or [])
    expected = set(correct_answer.get("option_ids") or [])

    is_correct = bool(expected) and selected == expected

    if not expected:
        score = 0.0
    else:
        true_positive = len(selected & expected) 
        false_positive = len(selected - expected)
        score = max((true_positive - false_positive) / len(expected), 0.0)

    feedback = (
        "Набор вариантов совпадает с эталоном."
        if is_correct
        else "Набор вариантов требует доработки."
    )
    return EvaluationResult(is_correct, round(score, 4), feedback)


def _evaluate_matching(
    answer_payload: dict[str, Any],
    correct_answer: dict[str, Any],
) -> EvaluationResult:
    """Проверяет задание на сопоставление."""

    selected = answer_payload.get("matches") or {}
    expected = correct_answer.get("matches") or {}

    if not expected:
        return EvaluationResult(
            False,
            0.0,
            "Для задания не задан эталон соответствий.",
        )

    matched = sum(1 for key, value in expected.items() if selected.get(key) == value)
    score = matched / max(len(expected), len(selected))
    is_correct = selected == expected
    feedback = (
        "Все соответствия выбраны верно."
        if is_correct
        else "Часть соответствий выбрана неверно."
    )
    return EvaluationResult(is_correct, round(score, 4), feedback)


def _evaluate_open_answer(task: dict[str, Any], answer_text: str) -> EvaluationResult:
    """Оценивает открытый ответ через локальную Qwen2.5-Coder в Ollama."""

    if not answer_text.strip():
        raise EvaluationUnavailable("Открытый ответ пустой, LLM-оценка невозможна.")

    prompt = _build_llm_prompt(task, answer_text)
    raw_content = _call_ollama(prompt)
    parsed = _parse_llm_json(raw_content)

    score = _clamp_score(parsed.get("score"))
    
    is_correct = score >= settings.llm_score_threshold

    feedback = str(parsed.get("feedback") or "").strip()
    if not feedback:
        feedback = "LLM оценила ответ, но не вернула текстовое пояснение."

    return EvaluationResult(
        is_correct=is_correct,
        score=score,
        feedback=feedback,
    )


def generate_session_report(
    competency_profile: list[dict[str, Any]],
    task_trajectory: list[dict[str, Any]],
    overall_index: float,
    anomaly_probability: float,
    anomaly_risk: str,
) -> str:
    """Формирует краткий итоговый отчет по всей сессии через локальную LLM."""

    prompt = _build_session_report_prompt(
        competency_profile,
        task_trajectory,
        overall_index,
        anomaly_probability,
        anomaly_risk,
    )
    raw_content = _call_ollama(prompt)
    parsed = _parse_llm_json(raw_content)

    report_text = str(parsed.get("report_text") or "").strip()
    if not report_text:
        raise EvaluationUnavailable("LLM не вернула итоговый текст отчета.")
    return report_text


def _build_llm_prompt(task: dict[str, Any], answer_text: str) -> str:
    """Собирает промпт с заданием, критериями и ответом кандидата."""

    rubric = json.dumps(task.get("rubric") or {}, ensure_ascii=False, indent=2)
    correct_answer = json.dumps(task.get("correct_answer") or {}, ensure_ascii=False, indent=2)
    task_type = task.get("task_type", "text")

    return f"""
Ты оцениваешь ответ кандидата в MVP системы технического интервью.
Используй только данные ниже. Не придумывай дополнительные требования.

Тип задания: {task_type}
Название: {task.get("title", "")}
Условие:
{task.get("question_text", "")}

Критерии проверки из базы:
{rubric}

Эталонный ответ, если он задан:
{correct_answer}

Ответ кандидата:
{answer_text}

Верни только JSON без markdown и без пояснений вне JSON.
Формат:
{{
  "score": 0.0,
  "feedback": "краткое объяснение оценки на русском языке"
}}

Правила:
- score должен быть числом от 0 до 1;
- если заданы rubric.criteria, оценивай прежде всего по этим критериям;
- если дополнительно задан эталонный ответ, используй его как ориентир для проверки полноты и корректности ответа, но не требуй буквального совпадения;
- если ответ содержит SQL, оценивай общую корректность логики запроса, группировки, фильтрации и выбора полей;
- feedback должен быть коротким: 1-2 предложения.
""".strip()


def _build_session_report_prompt(
    competency_profile: list[dict[str, Any]],
    task_trajectory: list[dict[str, Any]],
    overall_index: float,
    anomaly_probability: float,
    anomaly_risk: str,
) -> str:
    """Собирает промпт для итоговой обратной связи по всей сессии."""

    total_competencies = len(competency_profile)
    checked_competencies = sum(
        1 for item in competency_profile if item.get("score") is not None
    )
    index_level = _competency_index_level_text(overall_index)
    anomaly_level = _anomaly_probability_level_text(anomaly_probability)
    answer_summary = _build_answer_summary(task_trajectory)
    compact_profile = [
        {
            "competency": item.get("title"),
            "group": item.get("group_name"),
            "score": item.get("score"),
            "score_level": item.get("score_level"),
            "bkt_probability": float(item.get("bkt_probability") or item.get("probability") or 0),
            "answered_count": int(item.get("answered_count") or 0),
            "evaluated_answers_count": int(item.get("evaluated_answers_count") or 0),
            "bkt_status": item.get("bkt_status") or item.get("status"),
        }
        for item in competency_profile
    ]

    return f"""
Ты формируешь итоговую обратную связь кандидату после адаптивного технического интервью.
Используй только данные ниже. Не утверждай, что кандидат нарушал правила: риск поведения является вероятностным индикатором.

Score-профиль компетенций:
{json.dumps(compact_profile, ensure_ascii=False, indent=2)}

Краткая сводка по ответам:
{json.dumps(answer_summary, ensure_ascii=False, indent=2)}

Итоговый индекс компетентности по проверенным компетенциям: {_format_percent(overall_index)} ({index_level})
Проверено компетенций: {checked_competencies} из {total_competencies}.
Непроверенные компетенции не включались в расчет итогового индекса.
Вероятность аномального поведения: {_format_percent(anomaly_probability)} ({anomaly_level})
Технический уровень риска: {anomaly_risk}

Верни только JSON без markdown и без текста вне JSON.
Формат:
{{
  "report_text": "4-5 предложений на русском языке: общий итог, сильные стороны, зоны развития и осторожная интерпретация риска поведения."
}}

Правила:
- пиши в нейтральном академическом стиле, без разговорных оценок и без канцелярита;
- используй проценты, а не десятичные дроби;
- обязательно расшифруй итоговый индекс через его уровень сформированности;
- обязательно расшифруй вероятность аномального поведения через ее уровень;
- не перечисляй все задания подряд и не называй больше 2-3 примеров;
- сильные и слабые стороны определяй прежде всего по score, а BKT используй как дополнительную динамическую характеристику;
- если у примера ответа есть feedback, используй его как пояснение к конкретному ответу, но не пересчитывай score и индекс;
- если по компетенции evaluated_answers_count=0, не делай по ней жесткий вывод;
- обязательно учитывай, что индекс рассчитан только по проверенным компетенциям;
- если риск высокий, формулируй как необходимость дополнительной проверки результата, а не как доказанное нарушение;
- не используй англоязычные названия уровней low, medium, high в тексте отчета.
""".strip()


def _format_percent(value: float) -> str:
    """Форматирует долю 0..1 как целый процент для итогового текста."""

    return f"{round(float(value) * 100)}%"


def _competency_index_level_text(score: float) -> str:
    """Возвращает фронтовую интерпретацию итогового индекса компетенций."""

    if score < 0.40:
        return "низкая сформированность компетенций, связанных с анализом данных"
    if score < 0.60:
        return "базовая сформированность компетенций, связанных с анализом данных"
    if score < 0.75:
        return "хорошая сформированность компетенций, связанных с анализом данных"
    return "высокая сформированность компетенций, связанных с анализом данных"


def _anomaly_probability_level_text(probability: float) -> str:
    """Возвращает интерпретацию вероятности аномального поведения."""

    if probability < 0.31:
        return "вероятность аномального поведения низкая"
    if probability < 0.71:
        return "вероятность аномального поведения умеренная"
    return "вероятность аномального поведения высокая"


def _build_answer_summary(task_trajectory: list[dict[str, Any]]) -> dict[str, Any]:
    """Сжимает траекторию заданий до небольшой сводки для LLM-отчета.

    Полная траектория остается в API и frontend-е. В промпт передаем только
    агрегаты и несколько примеров, чтобы локальная LLM не падала на 27 заданиях.
    """

    evaluated = [
        item for item in task_trajectory if item.get("score") is not None
    ]
    failed_count = len(task_trajectory) - len(evaluated)
    if not evaluated:
        return {
            "total_answers": len(task_trajectory),
            "evaluated_answers": 0,
            "evaluation_failed_answers": failed_count,
            "strong_answer_examples": [],
            "weak_answer_examples": [],
            "partial_answer_examples": [],
        }

    scores = [float(item["score"]) for item in evaluated]
    strong = sorted(evaluated, key=lambda item: float(item["score"]), reverse=True)[:3]
    weak = sorted(evaluated, key=lambda item: float(item["score"]))[:3]
    partial = [
        item for item in evaluated
        if 0.4 <= float(item["score"]) < 0.75
    ][:3]

    return {
        "total_answers": len(task_trajectory),
        "evaluated_answers": len(evaluated),
        "evaluation_failed_answers": failed_count,
        "average_answer_score": round(sum(scores) / len(scores), 4),
        "min_answer_score": round(min(scores), 4),
        "max_answer_score": round(max(scores), 4),
        "strong_answer_examples": [_answer_example(item) for item in strong],
        "weak_answer_examples": [_answer_example(item) for item in weak],
        "partial_answer_examples": [_answer_example(item) for item in partial],
    }


def _answer_example(item: dict[str, Any]) -> dict[str, Any]:
    """Оставляет в примере только поля, полезные для текстовой интерпретации."""

    score = item.get("score")
    bkt_after = item.get("bkt_after")
    example = {
        "task": item.get("task_title"),
        "competency": item.get("competency_title"),
        "difficulty": item.get("difficulty"),
        "score": None if score is None else round(float(score), 4),
        "bkt_after": None if bkt_after is None else round(float(bkt_after), 4),
    }
    feedback = _feedback_excerpt(item.get("llm_feedback"))
    if feedback:

        example["feedback"] = feedback
    return example


def _feedback_excerpt(value: Any, max_length: int = 260) -> str | None:
    """Сжимает feedback по ответу, чтобы итоговый промпт оставался компактным."""

    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}..."


def _call_ollama(prompt: str) -> str:
    """Отправляет запрос в локальную Ollama и возвращает текст ответа модели."""

    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты строгий, но краткий ассистент для оценки "
                    "технических ответов кандидатов."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "options": {
            "temperature": 0,
            "num_predict": 400,
        },
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=settings.ollama_timeout_sec) as response:
            response_body = response.read().decode("utf-8")
    except (error.URLError, socket.timeout, TimeoutError) as exc:
        raise EvaluationUnavailable(f"Локальная Ollama недоступна: {exc}") from exc

    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise EvaluationUnavailable("Ollama вернула невалидный JSON-ответ API.") from exc

    if decoded.get("error"):
        raise EvaluationUnavailable(f"Ollama вернула ошибку: {decoded['error']}")

    content = (decoded.get("message") or {}).get("content")
    if not content:
        raise EvaluationUnavailable("Ollama не вернула текст оценки.")

    return str(content)


def _parse_llm_json(content: str) -> dict[str, Any]:
    """Разбирает JSON, который вернула модель."""

    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json\n", "", 1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise EvaluationUnavailable("LLM не вернула JSON с оценкой.") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise EvaluationUnavailable(
                "LLM вернула JSON, который не удалось разобрать."
            ) from exc

    if not isinstance(parsed, dict):
        raise EvaluationUnavailable("LLM вернула JSON не в формате объекта.")

    return parsed


def _clamp_score(value: Any) -> float:
    """Приводит обязательный score к диапазону 0..1."""

    if value is None:
        raise EvaluationUnavailable("LLM не вернула обязательное поле score.")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationUnavailable("Поле score от LLM не является числом.") from exc
    return round(min(max(score, 0.0), 1.0), 4)
