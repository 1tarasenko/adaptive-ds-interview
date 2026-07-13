/*
  Клиентский слой работает с Python backend:
  - backend создает сессию и выбирает задания из PostgreSQL;
  - frontend отвечает за ввод кандидата и сбор поведенческих признаков;
  - после каждого ответа backend возвращает оценку, BKT и следующее задание.
*/

const API_BASE_URL = 'http://127.0.0.1:8000';

const taskTypeLabels = {
  text: 'Развернутый ответ',
  single: 'Один вариант',
  multiple: 'Несколько вариантов',
  matching: 'Соответствие'
};

const difficultyLabels = {
  easy: 'Базовая',
  medium: 'Средняя',
  hard: 'Высокая'
};

const riskLevelInfo = {
  low: {
    className: 'risk-low',
    label: 'низкий',
    description: 'Вероятность аномального поведения низкая.'
  },
  medium: {
    className: 'risk-medium',
    label: 'умеренный',
    description: 'Вероятность аномального поведения умеренная.'
  },
  high: {
    className: 'risk-high',
    label: 'высокий',
    description: 'Вероятность аномального поведения высокая; результат требует дополнительной проверки.'
  }
};

const competencyIndexLevels = [
  {
    max: 0.4,
    className: 'score-low',
    label: 'низкая сформированность',
    description: 'Индекс компетенций соответствует низкой сформированности компетенций, связанных с анализом данных.'
  },
  {
    max: 0.6,
    className: 'score-basic',
    label: 'базовая сформированность',
    description: 'Индекс компетенций соответствует базовой сформированности компетенций, связанных с анализом данных.'
  },
  {
    max: 0.75,
    className: 'score-good',
    label: 'хорошая сформированность',
    description: 'Индекс компетенций соответствует хорошей сформированности компетенций, связанных с анализом данных.'
  },
  {
    max: Infinity,
    className: 'score-high',
    label: 'высокая сформированность',
    description: 'Индекс компетенций соответствует высокой сформированности компетенций, связанных с анализом данных.'
  }
];

const session = {
  started: false,
  backendSessionId: null,
  candidateName: '',
  candidateEmail: '',
  currentTask: null,
  currentTaskNumber: 0,
  totalTasksLimit: 27,
  correctAnswers: 0,
  answers: [],
  bktStates: [],
  knowledgeProbability: 0.45,
  currentRisk: 0,
  behavior: createEmptyBehavior()
};

const candidateForm = document.getElementById('candidateForm');
const candidateCard = document.getElementById('candidateCard');
const interviewCard = document.getElementById('interviewCard');
const reportCard = document.getElementById('reportCard');
const answerLabel = document.getElementById('answerLabel');
const answerInput = document.getElementById('answerInput');
const choiceAnswerField = document.getElementById('choiceAnswerField');
const matchingAnswerField = document.getElementById('matchingAnswerField');
const submitAnswerBtn = document.getElementById('submitAnswerBtn');
const logList = document.getElementById('logList');

const taskNumberPill = document.getElementById('taskNumberPill');
const skillPill = document.getElementById('skillPill');
const difficultyPill = document.getElementById('difficultyPill');
const typePill = document.getElementById('typePill');
const taskText = document.getElementById('taskText');
const typingHint = document.getElementById('typingHint');

const knowledgeValue = document.getElementById('knowledgeValue');
const knowledgeBar = document.getElementById('knowledgeBar');
const riskValue = document.getElementById('riskValue');
const riskBar = document.getElementById('riskBar');
const taskCounter = document.getElementById('taskCounter');

document.getElementById('scrollToStartBtn').addEventListener('click', () => {
  document.getElementById('startBlock').scrollIntoView({ behavior: 'smooth' });
});

document.getElementById('resetSessionBtn').addEventListener('click', () => {
  location.reload();
});

candidateForm.addEventListener('submit', async (event) => {
  event.preventDefault();

  const submitButton = candidateForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.textContent = 'Запуск...';

  try {
    const fullName = document.getElementById('candidateName').value.trim();
    const email = document.getElementById('candidateEmail').value.trim();

    // Сессия создается на backend-е, чтобы все дальнейшие ответы были
    // привязаны к одной записи `interview_sessions` в PostgreSQL.
    const data = await apiRequest('/sessions', {
      method: 'POST',
      body: {
        full_name: fullName,
        email
      }
    });

    session.started = true;
    session.backendSessionId = data.session.id;
    session.candidateName = data.candidate.full_name;
    session.candidateEmail = data.candidate.email;
    session.currentTaskNumber = 0;
    session.correctAnswers = 0;
    session.answers = [];
    session.bktStates = data.bkt_states || [];
    session.totalTasksLimit = Number(data.max_tasks || session.totalTasksLimit);
    session.knowledgeProbability = getTaskBktProbability(session.bktStates, data.next_task);
    session.currentRisk = 0;

    candidateCard.classList.add('hidden');
    interviewCard.classList.remove('hidden');

    addLog(`Сессия #${session.backendSessionId} создана в backend: ${session.candidateName} · ${session.candidateEmail}`);
    await loadTask(data.next_task);
    updateDashboard();
  } catch (error) {
    alert(`Не удалось запустить интервью: ${error.message}`);
    addLog(`Ошибка запуска backend-сессии: ${error.message}`);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = 'Запустить интервью';
  }
});

async function loadTask(rawTask) {
  if (!rawTask) {
    await finishInterviewFromBackend(null);
    return;
  }

  const task = normalizeTask(rawTask);
  session.currentTask = task;
  session.knowledgeProbability = getTaskBktProbability(session.bktStates, rawTask);
  session.currentTaskNumber += 1;
  session.behavior = createEmptyBehavior();
  session.behavior.taskStart = Date.now();

  answerInput.value = '';
  choiceAnswerField.innerHTML = '';
  matchingAnswerField.innerHTML = '';

  taskNumberPill.textContent = `Задание ${session.currentTaskNumber}`;
  skillPill.textContent = task.skill;
  difficultyPill.textContent = task.difficultyLabel;
  typePill.textContent = taskTypeLabels[task.type] || 'Ответ';

  taskText.innerHTML = `
    <h3>${escapeHtml(task.title)}</h3>
    <p>${escapeHtml(task.text)}</p>
  `;

  renderAnswerControls(task);
  taskCounter.textContent = `${session.currentTaskNumber} / ${session.totalTasksLimit}`;
  addLog(`Backend выдал задание: ${task.title}`);
}

function normalizeTask(rawTask) {
  const mappedType = {
    single_choice: 'single',
    multiple_choice: 'multiple',
    matching: 'matching',
    text: 'text'
  }[rawTask.task_type] || 'text';

  const options = rawTask.options || [];

  return {
    id: rawTask.id,
    type: mappedType,
    skill: rawTask.competency_title || rawTask.competency_code || 'Компетенция',
    difficultyLabel: difficultyLabels[rawTask.difficulty] || rawTask.difficulty,
    title: rawTask.title,
    text: rawTask.question_text,
    options: Array.isArray(options) ? options : [],
    pairs: Array.isArray(options.pairs) ? options.pairs : [],
    matches: Array.isArray(options.matches) ? options.matches : []
  };
}

function renderAnswerControls(task) {
  answerInput.classList.add('hidden');
  choiceAnswerField.classList.add('hidden');
  matchingAnswerField.classList.add('hidden');

  if (task.type === 'text') {
    answerLabel.textContent = 'Ответ кандидата';
    answerLabel.setAttribute('for', 'answerInput');
    answerInput.classList.remove('hidden');
    answerInput.placeholder = 'Введите развернутый ответ...';
    typingHint.textContent = 'Ответ и поведенческие признаки будут отправлены в backend.';
    return;
  }

  if (task.type === 'single' || task.type === 'multiple') {
    answerLabel.textContent = task.type === 'single' ? 'Выберите один вариант' : 'Выберите все подходящие варианты';
    answerLabel.removeAttribute('for');
    choiceAnswerField.classList.remove('hidden');
    renderChoiceOptions(task);
    typingHint.textContent = 'Выбор вариантов сохраняется как ответ в backend.';
    return;
  }

  if (task.type === 'matching') {
    answerLabel.textContent = 'Найдите соответствие';
    answerLabel.removeAttribute('for');
    matchingAnswerField.classList.remove('hidden');
    renderMatchingOptions(task);
    typingHint.textContent = 'Выбранные соответствия будут проверены backend-ом.';
  }
}

function renderChoiceOptions(task) {
  const optionType = task.type === 'single' ? 'radio' : 'checkbox';

  task.options.forEach((option) => {
    const optionLabel = document.createElement('label');
    optionLabel.className = 'choice-option';

    const input = document.createElement('input');
    input.type = optionType;
    input.name = 'taskChoice';
    input.value = option.id;

    const text = document.createElement('span');
    text.textContent = option.label;

    optionLabel.append(input, text);
    choiceAnswerField.append(optionLabel);
  });
}

function renderMatchingOptions(task) {
  task.pairs.forEach((pair) => {
    const row = document.createElement('div');
    row.className = 'match-row';

    const source = document.createElement('div');
    source.className = 'match-source';
    source.textContent = pair.label;

    const select = document.createElement('select');
    select.dataset.matchSource = pair.id;
    select.setAttribute('aria-label', `Соответствие для ${pair.label}`);

    const emptyOption = document.createElement('option');
    emptyOption.value = '';
    emptyOption.textContent = 'Выберите пункт';
    select.append(emptyOption);

    task.matches.forEach((match) => {
      const option = document.createElement('option');
      option.value = match.id;
      option.textContent = match.label;
      select.append(option);
    });

    row.append(source, select);
    matchingAnswerField.append(row);
  });

  updateMatchingOptionsAvailability();
}

submitAnswerBtn.addEventListener('click', async () => {
  const task = session.currentTask;
  if (!task || !session.backendSessionId) return;

  const answerData = getCurrentAnswer(task);
  if (!isAnswerComplete(task, answerData)) {
    alert(getIncompleteAnswerMessage(task));
    return;
  }

  submitAnswerBtn.disabled = true;
  submitAnswerBtn.textContent = 'Отправка...';

  try {
    const answerText = serializeAnswer(task, answerData);
    const payload = buildAnswerPayload(task, answerData);
    const behavior = buildBehaviorPayload(answerText);

    // Backend является источником истины: именно он проверяет ответ,
    // сохраняет его в PostgreSQL и пересчитывает BKT.
    const result = await apiRequest(`/sessions/${session.backendSessionId}/answers`, {
      method: 'POST',
      body: {
        task_id: task.id,
        answer_text: answerText,
        answer_payload: payload,
        behavior
      }
    });

    const evaluation = result.evaluation;
    const evaluationStatus = evaluation.status || 'evaluated';
    const backendRisk = Number(result.behavior?.anomaly_probability || 0);
    const nextTask = result.next_task;

    session.answers.push({
      task,
      answer: answerText,
      isCorrect: evaluation.is_correct,
      score: evaluation.score,
      risk: backendRisk,
      evaluationStatus
    });

    if (evaluationStatus === 'evaluated' && evaluation.is_correct) {
      session.correctAnswers += 1;
    }

    session.bktStates = result.bkt_states || session.bktStates;
    session.knowledgeProbability = nextTask
      ? getTaskBktProbability(session.bktStates, nextTask)
      : Number(result.answer.bkt_after ?? session.knowledgeProbability);
    session.currentRisk = backendRisk;

    if (evaluationStatus === 'evaluation_failed') {
      addLog(`LLM не оценила ответ: ${evaluation.feedback}`);
      addLog('BKT не изменялся, задание пропущено в оценке компетенции');
    } else {
      addLog(evaluation.is_correct ? 'Backend оценил ответ как корректный' : 'Backend отметил, что ответ требует доработки');
      addLog(`BKT: ${Math.round(result.answer.bkt_before * 100)}% -> ${Math.round(result.answer.bkt_after * 100)}%`);
    }
    addLog(`Риск внешней помощи: ${Math.round(backendRisk * 100)}%`);

    updateDashboard();

    if (result.session_finished) {
      await finishInterviewFromBackend(result.session_summary);
    } else {
      await loadTask(nextTask);
    }
  } catch (error) {
    alert(`Не удалось отправить ответ: ${error.message}`);
    addLog(`Ошибка отправки ответа: ${error.message}`);
  } finally {
    submitAnswerBtn.disabled = false;
    submitAnswerBtn.textContent = 'Отправить ответ';
  }
});

function getCurrentAnswer(task) {
  if (task.type === 'text') {
    return answerInput.value.trim();
  }

  if (task.type === 'single') {
    const selectedOption = choiceAnswerField.querySelector('input[name="taskChoice"]:checked');
    return selectedOption ? selectedOption.value : '';
  }

  if (task.type === 'multiple') {
    return [...choiceAnswerField.querySelectorAll('input[name="taskChoice"]:checked')]
      .map((input) => input.value);
  }

  if (task.type === 'matching') {
    const matches = {};
    matchingAnswerField.querySelectorAll('select[data-match-source]').forEach((select) => {
      matches[select.dataset.matchSource] = select.value;
    });
    return matches;
  }

  return '';
}

function isAnswerComplete(task, answerData) {
  if (task.type === 'text' || task.type === 'single') {
    return Boolean(answerData);
  }

  if (task.type === 'multiple') {
    return answerData.length > 0;
  }

  if (task.type === 'matching') {
    return Object.values(answerData).every(Boolean);
  }

  return false;
}

function getIncompleteAnswerMessage(task) {
  if (task.type === 'single') return 'Выберите один вариант ответа.';
  if (task.type === 'multiple') return 'Выберите хотя бы один вариант ответа.';
  if (task.type === 'matching') return 'Заполните все соответствия перед отправкой.';
  return 'Введите ответ перед отправкой.';
}

function buildAnswerPayload(task, answerData) {
  if (task.type === 'single') return { option_id: answerData };
  if (task.type === 'multiple') return { option_ids: answerData };
  if (task.type === 'matching') return { matches: answerData };
  return { text: answerData };
}

function serializeAnswer(task, answerData) {
  if (task.type === 'text') {
    return answerData;
  }

  if (task.type === 'single') {
    return getOptionLabel(task.options, answerData);
  }

  if (task.type === 'multiple') {
    return answerData.map((optionId) => getOptionLabel(task.options, optionId)).join('; ');
  }

  if (task.type === 'matching') {
    return task.pairs.map((pair) => {
      const matchLabel = getOptionLabel(task.matches, answerData[pair.id]);
      return `${pair.label} -> ${matchLabel}`;
    }).join('; ');
  }

  return '';
}

function getOptionLabel(options, optionId) {
  const option = options.find((item) => item.id === optionId);
  return option ? option.label : '';
}

answerInput.addEventListener('input', () => {
  if (!session.started) return;
  recordAnswerInteraction(answerInput.value.length);
});

choiceAnswerField.addEventListener('change', () => {
  if (!session.started) return;
  recordAnswerInteraction(getSerializedCurrentAnswer().length);
});

matchingAnswerField.addEventListener('change', () => {
  if (!session.started) return;
  updateMatchingOptionsAvailability();
  recordAnswerInteraction(getSerializedCurrentAnswer().length);
});

answerInput.addEventListener('beforeinput', (event) => {
  if (!session.started) return;

  if (event.inputType && event.inputType.includes('delete')) {
    session.behavior.deleteCount += 1;
  }
});

answerInput.addEventListener('paste', (event) => {
  if (!session.started) return;

  const pastedText = event.clipboardData?.getData('text') || '';
  session.behavior.pasteCount += 1;
  session.behavior.pastedCharsTotal += pastedText.length;

  addLog(`Вставка текста: ${pastedText.length} символов`);
  updateDashboard();
});

document.addEventListener('copy', () => {
  if (!session.started) return;

  const selectedText = window.getSelection().toString();
  session.behavior.copyCount += 1;

  addLog(`Копирование текста: ${selectedText.length} символов`);
  updateDashboard();
});

document.addEventListener('visibilitychange', () => {
  if (!session.started) return;

  if (document.hidden) {
    session.behavior.tabSwitchCount += 1;
    session.behavior.hiddenStartedAt = Date.now();
    addLog('Страница стала скрытой / кандидат ушёл со вкладки');
  } else if (session.behavior.hiddenStartedAt) {
    const hiddenTime = Date.now() - session.behavior.hiddenStartedAt;
    session.behavior.totalHiddenTimeMs += hiddenTime;
    session.behavior.hiddenStartedAt = null;
    addLog(`Возврат на страницу через ${Math.round(hiddenTime / 1000)} сек.`);
  }

  updateDashboard();
});

window.addEventListener('blur', () => {
  if (session.started) addLog('Окно потеряло фокус');
});

window.addEventListener('focus', () => {
  if (session.started) addLog('Окно снова в фокусе');
});

function recordAnswerInteraction(currentLength) {
  const now = Date.now();
  const delta = currentLength - session.behavior.previousAnswerLength;

  session.behavior.inputEventCount += 1;
  session.behavior.maxInputBurstChars = Math.max(session.behavior.maxInputBurstChars, Math.abs(delta));

  if (session.behavior.lastInputTime) {
    const pause = now - session.behavior.lastInputTime;
    session.behavior.maxPauseMs = Math.max(session.behavior.maxPauseMs, pause);
  }

  session.behavior.previousAnswerLength = currentLength;
  session.behavior.lastInputTime = now;

  updateDashboard();
}

function getSerializedCurrentAnswer() {
  if (!session.started || !session.currentTask) return '';
  return serializeAnswer(session.currentTask, getCurrentAnswer(session.currentTask));
}

function updateMatchingOptionsAvailability() {
  const selects = [...matchingAnswerField.querySelectorAll('select[data-match-source]')];
  const selectedValues = selects.map((select) => select.value).filter(Boolean);

  selects.forEach((select) => {
    [...select.options].forEach((option) => {
      option.disabled = Boolean(option.value) &&
        option.value !== select.value &&
        selectedValues.includes(option.value);
    });
  });
}

function buildBehaviorPayload(answerText) {
  const behavior = session.behavior;
  const now = Date.now();
  const responseTimeSec = behavior.taskStart ? (now - behavior.taskStart) / 1000 : 0;
  const hiddenTimeSec = behavior.totalHiddenTimeMs / 1000;
  const answerLength = answerText.length;

  return {
    response_time_sec: Math.round(responseTimeSec),
    tab_switch_count: behavior.tabSwitchCount,
    total_hidden_time_sec: Math.round(hiddenTimeSec),
    hidden_time_ratio: responseTimeSec > 0 ? hiddenTimeSec / responseTimeSec : 0,
    paste_count: behavior.pasteCount,
    pasted_chars_total: behavior.pastedCharsTotal,
    paste_ratio: answerLength > 0 ? behavior.pastedCharsTotal / answerLength : 0,
    input_event_count: behavior.inputEventCount,
    delete_count: behavior.deleteCount,
    max_pause_sec: Math.round(behavior.maxPauseMs / 1000),
    max_input_burst_chars: behavior.maxInputBurstChars,
    copy_count: behavior.copyCount,
    answer_length_chars: answerLength
  };
}

function updateDashboard() {
  const knowledgePercent = Math.round(session.knowledgeProbability * 100);
  const risk = session.currentRisk;
  const riskPercent = Math.round(risk * 100);

  knowledgeValue.textContent = `${knowledgePercent}%`;
  knowledgeBar.style.width = `${knowledgePercent}%`;

  riskValue.textContent = `${riskPercent}%`;
  riskBar.style.width = `${riskPercent}%`;

  riskValue.classList.remove('risk-low', 'risk-medium', 'risk-high');
  riskValue.classList.add(getRiskClass(risk));
}

function getRiskClass(risk) {
  return riskLevelInfo[getRiskLevelKey(risk)].className;
}

function getRiskLabel(risk) {
  return riskLevelInfo[getRiskLevelKey(risk)].label;
}

function getRiskDescription(risk) {
  return riskLevelInfo[getRiskLevelKey(risk)].description;
}

function getRiskLevelKey(risk) {
  if (risk < 0.31) return 'low';
  if (risk < 0.71) return 'medium';
  return 'high';
}

function getCompetencyIndexInfo(score) {
  return competencyIndexLevels.find((level) => score < level.max) ||
    competencyIndexLevels[competencyIndexLevels.length - 1];
}

async function finishInterviewFromBackend(summary) {
  let finalSummary = summary;
  if (!finalSummary && session.backendSessionId) {
    finalSummary = await apiRequest(`/sessions/${session.backendSessionId}/finish`, {
      method: 'POST',
      body: {}
    });
  }

  session.started = false;
  interviewCard.classList.add('hidden');
  reportCard.classList.remove('hidden');

  const finalRisk = Number(finalSummary?.anomaly_probability ?? session.currentRisk);
  const finalScore = Number(finalSummary?.final_score ?? 0);
  const finalProfile = finalSummary?.score_profile || finalSummary?.bkt_profile || finalSummary?.final_bkt_profile || session.bktStates;
  const taskTrajectory = finalSummary?.task_trajectory || [];
  const totalCompetencies = finalProfile.length;
  const checkedCompetencies = Number(
    finalSummary?.evaluated_competencies_count ?? countCheckedCompetencies(finalProfile)
  );
  const finalRiskPercent = Math.round(finalRisk * 100);
  const finalScorePercent = Math.round(finalScore * 100);
  const riskInfo = riskLevelInfo[getRiskLevelKey(finalRisk)];
  const indexInfo = getCompetencyIndexInfo(finalScore);
  const reportRiskValue = document.getElementById('reportRisk');
  const reportIndexValue = document.getElementById('reportAnswers');

  session.bktStates = finalProfile;
  session.currentRisk = finalRisk;

  document.getElementById('reportProfileCount').textContent = `${checkedCompetencies} / ${totalCompetencies}`;
  document.getElementById('reportProfileLevel').textContent =
    checkedCompetencies > 0
      ? `Проверены ${checkedCompetencies} из ${totalCompetencies} компетенций.`
      : 'Оцененных компетенций пока нет.';
  reportRiskValue.textContent = `${finalRiskPercent}%`;
  reportRiskValue.classList.remove('risk-low', 'risk-medium', 'risk-high');
  reportRiskValue.classList.add(riskInfo.className);
  document.getElementById('reportRiskLevel').textContent = riskInfo.description;
  reportIndexValue.textContent = `${finalScorePercent}%`;
  reportIndexValue.classList.remove('score-low', 'score-basic', 'score-good', 'score-high');
  reportIndexValue.classList.add(indexInfo.className);
  document.getElementById('reportIndexLevel').textContent = indexInfo.description;
  document.getElementById('reportCoverageNote').textContent =
    `Проверено компетенций: ${checkedCompetencies} из ${totalCompetencies}. ` +
    'Непроверенные компетенции не включались в расчет индекса.';
  renderBktProfile(finalProfile);
  renderTaskTrajectory(taskTrajectory);
  document.getElementById('reportText').textContent =
    finalSummary?.report_text ||
    `Интервью завершено. Индекс компетентности по проверенным компетенциям: ${finalScorePercent}% — ${indexInfo.label}. ` +
    `Риск аномального поведения: ${finalRiskPercent}% — ${riskInfo.label}.`;

  addLog('Интервью завершено, итог сохранён в backend');
  updateDashboard();
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    body: options.body ? JSON.stringify(options.body) : undefined
  });

  let data = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }

  if (!response.ok) {
    throw new Error(data?.detail || `HTTP ${response.status}`);
  }

  return data;
}

function createEmptyBehavior() {
  return {
    taskStart: null,
    lastInputTime: null,
    inputEventCount: 0,
    deleteCount: 0,
    pasteCount: 0,
    pastedCharsTotal: 0,
    copyCount: 0,
    tabSwitchCount: 0,
    totalHiddenTimeMs: 0,
    hiddenStartedAt: null,
    maxInputBurstChars: 0,
    previousAnswerLength: 0,
    maxPauseMs: 0
  };
}

function getTaskBktProbability(states, rawTask) {
  if (!Array.isArray(states) || states.length === 0) {
    return 0.45;
  }

  const taskCode = rawTask?.competency_code;
  const taskTitle = rawTask?.competency_title;
  const state = states.find((item) => item.code === taskCode || item.title === taskTitle);
  return Number(state?.probability ?? states[0]?.probability ?? 0.45);
}

function renderBktProfile(profile) {
  const container = document.getElementById('reportBktProfile');
  container.innerHTML = '';

  if (!Array.isArray(profile) || profile.length === 0) {
    container.textContent = 'Профиль компетенций не был сформирован.';
    return;
  }

  const chart = document.createElement('div');
  chart.className = 'competency-profile-chart';

  profile.forEach((item) => {
    const unchecked = isUncheckedCompetency(item);
    const scoreValue = unchecked ? null : clampPercentValue(item.score);
    const scorePercent = scoreValue === null ? 0 : Math.round(scoreValue * 100);
    const bktPercent = Math.round(Number(item.bkt_probability ?? item.probability ?? 0) * 100);
    const evaluatedCount = Number(item.evaluated_answers_count || 0);

    const label = document.createElement('div');
    label.className = 'competency-axis-label';
    if (unchecked) {
      label.classList.add('competency-axis-label-unchecked');
    }

    const title = document.createElement('div');
    title.className = 'competency-title';
    title.textContent = item.title || item.code || 'Компетенция';

    const meta = document.createElement('div');
    meta.className = 'competency-meta';
    meta.textContent = `${getScoreLevelLabel(item.score_level)} · BKT ${bktPercent}% · оценено заданий: ${evaluatedCount}`;

    const plotCell = document.createElement('div');
    plotCell.className = 'competency-plot-cell';
    plotCell.style.setProperty('--score-percent', `${scorePercent}%`);

    const track = document.createElement('div');
    track.className = 'competency-bar-track';

    const bar = document.createElement('div');
    bar.className = `competency-bar-fill ${getScoreClass(scoreValue)}`;
    if (unchecked) {
      bar.classList.add('competency-bar-empty');
    }

    const value = document.createElement('span');
    value.className = 'competency-bar-value';
    if (scoreValue !== null && scorePercent >= 86) {
      value.classList.add('competency-bar-value-inside');
    }
    value.textContent = scoreValue === null ? 'нет score' : `${scorePercent}%`;

    label.append(title, meta);
    track.append(bar, value);
    plotCell.append(track);
    chart.append(label, plotCell);
  });

  const axisSpacer = document.createElement('div');
  axisSpacer.className = 'competency-chart-axis-spacer';

  const axis = document.createElement('div');
  axis.className = 'competency-chart-axis';
  for (let tick = 0; tick <= 100; tick += 10) {
    const tickLabel = document.createElement('span');
    tickLabel.textContent = `${tick}%`;
    tickLabel.style.left = `${tick}%`;
    if (tick === 0) tickLabel.classList.add('axis-tick-start');
    if (tick === 100) tickLabel.classList.add('axis-tick-end');
    axis.append(tickLabel);
  }

  chart.append(axisSpacer, axis);
  container.append(chart);
}

function clampPercentValue(value) {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.min(Math.max(numeric, 0), 1);
}

function getScoreClass(score) {
  if (score === null || score === undefined) return 'score-none';
  return getCompetencyIndexInfo(score).className;
}

function countCheckedCompetencies(profile) {
  if (!Array.isArray(profile)) return 0;
  return profile.filter((item) => !isUncheckedCompetency(item)).length;
}

function isUncheckedCompetency(item) {
  return item.score === null ||
    item.score === undefined ||
    item.score_level === 'not_checked' ||
    Number(item.evaluated_answers_count || 0) === 0;
}

function renderTaskTrajectory(trajectory) {
  const container = document.getElementById('reportTaskTrajectory');
  container.innerHTML = '';

  if (!Array.isArray(trajectory) || trajectory.length === 0) {
    container.textContent = 'Траектория заданий не была сформирована.';
    return;
  }

  trajectory.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'trajectory-row';

    const title = document.createElement('div');
    title.className = 'trajectory-title';
    title.textContent = `${item.position}. ${item.task_title || 'Задание'}`;

    const meta = document.createElement('div');
    meta.className = 'trajectory-meta';
    const scoreText = item.score === null || item.score === undefined
      ? 'score: нет оценки'
      : `score: ${Math.round(Number(item.score) * 100)}%`;
    const bktAfter = item.bkt_after === null || item.bkt_after === undefined
      ? 'BKT: без изменения'
      : `BKT после: ${Math.round(Number(item.bkt_after) * 100)}%`;
    meta.textContent = [
      item.competency_title || 'Компетенция',
      difficultyLabels[item.difficulty] || item.difficulty,
      scoreText,
      bktAfter,
      `риск: ${getRiskLabel(Number(item.anomaly_probability || 0))}`
    ].filter(Boolean).join(' · ');

    row.append(title, meta);
    container.append(row);
  });
}

function getScoreLevelLabel(level) {
  const labels = {
    not_checked: 'не проверялась',
    low: 'низкий уровень',
    medium: 'средний уровень',
    high: 'высокий уровень'
  };
  return labels[level] || 'оценена';
}

function getCompetencyStatusLabel(status) {
  const labels = {
    not_checked: 'не проверялась',
    weak_fixed: 'зафиксирована как слабая',
    weak: 'слабая зона',
    developing: 'средний уровень',
    strong: 'сильная сторона'
  };
  return labels[status] || 'оценена';
}

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value;
  return element.innerHTML;
}

function addLog(message) {
  const time = new Date().toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });

  const item = document.createElement('div');
  item.className = 'log-item';
  item.textContent = `[${time}] ${message}`;

  logList.prepend(item);
}

updateDashboard();
