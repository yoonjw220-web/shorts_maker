"""설정 파일 — 여기 값만 바꾸면 채널 스타일이 바뀜."""
import os
import platform

# ── 내 채널 정보 ─────────────────────────────────────────
MY_CHANNEL_NAME = "에듀갤러리"      # 제목 위에 작게 들어가는 내 채널명 (빈 문자열이면 생략)
MY_CHANNEL_HANDLE = "@Gang_pyong"   # 직접 쓴 댓글에 표시되는 작성자 (내 유튜브 계정명)
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")  # 채널명 옆 동그란 로고
SOURCE_CHANNEL = "미미미누"          # 영상 안에 들어가는 출처 표기

# ── API 키 (환경변수로 넣는 걸 추천) ──────────────────────
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")   # 없으면 AI 기능 없이 동작
CLAUDE_MODEL = "claude-sonnet-5-5"   # 아끼려면: "claude-haiku-5-5"
FIX_SUBS_WITH_AI = True   # 받아쓰기 맞춤법·오인식을 AI로 교정
FIX_MODEL = "claude-sonnet-5-5"   # 자막 교정만 똑똑한 모델로 (한 편 몇 원 수준)

# ── Whisper (음성 → 자막) ────────────────────────────────
WHISPER_MODEL = "medium"   # tiny / base / small / medium / large-v3 (클수록 정확, 느림)
WHISPER_DEVICE = "auto"    # NVIDIA GPU 있으면 "cuda", 없으면 "cpu"
SUB_MAX_CHARS = 14         # 자막 한 덩어리 최대 글자 수

# ── 자막 스타일 (예능 자막 느낌) ─────────────────────────
SUB_FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/blackhansans/BlackHanSans-Regular.ttf"
SUB_FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "BlackHanSans-Regular.ttf")
SUB_SIZE = 100             # 글자 크기
SUB_STROKE = 6             # 검은 테두리 두께
SUB_BOX = True             # 자막 뒤 검은 박스
SRC_SUB_HIDE = 0.20        # 원본 자막 띠 높이(아래에서부터 비율). 얼굴 확대 + 내 자막 박스로 가림 (0=끄기)
SUB_COVER_PX = 160         # 내 자막 박스가 영상 맨 아래에서 확실히 덮는 높이(px)
SUB_BOX_MIN_W = 1.00       # 자막 박스 최소 가로폭 (화면 대비). 1.00 = 화면 전체 폭
CUT_SILENCE = True         # 말 없는 긴 구간 자동 잘라내기
SILENCE_MIN = 0.8          # 이 길이(초) 이상 조용하면 무음으로 침
SILENCE_PAD = 0.25         # 말 앞뒤로 남겨두는 여유(초)
AUDIO_POLISH = True        # 목소리 크기 일정하게 + 저음 잡음 제거 (유튜브 기준 음량)
SFX = True                 # 예능 효과음 (AI가 포인트마다 배치, 메모장 "sfx"에서 수정)
SFX_VOLUME = 0.55          # 효과음 크기 (0.3 작게 ~ 1.0 크게)
SUB_MIN_DUR = 0.4          # 자막 한 줄이 화면에 떠 있는 최소 시간(초). 이보다 짧으면 옆 자막 시간을 빌려 늘림
SUB_COLORS = ["#FFFFFF", "#FFE14D", "#FFFFFF", "#7CF5C1", "#FFFFFF", "#FF6B6B", "#FFFFFF", "#7FDBFF"]  # 자막마다 돌아가며
SUB_ITALIC = False         # True면 살짝 기울임
CONTEXT_CAPTIONS = True    # 메인 자막 위 초록색 상황 설명 자막 "(수능 점심때 노래들으면)"
CONTEXT_COLOR = "#4DFF6A"
CONTEXT_SIZE = 62          # 초록 상황 자막 글자 크기 (메인 자막 크기와 별개로 고정)
CONTEXT_SIZE_RATIO = 0.62  # CONTEXT_SIZE가 없을 때만 사용

# ── 캔버스 레이아웃 (1080x1920 세로) ──────────────────────
W, H = 1080, 1920
TITLE_AREA = (0, 520)        # 제목 영역 y 범위
VIDEO_AREA = (520, 1280)     # 영상 영역 y 범위 (높이 760, 가운데를 잘라서 채움)
SOURCE_Y = 1650              # 출처 텍스트 y (아래 가운데)
COMMENT_Y = 1315             # 댓글 카드 시작 y (영상 바로 아래)
COMMENT_STYLE = "clean"      # "clean" = 프사 없이 댓글+👍 (예능잼잼식) / "simple" = 회색 점+한 줄 / "card" = 프사·닉네임 카드
SUB_BOTTOM_MARGIN = 0        # 영상 영역 바닥에서 자막까지 여백 (0 = 영상 아래에 딱 붙음)
FACE_ZOOM = 1.45             # 얼굴 중심으로 확대하는 배율 (1.0 = 확대 안 함, 앵글 전환 끌 때 사용)
ANGLE_SWITCH = True          # 넓은 샷 ↔ 얼굴 클로즈업을 번갈아 (예능잼잼식 카메라 앵글 전환)
ZOOM_WIDE = 1.15             # 넓은 샷 배율
ZOOM_CLOSE = 2.4             # 클로즈업 배율 (말하는 사람 얼굴에 확 들어감)
SHOT_MIN, SHOT_MAX = 1.8, 3.5   # 한 샷 길이(초) 범위

# ── 폰트 자동 탐색 ───────────────────────────────────────
_BOLD = [
    "C:/Windows/Fonts/malgunbd.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
_REGULAR = [
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
]
# 직접 지정하고 싶으면 여기에 경로 입력 (예: 다운받은 Pretendard-Black.otf)
FONT_BOLD = os.environ.get("FONT_BOLD") or next((p for p in _BOLD if os.path.exists(p)), None)
FONT_REGULAR = os.environ.get("FONT_REGULAR") or next((p for p in _REGULAR if os.path.exists(p)), None)

# .ttc 파일 안에서 한국어 폰트 번호 (Noto CJK는 KR이 1번, 애플고딕은 0번)
def font_index(path):
    if path and "NotoSansCJK" in path:
        return 1
    return 0

IS_WINDOWS = platform.system() == "Windows"
