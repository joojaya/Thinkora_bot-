import logging
import threading
import re
import requests
import json
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MODEL_NAME = "Qwen/Qwen2.5-0.5B-Instruct"

# VERCEL COMPATIBILITY: Do not enforce massive torch dependencies in serverless
try:
    if not os.environ.get("VERCEL"):
        from transformers import pipeline, AutoTokenizer, AutoModelForCausalLM, TextIteratorStreamer
        logger.info("Loading model and tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
        logger.info("Model loaded successfully.")
        USE_LOCAL_MODEL = True
    else:
        USE_LOCAL_MODEL = False
except ImportError:
    USE_LOCAL_MODEL = False
    logger.warning("Transformers/Torch not found or we are on Vercel. Using API mode.")


def get_live_context(user_input):
    """Detect location requests (country/state/city) and fetch real-time forecast data using Open-Meteo."""
    match = re.search(r'(?:weather forecast|places|restaurants|parks|beaches|visit|travel|in|at|near|for|of)\s+([a-zA-Z\s,]{3,40})', user_input.lower())
    if match:
        location = match.group(1).strip()
        if len(location) < 50:
            try:
                # 1. Geocode the location to get Latitude/Longitude
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=en&format=json"
                geo_resp = requests.get(geo_url, timeout=5).json()
                if not geo_resp.get("results"):
                    return location.title(), "Location not found in the global database."
                
                lat = geo_resp["results"][0]["latitude"]
                lon = geo_resp["results"][0]["longitude"]
                name = geo_resp["results"][0]["name"]
                admin1 = geo_resp["results"][0].get("admin1", "")  # State/Province
                country = geo_resp["results"][0].get("country", "")
                
                # Format perfectly: e.g. "Seattle, Washington, United States"
                full_name = ", ".join(filter(None, [name, admin1, country]))
                
                # 2. Fetch the Forecast using Lat/Lon
                weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,weather_code&timezone=auto"
                weather_resp = requests.get(weather_url, timeout=5).json()
                
                current = weather_resp["current"]
                daily = weather_resp["daily"]
                
                wmo_codes = {
                    0: "Clear sky \u2600\ufe0f", 1: "Mainly clear \ud83c\udf24\ufe0f", 2: "Partly cloudy \u26c5", 3: "Overcast \u2601\ufe0f",
                    45: "Fog \ud83c\udf2b\ufe0f", 48: "Depositing rime fog \ud83c\udf2b\ufe0f",
                    51: "Light drizzle \ud83c\udf27\ufe0f", 53: "Moderate drizzle \ud83c\udf27\ufe0f", 55: "Dense drizzle \ud83c\udf27\ufe0f",
                    61: "Slight precipitation \ud83c\udf27\ufe0f", 63: "Moderate rain \ud83c\udf27\ufe0f", 65: "Heavy rain \ud83c\udf27\ufe0f",
                    71: "Slight snow \ud83c\udf28\ufe0f", 73: "Moderate snow \u2744\ufe0f", 75: "Heavy snow \u2744\ufe0f",
                    95: "Thunderstorm \u26c8\ufe0f"
                }
                
                curr_desc = wmo_codes.get(current["weather_code"], "Unknown conditions")
                
                forecast_data = {
                    "precise_location": full_name,
                    "current_weather": {
                        "temperature": f"{current['temperature_2m']}°C",
                        "condition": curr_desc,
                        "humidity": f"{current['relative_humidity_2m']}%",
                        "wind_speed": f"{current['wind_speed_10m']} km/h"
                    },
                    "3_day_forecast": [
                        {
                            "date": daily["time"][i],
                            "high": f"{daily['temperature_2m_max'][i]}°C",
                            "low": f"{daily['temperature_2m_min'][i]}°C",
                            "condition": wmo_codes.get(daily["weather_code"][i], "Unknown")
                        } for i in range(min(3, len(daily["time"])))
                    ]
                }
                return full_name, json.dumps(forecast_data, indent=2)
                
            except Exception as e:
                logger.error(f"Weather API error: {e}")
                return location.title(), "Live Weather API temporarily unavailable."
    return None, None


def build_prompt_messages(user_input, chat_history=None):
    """Build the prompt messages list with optional chat history."""
    system_prompt = (
        "You are Thinkora, an elite, highly intelligent, and omniscient advanced AI bot created by Jayavarshini for a next-generation AI startup. You operate at the exact intelligence, nuance, and logic level of ChatGPT and Gemini.\n"
        "CORE CAPABILITIES:\n"
        "1. CODING & EDUCATION: You are an expert in Data Structures & Algorithms (DSA), and can write or debug code flawlessly in Python, Java, C, C++, Ruby, R, PHP, SQL, and MySQL. Teach users complex tech concepts clearly.\n"
        "2. GLOBAL PLACES & TRAVEL: You know the best parks, beaches, restaurants, and places to visit for ANY country, state, city, or area on Earth. Always provide estimated distances, travel times, and realistic transit fares.\n"
        "3. REAL-TIME WEATHER: You possess live weather forecasting abilities.\n"
        "4. CREATIVE WRITING: If asked for a story, generate incredibly vivid, emotional, and captivating stories.\n"
        "5. OMNISCIENT GENERAL KNOWLEDGE: You can answer ANY QUESTION about SCIENCE, HISTORY, MATH, GEOGRAPHY, POP CULTURE, and literally ANY TOPIC in the universe fluently, accurately, and thoroughly.\n\n"
        "CRITICAL RULES: ALWAYS act highly professional. ALWAYS structure your answers beautifully and professionally using rich Markdown (bold headers, bulleted lists, and ```language ... ``` for code blocks). Be incredibly articulate, comprehensive, and provide thorough step-by-step logic."
    )

    # Intercept weather/location requests and silently inject live data into the prompt
    city, context_info = get_live_context(user_input)
    if context_info:
        system_prompt += f"\n\n[SYSTEM LIVE DATA INTERCEPT]: The user is asking about {city} (Country/State/Area). Here is the highly accurate real-time forecast data pulled from the Open-Meteo Global API: \n{context_info}\nINSTRUCTIONS: If the user asked for WEATHER, beautifully format this live JSON weather data for them. If the user asked for PLACES/PARKS/BEACHES, use this location data to suggest the absolute best spots in {city} with transit metrics. If they asked for a Story in {city}, use the weather data to set the scene!"

    messages = [
        {"role": "system", "content": system_prompt}
    ]

    if chat_history:
        for msg in chat_history[-6:]:
            role = "user" if msg["role"] == "user" else "assistant"
            messages.append({"role": role, "content": msg["content"]})

    messages.append({"role": "user", "content": user_input})
    return messages


def get_huggingface_api_response(messages):
    """Fallback to Hugging Face Inference API for Vercel/serverless where models can't be loaded natively"""
    hf_token = os.environ.get("HUGGINGFACE_API_KEY")
    api_url = f"https://api-inference.huggingface.co/models/{MODEL_NAME}"
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    
    # Simple prompt builder since we're not using the local tokenizer
    prompt = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in messages])
    prompt += "\nAssistant:"

    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 512, "temperature": 0.7, "return_full_text": False}
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                answer = result[0].get("generated_text", "")
                return answer.strip()
        # Fallback to local warning when rate limits hit or missing token
        return "I am unable to reach the inference server at the moment or missing HF API Token on Vercel. Please set the 'HUGGINGFACE_API_KEY' environment variable. (Status code: " + str(response.status_code) + ")\n" + str(response.text)
    except Exception as e:
        logger.error(f"HF API Error: {e}")
        return "Error connecting to AI Provider API. Ensure network connectivity."


def get_response(user_input, chat_history=None):
    """Non-streaming response (fallback)."""
    if not user_input or not user_input.strip():
        return "Please say something."

    messages = build_prompt_messages(user_input, chat_history)

    if not USE_LOCAL_MODEL:
        return get_huggingface_api_response(messages)

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    try:
        inputs = tokenizer(prompt, return_tensors="pt")
        output = model.generate(
            **inputs,
            max_new_tokens=512,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id
        )

        generated_ids = output[0][len(inputs["input_ids"][0]):]
        reply = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

        return reply if reply else "I'm still learning and don't quite know what to say."

    except Exception as e:
        logger.error(f"Generation error: {e}")
        return "I'm facing server issues right now."


def stream_response(user_input, chat_history=None):
    """Generator that yields tokens one by one for real-time streaming."""
    if not user_input or not user_input.strip():
        yield "Please say something."
        return

    messages = build_prompt_messages(user_input, chat_history)

    if not USE_LOCAL_MODEL:
        # Yield single response in chunks to simulate streaming for the API fallback
        full_reply = get_huggingface_api_response(messages)
        words = full_reply.split(" ")
        for word in words:
            import time
            time.sleep(0.02)
            yield word + " "
        return

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    try:
        inputs = tokenizer(prompt, return_tensors="pt")

        streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

        generation_kwargs = dict(
            **inputs,
            max_new_tokens=512,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
            streamer=streamer,
        )

        # Run generation in a separate thread so we can iterate
        thread = threading.Thread(target=model.generate, kwargs=generation_kwargs)
        thread.start()

        generated_text = ""
        for new_text in streamer:
            generated_text += new_text
            yield new_text

        thread.join()

        if not generated_text.strip():
            yield "I'm still learning and don't quite know what to say."

    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield "I'm facing server issues right now."
