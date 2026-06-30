import os
import logging
import requests
import pytz
from datetime import datetime, time as dtime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------- CONFIG ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8705367880:AAHidnfXwi5y2KwZ68jg9lrXMPl3oNFZKbY")
API_KEY = os.environ.get("API_KEY", "29a0cad8414f4c329e6ba1a05801f3b1")   # OpenWeather key (wind/temp/humidity)
CHAT_ID = os.environ.get("CHAT_ID", "-1003924481330")

IST = pytz.timezone("Asia/Kolkata")

LOCATIONS = {
    "Kundalika": (18.45, 73.20),
    "Bankot Creek Bridge": (17.98, 73.03),
    "JSW Jaigarh Port": (16.59, 73.35),
    "Daman/Jampur Beach": (20.41, 72.83),
}

REPORT_TIMES = [
    dtime(6, 0, tzinfo=IST),
    dtime(10, 0, tzinfo=IST),
    dtime(14, 0, tzinfo=IST),
    dtime(18, 0, tzinfo=IST),
    dtime(22, 0, tzinfo=IST),
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"


# ---------------- DATA FETCHERS ----------------
def fetch_wind_temp_humidity(lat, lon):
    """Wind/temp/humidity from OpenWeather (kept as-is, working fine)."""
    params = {"lat": lat, "lon": lon, "appid": API_KEY, "units": "metric"}
    r = requests.get(OPENWEATHER_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    wind_kmh = round(data["wind"]["speed"] * 3.6, 1)
    temp = round(data["main"]["temp"], 1)
    humidity = data["main"]["humidity"]
    condition = data["weather"][0]["description"].title()
    return {
        "wind_kmh": wind_kmh,
        "temp": temp,
        "humidity": humidity,
        "condition": condition,
    }


def fetch_rain_data(lat, lon):
    """Accurate rain forecast from Open-Meteo (replaces unreliable OpenWeather rain field)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "precipitation,rain,weather_code",
        "hourly": "precipitation_probability,precipitation",
        "timezone": "Asia/Kolkata",
        "forecast_days": 1,
    }
    r = requests.get(OPENMETEO_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    current = data.get("current", {})
    current_precip_mm = current.get("precipitation", 0) or 0
    current_rain_mm = current.get("rain", 0) or 0

    # Find current hour's index in hourly array to get precipitation_probability
    now_iso = datetime.now(IST).strftime("%Y-%m-%dT%H:00")
    hourly_times = data.get("hourly", {}).get("time", [])
    hourly_prob = data.get("hourly", {}).get("precipitation_probability", [])
    hourly_precip = data.get("hourly", {}).get("precipitation", [])

    prob = 0
    next_hours_precip = 0
    if now_iso in hourly_times:
        idx = hourly_times.index(now_iso)
        prob = hourly_prob[idx] if idx < len(hourly_prob) else 0
        # sum precipitation forecast for next 3 hours
        next_hours_precip = sum(hourly_precip[idx:idx + 3]) if hourly_precip else 0

    is_raining_now = current_rain_mm > 0 or current_precip_mm > 0

    return {
        "is_raining_now": is_raining_now,
        "current_precip_mm": round(current_precip_mm, 2),
        "rain_probability": prob,
        "next_3h_precip_mm": round(next_hours_precip, 2),
    }


# ---------------- CLASSIFICATION ----------------
def wind_status(wind_kmh):
    if wind_kmh < 20:
        return "✅ SAFE"
    elif wind_kmh <= 35:
        return "🟡 MODERATE"
    else:
        return "🔴 DANGER"


def rain_status(rain_info):
    if rain_info["is_raining_now"]:
        return "Raining now"
    elif rain_info["rain_probability"] >= 40:
        return f"Possible rain ({rain_info['rain_probability']}%)"
    else:
        return "No rain"


# ---------------- REPORT BUILDER ----------------
def build_report():
    lines = ["🚢📍 *Marine Multi-Location Safety Report*", ""]

    for name, (lat, lon) in LOCATIONS.items():
        try:
            weather = fetch_wind_temp_humidity(lat, lon)
            rain = fetch_rain_data(lat, lon)
        except Exception as e:
            logger.error(f"Error fetching data for {name}: {e}")
            lines.append(f"📍 *{name}*\n⚠️ Data unavailable\n")
            continue

        lines.append(f"📍 *{name}*")
        lines.append(f"🌬️ Wind: {weather['wind_kmh']} km/h")
        lines.append(f"🌡️ Temp: {weather['temp']} °C")
        lines.append(f"💧 Humidity: {weather['humidity']}%")
        lines.append(f"🌤️ Condition: {weather['condition']}")
        lines.append(f"🌧️ Rain: {rain_status(rain)}")
        lines.append(f"{wind_status(weather['wind_kmh'])}")
        lines.append("")

    return "\n".join(lines)


# ---------------- TELEGRAM HANDLERS ----------------
async def send_scheduled_report(context: ContextTypes.DEFAULT_TYPE):
    report = build_report()
    await context.bot.send_message(chat_id=CHAT_ID, text=report, parse_mode="Markdown")


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching latest weather & rain data...")
    report = build_report()
    await update.message.reply_text(report, parse_mode="Markdown")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚢 Marine Safety Bot active.\nUse /report for an instant update.\n"
        "Automated reports at 06:00, 10:00, 14:00, 18:00, 22:00 IST."
    )


# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("report", report_command))

    job_queue = app.job_queue
    for t in REPORT_TIMES:
        job_queue.run_daily(send_scheduled_report, time=t)

    logger.info("Marine Safety Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()