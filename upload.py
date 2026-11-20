"""완성된 쇼츠를 내 채널에 '비공개'로 업로드. 확인 후 유튜브 스튜디오에서 공개로 바꾸면 됨.

처음 실행하면 브라우저가 열리고 구글 로그인 → 권한 허용. 이후엔 token.json으로 자동 로그인.
예) python upload.py output/XXXX_190/upload.json
    python upload.py output/XXXX_190/upload.json --publish-at 2026-11-12T18:00:00+09:00   (예약 공개)
"""
import argparse
import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            creds = flow.run_local_server(port=0)
        open("token.json", "w").write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("upload_json")
    ap.add_argument("--publish-at", help="예약 공개 시각 (ISO 형식, 비공개 상태에서 이 시각에 자동 공개)")
    a = ap.parse_args()
    info = json.load(open(a.upload_json, encoding="utf-8"))

    status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    if a.publish_at:
        status["publishAt"] = a.publish_at
    body = {
        "snippet": {"title": info["title"][:100], "description": info["description"],
                    "tags": ["shorts"], "categoryId": "24"},
        "status": status,
    }
    yt = get_service()
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(info["file"], chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print(f"✅ 업로드 완료(비공개): https://youtube.com/shorts/{resp['id']}")


if __name__ == "__main__":
    main()
