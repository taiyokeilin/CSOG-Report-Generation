import os
import re
from datetime import datetime

import streamlit as st

from auth import require_login, filter_players, can_edit
from db import get_supabase

st.set_page_config(page_title="Players", page_icon="⛳", layout="wide")
access = require_login()

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1.5rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
</style>
""", unsafe_allow_html=True)

title_col, logo_col = st.columns([5, 1])
with title_col:
    heading = "Players" if access["is_coach"] else ("My Profile" if access["player_id"] else "My Child's Profile")
    st.markdown(f'<p class="main-header">⛳ {heading}</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Profile details and session information. '
                'Launch monitor data (the shots themselves) can only be added by coaches.</p>',
                unsafe_allow_html=True)
with logo_col:
    if os.path.exists("csog_logo.png"):
        st.image("csog_logo.png", width=150)

sb = get_supabase()


# ── Data ──────────────────────────────────────────────────────────────────────
def load_lookups():
    locs = sb.table("locations").select("location_id, name").eq("active", True).order("name").execute().data or []
    progs = sb.table("programs").select("program_id, name").eq("active", True).order("name").execute().data or []
    coaches = (sb.table("coaches").select("coach_id, first_name, last_name").eq("active", True).execute().data or [])
    coaches.sort(key=lambda c: (c["last_name"].lower(), c["first_name"].lower()))
    return locs, progs, coaches


def load_players():
    rows = (sb.table("players")
            .select("player_id, first_name, last_name, coach_id, location_id, program_id, email, active")
            .execute().data or [])
    return sorted(rows, key=lambda p: (p["last_name"].lower(), p["first_name"].lower()))


def parse_emails(text: str) -> list:
    found = [e.strip().lower() for e in re.split(r"[,\s;]+", text or "") if e.strip()]
    return list(dict.fromkeys(found))


def valid_email(e: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e))


locations, programs, coaches = load_lookups()
loc_by_id = {l["location_id"]: l["name"] for l in locations}
prog_by_id = {p["program_id"]: p["name"] for p in programs}
coach_by_id = {c["coach_id"]: f"{c['first_name']} {c['last_name']}" for c in coaches}

all_players = filter_players(access, load_players())
if not access["is_coach"]:
    all_players = [p for p in all_players if p["active"]]
if not all_players:
    st.info("No players to show yet.")
    st.stop()

# ── Pick a player ─────────────────────────────────────────────────────────────
st.markdown('<p class="section-header">1 · Player</p>', unsafe_allow_html=True)
label = {p["player_id"]: f"{p['last_name']}, {p['first_name']}" + ("" if p["active"] else " (inactive)")
         for p in all_players}
ids = [p["player_id"] for p in all_players]
player_id = st.selectbox("Player", ids, index=0 if len(ids) == 1 else None,
                         format_func=lambda i: label[i], placeholder="Type to search…")
if not player_id:
    st.stop()
player = next(p for p in all_players if p["player_id"] == player_id)
editable = can_edit(access, player_id)

if not editable:
    st.info("You can view this profile but not change it.")


def opt_index(options, value):
    return options.index(value) if value in options else None


# ── Profile ───────────────────────────────────────────────────────────────────
st.markdown('<p class="section-header">2 · Profile</p>', unsafe_allow_html=True)

guardian_rows = []
if access["is_coach"]:
    guardian_rows = (sb.table("player_guardians").select("email").eq("player_id", player_id)
                     .execute().data or [])

if editable:
    with st.form(f"profile_{player_id}"):
        c1, c2 = st.columns(2)
        first = c1.text_input("First name", value=player["first_name"])
        last = c2.text_input("Last name", value=player["last_name"])
        c3, c4, c5 = st.columns(3)
        coach_ids = [c["coach_id"] for c in coaches]
        loc_ids = [l["location_id"] for l in locations]
        prog_ids = [p["program_id"] for p in programs]
        coach_id = c3.selectbox("Coach", coach_ids, index=opt_index(coach_ids, player["coach_id"]),
                                format_func=lambda i: coach_by_id[i], placeholder="Select a coach…")
        location_id = c4.selectbox("Location", loc_ids, index=opt_index(loc_ids, player["location_id"]),
                                   format_func=lambda i: loc_by_id[i], placeholder="Select a location…")
        program_id = c5.selectbox("Program", prog_ids, index=opt_index(prog_ids, player["program_id"]),
                                  format_func=lambda i: prog_by_id[i], placeholder="Select a program…")

        if access["is_coach"]:
            st.markdown("**Logins (coaches only)**")
            c6, c7 = st.columns(2)
            login_email = c6.text_input(
                "Player's login email", value=player.get("email") or "",
                help="The email this player uses to log in. Leave blank if they don't have a login.")
            guardians_text = c7.text_area(
                "Parent / guardian login emails", value="\n".join(g["email"] for g in guardian_rows),
                help="One per line. Each parent who logs in with one of these emails can view this player's data.",
                height=100)
            active = st.checkbox("Active player", value=player["active"])
        saved = st.form_submit_button("💾 Save profile", type="primary")

    if saved:
        if not (first.strip() and last.strip()):
            st.error("First and last name are required.")
        else:
            try:
                if not can_edit(access, player_id):          # re-check on the server side
                    raise PermissionError("Not allowed.")
                update = {"first_name": first.strip(), "last_name": last.strip(),
                          "coach_id": coach_id, "location_id": location_id, "program_id": program_id}
                if access["is_coach"]:
                    login_clean = login_email.strip().lower()
                    guardians = parse_emails(guardians_text)
                    bad = [e for e in ([login_clean] if login_clean else []) + guardians if not valid_email(e)]
                    if bad:
                        raise ValueError("These don't look like email addresses: " + ", ".join(bad))
                    update["email"] = login_clean or None
                    update["active"] = active
                sb.table("players").update(update).eq("player_id", player_id).execute()

                if access["is_coach"]:
                    have = {g["email"] for g in guardian_rows}
                    for e in set(guardians) - have:
                        sb.table("player_guardians").insert({"player_id": player_id, "email": e}).execute()
                    for e in have - set(guardians):
                        sb.table("player_guardians").delete().eq("player_id", player_id).eq("email", e).execute()
                st.cache_data.clear()
                st.success("Profile saved.")
                st.rerun()
            except Exception as e:
                msg = str(e)
                if "idx_players_email" in msg or "duplicate" in msg.lower():
                    msg = "That login email is already used by another player."
                st.error(f"Couldn't save: {msg}")
else:
    cols = st.columns(3)
    cols[0].markdown(f"**Name**  \n{player['first_name']} {player['last_name']}")
    cols[1].markdown(f"**Coach**  \n{coach_by_id.get(player['coach_id'], '-')}")
    cols[2].markdown(f"**Location / Program**  \n{loc_by_id.get(player['location_id'], '-')} / "
                     f"{prog_by_id.get(player['program_id'], '-')}")


# ── Sessions ──────────────────────────────────────────────────────────────────
st.markdown('<p class="section-header">3 · Sessions</p>', unsafe_allow_html=True)
sessions = (sb.table("sessions")
            .select("session_id, session_date, week, location_id, program_id, launch_monitor_type, raw_file_path")
            .eq("player_id", player_id).order("session_date", desc=True).execute().data or [])
if not sessions:
    st.info("No sessions saved for this player yet.")
    st.stop()

st.dataframe(
    [{"Date": datetime.strptime(s["session_date"], "%Y-%m-%d").strftime("%b %d, %Y"),
      "Week": s.get("week") or "", "Location": loc_by_id.get(s["location_id"], ""),
      "Program": prog_by_id.get(s["program_id"], ""), "Launch monitor": s["launch_monitor_type"]}
     for s in sessions],
    hide_index=True, use_container_width=True,
)

if editable:
    with st.expander("✏️ Edit a session's details"):
        sess_label = {s["session_id"]: datetime.strptime(s["session_date"], "%Y-%m-%d").strftime("%b %d, %Y")
                      + (f" · Week {s['week']}" if s.get("week") else "") for s in sessions}
        sid = st.selectbox("Session", [s["session_id"] for s in sessions], format_func=lambda i: sess_label[i])
        s = next(x for x in sessions if x["session_id"] == sid)
        with st.form(f"session_{sid}"):
            m1, m2, m3, m4 = st.columns(4)
            new_date = m1.date_input("Session date", value=datetime.strptime(s["session_date"], "%Y-%m-%d").date())
            new_week = m2.text_input("Week", value=s.get("week") or "")
            loc_ids = [l["location_id"] for l in locations]
            prog_ids = [p["program_id"] for p in programs]
            new_loc = m3.selectbox("Location", loc_ids, index=opt_index(loc_ids, s["location_id"]),
                                   format_func=lambda i: loc_by_id[i], placeholder="Select…")
            new_prog = m4.selectbox("Program", prog_ids, index=opt_index(prog_ids, s["program_id"]),
                                    format_func=lambda i: prog_by_id[i], placeholder="Select…")
            if st.form_submit_button("💾 Save session details"):
                try:
                    if not can_edit(access, player_id):
                        raise PermissionError("Not allowed.")
                    (sb.table("sessions")
                       .update({"session_date": new_date.isoformat(), "week": new_week.strip() or None,
                                "location_id": new_loc, "program_id": new_prog})
                       .eq("session_id", sid).eq("player_id", player_id).execute())
                    st.cache_data.clear()
                    st.success("Session details saved.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't save: {e}")

if access["is_coach"]:
    with st.expander("🗑️ Delete a session (coaches only)"):
        st.caption("This permanently removes the session and all of its shots. Use it to fix a mistaken upload.")
        del_id = st.selectbox("Session to delete", [s["session_id"] for s in sessions],
                              format_func=lambda i: next(
                                  datetime.strptime(x["session_date"], "%Y-%m-%d").strftime("%b %d, %Y")
                                  for x in sessions if x["session_id"] == i),
                              key="del_session")
        sure = st.checkbox("I understand this can't be undone", key="del_sure")
        if st.button("Delete session", disabled=not sure):
            try:
                row = next(x for x in sessions if x["session_id"] == del_id)
                sb.table("sessions").delete().eq("session_id", del_id).eq("player_id", player_id).execute()
                if row.get("raw_file_path"):
                    try:
                        sb.storage.from_("raw-csvs").remove([row["raw_file_path"]])
                    except Exception:
                        pass
                st.cache_data.clear()
                st.success("Session deleted.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't delete: {e}")

if editable:
    st.caption("To change this player's targets, open the **Report Generator** page, adjust the clubs, "
               "and use *Save as this player's targets*.")
