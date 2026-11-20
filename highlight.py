"""링크만으로 쇼츠 구간 자동 선택.

재료 두 가지:
  1) 유튜브 자막(수동 > 자동) → 전체 대본 + 시각
  2) heatmap ('가장 많이 다시 본 구간' 그래프) → 사람들이 몰린 지점
판단:
  - ANTHROPIC_API_KEY 있으면 Claude가 대본+그래프를 보고 후보 3개 선정
  - 없으면 heatmap이 가장 높은 45초 구간을 고름
"""
import glob
import json
import os
import time

import config as C
import pipeline as P

MIN_LEN, MAX_LEN, DEFAULT_LEN = 25, 55, 45   # 쇼츠 하나 = 구간 하나


def fmt(t):
    t = round(float(t), 2)
    m, s = int(t // 60), t % 60
    return f"{m}:{s:04.1f}" if t % 1 else f"{m}:{int(s):02d}"


# ── 재료 가져오기 ─────────────────────────────────────────
def video_info(url):
    import yt_dlp
    with yt_dlp.YoutubeDL({**P.YDL_BASE, "skip_download": True}) as ydl:
        return ydl.extract_info(url, download=False)


def captions(url, workdir):
    """yt-dlp로 한국어 자막(json3)을 받아서 [{start, end, text}]로. 없으면 빈 리스트."""
    import yt_dlp
    os.makedirs(workdir, exist_ok=True)
    for f in glob.glob(os.path.join(workdir, "caps*.json3")):
        os.remove(f)
    opts = {**P.YDL_BASE, "skip_download": True, "writesubtitles": True,
            "writeautomaticsub": True, "subtitleslangs": ["ko", "ko-orig"],
            "subtitlesformat": "json3", "outtmpl": os.path.join(workdir, "caps.%(ext)s")}
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            break
        except Exception as e:
            print(f"   자막 받기 재시도 {attempt + 1}/3: {str(e)[:80]}")
            time.sleep(5 * (attempt + 1))
    files = sorted(glob.glob(os.path.join(workdir, "caps*.json3")))
    if not files:
        return []
    data = json.load(open(files[0], encoding="utf-8"))
    out = []
    for ev in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in ev.get("segs", []) or []).replace("\n", " ").strip()
        if not text:
            continue
        s = ev.get("tStartMs", 0) / 1000
        out.append({"start": s, "end": s + ev.get("dDurationMs", 0) / 1000, "text": text})
    return out


def heat_peaks(info, k=5):
    """heatmap에서 높은 지점 k개 (서로 30초 이상 떨어지게)."""
    hm = sorted(info.get("heatmap") or [], key=lambda h: h["value"], reverse=True)
    peaks = []
    for h in hm:
        mid = (h["start_time"] + h["end_time"]) / 2
        if all(abs(mid - p["t"]) > 30 for p in peaks):
            peaks.append({"t": mid, "value": round(h["value"], 2)})
        if len(peaks) >= k:
            break
    return peaks


def comment_peaks(url, k=5):
    """API 키 없이 yt-dlp로 인기 댓글을 받아 '3:42' 같은 타임스탬프를 모음. 좋아요 많을수록 가중치."""
    import re
    import yt_dlp
    opts = {**P.YDL_BASE, "skip_download": True, "getcomments": True,
            "extractor_args": {"youtube": {"max_comments": ["300", "all", "0", "0"],
                                           "comment_sort": ["top"]}}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"   댓글 분석 실패: {str(e)[:80]}")
        return []
    score = {}
    for c in info.get("comments") or []:
        for m in re.findall(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", c.get("text", "")):
            t = int(m[0]) * 60 + int(m[1])
            if t >= (info.get("duration") or 10**9):
                continue
            b = t // 10 * 10                       # 10초 단위로 묶음
            score[b] = score.get(b, 0) + 1 + (c.get("like_count") or 0) ** 0.5
    peaks = []
    for b, v in sorted(score.items(), key=lambda x: -x[1]):
        if all(abs(b - p["t"]) > 30 for p in peaks):
            peaks.append({"t": b + 5, "value": round(v, 1)})
        if len(peaks) >= k:
            break
    return peaks


def best_by_peaks(peaks, caps, length=DEFAULT_LEN):
    """인기 지점(상위 3개)을 펀치라인으로 보고, 각각 그 15초 전부터 시작."""
    if not peaks:
        return None
    return [{"start": max(0, p["t"] - 15), "end": p["t"] - 15 + length,
             "reason": f"댓글이 많이 언급한 지점({fmt(p['t'])})"} for p in peaks[:3]]


def best_by_reactions(caps, length=DEFAULT_LEN):
    """최후 수단: 자막의 웃음/반응 표시와 말 밀도가 높은 구간."""
    if not caps:
        return None
    import re
    react = re.compile(r"\[(웃음|박수|함성|환호)\]|ㅋㅋ|하하|헐|대박|미쳤")
    end_t = caps[-1]["end"]
    best, best_score, t = 0, -1, 0
    while t + length <= end_t:
        win = [c for c in caps if t <= c["start"] < t + length]
        sc = sum(3 if react.search(c["text"]) else 0 for c in win) + len(win) * 0.2
        if sc > best_score:
            best, best_score = t, sc
        t += 5
    return {"start": best, "end": best + length, "reason": "반응·대사 밀도가 높은 구간 (정확도 낮음)"}


# ── 구간 다듬기 ───────────────────────────────────────────
def snap(start, end, caps, duration):
    """자막 문장 경계에 맞춰서 말이 중간에 잘리지 않게.
    시작: 그 시점에 진행 중인 문장의 시작으로 당김 / 끝: 진행 중인 문장이 끝날 때까지 늘리고 +0.5초 여유."""
    if caps:
        before = [c["start"] for c in caps if c["start"] <= start + 0.3]
        if before and start - before[-1] < 5:
            start = before[-1]
        # end 시점에 걸쳐 있거나 바로 뒤(0.8초 내)에 이어지는 문장들까지 포함
        for _ in range(3):
            covering = [c for c in caps if c["start"] < end + 0.8 and c["end"] > end - 0.2]
            if not covering:
                break
            new_end = max(c["end"] for c in covering)
            if new_end - end > 6 or new_end <= end:
                break
            end = new_end
    start = max(0, start - 0.3)
    end = min(duration or end + 1, end + 0.5)
    if end - start > MAX_LEN + 4:
        end = start + MAX_LEN + 4
    return round(start, 2), round(end, 2)


def best_by_heatmap(info, length=DEFAULT_LEN):
    hm = info.get("heatmap") or []
    if not hm:
        return None
    dur = info.get("duration") or hm[-1]["end_time"]
    best, best_score = 0, -1
    t = 0
    while t + length <= dur:
        vals = [h["value"] for h in hm if h["end_time"] > t and h["start_time"] < t + length]
        score = sum(vals) / max(len(vals), 1)
        if score > best_score:
            best, best_score = t, score
        t += 2
    return {"start": best, "end": best + length, "reason": "다시 보기가 가장 많은 구간"}


# ── Claude에게 고르게 하기 ─────────────────────────────────
def _merge_lines(caps, gap=1.2):
    """자동 자막 조각을 문장 단위로 묶어서 토큰 절약 + 읽기 쉽게."""
    out, cur = [], None
    for c in caps:
        if cur and c["start"] - cur["end"] < gap and len(cur["text"]) < 80:
            cur["text"] += " " + c["text"]
            cur["end"] = c["end"]
        else:
            if cur:
                out.append(cur)
            cur = dict(c)
    if cur:
        out.append(cur)
    return out


def ai_candidates(info, caps, peaks):
    lines = _merge_lines(caps)
    script = "\n".join(f"[{fmt(c['start'])}] {c['text']}" for c in lines)
    script = script[:90000]           # 영상 전체 대본을 통째로 읽힘 (30~40분 영상까지)
    peak_txt = ", ".join(f"{fmt(p['t'])}" for p in peaks) or "데이터 없음"
    prompt = f"""너는 구독자 수십만 '예능잼잼' 같은 수능·교육 클립 쇼츠 채널의 메인 PD야.
롱폼 영상 대본 전체를 꼼꼼히 읽고, 쇼츠로 잘랐을 때 "어? 이거 뭔데?" 하고 손가락이 멈추는 장면을 찾아내는 게 네 일이야.

영상 제목: {info.get('title')}
영상 길이: {fmt(info.get('duration') or 0)}
(참고만) 시청자가 많이 다시 본 지점: {peak_txt}
→ 다시 본 지점 = 재밌는 지점이 아니야. 반드시 대본 내용으로 판단해.

[전체 대본]
{script}

━━━ 이런 장면을 찾아 ━━━
① 의외의 사실·반전: "수능 만점자는 수능장을 이렇게 생각함", "같은 문제인데 3번이 5번 되는 시험지"
② 엉뚱한 연결·비유: "무협지 읽던 애들이 수능 국어에서 강한 이유" 처럼 전혀 상관없어 보이는 게 연결되는 순간
③ 말도 안 되는 썰·사건: "수능날 일어난 말도 안 되는 일", 실수담, 억울한 일
④ 수험생 공감 폭발: "수능 볼 때 긴장 안 하면 나중에 후회하는 이유", "엄마한테 말 꺼낼 때"
⑤ 금지·경고·비밀 느낌: "수능 전엔 위험한 노래", "평가원이 쓴 꼼수"
⑥ 웃긴 리액션·티키타카, 펀치라인이 확실한 대화

━━━ 이런 건 피해 ━━━
- 인사, 광고, 채널 소개, 단순 정보 나열, 맥락 없으면 이해 안 되는 대화
- 같은 이야기를 반복하는 밋밋한 구간

━━━ 조건 ━━━
- 후보 5개, 재미있는 순서대로. 서로 다른 장면.
- ★ 첫 문장 우대: 구간의 첫 대사가 '뒷얘기를 들어야 해소되는 문장'(예고·질문·충격 선언)인 구간에 점수를 더 줘.
  좋은 첫 문장 예) "제가 문해력이 좋아진 이유를 말씀드리자면", "이거 절대 하지 마세요", "수능날 무슨 일이 있었냐면"
  나쁜 첫 문장 예) "이번엔 김유연~", "안녕하세요", 맥락 없는 리액션 → 이런 시작은 시작점을 뒤로 옮기거나 후보에서 빼.
- 구간마다 {MIN_LEN}~{MAX_LEN}초. 쇼츠 첫 2초 안에 흥미 포인트(질문·충격 발언·웃음)가 나오게 시작점을 잡아.
- 시작은 문장이 시작될 때, 끝은 펀치라인·리액션이 완전히 끝난 직후.
- 제목: 예능잼잼 스타일 후킹 제목. 2줄 기준 14~24자. 궁금증·공감·의외성을 자극.
  예) "수능만점자는 수능장을 이렇게 생각함ㅋㅋ", "영어가사 섞인 노래 수능 전엔 위험한 이유", "무협지 읽던 애들이 수능 국어에서 강한 이유", "의미 없는 거 아는데 알면서도 못 끊음ㅋㅋ"
  거짓 낚시 금지 (영상에 실제로 있는 내용이어야 함).
- hook: 이 구간에서 시청자가 멈출 포인트 한 줄 / score: 재미 점수 1~10 (냉정하게)

JSON 배열로만 답해:
[{{"start": "3:10", "end": "3:52", "title": "...", "hook": "...", "score": 9}}]"""
    raw = P.ask_claude(prompt, max_tokens=2500)
    if not raw:
        return []
    try:
        items = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
    except Exception:
        return []
    out = []
    for it in items:
        try:
            s, e = P.parse_time(it["start"]), P.parse_time(it["end"])
        except Exception:
            continue
        if e - s < MIN_LEN - 5:
            continue
        sc = it.get("score")
        reason = (f"[{sc}점] " if sc is not None else "") + str(it.get("hook", it.get("reason", "")))
        out.append({"start": s, "end": e, "title": it.get("title"), "reason": reason})
    return out


# ── 진입점 ────────────────────────────────────────────────
def whisper_full(url, workdir):
    """자막이 없을 때 비상 경로: 오디오만 받아서 Whisper로 전체 받아쓰기 (느림)."""
    import yt_dlp
    from faster_whisper import WhisperModel
    audio = os.path.join(workdir, "full_audio.m4a")
    if not os.path.exists(audio):
        print("   유튜브 자막이 없어 오디오를 받아 직접 받아써요 (영상 길이에 따라 몇 분 걸려요)...")
        with yt_dlp.YoutubeDL({**P.YDL_BASE, "format": "ba[ext=m4a]/ba", "outtmpl": audio}) as ydl:
            ydl.download([url])
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(audio, language="ko", vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs]


def find(url):
    print("🔎 영상 정보·자막·인기 구간 분석 중...")
    info = video_info(url)
    dur = info.get("duration") or 0
    work = os.path.join("output", "_analysis", info.get("id", "video"))
    try:
        caps = captions(url, work)
    except Exception as e:
        print(f"   자막을 못 가져옴: {e}")
        caps = []
    if not caps:
        try:
            caps = whisper_full(url, work)
        except Exception as e:
            print(f"   받아쓰기 실패: {e}")
    peaks = heat_peaks(info)
    if not peaks:
        print("   다시보기 그래프가 없어 댓글 타임스탬프를 분석해요...")
        peaks = comment_peaks(url)
    print(f"   자막 {len(caps)}줄 / 인기 지점 {len(peaks)}개"
          + (f" ({', '.join(fmt(p['t']) for p in peaks)})" if peaks else ""))

    cands = []
    if C.ANTHROPIC_API_KEY and caps:
        cands = ai_candidates(info, caps, peaks)
    if not cands:
        if not C.ANTHROPIC_API_KEY:
            print("   (ANTHROPIC_API_KEY가 없어 AI 판단 없이 골라요. 키를 넣으면 훨씬 똑똑해져요)")
        for fn in (lambda: best_by_heatmap(info), lambda: best_by_peaks(peaks, caps),
                   lambda: best_by_reactions(caps)):
            hb = fn()
            if hb:
                cands = hb if isinstance(hb, list) else [hb]
                break
    if not cands:
        raise RuntimeError("자동 선택 실패: 자막과 인기 구간 데이터가 모두 없어요. --start/--end로 직접 지정해 주세요.")

    for c in cands:
        c["start"], c["end"] = snap(c["start"], c["end"], caps, dur)
    print("\n🎬 구간 후보")
    for i, c in enumerate(cands):
        print(f"   [{i}] {fmt(c['start'])}~{fmt(c['end'])} ({c['end'] - c['start']:.0f}초) "
              f"{c.get('title') or ''} — {c.get('reason', '')}")
    return cands, info
