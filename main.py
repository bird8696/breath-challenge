from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo

app = FastAPI(title="숨 참기 챌린지 🫁")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

CHZZK_API = "https://api.chzzk.naver.com"
KST = ZoneInfo("Asia/Seoul")

# closeDate 포맷: "2024-03-01 14:22:05" (KST, 타임존 표기 없음)
CLOSE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_close_date(close_date_str):
    """치지직 closeDate 문자열을 KST aware datetime으로 파싱"""
    if not close_date_str:
        return None
    try:
        dt = datetime.strptime(close_date_str, CLOSE_DATE_FORMAT)
        return dt.replace(tzinfo=KST)
    except ValueError:
        return None


@app.get("/api/status/{channel_id}")
async def get_channel_status(channel_id: str):
    """치지직 채널의 현재 방송 상태를 반환합니다."""
    url = f"{CHZZK_API}/service/v2/channels/{channel_id}/live-detail"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://chzzk.naver.com",
            })
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail="치지직 API 오류")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"치지직 API 연결 실패: {str(e)}")

    content = data.get("content") or {}
    status = content.get("status", "CLOSE")  # "OPEN" or "CLOSE"
    is_live = status == "OPEN"

    now = datetime.now(KST)

    if not is_live:
        # ✅ API의 closeDate(실제 방송 종료 시각)로 경과 시간 계산
        close_date = parse_close_date(content.get("closeDate"))
        if close_date:
            elapsed_seconds = max(0, int((now - close_date).total_seconds()))
        else:
            # closeDate 없을 경우 (방송한 적 없는 채널 등) → 0
            elapsed_seconds = 0
    else:
        elapsed_seconds = 0

    return {
        "channel_id": channel_id,
        "channel_name": content.get("channel", {}).get("channelName", channel_id) if content.get("channel") else channel_id,
        "is_live": is_live,
        "live_title": content.get("liveTitle") if is_live else None,
        "viewer_count": content.get("concurrentUserCount") if is_live else None,
        "elapsed_seconds": elapsed_seconds,   # 실제 방송 종료 후 경과 시간(초)
        "close_date": content.get("closeDate"),  # 디버그용
        "checked_at": now.isoformat(),
    }


@app.get("/api/channel-info/{channel_id}")
async def get_channel_info(channel_id: str):
    """채널 기본 정보 (이름, 아바타 등)"""
    url = f"{CHZZK_API}/service/v1/channels/{channel_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://chzzk.naver.com",
            })
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    content = data.get("content", {})
    return {
        "channel_id": channel_id,
        "channel_name": content.get("channelName", channel_id),
        "channel_image": content.get("channelImageUrl"),
        "follower_count": content.get("followerCount", 0),
    }


# 정적 파일 서빙 (index.html)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")