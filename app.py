"""
Repurpose by Par4
=================
Flask backend — handles signup, sessions, and Claude API calls.
Uses Supabase (Postgres) for persistent user storage.
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import anthropic
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-in-production")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATABASE_URL      = os.environ.get("DATABASE_URL")

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id        SERIAL PRIMARY KEY,
            name      TEXT NOT NULL,
            email     TEXT UNIQUE NOT NULL,
            joined_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_user(email):
    conn = get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def create_user(name, email):
    conn = get_conn()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (name, email) VALUES (%s, %s)",
            (name, email)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cur.close()
        conn.close()
        return False  # email already exists

# Initialize DB at startup (runs under both gunicorn and direct python)
init_db()

# ─── BUSINESS CONTEXTS ────────────────────────────────────────────────────────

BUSINESS_CONTEXTS = {
    "gym_crossfit": {
        "label": "CrossFit / Gym",
        "desc":  "CrossFit box or fitness gym",
        "voice": "Energetic, community-driven, motivating. Real talk. No corporate fluff.",
        "cta":   "Book a free intro class"
    },
    "bjj_martial": {
        "label": "BJJ / Martial Arts",
        "desc":  "Brazilian Jiu-Jitsu or martial arts academy",
        "voice": "Disciplined, technical, community-focused. Beginner-friendly but serious about the craft.",
        "cta":   "Book your free trial class"
    },
    "restaurant_cafe": {
        "label": "Restaurant / Café",
        "desc":  "Local restaurant, café, or food business",
        "voice": "Warm, inviting, food-forward. Make people hungry and feel welcome.",
        "cta":   "Visit us or order online"
    },
    "law_firm": {
        "label": "Law Firm",
        "desc":  "Law firm or legal services",
        "voice": "Professional, trustworthy, clear. No jargon. Approachable but authoritative.",
        "cta":   "Schedule a free consultation"
    },
    "camp_outdoor": {
        "label": "Camp / Outdoor Activity",
        "desc":  "Summer camp, fishing camp, or outdoor recreation",
        "voice": "Adventurous, genuine, family-focused. Make parents feel confident and kids feel excited.",
        "cta":   "Register now or book a spot"
    },
    "dental_medical": {
        "label": "Dental / Medical",
        "desc":  "Dental office or medical practice",
        "voice": "Warm, professional, reassuring. Patients should feel comfortable and cared for.",
        "cta":   "Schedule your appointment"
    },
    "real_estate": {
        "label": "Real Estate",
        "desc":  "Real estate agent or property business",
        "voice": "Knowledgeable, confident, local expert. Build trust and demonstrate market knowledge.",
        "cta":   "Get in touch for a free market analysis"
    },
    "salon_spa": {
        "label": "Salon / Spa",
        "desc":  "Hair salon, beauty salon, or spa",
        "voice": "Welcoming, stylish, confidence-building. Make people feel pampered before they arrive.",
        "cta":   "Book your appointment"
    },
    "web_tech": {
        "label": "Web / Tech Agency",
        "desc":  "Web design, software, or tech company",
        "voice": "Smart, direct, results-focused. No buzzwords. Let the work speak.",
        "cta":   "See our work or get in touch"
    },
    "custom": {
        "label": "Other (I'll describe it)",
        "desc":  "Custom business type",
        "voice": "Professional and authentic",
        "cta":   "Get in touch"
    }
}

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if session.get("user_email"):
        return redirect(url_for("app_page"))
    return render_template("index.html")


@app.route("/signup", methods=["POST"])
def signup():
    name  = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()

    if not name or not email:
        return render_template("index.html", error="Please enter your name and email.")

    # Create user (or sign in if they already exist)
    create_user(name, email)
    user = get_user(email)

    if user:
        session["user_email"] = email
        session["user_name"]  = user["name"]
        return redirect(url_for("app_page"))

    return render_template("index.html", error="Something went wrong. Please try again.")


@app.route("/app")
def app_page():
    if not session.get("user_email"):
        return redirect(url_for("index"))
    return render_template("app.html",
                           user_name=session.get("user_name", ""),
                           business_types=BUSINESS_CONTEXTS)


@app.route("/generate", methods=["POST"])
def generate():
    try:
        return _generate_inner()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def _generate_inner():
    if not session.get("user_email"):
        return jsonify({"error": "Not logged in"}), 401

    data          = request.get_json()
    content       = data.get("content", "").strip()
    business_key  = data.get("business_type", "custom")
    custom_desc   = data.get("custom_desc", "").strip()

    if not content:
        return jsonify({"error": "No content provided"}), 400

    ctx = BUSINESS_CONTEXTS.get(business_key, BUSINESS_CONTEXTS["custom"])

    # Override for custom business type
    if business_key == "custom" and custom_desc:
        ctx = {**ctx, "desc": custom_desc, "voice": "Authentic and professional"}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a social media content specialist.
Repurpose the following content for a {ctx['desc']}.

VOICE & TONE: {ctx['voice']}
CALL TO ACTION: {ctx['cta']}

SOURCE CONTENT:
\"\"\"
{content}
\"\"\"

Generate all 7 formats below. Make each one feel native to its platform.

---INSTAGRAM---
Hook-first. 150-250 words. Short paragraphs. 8-12 hashtags on their own line at the end.

---LINKEDIN---
Professional but human. Story or insight-driven. 150-300 words. Max 3 hashtags at end. End with a question or observation.

---FACEBOOK---
Conversational and warm. 100-200 words. Community feel. Clear CTA. Max 5 hashtags.

---TWITTER---
3-5 tweets. Number them 1/ 2/ 3/ etc. Each under 280 chars and stands alone. Last tweet has CTA.

---GOOGLE_BUSINESS---
Under 300 words. Include 2-3 local/service keywords naturally. Strong CTA with no URL needed. No hashtags.

---EMAIL---
Short headline. 100-200 words. Scannable short paragraphs. Personal tone. One CTA at end.

---SMS---
Under 160 characters total. Direct and punchy. Include CTA.
"""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception as e:
        return jsonify({"error": f"Claude API error: {str(e)}"}), 500

    raw = message.content[0].text

    # Parse the sections
    sections = {
        "instagram":      _extract(raw, "---INSTAGRAM---",      "---LINKEDIN---"),
        "linkedin":       _extract(raw, "---LINKEDIN---",       "---FACEBOOK---"),
        "facebook":       _extract(raw, "---FACEBOOK---",       "---TWITTER---"),
        "twitter":        _extract(raw, "---TWITTER---",        "---GOOGLE_BUSINESS---"),
        "google_business":_extract(raw, "---GOOGLE_BUSINESS---","---EMAIL---"),
        "email":          _extract(raw, "---EMAIL---",           "---SMS---"),
        "sms":            _extract(raw, "---SMS---",             None),
    }

    return jsonify({"success": True, "sections": sections})


def _extract(text, start_marker, end_marker):
    """Pull content between two markers."""
    try:
        start = text.index(start_marker) + len(start_marker)
        if end_marker:
            end = text.index(end_marker)
            return text[start:end].strip()
        return text[start:].strip()
    except ValueError:
        return ""


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ─── START ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
