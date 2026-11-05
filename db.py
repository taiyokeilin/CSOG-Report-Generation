"""Shared Supabase client.

Secrets required (Streamlit secrets editor):
  [supabase]
  url = "https://xxxx.supabase.co"
  key = "<service_role key>"   # server-side only; never the anon key
"""
import streamlit as st
from supabase import create_client


@st.cache_resource
def get_supabase():
    cfg = st.secrets["supabase"]
    return create_client(cfg["url"], cfg["key"])
