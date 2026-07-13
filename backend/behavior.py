"""Работа с поведенческими признаками кандидата.

Фронтенд собирает события во время ответа: время выполнения,
переключения вкладок, вставки текста, паузы при наборе. Этот модуль приводит
эти данные к единому виду и считает риск аномального поведения через обученную
ML-модель из `ml/artifacts`.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

# Пути к артефактам ML-модели, которые должны быть собраны в пакете.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = PROJECT_ROOT / "ml" / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "anomaly_best_model.joblib"
METADATA_PATH = ARTIFACTS_DIR / "anomaly_model_metadata.json"


@dataclass(slots=True)
class BehaviorMetrics:
    """Агрегированные признаки поведения кандидата.

    Поля совпадают с таблицей `behavior_features` и признаками ML-модели.
    """

    response_time_sec: float = 0
    tab_switch_count: int = 0
    total_hidden_time_sec: float = 0
    hidden_time_ratio: float = 0
    paste_count: int = 0
    pasted_chars_total: int = 0
    paste_ratio: float = 0
    input_event_count: int = 0
    delete_count: int = 0
    max_pause_sec: float = 0
    max_input_burst_chars: int = 0
    copy_count: int = 0
    answer_length_chars: int = 0


def normalize_behavior(
    raw: dict[str, Any] | None,
    answer_text: str | None,
) -> BehaviorMetrics:
    """Делает стабильный набор признаков."""

    raw = raw or {}
    answer_length = int(raw.get("answer_length_chars") or len(answer_text or ""))
    response_time = float(raw.get("response_time_sec") or 0)
    hidden_time = float(raw.get("total_hidden_time_sec") or 0)
    pasted_chars = int(raw.get("pasted_chars_total") or 0)

    hidden_ratio = raw.get("hidden_time_ratio")
    if hidden_ratio is None and response_time > 0:
        hidden_ratio = hidden_time / response_time

    paste_ratio = raw.get("paste_ratio")
    if paste_ratio is None and answer_length > 0:
        paste_ratio = pasted_chars / answer_length

    return BehaviorMetrics(
        response_time_sec=max(response_time, 0),
        tab_switch_count=max(int(raw.get("tab_switch_count") or 0), 0),
        total_hidden_time_sec=max(hidden_time, 0),
        hidden_time_ratio=_ratio(hidden_ratio),
        paste_count=max(int(raw.get("paste_count") or 0), 0),
        pasted_chars_total=max(pasted_chars, 0),
        paste_ratio=_ratio(paste_ratio),
        input_event_count=max(int(raw.get("input_event_count") or 0), 0),
        delete_count=max(int(raw.get("delete_count") or 0), 0),
        max_pause_sec=max(float(raw.get("max_pause_sec") or 0), 0),
        max_input_burst_chars=max(int(raw.get("max_input_burst_chars") or 0), 0),
        copy_count=max(int(raw.get("copy_count") or 0), 0),
        answer_length_chars=max(answer_length, 0),
    )


def score_anomaly_risk(metrics: BehaviorMetrics) -> tuple[float, str]:
    """Считает вероятность аномального поведения обученной моделью.
    """

    model, feature_columns = _load_anomaly_model()
    feature_row = _metrics_to_feature_row(metrics, feature_columns)

    feature_frame = pd.DataFrame([feature_row], columns=feature_columns)
    probability = _predict_anomaly_probability(model, feature_frame)

    probability = round(_ratio(probability), 4)
    return probability, _risk_level(probability)


@lru_cache(maxsize=1)
def _load_anomaly_model() -> tuple[Any, list[str]]:
    """Загружает модель и список признаков один раз на процесс backend-а."""

    if not MODEL_PATH.exists():
        raise RuntimeError(f"Файл модели не найден: {MODEL_PATH}")
    if not METADATA_PATH.exists():
        raise RuntimeError(f"Файл metadata модели не найден: {METADATA_PATH}")

    with METADATA_PATH.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    feature_columns = metadata.get("feature_columns")
    if not isinstance(feature_columns, list) or not feature_columns:
        raise RuntimeError("В metadata модели нет непустого списка feature_columns.")

    model = joblib.load(MODEL_PATH)
    if not hasattr(model, "predict_proba"):
        raise RuntimeError("Загруженная модель не поддерживает predict_proba().")

    return model, [str(column) for column in feature_columns]


def _metrics_to_feature_row(metrics: BehaviorMetrics, feature_columns: list[str]) -> dict[str, float]:
    """Преобразует BehaviorMetrics в строку признаков для ML-модели."""

    values = {
        "response_time_sec": metrics.response_time_sec,
        "tab_switch_count": metrics.tab_switch_count,
        "total_hidden_time_sec": metrics.total_hidden_time_sec,
        "hidden_time_ratio": metrics.hidden_time_ratio,
        "paste_count": metrics.paste_count,
        "pasted_chars_total": metrics.pasted_chars_total,
        "paste_ratio": metrics.paste_ratio,
        "input_event_count": metrics.input_event_count,
        "delete_count": metrics.delete_count,
        "max_pause_sec": metrics.max_pause_sec,
        "max_input_burst_chars": metrics.max_input_burst_chars,
        "copy_count": metrics.copy_count,
        "answer_length_chars": metrics.answer_length_chars,
    }

    missing_columns = [column for column in feature_columns if column not in values]
    if missing_columns:
        raise RuntimeError(f"Backend не знает признаки модели: {', '.join(missing_columns)}")

    return {column: float(values[column]) for column in feature_columns}


def _predict_anomaly_probability(model: Any, feature_frame: pd.DataFrame) -> float:
    """Возвращает вероятность аномального поведения."""

    probabilities = model.predict_proba(feature_frame)[0]
    classes = _model_classes(model)

    if 1 not in classes:
        raise RuntimeError("В модели не найден класс 1 для аномального поведения.")

    anomaly_index = classes.index(1)
    return float(probabilities[anomaly_index])


def _model_classes(model: Any) -> list[int]:
    """Достает классы"""

    classes = getattr(model, "classes_", None)
    if classes is None and hasattr(model, "named_steps"):
        last_step = list(model.named_steps.values())[-1]
        classes = getattr(last_step, "classes_", None)
    if classes is None:
        raise RuntimeError("В модели не найден список классов.")

    return [int(class_name) for class_name in classes]


def _risk_level(probability: float) -> str:
    """Переводит вероятность модели в человекочитаемый уровень риска."""

    if probability < 0.31:
        return "low"
    if probability < 0.71:
        return "medium"
    return "high"


def _ratio(value: Any) -> float:
    """Приводит любое значение доли к диапазону от 0 до 1."""

    if value is None:
        return 0.0

    return round(min(max(float(value), 0.0), 1.0), 6)
