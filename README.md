# HARLEY — голосовой ассистент на Python (Windows)

HARLEY — локальный голосовой ассистент на Python с офлайн/онлайн распознаванием речи, несколькими движками синтеза речи и интеграцией с GPT для диалогов. Проект ориентирован на Windows (SAPI, управление громкостью через pycaw) и русскоязычный голосовой ввод.

## Возможности
- Голосовой ввод: офлайн Vosk (ru) или онлайн Google (через `speech_recognition`), fallback — текстовый ввод.
- Голосовой вывод (TTS):
  - Edge TTS (онлайн, `edge-tts`),
  - ElevenLabs (онлайн, по API‑ключу),
  - Windows SAPI (локально, `pywin32`) или `pyttsx3`,
  - VBS‑fallback, если ничего не доступно.
- GPT‑диалоги: потоковая генерация ответа (OpenAI API), проговаривание фраз по мере появления.
- Активация:
  - push‑to‑talk по горячей клавише (по умолчанию `X`),
  - режим «ключевое слово» (wake‑word) — «Harley/Харли».
- Команды и действия:
  - «Сколько времени?» / «Какая дата?»,
  - «Открой …» (калькулятор, Telegram, VS Code, браузер, сайт),
  - «Сделай тише/громче/на 30%/выключи звук» (через pycaw),
  - «Найди …» (поиск в браузере),
  - «Кто такой/что такое …» (кратко из Википедии),
  - «Расскажи анекдот»,
  - «Напомни через N минут/часов …» (локальные напоминания).
- Память и логирование: сохранение контекста диалога и истории чата в `data/`.

## Как это работает
- `HARLEYproject.py` — единый исполняемый файл с модулями:
  - STT: выбор между Vosk → Google → текст, в зависимости от доступности.
  - TTS: выбор Edge → ElevenLabs → Win32/pyttsx3 → VBS.
  - GPT: потоковая генерация с историей контекста и системным промптом.
  - Роутинг команд: парсинг фраз и вызов соответствующих действий.
  - Управление системой: запуск приложений, открытие сайтов, громкость, время/дата, напоминания.

## Требования
- ОС: Windows 10/11 (SAPI, pycaw).
- Python: 3.10+
- Пакеты (минимум):
  - `vosk`, `sounddevice`, `speechrecognition`
  - `pywin32`, `pyttsx3`, `edge-tts`, `playsound`
  - `wikipedia`, `openai`, `requests`
  - `pycaw`, `comtypes`

Пример установки (PowerShell):
```powershell
python -m venv .venv
. .venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install vosk sounddevice SpeechRecognition pywin32 pyttsx3 edge-tts playsound wikipedia openai requests pycaw comtypes
```

## Модели Vosk (офлайн STT)
- Скачайте русскую модель Vosk (ru) и распакуйте, затем:
  - поместите в `models/vosk-ru`, или
  - укажите путь в переменной окружения `VOSK_MODEL`, или
  - задайте `Settings.vosk_model_path` в коде.

## Ключи и переменные окружения
Настройка (PowerShell):
```powershell
# OpenAI
setx OPENAI_API_KEY "sk-..."
# при необходимости (совместимые хостинги)
# setx OPENAI_BASE_URL "https://api.your-gateway.example/v1"

# ElevenLabs (опционально)
setx ELEVENLABS_API_KEY "..."
setx ELEVENLABS_VOICE_ID "..."
```

Важно: Не храните ключи в коде и репозитории. Убедитесь, что в `HARLEYproject.py` нет хардкода `os.environ['OPENAI_API_KEY'] = ...` перед публикацией.

## Настройки
Базовые настройки собраны в `Settings` внутри `HARLEYproject.py`:
- `name`: имя ассистента.
- `use_wake_word`: активация по ключевому слову.
- `wake_words`: варианты ключевого слова (Harley/Харли …).
- `tts_rate`, `tts_volume`: скорость и громкость TTS.
- `tts_backend_preferred`: `auto` | `edge` | `eleven` | `win32`.
- `edge_tts_voice`: например, `ru-RU-SvetlanaNeural`.
- `sample_rate`, `phrase_timeout_sec`: параметры распознавания.
- `enable_gpt`, `gpt_model`, `gpt_max_tokens`, `gpt_context_turns`.
- `persist_context`, пути `data/context.json`, `data/chat_history.jsonl`.
- `push_to_talk`, `hotkey`: режим PTT и клавиша активации.

## Быстрый старт
```powershell
# 1) Активируйте venv и установите зависимости (см. выше)
# 2) Задайте OPENAI_API_KEY (если нужен GPT)
# 3) Распакуйте модель Vosk (если нужен офлайн STT)
# 4) Запустите
python HARLEYproject.py
```
- По умолчанию включён push‑to‑talk: зажмите/нажмите клавишу `X` и говорите.
- Для режима wake‑word установите `push_to_talk=False`.

## Примеры команд
- «Харли, сколько времени?»
- «Харли, какая сегодня дата?»
- «Открой калькулятор» / «Открой Телеграм» / «Открой VS Code»
- «Открой сайт example.com»
- «Сделай громче на 20» / «Сделай тише» / «Выключи звук»
- «Найди рецепт борща»
- «Кто такой Пушкин?»
- «Расскажи анекдот»
- «Напомни через 10 минут поставить чай»
- «Харли, объясни, что такое TCP/IP» (GPT)

## Структура проекта
- `HARLEYproject.py` — основной скрипт ассистента.
- `data/` — файлы состояния и логов:
  - `data/context.json` — краткая история контекста для GPT.
  - `data/chat_history.jsonl` — журнал диалога (по строке на сообщение).

## Примечания и ограничения
- Проект ориентирован на Windows (SAPI, управление громкостью через pycaw).
- Для Edge TTS и ElevenLabs требуется интернет.
- При отсутствии микрофона/моделей ассистент предлагает текстовый ввод.
- Не публикуйте API‑ключи в открытом доступе (git‑история, код, issues).


