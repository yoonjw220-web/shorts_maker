"""예능 효과음.

- sfx/ 폴더의 소리를 씀. 처음 실행하면 기본 효과음 8개를 직접 합성해서 만들어 둠 (저작권 걱정 없음).
- 파일 이름 규칙: "<효과음 이름>_<아무거나>.wav/.mp3"  예) 쿵_기본.wav, 쿵_내꺼.mp3
  같은 이름의 '내 파일'이 있으면 기본 소리 대신 그걸 씀. 새 이름(예: 박수_1.mp3)을 넣으면 그 효과음도 생김.
"""
import glob
import math
import os
import random
import wave

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
SFX_DIR = os.path.join(BASE, "sfx")
SR = 44100

# 이름: (영문 별칭, 언제 쓰는지)
DESC = {
    "휙": ("whoosh", "장면 전환, 고개 확 돌림, 갑자기 끼어드는 말"),
    "뽁": ("pop", "괄호 리액션 자막 (당황)(억울), 귀여운 포인트, 가벼운 강조"),
    "띵": ("ding", "깨달음, 정답, 핵심 한마디, 공감 포인트"),
    "띠용": ("boing", "황당, 어이없음, 예상 못한 말, 당황"),
    "쿵": ("impact", "충격 발언, 팩폭, 무거운 한마디"),
    "두둥": ("dundun", "폭로, 긴장, 진지해지는 순간, 반전 직전"),
    "반짝": ("twinkle", "여유, 뿌듯, 잘난 척, 기분 좋은 순간"),
    "띠로리": ("fail", "실패, 민망, 망함, 허무"),
}
ALIASES = {v[0]: k for k, v in DESC.items()}


# ── 합성 도구 ──────────────────────────────────────────
def _t(sec):
    return np.arange(int(SR * sec)) / SR


def _sweep(f0, f1, sec, curve="exp"):
    t = _t(sec)
    f = f0 * (f1 / f0) ** (t / sec) if curve == "exp" else f0 + (f1 - f0) * t / sec
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _bell(freq, sec, decay=4.0, partials=((1, 1.0), (2.0, 0.35), (3.01, 0.15), (5.4, 0.06))):
    t = _t(sec)
    y = sum(a * np.sin(2 * np.pi * freq * m * t) * np.exp(-decay * m ** 0.5 * t) for m, a in partials)
    return y * np.minimum(1, t / 0.003)


def _lowpass(x, cutoff):
    a = math.exp(-2 * math.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def _place(dst, src, at):
    i = int(at * SR)
    n = min(len(src), len(dst) - i)
    dst[i:i + n] += src[:n]


def _finish(y, attack=0.003, release=0.035):
    """DC 제거와 짧은 페이드로 클릭 방지. 합성 함수도 동일하게 마감."""
    y = np.asarray(y, dtype=np.float64).copy()
    y -= np.mean(y)
    na, nr = min(len(y), int(SR * attack)), min(len(y), int(SR * release))
    if na:
        y[:na] *= np.sin(np.linspace(0, np.pi / 2, na)) ** 2
    if nr:
        y[-nr:] *= np.sin(np.linspace(np.pi / 2, 0, nr)) ** 2
    return y


def _save(y, path, peak=0.7):
    y = _finish(y)
    if not len(y) or not np.isfinite(y).all():
        raise ValueError("효과음 데이터가 비어 있거나 잘못되었습니다")
    # 피크만 키워 특정 효과음이 지나치게 커지지 않게 평균 에너지도 제한.
    gain = min(peak / max(np.max(np.abs(y)), 1e-9),
               0.18 / max(np.sqrt(np.mean(y * y)), 1e-9))
    pcm = np.rint(np.clip(y * gain, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _echo(y, delay=.065, gain=.18, repeats=2):
    out = y.copy()
    for k in range(1, repeats + 1):
        _place(out, y * gain ** k, delay * k)
    return out


def _tone(freq, sec, decay=5, bright=.3):
    t = _t(sec)
    phase = 2 * np.pi * freq * t
    y = (np.sin(phase) + bright * np.sin(2 * phase) * np.exp(-t * 8)
         + bright * .3 * np.sin(3 * phase) * np.exp(-t * 12))
    return _finish(y * np.exp(-decay * t))


# ── 기본 효과음 8종 · 두 번째 합성 버전 ──────────────────

def _whoosh():
    """공기가 스쳐 지나가는 짧은 전환음. 중간이 밝아지고 끝은 가볍게 내려감."""
    sec = .42; t = _t(sec); pos = t / sec
    noise = np.random.default_rng(101).standard_normal(len(t))
    low = _lowpass(noise, 450)
    air = _lowpass(noise, 6400) - _lowpass(noise, 1100)
    env = np.sin(np.pi * pos) ** 2.4
    sweep = _sweep(1050, 190, sec) * np.exp(-((pos - .66) / .23) ** 2)
    return _finish((air * (0.35 + 1.6 * pos) + low * .5 + sweep * .08) * env)


def _pop():
    """물방울처럼 짧고 둥근 뽁. 날카로운 클릭 대신 작은 공명 두 개."""
    sec = .19; t = _t(sec)
    body = _sweep(820, 250, sec) * np.exp(-t * 32)
    ring = _sweep(1650, 600, sec) * np.exp(-t * 48)
    return _finish(body + .22 * ring, attack=.0015, release=.04)


def _ding():
    """맑은 정답 벨. 강한 고역 대신 짧은 어택과 부드러운 잔향."""
    sec = .96; t = _t(sec)
    y = _bell(1174.66, sec, 5.2, ((1, 1), (2.76, .20), (4.07, .06)))
    _place(y, _bell(1760, .75, 6.3) * .20, .018)
    return _finish(_echo(y, .075, .16))


def _boing():
    """위로 튀었다 내려오며 떨리는 스프링. 황당한 장면용."""
    sec = .72; t = _t(sec)
    f = 260 + 480 * np.exp(-t * 6) + 150 * np.sin(2 * np.pi * 8.5 * t) * np.exp(-t * 4)
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.exp(-t * 4.9) * (.8 + .2 * np.cos(2 * np.pi * 8.5 * t))
    y = (np.sin(ph) + .24 * np.sin(ph * 2) + .07 * np.sin(ph * 3)) * env
    return _finish(_echo(y, .09, .14))


def _impact():
    """둔탁한 타격 + 짧은 공기층. 폰에서도 들리는 중저역 공명 포함."""
    sec = .64; t = _t(sec)
    body = _sweep(155, 58, sec) * np.exp(-t * 9)
    knock = np.sin(2 * np.pi * 195 * t) * np.exp(-t * 19)
    noise = np.random.default_rng(102).standard_normal(len(t))
    crack = (_lowpass(noise, 2800) - _lowpass(noise, 300)) * np.exp(-t * 55)
    tail = _lowpass(noise, 750) * np.exp(-t * 14)
    return _finish(np.tanh(1.4 * (body + .45 * knock + .5 * crack + .2 * tail)), attack=.002)


def _dundun():
    """작은 두 → 큰 둥. 두 번째 타격에 긴장감을 주는 낮은 코드."""
    sec = 1.08; y = np.zeros(len(_t(sec)))
    _place(y, _impact() * .58, 0)
    _place(y, _impact(), .27)
    t = _t(.8)
    chord = sum(np.sin(2 * np.pi * f * t) * a for f, a in ((98, .45), (146.83, .22), (207.65, .13)))
    chord *= np.exp(-t * 6) * np.minimum(1, t / .012)
    _place(y, chord, .28)
    return _finish(_echo(y, .085, .12))


def _twinkle():
    """위로 올라가는 유리빛 아르페지오. 짧은 잔향으로 반짝이는 꼬리."""
    y = np.zeros(len(_t(1.02)))
    for k, f in enumerate((1318.51, 1760, 2093, 2637.02)):
        note = _bell(f, .72, decay=8.0, partials=((1, 1), (2, .12), (3.01, .035)))
        _place(y, note * (.9 - .12 * k), k * .065)
    return _finish(_echo(y, .11, .24, repeats=3))


def _fail():
    """세 음 뒤 마지막 음이 미끄러지는 코믹한 금관 하강음."""
    y = np.zeros(len(_t(1.32)))
    at = 0.0
    for i, (f, dur) in enumerate(((392, .19), (349.23, .19), (311.13, .19), (261.63, .69))):
        t = _t(dur)
        freq = f * (1 - .17 * (t / dur) ** 1.7 if i == 3 else np.ones_like(t))
        freq *= 1 + .009 * np.sin(2 * np.pi * 5.5 * t)
        ph = 2 * np.pi * np.cumsum(freq) / SR
        tone = sum(np.sin(k * ph) * a for k, a in ((1, 1), (2, .42), (3, .20), (4, .08)))
        env = np.minimum(1, t / .018) * np.exp(-t * (1.8 if i == 3 else 1))
        note = _finish(_lowpass(tone * env, 1900), attack=.01, release=.055)
        _place(y, note * (.8 if i < 3 else 1), at)
        at += dur + .012
    return _finish(y)


MAKERS = {"휙": _whoosh, "뽁": _pop, "띵": _ding, "띠용": _boing, "쿵": _impact,
          "두둥": _dundun, "반짝": _twinkle, "띠로리": _fail}


def ensure_library():
    os.makedirs(SFX_DIR, exist_ok=True)
    made = 0
    for name, fn in MAKERS.items():
        p = os.path.join(SFX_DIR, f"{name}_기본.wav")
        if not os.path.exists(p):
            _save(fn(), p)
            made += 1
    if made:
        print(f"   🔊 기본 효과음 {made}개를 만들었어요 (sfx 폴더)")


def library():
    """{이름: [파일들]} — 내 파일이 있으면 기본 파일보다 우선."""
    ensure_library()
    lib = {}
    for p in sorted(glob.glob(os.path.join(SFX_DIR, "*"))):
        if not p.lower().endswith((".wav", ".mp3", ".m4a", ".ogg")):
            continue
        name = os.path.basename(p).split("_")[0].split(".")[0].strip()
        if name:
            lib.setdefault(name, []).append(p)
    for name, files in lib.items():
        mine = [f for f in files if "_기본" not in os.path.basename(f)]
        lib[name] = mine or files
    return lib


def normalize_name(name):
    name = (name or "").strip().strip("()[]")
    return ALIASES.get(name.lower(), name)


def pick_file(name, lib=None):
    lib = lib or library()
    files = lib.get(normalize_name(name))
    return random.choice(files) if files else None


def describe_for_ai(lib=None):
    lib = lib or library()
    lines = []
    for name in lib:
        why = DESC.get(name, ("", "(내가 추가한 효과음 — 이름 뜻에 맞게 사용)"))[1]
        lines.append(f'- "{name}": {why}')
    return "\n".join(lines)
