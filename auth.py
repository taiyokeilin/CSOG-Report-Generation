"""Login (Auth0, through Streamlit's built-in st.login) and role-based access.

Who someone is comes from their verified login email:
  * coach / admin : email is in the `coaches` table (active)
  * player        : email is on their own row in `players`
  * parent        : email is listed in `player_guardians` for the child

Every page calls require_login() first, then uses filter_players() to limit
which players the person can pick, and can_edit() before changing anything.

Secrets needed (Streamlit secrets editor):
  [auth]
  redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
  cookie_secret = "a long random string"
  [auth.auth0]
  client_id = "..."
  client_secret = "..."
  server_metadata_url = "https://YOUR-TENANT.us.auth0.com/.well-known/openid-configuration"
  client_kwargs = { "prompt" = "login" }
"""
import streamlit as st

from db import get_supabase

PROVIDER = "auth0"
DEFAULT_PAGE = "pages/3_Practice_Dashboard.py"   # where players and parents land


def _norm(value) -> str:
    return (value or "").strip().lower()


def verified_email(user):
    """The login email if we can trust it, otherwise None.

    Email/password accounts in Auth0 are created by an administrator (sign-ups are
    switched off), so we trust those. Anything else (such as "Continue with Google")
    must come with a provider-verified email.
    """
    email = _norm(getattr(user, "email", None))
    if not email:
        return None
    if bool(getattr(user, "email_verified", False)):
        return email
    if str(getattr(user, "sub", "") or "").startswith("auth0|"):
        return email
    return None


@st.cache_data(ttl=60)
def _lookup(email: str) -> dict:
    sb = get_supabase()
    coaches = (sb.table("coaches")
               .select("coach_id, first_name, last_name, email, role, active")
               .eq("email", email).limit(1).execute().data or [])
    coach = coaches[0] if coaches and coaches[0].get("active") else None

    players = (sb.table("players").select("player_id, first_name, last_name")
               .eq("email", email).eq("active", True).limit(1).execute().data or [])
    player = players[0] if players else None

    links = sb.table("player_guardians").select("player_id").eq("email", email).execute().data or []
    child_ids = [r["player_id"] for r in links]
    if child_ids:
        kids = (sb.table("players").select("player_id")
                .in_("player_id", child_ids).eq("active", True).execute().data or [])
        child_ids = [k["player_id"] for k in kids]
    return {"coach": coach, "player": player, "child_ids": child_ids}


def build_access(email: str, display_name: str = "") -> dict:
    found = _lookup(email)
    coach, player, kids = found["coach"], found["player"], found["child_ids"]
    if coach:
        name, role = f"{coach['first_name']} {coach['last_name']}", coach["role"].capitalize()
    elif player:
        name, role = f"{player['first_name']} {player['last_name']}", "Player"
    else:
        name, role = display_name or email, "Parent"
    return {
        "email": email, "name": name, "role_label": role,
        "coach": coach,
        "is_coach": bool(coach),
        "is_admin": bool(coach and coach.get("role") == "admin"),
        "player_id": player["player_id"] if player else None,
        "child_ids": kids,
    }


def has_any_access(a: dict) -> bool:
    return a["is_coach"] or bool(a["player_id"]) or bool(a["child_ids"])


def viewable_ids(a: dict):
    """None means 'every player' (coaches). Otherwise the set of player ids this person may view."""
    if a["is_coach"]:
        return None
    ids = set(a["child_ids"])
    if a["player_id"]:
        ids.add(a["player_id"])
    return ids


def filter_players(a: dict, players: list) -> list:
    ids = viewable_ids(a)
    return players if ids is None else [p for p in players if p["player_id"] in ids]


def can_view(a: dict, player_id: str) -> bool:
    ids = viewable_ids(a)
    return ids is None or player_id in ids


def can_edit(a: dict, player_id: str) -> bool:
    """Coaches edit anyone; a player edits only themselves; parents never edit."""
    return a["is_coach"] or (a["player_id"] is not None and a["player_id"] == player_id)


PORTAL_NAME = "Chicago School of Golf Practice Portal"


def apply_branding():
    """Show the portal name in the top bar, on the same line as Share and the menu, on every page."""
    st.markdown(f"""
<style>
    header[data-testid="stHeader"]::before {{
        content: "{PORTAL_NAME}";
        position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
        font-size: 1.15rem; font-weight: 700; color: #1F4E79; letter-spacing: 0.02em;
        white-space: nowrap; pointer-events: none;
    }}
    @media (max-width: 900px) {{ header[data-testid="stHeader"]::before {{ font-size: 0.95rem; }} }}
    @media (max-width: 640px) {{ header[data-testid="stHeader"]::before {{ display: none; }} }}
</style>""", unsafe_allow_html=True)


def _login_screen():
    st.title("Golf Coaching Portal")
    st.write("Please log in to continue.")
    st.button("Log in", on_click=st.login, args=(PROVIDER,), type="primary")
    st.stop()


def _logout_button(key: str):
    st.button("Log out", on_click=st.logout, key=key)


def require_login(coach_only: bool = False, admin_only: bool = False) -> dict:
    """Stop the page unless a logged-in person with access is present. Returns their access dict."""
    apply_branding()
    if not getattr(st.user, "is_logged_in", False):
        _login_screen()

    email = verified_email(st.user)
    if not email:
        st.error("We couldn't confirm your email address, so we can't log you in. "
                 "Please check that you've verified your email, or ask your coach for help.")
        _logout_button("lo_unverified")
        st.stop()

    try:
        a = build_access(email, getattr(st.user, "name", "") or "")
    except Exception as e:
        st.error(f"Couldn't check your access right now. Please try again in a minute. ({e})")
        st.stop()

    if not has_any_access(a):
        st.error(f"{email} hasn't been given access yet. Ask your coach to add this email address.")
        _logout_button("lo_denied")
        st.stop()

    if (coach_only or admin_only) and not a["is_coach"]:
        try:
            st.switch_page(DEFAULT_PAGE)
        except Exception:
            st.info("This page is for coaches. Use the menu on the left to open your progress.")
            st.stop()
    if admin_only and not a["is_admin"]:
        st.error("This page is for administrators only.")
        st.stop()

    st.session_state["access"] = a
    with st.sidebar:
        st.caption(f"Logged in as **{a['name']}** ({a['role_label']})")
        _logout_button("lo_sidebar")
    return a
