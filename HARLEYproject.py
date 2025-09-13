import os
import re
import sys
import json
import time
import queue
import tempfile
import threading
import datetime
import webbrowser
import subprocess
from dataclasses import dataclass


try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    import vosk
    import sounddevice as sd
except Exception:
    vosk = None
    sd = None

try:
    import speech_recognition as sr
except Exception:
    sr = None

try:
    import wikipedia
    wikipedia.set_lang("ру")
except Exception:
    try:
        import wikipedia
        wikipedia.set_lang("ru")
    except Exception:
        wikipedia = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    import msvcrt
except Exception:
    msvcrt = None


try:
    import edge_tts
except Exception:
    edge_tts = None

try:
    from playsound import playsound
except Exception:
    playsound = None


try:
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
except Exception:
    AudioUtilities = None
    IAudioEndpointVolume = None
    CLSCTX_ALL = None


#  Настройки 
@dataclass
class Settings:
    name: str = "Harley"

    # Активация
    use_wake_word: bool = True
    wake_words: tuple = (
        "харли", "harley", "харлик", "харлей", "харл", "харлюшка", "хрюшка"
    )

    # Речь
    tts_rate: int = 170
    tts_volume: float = 1.0


    tts_backend_preferred: str = "auto"  
    edge_tts_voice: str = "ru-RU-SvetlanaNeural"  

    
    vosk_model_path: str = ""
    sample_rate: int = 16000
    phrase_timeout_sec: float = 8.0
    ambient_adjust_sec: float = 0.6
    print_heard: bool = True

    # GPT
    enable_gpt: bool = True
    gpt_model: str = "gpt-4o-mini"
    gpt_system_prompt: str = (
        "Ты — голосовой ассистент Harley. Отвечай кратко, по делу, дружелюбно, "
        "на русском. Если нужно — шаги и примеры. Не выдумывай факты."
    )
    gpt_max_tokens: int = 800
    gpt_context_turns: int = 10  


    persist_context: bool = True
    context_path: str = os.path.join("data", "context.json")
    chat_log_path: str = os.path.join("data", "chat_history.jsonl")


    push_to_talk: bool = True
    hotkey: str = "x"


SETTINGS = Settings()


# Утилиты 
def ensure_dirs():
    for p in {os.path.dirname(SETTINGS.context_path), os.path.dirname(SETTINGS.chat_log_path)}:
        if p and not os.path.exists(p):
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                pass

ensure_dirs()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip().lower().replace("ё", "е")
    t = re.sub(r"[.,!?:;()\[\]\"'«»\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def contains_wake_word(text: str) -> bool:
    t = normalize_text(text)
    return any((" " + w + " ") in (" " + t + " ") or t == w for w in SETTINGS.wake_words)


def strip_wake_word(text: str) -> str:
    t = normalize_text(text)
    for w in sorted(SETTINGS.wake_words, key=len, reverse=True):
        t = re.sub(rf"(^|\s){re.escape(w)}(\s|$)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_greeting(text: str) -> bool:
    t = normalize_text(text)
    return any(k in t for k in ("привет", "здравств", "приветств", "доброе утро", "добрый день", "добрый вечер", "йоу", "хай", "хэй"))


def safe_open(path_or_cmd: str):
    try:
        subprocess.Popen(path_or_cmd, shell=True)
        return True
    except Exception:
        return False


def guess_vosk_model_path() -> str:
    candidates = [
        os.environ.get("VOSK_MODEL", "").strip(),
        os.path.join("models", "vosk-ru"),
        os.path.join("models", "vosk-model-ru"),
        os.path.join("models", "vosk"),
        "vosk-model-ru",
        "model",
    ]
    for p in candidates:
        if p and os.path.isdir(p):
            if os.path.exists(os.path.join(p, "conf")) or any(fn.endswith(".model") for fn in os.listdir(p)):
                return p
    return ""



def tts_clean(text: str) -> str:
    if not text:
        return ""
    s = text
    s = re.sub(r"`+([^`]+)`+", r"\1", s)
    s = re.sub(r"[*_#>]+", " ", s)
    s = re.sub(r"(?m)^\s*[-*+]\s+", "", s)
    s = re.sub(r"https?://\S+", " ссылка ", s)
    s = re.sub(r"\s-\s", " — ", s)
    s = re.sub(r"[\[\]\{\}\(\)]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Лог чата
class ChatLogger:
    def __init__(self, path: str):
        self.path = path

    def log(self, role: str, content: str):
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "role": role, "content": content}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass


LOGGER = ChatLogger(SETTINGS.chat_log_path)



TTS_VOICE_HINT = "irina"

class TTSEngine:
    def __init__(self):
        self.backend = "none"
        self.spvoice = None
        self.engine = None
        self._vbs_proc = None
        self._lock = threading.Lock()

        self.edge_available = edge_tts is not None and playsound is not None
        self.eleven_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        self.eleven_voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()

        prefer = SETTINGS.tts_backend_preferred.lower()

        if (prefer in ("edge", "auto")) and self.edge_available:
            self.backend = "edge"
            print("TTS: Edge‑TTS активен.")
        elif (prefer == "eleven" or (prefer == "auto" and self.eleven_key and self.eleven_voice)):
            if self.eleven_key and self.eleven_voice:
                self.backend = "eleven"
                print("TTS: ElevenLabs активен.")
            else:
                self._init_win32_chain()
        elif prefer == "win32":
            self._init_win32_chain()
        else:
            self._init_win32_chain()

    def _init_win32_chain(self):
        
        try:
            import win32com.client  
            self.spvoice = win32com.client.Dispatch("SAPI.SpVoice")
            try:
                rate = max(-10, min(10, int((SETTINGS.tts_rate - 170) / 15)))
                self.spvoice.Rate = rate
                self.spvoice.Volume = int(SETTINGS.tts_volume * 100)
            except Exception:
                pass
            self.backend = "win32"
            print("TTS: win32 SAPI активен.")
            return
        except Exception as e:
            self.spvoice = None
            print(f"TTS: win32 недоступен: {e}")

        
        if pyttsx3 is not None:
            try:
                self.engine = pyttsx3.init(driverName='sapi5')
                self.engine.setProperty("rate", SETTINGS.tts_rate)
                self.engine.setProperty("volume", SETTINGS.tts_volume)
                self.backend = "pyttsx3"
                print("TTS: pyttsx3 активен.")
                return
            except Exception as e:
                self.engine = None
                print(f"TTS: pyttsx3 недоступен: {e}")

        print("TTS: будет VBS fallback.")

    def _stop_vbs(self):
        try:
            if self._vbs_proc and self._vbs_proc.poll() is None:
                self._vbs_proc.terminate()
            self._vbs_proc = None
        except Exception:
            pass

    def _speak_vbs_async(self, text: str):
        self._stop_vbs()
        try:
            vbs_text = text.replace('"', '""')
            script = f'Option Explicit\nDim v\nSet v = CreateObject("SAPI.SpVoice")\nv.Speak "{vbs_text}"\n'
            with tempfile.NamedTemporaryFile(delete=False, suffix=".vbs", mode="w", encoding="utf-16le") as f:
                f.write(script)
                path = f.name
            self._vbs_proc = subprocess.Popen(["cscript", "//nologo", path])
            def cleanup(p, pth):
                p.wait()
                try:
                    if os.path.exists(pth):
                        os.remove(pth)
                except Exception:
                    pass
            threading.Thread(target=cleanup, args=(self._vbs_proc, path), daemon=True).start()
        except Exception as e:
            print(f"TTS VBS ошибка: {e}")

    def _edge_say(self, text: str) -> bool:
        if edge_tts is None or playsound is None:
            return False
        try:
            async def synth():
                out = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                out.close()
                communicate = edge_tts.Communicate(text, SETTINGS.edge_tts_voice)
                await communicate.save(out.name)
                return out.name
            import asyncio
            mp3 = asyncio.run(synth())
            threading.Thread(target=lambda: playsound(mp3), daemon=True).start()
            def rm():
                time.sleep(6)
                try:
                    if os.path.exists(mp3):
                        os.remove(mp3)
                except Exception:
                    pass
            threading.Thread(target=rm, daemon=True).start()
            return True
        except Exception as e:
            print(f"Edge‑TTS ошибка: {e}")
            return False

    
    def _eleven_say(self, text: str) -> bool:
        try:
            import requests, winsound
            if not (self.eleven_key and self.eleven_voice):
                return False
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.eleven_voice}"
            headers = {"xi-api-key": self.eleven_key, "accept": "audio/wav", "content-type": "application/json"}
            payload = {"text": text, "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}}
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code != 200:
                print("ElevenLabs HTTP:", r.status_code, r.text[:200])
                return False
            wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            wav.write(r.content); wav.close()
            winsound.PlaySound(wav.name, winsound.SND_FILENAME | winsound.SND_ASYNC)
            def rm():
                time.sleep(6)
                try:
                    if os.path.exists(wav.name):
                        os.remove(wav.name)
                except Exception:
                    pass
            threading.Thread(target=rm, daemon=True).start()
            return True
        except Exception as e:
            print(f"ElevenLabs ошибка: {e}")
            return False

    def say(self, text: str, purge=True):
        spoken = tts_clean(text)
        print(f"{SETTINGS.name}: {spoken}")
        LOGGER.log("assistant", spoken)
        with self._lock:
            if self.backend == "edge":
                ok = self._edge_say(spoken)
                if ok: return
                self._init_win32_chain()
            if self.backend == "eleven":
                ok = self._eleven_say(spoken)
                if ok: return
                self._init_win32_chain()

            try:
                if self.backend == "win32" and self.spvoice:
                    SVSFlagsAsync = 1
                    SVSFPurgeBeforeSpeak = 2
                    if purge:
                        self.spvoice.Speak("", SVSFPurgeBeforeSpeak)
                    self.spvoice.Speak(spoken, SVSFlagsAsync)
                    return
                if self.backend == "pyttsx3" and self.engine:
                    try:
                        if purge:
                            self.engine.stop()
                    except Exception:
                        pass
                    self.engine.say(spoken)
                    threading.Thread(target=self._run_and_wait_safe, daemon=True).start()
                    return
                self._speak_vbs_async(spoken)
            except Exception as e:
                print(f"TTS ошибка: {e}")
                self._speak_vbs_async(spoken)

    def say_append(self, text: str):
        self.say(text, purge=False)

    def _run_and_wait_safe(self):
        try:
            if self.engine:
                self.engine.runAndWait()
        except Exception:
            pass

    def stop(self):
        with self._lock:
            try:
                if self.backend == "win32" and self.spvoice:
                    self.spvoice.Speak("", 2)  # purge
                elif self.backend == "pyttsx3" and self.engine:
                    self.engine.stop()
                else:
                    self._stop_vbs()
            except Exception:
                pass


TTS = TTSEngine()



class STTEngine:
    def __init__(self):
        self.mode = "none"
        self.vosk_model = None
        self._init_engines()

    def _init_engines(self):
        # Vosk офлайн
        if vosk is not None and sd is not None:
            model_path = SETTINGS.vosk_model_path or guess_vosk_model_path()
            if model_path and os.path.isdir(model_path):
                try:
                    self.vosk_model = vosk.Model(model_path)
                    self.mode = "vosk"
                    print("STT: Vosk офлайн")
                    return
                except Exception as e:
                    print(f"STT: Vosk ошибка: {e}")

        if sr is not None:
            try:
                _ = sr.Recognizer()
                self.mode = "sr"
                print("STT: Google (SpeechRecognition)")
                return
            except Exception:
                pass

        self.mode = "text"
        print("STT: текстовый режим")

    def listen_once(self, prompt: str = "") -> str:
        if prompt:
            print(prompt)
        if self.mode == "vosk":
            return self._listen_vosk()
        if self.mode == "sr":
            return self._listen_sr()
        try:
            return input("Вы: ").strip()
        except EOFError:
            time.sleep(0.5)
            return ""

    def _listen_vosk(self) -> str:
        if not (vosk and sd and self.vosk_model):
            return ""
        q = queue.Queue()

        def callback(indata, frames, time_info, status):
            q.put(bytes(indata))

        try:
            rec = vosk.KaldiRecognizer(self.vosk_model, SETTINGS.sample_rate)
        except Exception:
            return ""

        last_ts = time.time()
        partial = ""
        try:
            with sd.RawInputStream(
                samplerate=SETTINGS.sample_rate,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while True:
                    try:
                        data = q.get(timeout=0.5)
                    except queue.Empty:
                        if time.time() - last_ts > SETTINGS.phrase_timeout_sec:
                            break
                        continue
                    last_ts = time.time()
                    if rec.AcceptWaveform(data):
                        result = rec.Result()
                        try:
                            j = json.loads(result)
                            txt = (j.get("text") or "").strip()
                        except Exception:
                            txt = ""
                        if txt:
                            if SETTINGS.print_heard:
                                print(f"Вы: {txt}")
                            return txt
                    else:
                        pj = rec.PartialResult()
                        try:
                            j2 = json.loads(pj)
                            partial = (j2.get("partial") or "").strip()
                        except Exception:
                            partial = ""
                if partial:
                    if SETTINGS.print_heard:
                        print(f"Вы (незавершённо): {partial}")
                    return partial
                return ""
        except Exception:
            return ""

    def _listen_sr(self) -> str:
        if sr is None:
            return ""
        r = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                r.dynamic_energy_threshold = True
                r.pause_threshold = 0.6
                r.energy_threshold = 300
                try:
                    r.adjust_for_ambient_noise(source, duration=SETTINGS.ambient_adjust_sec)
                except Exception:
                    pass
                audio = r.listen(source, phrase_time_limit=SETTINGS.phrase_timeout_sec)
            try:
                txt = r.recognize_google(audio, language="ru-RU")
                txt = (txt or "").strip()
                if txt and SETTINGS.print_heard:
                    print(f"Вы: {txt}")
                return txt
            except sr.UnknownValueError:
                return ""
            except Exception:
                return ""
        except Exception:
            return ""


STT = STTEngine()
print(f"STT режим: {STT.mode}")



class GPTClient:
    def __init__(self):
        self.client = None
        self.model = SETTINGS.gpt_model
        self.system_prompt = SETTINGS.gpt_system_prompt
        self.max_tokens = SETTINGS.gpt_max_tokens
        self.history = []
        if SETTINGS.persist_context:
            self._load_context()

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if SETTINGS.enable_gpt and OpenAI is not None and api_key:
            base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
            try:
                self.client = OpenAI(api_key=api_key, base_url=base_url)
            except Exception:
                self.client = None

    def available(self) -> bool:
        return self.client is not None

    def _save_context(self):
        if not SETTINGS.persist_context:
            return
        try:
            with open(SETTINGS.context_path, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_context(self):
        try:
            if os.path.exists(SETTINGS.context_path):
                with open(SETTINGS.context_path, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
        except Exception:
            self.history = []

    def reset(self):
        self.history = []
        self._save_context()

    def _tail(self):
        return self.history[-(SETTINGS.gpt_context_turns * 2):] if SETTINGS.gpt_context_turns > 0 else []

    def chat_stream(self, user_text: str):
        if not self.available():
            yield ""
            return
        msgs = [{"role": "system", "content": self.system_prompt}] + self._tail() + [{"role": "user", "content": user_text}]
        acc = []
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=msgs,
                temperature=0.5,
                max_tokens=self.max_tokens,
                stream=True,
            )
            for event in stream:
                delta = getattr(getattr(event, "choices", [None])[0], "delta", None)
                chunk = getattr(delta, "content", "") if delta else ""
                if chunk:
                    acc.append(chunk); yield chunk
            answer = "".join(acc).strip()
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": answer})
            if SETTINGS.gpt_context_turns > 0 and len(self.history) > SETTINGS.gpt_context_turns * 4:
                self.history = self.history[-(SETTINGS.gpt_context_turns * 4):]
            self._save_context()
        except Exception:
            return


os.environ["OPENAI_API_KEY"] = "You Token ot Open Ai"
GPT = GPTClient()



def find_existing(paths):
    for p in paths:
        if not p:
            continue
        pp = os.path.expandvars(os.path.expanduser(p))
        if os.path.exists(pp):
            return pp
    return None



KNOWN_APPS = {
    "калькулятор": "calc.exe",
    "проводник": "explorer.exe",
    "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "блокнот": r"C:\Windows\System32\notepad.exe",
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "браузер": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "гугл": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "google": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}

def act_time():
    now = datetime.datetime.now().strftime("%H:%M")
    TTS.say(f"Сейчас {now}.")

def act_date():
    today = datetime.datetime.now().strftime("%d.%m.%Y")
    TTS.say(f"Сегодня {today}.")

def launch_telegram():
    candidates = [
        r"%APPDATA%\Telegram Desktop\Telegram.exe",
        r"%LOCALAPPDATA%\Programs\Telegram Desktop\Telegram.exe",
        r"C:\Program Files\Telegram Desktop\Telegram.exe",
        r"C:\Program Files (x86)\Telegram Desktop\Telegram.exe",
    ]
    exe = find_existing(candidates)
    return safe_open(f'"{exe}"') if exe else safe_open("telegram")

def launch_vscode():
    candidates = [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
    ]
    exe = find_existing(candidates)
    return safe_open(f'"{exe}"') if exe else safe_open("code")

def act_open_app(text: str):
    t = normalize_text(text)

    m_site = re.search(r"(открой|открыть)\s+(сайт|ссылку)\s+(.+)", t)
    if m_site:
        query = m_site.group(3).strip()
        if not query.startswith(("http://", "https://")):
            query = "https://" + query.replace(" ", "")
        webbrowser.open(query); TTS.say("Открываю сайт."); return True

    if "калькулятор" in t:
        ok = safe_open("calc.exe"); TTS.say("Открываю калькулятор." if ok else "Не удалось открыть калькулятор."); return True

    if any(k in t for k in ("телеграм", "telegram", "телега", "тг")):
        ok = launch_telegram(); TTS.say("Открываю Телеграм." if ok else "Не удалось открыть Телеграм."); return True

    if any(k in t for k in ("vs code", "vscode", "visual studio code", "вс код", "код")):
        ok = launch_vscode(); TTS.say("Открываю ВэСи Код." if ok else "Не удалось открыть ВэСи Код."); return True

    if any(k in t for k in ("гугл", "google")):
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        chrome_path = find_existing(chrome_candidates)
        if chrome_path:
            ok = safe_open(f'"{chrome_path}" https://www.google.com')
            TTS.say("Открываю Гугл." if ok else "Не удалось открыть Гугл.")
        else:
            webbrowser.open("https://www.google.com"); TTS.say("Открываю Гугл в браузере.")
        return True

    if "edge" in t:
        ok = safe_open(KNOWN_APPS["edge"]); TTS.say("Открываю Эдж." if ok else "Не удалось открыть Эдж."); return True

    if any(k in t for k in ("chrome", "хром", "браузер")):
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        chrome_path = find_existing(chrome_candidates)
        if chrome_path:
            ok = safe_open(f'"{chrome_path}"'); TTS.say("Открываю Хром." if ok else "Не удалось открыть Хром.")
        else:
            webbrowser.open("about:blank"); TTS.say("Открываю браузер.")
        return True

    for key, path in KNOWN_APPS.items():
        if key in t:
            ok = safe_open(path if path.endswith(".exe") else f'"{path}"')
            TTS.say(f"Открываю {key}." if ok else f"Не удалось открыть {key}.")
            return True

    m_generic = re.search(r"(открой|запусти|запустить)\s+(.+)", t)
    if m_generic:
        term = m_generic.group(2).strip()
        if term:
            webbrowser.open(f"https://duckduckgo.com/?q={term}"); TTS.say(f"Ищу {term} в браузере."); return True
    return False


# Громкость звука
class VolumeController:
    def __init__(self):
        self.volume = None
        if AudioUtilities and IAudioEndpointVolume and CLSCTX_ALL:
            try:
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self.volume = cast(interface, POINTER(IAudioEndpointVolume))
            except Exception:
                self.volume = None

    def available(self):
        return self.volume is not None

    def get_percent(self) -> int:
        if not self.available(): return -1
        try:
            scalar = self.volume.GetMasterVolumeLevelScalar(); return int(round(scalar * 100))
        except Exception:
            return -1

    def set_percent(self, percent: int):
        if not self.available(): return False
        try:
            p = max(0, min(100, int(percent))); self.volume.SetMasterVolumeLevelScalar(p / 100.0, None); return True
        except Exception:
            return False

    def change_percent(self, delta: int):
        cur = self.get_percent(); 
        if cur < 0: return False
        return self.set_percent(cur + delta)

    def mute(self, on: bool):
        if not self.available(): return False
        try:
            self.volume.SetMute(1 if on else 0, None); return True
        except Exception:
            return False

    def is_muted(self) -> bool:
        if not self.available(): return False
        try:
            return bool(self.volume.GetMute())
        except Exception:
            return False

VOLUME = VolumeController()

def act_volume(text: str) -> bool:
    if not VOLUME.available():
        TTS.say("Не могу управлять громкостью. Установите пакет pycaw.")
        return True
    t = normalize_text(text)
    if any(k in t for k in ("выключи звук", "без звука", "замьют", "замуть", "мут")):
        VOLUME.mute(True); TTS.say("Звук выключен."); return True
    if any(k in t for k in ("включи звук", "включи громкость", "размуть", "размьют")):
        VOLUME.mute(False); TTS.say("Звук включён."); return True
    m_less = re.search(r"(сделай|сделать|сделайте)?\s*тише(\s*на\s*(\d+)\s*проц)", t)
    m_more = re.search(r"(сделай|сделать|сделайте)?\s*громче(\s*на\s*(\d+)\s*проц)", t)
    if "тише" in t:
        delta = -int(m_less.group(3)) if m_less and m_less.group(3) else -5
        if VOLUME.change_percent(delta): TTS.say(f"Сделал тише. Громкость {VOLUME.get_percent()} процентов.")
        return True
    if "громче" in t or "добавь громкость" in t or "увеличь громкость" in t:
        delta = int(m_more.group(3)) if m_more and m_more.group(3) else 5
        if VOLUME.change_percent(delta): TTS.say(f"Сделал громче. Громкость {VOLUME.get_percent()} процентов.")
        return True
    m_set = re.search(r"(громк|поставь громк|сделай громк)[^\d]{0,10}(\d{1,3})", t)
    if m_set:
        val = max(0, min(100, int(m_set.group(2))))
        if VOLUME.set_percent(val): TTS.say(f"Поставил громкость {val} процентов.")
        return True
    if t.strip() == "громкость":
        cur = VOLUME.get_percent()
        if cur >= 0: TTS.say(f"Текущая громкость {cur} процентов.")
        return True
    return False



def act_search(text: str):
    t = normalize_text(text)
    m = re.search(r"(найди|поиск|поищи)\s+(.+)", t)
    if not m:
        if "найди" in t: term = t.split("найди", 1)[-1].strip()
        else: return False
    else:
        term = m.group(2).strip()
    if not term: TTS.say("Что искать?"); return True
    webbrowser.open(f"https://duckduckgo.com/?q={term}")
    TTS.say(f"Ищу {term}."); return True

JOKES = [
    "Программист зашёл в бар, заказал один кофе, потом десять кофе. Бармен спросил: вам десять кофе? Программист ответил: нет, одиннадцать.",
    "Почему программисты путают Хэллоуин и Рождество? Потому что 31 окт = 25 дек.",
    "Если не работает — выключи и включи. Если заработало — не трогай.",
]

def act_joke():
    import random
    TTS.say(random.choice(JOKES))

def act_reminder(text: str):
    t = normalize_text(text)
    m = re.search(r"напомни\s+через\s+(\d+)\s+(секунд|секунды|секунда|минут|минуты|минута|час|часа|часов)\s*(.*)", t)
    if not m: return False
    num = int(m.group(1)); unit = m.group(2); msg = m.group(3).strip() or "сделать дело"
    seconds = num * (60 if unit.startswith("минут") else 3600 if unit.startswith("час") else 1)
    def remind(): TTS.say(f"Напоминаю: {msg}.")
    timer = threading.Timer(seconds, remind); timer.daemon = True; timer.start()
    TTS.say(f"Окей, напомню через {num} {unit}."); return True

def act_wikipedia_short(text: str):
    if wikipedia is None: return False
    t = normalize_text(text)
    t = re.sub(r"\b(кто такой|кто такая|что такое|расскажи про|что это|объясни|ответь|переведи|суммируй)\b", "", t).strip()
    if not t or len(t) < 3: return False
    try:
        summary = wikipedia.summary(t, sentences=2)
        if summary: TTS.say(summary); return True
    except Exception:
        pass
    return False

def act_gpt_answer(text: str) -> bool:
    if not (SETTINGS.enable_gpt and GPT.available()): return False
    user = normalize_text(text)
    user = re.sub(r"^(объясни(те)?|ответь|расскажи(те)?|переведи(те)?|суммируй(те)?|гпт)\s*", "", user).strip() or text
    LOGGER.log("user", user)
    buf = ""
    for chunk in GPT.chat_stream(user):
        if not chunk: continue
        buf += chunk
        while True:
            m = re.search(r"(.+?[.!?…])(\s|$)", buf, flags=re.S)
            if not m: break
            phrase = m.group(1).strip()
            if phrase: TTS.say_append(phrase)
            buf = buf[m.end(1):].lstrip()
        if len(buf) > 200:  # длинный хвост — порционно
            TTS.say_append(buf[:180]); buf = buf[180:]
    rest = buf.strip()
    if rest: TTS.say_append(rest)
    return True



def route_command(text: str) -> bool:
    t = normalize_text(text)
    if t:
        LOGGER.log("user", t)


    if "замолчи" in t and not any(k in t for k in ("сделай", "громче", "тише")):
        TTS.stop(); return True


    if any(k in t for k in ("громк", "тише", "громче", "звук", "без звука", "включи звук", "выключи звук", "убавь громкость", "добавь громкость", "увеличь громкость")):
        if act_volume(t): return True


    if any(k in t for k in ("сбрось контекст", "забудь все", "забудь контекст")):
        if GPT and GPT.available(): GPT.reset(); TTS.say("Контекст диалога очищен."); return True


    if any(w in t for w in ("стоп", "выход", "пока", "хватит")):
        TTS.say("Выключаюсь. До связи!"); sys.exit(0)


    if "время" in t: act_time(); return True
    if "дата" in t or "число" in t: act_date(); return True


    if "напомни" in t:
        if act_reminder(t): return True


    if any(k in t for k in ("открой", "запусти", "запустить")) or any(k in t for k in ("калькулятор","телеграм","telegram","тг","телега","vscode","vs code","visual studio code","вс код","код","chrome","хром","браузер","google","гугл","edge")):
        if act_open_app(t): return True


    if any(k in t for k in ("найди", "поиск", "поищи")):
        if act_search(t): return True


    if "шутк" in t: act_joke(); return True


    if any(t.startswith(k) for k in ("объясни","ответь","расскажи","переведи","суммируй","гпт")):
        if act_gpt_answer(text): return True
        TTS.say("GPT недоступен."); return True


    if SETTINGS.enable_gpt and GPT.available():
        if act_gpt_answer(text): return True
    if act_wikipedia_short(text): return True

    if t:
        webbrowser.open(f"https://duckduckgo.com/?q={t}"); TTS.say("Не уверен, но вот что нашёл в сети."); return True
    return False



def greet_once():
    if SETTINGS.push_to_talk:
        TTS.say(f"Готов к работе. Нажми клавишу {SETTINGS.hotkey.upper()} и говори.")
    else:
        if SETTINGS.enable_gpt and GPT.available():
            TTS.say("Готов к работе. Скажи: 'Харли, ...'.")
        else:
            TTS.say("Готов к работе без GPT. Скажи: 'Харли, ...'.")

def wait_for_hotkey() -> bool:
    if not (SETTINGS.push_to_talk and msvcrt):
        return True
    print(f"Нажми '{SETTINGS.hotkey.upper()}' и говори...")
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            try: c = ch.decode("cp1251", errors="ignore").lower()
            except Exception: c = ""
            if c == SETTINGS.hotkey.lower():
                return True
        time.sleep(0.03)

def main_loop():
    greet_once()
    greeted_once = False
    while True:
        if SETTINGS.push_to_talk:
            if not wait_for_hotkey(): continue
            phrase = STT.listen_once("Говорите...")
        else:
            if STT.mode == "text":
                phrase = STT.listen_once("Введите команду (или пусто, чтобы повторить):")
            else:
                phrase = STT.listen_once("Скажи: 'Харли, ...' и команду.")
                if not phrase: continue
                if SETTINGS.use_wake_word:
                    if contains_wake_word(phrase):
                        rest = strip_wake_word(phrase)
                        if not greeted_once and (not rest or is_greeting(rest)):
                            TTS.say("Здравствуй, мой господин!")
                            greeted_once = True
                            phrase = STT.listen_once("Слушаю команду...")
                        else:
                            phrase = rest if rest else STT.listen_once("Слушаю команду...")
                    else:
                        continue

        if not phrase:
            continue

        try:
            handled = route_command(phrase)
        except SystemExit:
            raise
        except Exception as e:
            print(f"Ошибка при обработке команды: {e}")
            handled = False

        if not handled:
            TTS.say("Не понял запрос, повторите пожалуйста.")


if __name__ == "__main__":
    try:
        print(f"STT режим: {STT.mode}")
        main_loop()
    except KeyboardInterrupt:
        print("\nЗавершение по Ctrl+C")

