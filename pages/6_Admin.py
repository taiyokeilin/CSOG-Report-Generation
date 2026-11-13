import re

import streamlit as st

from auth import require_login
from db import get_supabase

st.set_page_config(page_title="Admin", page_icon="⛳", layout="wide")
access = require_login(admin_only=True)

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1.5rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
</style>
""", unsafe_allow_html=True)
st.markdown('<p class="main-header">⛳ Admin</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Manage coaches, locations and programs.</p>', unsafe_allow_html=True)

sb = get_supabase()


def valid_email(e: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e))


# ── Coaches ───────────────────────────────────────────────────────────────────
st.markdown('<p class="section-header">Coaches</p>', unsafe_allow_html=True)
locations = sb.table("locations").select("location_id, name").eq("active", True).order("name").execute().data or []
loc_by_id = {l["location_id"]: l["name"] for l in locations}
coaches = sb.table("coaches").select("coach_id, first_name, last_name, email, role, active, location_id").execute().data or []
coaches.sort(key=lambda c: (c["last_name"].lower(), c["first_name"].lower()))

st.dataframe(
    [{"Name": f"{c['first_name']} {c['last_name']}", "Login email": c["email"], "Role": c["role"],
      "Active": "Yes" if c["active"] else "No", "Location": loc_by_id.get(c["location_id"], "")} for c in coaches],
    hide_index=True, use_container_width=True,
)
st.caption("A coach logs in with the email shown here, so it must match the email on their login account.")

with st.expander("➕ Add a coach"):
    with st.form("add_coach", clear_on_submit=True):
        a1, a2, a3 = st.columns(3)
        first = a1.text_input("First name")
        last = a2.text_input("Last name")
        email = a3.text_input("Login email")
        b1, b2 = st.columns(2)
        role = b1.selectbox("Role", ["coach", "admin"])
        loc = b2.selectbox("Location", [l["location_id"] for l in locations], index=None,
                           format_func=lambda i: loc_by_id[i], placeholder="Optional")
        if st.form_submit_button("Add coach", type="primary"):
            e = email.strip().lower()
            if not (first.strip() and last.strip() and valid_email(e)):
                st.error("Enter a first name, last name and a valid email.")
            else:
                try:
                    sb.table("coaches").insert({"first_name": first.strip(), "last_name": last.strip(),
                                                "email": e, "role": role, "location_id": loc}).execute()
                    st.success(f"Added {first.strip()} {last.strip()}. Remember to create their login too.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Couldn't add coach (is the email already used?): {ex}")

with st.expander("✏️ Change a coach's role or access"):
    if coaches:
        cid = st.selectbox("Coach", [c["coach_id"] for c in coaches],
                           format_func=lambda i: next(f"{c['first_name']} {c['last_name']}" for c in coaches if c["coach_id"] == i))
        c = next(x for x in coaches if x["coach_id"] == cid)
        is_me = c["email"] == access["email"]
        with st.form(f"edit_coach_{cid}"):
            new_role = st.selectbox("Role", ["coach", "admin"], index=0 if c["role"] == "coach" else 1, disabled=is_me)
            new_active = st.checkbox("Active (can log in)", value=c["active"], disabled=is_me)
            if is_me:
                st.caption("You can't change your own role or deactivate yourself, so you can't lock yourself out.")
            if st.form_submit_button("Save") and not is_me:
                sb.table("coaches").update({"role": new_role, "active": new_active}).eq("coach_id", cid).execute()
                st.cache_data.clear()
                st.success("Saved. The change takes effect within a minute.")
                st.rerun()

# ── Locations and programs ────────────────────────────────────────────────────
for table, id_col, title in (("locations", "location_id", "Locations"), ("programs", "program_id", "Programs")):
    st.markdown(f'<p class="section-header">{title}</p>', unsafe_allow_html=True)
    rows = sb.table(table).select(f"{id_col}, name, active").order("name").execute().data or []
    st.dataframe([{"Name": r["name"], "Active": "Yes" if r["active"] else "No"} for r in rows],
                 hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    with c1.form(f"add_{table}", clear_on_submit=True):
        name = st.text_input(f"Add to {title.lower()}")
        if st.form_submit_button("Add") and name.strip():
            try:
                sb.table(table).insert({"name": name.strip()}).execute()
                st.rerun()
            except Exception as ex:
                st.error(f"Couldn't add (does it already exist?): {ex}")
    with c2.form(f"toggle_{table}"):
        pick = st.selectbox("Switch on or off", [r[id_col] for r in rows],
                            format_func=lambda i: next(f"{r['name']} ({'active' if r['active'] else 'inactive'})"
                                                       for r in rows if r[id_col] == i)) if rows else None
        if st.form_submit_button("Toggle active") and pick:
            cur = next(r for r in rows if r[id_col] == pick)
            sb.table(table).update({"active": not cur["active"]}).eq(id_col, pick).execute()
            st.rerun()
