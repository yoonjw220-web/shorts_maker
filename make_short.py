"""쇼츠 만들기.

가장 간단)  python make_short.py "https://youtu.be/XXXXXXXXXXX"      ← 구간까지 AI가 자동 선택

예)  python make_short.py "https://youtu.be/XXXXXXXXXXX" --start 3:10 --end 3:52
     python make_short.py "https://youtu.be/XXXXXXXXXXX" --start 3:10 --end 3:52 --title "수능장에서 억울하게 경고받은 이유"
     (자막 오타 고친 뒤 다시)  python make_short.py "URL" --start 3:10 --end 3:52 --subs output/XXXX_310/subs.json
"""
import argparse
import json
import os
import sys

import config as C
import pipeline as P


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="원본 유튜브 URL")
    ap.add_argument("--start", help="시작 시각 (예: 3:10). 생략하면 AI가 자동 선택")
    ap.add_argument("--end", help="끝 시각 (예: 3:52, 60초 이하 권장)")
    ap.add_argument("--segments", help='구간 직접 지정. 예: "4:32-4:54, 5:21-5:42" (여러 개면 이어 붙임)')
    ap.add_argument("--pick", type=int, help="후보 하나만 골라서 단독 쇼츠로 (기본: 후보 3개를 이어 붙임)")
    ap.add_argument("--all", action="store_true", help="후보마다 각각 단독 쇼츠 만들기")
    ap.add_argument("--title", help="쇼츠 제목 (없으면 AI가 짓거나 직접 입력 요청)")
    ap.add_argument("--input", help="이미 받아둔 원본 mp4가 있으면 경로 (다운로드 생략)")
    ap.add_argument("--subs", help="수정한 subs.json 경로 (Whisper 생략)")
    ap.add_argument("--comment-index", type=int, help="후보 목록에서 쓸 댓글 번호")
    ap.add_argument("--comment", help="댓글 문구를 직접 지정 (좋아요 수는 --likes)")
    ap.add_argument("--likes", type=int, default=0)
    ap.add_argument("--no-comment", action="store_true", help="댓글 카드 없이")
    ap.add_argument("--no-subs", action="store_true", help="자막 넣지 않기 (원본에 자막이 이미 있을 때)")
    ap.add_argument("--subs-on", action="store_true", help="묻지 않고 자막 넣기")
    a = ap.parse_args()
    a.cli_title = a.title          # --title로 직접 준 제목만 '확정 제목'으로 취급
    _vid = P.video_id_from_url(a.url)
    if _vid:
        a.url = f"https://www.youtube.com/watch?v={_vid}"   # &list= 같은 재생목록 정보 제거
    if a.no_subs:
        a.burn_subs = False
    elif a.subs_on:
        a.burn_subs = True
    else:
        a.burn_subs = True        # 자막은 항상 넣음 (빼려면 --no-subs)
    print("   → 자막 " + ("넣음" if a.burn_subs else "안 넣음 (받아쓰기 생략)"))

    if a.segments or (a.start and a.end):
        segs = parse_segments(a.segments) if a.segments else [(P.parse_time(a.start), P.parse_time(a.end))]
        title = a.title
        while True:
            print("\n━━━ 구간 " + " + ".join(f"{_fmt(s0)}~{_fmt(e0)}" for s0, e0 in segs) + " 제작 ━━━")
            title = make_one(a, segs, title, {})
            new = review_segments(segs, first=False)
            if new is None:
                return
            if isinstance(new, tuple) and new[0] == "title":
                title = new[1] or None
                continue
            segs = new
    if a.input:
        sys.exit("로컬 파일(--input)은 --start/--end를 같이 넣어 주세요.")

    import highlight
    cands, info = highlight.find(a.url)
    meta = {"title": info.get("title", ""), "channel": info.get("channel", "")}
    if a.all:
        for c in cands:
            print(f"\n━━━ {highlight.fmt(c['start'])}~{highlight.fmt(c['end'])} 단독 제작 ━━━")
            make_one(a, [(c["start"], c["end"])], a.title or c.get("title"), meta)
    elif a.pick is not None:
        c = cands[min(a.pick, len(cands) - 1)]
        make_one(a, [(c["start"], c["end"])], a.title or c.get("title"), meta)
    else:
        segs = select_candidates(cands)
        title = a.title
        while True:
            print("\n━━━ 구간 " + " + ".join(f"{highlight.fmt(s0)}~{highlight.fmt(e0)}" for s0, e0 in segs)
                  + " 제작 ━━━")
            title = make_one(a, segs, title, meta)
            new = review_segments(segs, first=False)
            if new is None:
                break
            if isinstance(new, tuple) and new[0] == "title":
                title = new[1] or None
                continue
            segs = new


def select_candidates(cands):
    """AI가 고른 구간 후보를 보여주고 선택받음."""
    print("\n🤖 AI 추천 구간 후보")
    for i, c in enumerate(cands, 1):
        extra = f"  {c.get('title') or ''}" + (f" — {c['reason']}" if c.get("reason") else "")
        print(f"   {i}. {_fmt(c['start'])} ~ {_fmt(c['end'])} ({c['end'] - c['start']:.0f}초){extra}")
    print("   고르는 법) 1  /  여러 개 이어 붙이기: 1,3  /  직접 입력: 4:32-4:54, 5:21-5:42")
    while True:
        print("👉 쓸 구간 (엔터 = 1번)")
        txt = input("> ").strip()
        try:
            if not txt:
                return [(cands[0]["start"], cands[0]["end"])]
            if ":" in txt:
                return parse_segments(txt)
            nums = [int(t) for t in txt.replace(" ", ",").split(",") if t]
            assert nums and all(1 <= n <= len(cands) for n in nums)
            return sorted((cands[n - 1]["start"], cands[n - 1]["end"]) for n in nums)
        except Exception:
            print("   ⚠️ 후보 번호(예: 1 또는 1,3)나 시간(예: 4:32-4:54)으로 입력해 주세요.")


def choose_comments_per_segment(cands, segments, pick, a):
    """구간마다 댓글 후보 15개를 보여주고 번호로 고르게 함. 엔터 = AI 추천 상위 3개."""
    ai_segs = (pick or {}).get("segments") or []
    bounds, off = [], 0.0
    for s0, e0 in segments:
        bounds.append((off, off + e0 - s0))
        off += e0 - s0
    taken, chosen = set(), []
    general = [c for c in cands if not c.get("scene")]
    for k, ((s0, e0), (b0, b1)) in enumerate(zip(segments, bounds)):
        # 이 구간 후보 목록 만들기: AI 순위 → 이 구간 장면 댓글 → 인기 댓글
        items = []
        for it in (ai_segs[k] if k < len(ai_segs) else []):
            try:
                i = int(it["index"])
                at = float(it.get("at", b0))
            except Exception:
                continue
            if 0 <= i < len(cands) and i not in taken and all(i != x[0] for x in items):
                items.append((i, min(max(at, b0), b1 - 1)))
        for i, c in enumerate(cands):
            if len(items) >= 15:
                break
            if i in taken or any(i == x[0] for x in items):
                continue
            stamps = [int(m[0]) * 60 + int(m[1]) for m in P.TS.findall(c.get("raw", ""))]
            if any(s0 - 5 <= t <= e0 + 5 for t in stamps):
                items.append((i, b0 + min(max(stamps[0] - s0, 0), e0 - s0)))
        rot = general[(k * 7) % max(len(general), 1):] + general[:(k * 7) % max(len(general), 1)]
        for c in rot:
            if len(items) >= 15:
                break
            i = cands.index(c)
            if i not in taken and all(i != x[0] for x in items):
                items.append((i, None))
        items = items[:15]
        if not items:
            continue
        print(f"\n💬 [구간 {k + 1}] {_fmt(s0)}~{_fmt(e0)} 댓글 후보 {len(items)}개 (쇼츠 {b0:.0f}~{b1:.0f}초에 표시)")
        for n, (i, at) in enumerate(items, 1):
            c = cands[i]
            mark = "🎯" if c.get("scene") else "  "
            one_line = " / ".join(x.strip() for x in c["text"].splitlines() if x.strip())
            print(f"   {n:2d}. {mark}({c['likes']:>6,}) {one_line[:60]}")
        default = list(range(1, min(3, len(items)) + 1))
        print("   직접 쓰기: + 뒤에 내용 (예: 1,3,+여기서 표정 진짜ㅋㅋ) → 내 계정명(@Gang_pyong)으로 표시돼요")
        while True:
            txt = input(f"✍️  쓸 댓글 번호 (예: 1,4,7 / 엔터 = 추천 {','.join(map(str, default))} / x = 이 구간 댓글 없음): ").strip()
            if txt.lower() == "x":
                sel = []
                break
            try:
                if not txt:
                    sel = default
                    break
                sel = []
                for tok in [t.strip() for t in txt.split(",") if t.strip()]:
                    if tok.startswith("+"):
                        body = tok[1:].strip()
                        assert body
                        sel.append(("own", body))
                    else:
                        n = int(tok)
                        assert 1 <= n <= len(items)
                        sel.append(n)
                break
            except Exception:
                print("   ⚠️ 번호는 쉼표로, 직접 쓰는 댓글은 + 뒤에 써 주세요. (쉼표는 댓글 내용에 쓸 수 없어요)")
        # 표시 시각: AI 추천 시각이 있으면 사용, 없으면 구간 안에 고르게 배치
        span = (b1 - b0) / max(len(sel), 1)
        for j, n in enumerate(sel):
            if isinstance(n, tuple):                 # 직접 쓴 댓글: 기본 프사 + 채널 핸들 + 좋아요 숨김
                chosen.append({"text": n[1], "likes": None, "author": C.MY_CHANNEL_HANDLE,
                               "avatar": "", "own": True, "raw": n[1], "at": b0 + j * span})
                continue
            i, at = items[n - 1]
            c = dict(cands[i])
            c["at"] = at if at is not None else b0 + j * span
            taken.add(i)
            chosen.append(c)
        # 같은 구간 안에서 선택 순서대로 시간이 겹치지 않게 정렬
    chosen.sort(key=lambda c: c["at"])
    for x, y in zip(chosen, chosen[1:]):         # 너무 붙어 있으면 최소 3초 간격
        if y["at"] - x["at"] < 3:
            y["at"] = x["at"] + 3
    return chosen


def _fmt(t):
    t = float(t)
    return f"{int(t // 60)}:{t % 60:04.1f}" if t % 1 else f"{int(t // 60)}:{int(t % 60):02d}"


def _parse_range(txt):
    a_, b_ = txt.replace("~", "-").split("-")
    s0, e0 = P.parse_time(a_.strip()), P.parse_time(b_.strip())
    if e0 <= s0:
        raise ValueError("끝이 시작보다 빨라요")
    return s0, e0


def open_notepad(path):
    try:
        if C.IS_WINDOWS:
            import subprocess
            subprocess.Popen(["notepad.exe", os.path.abspath(path)])
    except Exception:
        pass


def load_and_check_subs(subs_path):
    """자막 파일을 읽고, 한 줄에 안 들어가는 자막이 있으면 알려준 뒤 메모장을 다시 열어 고칠 때까지 반복."""
    import render
    while True:
        try:
            subs = json.load(open(subs_path, encoding="utf-8"))
        except Exception as e:
            import re
            err = str(e)
            raw = open(subs_path, encoding="utf-8").read()
            fixed = re.sub(r"\}(\s*)\{", r"},\1{", raw)          # 빠진 쉼표: } {  → }, {
            fixed = re.sub(r",(\s*)([\]\}])", r"\1\2", fixed)       # 남는 쉼표: , ]  → ]
            try:
                subs = json.loads(fixed)
                open(subs_path, "w", encoding="utf-8").write(fixed)
                print("   🔧 빠지거나 남는 쉼표가 있어서 자동으로 고쳤어요")
            except Exception:
                subs = None
        if subs is None:
            print(f"   ⚠️ 자막 파일 형식이 깨졌어요 ({err}). 따옴표·쉼표를 확인하고 다시 저장해 주세요.")
            open_notepad(subs_path)
            input("   고쳤으면 엔터: ")
            continue
        if getattr(C, "SFX", True):
            import sfx
            lib = sfx.library()
            for x in subs:
                if x.get("sfx"):
                    nm = sfx.normalize_name(x["sfx"])
                    if nm not in lib:
                        print(f"   ⚠️ {x['start']:.1f}초 효과음 '{x['sfx']}'은 없는 이름이라 빼요 (가능: {', '.join(lib)})")
                        nm = ""
                    x["sfx"] = nm
        long_ones = []
        for i, x in enumerate(subs):
            over = render.subtitle_overflow(x.get("text", ""), x.get("context", ""))
            if over:
                long_ones.append((i, x, over))
        if not long_ones:
            print("   ✔ 모든 자막이 한 줄에 들어가요")
            return subs
        print(f"\n   ⚠️ 두 줄로 넘어가는 자막이 {len(long_ones)}개 있어요. 글자를 줄여 주세요:")
        for i, x, over in long_ones:
            cut = max(1, round(len(x.get("text", "")) * (1 - 1 / over)) + 1)
            ctx = f"  (상황: {x.get('context')})" if x.get("context") else ""
            print(f"      {x['start']:5.1f}초  \"{x.get('text', '')}\"{ctx}  → 약 {cut}글자 줄이기")
        print("   메모장을 다시 열게요. 고친 뒤 저장(Ctrl+S)하세요.")
        open_notepad(subs_path)
        input("   [v] 다 고쳤으면 엔터: ")


def parse_segments(txt):
    """'4:32-4:54, 5:21-5:42' 또는 '3:10 3:52' 형식을 [(시작초, 끝초), ...]로."""
    txt = txt.strip()
    if "-" not in txt and "~" not in txt:
        parts = txt.replace(",", " ").split()
        if len(parts) % 2:
            raise ValueError("시작과 끝을 짝지어 입력해 주세요")
        return [(P.parse_time(parts[i]), P.parse_time(parts[i + 1])) for i in range(0, len(parts), 2)]
    return sorted(_parse_range(p_) for p_ in txt.split(",") if p_.strip())


def review_segments(segs, first):
    """구간을 보여주고 수정받음. first=False(완성 후)일 땐 엔터 = 끝(None 반환)."""
    while True:
        print("\n🎬 " + ("AI가 고른 구간" if first else "현재 구간"))
        for i, (s0, e0) in enumerate(segs, 1):
            print(f"   [{i}] {_fmt(s0)} ~ {_fmt(e0)}  ({e0 - s0:.0f}초)")
        print("   수정 예) 1=0:35-0:50   /   여러 개: 1=0:35-0:50, 3=25:38-26:01")
        print("           구간 삭제: 2=x   /   구간 추가: +=31:10-31:25   /   통째로: 0:35-0:50, 15:02-15:16")
        if first:
            print("✂️  수정할 내용 입력 (엔터 = 그대로 진행)")
            txt = input("> ").strip()
            if not txt:
                return segs
        else:
            print("✂️  다시 수정할까요? 수정 내용 입력 (엔터 = 완료 / 제목만 바꾸기: 제목 새 제목)")
            txt = input("> ").strip()
            if not txt:
                return None
            if txt.startswith("제목"):
                return ("title", txt[2:].lstrip(" :=").strip())
        try:
            new = list(segs)
            parts = [p_.strip() for p_ in txt.split(",") if p_.strip()]
            if all("=" not in p_ for p_ in parts):
                new = [_parse_range(p_) for p_ in parts]
            else:
                drop = set()
                for p_ in parts:
                    key, val = [x.strip() for x in p_.split("=", 1)]
                    if key == "+":
                        new.append(_parse_range(val))
                    elif val.lower() == "x":
                        drop.add(int(key) - 1)
                    else:
                        new[int(key) - 1] = _parse_range(val)
                new = [sg for i, sg in enumerate(new) if i not in drop]
            if not new:
                raise ValueError("구간이 하나도 없어요")
            segs = sorted(new)
            first = True          # 수정 후 한 번 더 확인
            print("   ✔ 반영했어요. 확인 후 엔터를 누르면 진행해요.")
        except Exception as e:
            print(f"   ⚠️ 형식을 이해하지 못했어요 ({e}). 다시 입력해 주세요.")


def ask_yn(q):
    """엔터=예 / n=아니오"""
    return input(f"   {q} [엔터=넣기 / n=빼기]: ").strip().lower() not in ("n", "no", "ㄴ", "x", "빼")


def ask_reuse_subs(subs_path):
    """이전에 만든 자막(대본)이 있으면 미리보기 보여주고 재사용할지 물어봄."""
    import time
    try:
        old = json.load(open(subs_path, encoding="utf-8"))
    except Exception:
        print("   (이전 자막 파일이 깨져 있어서 새로 만들게요)")
        return False
    when = time.strftime("%m/%d %H:%M", time.localtime(os.path.getmtime(subs_path)))
    print(f"\n📄 이 구간으로 만든 자막이 이미 있어요 ({len(old)}줄, {when} 저장)")
    for x in old[:4]:
        ctx = f"  [{x['context']}]" if x.get("context") else ""
        print(f"   {_fmt(x.get('start', 0))}  {x.get('text', '')}{ctx}")
    if len(old) > 4:
        print(f"   ... 외 {len(old) - 4}줄")
    ans = input("   이 자막 그대로 쓸까요? [엔터=재사용 / n=새로 받아쓰기]: ").strip().lower()
    return ans not in ("n", "no", "ㅜ", "아니", "새로")


def reset_subs(subs_path):
    """기존 자막은 백업해 두고, 교정·상황자막 표시도 지워서 처음부터 다시 만들게 함."""
    import time
    backup = subs_path.replace(".json", f"_이전_{time.strftime('%m%d_%H%M%S')}.json")
    os.replace(subs_path, backup)
    for m in (".fixed", ".context", ".sfx"):
        if os.path.exists(subs_path + m):
            os.remove(subs_path + m)
    print(f"   🔄 새로 받아쓸게요 (이전 자막은 {os.path.basename(backup)} 로 보관)")


def make_one(a, segments, title_hint, meta_hint):
    a = argparse.Namespace(**vars(a))
    a.title = title_hint
    start, end = segments[0][0], segments[-1][1]
    total = sum(e0 - s0 for s0, e0 in segments)
    if total > 60:
        print(f"⚠️  길이가 {total:.0f}초예요. 쇼츠는 60초 이하가 안전해요(최대 3분까지 허용).")
    vid = P.video_id_from_url(a.url) or "local"
    tag = str(int(start)) if len(segments) == 1 else "mix_" + "_".join(f"{int(s0)}-{int(e0)}" for s0, e0 in segments)
    work = os.path.join("output", f"{vid}_{tag}")
    os.makedirs(work, exist_ok=True)
    clip = os.path.join(work, "clip.mp4")

    # ① 원본 구간 확보
    meta = {"title": "", "channel": C.SOURCE_CHANNEL, **meta_hint}
    if a.input:
        print("① 로컬 파일에서 구간 자르는 중...")
        P.cut_local(a.input, start, end, clip)
        P.trim_silence(clip)
    elif not os.path.exists(clip):
        print(f"① 원본 구간 {len(segments)}개 다운로드 중...")
        parts = []
        cache = os.path.join("output", f"{vid}_segments")
        os.makedirs(cache, exist_ok=True)
        source = os.path.join(cache, "source.mp4")
        if not os.path.exists(source):
            print("   원본 영상 전체를 받는 중... (처음 한 번만, 영상 길이에 따라 몇 분)")
        m = P.download_source(a.url, source)
        meta = {**meta, **{kk: v for kk, v in m.items() if v}}
        for k, (s0, e0) in enumerate(segments):
            pth = os.path.join(cache, f"{s0:.1f}_{e0:.1f}.mp4")
            if not os.path.exists(pth):
                P.cut_local(source, s0, e0, pth)
            parts.append(pth)
        P.concat_clips(parts, clip)
        P.trim_silence(clip)
    else:
        print("① 이미 받은 클립 재사용")

    # ② 자막
    subs = []
    if a.burn_subs:
        subs_path = a.subs or os.path.join(work, "subs.json")
        if os.path.exists(subs_path) and not a.subs and not ask_reuse_subs(subs_path):
            reset_subs(subs_path)
        fresh = False
        if os.path.exists(subs_path):
            print(f"② 자막 불러오기: {subs_path}")
            subs = json.load(open(subs_path, encoding="utf-8"))
        else:
            fresh = True
            words_path = os.path.join(work, "words.json")
            if os.path.exists(words_path):
                print("② 받아쓰기 결과 재사용 (줄 나누기·교정만 새로 해요)")
                words = [tuple(w) for w in json.load(open(words_path, encoding="utf-8"))]
            else:
                print(f"② Whisper({C.WHISPER_MODEL})로 받아쓰는 중... (처음엔 모델 다운로드로 오래 걸려요)")
                words = P.transcribe_words(clip, hint=meta.get("title", ""))
                json.dump(words, open(words_path, "w", encoding="utf-8"), ensure_ascii=False)
            ref = P.youtube_caption_text(vid, segments) if vid != "local" else ""
            subs, ok = P.ai_segment(words, meta.get("title", ""), reference=ref)
            if ok:
                open(subs_path + ".fixed", "w").close()     # 교정까지 끝난 상태
            else:
                subs = P.chunk_words(words)
            json.dump(subs, open(subs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"   → {len(subs)}개 자막 저장: {subs_path}")

        marker = subs_path + ".fixed"
        if not os.path.exists(marker):
            ref = P.youtube_caption_text(vid, segments) if vid != "local" else ""
            subs, ok = P.fix_subs(subs, meta.get("title", ""), reference=ref)
            if ok:
                json.dump(subs, open(subs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                open(marker, "w").close()

        ctx_marker = subs_path + ".context"
        if getattr(C, "CONTEXT_CAPTIONS", True) and not os.path.exists(ctx_marker):
            if ask_yn("초록 상황 자막 넣을까요?"):
                subs, ok = P.add_context_captions(subs, meta.get("title", ""))
                if ok:
                    json.dump(subs, open(subs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    open(ctx_marker, "w").close()
            else:
                open(ctx_marker, "w").close()      # 이 구간에선 다시 묻지 않음

        sfx_marker = subs_path + ".sfx"
        use_sfx = getattr(C, "SFX", True)
        if use_sfx and not os.path.exists(sfx_marker):
            if ask_yn("효과음 넣을까요?"):
                subs, ok = P.add_sfx(subs, meta.get("title", ""))
                if ok:
                    json.dump(subs, open(subs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    open(sfx_marker, "w").close()
            else:
                use_sfx = False
                open(sfx_marker, "w").close()
                for x in subs:
                    x.pop("sfx", None)             # 예전에 넣어둔 효과음도 이번엔 빼고 감

        # 한 줄 넘는 자막 자동 분할 (교정·상황자막 이후)
        new_subs = P.fix_short_subs(P.split_long_subs(subs), merge=False)
        if new_subs != subs:
            subs = new_subs
            json.dump(subs, open(subs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        auto_path = subs_path.replace(".json", ".auto.json")   # AI가 만든 원본 (학습 비교용)
        if fresh or not os.path.exists(auto_path):
            json.dump(subs, open(auto_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        # 직접 고치기: 메모장으로 열고 기다림
        print(f"\n📝 자막 확인: {subs_path}")
        try:
            if C.IS_WINDOWS:
                import subprocess
                subprocess.Popen(["notepad.exe", os.path.abspath(subs_path)])
                print("   메모장이 열렸어요. \"text\"(메인 자막)·\"context\"(초록 상황 자막)를 고치고 저장(Ctrl+S)하세요.")
                print("   상황 자막을 빼려면 \"context\": \"\" 처럼 비워두면 돼요.")
                if getattr(C, "SFX", True):
                    import sfx
                    print(f"   \"sfx\"(효과음): {' / '.join(sfx.library())} 중 하나, 빼려면 \"\"")
        except Exception:
            pass
        input("   [v] 다 고쳤으면 엔터 (고칠 게 없으면 그냥 엔터): ")
        subs = load_and_check_subs(subs_path)
        subs = P.fix_short_subs(subs, min_dur=0.25, merge=False)   # 내가 고친 건 존중, 거의 0초짜리만 보정
        try:
            import sub_style
            auto = json.load(open(auto_path, encoding="utf-8"))
            sub_style.record_edit(os.path.basename(work), auto, subs, title=meta.get("title", ""))
        except Exception:
            pass

    else:
        print("② 자막 생략")

    # ③ 댓글 후보
    cands = []
    if not a.no_comment and not a.comment and vid != "local":
        try:
            print("③ 댓글 가져오는 중...")
            cands = P.candidate_comments(P.fetch_comments(vid), segments=segments,
                                         used=P.load_used(vid))
            cands = P.limit_keyword_dupes(cands, max_per=3)
            print(f"   댓글 후보 {len(cands)}개 확보 (🎯 장면 언급 {sum(1 for c in cands if c.get('scene'))}개)")
        except Exception as e:
            print(f"   댓글 단계 건너뜀: {e}")

    # ④ 제목·댓글 결정
    title = a.title
    comment = None
    pick = None
    n_slots = max(1, min(10, round(total / 6)))   # 평균 6초마다 댓글 1개
    if C.ANTHROPIC_API_KEY:
        script = subs
        if not script and vid != "local":
            script = P.youtube_caption_lines(vid, segments)
            if not script:                       # 구간을 직접 지정해서 아직 유튜브 자막을 안 받은 경우
                try:
                    import highlight
                    print("   대사 확인용 유튜브 자막 받는 중...")
                    highlight.captions(a.url, os.path.join("output", "_analysis", vid))
                    script = P.youtube_caption_lines(vid, segments)
                except Exception as e:
                    print(f"   (유튜브 자막을 못 받아서 대사 없이 댓글을 골라요: {str(e)[:60]})")
        if script:
            print(f"   대사 {len(script)}줄을 보고 구간에 맞는 댓글을 골라요")
        if cands:
            pick = P.ai_rank_per_segment(script, cands, segments, meta.get("title", ""))
        else:
            pick = P.ai_pick(script, cands, meta.get("title", ""), n_comments=n_slots, total=total)
        if pick:
            print(f"④ AI 추천 제목: {pick.get('title')}")

    comment = None
    if a.comment:
        comment = {"text": a.comment, "likes": a.likes}
    elif cands:
        chosen = choose_comments_per_segment(cands, segments, pick, a)
        comment = P.schedule_comments(chosen, segments, slot=0.1, max_n=100)
        for c in comment:
            if not c.get("own"):
                P.mark_used(vid, c.get("raw", c["text"]))
            print(f"   💬 {c['start']:4.1f}~{c['end']:4.1f}초: {c['text'][:40]}")

    # 제목은 항상 직접 입력 (엔터만 치면 AI 추천 제목 사용)
    if a.cli_title:
        title = a.cli_title
    else:
        sug = title or (pick or {}).get("title") or ""
        print("\n" + "─" * 50)
        if sug:
            print(f"  AI 추천 제목: {sug}")
        print("  줄바꿈 위치를 정하고 싶으면 / 를 넣어요 (예: 수능장에서 억울하게/경고받은 이유)")
        print("✍️  쇼츠 제목 입력 (엔터 = 추천 제목 사용)")
        typed = input("> ").strip()
        title = typed or sug
        while not title:
            title = input("> 제목을 입력해 주세요: ").strip()
        print("─" * 50)
    # ⑤ 합성
    out = os.path.join(work, "short.mp4")
    print("⑤ 영상 합성 중...")
    P.compose(clip, title, subs, comment, out, os.path.join(work, "_layers"))

    lines = [f"출처: {C.SOURCE_CHANNEL} 채널", f"원본 영상: {a.url}"]
    if meta.get("title"):
        lines.append(f"원제: {meta['title']}")
    desc = "\n".join(lines) + f"\n\n#shorts #{C.SOURCE_CHANNEL}"
    json.dump({"title": title, "description": desc.strip(), "file": out, "source_url": a.url},
              open(os.path.join(work, "upload.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✅ 완성: {out}\n   업로드: python upload.py {os.path.join(work, 'upload.json')}")
    print_source_comment(a.url, vid, meta, work)
    return title


def print_source_comment(url, vid, meta, work):
    """고정 댓글로 달 출처 문구를 출력 + 파일 저장 + 클립보드 복사."""
    title = meta.get("title", "")
    if not title and vid != "local":
        try:
            import highlight
            title = highlight.video_info(url).get("title", "")
        except Exception:
            title = ""
    link = f"https://www.youtube.com/watch?v={vid}" if vid != "local" else url
    text = "원본:\n" + (f"{title}\n" if title else "") + link
    with open(os.path.join(work, "source_comment.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    copied = False
    try:
        import subprocess
        subprocess.run("clip", input=text.encode("utf-16"), check=True, shell=True)
        copied = True
    except Exception:
        pass
    print("\n📋 출처 댓글 (복사해서 고정 댓글로 달기)" + (" — 클립보드에 복사됨, Ctrl+V 하면 돼요" if copied else ""))
    print("=" * 50)
    print(text)
    print("=" * 50)


def double_click_mode():
    """더블클릭으로 실행했을 때: 링크를 물어보고, 끝나면(에러여도) 창을 바로 닫지 않음."""
    print("=" * 50)
    print("  쇼츠 자동 제작기")
    print("=" * 50)
    print("유튜브 링크를 붙여넣고 엔터를 누르세요.")
    url = input("URL: ").strip().strip('"')
    if not url:
        return
    sys.argv = [sys.argv[0], url]      # 구간은 AI 분석 후 후보를 보고 고름
    main()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):          # 윈도우 콘솔 한글/이모지 깨짐 방지
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    os.chdir(os.path.dirname(os.path.abspath(__file__)))   # 어디서 실행해도 이 폴더 기준
    if len(sys.argv) == 1:
        try:
            double_click_mode()
        except Exception:
            import traceback
            traceback.print_exc()
            print("\n❌ 에러가 났어요. 위 내용을 캡처해서 보내주세요.")
        input("\n엔터를 누르면 창이 닫혀요...")
    else:
        sys.exit(main())
