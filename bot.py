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
    """Accurate rain forecast from Open-Meteo using hourly precipitation (same as Rain Indicator Bot)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation_probability,precipitation",
        "timezone": "Asia/Kolkata",
        "forecast_days": 1,
    }
    r = requests.get(OPENMETEO_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    # Match current hour in hourly forecast array
    now_iso = datetime.now(IST).strftime("%Y-%m-%dT%H:00")
    hourly_times = data.get("hourly", {}).get("time", [])
    hourly_prob = data.get("hourly", {}).get("precipitation_probability", [])
    hourly_precip = data.get("hourly", {}).get("precipitation", [])

    prob = 0
    hourly_precip_mm = 0

    if now_iso in hourly_times:
        idx = hourly_times.index(now_iso)
        prob = hourly_prob[idx] if idx < len(hourly_prob) else 0
        hourly_precip_mm = hourly_precip[idx] if idx < len(hourly_precip) else 0

    hourly_precip_mm = round(hourly_precip_mm or 0, 2)
    is_raining = hourly_precip_mm > 0 or prob >= 40

    return {
        "is_raining_now": is_raining,
        "current_precip_mm": hourly_precip_mm,
        "rain_probability": prob,
    }


# ---------------- CLASSIFICATION ----------------
# Severity scale used everywhere: 0 = SAFE, 1 = MODERATE, 2 = DANGER
STATUS_LABELS = {0: "✅ SAFE", 1: "🟡 MODERATE", 2: "🔴 DANGER"}

# Rain intensity thresholds (mm in the preceding hour — standard met. classification)
LIGHT_RAIN_MM = 2.5    # below this = light rain
HEAVY_RAIN_MM = 7.6    # at/above this = heavy rain


def wind_severity(wind_kmh):
    if wind_kmh < 20:
        return 0
    elif wind_kmh <= 35:
        return 1
    else:
        return 2


def wind_status(wind_kmh):
    return STATUS_LABELS[wind_severity(wind_kmh)]


def rain_severity_and_label(rain_info):
    """Returns (severity, label_text) based on current rain intensity / forecast probability."""
    if rain_info["is_raining_now"]:
        intensity = max(rain_info["current_precip_mm"], 0)
        if intensity >= HEAVY_RAIN_MM:
            return 2, f"Heavy rain ({intensity} mm/hr)"
        elif intensity >= LIGHT_RAIN_MM:
            return 2, f"Moderate rain ({intensity} mm/hr)"
        else:
            return 1, f"Light rain ({intensity} mm/hr)"
    elif rain_info["rain_probability"] >= 40:
        return 1, f"Possible rain ({rain_info['rain_probability']}%)"
    else:
        return 0, "No rain"


def rain_status(rain_info):
    _, label = rain_severity_and_label(rain_info)
    return label


def combined_status(wind_kmh, rain_info):
    """Overall location status = worst case of wind and rain severity."""
    w_sev = wind_severity(wind_kmh)
    r_sev, _ = rain_severity_and_label(rain_info)
    overall = max(w_sev, r_sev)
    return STATUS_LABELS[overall]


# ---------------- REPORT BUILDER ----------------
def build_report():
    lines = ["🚢📍 *Marine Multi-Location Safety Report*", ""]

    for name, (lat, lon) in LOCATIONS.items():
        try:
            weather = fetch_wind_temp_humidity(lat, lon)
        except Exception as e:
            logger.error(f"OpenWeather failed for {name}: {type(e).__name__}: {e}")
            lines.append(f"📍 *{name}*\n⚠️ Data unavailable\n")
            continue

        try:
            rain = fetch_rain_data(lat, lon)
            rain_line = f"🌧️ Rain: {rain_status(rain)}"
            status_line = combined_status(weather['wind_kmh'], rain)
        except Exception as e:
            logger.error(f"Open-Meteo failed for {name}: {type(e).__name__}: {e}")
            rain_line = "🌧️ Rain: Data unavailable"
            status_line = wind_status(weather['wind_kmh'])

        lines.append(f"📍 *{name}*")
        lines.append(f"🌬️ Wind: {weather['wind_kmh']} km/h")
        lines.append(f"🌡️ Temp: {weather['temp']} °C")
        lines.append(f"💧 Humidity: {weather['humidity']}%")
        lines.append(f"🌤️ Condition: {weather['condition']}")
        lines.append(rain_line)
        lines.append(status_line)
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