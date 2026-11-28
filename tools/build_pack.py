#!/usr/bin/env python3
"""
Озвучка и сборка пакетов для EnglishDaily — постепенно, с журналом.

Озвучить (продолжает с того места, где остановились; уже озвученное не трогает):
  python3 tools/build_pack.py voice --items ../englishdaily-android/app/src/main/assets/items.tsv \
      --kind words --accent us --voice matilda --max-credits 50000

Посмотреть, сколько готово и сколько кредитов нужно на остаток:
  python3 tools/build_pack.py status --items .../items.tsv

Собрать пакет из готовых записей (zip + json для публикации через ветку incoming):
  python3 tools/build_pack.py pack --items .../items.tsv --kind words --accent us --voice matilda --version 1

kind=words     — заголовки карточек (слова, фразы, чанки) + формы слов для пропусков (won для win). Голос --voice.
kind=examples  — примеры и диалоги: реплика A — женский голос акцента, B — мужской; ответы «второго контекста» — A.

Записи хранятся в репозитории: clips/<модель>/<голос>/<ключ>.webm (Opus 20 кбит/с) — не пропадут вместе
с контейнером. Каждые 200 записей скрипт сам делает коммит и push (записи + журнал).
Ключ клипа = первые 16 знаков SHA-1 от «[роль|]нормализованный текст» — так же, как в приложении (VoicePacks.kt).
Бесплатный план ElevenLabs без коммерческой лицензии: с ним записи идут только во временную папку trial/
(не в git) и в журнал с пометкой «пробно». Ключ API подставляет сетевой прокси (xi-api-key)
или переменная ELEVENLABS_API_KEY.
"""
import argparse, datetime, hashlib, json, os, re, subprocess, sys, zipfile, concurrent.futures as cf, threading

VOICES = {"matilda": "XrExE9yKIg1WjnnlVkGX", "brian": "nPczCjzI2devNBz1zQrb",
          "alice": "Xb7hH8MSUJpSbSDYk0k2", "george": "JBFqnCBsd6RMkjVDRZzb"}
PAIRS = {"us": ("matilda", "brian"), "uk": ("alice", "george")}   # (A — женский, B — мужской)
RATE = {"eleven_v4_turbo": 0.22, "eleven_flash_v2_5": 0.5, "eleven_turbo_v2_5": 0.5}  # кредитов за символ (v4 turbo — по факту списаний), остальное — 1
LEVEL = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5}
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PLANNED = [("words", "us", "matilda"), ("words", "us", "brian"), ("words", "uk", "alice"), ("words", "uk", "george"),
           ("examples", "us", ""), ("examples", "uk", "")]

def norm(s): return re.sub(r"\s+", " ", s.replace("’", "'")).strip().lower()
def key(text, role=""): return hashlib.sha1(((role + "|") if role else "").encode() + norm(text).encode()).hexdigest()[:16]

def spoken(text):
    """Что произносить: как Speech.clean() в приложении — слоты «…» убираем."""
    t = re.sub(r"…ing\b", "", text).replace("…", ",")
    t = re.sub(r",\s*([,.?!])", r"\1", t)
    t = re.sub(r"\(.*?\)", "", t)
    return re.sub(r"\s+", " ", t).strip().strip(",").strip()

def load(path):
    L = open(path, encoding="utf-8").read().rstrip("\n").split("\n")
    h = L[0].split("\t")
    rows = []
    for l in L[1:]:
        f = l.split("\t"); rows.append(dict(zip(h, f + [""] * (len(h) - len(f)))))
    rows = [r for r in rows if r.get("tag") != "hide" and r.get("kind") in ("w", "p", "k")]
    # Порядок озвучки = порядок изучения: сначала лёгкое и частое.
    rows.sort(key=lambda r: (LEVEL.get(r.get("cefr", ""), 3), int(r.get("band") or 9), int(r.get("rank") or 0)))
    return rows

def jobs_for(rows, kind, accent, voice):
    out, seen = [], set()
    def add(text, role, v):
        k = key(text, role)
        if k in seen or not spoken(text): return
        seen.add(k); out.append((k, spoken(text), v))
    a, b = PAIRS[accent]
    for r in rows:
        if kind == "words":
            add(r["text"], "", voice)
            ans = r.get("cloze_ans", "")
            if ans and norm(ans) != norm(r["text"]): add(ans, "", voice)
        else:
            for ln in [x.strip() for x in r.get("ex", "").split(" | ") if x.strip()]:
                m = re.match(r"^([AB]): (.*)$", ln)
                role, text = (m.group(1), m.group(2)) if m else ("A", ln)
                add(text, role, a if role == "A" else b)
            for ans in r.get("ctx_ans", "").split(" | ")[:1]:
                if ans.strip(): add(ans.strip(), "A", a)
    return out

def clip_path(model, voice, k, trial=False):
    base = os.path.join(ROOT, "trial" if trial else "clips", model, voice)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, k + ".webm")

def api(path):
    r = subprocess.run(["curl", "-sS", f"https://api.elevenlabs.io{path}"] + auth(), capture_output=True, text=True)
    return json.loads(r.stdout or "{}")

def auth():
    return ["-H", "xi-api-key: " + os.environ["ELEVENLABS_API_KEY"]] if os.environ.get("ELEVENLABS_API_KEY") else []

def tts(text, voice, model, dst):
    """Озвучить → MP3 во временный файл → Opus в dst. True при успехе."""
    tmp = dst + ".mp3.part"
    err = ""
    for _ in range(3):
        r = subprocess.run(["curl", "-sS", "-o", tmp, "-w", "%{http_code}", "-X", "POST",
                            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICES[voice]}?output_format=mp3_44100_128",
                            "-H", "Content-Type: application/json", *auth(),
                            "-d", json.dumps({"text": text, "model_id": model})], capture_output=True, text=True)
        if r.stdout.strip() == "200" and os.path.getsize(tmp) > 1000:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-ac", "1", "-c:a", "libopus", "-b:a", "20k",
                            "-application", "voip", dst + ".part.webm"], check=True)
            os.replace(dst + ".part.webm", dst); os.remove(tmp)
            return True
        err = open(tmp, errors="ignore").read()[:300] if os.path.exists(tmp) else r.stderr
        if "quota" in err.lower() or "credits" in err.lower(): raise SystemExit("Кредиты закончились: " + err)
    print("ОШИБКА:", text, err, file=sys.stderr)
    return False

# ---------------------------------------------------------------- журнал

JPATH = os.path.join(ROOT, "journal.json")

def journal():
    return json.load(open(JPATH)) if os.path.exists(JPATH) else {"runs": []}

def save_journal(j, items):
    json.dump(j, open(JPATH, "w"), ensure_ascii=False, indent=1)
    lines = ["# Журнал озвучки", "", "Обновляется скриптом `tools/build_pack.py`. Записи — в `clips/`.", "",
             "## Готовность пакетов", "", "| Пакет | Готово | Осталось символов | Нужно кредитов |", "|---|---|---|---|"]
    for row in status_rows(items):
        lines.append(f"| {row[0]} | {row[1]}/{row[2]} | {row[3]:,} | {row[4]:,} |".replace(",", " "))
    lines += ["", "## Запуски", "", "| Дата | Пакет | Голос | Модель | Новых записей | Символов | Кредитов | Тариф |", "|---|---|---|---|---|---|---|---|"]
    for r in reversed(j["runs"]):
        lines.append(f"| {r['date']} | {r['pack']} | {r['voice']} | {r['model']} | {r['clips']} | {r['chars']} | {r['credits']} | {r['tier']}{' (пробно, не в git)' if r.get('trial') else ''} |")
    open(os.path.join(ROOT, "JOURNAL.md"), "w").write("\n".join(lines) + "\n")

def status_rows(items, model="eleven_v4_turbo"):
    rows = load(items) if items else []
    out = []
    for kind, accent, voice in PLANNED:
        jobs = jobs_for(rows, kind, accent, voice or PAIRS[accent][0])
        done = [j for j in jobs if os.path.exists(clip_path(model, j[2], j[0]))]
        left = sum(len(j[1]) for j in jobs if j not in done)
        name = f"words_{accent}_{voice}" if kind == "words" else f"examples_{accent}"
        out.append((name, len(done), len(jobs), left, int(left * RATE.get(model, 1))))
    return out

def git_save(msg):
    subprocess.run(["git", "-C", ROOT, "add", "clips", "journal.json", "JOURNAL.md"], check=False)
    if subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode == 0: return
    subprocess.run(["git", "-C", ROOT, "commit", "-q", "-m", msg + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"], check=False)
    for _ in range(4):
        if subprocess.run(["git", "-C", ROOT, "-c", "push.negotiate=true", "push", "-q", "origin", "HEAD:main"]).returncode == 0: return
        subprocess.run(["git", "-C", ROOT, "pull", "-q", "--rebase", "origin", "main"])
    print("Не удалось отправить в GitHub — записи остались локально, отправьте вручную.", file=sys.stderr)

# ---------------------------------------------------------------- команды

def cmd_voice(a):
    sub = api("/v1/user/subscription")
    tier = sub.get("tier", "?")
    used_before = sub.get("character_count")
    trial = tier == "free" or a.trial
    if trial: print("Бесплатный план: записи — только в trial/ (не в git, не для публикации).")
    voice = a.voice or PAIRS[a.accent][0]
    pack = f"words_{a.accent}_{voice}" if a.kind == "words" else f"examples_{a.accent}"
    jobs = jobs_for(load(a.items), a.kind, a.accent, voice)
    rate = RATE.get(a.model, 1)
    todo, budget = [], a.max_credits / rate if a.max_credits else float("inf")
    for j in jobs:
        if os.path.exists(clip_path(a.model, j[2], j[0])) or (trial and os.path.exists(clip_path(a.model, j[2], j[0], True))): continue
        if budget - len(j[1]) < 0: break
        budget -= len(j[1]); todo.append(j)
    print(f"{pack}: всего {len(jobs)}, уже готово {len(jobs) - len([j for j in jobs if not os.path.exists(clip_path(a.model, j[2], j[0]))])}, "
          f"сейчас озвучиваем {len(todo)} ({sum(len(j[1]) for j in todo)} симв. ≈ {int(sum(len(j[1]) for j in todo) * rate)} кредитов)")
    j = journal(); lock = threading.Lock()
    run = {"date": datetime.date.today().isoformat(), "pack": pack, "voice": voice, "model": a.model,
           "clips": 0, "chars": 0, "credits": 0, "tier": tier, "trial": trial}
    j["runs"].append(run)
    done_since_save = [0]
    def work(job):
        k, text, v = job
        if tts(text, v, a.model, clip_path(a.model, v, k, trial)):
            with lock:
                run["clips"] += 1; run["chars"] += len(text); run["credits"] = int(run["chars"] * rate)
                done_since_save[0] += 1
                if done_since_save[0] >= 200 and not trial:
                    done_since_save[0] = 0
                    save_journal(j, a.items); git_save(f"Озвучка {pack}: +200 записей")
    try:
        with cf.ThreadPoolExecutor(a.threads) as ex: list(ex.map(work, todo))
    finally:
        # Фактический расход — по счётчику аккаунта (оценка по символам бывает неточной).
        after = api("/v1/user/subscription").get("character_count")
        if isinstance(used_before, int) and isinstance(after, int) and after >= used_before:
            run["credits"] = after - used_before
        save_journal(j, a.items)
        if not trial: git_save(f"Озвучка {pack}: {run['clips']} записей, {run['credits']} кредитов")
    print(f"Готово: {run['clips']} записей, {run['credits']} кредитов. Журнал — JOURNAL.md")

def cmd_status(a):
    print(f"{'Пакет':<22}{'Готово':>14}{'Осталось симв.':>18}{'Кредитов':>12}")
    for name, done, total, left, cr in status_rows(a.items):
        print(f"{name:<22}{f'{done}/{total}':>14}{left:>18,}{cr:>12,}")

def cmd_pack(a):
    voice = a.voice or PAIRS[a.accent][0]
    pid = a.id or (f"words_{a.accent}_{voice}" if a.kind == "words" else f"examples_{a.accent}")
    jobs = jobs_for(load(a.items), a.kind, a.accent, voice)
    os.makedirs(a.out, exist_ok=True)
    zpath = f"{a.out}/{pid}-v{a.version}.zip"
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for k, text, v in jobs:
            src = clip_path(a.model, v, k, a.trial)
            if os.path.exists(src): z.write(src, f"{k}.webm"); n += 1
        z.writestr("pack.json", json.dumps({"id": pid, "kind": a.kind, "accent": a.accent, "voice": voice,
                                            "version": a.version, "clips": n, "model": a.model}))
    sha = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    names = {"matilda": "Matilda", "brian": "Brian", "alice": "Alice", "george": "George"}
    acc = {"us": ("американский", "американський"), "uk": ("британский", "британський")}[a.accent]
    if a.kind == "words":
        g = "женский" if voice in ("matilda", "alice") else "мужской"; gu = "жіночий" if voice in ("matilda", "alice") else "чоловічий"
        title, title_uk = f"Слова и выражения — {names[voice]} ({g}, {acc[0]})", f"Слова й вирази — {names[voice]} ({gu}, {acc[1]})"
    else:
        title, title_uk = f"Примеры и диалоги ({acc[0]})", f"Приклади й діалоги ({acc[1]})"
    entry = {"id": pid, "kind": a.kind, "accent": a.accent, "voice": voice, "version": a.version, "clips": n,
             "total": len(jobs), "size": os.path.getsize(zpath), "sha256": sha, "url": "", "preview": "",
             "title": title, "title_uk": title_uk}
    open(f"{a.out}/{pid}-v{a.version}.json", "w").write(json.dumps(entry, ensure_ascii=False))
    print(json.dumps(entry, ensure_ascii=False))
    if n < len(jobs): print(f"Внимание: в пакете {n} из {len(jobs)} записей — остальное ещё не озвучено.")

def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("voice", "status", "pack"):
        p = sp.add_parser(name)
        p.add_argument("--items", required=True)
        p.add_argument("--model", default="eleven_v4_turbo")
        if name != "status":
            p.add_argument("--kind", choices=["words", "examples"], required=True)
            p.add_argument("--accent", choices=["us", "uk"], required=True)
            p.add_argument("--voice", default="")
            p.add_argument("--trial", action="store_true", help="пробно: записи в trial/, не в git")
        if name == "voice":
            p.add_argument("--max-credits", type=int, default=0, help="не тратить больше стольких кредитов за запуск")
            p.add_argument("--threads", type=int, default=2)
        if name == "pack":
            p.add_argument("--version", type=int, default=1); p.add_argument("--id", default=""); p.add_argument("--out", default="dist")
    a = ap.parse_args()
    {"voice": cmd_voice, "status": cmd_status, "pack": cmd_pack}[a.cmd](a)

if __name__ == "__main__":
    main()
