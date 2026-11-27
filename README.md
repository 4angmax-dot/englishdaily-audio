# EnglishDaily — пакеты озвучки

Необязательные пакеты качественной озвучки для приложения EnglishDaily (Android).
Приложение остаётся лёгким; пакеты скачиваются в настройках по желанию.

- Файлы пакетов лежат в **Releases** этого репозитория.
- Оглавление пакетов — `manifest.json` в ветке `main`; приложение читает его по адресу
  `https://raw.githubusercontent.com/4angmax-dot/englishdaily-audio/main/manifest.json`.
- Формат: Opus 20 кбит/с в WebM, моно; внутри пакета — ZIP (без сжатия) с файлами `<hash>.webm` и `index.json`.
- Голоса: US — Matilda (жен.), Brian (муж.); UK — Alice (жен.), George (муж.). Модель ElevenLabs v4 turbo.

## Как выложить пакет
1. Собрать: `python3 tools/build_pack.py ... --out dist` → `dist/<id>-v<N>.zip` и `dist/<id>-v<N>.json`.
2. Положить оба файла в папку `packs/` ветки `incoming` и отправить ветку.
3. Workflow «Publish voice packs» сам создаст релиз, обновит `manifest.json` и удалит ветку `incoming`.
Убрать пакет из приложения: `packs/remove.txt` со списком id в ветке `incoming`.
