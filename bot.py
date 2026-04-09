import discord
from discord.ext import commands, tasks
import httpx
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import os
from dotenv import load_dotenv

load_dotenv()

# ── 환경변수 ──
BOT_TOKEN      = os.getenv("DISCORD_BOT_TOKEN")
WEBHOOK_URL    = os.getenv("DISCORD_WEBHOOK_URL")
CHANNEL_ID     = os.getenv("CHZZK_CHANNEL_ID")   # 치지직 채널 ID
POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL", "5"))  # 폴링 주기(초)

KST = ZoneInfo("Asia/Seoul")
CHZZK_API = "https://api.chzzk.naver.com"
CLOSE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── 상태 ──
state = {
    "is_live": None,       # 현재 방송 상태
    "channel_name": "",
    "close_date": None,    # 방종 시각 문자열
    "elapsed_seconds": 0,
}

# ── Discord 봇 설정 ──
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ── 치지직 API 호출 ──
async def fetch_live_status():
    url = f"{CHZZK_API}/service/v2/channels/{CHANNEL_ID}/live-detail"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://chzzk.naver.com",
        })
        resp.raise_for_status()
        data = resp.json()

    content = data.get("content") or {}
    is_live = content.get("status") == "OPEN"
    channel_name = content.get("channel", {}).get("channelName", CHANNEL_ID) if content.get("channel") else CHANNEL_ID

    now = datetime.now(KST)
    close_date_str = content.get("closeDate")
    elapsed_seconds = 0

    if not is_live and close_date_str:
        try:
            close_dt = datetime.strptime(close_date_str, CLOSE_DATE_FORMAT).replace(tzinfo=KST)
            elapsed_seconds = max(0, int((now - close_dt).total_seconds()))
        except ValueError:
            pass

    return {
        "is_live": is_live,
        "channel_name": channel_name,
        "close_date": close_date_str,
        "elapsed_seconds": elapsed_seconds,
        "live_title": content.get("liveTitle") if is_live else None,
    }


# ── 웹훅 메시지 전송 ──
async def send_webhook(content: str, embeds: list = None):
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(WEBHOOK_URL, json=payload)


def fmt_time(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}시간 {m}분 {s}초"
    elif m > 0:
        return f"{m}분 {s}초"
    return f"{s}초"


def fmt_close_date(close_date_str: str) -> str:
    if not close_date_str:
        return ""
    try:
        dt = datetime.strptime(close_date_str, CLOSE_DATE_FORMAT).replace(tzinfo=KST)
        return dt.strftime("%m월 %d일 %H시 %M분")
    except ValueError:
        return close_date_str


# ── 폴링 태스크 ──
@tasks.loop(seconds=POLL_INTERVAL)
async def poll_chzzk():
    try:
        result = await fetch_live_status()
        prev_live = state["is_live"]
        curr_live = result["is_live"]

        state["channel_name"] = result["channel_name"]
        state["close_date"]   = result["close_date"]
        state["elapsed_seconds"] = result["elapsed_seconds"]

        # 첫 실행
        if prev_live is None:
            state["is_live"] = curr_live
            print(f"[봇 시작] {result['channel_name']} — {'방송 중' if curr_live else '오프라인'}")
            return

        # 방종 감지 (LIVE → OFFLINE)
        if prev_live and not curr_live:
            state["is_live"] = False
            close_str = fmt_close_date(result["close_date"])
            await send_webhook(
                content="",
                embeds=[{
                    "title": "🔴 방송 종료",
                    "description": f"**{result['channel_name']}** 님이 방송을 종료했어요.",
                    "color": 0xff4757,
                    "fields": [
                        {"name": "방종 시각", "value": close_str or "알 수 없음", "inline": True},
                    ],
                    "footer": {"text": "뿡댕이가 숨을 참기 시작했어요... 🫧"}
                }]
            )
            print(f"[방종] {result['channel_name']} — {close_str}")

        # 복귀 감지 (OFFLINE → LIVE)
        elif not prev_live and curr_live:
            state["is_live"] = True
            elapsed = state["elapsed_seconds"] if state["elapsed_seconds"] else 0
            # 실제 elapsed는 close_date 기준으로 계산
            if state["close_date"]:
                try:
                    close_dt = datetime.strptime(state["close_date"], CLOSE_DATE_FORMAT).replace(tzinfo=KST)
                    elapsed = int((datetime.now(KST) - close_dt).total_seconds())
                except ValueError:
                    pass

            await send_webhook(
                content="@everyone",
                embeds=[{
                    "title": "🟢 방송 복귀!",
                    "description": f"**{result['channel_name']}** 님이 돌아왔어요! 🎉",
                    "color": 0x2ed573,
                    "fields": [
                        {"name": "방종 시간", "value": fmt_time(elapsed), "inline": True},
                        {"name": "방송 제목", "value": result["live_title"] or "제목 없음", "inline": True},
                    ],
                    "footer": {"text": "뿡댕이가 살아났어요!! 🫧"}
                }]
            )
            print(f"[복귀] {result['channel_name']} — {fmt_time(elapsed)} 만에 복귀")

        state["is_live"] = curr_live

    except Exception as e:
        print(f"[폴링 오류] {e}")


# ── 슬래시 명령어 ──
@bot.tree.command(name="상태", description="스트리머 현재 방송 상태 확인")
async def status_command(interaction: discord.Interaction):
    try:
        result = await fetch_live_status()
        if result["is_live"]:
            embed = discord.Embed(
                title="🟢 방송 중",
                description=f"**{result['channel_name']}** 님이 방송 중이에요!",
                color=0x2ed573
            )
            embed.add_field(name="방송 제목", value=result["live_title"] or "제목 없음")
        else:
            close_str = fmt_close_date(result["close_date"])
            elapsed_str = fmt_time(result["elapsed_seconds"]) if result["elapsed_seconds"] else "알 수 없음"
            embed = discord.Embed(
                title="🔴 오프라인",
                description=f"**{result['channel_name']}** 님은 현재 방송 중이 아니에요.",
                color=0xff4757
            )
            embed.add_field(name="방종 시각", value=close_str or "알 수 없음", inline=True)
            embed.add_field(name="경과 시간", value=elapsed_str, inline=True)
            embed.set_footer(text="뿡댕이 숨 참는 중... 🫧")
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"오류 발생: {e}", ephemeral=True)


# ── 봇 시작 ──
@bot.event
async def on_ready():
    print(f"[봇 온라인] {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"[슬래시 명령어 등록] {len(synced)}개")
    except Exception as e:
        print(f"[명령어 등록 오류] {e}")
    poll_chzzk.start()


bot.run(BOT_TOKEN)