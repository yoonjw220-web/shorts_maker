"""각 단계 함수: 다운로드, 자막, 댓글, AI 선택, 합성."""
import json
import os
import re
import subprocess

import config as C
import render


# yt-dlp 공통 옵션: JS 해독 스크립트 자동 받기(Deno 필요) + 조용히
YDL_BASE = {"quiet": True, "no_warnings": False, "remote_components": ["ejs:github"], "noplaylist": True}


# ① 다운로드 ─────────────────────────────────────────────
def video_id_from_url(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]{11})", url)
    return m.group(1) if m else None


def parse_time(t):
    """'1:23' / '01:02:03' / '83.5' → 초"""
    parts = [float(p) for p in str(t).split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def download_clip(url, start, end, out_path):
    import yt_dlp
    from yt_dlp.utils import download_range_func
    opts = {
        **YDL_BASE,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": out_path,
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "overwrites": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return {"title": info.get("title", ""), "channel": info.get("channel", "")}


def download_source(url, out_path):
    """영상 전체를 한 번만 받아둠 (yt-dlp 자체 다운로더 사용 → 403 차단에 강함)."""
    if os.path.exists(out_path):
        return {}
    import yt_dlp
    opts = {**YDL_BASE,
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4", "outtmpl": out_path, "overwrites": True,
            "retries": 10, "fragment_retries": 10}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return {"title": info.get("title", ""), "channel": info.get("channel", "")}


def concat_clips(paths, out_path):
    """여러 클립을 하나로 이어 붙임 (해상도·프레임 통일)."""
    if len(paths) == 1:
        import shutil
        shutil.copy(paths[0], out_path)
        return
    args, parts = [], []
    for i, pth in enumerate(paths):
        args += ["-i", pth]
        parts.append(f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
                     f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[v{i}];"
                     f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    joined = "".join(f"[v{i}][a{i}]" for i in range(len(paths)))
    fc = ";".join(parts) + f";{joined}concat=n={len(paths)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", fc,
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "18", "-c:a", "aac", out_path], check=True)


def to_montage_time(t, segments):
    """원본 영상의 t초가 이어 붙인 쇼츠에서 몇 초인지. 구간 밖이면 None."""
    offset = 0.0
    for s0, e0 in segments:
        if s0 - 5 <= t <= e0 + 5:
            return offset + min(max(t - s0, 0), e0 - s0)
        offset += e0 - s0
    return None


def cut_local(src, start, end, out_path):
    # -ss를 -i 앞에 두면 긴 영상에서도 빠르게 찾아감 (재인코딩이라 정확도 유지)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", src,
                    "-t", f"{end - start:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", out_path], check=True)


# ② 음성 → 자막 ──────────────────────────────────────────
def trim_silence(clip):
    """말이 없는 긴 구간을 자동으로 잘라냄 (늘어지는 느낌 제거). clip을 제자리에서 교체."""
    if not getattr(C, "CUT_SILENCE", True):
        return
    db = getattr(C, "SILENCE_DB", -35)
    min_s = getattr(C, "SILENCE_MIN", 0.8)
    pad = getattr(C, "SILENCE_PAD", 0.25)
    r = subprocess.run(["ffmpeg", "-i", clip, "-af", f"silencedetect=noise={db}dB:d={min_s}",
                        "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    dur = clip_duration(clip)
    sil = []
    for i, st in enumerate(starts):
        en = ends[i] if i < len(ends) else dur
        st, en = st + pad, en - pad            # 말 앞뒤 여유는 남김
        if en - st > 0.2:
            sil.append((max(st, 0), min(en, dur)))
    if not sil:
        return
    keep, t = [], 0.0
    for st, en in sil:
        if st - t > 0.15:
            keep.append((t, st))
        t = en
    if dur - t > 0.15:
        keep.append((t, dur))
    cut = dur - sum(e - s for s, e in keep)
    if cut < 0.4 or not keep:
        return
    fg = []
    for i, (s0, e0) in enumerate(keep):
        fg.append(f"[0:v]trim={s0:.3f}:{e0:.3f},setpts=PTS-STARTPTS[v{i}];"
                  f"[0:a]atrim={s0:.3f}:{e0:.3f},asetpts=PTS-STARTPTS[a{i}]")
    fg.append("".join(f"[v{i}][a{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[v][a]")
    tmp = clip + ".cut.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip, "-filter_complex", ";".join(fg),
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", tmp], check=True)
    os.replace(tmp, clip)
    print(f"   ⏩ 무음 {cut:.1f}초 잘라냄 ({len(sil)}곳) → {clip_duration(clip):.1f}초")


def transcribe_words(video_path, hint=""):
    """faster-whisper로 단어 단위 타임스탬프 [(시작, 끝, 단어), ...]."""
    from faster_whisper import WhisperModel
    device = C.WHISPER_DEVICE
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    model = WhisperModel(C.WHISPER_MODEL, device=device,
                         compute_type="float16" if device == "cuda" else "int8")
    segments, _ = model.transcribe(video_path, language="ko", word_timestamps=True,
                                   vad_filter=True, initial_prompt=hint or None)
    words = [w for s in segments for w in (s.words or [])]
    return [(round(w.start, 2), round(w.end, 2), w.word.strip()) for w in words if w.word.strip()]


def transcribe(video_path, hint=""):
    """(예전 방식) 받아쓰기 → 글자 폭 기준으로 기계적으로 묶기."""
    return chunk_words(transcribe_words(video_path, hint))


def _fits(text, margin=0.95):
    """자막 글씨 크기 기준으로 한 줄(화면 폭)에 들어가는지 픽셀 단위로 검사."""
    from PIL import Image, ImageDraw
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w = tmp.textlength(text, font=render._sub_font(getattr(C, "SUB_SIZE", 82)))
    return w <= render.subtitle_max_width() * margin


def chunk_words(words, max_chars=None, gap=0.45):
    """단어들을 자막 덩어리로 묶음. 글자 수가 아니라 실제 글씨 폭으로 '한 줄에 들어가는지' 판단."""
    chunks, cur = [], []
    for (s, e, t) in words:
        if not t:
            continue
        text_now = " ".join(x[2] for x in cur)
        too_long = bool(cur) and not _fits(text_now + " " + t)
        paused = cur and s - cur[-1][1] > gap
        if cur and (too_long or paused):
            chunks.append({"start": cur[0][0], "end": cur[-1][1], "text": text_now})
            cur = []
        cur.append((s, e, t))
    if cur:
        chunks.append({"start": cur[0][0], "end": cur[-1][1], "text": " ".join(x[2] for x in cur)})
    for a, b in zip(chunks, chunks[1:]):
        a["end"] = b["start"]
    return chunks


def split_long_subs(subs):
    """교정·수정 후에도 한 줄을 넘는 자막은 띄어쓰기 기준으로 자동으로 쪼갬 (시간은 글자 수 비율로 나눔)."""
    out, n_split = [], 0
    for x in subs:
        text = x.get("text", "")
        if not text or _fits(text):
            out.append(x)
            continue
        words, pieces, cur = text.split(), [], ""
        for w in words:
            cand = (cur + " " + w).strip()
            if cur and not _fits(cand):
                pieces.append(cur)
                cur = w
            else:
                cur = cand
        if cur:
            pieces.append(cur)
        total = sum(len(p) for p in pieces) or 1
        t = x["start"]
        dur = max(x["end"] - x["start"], 0.1)
        for k, p in enumerate(pieces):
            d = dur * len(p) / total
            piece = {**x, "start": round(t, 2), "end": round(t + d, 2), "text": p}
            if k and "sfx" in piece:
                piece["sfx"] = ""          # 효과음은 첫 조각에서 한 번만
            out.append(piece)
            t += d
        n_split += 1
    if n_split:
        print(f"   ✂️  긴 자막 {n_split}개를 한 줄 크기로 자동 분할")
    return out


def fix_short_subs(subs, min_dur=None, merge=True):
    """너무 짧게 스쳐 지나가는 자막 방지.
    ① 옆 자막과 합쳐도 한 줄에 들어가면 합침
    ② 아니면 옆 자막 시간을 조금 빌려와서 최소 시간 확보 (다음 자막 → 이전 자막 순)."""
    min_dur = min_dur or getattr(C, "SUB_MIN_DUR", 0.7)
    subs = [dict(x) for x in subs]
    dur = lambda x: x["end"] - x["start"]
    n_merge = n_stretch = 0
    i = 0
    while i < len(subs):
        x = subs[i]
        if dur(x) >= min_dur or len(subs) == 1:
            i += 1
            continue
        prev = subs[i - 1] if i > 0 else None
        nxt = subs[i + 1] if i + 1 < len(subs) else None
        # ① 합치기 (더 짧은 쪽 이웃 먼저)
        opts = [o for o in (prev, nxt) if o is not None]
        opts.sort(key=dur)
        merged = False
        for o in (opts if merge else []):
            a, b = (o, x) if o is prev else (x, o)
            text = f"{a['text']} {b['text']}".strip()
            if _fits(text):
                a.update(start=min(a["start"], b["start"]), end=max(a["end"], b["end"]), text=text,
                         context=a.get("context") or b.get("context", ""))
                if not a["context"]:
                    a.pop("context")
                subs.remove(b)
                merged, n_merge = True, n_merge + 1
                i = max(subs.index(a), 0)
                break
        if merged:
            continue
        # ② 시간 빌려오기
        need = min_dur - dur(x)
        if nxt and dur(nxt) - need >= min_dur:
            x["end"] = round(x["end"] + need, 2); nxt["start"] = x["end"]
        elif prev and dur(prev) - need >= min_dur:
            x["start"] = round(x["start"] - need, 2); prev["end"] = x["start"]
        else:  # 양쪽에서 나눠 빌리기
            give_n = max(0, dur(nxt) - min_dur) if nxt else 0
            give_n = min(give_n, need)
            if nxt:
                x["end"] = round(x["end"] + give_n, 2); nxt["start"] = x["end"]
            rest = need - give_n
            if prev and rest > 0:
                give_p = min(rest, max(0, dur(prev) - min_dur))
                x["start"] = round(x["start"] - give_p, 2); prev["end"] = x["start"]
        n_stretch += 1
        i += 1
    if n_merge or n_stretch:
        print(f"   ⏱️  너무 짧은 자막 정리: 합침 {n_merge}개, 시간 늘림 {n_stretch}개 (최소 {min_dur}초)")
    return subs


# ③ 댓글 ────────────────────────────────────────────────
BAD = re.compile(r"https?://|\d{1,2}:\d{2}|@\w")


def fetch_comments_ytdlp(video_id, n=100):
    """API 키 없이 yt-dlp로 인기 댓글 가져오기 (조금 느림)."""
    import yt_dlp
    opts = {**YDL_BASE, "skip_download": True, "getcomments": True,
            "extractor_args": {"youtube": {"max_comments": [str(n), "all", "0", "0"],
                                           "comment_sort": ["top"]}}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    return [{"text": (c.get("text") or "").strip(), "likes": c.get("like_count") or 0,
             "author": c.get("author") or "", "avatar": c.get("author_thumbnail") or ""}
            for c in info.get("comments") or [] if c.get("parent", "root") == "root"]


def fetch_comments(video_id, n=100):
    if not C.YOUTUBE_API_KEY:
        print("   (API 키가 없어 yt-dlp로 댓글을 읽어요...)")
        return fetch_comments_ytdlp(video_id, n)
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", developerKey=C.YOUTUBE_API_KEY)
    res = yt.commentThreads().list(part="snippet", videoId=video_id, order="relevance",
                                   maxResults=min(n, 100), textFormat="plainText").execute()
    out = []
    for item in res.get("items", []):
        c = item["snippet"]["topLevelComment"]["snippet"]
        out.append({"text": c["textDisplay"].strip(), "likes": c.get("likeCount", 0),
                    "author": c.get("authorDisplayName", ""), "avatar": c.get("authorProfileImageUrl", "")})
        # 닉네임·프사는 카드에서 흐리게 처리됨 (파일로 저장하지 않음)
    return out


TS = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
URL_AT = re.compile(r"https?://|@\w")


def candidate_comments(comments, k=50, clip_range=None, used=(), segments=None):
    """좋은 댓글 후보.
    1순위: 클립 구간(clip_range=(시작초, 끝초))을 타임스탬프로 언급한 댓글 → 장면 맞춤 반응
    2순위: 나머지 인기 댓글
    이미 이 영상 쇼츠에 쓴 댓글(used)은 제외. 화면용 텍스트에서는 타임스탬프를 지움."""
    scene, general = [], []
    for c in comments:
        raw = c["text"]
        if URL_AT.search(raw) or raw in used:
            continue
        stamps = [int(m[0]) * 60 + int(m[1]) for m in TS.findall(raw)]
        clean = TS.sub("", raw).strip(" ~-:,.\n")
        if not (4 <= len(clean) <= 80):
            continue
        item = {**c, "text": clean, "raw": raw}
        ranges = segments or ([clip_range] if clip_range else [])
        if stamps and any(r[0] - 5 <= t <= r[1] + 5 for t in stamps for r in ranges):
            item["scene"] = True
            scene.append(item)
        elif not stamps and c.get("likes", 0) >= 5:
            general.append(item)
    by_likes = lambda x: x["likes"]
    return (sorted(scene, key=by_likes, reverse=True) + sorted(general, key=by_likes, reverse=True))[:k]


KEY_STOP = {"수능", "공부", "시험", "진짜", "너무", "이거", "그냥", "완전", "근데", "여기", "저거", "이게", "ㅋㅋ", "ㅋㅋㅋ", "미미누",
            "영상", "사람", "우리", "나도", "저도", "이건", "제일", "정말", "아니", "그리고", "ㅠㅠ"}
PARTICLE = re.compile(r"(으로|에서|에게|까지|부터|처럼|보다|이랑|하고|은|는|이|가|을|를|에|의|도|만|로|와|과|요)$")


def _keywords(text):
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
    keys = set()
    for w in words:
        w2 = PARTICLE.sub("", w)
        for k in {w2, w2[:2]} if len(w2) >= 3 else {w2}:   # '버튼이었으면' → '버튼'도 키워드로
            if len(k) >= 2 and k not in KEY_STOP and not re.fullmatch(r"ㅋ+|ㅎ+|\d+", k):
                keys.add(k)
    return keys


def limit_keyword_dupes(comments, max_per=3):
    """같은 핵심 단어(예: '버튼')가 들어간 댓글은 최대 max_per개까지만.
    '버튼이었으면', '버튼좀'처럼 단어가 붙어 있어도 글자 포함 여부로 같이 셈."""
    accepted, out = [], []
    key_pool = set()
    for c in comments:
        text = re.sub(r"\s+", "", c["text"])
        mine = _keywords(c["text"])
        # 지금까지 나온 키워드 + 내 키워드 중, 이 댓글에 들어 있는 것들
        hits = {k for k in key_pool | mine if k in text}
        if any(sum(k in t for t in accepted) >= max_per for k in hits):
            continue
        accepted.append(text)
        key_pool |= mine
        out.append(c)
    return out


def schedule_comments(chosen, segments, slot=6.0, max_n=10):
    """댓글을 쇼츠 시간축에 배치.
    우선순위: AI가 정한 표시 시각(at) > 타임스탬프로 언급한 장면 > 나머지는 빈 시간대.
    각 댓글은 다음 댓글이 나올 때까지 유지 (틈 없음)."""
    dur = max(sum(e - s for s, e in segments), 1)
    n = max(1, min(max_n, round(dur / slot), len(chosen)))
    chosen = chosen[:n]
    timed, rest = [], []
    for c in chosen:
        t = c.get("at")
        if t is None:
            stamps = [int(m[0]) * 60 + int(m[1]) for m in TS.findall(c.get("raw", ""))]
            mt = [x for x in (to_montage_time(st, segments) for st in stamps) if x is not None]
            t = mt[0] if mt else None
        if t is not None:
            timed.append((max(0.0, min(float(t), dur - 2)), c))
        else:
            rest.append(c)
    # 시간 없는 댓글은 가장 넓게 비어 있는 곳에 하나씩 끼워 넣음
    for c in rest:
        pts = sorted([0.0] + [t for t, _ in timed] + [dur])
        gaps = [(b - a, (a + b) / 2) for a, b in zip(pts, pts[1:])]
        timed.append((max(gaps)[1] if gaps else 0.0, c))
    timed.sort(key=lambda x: x[0])
    out = []
    for i, (t, c) in enumerate(timed):
        end = timed[i + 1][0] if i + 1 < len(timed) else dur + 1
        out.append({**c, "start": 0.0 if i == 0 else t, "end": end})
    # 너무 짧게 스쳐 지나가는 댓글(1.5초 미만)은 빼고 앞 댓글을 늘림
    cleaned = []
    for c in out:
        if cleaned and c["end"] - c["start"] < 1.5:
            cleaned[-1]["end"] = c["end"]
            continue
        cleaned.append(c)
    return cleaned


def load_used(video_id):
    path = os.path.join("output", "used_comments.json")
    try:
        return set(json.load(open(path, encoding="utf-8")).get(video_id, []))
    except Exception:
        return set()


def mark_used(video_id, raw_text):
    path = os.path.join("output", "used_comments.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault(video_id, [])
    if raw_text not in data[video_id]:
        data[video_id].append(raw_text)
    os.makedirs("output", exist_ok=True)
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ④ AI (선택) ──────────────────────────────────────────
def ask_claude(prompt, max_tokens=1000, model=None):
    if not C.ANTHROPIC_API_KEY:
        return None
    import anthropic
    client = anthropic.Anthropic(api_key=C.ANTHROPIC_API_KEY)
    msg = client.messages.create(model=model or C.CLAUDE_MODEL, max_tokens=max_tokens,
                                 messages=[{"role": "user", "content": prompt}])
    # 생각(thinking) 블록은 건너뛰고 텍스트 블록만 모아서 읽음
    return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()


def ai_rank_per_segment(transcript, candidates, segments, original_title, per_seg=15):
    """구간마다 어울리는 댓글 후보를 순위대로 per_seg개 + 각각 띄울 시각(at) 추천 + 제목.
    반환: {"title": str, "segments": [[{"index": i, "at": 초}, ...], ...]}  (키 없으면 None)"""
    if not C.ANTHROPIC_API_KEY:
        return None
    bounds, off = [], 0.0
    for s0, e0 in segments:
        bounds.append((off, off + e0 - s0))
        off += e0 - s0
    blocks = []
    for k, (b0, b1) in enumerate(bounds):
        lines = [f"  [{c['start']:.0f}초] {c['text']}" for c in transcript
                 if "start" in c and b0 <= c["start"] < b1]
        if not lines and k == 0 and transcript and "start" not in transcript[0]:
            lines = ["  " + " ".join(c["text"] for c in transcript)]
        blocks.append(f"[구간 {k + 1}] 쇼츠 {b0:.0f}~{b1:.0f}초\n" + ("\n".join(lines) or "  (대사 정보 없음)"))
    cand = "\n".join(f"{i}. ({c['likes']}좋아요){' [장면 언급]' if c.get('scene') else ''} {c['text']}"
                     for i, c in enumerate(candidates))
    prompt = f"""유튜브 쇼츠 편집자 역할이야. 원본 영상 제목: {original_title}

[쇼츠 구간별 대사 (시간은 쇼츠 안에서의 시간)]
{chr(10).join(blocks)}

[댓글 후보]
{cand}

1) 쇼츠 제목: 12~16자, 짧고 궁금하게, "~한 이유"/"~할 때" 같은 쇼츠식 말투, 거짓 낚시 금지.
2) 구간마다 그 구간 대사·상황에 잘 어울리는 댓글을 어울리는 순서대로 최대 {per_seg}개 골라줘.
   - 각 댓글이 반응하는 대사가 나오는 시각(at, 그 구간 시간 범위 안)을 함께 적어줘. 대사 직후에 뜨는 게 자연스러워.
   - [장면 언급] 댓글은 해당 구간이면 우선, 같은 댓글을 여러 구간에 넣지 마, 비슷한 내용은 피해.
JSON으로만 답해: {{"title": "...", "segments": [[{{"index": 3, "at": 2}}, ...], [{{"index": 7, "at": 25}}, ...]]}}"""
    raw = ask_claude(prompt, max_tokens=2500)
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return None


def ai_pick(transcript, candidates, original_title, n_comments=1, total=60):
    """시간 붙은 대본을 보고 제목 + 댓글(각각 몇 초에 띄울지)을 고름. 키 없으면 None."""
    if not C.ANTHROPIC_API_KEY:
        return None
    lines = []
    for c in transcript:
        if "start" in c:
            lines.append(f"[{c['start']:.0f}초] {c['text']}")
        else:
            lines.append(c["text"])
    script = "\n".join(lines)[:5000]
    cand = "\n".join(f"{i}. ({c['likes']}좋아요){' [이 장면 언급]' if c.get('scene') else ''} {c['text']}"
                     for i, c in enumerate(candidates))
    prompt = f"""유튜브 쇼츠 편집자 역할이야. 원본 영상 제목: {original_title}
쇼츠 전체 길이: {total:.0f}초

[쇼츠 대사 (쇼츠 안에서의 시간)]
{script}

[댓글 후보]
{cand}

1) 이 쇼츠를 궁금하게 만드는 한국어 제목을 12~16자로 짧게 지어줘. "~한 이유", "~할 때" 같은 쇼츠식 말투, 거짓 낚시 금지.
2) 쇼츠 화면 아래에 차례로 바뀌며 나올 댓글을 최대 {n_comments}개 골라줘.
   - 각 댓글이 반응하는 대사가 나오는 시각(초)을 at으로 정해줘. 댓글은 그 대사 직후에 떠야 자연스러워.
   - 특정 대사와 상관없는 일반 반응 댓글은 비어 있는 시간대에 배치
   - [이 장면 언급] 댓글 우선, 클립과 무관하면 고르지 마, 비슷한 내용 중복 금지
   - at 값은 0~{total:.0f} 사이, 댓글끼리 최소 3초 이상 간격
JSON으로만 답해: {{"title": "...", "comments": [{{"index": 3, "at": 2}}, {{"index": 0, "at": 9}}]}}"""
    raw = ask_claude(prompt, max_tokens=1500)
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return None


def youtube_caption_text(video_id, segments):
    """구간 선택 때 받아둔 유튜브 자막에서, 쇼츠에 쓴 구간의 텍스트만 꺼냄 (교정 참고용)."""
    import glob
    files = sorted(glob.glob(os.path.join("output", "_analysis", video_id, "caps*.json3")))
    if not files:
        return ""
    try:
        data = json.load(open(files[0], encoding="utf-8"))
    except Exception:
        return ""
    out = []
    for s0, e0 in segments:
        for ev in data.get("events", []):
            t = ev.get("tStartMs", 0) / 1000
            if s0 - 1 <= t <= e0 + 1:
                txt = "".join(x.get("utf8", "") for x in ev.get("segs", []) or []).strip()
                if txt and txt != "\n":
                    out.append(txt.replace("\n", " "))
    return " ".join(out)[:4000]


def _my_context_block():
    import sub_style
    ex = sub_style.context_examples_text()
    return ("\n[편집자(나)가 실제로 최종으로 남긴 상황 자막 — 어느 줄에 달지, 말투를 이대로 따라 해]\n" + ex + "\n") if ex else ""


def add_context_captions(subs, title=""):
    """메인 자막 위에 붙일 '상황 설명' 자막을 AI가 작성 → 각 자막에 "context" 필드로 저장."""
    if not (C.ANTHROPIC_API_KEY and getattr(C, "CONTEXT_CAPTIONS", False) and subs):
        return subs, False
    src = [f"{i}. [{x['start']:.1f}초] {x['text']}" for i, x in enumerate(subs)]
    prompt = f"""예능 편집 자막 작가 역할이야. 영상: {title}
아래는 쇼츠의 대사 자막 목록이야. 메인 자막 위에 작게 붙는 초록색 '상황 설명 자막'을 달아줘.

예시 (예능잼잼 스타일):
- 대사 "여러분이 점심 시간 때" → 상황: "수능 점심때 노래들으면"
- 대사 "갑자기 머릿속에서 노래가" → 상황: "갑분 블랙핑크"
- 대사 "23번 글의 주제" → 상황: "영어 시험 시작"
- (말없이 문제 푸는 장면) → 상황: "집중해서 지문 읽는 중"

{_my_context_block()}
규칙:
- 시청자가 '지금 무슨 상황인지' 바로 알게 하거나 웃음 포인트를 짚어주는 짧은 말 (4~14자)
- 괄호는 쓰지 마 (자동으로 붙음), 대사를 그대로 반복하지 마
- 모든 줄에 달지 말고 30~50% 정도만. 같은 상황이 이어지면 연속된 줄에 같은 문구를 넣어도 돼
- 상황 설명이 필요 없는 줄은 빈 문자열 ""
- 배열 길이는 반드시 {len(subs)}개

{chr(10).join(src)}

JSON 문자열 배열로만 답해: ["", "수능 점심때 노래들으면", "수능 점심때 노래들으면", "", ...]"""
    raw = ask_claude(prompt, max_tokens=3000)
    try:
        arr = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
        assert isinstance(arr, list) and len(arr) == len(subs)
    except Exception:
        print("   ⚠️ 상황 설명 자막 생성 실패 (메인 자막만 사용)")
        return subs, False
    n = 0
    for x, c in zip(subs, arr):
        c = str(c or "").strip().strip("()")
        x["context"] = c
        n += bool(c)
    print(f"   🟩 상황 설명 자막 {n}줄 추가")
    return subs, True


def add_sfx(subs, title=""):
    """예능잼잼처럼 포인트마다 효과음. AI가 줄마다 효과음 이름을 정해 "sfx" 필드로 저장 (메모장에서 수정 가능)."""
    import sfx, sub_style
    if not (C.ANTHROPIC_API_KEY and getattr(C, "SFX", True) and subs):
        return subs, False
    lib = sfx.library()
    src = [f"{i}. [{x['start']:.1f}초] {x['text']}" + (f"   〔상황: {x['context']}〕" if x.get("context") else "")
           for i, x in enumerate(subs)]
    dur = subs[-1]["end"] - subs[0]["start"]
    target = max(2, round(dur / 7))
    ex = sub_style.sfx_examples_text()
    ex = f"\n[편집자(나)가 실제로 효과음을 넣은 예시 — 위치·빈도·종류를 따라 해]\n{ex}\n" if ex else ""
    prompt = f"""예능 쇼츠 편집자 역할이야 (예능잼잼 스타일). 영상: {title}
대사 자막마다 효과음을 넣을지 정해줘.

[쓸 수 있는 효과음]
{sfx.describe_for_ai(lib)}
{ex}
규칙:
- 웃음·반전·충격·리액션 포인트에만. 전체 {len(subs)}줄 중 {target}개 안팎 (약 7초에 1번)
- 연속된 두 줄에 연달아 넣지 마. 같은 효과음을 계속 반복하지 마
- 괄호 리액션 자막((당황)(억울) 등)이나 상황이 바뀌는 줄은 좋은 후보
- 위 목록에 있는 이름만 써. 효과음을 넣을 줄만 골라서 답해 (나머지 줄은 아예 쓰지 마)

{chr(10).join(src)}

JSON 배열로만 답해 (i = 줄 번호): [{{"i": 3, "sfx": "띠용"}}, {{"i": 11, "sfx": "뽁"}}]"""
    raw = ask_claude(prompt, max_tokens=3000)
    try:
        arr = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
        assert isinstance(arr, list)
    except Exception:
        print(f"   ⚠️ 효과음 배치 실패 (효과음 없이 진행) — AI 응답: {raw[:80]!r}")
        return subs, False
    for x in subs:
        x["sfx"] = ""
    n = 0
    for it in arr:
        try:
            i, a = int(it["i"]), sfx.normalize_name(str(it.get("sfx", "")))
        except Exception:
            continue
        if 0 <= i < len(subs) and a in lib:
            subs[i]["sfx"] = a
            n += 1
    print(f"   🔊 효과음 {n}개 배치: " + ", ".join(f"{x['start']:.0f}초 {x['sfx']}" for x in subs if x.get("sfx")))
    return subs, True


def youtube_caption_lines(video_id, segments):
    """유튜브 자막을 '쇼츠 안에서의 시간'으로 바꾼 줄 목록 [{start, text}]."""
    import glob
    files = sorted(glob.glob(os.path.join("output", "_analysis", video_id, "caps*.json3")))
    if not files:
        return []
    try:
        data = json.load(open(files[0], encoding="utf-8"))
    except Exception:
        return []
    out, offset = [], 0.0
    for s0, e0 in segments:
        for ev in data.get("events", []):
            t = ev.get("tStartMs", 0) / 1000
            if s0 - 0.5 <= t <= e0:
                txt = "".join(x.get("utf8", "") for x in ev.get("segs", []) or []).replace("\n", " ").strip()
                if txt:
                    out.append({"start": offset + max(0, t - s0), "text": txt})
        offset += e0 - s0
    return out


def ai_segment(words, title="", reference=""):
    """받아쓴 단어들을 '내 자막 규칙 + 내가 예전에 고친 예시'대로 AI가 줄 나누기 + 오타 교정.
    반환: (subs, ok). 타이밍은 단어 타임스탬프에서 가져와서 정확함."""
    import sub_style
    if not (C.ANTHROPIC_API_KEY and words):
        return [], False
    wl = "\n".join(f"{i}\t{s:.2f}\t{t}" for i, (s, e, t) in enumerate(words))
    rules = sub_style.load_rules()
    examples = sub_style.segment_examples_text()
    ref = (f"\n[참고: 유튜브 자동 자막 — 잘못 들린 단어를 고칠 때 참고]\n{reference}\n" if reference else "")
    prompt = f"""너는 예능 쇼츠 자막 편집자야. 영상: {title}
아래는 음성 인식으로 받아쓴 단어 목록이야 (번호 \t 시작초 \t 단어).
이걸 쇼츠 자막 줄로 나누고, 잘못 들린 단어는 고쳐줘.

[편집자(나)의 자막 규칙 — 반드시 따를 것]
{rules or "(없음)"}

[내가 실제로 고친 예시 — 줄 끊는 위치, 길이, 말투를 이대로 흉내 내]
{examples or "(아직 없음)"}
{ref}
[기술 조건]
- 한 줄은 화면 한 줄: 띄어쓰기 포함 최대 13자 (한글 글자만 세면 12자). 넘으면 의미 덩어리로 두 줄로 나눠. 짧은 줄은 얼마든지 OK
- 각 줄은 연속된 단어 번호 구간 [from, to] 을 가리킴. 순서대로, 겹치지 않게
- 의미 없는 잡음 단어는 어느 줄에도 넣지 않아도 됨 (건너뛰기 OK)
- text 는 그 구간의 말을 '맞춤법 교정 + 내 스타일'로 다듬은 최종 자막
- 동작·장난 멘트를 괄호로 쓸 땐 그 소리가 나는 구간 번호를 지정

JSON 배열로만 답해. 설명 금지:
[{{"from": 0, "to": 3, "text": "이번엔 김유연~"}}, {{"from": 4, "to": 4, "text": "진짜"}}, ...]

{wl}"""
    raw = ask_claude(prompt, max_tokens=8000, model=getattr(C, "FIX_MODEL", None))
    try:
        arr = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
        subs, last = [], -1
        for x in arr:
            f, t, text = int(x["from"]), int(x["to"]), str(x["text"]).strip()
            assert last < f <= t < len(words) and text
            subs.append({"start": words[f][0], "end": words[t][1], "text": text})
            last = t
        assert subs
    except Exception:
        print("   ⚠️ AI 자막 줄 나누기 실패 → 예전 방식(기계적 나누기 + 맞춤법 교정)으로 할게요")
        return [], False
    for a, b in zip(subs, subs[1:]):          # 빈틈 없이 이어 붙이기
        a["end"] = b["start"]
    n_ex = len(sub_style.load_examples())
    print(f"   🧠 내 자막 스타일(예시 {n_ex}개 학습)대로 {len(subs)}줄 생성")
    return subs, True


def fix_subs(subs, context_title="", reference=""):
    """받아쓰기 오타·오인식 교정 (예: '맞이 갔네' → '맛이 갔네'). 타이밍은 그대로, 글자만 고침."""
    if not (C.ANTHROPIC_API_KEY and getattr(C, "FIX_SUBS_WITH_AI", True) and subs):
        return subs, False
    src = [s["text"] for s in subs]
    ref = (f"\n[참고: 유튜브가 따로 만든 같은 구간 자동 자막 — 단어가 다르면 둘 중 문맥에 맞는 쪽을 골라]\n{reference}\n"
           if reference else "")
    prompt = f"""아래 JSON 배열은 유튜브 영상({context_title}) 음성을 자동으로 받아쓴 한국어 자막이야.
각 줄의 맞춤법, 띄어쓰기, 소리가 비슷해서 잘못 받아쓴 단어를 문맥에 맞게 고쳐줘.
예: "맞이 갔네" → "맛이 갔네", "안 되요" → "안 돼요", "감자 신인 거 같아" → "감자상인 것 같아"
{ref}
- 말투·구어체·줄임말·감탄사는 살릴 것
- 배열 길이({len(src)}개)와 순서를 반드시 유지, 내용 추가·삭제 금지
- 고칠 게 없는 줄은 그대로
고친 결과를 같은 길이의 JSON 문자열 배열로만 답해. 설명 금지.

{json.dumps(src, ensure_ascii=False)}"""
    raw = ask_claude(prompt, max_tokens=4000, model=getattr(C, "FIX_MODEL", None))
    try:
        fixed = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
        assert isinstance(fixed, list) and len(fixed) == len(src)
    except Exception:
        print("   ⚠️ AI 맞춤법 교정 응답을 해석하지 못해 원본 자막을 써요.")
        return subs, False
    changed = 0
    for sub, t in zip(subs, fixed):
        t = str(t).strip()
        if t and t != sub["text"]:
            print(f"      {sub['text']}  →  {t}")
            sub["text"] = t; changed += 1
    print(f"   ✏️  AI 맞춤법 교정: {changed}줄 수정")
    return subs, True


YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet_2023mar.onnx")
_DET = {}


def _detector():
    """얼굴 인식기: YuNet(옆얼굴·비스듬한 얼굴도 잡음) → 없으면 Haar(정면+옆얼굴)."""
    if "kind" in _DET:
        return _DET
    try:
        import cv2
    except Exception:
        print("   📦 얼굴 인식 도구(OpenCV)가 없어서 자동으로 설치할게요... (처음 한 번, 1~2분)")
        try:
            import sys, importlib
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "opencv-python<5"], check=True)
            importlib.invalidate_caches()
            import cv2
            print("   ✅ 설치 완료")
        except Exception:
            print('   ⚠️ 자동 설치 실패. cmd에서 직접: python -m pip install "opencv-python<5"')
            _DET["kind"] = None
            return _DET
    _DET["cv2"] = cv2
    if not os.path.exists(YUNET_PATH):
        try:
            import urllib.request
            os.makedirs(os.path.dirname(YUNET_PATH), exist_ok=True)
            urllib.request.urlretrieve(YUNET_URL, YUNET_PATH)
        except Exception:
            pass
    try:
        _DET["yunet"] = cv2.FaceDetectorYN.create(YUNET_PATH, "", (960, 540), 0.55)
        _DET["kind"] = "yunet"
        return _DET
    except Exception:
        pass
    try:
        _DET["front"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        _DET["side"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
        _DET["kind"] = "haar" if not _DET["front"].empty() else None
    except Exception:
        _DET["kind"] = None
    return _DET


def _faces_in_frame(frame):
    """한 프레임의 얼굴들 [(cx, cy, size)] (모두 0~1 비율)."""
    D = _detector()
    cv2 = D.get("cv2")
    if not D.get("kind"):
        return []
    h, w = frame.shape[:2]
    sw, sh = 960, int(960 * h / w)
    small = cv2.resize(frame, (sw, sh))
    out = []
    if D["kind"] == "yunet":
        det = D["yunet"]
        det.setInputSize((sw, sh))
        _, faces = det.detect(small)
        for f in (faces if faces is not None else []):
            x, y, fw, fh = f[:4]
            out.append(((x + fw / 2) / sw, (y + fh / 2) / sh, fh / sh))
    else:
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        for (x, y, fw, fh) in D["front"].detectMultiScale(g, 1.15, 5, minSize=(30, 30)):
            out.append(((x + fw / 2) / sw, (y + fh / 2) / sh, fh / sh))
        if not D["side"].empty():
            for (x, y, fw, fh) in D["side"].detectMultiScale(g, 1.1, 4, minSize=(30, 30)):
                out.append(((x + fw / 2) / sw, (y + fh / 2) / sh, fh / sh))
            for (x, y, fw, fh) in D["side"].detectMultiScale(cv2.flip(g, 1), 1.1, 4, minSize=(30, 30)):
                out.append((1 - (x + fw / 2) / sw, (y + fh / 2) / sh, fh / sh))
    return out


def detect_people(video_path, t0=None, t1=None, samples=24):
    """구간에서 얼굴을 찾아 사람별로 묶음. [{x, y, size, count}] (자주 나온 사람 순)."""
    D = _detector()
    if not D.get("kind"):
        return []
    cv2 = D["cv2"]
    cap = cv2.VideoCapture(video_path)
    if t0 is None:
        t0, t1 = 0.0, (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30)
    dets = []
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + (t1 - t0) * (k + 0.5) / samples) * 1000)
        ok, frame = cap.read()
        if ok:
            dets += _faces_in_frame(frame)
    cap.release()
    people = []
    for (x, y, sz) in sorted(dets):
        for p in people:
            if abs(p["x"] - x) < 0.09:
                n = p["count"]
                p["x"] = (p["x"] * n + x) / (n + 1)
                p["y"] = (p["y"] * n + y) / (n + 1)
                p["size"] = (p["size"] * n + sz) / (n + 1)
                p["count"] += 1
                p["face_top"] = min(p["face_top"], y - sz * 0.65)
                p["face_bottom"] = max(p["face_bottom"], y + sz * 0.60)
                p["x0"], p["x1"] = min(p["x0"], x), max(p["x1"], x)
                p["y0"], p["y1"] = min(p["y0"], y), max(p["y1"], y)
                break
        else:
            people.append({"x": x, "y": y, "size": sz, "count": 1, "x0": x, "x1": x, "y0": y, "y1": y,
                           "face_top": y - sz * 0.65, "face_bottom": y + sz * 0.60})
    min_count = max(2, samples // 8)
    people = [p for p in people if p["count"] >= min_count] or people
    return sorted(people, key=lambda p: -p["count"] * p["size"])[:3]


def shot_track(video_path, t0, t1, target_x, samples=8, tol=0.18):
    """한 컷 안에서 '그 사람' 얼굴 위치를 시간순으로 추적. [(컷 안 상대시간, x, y)]"""
    D = _detector()
    if not D.get("kind"):
        return []
    cv2 = D["cv2"]
    cap = cv2.VideoCapture(video_path)
    pts = []
    for k in range(samples):
        t = t0 + (t1 - t0) * (k + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        faces = _faces_in_frame(frame)
        near = [f for f in faces if abs(f[0] - target_x) < tol]
        if near:
            x, y, _ = max(near, key=lambda f: f[2])
            pts.append((round(t - t0, 3), float(x), float(y)))
            target_x = x                     # 조금씩 움직여도 계속 따라붙음
    cap.release()
    return pts


def _follow_expr(pts, axis, size):
    """얼굴 위치를 따라 움직이는 crop 좌표식. pts=[(컷 안 상대시간, 비율)]를 직선으로 이어 부드럽게 따라감."""
    if len(pts) == 1:
        frac = f"{pts[0][1]:.4f}"
    else:
        frac = f"{pts[-1][1]:.4f}"
        for (ta, va), (tb, vb) in reversed(list(zip(pts, pts[1:]))):
            seg = f"{va:.4f}" if tb == ta else f"{va:.4f}+({vb - va:.4f})*(t-{ta:.3f})/{tb - ta:.3f}"
            frac = f"if(lt(t,{tb:.3f}),if(lt(t,{ta:.3f}),{va:.4f},{seg}),{frac})"
    return f"min(max({axis}*({frac})-{size}/2,0),{axis}-{size})"


def speaker_x(video_path, t0, t1, people, samples=5):
    """이 컷에서 '말하는 사람'의 x 위치. 사람별로 입 주변 움직임을 재서 제일 많이 움직인 쪽.
    확실하지 않으면 None (호명 실패 → 번갈아 찍기로)."""
    D = _detector()
    if not D.get("kind") or len(people) < 2:
        return None
    cv2 = D["cv2"]
    cap = cv2.VideoCapture(video_path)
    score = [0.0] * len(people)
    hits = [0] * len(people)
    for k in range(samples):
        t = t0 + (t1 - t0) * (k + 0.5) / samples
        frames = []
        for dt in (0.0, 0.13):
            cap.set(cv2.CAP_PROP_POS_MSEC, (t + dt) * 1000)
            ok, fr = cap.read()
            if not ok:
                break
            h, w = fr.shape[:2]
            sm = cv2.resize(fr, (960, int(960 * h / w)))
            frames.append(cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY))
        if len(frames) < 2:
            continue
        g1, g2 = frames
        sh, sw = g1.shape
        faces = _faces_in_frame_cached = _faces_in_frame(cv2.cvtColor(g1, cv2.COLOR_GRAY2BGR))
        for i, p in enumerate(people):
            near = [f for f in faces if abs(f[0] - p["x"]) < 0.12]
            if not near:
                continue
            cx, cy, size = max(near, key=lambda f: f[2])
            # 입 주변 상자 (얼굴 아래쪽 1/3)
            x0 = int(max(cx - size * 0.45, 0) * sw); x1 = int(min(cx + size * 0.45, 1) * sw)
            y0 = int(max(cy + size * 0.05, 0) * sh); y1 = int(min(cy + size * 0.5, 1) * sh)
            if x1 <= x0 or y1 <= y0:
                continue
            import numpy as np
            d = np.abs(g1[y0:y1, x0:x1].astype(int) - g2[y0:y1, x0:x1].astype(int)).mean()
            score[i] += d; hits[i] += 1
    cap.release()
    avg = [(score[i] / hits[i]) if hits[i] else 0.0 for i in range(len(people))]
    order = sorted(range(len(people)), key=lambda i: -avg[i])
    if avg[order[0]] <= 0 or (len(order) > 1 and avg[order[0]] < avg[order[1]] * 1.25):
        return None                       # 둘 다 비슷하게 움직이면 못 정함
    return people[order[0]]["x"]


def drop_still_faces(video_path, people, t0, t1, quiet=False):
    """벽에 붙은 사진·포스터 속 얼굴 제거. 진짜 사람은 미세하게라도 계속 움직이고,
    인쇄된 얼굴은 픽셀이 거의 안 변한다. 다른 얼굴은 확실히 움직이는데 혼자 안 움직이면 사진으로 판정."""
    D = _detector()
    if not D.get("kind") or not people:
        return people
    import numpy as np
    cv2 = D["cv2"]
    cap = cv2.VideoCapture(video_path)
    scores = []
    grays = {}
    def gray_at(t):
        if t not in grays:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if not ok:
                return None
            h, w = fr.shape[:2]
            grays[t] = cv2.cvtColor(cv2.resize(fr, (960, int(960 * h / w))), cv2.COLOR_BGR2GRAY)
        return grays.get(t)
    for p in people:
        diffs = []
        for k in range(3):
            t = t0 + (t1 - t0) * (k + 0.5) / 3
            g1, g2 = gray_at(round(t, 2)), gray_at(round(t + 0.15, 2))
            if g1 is None or g2 is None:
                continue
            sh, sw = g1.shape
            x0 = int(max(p["x"] - p["size"] * 0.7, 0) * sw); x1 = int(min(p["x"] + p["size"] * 0.7, 1) * sw)
            y0 = int(max(p["y"] - p["size"] * 0.9, 0) * sh); y1 = int(min(p["y"] + p["size"] * 0.9, 1) * sh)
            if x1 > x0 and y1 > y0:
                diffs.append(float(np.abs(g1[y0:y1, x0:x1].astype(int) - g2[y0:y1, x0:x1].astype(int)).mean()))
        scores.append(sum(diffs) / len(diffs) if diffs else 0.0)
    cap.release()
    top = max(scores)
    if top < 0.8:
        return people                      # 전체가 조용한 장면이면 판단 보류
    alive = [p for p, sc in zip(people, scores) if sc > max(0.5, top * 0.18)]
    if len(alive) < len(people) and not quiet:
        print(f"   🖼️ 사진/포스터 속 얼굴 {len(people) - len(alive)}개 무시")
    return alive or people


def face_center(video_path, samples=24):
    """(앵글 전환을 끈 경우) 가장 자주 나온 사람 얼굴 위치. 없으면 화면 가운데."""
    ppl = detect_people(video_path, samples=samples)
    if not ppl:
        return 0.5, 0.5
    if len(ppl) >= 2:
        return (ppl[0]["x"] + ppl[1]["x"]) / 2, (ppl[0]["y"] + ppl[1]["y"]) / 2
    return ppl[0]["x"], ppl[0]["y"]


def clip_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def plan_shots(dur, subs):
    """샷 전환 시점 계획: 자막 경계에 맞춰 SHOT_MIN~SHOT_MAX초 단위로 끊고, 넓은 샷/클로즈업 번갈아."""
    lo, hi = C.SHOT_MIN, C.SHOT_MAX
    bounds = sorted({round(x["start"], 2) for x in subs if 0 < x["start"] < dur})
    cuts, t = [0.0], 0.0
    while t < dur - lo:
        cand = [b for b in bounds if t + lo <= b <= t + hi]
        nxt = cand[-1] if cand else min(t + (lo + hi) / 2, dur)
        if dur - nxt < lo:          # 마지막 샷이 너무 짧으면 합침
            break
        cuts.append(nxt)
        t = nxt
    cuts.append(dur)
    shots = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        shots.append({"start": a, "end": b, "close": i % 2 == 1})
    return shots


def _fit_zoom(span, visible, want):
    """필요한 범위(span)가 화면(visible/zoom)에 들어가는 최대 배율."""
    return max(1.0, min(want, visible / max(span, 1e-4)))


def _subtitle_spans(subs, dur):
    """compose와 동일하게 다음 자막 시작까지 이전 자막을 유지."""
    spans = []
    for i, sub in enumerate(subs):
        end = subs[i + 1]["start"] if i + 1 < len(subs) else sub["end"]
        if end > sub["start"]:
            spans.append((max(0, sub["start"]), min(dur, end), sub,
                          render.subtitle_height(sub.get("text", ""), sub.get("context", ""))))
    return spans


def _context_face_framing(vh, aspect, zoom, fx, fy, faces, caption_h, cover_h, cut):
    """얼굴과 자막 사이 공간을 확보하는 고정 크롭. 높이/y/변경 여부/원본 자막 보호 반환."""
    margin = 16
    safe_bottom = vh - C.SUB_BOTTOM_MARGIN - caption_h - margin
    min_h = max(vh, int((C.W / aspect + 1) // 2) * 2)
    height = max(min_h, int(vh * zoom) // 2 * 2)

    def default_y(h):
        limit = h * (1 - cut) - (vh - cover_h) if cut else h - vh
        return max(0, min(h * fy - vh * .42, h - vh, limit))

    original_y = default_y(height)
    if not faces or all(p["face_bottom"] * height - original_y <= safe_bottom and
                        p["face_top"] * height - original_y >= margin for p in faces):
        return height, original_y, False, True

    # 조금씩 줌아웃하며, 원본 자막은 실제 전폭 박스로 가릴 수 있는 범위까지만 허용.
    for protect_source in (True, False):
        for h in range(height, min_h - 1, -2):
            low = max(0, max(p["face_bottom"] * h - safe_bottom for p in faces))
            high = min(h - (vh - C.SUB_BOTTOM_MARGIN - caption_h),
                       min(p["face_top"] * h - margin for p in faces))
            if protect_source and cut:
                high = min(high, h * (1 - cut) - (vh - cover_h))
            # crop의 픽셀 반올림 오차를 막기 위해 허용 범위도 짝수로 맞춤.
            low = int(-(-low // 2)) * 2
            high = int(high // 2) * 2
            if low <= high:
                y = min(max(round(default_y(h) / 2) * 2, low), high)
                return h, y, True, protect_source
    # 얼굴 자체가 가용 영역보다 큰 경우 등은 줌아웃만으로 해결 불가.
    # 최대로 넓혀도 안 들어가면 전체 원본을 자막 위 공간에 맞추고 검은 여백으로 채움.
    # 얼굴 보호를 우선하며, 이 경우 원본 자막 노출 가능성을 호출자에게 알림.
    fit_h = max(2, int(min(safe_bottom, C.W / aspect)) // 2 * 2)
    return fit_h, 0, True, False


def angle_filter(clip, vh, subs, out_label="v", fixed=False):
    """앵글 전환. 컷마다 그 구간에서 얼굴을 다시 찾아 중앙에 오게 확대
    (원본에 편집 컷이 있어서 사람 위치가 계속 바뀌기 때문)."""
    dur = clip_duration(clip)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "json", clip],
                           capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    aspect = stream["width"] / stream["height"]
    spans = _subtitle_spans(subs, dur)
    base_w_frac = C.W / (vh * aspect)          # 1배일 때 화면이 보는 원본 가로 비율
    cut = min(max(float(getattr(C, "SRC_SUB_HIDE", 0.2)), 0.0), 0.4)   # 원본 자막 띠 높이 (아래에서부터)
    cover = int(getattr(C, "SUB_COVER_PX", 160))                        # 내 자막 박스가 확실히 덮는 높이(px)
    glob = drop_still_faces(clip, detect_people(clip), 0, dur)

    def hide_zoom(z, fy_):
        """원본 자막 띠가 '내 자막 박스 뒤'로 들어갈 때까지 확대 배율을 올림."""
        if not cut:
            return z
        while z < 3.0:
            ih = vh * z
            y = min(max(ih * fy_ - vh * 0.42, 0), ih - vh)
            if ih * (1 - cut) - y >= vh:               # 띠가 화면 밖 (얼굴 중심 유지한 채)
                return z
            z += 0.05
        return 3.0
    shots = ([{"start": 0.0, "end": dur, "close": False}] if fixed else plan_shots(dur, subs))

    if glob:
        print(f"   👤 얼굴 {len(glob)}명 발견 (가로 위치 " + ", ".join(f"{p['x']:.0%}" for p in glob) + ")")
    else:
        print("   👤 얼굴을 못 찾아서 확대 없이 원본 그대로 써요")

    parts = [f"[0:v]split={len(shots)}" + "".join(f"[ang_in{i}]" for i in range(len(shots)))]
    labels, n_close, close_k = [], 0, 0
    for i, sh in enumerate(shots):
        here = detect_people(clip, sh["start"], sh["end"], samples=6)
        here = drop_still_faces(clip, here, sh["start"], sh["end"], quiet=True)
        protected_faces = []
        active = [s for s in spans if s[0] < sh["end"] and s[1] > sh["start"]]
        contexts = [s for s in active if (s[2].get("context") or "").strip()]
        if contexts and not here and glob:
            # 고개를 숙여 구간 탐지가 실패하면 영상 전체에서 찾은 인물을 보수적으로 보호.
            here = [dict(p, face_top=max(0, p["face_top"] - .04),
                         face_bottom=min(1, p["face_bottom"] + .04)) for p in glob]
            print(f"   ⚠️ {sh['start']:.1f}초: 상황 자막 구간 얼굴 탐지 불확실, 넓은 안전 구도 사용")
        if not here:
            fx, fy, zoom = 0.5, 0.42, 1.0       # 이 컷에 얼굴이 없으면 확대하지 않음
        elif sh["close"]:
            # 클로즈업: 말하는 사람(입이 제일 많이 움직인 사람) 한 명만, 최대한 크게. 컷 안에서는 고정.
            spk = speaker_x(clip, sh["start"], sh["end"], here)
            if spk is not None:
                pick = min(here, key=lambda p: abs(p["x"] - spk))
            else:
                pick = here[close_k % min(len(here), 2)]
            protected_faces = [pick]
            close_k += 1; n_close += 1
            track = shot_track(clip, sh["start"], sh["end"], pick["x"])
            if track:                          # 컷 안에서 돌아다닌 위치의 중앙값에 '고정'
                xs = sorted(t[1] for t in track); ys = sorted(t[2] for t in track)
                fx, fy = xs[len(xs) // 2], ys[len(ys) // 2]
            else:
                fx, fy = pick["x"], pick["y"]
            fy = max(fy - pick["size"] * 0.15, 0)
            # 얼굴이 화면 높이의 42%쯤 차도록 최대 확대 (ZOOM_CLOSE가 상한)
            zoom = min(C.ZOOM_CLOSE, max(1.3, 0.42 / max(pick["size"], 0.04)))
        else:
            protected_faces = list(here)
            # 넓은 샷: 이 컷의 모든 얼굴(이동 범위 포함)이 들어가게
            x0 = min(p["x0"] - p["size"] * 1.2 for p in here)
            x1 = max(p["x1"] + p["size"] * 1.2 for p in here)
            y0 = min(p["y0"] - p["size"] * 1.4 for p in here)
            y1 = max(p["y1"] + p["size"] * 1.2 for p in here)
            fx, fy = (x0 + x1) / 2, (y0 + y1) / 2
            zoom = min(_fit_zoom(x1 - x0, base_w_frac, C.ZOOM_WIDE), _fit_zoom(y1 - y0, 1.0, C.ZOOM_WIDE))
        if fixed:
            zoom = max(1.0, float(getattr(C, "FACE_ZOOM", 1.0)))
        zoom = hide_zoom(zoom, fy)             # 원본 자막이 내 자막 박스 밖으로 보이면 얼굴 쪽으로 더 확대
        h = max(int(vh * zoom) // 2 * 2, vh, int((C.W / aspect + 1) // 2) * 2)
        ymax = f"ih*{1 - cut:.3f}-({vh}-{cover})" if cut else f"ih-{vh}"
        crop_y = f"max(min(max(ih*{fy:.5f}-{vh}*0.42,0),min(ih-{vh},{ymax})),0)"
        if contexts and protected_faces:
            # 초록 자막이 실제로 나오는 구간마다 재탐지. 같은 컷은 최대 범위를 보호하고 고정.
            context_faces = []
            for a, b, sub, sub_h in contexts:
                detected = detect_people(clip, max(a, sh["start"]), min(b, sh["end"]), samples=8)
                for target in protected_faces:
                    near = [p for p in detected if abs(p["x"] - target["x"]) < .18]
                    context_faces.extend(near or [target])
            caption_h = max(s[3] for s in active)
            # 박스가 없는 구간/여백에는 원본 자막을 숨길 수 있다고 가정하지 않음.
            cover_h = min(s[3] for s in active) if active else 0
            if (not C.SUB_BOX or C.SUB_BOX_MIN_W < 1.0 or C.SUB_BOTTOM_MARGIN != 0
                    or active[0][0] > sh["start"] or active[-1][1] < sh["end"]):
                cover_h = 0
            h, y, changed, source_safe = _context_face_framing(
                vh, aspect, h / vh, fx, fy, protected_faces + context_faces,
                caption_h, cover_h, cut)
            crop_y = str(y)
            if changed:
                print(f"   🟩 {sh['start']:.1f}~{sh['end']:.1f}초: 상황 자막 얼굴 가림 방지 (고정 {h / vh:.2f}배)")
            if not source_safe:
                print(f"   ⚠️ {sh['start']:.1f}~{sh['end']:.1f}초: 얼굴/원본 자막 동시 보호 어려움 — 미리보기 확인 필요")
        viewport_h = vh
        if contexts and protected_faces:
            viewport_h = max(2, int(vh - C.SUB_BOTTOM_MARGIN - caption_h) // 2 * 2)
        if h < viewport_h:
            framing = f"scale=-2:{h},pad={C.W}:{vh}:(ow-iw)/2:0:black"
        else:
            framing = (f"scale=-2:{h},crop={C.W}:{viewport_h}:"
                       f"'min(max(iw*{fx:.5f}-{C.W}/2,0),iw-{C.W})':'{crop_y}'")
            if viewport_h < vh:
                framing += f",pad={C.W}:{vh}:0:0:black"
        parts.append(f"[ang_in{i}]trim={sh['start']:.3f}:{sh['end']:.3f},setpts=PTS-STARTPTS,"
                     f"{framing},setsar=1,fps=30[ang{i}]")
        labels.append(f"[ang{i}]")
    parts.append("".join(labels) + f"concat=n={len(shots)}:v=1:a=0[{out_label}]")
    print(f"   🎥 앵글 전환 {len(shots)}컷 (클로즈업 {n_close}컷, 컷 안 카메라 고정)")
    return ";".join(parts)


# ⑤ 합성 ────────────────────────────────────────────────
def _has_audio(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", path], capture_output=True, text=True)
        return bool(r.stdout.strip())
    except Exception:
        return True


def compose(clip, title, subs, comment, out_path, workdir):
    os.makedirs(workdir, exist_ok=True)
    title_png = os.path.join(workdir, "title.png")
    render.render_title(title, title_png)
    src_png = os.path.join(workdir, "source.png")
    render.render_source(f"출처: {C.SOURCE_CHANNEL}", src_png)

    dur = clip_duration(clip)
    loop_img = lambda png: ["-loop", "1", "-t", f"{dur:.3f}", "-i", png]   # 끝이 있는 반복 (안 끝나는 렌더 방지)
    inputs = ["-i", clip, *loop_img(title_png), *loop_img(src_png)]
    vy0, vy1 = C.VIDEO_AREA
    vh = vy1 - vy0
    # 얼굴 중심 확대: 영상을 (영역 높이 × 배율)로 키운 뒤, 얼굴이 가운데 오도록 1080×vh 만큼 잘라냄
    cut = min(max(float(getattr(C, "SRC_SUB_HIDE", 0.2)), 0.0), 0.4)
    cover = int(getattr(C, "SUB_COVER_PX", 160))
    zoom = max(1.0, float(getattr(C, "FACE_ZOOM", 1.0)))
    fx, fy = face_center(clip) if zoom > 1.0 and not getattr(C, "ANGLE_SWITCH", False) else (0.5, 0.5)
    sh = int(vh * zoom) // 2 * 2
    ymax = f"ih*{1 - cut:.3f}-({vh}-{cover})" if cut else f"ih-{vh}"
    if getattr(C, "ANGLE_SWITCH", False) or any((s.get("context") or "").strip() for s in subs):
        video_filter = angle_filter(clip, vh, subs or [], fixed=not getattr(C, "ANGLE_SWITCH", False))
    else:
      video_filter = (f"[0:v]scale=-2:{sh},"
                    f"crop={C.W}:{vh}:'min(max(iw*{fx:.3f}-{C.W}/2,0),iw-{C.W})':'max(min(max(ih*{fy:.3f}-{vh}/2,0),min(ih-{vh},{ymax})),0)',"
                    f"setsar=1[v]")
    fg = [
        f"color=c=white:s={C.W}x{C.H}:r=30[bg]",
        # 영상: 높이를 맞춘 뒤 가운데를 잘라 1080 폭으로
        video_filter,
        f"[bg][v]overlay=0:{vy0}:shortest=1[a0]",
        f"[a0][1:v]overlay=0:{C.TITLE_AREA[0]}[a1]",
        f"[a1][2:v]overlay=0:{C.SOURCE_Y}[a2]",
    ]
    last, idx = "a2", 3
    # comment: dict 하나 또는 [{..., "start": 초, "end": 초}, ...] 리스트
    comments = comment if isinstance(comment, list) else ([comment] if comment else [])
    for k, cm in enumerate(comments):
        cpng = os.path.join(workdir, f"comment_{k}.png")
        style = getattr(C, "COMMENT_STYLE", "card")
        if style == "clean":
            render.render_comment_clean(cm["text"], cm.get("likes"), cpng,
                                        author=cm.get("author", ""), own=cm.get("own", False))
        elif style == "simple":
            render.render_comment_simple(cm["text"], cpng, author=cm.get("author", ""), own=cm.get("own", False))
        else:
            avatar_file = None
            if cm.get("avatar"):
                try:
                    import urllib.request
                    avatar_file = os.path.join(workdir, f"avatar_{k}.jpg")
                    urllib.request.urlretrieve(cm["avatar"], avatar_file)
                except Exception:
                    avatar_file = None
            render.render_comment(cm["text"], cm.get("likes"), cpng, author=cm.get("author", ""),
                                  avatar_path=avatar_file, own=cm.get("own", False))
        inputs += loop_img(cpng)
        when = ""
        if "start" in cm:
            when = f":enable='between(t,{cm['start']:.2f},{cm['end']:.2f})'"
        fg.append(f"[{last}][{idx}:v]overlay=0:{C.COMMENT_Y}{when}[c{k}]")
        last, idx = f"c{k}", idx + 1
    # 자막 사이 틈 없애기: 다음 자막이 나올 때까지 이전 자막 유지
    subs = [dict(x) for x in subs]
    for a_, b_ in zip(subs, subs[1:]):
        a_["end"] = b_["start"]
    for i, s in enumerate(subs):
        p = os.path.join(workdir, f"sub_{i:03d}.png")
        h = render.render_subtitle(s["text"], p, index=i, context=s.get("context", ""))
        y = vy1 - C.SUB_BOTTOM_MARGIN - h   # 자막 박스 아래끝 = 영상 아래끝
        inputs += ["-i", p]
        fg.append(f"[{last}][{idx}:v]overlay=0:{y}:enable='between(t,{s['start']:.2f},{s['end']:.2f})'[s{i}]")
        last, idx = f"s{i}", idx + 1

    # 소리 다듬기 + 효과음 섞기
    audio_map = "0:a?"
    polish = ("highpass=f=80,loudnorm=I=-14:TP=-1.5:LRA=11"
              if getattr(C, "AUDIO_POLISH", True) else "anull")
    events = [x for x in subs if x.get("sfx")] if getattr(C, "SFX", True) else []
    if not events and _has_audio(clip) and polish != "anull":
        fg.append(f"[0:a]{polish},aformat=sample_rates=44100:channel_layouts=stereo[aout]")
        audio_map = "[aout]"
    if events and _has_audio(clip):
        import sfx
        lib = sfx.library()
        labels = []
        vol = getattr(C, "SFX_VOLUME", 0.55)
        for k, x in enumerate(events):
            f = sfx.pick_file(x["sfx"], lib)
            if not f:
                print(f"   (효과음 '{x['sfx']}' 파일이 없어서 건너뜀)")
                continue
            ms = max(0, int(x["start"] * 1000))
            inputs += ["-i", f]
            fg.append(f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                      f"adelay={ms}|{ms},volume={vol}[fx{k}]")
            labels.append(f"[fx{k}]")
            idx += 1
        if labels:
            fg.append(f"[0:a]{polish},aformat=sample_rates=44100:channel_layouts=stereo[main];"
                      f"[main]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:normalize=0[aout]")
            audio_map = "[aout]"
            print(f"   🔊 효과음 {len(labels)}개 넣는 중")

    script = os.path.join(workdir, "filter.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(";\n".join(fg))
    tail = ["-map", f"[{last}]", "-map", audio_map,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out_path]
    base = ["ffmpeg", "-y", "-loglevel", "error", *inputs]
    # ffmpeg 7+ 는 '-/filter_complex 파일', 옛 버전은 '-filter_complex_script 파일'
    r = subprocess.run(base + ["-/filter_complex", script] + tail)
    if r.returncode != 0:
        subprocess.run(base + ["-filter_complex_script", script] + tail, check=True)
