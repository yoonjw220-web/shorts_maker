# 쇼츠 자동 제작기 (미미미누 편)

URL과 구간만 넣으면 **제목 + 자막 + 베스트 댓글 + 출처 표기**가 들어간 1080×1920 쇼츠가 나와요.

```
원본 URL ─▶ ① 구간 다운로드(yt-dlp) ─▶ ② 받아쓰기(Whisper) ─▶ ③ 댓글 수집(YouTube API)
         ─▶ ④ 제목·댓글 선택(직접 or Claude) ─▶ ⑤ 합성(ffmpeg) ─▶ ⑥ 비공개 업로드
```

## 1. 설치 (처음 한 번)

1. **Python 3.10+** 설치
2. **ffmpeg** 설치
   - Windows: `winget install ffmpeg` 후 터미널 재시작
   - Mac: `brew install ffmpeg`
3. 이 폴더에서:
   ```
   pip install -r requirements.txt
   ```

## 2. API 키 (댓글·업로드용)

- **YouTube Data API 키** (댓글 가져오기)
  Google Cloud Console → 새 프로젝트 → "YouTube Data API v3" 사용 설정 → 사용자 인증 정보 → API 키 만들기
- **OAuth 클라이언트** (업로드)
  같은 프로젝트 → OAuth 동의 화면(테스트 사용자에 내 계정 추가) → 사용자 인증 정보 → OAuth 클라이언트 ID(데스크톱 앱) → JSON 받아서 이 폴더에 `client_secret.json`으로 저장
- **Anthropic API 키** (선택: 제목 자동 작성 + 어울리는 댓글 고르기)

키는 환경변수로 넣어줘요:
```
# Windows (PowerShell)
$env:YOUTUBE_API_KEY="..."; $env:ANTHROPIC_API_KEY="..."
# Mac
export YOUTUBE_API_KEY=...; export ANTHROPIC_API_KEY=...
```

## 3. 사용법

**링크만 (구간 자동 선택):**
```
python make_short.py "https://youtu.be/영상ID"
python make_short.py "https://youtu.be/영상ID" --pick 1   # 두 번째 후보로
python make_short.py "https://youtu.be/영상ID" --all      # 후보 전부 제작
```
유튜브 자막(대본)과 '가장 많이 다시 본 구간' 그래프를 보고 구간을 골라요.
ANTHROPIC_API_KEY가 있으면 Claude가 대본을 읽고 후보 3개와 제목을 정하고,
없으면 다시 보기가 가장 많은 45초를 골라요. 말이 중간에 끊기지 않게 자막 문장 경계에 맞춰요.

**구간 직접 지정:**
```
python make_short.py "https://youtu.be/영상ID" --start 3:10 --end 3:52
```
- 제목을 직접 정하려면 `--title "수능장에서 억울하게 경고받은 이유"`
- 댓글 후보 목록이 출력되면, 다른 걸 쓰고 싶을 때 `--comment-index 3`
- 결과물: `output/영상ID_시작초/short.mp4`

**자막 오타 고치기:** 같은 폴더의 `subs.json`을 메모장으로 고친 뒤 같은 명령을 다시 실행하면, 받아쓰기는 건너뛰고 고친 자막으로 다시 합성해요.

**업로드 (비공개):**
```
python upload.py output/영상ID_190/upload.json
python upload.py output/영상ID_190/upload.json --publish-at 2026-11-12T18:00:00+09:00
```
API로 올린 영상은 구글의 API 감사를 통과하기 전까지 비공개로만 올라갈 수 있어요. 스튜디오에서 확인하고 직접 공개로 바꾸면 돼요.

## 4. 스타일 바꾸기 → `config.py`

| 항목 | 설명 |
|---|---|
| `MY_CHANNEL_NAME` | 제목 위 내 채널명 |
| `SOURCE_CHANNEL` | 출처 표기 |
| `WHISPER_MODEL` | `small`(빠름) ~ `large-v3`(정확). GPU 없으면 `small`이나 `medium` 추천 |
| `FONT_BOLD` | 예: 다운받은 Pretendard/G마켓산스 경로 |
| `*_AREA`, `*_Y` | 레이아웃 위치 |

## 5. 꼭 기억할 것

- **허락:** 출처를 적는 건 허락을 받는 것과 달라요. 채널 비즈니스 메일로 클립 허용 여부를 먼저 확인하세요.
- **수익화:** 자막과 댓글만 붙인 클립은 YouTube 재사용 콘텐츠 정책상 수익 창출이 거절될 수 있어요. 제목이나 해설에 내 관점을 넣으세요.
- 댓글 작성자의 닉네임과 프로필은 일부러 넣지 않아요.
