import os
from flask import Flask, flash, render_template, request, redirect, session
from neo4j import GraphDatabase
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Set FLASK_SECRET_KEY before starting SkillSwap.")

URI = os.environ.get("NEO4J_URI", "neo4j://localhost:7687")
USERNAME = os.environ.get("NEO4J_USERNAME", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD")
if not PASSWORD:
    raise RuntimeError("Set NEO4J_PASSWORD before starting SkillSwap.")

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

SKILL_CATEGORIES = (
    "Arts & Design",
    "Business",
    "Communication",
    "Data & Technology",
    "Languages",
    "Music",
    "Personal Development",
    "Science & Education",
    "Other",
)
EXPERIENCE_LEVELS = ("Beginner", "Intermediate", "Advanced", "Professional")
ADMIN_EMAILS = {
    address.strip().lower()
    for address in os.environ.get("SKILLSWAP_ADMIN_EMAILS", "").split(",")
    if address.strip()
}


def create_user(name, email, password):
    with driver.session() as session:
        session.run("""
            CREATE (u:User {
                name: $name,
                email: $email,
                password: $password
            })
        """, name=name, email=email, password=password)


def add_notification(db, recipient_email, message, url):
    db.run("""
        MATCH (recipient:User {email: $email})
        CREATE (recipient)-[:HAS_NOTIFICATION]->(:Notification {
            message: $message,
            url: $url,
            created_at: datetime(),
            read: false
        })
    """, email=recipient_email, message=message, url=url)


@app.context_processor
def inject_navigation_state():
    return {"is_admin": session.get("email", "").lower() in ADMIN_EMAILS}


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        create_user(name, email, password)
        flash("Your account is ready. Log in to start building your skill profile.", "success")

        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"]
        password = request.form["password"]

        with driver.session() as db:
            result = db.run("""
                MATCH (u:User {email: $email, password: $password})
                RETURN u
            """, email=email, password=password)

            user = result.single()

        if user:
            session["email"] = email
            return redirect("/dashboard")

        flash("That email and password combination was not recognized.", "error")
        return redirect("/login")

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():

    if "email" not in session:
        return redirect("/login")

    with driver.session() as db:
        result = db.run("""
            MATCH (u:User {email: $email})
            OPTIONAL MATCH (u)-[:KNOWS]->(known:Skill)
            OPTIONAL MATCH (u)-[:WANTS]->(wanted:Skill)
            RETURN u.name AS name,
                   u.location AS location,
                   u.bio AS bio,
                   u.phone AS phone,
                   u.linkedin AS linkedin,
                   u.website AS website,
                   u.experience_level AS experience_level,
                   u.interests AS interests,
                   collect(DISTINCT CASE WHEN known IS NULL THEN null
                       ELSE {name: known.name, category: coalesce(known.category, 'Other')} END) AS known_skills,
                   collect(DISTINCT CASE WHEN wanted IS NULL THEN null
                       ELSE {name: wanted.name, category: coalesce(wanted.category, 'Other')} END) AS wanted_skills
        """, email=session["email"])

        record = result.single()
        exchange_result = db.run("""
            MATCH (sender:User)-[r:REQUESTED]->(recipient:User)
            WHERE (sender.email = $email OR recipient.email = $email)
              AND coalesce(r.status, 'pending') IN ['accepted', 'completed']
            RETURN sender.email AS sender_email, sender.name AS sender_name,
                   recipient.email AS recipient_email, recipient.name AS recipient_name,
                   r.skill AS skill, coalesce(r.status, 'pending') AS status,
                   toString(r.responded_at) AS started_at,
                   toString(r.completed_at) AS completed_at
            ORDER BY r.responded_at DESC
        """, email=session["email"])
        exchanges = [item.data() for item in exchange_result]
        rated_result = db.run("""
            MATCH (:User {email: $email})-[:WROTE]->(review:Review)
            RETURN collect(review.exchange_id) AS exchange_ids
        """, email=session["email"])
        rated_record = rated_result.single()
        rated_exchange_ids = set(rated_record["exchange_ids"] or []) if rated_record else set()
        for exchange in exchanges:
            exchange["exchange_id"] = (
                f"{exchange['sender_email']}|{exchange['recipient_email']}|{exchange['skill']}"
            )
            exchange["partner_name"] = (
                exchange["recipient_name"]
                if exchange["sender_email"] == session["email"]
                else exchange["sender_name"]
            )
            exchange["can_rate"] = (
                exchange["status"] == "completed"
                and exchange["exchange_id"] not in rated_exchange_ids
            )
        notification_result = db.run("""
            MATCH (:User {email: $email})-[:HAS_NOTIFICATION]->(notification:Notification)
            RETURN notification.message AS message, notification.url AS url,
                   notification.read AS read, toString(notification.created_at) AS created_at
            ORDER BY notification.created_at DESC
            LIMIT 8
        """, email=session["email"])
        notifications = [item.data() for item in notification_result]
        unread_result = db.run("""
            MATCH (:User {email: $email})-[:HAS_NOTIFICATION]->(notification:Notification)
            WHERE coalesce(notification.read, false) = false
            RETURN count(notification) AS unread_count
        """, email=session["email"])
        unread_record = unread_result.single()
        rating_result = db.run("""
            MATCH (review:Review)-[:ABOUT]->(:User {email: $email})
            RETURN avg(review.rating) AS average_rating, count(review) AS rating_count
        """, email=session["email"])
        rating_record = rating_result.single()
        pending_result = db.run("""
            MATCH (sender:User)-[r:REQUESTED]->(recipient:User)
            WHERE (sender.email = $email OR recipient.email = $email)
              AND coalesce(r.status, 'pending') = 'pending'
            RETURN count(r) AS pending_count
        """, email=session["email"])
        pending_record = pending_result.single()

    return render_template(
        "dashboard.html",
        name=record["name"],
        location=record["location"] or "",
        bio=record["bio"] or "",
        phone=record["phone"] or "",
        linkedin=record["linkedin"] or "",
        website=record["website"] or "",
        experience_level=record["experience_level"] or "",
        interests=record["interests"] or "",
        known_skills=record["known_skills"],
        wanted_skills=record["wanted_skills"],
        experience_levels=EXPERIENCE_LEVELS,
        categories=SKILL_CATEGORIES,
        active_exchanges=[item for item in exchanges if item["status"] == "accepted"],
        completed_exchanges=[item for item in exchanges if item["status"] == "completed"],
        notifications=notifications,
        unread_notifications=unread_record["unread_count"] if unread_record else 0,
        average_rating=rating_record["average_rating"] if rating_record else None,
        rating_count=rating_record["rating_count"] if rating_record else 0,
        pending_requests=pending_record["pending_count"] if pending_record else 0
    )


@app.route("/update_profile", methods=["POST"])
def update_profile():
    if "email" not in session:
        return redirect("/login")

    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    bio = request.form.get("bio", "").strip()
    phone = request.form.get("phone", "").strip()
    linkedin = request.form.get("linkedin", "").strip()
    website = request.form.get("website", "").strip()
    experience_level = request.form.get("experience_level", "").strip()
    interests = request.form.get("interests", "").strip()

    if not name:
        flash("Your name is required.", "error")
        return redirect("/dashboard")
    if len(name) > 100:
        flash("Name must be 100 characters or fewer.", "error")
        return redirect("/dashboard")
    if len(location) > 100:
        flash("Location must be 100 characters or fewer.", "error")
        return redirect("/dashboard")
    if len(bio) > 280:
        flash("Your bio must be 280 characters or fewer.", "error")
        return redirect("/dashboard")
    if len(phone) > 30:
        flash("Phone number must be 30 characters or fewer.", "error")
        return redirect("/dashboard")
    if experience_level and experience_level not in EXPERIENCE_LEVELS:
        flash("Choose a valid experience level.", "error")
        return redirect("/dashboard")
    if len(interests) > 300:
        flash("Interests must be 300 characters or fewer.", "error")
        return redirect("/dashboard")
    for label, link in (("LinkedIn", linkedin), ("Website", website)):
        if len(link) > 300:
            flash(f"{label} link must be 300 characters or fewer.", "error")
            return redirect("/dashboard")
        if link:
            parsed_link = urlparse(link)
            if parsed_link.scheme not in {"http", "https"} or not parsed_link.netloc:
                flash(f"Enter a valid http or https {label} URL.", "error")
                return redirect("/dashboard")

    with driver.session() as db:
        result = db.run("""
            MATCH (u:User {email: $email})
            SET u.name = $name,
                u.location = $location,
                u.bio = $bio,
                u.phone = $phone,
                u.linkedin = $linkedin,
                u.website = $website,
                u.experience_level = $experience_level,
                u.interests = $interests
            RETURN u.email AS email
        """, email=session["email"], name=name, location=location, bio=bio,
             phone=phone, linkedin=linkedin, website=website,
             experience_level=experience_level, interests=interests)
        updated = result.single()

    if updated:
        flash("Your profile has been updated.", "success")
    else:
        flash("Your profile could not be found.", "error")
    return redirect("/dashboard")


@app.route("/clear_profile", methods=["POST"])
def clear_profile():
    if "email" not in session:
        return redirect("/login")

    with driver.session() as db:
        result = db.run("""
            MATCH (u:User {email: $email})
            SET u.location = null,
                u.bio = null,
                u.phone = null,
                u.linkedin = null,
                u.website = null,
                u.experience_level = null,
                u.interests = null
            RETURN u.email AS email
        """, email=session["email"])
        cleared = result.single()

    if cleared:
        flash("Optional profile details have been cleared. Your account and skills are unchanged.", "success")
    else:
        flash("Your profile could not be found.", "error")
    return redirect("/dashboard")


@app.route("/add_skill", methods=["POST"])
def add_skill():

    if "email" not in session:
        return redirect("/login")

    skill = request.form.get("skill", "").strip()
    category = request.form.get("category", "Other").strip()
    if not skill or len(skill) > 60 or category not in SKILL_CATEGORIES:
        flash("Enter a skill of 60 characters or fewer and choose a valid category.", "error")
        return redirect("/dashboard")

    with driver.session() as db:
        db.run("""
            MATCH (u:User {email: $email})
            MERGE (s:Skill {name: $skill})
            SET s.category = $category
            MERGE (u)-[:KNOWS]->(s)
        """, email=session["email"], skill=skill, category=category)

    return redirect("/dashboard")


@app.route("/add_wanted_skill", methods=["POST"])
def add_wanted_skill():
    if "email" not in session:
        return redirect("/login")

    skill = request.form.get("skill", "").strip()
    category = request.form.get("category", "Other").strip()
    if not skill or len(skill) > 60 or category not in SKILL_CATEGORIES:
        flash("Enter a skill of 60 characters or fewer and choose a valid category.", "error")
        return redirect("/dashboard")

    with driver.session() as db:
        db.run("""
            MATCH (u:User {email: $email})
            MERGE (s:Skill {name: $skill})
            SET s.category = $category
            MERGE (u)-[:WANTS]->(s)
        """, email=session["email"], skill=skill, category=category)

    flash(f"Added {skill} to your learning list.", "success")
    return redirect("/dashboard")


@app.route("/update_skill", methods=["POST"])
def update_skill():
    if "email" not in session:
        return redirect("/login")

    old_skill = request.form.get("old_skill", "").strip()
    new_skill = request.form.get("new_skill", "").strip()
    category = request.form.get("category", "Other").strip()
    kind = request.form.get("kind")
    relationship = "KNOWS" if kind == "known" else "WANTS" if kind == "wanted" else None

    if not old_skill or not new_skill or len(new_skill) > 60 or not relationship or category not in SKILL_CATEGORIES:
        flash("Enter a valid skill name.", "error")
        return redirect("/dashboard")
    if old_skill == new_skill:
        return redirect("/dashboard")

    with driver.session() as db:
        result = db.run(f"""
            MATCH (u:User {{email: $email}})-[old_relation:{relationship}]->(old_skill:Skill {{name: $old_skill}})
            MERGE (new_skill:Skill {{name: $new_skill}})
            SET new_skill.category = $category
            MERGE (u)-[:{relationship}]->(new_skill)
            DELETE old_relation
            RETURN new_skill.name AS name
        """, email=session["email"], old_skill=old_skill, new_skill=new_skill, category=category)
        updated = result.single()

    if updated:
        flash(f"Updated skill to {updated['name']}.", "success")
    else:
        flash("That skill was not found on your profile.", "error")
    return redirect("/dashboard")


@app.route("/remove_skill", methods=["POST"])
def remove_skill():
    if "email" not in session:
        return redirect("/login")

    skill = request.form.get("skill", "").strip()
    kind = request.form.get("kind")
    relationship = "KNOWS" if kind == "known" else "WANTS" if kind == "wanted" else None
    if not skill or not relationship:
        return redirect("/dashboard")

    with driver.session() as db:
        db.run(f"""
            MATCH (u:User {{email: $email}})-[r:{relationship}]->(s:Skill {{name: $skill}})
            DELETE r
        """, email=session["email"], skill=skill)

    flash(f"Removed {skill} from your list.", "success")
    return redirect("/dashboard")


@app.route("/matches")
def matches():

    if "email" not in session:
        return redirect("/login")

    search = request.args.get("q", "").strip()[:80]
    category = request.args.get("category", "").strip()
    if category not in SKILL_CATEGORIES:
        category = ""

    with driver.session() as db:
        result = db.run("""
            MATCH (me:User {email: $email})-[:WANTS]->(skill:Skill)
            MATCH (other:User)-[:KNOWS]->(skill)
            WHERE other.email <> $email
              AND ($category = '' OR coalesce(skill.category, 'Other') = $category)
              AND ($search = '' OR toLower(skill.name) CONTAINS toLower($search)
                   OR toLower(other.name) CONTAINS toLower($search))
            OPTIONAL MATCH (me)-[:KNOWS]->(shared:Skill)<-[:WANTS]-(other)
            OPTIONAL MATCH (review:Review)-[:ABOUT]->(other)
            WITH other.email AS email, collect(DISTINCT other) AS profiles,
                 collect(DISTINCT {
                     name: skill.name,
                     category: coalesce(skill.category, 'Other')
                 }) AS skills,
                 count(DISTINCT shared) AS reciprocal_skills,
                 avg(review.rating) AS average_rating,
                 count(DISTINCT review) AS rating_count,
                 collect(DISTINCT CASE WHEN review IS NULL THEN null
                     ELSE {rating: review.rating, comment: review.comment} END)[0..3] AS reviews
            WITH head(profiles) AS profile, email, skills, reciprocal_skills,
                 average_rating, rating_count, reviews
            RETURN profile.name AS name,
                     head(split(trim(profile.name), ' ')) AS given_name,
                   email AS email,
                   coalesce(profile.location, '') AS location,
                   coalesce(profile.experience_level, '') AS experience_level,
                   coalesce(profile.bio, '') AS bio,
                   coalesce(profile.interests, '') AS interests,
                   skills,
                   reciprocal_skills, average_rating, reviews
            ORDER BY reciprocal_skills DESC, name
        """, email=session["email"], search=search, category=category)

        matches = [record.data() for record in result]

        requested_result = db.run("""
            MATCH (:User {email: $email})-[r:REQUESTED]->(other:User)
            WHERE coalesce(r.status, 'pending') <> 'declined'
            RETURN other.email AS email, r.skill AS skill
        """, email=session["email"])
        requested = {(record["email"], record["skill"]) for record in requested_result}

    return render_template(
        "matches.html",
        matches=matches,
        requested=requested,
        search=search,
        selected_category=category,
        categories=SKILL_CATEGORIES
    )


@app.route("/requests")
def requests_page():
    if "email" not in session:
        return redirect("/login")

    with driver.session() as db:
        incoming_result = db.run("""
            MATCH (sender:User)-[r:REQUESTED]->(me:User {email: $email})
            RETURN sender.name AS name,
                   sender.email AS email,
                   r.skill AS skill,
                   coalesce(r.status, 'pending') AS status
            ORDER BY r.created_at DESC
        """, email=session["email"])
        incoming = [record.data() for record in incoming_result]

        outgoing_result = db.run("""
            MATCH (me:User {email: $email})-[r:REQUESTED]->(recipient:User)
            RETURN recipient.name AS name,
                   recipient.email AS email,
                   r.skill AS skill,
                   coalesce(r.status, 'pending') AS status
            ORDER BY r.created_at DESC
        """, email=session["email"])
        outgoing = [record.data() for record in outgoing_result]

    return render_template("requests.html", incoming=incoming, outgoing=outgoing)


@app.route("/request_match", methods=["POST"])
def request_match():
    if "email" not in session:
        return redirect("/login")

    other_email = request.form.get("email", "").strip()
    skill = request.form.get("skill", "").strip()
    if not other_email or not skill or other_email == session["email"]:
        flash("That match request could not be sent.", "error")
        return redirect("/matches")

    with driver.session() as db:
        result = db.run("""
            MATCH (me:User {email: $email})-[:WANTS]->(:Skill {name: $skill})
            MATCH (other:User {email: $other_email})-[:KNOWS]->(:Skill {name: $skill})
            OPTIONAL MATCH (me)-[previous:REQUESTED {skill: $skill}]->(other)
            WITH me, other, previous
            WHERE previous IS NULL OR previous.status = 'declined'
            MERGE (me)-[r:REQUESTED {skill: $skill}]->(other)
            ON CREATE SET r.created_at = datetime(), r.status = 'pending'
            ON MATCH SET r.created_at = datetime(), r.status = 'pending',
                         r.responded_at = null, r.completed_at = null
            RETURN other.name AS name, me.name AS sender_name
        """, email=session["email"], other_email=other_email, skill=skill)
        record = result.single()
        if record:
            add_notification(
                db,
                other_email,
                f"{record['sender_name']} requested to learn {skill}.",
                "/requests"
            )

    if record:
        flash(f"Request sent to {record['name']} for {skill}.", "success")
    else:
        flash("That match is no longer available. Refresh and try another.", "error")
    return redirect("/matches")


@app.route("/respond_request", methods=["POST"])
def respond_request():
    if "email" not in session:
        return redirect("/login")

    sender_email = request.form.get("email", "").strip()
    skill = request.form.get("skill", "").strip()
    decision = request.form.get("decision")
    status = {"accept": "accepted", "decline": "declined"}.get(decision)
    if not sender_email or not skill or not status or sender_email == session["email"]:
        flash("That request could not be updated.", "error")
        return redirect("/requests")

    with driver.session() as db:
        result = db.run("""
            MATCH (sender:User {email: $sender_email})-[r:REQUESTED {skill: $skill}]->(me:User {email: $email})
            WHERE coalesce(r.status, 'pending') = 'pending'
            SET r.status = $status, r.responded_at = datetime()
            FOREACH (_ IN CASE WHEN $status = 'accepted' THEN [1] ELSE [] END |
                MERGE (sender)-[:CONNECTED {skill: $skill}]->(me)
            )
            RETURN sender.name AS name
        """, sender_email=sender_email, email=session["email"], skill=skill, status=status)
        record = result.single()
        if record:
            decision_text = "accepted" if status == "accepted" else "declined"
            add_notification(
                db,
                sender_email,
                f"{record['name']} {decision_text} your request to learn {skill}.",
                "/requests"
            )

    if record and status == "accepted":
        flash(f"You are now connected with {record['name']} for {skill}.", "success")
    elif record:
        flash(f"You declined the request for {skill}.", "success")
    else:
        flash("That request is no longer pending.", "error")
    return redirect("/requests")


@app.route("/complete_exchange", methods=["POST"])
def complete_exchange():
    if "email" not in session:
        return redirect("/login")

    sender_email = request.form.get("sender_email", "").strip()
    recipient_email = request.form.get("recipient_email", "").strip()
    skill = request.form.get("skill", "").strip()
    if not sender_email or not recipient_email or not skill:
        flash("That exchange could not be completed.", "error")
        return redirect("/dashboard")

    with driver.session() as db:
        result = db.run("""
            MATCH (sender:User {email: $sender_email})-[r:REQUESTED {skill: $skill}]->(recipient:User {email: $recipient_email})
            WHERE $email IN [sender.email, recipient.email]
              AND r.status = 'accepted'
            SET r.status = 'completed', r.completed_at = datetime()
            RETURN CASE WHEN sender.email = $email THEN recipient.email ELSE sender.email END AS partner_email,
                   CASE WHEN sender.email = $email THEN recipient.name ELSE sender.name END AS partner_name,
                   CASE WHEN sender.email = $email THEN sender.name ELSE recipient.name END AS actor_name
        """, sender_email=sender_email, recipient_email=recipient_email,
             skill=skill, email=session["email"])
        exchange = result.single()
        if exchange:
            add_notification(
                db,
                exchange["partner_email"],
                f"{exchange['actor_name']} marked your {skill} exchange as complete.",
                "/dashboard"
            )

    if exchange:
        flash("Exchange marked complete. You can now leave a review.", "success")
    else:
        flash("Only an active exchange can be marked complete.", "error")
    return redirect("/dashboard")


@app.route("/review_exchange", methods=["POST"])
def review_exchange():
    if "email" not in session:
        return redirect("/login")

    sender_email = request.form.get("sender_email", "").strip()
    recipient_email = request.form.get("recipient_email", "").strip()
    skill = request.form.get("skill", "").strip()
    comment = request.form.get("comment", "").strip()
    try:
        rating = int(request.form.get("rating", ""))
    except ValueError:
        rating = 0
    if not sender_email or not recipient_email or not skill or not 1 <= rating <= 5 or len(comment) > 500:
        flash("Enter a rating from 1 to 5 and a review of 500 characters or fewer.", "error")
        return redirect("/dashboard")
    partner_email = recipient_email if session["email"] == sender_email else sender_email
    exchange_id = f"{sender_email}|{recipient_email}|{skill}"

    with driver.session() as db:
        result = db.run("""
            MATCH (sender:User {email: $sender_email})-[request:REQUESTED {skill: $skill, status: 'completed'}]->(recipient:User {email: $recipient_email})
            MATCH (reviewer:User {email: $email}), (partner:User {email: $partner_email})
            WHERE $email IN [sender.email, recipient.email]
            MERGE (reviewer)-[:WROTE]->(review:Review {exchange_id: $exchange_id, reviewer_email: $email})
            ON CREATE SET review.rating = $rating, review.comment = $comment, review.created_at = datetime(), review.skill = $skill
            MERGE (review)-[:ABOUT]->(partner)
            RETURN review.rating AS rating
        """, sender_email=sender_email, recipient_email=recipient_email, skill=skill,
             email=session["email"], partner_email=partner_email,
             exchange_id=exchange_id, rating=rating, comment=comment)
        saved_review = result.single()

    if saved_review:
        flash("Thanks for sharing your review.", "success")
    else:
        flash("A review can only be submitted by an exchange participant after completion.", "error")
    return redirect("/dashboard")


@app.route("/notifications/read_all", methods=["POST"])
def read_all_notifications():
    if "email" not in session:
        return redirect("/login")

    with driver.session() as db:
        db.run("""
            MATCH (:User {email: $email})-[:HAS_NOTIFICATION]->(notification:Notification)
            SET notification.read = true
        """, email=session["email"])
    flash("Notifications marked as read.", "success")
    return redirect("/dashboard")


@app.route("/notifications")
def notifications_page():
    if "email" not in session:
        return redirect("/login")

    with driver.session() as db:
        result = db.run("""
            MATCH (:User {email: $email})-[:HAS_NOTIFICATION]->(notification:Notification)
            RETURN notification.message AS message, notification.url AS url,
                   notification.read AS read, toString(notification.created_at) AS created_at
            ORDER BY notification.created_at DESC
            LIMIT 100
        """, email=session["email"])
        notifications = [item.data() for item in result]
    return render_template("notifications.html", notifications=notifications)


@app.route("/admin")
def admin_dashboard():
    if "email" not in session:
        return redirect("/login")
    if session["email"].lower() not in ADMIN_EMAILS:
        flash("Administrator access is required.", "error")
        return redirect("/dashboard")

    with driver.session() as db:
        user_count = db.run("MATCH (u:User) RETURN count(u) AS total").single()
        skill_count = db.run("""
            MATCH (:User)-[:KNOWS|WANTS]->(s:Skill)
            RETURN count(DISTINCT s) AS total
        """).single()
        completed_count = db.run("""
            OPTIONAL MATCH ()-[r:REQUESTED]->()
            RETURN count(CASE WHEN r.status = 'completed' THEN 1 END) AS total
        """).single()
        totals = {
            "users": user_count["total"] if user_count else 0,
            "skills": skill_count["total"] if skill_count else 0,
            "completed_exchanges": completed_count["total"] if completed_count else 0
        }
        popular_result = db.run("""
            MATCH (skill:Skill)
            OPTIONAL MATCH (user:User)-[r]->(skill)
            WHERE type(r) IN ['KNOWS', 'WANTS']
            WITH skill, count(DISTINCT user) AS profile_count
            WHERE profile_count > 0
            RETURN skill.name AS name, coalesce(skill.category, 'Other') AS category,
                   profile_count
            ORDER BY profile_count DESC, name
            LIMIT 10
        """)
        popular_skills = [item.data() for item in popular_result]

    return render_template(
        "admin.html",
        totals=totals,
        popular_skills=popular_skills
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)