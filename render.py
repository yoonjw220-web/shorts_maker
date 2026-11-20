"""Pillow로 제목·자막·댓글 카드 PNG를 그리는 모듈."""
from PIL import Image, ImageDraw, ImageFont
import config as C


def _font(bold, size):
    path = C.FONT_BOLD if bold else C.FONT_REGULAR
    if not path:
        raise RuntimeError("한국어 폰트를 못 찾았어요. config.py의 FONT_BOLD / FONT_REGULAR를 지정해 주세요.")
    return ImageFont.truetype(path, size, index=C.font_index(path))


def _wrap(draw, text, font, max_w):
    """글자 단위로 줄바꿈 (한국어는 띄어쓰기 기준보다 이게 자연스러움). 단어는 가능하면 안 끊음."""
    lines, cur = [], ""
    for word in text.replace("\n", " \n ").split(" "):
        if word == "\n":
            lines.append(cur.strip()); cur = ""; continue
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            # 단어 자체가 너무 길면 글자 단위로 자름
            cur = ""
            for ch in word:
                if draw.textlength(cur + ch, font=font) > max_w:
                    lines.append(cur); cur = ch
                else:
                    cur += ch
    if cur:
        lines.append(cur)
    return [l for l in lines if l]


def render_title(title, out_path):
    """흰 배경 + 내 채널명 + 굵은 제목(최대 2줄, 자동 크기 조절)."""
    top, bottom = C.TITLE_AREA
    img = Image.new("RGB", (C.W, bottom - top), "white")
    d = ImageDraw.Draw(img)

    # '/'를 넣으면 그 위치에서 줄바꿈
    title = "\n".join(part.strip() for part in title.split("/"))
    # 제목: 2줄에 들어갈 때까지 글자 크기를 줄임
    size = 96
    while size > 50:
        f = _font(True, size)
        lines = _wrap(d, title, f, C.W - 100)
        if len(lines) <= 2:
            break
        size -= 4
    lines = lines[:2]
    line_h = int(size * 1.18)
    y = img.height - 40 - line_h * len(lines)
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((C.W - w) / 2, y), line, font=f, fill="black", stroke_width=1, stroke_fill="black")
        y += line_h

    if C.MY_CHANNEL_NAME:
        cf = _font(True, 42)
        cy = img.height - 40 - line_h * len(lines) - 90
        # 로고 + 채널명: 왼쪽 정렬 (레퍼런스 스타일)
        logo_sz, gap = 86, 18
        x0 = 56
        import os
        if getattr(C, "LOGO_PATH", None) and os.path.exists(C.LOGO_PATH):
            logo = Image.open(C.LOGO_PATH).convert("RGBA").resize((logo_sz, logo_sz), Image.LANCZOS)
            img.paste(logo, (int(x0), int(cy - 20)), logo)
        else:
            d.ellipse((x0, cy - 20, x0 + logo_sz, cy - 20 + logo_sz), fill="#ff4d6d")
        d.text((x0 + logo_sz + gap, cy), C.MY_CHANNEL_NAME, font=cf, fill="black")
    img.save(out_path)


def _sub_font(size):
    """예능 자막용 굵은 폰트(Black Han Sans, 무료 OFL). 없으면 처음 한 번 자동 다운로드."""
    import os, urllib.request
    path = getattr(C, "SUB_FONT", None)
    if path and not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            print("   (자막 폰트 처음 한 번 다운로드 중...)")
            urllib.request.urlretrieve(C.SUB_FONT_URL, path)
        except Exception:
            path = None
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    return _font(True, size)


SUB_MAX_W = None


def subtitle_max_width():
    """자막 한 줄에 쓸 수 있는 최대 글자 폭(px). 박스·테두리 여백을 뺀 값."""
    return C.W - 100


def subtitle_overflow(text, context=""):
    """한 줄에 안 들어가면 (넘친 비율) 반환, 들어가면 0."""
    size = getattr(C, "SUB_SIZE", 82)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    worst = 0.0
    if text:
        w = tmp.textlength(text, font=_sub_font(size))
        worst = max(worst, w / subtitle_max_width())
    ctx = (context or "").strip()
    if ctx:
        if not ctx.startswith("("):
            ctx = f"({ctx})"
        cw = tmp.textlength(ctx, font=_sub_font(int(getattr(C, "CONTEXT_SIZE", 0) or size * getattr(C, "CONTEXT_SIZE_RATIO", 0.62))))
        worst = max(worst, cw / subtitle_max_width())
    return worst if worst > 1.0 else 0.0


def subtitle_height(text, context=""):
    """합성에 쓰는 자막 레이어와 같은 높이(px). 얼굴 가림 검사에서도 공유."""
    size = getattr(C, "SUB_SIZE", 82)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = _wrap(draw, text, _sub_font(size), C.W - 100)[:2] if text else []
    csize = int(getattr(C, "CONTEXT_SIZE", 0) or size * getattr(C, "CONTEXT_SIZE_RATIO", 0.62))
    return int(size * 1.15) * len(lines) + (int(csize * 1.2) if (context or "").strip() else 0) + 20


def render_subtitle(text, out_path, index=0, context=""):
    """예능 자막: 굵은 글씨 + 검은 테두리(+검은 박스). context가 있으면 위에 초록 상황 설명 한 줄."""
    from PIL import ImageFilter
    size = getattr(C, "SUB_SIZE", 82)
    stroke = getattr(C, "SUB_STROKE", 9)
    colors = getattr(C, "SUB_COLORS", ["#FFFFFF"])
    color = colors[index % len(colors)]
    f = _sub_font(size)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = _wrap(tmp, text, f, C.W - 100)[:2] if text else []
    line_h = int(size * 1.15)
    pad_x, pad_y = 22, 10
    box = getattr(C, "SUB_BOX", False)

    context = (context or "").strip()
    if context and not context.startswith("("):
        context = f"({context})"
    csize = int(getattr(C, "CONTEXT_SIZE", 0) or size * getattr(C, "CONTEXT_SIZE_RATIO", 0.62))
    cf = _sub_font(csize)
    c_h = int(csize * 1.2) if context else 0

    h = subtitle_height(text, context)
    layer = Image.new("RGBA", (C.W, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (C.W, h), (0, 0, 0, 0))
    d, sd = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    if box:   # 글자 뒤 검은 박스 (상황 설명 줄까지 감싸도록)
        widths = [d.textlength(l, font=f) for l in lines] + ([d.textlength(context, font=cf)] if context else [])
        bw = max(widths) + stroke * 2 + pad_x * 2
        bw = max(bw, C.W * getattr(C, "SUB_BOX_MIN_W", 0.80))   # 짧은 자막도 박스는 길게 (원본 자막 가리기)
        bx = (C.W - bw) / 2
        d.rectangle((bx, 0, bx + bw, h), fill=(0, 0, 0, 255))
    y = pad_y
    if context:
        w = d.textlength(context, font=cf)
        d.text(((C.W - w) / 2, y + csize * 0.05), context, font=cf, fill=getattr(C, "CONTEXT_COLOR", "#4DFF6A"),
               stroke_width=max(3, stroke - 2), stroke_fill="black")
        y += c_h
    for l in lines:
        w = d.textlength(l, font=f)
        x = (C.W - w) / 2
        if not box:
            sd.text((x + 6, y + 7), l, font=f, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        d.text((x, y + size * 0.07), l, font=f, fill=color, stroke_width=stroke, stroke_fill="black")
        y += line_h
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    img = Image.alpha_composite(shadow, layer)
    if getattr(C, "SUB_ITALIC", False):
        img = img.transform(img.size, Image.AFFINE, (1, 0.18, -0.09 * h, 0, 1, 0), resample=Image.BICUBIC)
    img.save(out_path)
    return h


def _clean(text):
    """폰트에 없는 이모지·특수기호 제거 (□로 깨지는 것 방지)."""
    import re
    text = re.sub(r"[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F\u200D]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _short_count(n):
    if n >= 10000:
        v = n / 10000
        return (f"{v:.1f}".rstrip("0").rstrip(".")) + "만"
    if n >= 1000:
        v = n / 1000
        return (f"{v:.1f}".rstrip("0").rstrip(".")) + "천"
    return str(n)


def _thumb(d, x, y, s, down=False, color="#0f0f0f", w=3):
    """유튜브 스타일 엄지 아이콘(외곽선). (x,y)=좌상단, s=크기."""
    def P(px, py):
        return (x + px * s, y + (1 - py if down else py) * s)
    # 손목 부분
    d.rectangle([P(0.05, 0.45), P(0.27, 0.95)] if not down else [P(0.05, 0.55), P(0.27, 0.05)],
                outline=color, width=w)
    # 손바닥 + 엄지
    pts = [P(0.27, 0.45), P(0.48, 0.12), P(0.55, 0.02), P(0.66, 0.08), P(0.62, 0.38),
           P(0.92, 0.38), P(0.98, 0.48), P(0.88, 0.95), P(0.27, 0.95)]
    d.line(pts + [pts[0]], fill=color, width=w, joint="curve")


def render_comment_clean(text, likes, out_path, author="", own=False):
    """예능잼잼 스타일: 프사·닉네임 없이 댓글 내용 + 👍 숫자 · 답글, 가운데 정렬."""
    text = " ".join(x.strip() for x in _clean(text).splitlines() if x.strip())
    f, small, bold = _font(False, 40), _font(False, 30), _font(True, 30)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = _wrap(tmp, text, f, C.W - 160)[:2]
    head = 44 if own and author else 0
    line_h = 56
    h = head + line_h * len(lines) + 70
    img = Image.new("RGBA", (C.W, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    y = 0
    if head:   # 직접 쓴 댓글은 작성자(내 계정) 표시
        w = d.textlength(author, font=bold)
        d.text(((C.W - w) / 2, 0), author, font=bold, fill="#0f0f0f")
        y = head
    # 글 블록 전체는 가운데, 줄은 왼쪽 정렬 (예능잼잼 스타일)
    block_left = min((C.W - d.textlength(l, font=f)) / 2 for l in lines)
    for l in lines:
        d.text((block_left, y), l, font=f, fill="#111111")
        y += line_h
    # 👍 숫자 · 답글: 댓글 글 블록의 왼쪽 끝에 맞춰 정렬 (예능잼잼 스타일)
    y += 10
    x = block_left
    cnt = "" if likes is None else _short_count(likes)
    _thumb(d, x, y, 30)
    x += 34
    if cnt:
        d.text((x + 12, y - 3), cnt, font=small, fill="#606060")
        x += 12 + d.textlength(cnt, font=small)
    d.text((x + 50, y - 3), "답글", font=small, fill="#0f0f0f")
    img.save(out_path)
    return h


def render_comment_simple(text, out_path, author="", own=False):
    """레퍼런스 스타일: 회색 동그라미 + 댓글 한 줄, 가운데 정렬. 길면 글자를 줄이고, 그래도 길면 … 처리."""
    text = " ".join(x.strip() for x in _clean(text).splitlines() if x.strip())
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    prefix = (author + "  ") if own and author else ""
    dot, gap, maxw = 40, 16, C.W - 140
    size = 40
    while True:
        f, nf = _font(False, size), _font(True, size - 4)
        tw = tmp.textlength(prefix, font=nf) + tmp.textlength(text, font=f)
        if dot + gap + tw <= maxw or size <= 30:
            break
        size -= 2
    while dot + gap + tmp.textlength(prefix, font=nf) + tmp.textlength(text, font=f) > maxw and len(text) > 2:
        text = text[:-2].rstrip() + "…"
    h = 70
    content_w = dot + gap + tmp.textlength(prefix, font=nf) + tmp.textlength(text, font=f)
    x0 = (C.W - content_w) / 2
    img = Image.new("RGBA", (C.W, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((x0, (h - dot) / 2, x0 + dot, (h + dot) / 2), fill="#d0d0d0")
    tx = x0 + dot + gap
    ty = (h - size) / 2 - 6
    if prefix:
        d.text((tx, ty + 2), prefix, font=nf, fill="#0f0f0f")
        tx += d.textlength(prefix, font=nf)
    d.text((tx, ty), text, font=f, fill="#111111")
    img.save(out_path)
    return h


def render_comment(text, likes, out_path, author="", avatar_path=None, own=False):
    """유튜브 댓글처럼: (흐린) 프로필·닉네임 + 본문 + 👍 숫자 👎 답글."""
    from PIL import ImageFilter
    text = _clean(text)
    f = _font(False, 42)
    meta_f = _font(False, 30)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, av = 170, 76
    lines = _wrap(tmp, text, f, C.W - left - 70)[:3]
    line_h = 60
    h = 60 + line_h * len(lines) + 90
    img = Image.new("RGBA", (C.W, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)

    # 프로필 사진 (실제 사진이면 흐리게, 없으면 기본 아이콘)
    ax, ay = left - av - 22, 4
    mask = Image.new("L", (av, av), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, av - 1, av - 1), fill=255)
    pic = None
    if avatar_path:
        try:
            pic = Image.open(avatar_path).convert("RGB").resize((av, av)).filter(ImageFilter.GaussianBlur(6))
        except Exception:
            pic = None
    if pic is None:
        pic = Image.new("RGB", (av, av), "#c7c7c7")
        pd = ImageDraw.Draw(pic)
        pd.ellipse((av * .3, av * .18, av * .7, av * .58), fill="#efefef")
        pd.pieslice((av * .12, av * .6, av * .88, av * 1.3), 180, 360, fill="#efefef")
    img.paste(pic, (ax, ay), mask)

    # 닉네임 (흐리게 처리해서 개인정보 보호)
    name = _clean(author) or "@user"
    nl = Image.new("RGBA", (600, 50), (255, 255, 255, 0))
    nd = ImageDraw.Draw(nl)
    if own:   # 내가 직접 쓴 댓글: 채널 핸들을 또렷하게 표시
        nd.text((0, 6), name[:30], font=_font(True, 30), fill="#0f0f0f")
    else:     # 시청자 댓글: 닉네임 흐리게
        nd.text((0, 6), name[:18] + "  ·  1일 전", font=meta_f, fill="#606060")
        nl = nl.filter(ImageFilter.GaussianBlur(5))
    img.alpha_composite(nl, (left, 0))

    y = 52
    for l in lines:
        d.text((left, y), l, font=f, fill="#0f0f0f")
        y += line_h

    # 👍 숫자  👎  답글
    y += 14
    _thumb(d, left, y, 34)
    if likes is None:          # 좋아요 수 숨김
        cx = left + 70
    else:
        d.text((left + 50, y - 2), _short_count(likes), font=meta_f, fill="#606060")
        cx = left + 50 + d.textlength(_short_count(likes), font=meta_f) + 60
    d.text((cx, y - 2), "답글", font=_font(True, 30), fill="#0f0f0f")
    # 내용만 잘라서 가로 가운데 정렬 (레퍼런스처럼 영상 바로 아래 깔끔하게)
    from PIL import ImageChops
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    diff = ImageChops.difference(img.convert("RGB"), bg.convert("RGB")).convert("L")
    bbox = diff.point(lambda v: 255 if v > 24 else 0).getbbox()
    if bbox:
        part = img.crop((bbox[0], 0, bbox[2], h))
        img = Image.new("RGBA", (C.W, h), (255, 255, 255, 255))
        img.paste(part, ((C.W - part.width) // 2, 0))
    img.save(out_path)
    return h


def render_source(text, out_path):
    f = _font(True, 34)
    img = Image.new("RGBA", (C.W, 52), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = d.textlength(text, font=f)
    d.text(((C.W - w) / 2, 4), text, font=f, fill="#555555")   # 아래 가운데
    img.save(out_path)
