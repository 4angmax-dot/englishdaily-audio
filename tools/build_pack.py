#!/usr/bin/env python3
"""
Сборка пакета озвучки для EnglishDaily.

  python3 tools/build_pack.py --items ../englishdaily-android/app/src/main/assets/items.tsv \
      --kind words --accent us --voice matilda --version 1 [--limit-chars 9000] [--model eleven_v4_turbo]

kind=words     — заголовки карточек (слова, фразы, чанки) + формы слов для пропусков (won для win). Голос --voice.
kind=examples  — примеры и диалоги: реплика A — женский голос акцента, B — мужской; ответы «второго контекста» — A.

Ключ клипа (имя файла) = первые 16 знаков SHA-1 от «[роль|]нормализованный текст» — так же считает приложение
(data/VoicePacks.kt). Озвученное кэшируется в cache/ (повторная сборка не тратит кредиты). Ключ API ElevenLabs
подставляет сетевой прокси (заголовок xi-api-key) либо переменная окружения ELEVENLABS_API_KEY.
"""
import argparse, hashlib, json, os, re, subprocess, sys, zipfile, concurrent.futures as cf

VOICES = {"matilda": "XrExE9yKIg1WjnnlVkGX", "brian": "nPczCjzI2devNBz1zQrb",
          "alice": "Xb7hH8MSUJpSbSDYk0k2", "george": "JBFqnCBsd6RMkjVDRZzb"}
PAIRS = {"us": ("matilda", "brian"), "uk": ("alice", "george")}   # (A — женский, B — мужской)
LEVEL = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5}

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
    rows = [dict(zip(h, l.split("\t") + [""] * (len(h) - len(l.split("\t"))))) for l in L[1:]]
    rows = [r for r in rows if r.get("tag") != "hide" and r.get("kind") in ("w", "p", "k")]
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
            lines = [x.strip() for x in r.get("ex", "").split(" | ") if x.strip()]
            for ln in lines:
                m = re.match(r"^([AB]): (.*)$", ln)
                role, text = (m.group(1), m.group(2)) if m else ("A", ln)
                add(text, role, a if role == "A" else b)
            for ans in r.get("ctx_ans", "").split(" | ")[:1]:
                if ans.strip(): add(ans.strip(), "A", a)
    return out

def tts(text, voice, model, dst):
    tmp = dst + ".part"
    hdr = ["-H", "Content-Type: application/json"]
    if os.environ.get("ELEVENLABS_API_KEY"): hdr += ["-H", "xi-api-key: " + os.environ["ELEVENLABS_API_KEY"]]
    for _ in range(3):
        r = subprocess.run(["curl", "-sS", "-o", tmp, "-w", "%{http_code}", "-X", "POST",
                            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICES[voice]}?output_format=mp3_44100_128", *hdr,
                            "-d", json.dumps({"text": text, "model_id": model})], capture_output=True, text=True)
        if r.stdout.strip() == "200" and os.path.getsize(tmp) > 1000:
            os.replace(tmp, dst); return True
        err = open(tmp, errors="ignore").read()[:300] if os.path.exists(tmp) else r.stderr
        if "quota" in err or "credits" in err.lower(): sys.exit("Кредиты закончились: " + err)
    print("ОШИБКА:", text, err, file=sys.stderr); return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True); ap.add_argument("--kind", choices=["words", "examples"], required=True)
    ap.add_argument("--accent", choices=["us", "uk"], required=True); ap.add_argument("--voice", default="")
    ap.add_argument("--version", type=int, default=1); ap.add_argument("--model", default="eleven_v4_turbo")
    ap.add_argument("--limit-chars", type=int, default=0, help="для пробного пакета: не больше стольких символов")
    ap.add_argument("--id", default=""); ap.add_argument("--out", default="dist"); ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    voice = a.voice or PAIRS[a.accent][0]
    pid = a.id or (f"{a.kind}_{a.accent}_{voice}" if a.kind == "words" else f"examples_{a.accent}")
    jobs = jobs_for(load(a.items), a.kind, a.accent, voice)
    if a.limit_chars:
        acc, cut = 0, []
        for j in jobs:
            acc += len(j[1])
            if acc > a.limit_chars: break
            cut.append(j)
        jobs = cut
    base = os.path.dirname(os.path.abspath(__file__)) + "/.."
    cache = f"{base}/cache/{a.model}"; os.makedirs(cache, exist_ok=True)
    todo = [j for j in jobs if not os.path.exists(f"{cache}/{j[2]}_{j[0]}.mp3")]
    print(f"{pid}: клипов {len(jobs)}, озвучить {len(todo)} ({sum(len(j[1]) for j in todo)} симв.)")
    with cf.ThreadPoolExecutor(a.threads) as ex:
        list(ex.map(lambda j: tts(j[1], j[2], a.model, f"{cache}/{j[2]}_{j[0]}.mp3"), todo))
    os.makedirs(a.out, exist_ok=True)
    zpath = f"{a.out}/{pid}-v{a.version}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        n = 0
        for k, text, v in jobs:
            src = f"{cache}/{v}_{k}.mp3"
            if not os.path.exists(src): continue
            enc = f"{cache}/{v}_{k}.webm"
            if not os.path.exists(enc):
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ac", "1", "-c:a", "libopus", "-b:a", "20k",
                                "-application", "voip", enc], check=True)
            z.write(enc, f"{k}.webm"); n += 1
        z.writestr("pack.json", json.dumps({"id": pid, "kind": a.kind, "accent": a.accent, "voice": voice,
                                            "version": a.version, "clips": n, "model": a.model}))
    sha = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    entry = {"id": pid, "kind": a.kind, "accent": a.accent, "voice": voice, "version": a.version, "clips": n,
             "size": os.path.getsize(zpath), "sha256": sha, "url": "", "preview": ""}
    print(json.dumps(entry, ensure_ascii=False))

if __name__ == "__main__":
    main()
