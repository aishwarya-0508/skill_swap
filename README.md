# SkillSwap

SkillSwap is a Flask and Neo4j app for finding peer-to-peer skill exchanges. Members can create profiles, categorize teaching and learning skills, find reciprocal matches, manage requests, track exchanges, leave reviews, and receive in-app notifications.

## Running locally

Set the Flask session key and Neo4j password before starting the app. The Neo4j
URI and username default to `neo4j://localhost:7687` and `neo4j`.

PowerShell:

```powershell
$env:FLASK_SECRET_KEY = "<a-long-random-secret>"
$env:NEO4J_PASSWORD = "<your-neo4j-password>"
python app.py
```

Optionally set `NEO4J_URI` and `NEO4J_USERNAME` to configure a different
Neo4j instance. Keep these values out of source control.

## Administrator access

Set `SKILLSWAP_ADMIN_EMAILS` to a comma-separated list of member email addresses before starting the app. Only logged-in members with an email on this list can open `/admin`.

PowerShell:

```powershell
$env:SKILLSWAP_ADMIN_EMAILS = "admin@example.com"
python app.py
```

To allow multiple administrators, separate addresses with commas.

The administrator dashboard reports registered users, distinct skills currently
listed on member profiles, completed exchanges, and the ten most popular skills
ranked by the number of distinct profiles that teach or want each skill.
