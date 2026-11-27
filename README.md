# EnglishDaily — пакеты озвучки

Необязательные пакеты качественной озвучки для приложения EnglishDaily (Android).
Приложение остаётся лёгким; пакеты скачиваются в настройках по желанию.

- Файлы пакетов лежат в **Releases** этого репозитория.
- Оглавление пакетов — `manifest.json` в ветке `main`; приложение читает его по адресу
  `https://raw.githubusercontent.com/4angmax-dot/englishdaily-audio/main/manifest.json`.
- Формат: Opus 20 кбит/с в WebM, моно; внутри пакета — ZIP (без сжатия) с файлами `<hash>.webm` и `index.json`.
- Голоса: US — Matilda (жен.), Brian (муж.); UK — Alice (жен.), George (муж.). Модель ElevenLabs v4 turbo.
