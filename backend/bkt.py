"""Bayesian Knowledge Tracing для оценки знания кандидата.

BKT хранит вероятность того, что кандидат
уже владеет конкретной компетенцией. После каждого ответа эта вероятность
обновляется и сохраняется в таблицах `bkt_states` и `answers`.
"""

from .config import settings


class BKTParams:
    """Параметры классической Bayesian Knowledge Tracing модели.

    learn - вероятность, что кандидат "доучился" после задания.
    slip - вероятность ошибки, даже если кандидат компетенцией владеет.
    guess - вероятность угадать правильный ответ без реального знания.
    """

    def __init__(
        self,
        learn: float | None = None,
        slip: float | None = None,
        guess: float | None = None,
    ):
        self.learn = settings.bkt_learn if learn is None else learn
        self.slip = settings.bkt_slip if slip is None else slip
        self.guess = settings.bkt_guess if guess is None else guess


def update_probability(prior: float, is_correct: bool, params: BKTParams | None = None) -> float:
    """Пересчитывает вероятность владения компетенцией после ответа.

    backend передает текущую вероятность и факт правильности ответа,
    а функция возвращает новое значение для bkt_states и answers.
    """

    params = params or BKTParams()

    # Ограничиваем вероятность в диапазоне [0, 1], чтобы избежать ошибок при вычислениях.
    prior = min(max(prior, 0.0), 1.0)

    if is_correct:
        denominator = prior * (1 - params.slip) + (1 - prior) * params.guess
        posterior = prior if denominator == 0 else (prior * (1 - params.slip)) / denominator

        # Один удачный ответ не должен сразу переводить кандидата в эксперты.
        posterior = min(prior + 0.15, posterior)
    else:
        denominator = prior * params.slip + (1 - prior) * (1 - params.guess)
        posterior = prior if denominator == 0 else (prior * params.slip) / denominator

    updated = posterior + (1 - posterior) * params.learn

    return round(min(max(updated, 0.0), 1.0), 4)


def difficulty_for_probability(probability: float) -> str:
    """Выбирает сложность задания по текущей BKT-вероятности.
    """

    if probability < 0.4:
        return "easy"

    if probability < 0.75:
        return "medium"

    return "hard"
