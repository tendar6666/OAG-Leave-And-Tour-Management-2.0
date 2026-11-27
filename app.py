# Force IPv4 network resolution to prevent Windows IPv6 SYN timeouts to Google APIs
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(*args, **kwargs):
    return [r for r in _orig_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_getaddrinfo

import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, timedelta, date, timezone
import threading
import uuid
import calendar
import requests
import json
import html
import re
import inspect
import math
import random
import time

try:
    from zoneinfo import ZoneInfo
    CTA_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    CTA_TZ = timezone(timedelta(hours=5, minutes=30))

_AUTO_ROLLOVER_LOCK = threading.Lock()

import extra_streamlit_components as stx
cookie_manager = stx.CookieManager(key="main_cookie_manager")

if "pending_cookie" in st.session_state:
    expire_date = datetime.now(CTA_TZ) + timedelta(days=60)
    cookie_manager.set("auth_token", st.session_state.pending_cookie, key="set_auth_token", expires_at=expire_date)
    del st.session_state.pending_cookie

if "pending_gmail_cookie" in st.session_state:
    expire_date = datetime.now(CTA_TZ) + timedelta(days=60)
    cookie_manager.set("cta_verified_gmail", st.session_state.pending_gmail_cookie, key="set_verified_gmail", expires_at=expire_date)
    del st.session_state.pending_gmail_cookie

if st.session_state.get("pending_cookie_delete"):
    try:
        cookie_manager.delete("auth_token", key="del_auth_token")
    except Exception:
        pass
    if st.session_state.get("pending_full_signout"):
        try:
            cookie_manager.delete("cta_verified_gmail", key="del_verified_gmail")
        except Exception:
            pass
        del st.session_state["pending_full_signout"]
    del st.session_state["pending_cookie_delete"]


ENABLE_NTFY = True  # Targeted user-specific notifications active

# --- DEVELOPMENT MODE CONFIGURATION ---
# Set to True during development for easy 1-click login and instant user switching.
# Set to False (or remove) before final production deployment.
DEV_MODE = False

def get_user_ntfy_topic(user_name):
    """Retrieves the unique personal NTFY topic for a specific user from Users worksheet."""
    if not user_name:
        return None
    users = load_users_data()
    if users is not None and not users.empty and "Name" in users.columns and "NtfyTopic" in users.columns:
        match = users[users["Name"].astype(str).str.strip() == str(user_name).strip()]
        if not match.empty:
            val = str(match.iloc[0]["NtfyTopic"]).strip()
            if val and val not in ["nan", "None", ""]:
                return val
    return None

def get_role_ntfy_topics(role_name):
    """Retrieves list of NTFY topics for all users with a specific role (e.g. 'Admin', 'Accounts')."""
    users = load_users_data()
    topics = []
    if users is not None and not users.empty and "Role" in users.columns and "NtfyTopic" in users.columns:
        match = users[users["Role"].astype(str).str.strip().str.lower() == str(role_name).strip().lower()]
        for _, r in match.iterrows():
            t = str(r.get("NtfyTopic", "")).strip()
            if t and t not in ["nan", "None", ""]:
                topics.append(t)
    return topics

def generate_user_ntfy_topic():
    """Generates a new unique random 5-character topic (e.g. oag_leave_9b2es)."""
    import secrets
    import string
    chars = string.ascii_lowercase + string.digits
    return f"oag_leave_{''.join(secrets.choice(chars) for _ in range(5))}"

def send_ntfy_notification(topic, title, message):
    if not ENABLE_NTFY:
        return
    try:
        if not topic:
            return
        if isinstance(topic, (list, set, tuple)):
            for t in topic:
                if t and str(t).strip() and str(t).strip() not in ["nan", "None"]:
                    try:
                        requests.post(
                            f"https://ntfy.sh/{str(t).strip()}",
                            data=message.encode('utf-8'),
                            headers={"Title": title.encode('utf-8')},
                            timeout=5
                        )
                    except Exception:
                        pass
            return
        
        t_str = str(topic).strip()
        if t_str and t_str not in ["nan", "None"]:
            requests.post(
                f"https://ntfy.sh/{t_str}",
                data=message.encode('utf-8'),
                headers={"Title": title.encode('utf-8')},
                timeout=5
            )
    except Exception as e:
        print(f"NTFY Error: {e}")


import io
from fpdf import FPDF

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Leave Management System",
                    page_icon="📅", layout="wide")

# --- INITIALIZE SESSION STATE ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_name" not in st.session_state:
    st.session_state.user_name = None
if "user_role" not in st.session_state:
    st.session_state.user_role = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None

# --- DATABASE CONNECTION ---
conn = st.connection("gsheets", type=GSheetsConnection)

_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
_TRANSIENT_ERR_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "resource_exhausted",
    "quota exceeded",
    "rate limit",
    "too many requests",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "timeout",
    "connection reset",
    "temporarily unavailable",
)


def _is_transient_gsheets_error(exc):
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_HTTP_STATUSES
    if "worksheetnotfound" in type(exc).__name__.lower():
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    if "worksheetnotfound" in msg or "worksheet not found" in msg or "404" in msg or "403" in msg:
        return False
    return any(marker in msg for marker in _TRANSIENT_ERR_MARKERS)


def _gsheets_retry(fn, *args, max_attempts=4, base_delay=0.5, max_delay=8.0, **kwargs):
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            attempt += 1
            if attempt >= max_attempts or not _is_transient_gsheets_error(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0.0, delay * 0.25)
            time.sleep(delay)


def _gsheets_read(active_conn, worksheet, **kwargs):
    read_kwargs = dict(kwargs)
    if "ttl" not in read_kwargs:
        accepts_ttl = True
        try:
            sig = inspect.signature(active_conn.read)
            params = sig.parameters
            accepts_ttl = (
                "ttl" in params
                or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            )
        except (ValueError, TypeError):
            accepts_ttl = True
        if accepts_ttl:
            read_kwargs["ttl"] = 0
    try:
        return _gsheets_retry(active_conn.read, worksheet=worksheet, **read_kwargs)
    except TypeError as te:
        if "ttl" in read_kwargs and "ttl" in str(te):
            read_kwargs.pop("ttl", None)
            return _gsheets_retry(active_conn.read, worksheet=worksheet, **read_kwargs)
        raise


def _clear_fn_cache(fn_name):
    fn = globals().get(fn_name)
    clear_fn = getattr(fn, "clear", None)
    if callable(clear_fn):
        try:
            clear_fn()
        except Exception:
            pass


def _invalidate_sheet_cache(*sheet_names):
    mapping = {
        "Users": ("load_users_data",),
        "Staff_Master": ("load_staff_master",),
        "LeaveRules": ("_read_leave_rules_sheet",),
        "AuditLog": ("_load_audit_log_cached", "load_audit_log"),
        "LeaveRequests": ("load_leave_requests",),
        "TourSanctions": ("load_tour_sanctions",),
        "Titles": ("_list_spreadsheet_sheet_titles",),
    }
    cleared = set()
    for name in sheet_names:
        if name in mapping:
            targets = mapping[name]
        elif str(name).startswith(("LeaveRequests_Archive_", "TourSanctions_Archive_", "AuditLog_Archive_")):
            targets = ("_list_spreadsheet_sheet_titles", "_read_archive_worksheet_cached")
        else:
            targets = ()
        for fn_name in targets:
            if fn_name not in cleared:
                _clear_fn_cache(fn_name)
                cleared.add(fn_name)


def _invalidate_all_data_caches():
    _invalidate_sheet_cache(
        "Users", "Staff_Master", "LeaveRules", "AuditLog",
        "LeaveRequests", "TourSanctions", "Titles",
    )
    _clear_fn_cache("_read_archive_worksheet_cached")

BRANCH_NAMES = {
    "HQ": "དབུས། (Headquarters)",
    "SB": "South Branch (ལྷོ་ཕྱོགས་ཡན་ལག)",
    "NB": "Nepal Branch (བལ་ཡུལ་ཡན་ལག)",
    "DB": "Dekyiling Branch (བདེ་སྐྱིད་གླིང་ཡན་ལག)"
}

OFFICIAL_CTA_POSTS = [
    "རྩིས་ཞིབ་སྤྱི་ཁྱབ་པ།",
    "དྲུང་ཆེ།",
    "དྲུང་འཕར།",
    "ཟུང་དྲུང་།",
    "དྲུང་གཞོན།",
    "དྲུང་ལས།",
    "སྡེ་འགན།",
    "ལས་དོ།",
    "ལས་དྲུང་།",
    "རྒན་དྲུང་།",
    "གཞོན་དྲུང་།",
    "ལས་བྱ།",
]


@st.cache_data(ttl=60)
def load_users_data():
    try:
        df = _gsheets_read(conn, "Users")
        if df is None:
            return None
        df = df.dropna(subset=["Name", "PIN"])
        if "PIN" in df.columns:
            df["PIN"] = df["PIN"].apply(lambda x: str(int(float(x))) if pd.notnull(x) and str(x).replace('.0','').isdigit() else str(x) if pd.notnull(x) else '')
        if "NtfyTopic" not in df.columns:
            df["NtfyTopic"] = ""
        if "Branch" not in df.columns:
            df["Branch"] = "HQ"
        df["Branch"] = df["Branch"].fillna("HQ")
        if "Email" not in df.columns:
            df["Email"] = ""
        df["Email"] = df["Email"].fillna("").astype(str).str.strip().str.lower()
        return df
    except Exception as e:
        st.error(f"Error connecting to database (Users): {e}")
        return None


BRANCH_FILTER_ORDER = ["HQ", "DB", "NB", "SB"]
BRANCH_FILTER_LABELS = {
    "HQ": "དབུས། (HQ)",
    "DB": "DB",
    "NB": "NB",
    "SB": "SB",
}


def format_branch_scope(branches):
    """Formats a list of branch codes for display, designating HQ as དབུས། (HQ)."""
    if not branches:
        return "None"
    return ", ".join(BRANCH_FILTER_LABELS.get(b, b) for b in branches)


def normalize_branch_code(branch_val):
    """Normalizes a branch string into HQ, DB, NB, or SB (defaults to HQ)."""
    b = str(branch_val).strip().upper() if branch_val is not None else "HQ"
    return b if b in ("SB", "NB", "DB", "HQ") else "HQ"


def get_branch_accounts_topics(branch_code):
    """Retrieves list of NTFY topics for Accounts users in a specific branch, falling back to all Accounts."""
    users = load_users_data()
    topics = []
    if users is not None and not users.empty and "Role" in users.columns:
        norm_b = normalize_branch_code(branch_code)
        match = users[
            (users["Role"].astype(str).str.strip().str.lower().isin(["accounts", "accountant"])) &
            (users["Branch"].apply(normalize_branch_code) == norm_b)
        ]
        for _, r in match.iterrows():
            t = str(r.get("NtfyTopic", "")).strip()
            if t and t not in ["nan", "None", ""]:
                topics.append(t)
    if not topics:
        topics = get_role_ntfy_topics("Accounts")
    return topics


def build_user_branch_map(users_df, staff_df=None):
    """Builds a {user_name: branch_code} lookup dict, preferring Users over Staff_Master."""
    branch_map = {}
    if staff_df is not None and not staff_df.empty and "Name" in staff_df.columns:
        has_branch = "Branch" in staff_df.columns
        for _, row in staff_df.iterrows():
            nm = str(row.get("Name", "")).strip()
            if nm and has_branch and pd.notnull(row.get("Branch")) and str(row.get("Branch", "")).strip() != "":
                branch_map[nm] = normalize_branch_code(row.get("Branch"))
    if users_df is not None and not users_df.empty and "Name" in users_df.columns:
        has_branch = "Branch" in users_df.columns
        for _, row in users_df.iterrows():
            nm = str(row.get("Name", "")).strip()
            if nm:
                raw_b = row.get("Branch", "HQ") if has_branch else "HQ"
                branch_map[nm] = normalize_branch_code(raw_b)
    return branch_map


def is_central_branch_filter_role(user_role, user_branch):
    """Returns True for Admin, Co-Admin, Auditor, or HQ Accounts who have access to the multi-branch filter."""
    norm_role = str(user_role or "").strip()
    norm_branch = normalize_branch_code(user_branch)
    if norm_role in ("Admin", "Co-Admin", "Auditor"):
        return True
    if norm_role in ("Accounts", "Accountant") and norm_branch == "HQ":
        return True
    return False


def resolve_visible_branches(user_role, user_branch, selected_branches=None):
    """Returns allowed branch codes for the viewer.

    - Branch Managers, Regular Employees, and Branch Accounts (SB/NB/DB) are strictly
      locked to their own branch.
    - Central Admin, Co-Admin, and Central Accounts (HQ) default to ['HQ'] unless
      explicit checkbox selections are supplied.
    """
    own_branch = normalize_branch_code(user_branch)
    if not is_central_branch_filter_role(user_role, own_branch):
        return [own_branch]
    if selected_branches is None:
        return ["HQ"]
    selected_set = {
        str(b).strip().upper()
        for b in selected_branches
        if str(b).strip().upper() in BRANCH_FILTER_ORDER
    }
    return [b for b in BRANCH_FILTER_ORDER if b in selected_set]


def filter_df_by_branches(df, allowed_branches, branch_map, name_col="Name"):
    """Filters a DataFrame to rows whose staff belong to allowed_branches (preserves original index)."""
    if df is None or df.empty or name_col not in df.columns:
        return df
    allowed_set = {normalize_branch_code(b) for b in (allowed_branches or [])}
    if not allowed_set:
        return df.iloc[0:0]

    def _row_in_scope(val):
        nm = str(val).strip() if pd.notnull(val) else ""
        if not nm:
            return False
        br = normalize_branch_code(branch_map.get(nm, "HQ"))
        return br in allowed_set

    return df[df[name_col].apply(_row_in_scope)]


def get_user_branch(user_name):
    """Returns the branch code for a user (HQ, SB, NB, DB)."""
    try:
        users_df = load_users_data()
        if users_df is not None and not users_df.empty and "Name" in users_df.columns:
            u_match = users_df[users_df["Name"] == user_name]
            if not u_match.empty:
                return normalize_branch_code(u_match.iloc[0].get("Branch", "HQ"))
    except Exception:
        pass
    return "HQ"


def get_user_branch_map():
    """Returns {user_name: branch_code} mapping from Users and Staff_Master."""
    try:
        u_df = load_users_data()
    except Exception:
        u_df = None
    try:
        s_df = load_staff_master()
    except Exception:
        s_df = None
    return build_user_branch_map(u_df, s_df)


def _ensure_branch_filter_state(viewer_name):
    """Initializes or resets central branch checkbox state when the signed-in user changes."""
    owner_key = str(viewer_name or "").strip()
    state = st.session_state.get("branch_filter_state")
    if not isinstance(state, dict) or state.get("owner") != owner_key:
        st.session_state["branch_filter_state"] = {
            "owner": owner_key,
            "HQ": True,
            "DB": False,
            "NB": False,
            "SB": False,
        }
        for k in list(st.session_state.keys()):
            if str(k).startswith("bf_chk_"):
                del st.session_state[k]
    return st.session_state["branch_filter_state"]


def _on_branch_checkbox_change(widget_key, branch_code):
    state = _ensure_branch_filter_state(st.session_state.get("user_name", ""))
    state[branch_code] = bool(st.session_state.get(widget_key, False))


def get_active_visible_branches():
    """Computes effective visible branch codes for the current signed-in user."""
    viewer_name = st.session_state.get("user_name", "")
    viewer_role = st.session_state.get("user_role", "Employee")
    viewer_branch = get_user_branch(viewer_name) if viewer_name else "HQ"
    if not is_central_branch_filter_role(viewer_role, viewer_branch):
        return resolve_visible_branches(viewer_role, viewer_branch)
    state = _ensure_branch_filter_state(viewer_name)
    checked = [b for b in BRANCH_FILTER_ORDER if state.get(b, b == "HQ")]
    return resolve_visible_branches(viewer_role, viewer_branch, checked)


def _ensure_org_cal_branch_state(viewer_name):
    """Initializes full-organization calendar checkboxes for Branch Managers and Branch Accounts."""
    owner_key = str(viewer_name or "").strip()
    state = st.session_state.get("org_cal_branch_filter_state")
    if not isinstance(state, dict) or state.get("owner") != owner_key:
        st.session_state["org_cal_branch_filter_state"] = {
            "owner": owner_key,
            "HQ": True,
            "DB": True,
            "NB": True,
            "SB": True,
        }
        for k in list(st.session_state.keys()):
            if str(k).startswith("bf_cal_chk_"):
                del st.session_state[k]
    return st.session_state["org_cal_branch_filter_state"]


def _on_org_cal_checkbox_change(widget_key, branch_code):
    state = _ensure_org_cal_branch_state(st.session_state.get("user_name", ""))
    state[branch_code] = bool(st.session_state.get(widget_key, True))


def render_branch_filter_checkboxes(key_prefix="org_cal"):
    """Renders HQ, DB, NB, SB checkboxes for Central Admin, Co-Admin, and Central Accounts,
    plus the full-organization calendar filter when Branch Manager or Branch Account views
    the Organization Calendar (`key_prefix == 'org_cal'`).
    """
    viewer_name = st.session_state.get("user_name", "")
    viewer_role = st.session_state.get("user_role", "Employee")
    viewer_branch = get_user_branch(viewer_name) if viewer_name else "HQ"

    if not is_central_branch_filter_role(viewer_role, viewer_branch):
        norm_vr = str(viewer_role or "").strip()
        allow_org_view = (
            (key_prefix == "org_cal" and norm_vr in ("Branch Manager", "Accounts", "Accountant"))
            or (key_prefix == "bm_org_avail" and norm_vr == "Branch Manager")
        )
        if allow_org_view:
            cal_state = _ensure_org_cal_branch_state(viewer_name)
            safe_user = "".join(ch if ch.isalnum() else "_" for ch in str(viewer_name))
            with st.container(border=True):
                cols = st.columns([1.4, 1, 1, 1, 1])
                with cols[0]:
                    st.markdown("**Branch Filter:**")
                for idx, b_code in enumerate(BRANCH_FILTER_ORDER):
                    wkey = f"bf_cal_chk_{key_prefix}_{safe_user}_{b_code}"
                    st.session_state[wkey] = bool(cal_state.get(b_code, True))
                    with cols[idx + 1]:
                        st.checkbox(
                            BRANCH_FILTER_LABELS.get(b_code, b_code),
                            key=wkey,
                            help=BRANCH_NAMES.get(b_code, b_code),
                            on_change=_on_org_cal_checkbox_change,
                            args=(wkey, b_code),
                        )
            checked_cal = [b for b in BRANCH_FILTER_ORDER if cal_state.get(b, True)]
            if not checked_cal:
                st.warning("No branches selected. Check དབུས། (HQ), DB, NB, or SB above to view calendar events.")
            return checked_cal
        return resolve_visible_branches(viewer_role, viewer_branch)

    state = _ensure_branch_filter_state(viewer_name)
    safe_user = "".join(ch if ch.isalnum() else "_" for ch in str(viewer_name))

    with st.container(border=True):
        cols = st.columns([1.4, 1, 1, 1, 1])
        with cols[0]:
            st.markdown("**Branch Filter:**")
        for idx, b_code in enumerate(BRANCH_FILTER_ORDER):
            wkey = f"bf_chk_{key_prefix}_{safe_user}_{b_code}"
            desired_val = bool(state.get(b_code, b_code == "HQ"))
            if wkey not in st.session_state:
                st.session_state[wkey] = desired_val
            with cols[idx + 1]:
                st.checkbox(
                    BRANCH_FILTER_LABELS.get(b_code, b_code),
                    key=wkey,
                    help=BRANCH_NAMES.get(b_code, b_code),
                    on_change=_on_branch_checkbox_change,
                    args=(wkey, b_code),
                )

    checked = [b for b in BRANCH_FILTER_ORDER if state.get(b, b == "HQ")]
    if not checked:
        st.warning("No branches selected. Check དབུས། (HQ), DB, NB, or SB above to view staff records.")
    return resolve_visible_branches(viewer_role, viewer_branch, checked)


def get_branch_manager(branch_code):
    """Returns (manager_name, ntfy_topic) for the given branch."""
    try:
        users_df = load_users_data()
        if users_df is not None and not users_df.empty:
            m_match = users_df[(users_df["Branch"] == branch_code) & (users_df["Role"] == "Branch Manager")]
            if not m_match.empty:
                return m_match.iloc[0]["Name"], str(m_match.iloc[0].get("NtfyTopic", ""))
    except Exception:
        pass
    return None, None


@st.cache_data(ttl=60)
def load_staff_master():
    df = _gsheets_read(conn, "Staff_Master")
    if df is not None and not df.empty:
        if "Branch" not in df.columns:
            df["Branch"] = "HQ"
        for c in df.columns:
            if c not in ("Name", "Post", "JoiningDate", "Branch", "Active_FY"):
                df[c] = df[c].astype(object)
    return df


LEAVE_RULES_COLUMNS = [
    "LeaveType",
    "Label",
    "Annual_Quota",
    "Is_Accumulated",
    "Max_Accumulation_Cap",
    "Affects_Service_Year",
    "Earned_During_Full_Year_UL",
    "Allow_Encashment",
    "Pro_Rata_UL_Penalty",
    "Strict_Balance_Required",
    "Max_Consecutive_Days",
    "Non_Combinable",
    "UL_Milestone_Interval_Years",
    "UL_Max_Milestone_Years",
    "UL_Grant_Per_Milestone",
    "UL_Career_Max_Cap",
    "Service_Freeze_Rule",
    "Auto_Apply_Pro_Rata_UL_Penalty",
]

STAFF_MASTER_FIELD_HELP = {
    "Name": (
        "Note: Official full name of the staff member (supports Tibetan Unicode).\n"
        "Formula: Primary key linking Users, Staff_Master, LeaveRequests, and TourSanctions."
    ),
    "Post": (
        "Note: Official designation / rank of the staff member (e.g. ལས་དོ། / Auditor).\n"
        "Formula: Synced between Users and Staff_Master."
    ),
    "JoiningDate": (
        "Note: Official CTA appointment date used as the baseline for service tenure and 5-year UL milestones.\n"
        "Formula: Service Year = max(0, Today - JoiningDate) / 365.25"
    ),
    "Branch": (
        "Note: Assigned office branch (དབུས། HQ, DB, NB, SB). Controls branch-level approval routing and visibility.\n"
        "Formula: Normalized to HQ, DB, NB, or SB."
    ),
    "Role": (
        "Note: System access level (Employee, Co-Admin, Accounts, Branch Manager, Admin).\n"
        "Formula: Determines dashboard permissions and approval authority."
    ),
    "PIN": (
        "Note: Personal 4-digit numeric unlock passcode for Tier-2 login verification.\n"
        "Formula: Exact 4 numeric digits (0000–9999)."
    ),
    "Email": (
        "Note: Authorized Google / Gmail address for Tier-1 perimeter security.\n"
        "Formula: Must match a registered @gmail.com / Google Workspace account."
    ),
    "Active_FY": (
        "Note: Active Financial Year stamp for this staff member's ledger (April 1 to March 31).\n"
        "Formula: Auto-rolls over at midnight March 31 -> April 1 IST when Active_FY < Current FY."
    ),
    "Service Year": (
        "Note: Total calendar years elapsed since the employee's Official Joining Date (before deducting service penalties).\n"
        "Formula: Service Year = round(max(0, Today - JoiningDate) / 365.25, 2)"
    ),
    "Service Year Penalty": (
        "Note: Total leave days that delay service tenure (prior Cumulative_UL + elapsed active-FY Used_UL + prior Cumulative Service-Affecting Leave + elapsed active-FY Service-Affecting Leave).\n"
        "Formula: Service Year Penalty = Cumulative_UL + Elapsed_Used_UL + Cumulative_Service_Affecting_Leave + Elapsed_Current_FY_Service_Affecting_Leave"
    ),
    "Actual Service Year": (
        "Note: Effective net service tenure in years after deducting Service Year Penalty days. Used to trigger 5, 10, 15, and 20-year Unpaid Leave (UL) milestone grants. Freezes smoothly during active leave without regressing.\n"
        "Formula: Actual Service Year = round(max(0, (Today - JoiningDate) - Service Year Penalty) / 365.25, 2)"
    ),
    "Opening_CL": (
        "Note: Carried-over Casual Leave (CL) balance at the start of the Financial Year (April 1). Under default CTA rules, CL is non-accumulated and resets to 0.0 each new FY.\n"
        "Formula: Opening_CL = 0.0 (if Is_Accumulated = No) or min(Max_Cap, Prior Balance_CL) (if Is_Accumulated = Yes)"
    ),
    "Addition_CL": (
        "Note: Annual Casual Leave quota added on April 1 for the active Financial Year (default 9.0 days/FY; 0.0 if on full-year UL).\n"
        "Formula: Addition_CL = Annual_CL_Quota (9.0d), or 0.0 if on Full-Year UL"
    ),
    "Penalty_CL(UL Effect)": (
        "Note: Pro-rata Casual Leave deduction caused by taking Unpaid Leave (UL) during the year.\n"
        "Formula: Penalty_CL = ROUND((Used_UL / 30) * Pro_Rata_CL_Penalty [0.75d] * 2) / 2"
    ),
    "Used_CL": (
        "Note: Total approved Casual Leave days taken (net of early-return reversals) in the active Financial Year.\n"
        "Formula: Used_CL = Sum of Approved CL days in active FY"
    ),
    "Balance_CL": (
        "Note: Remaining available Casual Leave balance for the active Financial Year. Editing Balance_CL directly adjusts Opening_CL automatically.\n"
        "Formula: Balance_CL = max(0.0, Opening_CL + Addition_CL - Penalty_CL(UL Effect) - Used_CL)"
    ),
    "Opening_SL": (
        "Note: Carried-over Sick Leave (SL) balance at the start of the Financial Year (April 1). Under default CTA rules, SL is non-accumulated and resets to 0.0 each new FY.\n"
        "Formula: Opening_SL = 0.0 (if Is_Accumulated = No) or min(Max_Cap, Prior Balance_SL) (if Is_Accumulated = Yes)"
    ),
    "Addition_SL": (
        "Note: Annual Sick Leave quota added on April 1 for the active Financial Year (default 30.0 days/FY; 0.0 if on full-year UL).\n"
        "Formula: Addition_SL = Annual_SL_Quota (30.0d), or 0.0 if on Full-Year UL"
    ),
    "Penalty_SL(UL Effect)": (
        "Note: Pro-rata Sick Leave deduction caused by taking Unpaid Leave (UL) during the year.\n"
        "Formula: Penalty_SL = ROUND((Used_UL / 30) * Pro_Rata_SL_Penalty [2.50d] * 2) / 2"
    ),
    "Used_SL": (
        "Note: Total approved Sick Leave days taken (net of early-return reversals) in the active Financial Year.\n"
        "Formula: Used_SL = Sum of Approved SL days in active FY"
    ),
    "Balance_SL": (
        "Note: Remaining Sick Leave balance for the active Financial Year. Editing Balance_SL directly adjusts Opening_SL automatically.\n"
        "Formula: Balance_SL = max(0.0, Opening_SL + Addition_SL - Penalty_SL(UL Effect) - Used_SL)"
    ),
    "Opening_AL": (
        "Note: Carried-over Annual / Earned Leave (AL) balance from the previous Financial Year on April 1. Under CTA rules, AL accumulates up to the 180.0-day maximum cap.\n"
        "Formula: Opening_AL = min(Max_Accumulation_Cap [180.0d], Prior FY Closing Balance_AL)"
    ),
    "Addition_AL": (
        "Note: Annual / Earned Leave quota added on April 1 for the active Financial Year (default 30.0 days/FY; 0.0 if on full-year UL).\n"
        "Formula: Addition_AL = Annual_AL_Quota (30.0d), or 0.0 if on Full-Year UL"
    ),
    "Penalty_AL(UL Effect)": (
        "Note: Pro-rata Annual Leave deduction caused by taking Unpaid Leave (UL) during the year.\n"
        "Formula: Penalty_AL = ROUND((Used_UL / 30) * Pro_Rata_AL_Penalty [2.50d] * 2) / 2"
    ),
    "Used_AL": (
        "Note: Total approved Annual Leave days taken (net of early-return reversals) in the active Financial Year.\n"
        "Formula: Used_AL = Sum of Approved AL days in active FY"
    ),
    "Encashed_AL": (
        "Note: Total Annual Leave days encashed (paid out) during the active Financial Year. Resets to 0.0 at FY rollover after reducing carried-over Balance_AL.\n"
        "Formula: Encashed_AL = Sum of encashed AL days in active FY"
    ),
    "Balance_AL": (
        "Note: Current available Annual Leave balance, capped at the maximum accumulation limit (default 180.0 days). Editing Balance_AL directly adjusts Opening_AL automatically.\n"
        "Formula: Balance_AL = min(Max_Cap [180.0d], max(0.0, Opening_AL + Addition_AL - Penalty_AL(UL Effect) - Used_AL - Encashed_AL))"
    ),
    "Opening_UL": (
        "Note: Carried-over Unpaid Leave (UL) entitlement at the start of the Financial Year.\n"
        "Formula: Opening_UL = Prior FY Closing Balance_UL (if Is_Accumulated = Yes)"
    ),
    "Addition_UL": (
        "Note: Unpaid Leave milestone grant credited when Actual Service Year reaches a 5, 10, 15, or 20-year milestone (default 182.5 days per milestone, subject to the 730.0-day career cap).\n"
        "Formula: Addition_UL = min(182.5, max(0.0, 730.0 - (Opening_UL + Cumulative_UL))) when Actual_Service_Year >= Milestone > Last_UL_Milestone"
    ),
    "Used_UL": (
        "Note: Approved Unpaid Leave days taken in the active Financial Year. At March 31 midnight rollover, Used_UL is promoted into Cumulative_UL and reset to 0.0.\n"
        "Formula: Used_UL = Active-FY portion of Approved UL days"
    ),
    "Balance_UL": (
        "Note: Remaining available Unpaid Leave entitlement in the active Financial Year. Editing Balance_UL directly adjusts Opening_UL automatically.\n"
        "Formula: Balance_UL = max(0.0, Opening_UL + Addition_UL - Used_UL)"
    ),
    "Cumulative_UL": (
        "Note: Historical total of all Unpaid Leave days taken across prior Financial Years. Delays Service Year tenure and counts toward the 730.0-day career UL cap.\n"
        "Formula: New Cumulative_UL (at FY Rollover) = Prior Cumulative_UL + Ending-FY Used_UL"
    ),
    "Last_UL_Milestone": (
        "Note: Highest service tenure milestone (0, 5, 10, 15, or 20 years) for which the 182.5-day UL grant has already been credited. Prevents duplicate milestone grants.\n"
        "Formula: Updated to Milestone (5, 10, 15, 20) when Actual_Service_Year >= Milestone"
    ),
    "Current Year Leave affecting Service Year": (
        "Note: Approved non-UL leave days in the active Financial Year marked as delaying service tenure (e.g. Education Leave with EffectsServiceYear = Yes).\n"
        "Formula: Promoted into Cumulative Service-Affecting Leave at FY rollover and reset to 0.0"
    ),
    "Cummulative leave effecting Service year.(Education Leave & Etc).": (
        "Note: Historical total of non-UL service-affecting leave days (e.g. Education Leave & Etc.) from prior Financial Years. Delays Actual Service Year.\n"
        "Formula: New Cumulative Service-Affecting Leave (at FY Rollover) = Prior Cumulative + Ending-FY Current Year Service-Affecting Leave"
    ),
}

LEAVE_RULE_FIELD_HELP = {
    "Annual_Quota": (
        "Note: Annual number of leave days automatically credited on April 1 for each new Financial Year.\n"
        "Formula: Addition_X = Annual_Quota (or 0.0 if on Full-Year UL and Earned_During_Full_Year_UL is unchecked)"
    ),
    "Is_Accumulated": (
        "Note: Controls whether unused leave balance at March 31 carries forward into Opening_X of the new Financial Year (e.g. AL & UL carry forward; CL & SL reset to 0.0).\n"
        "Formula: Opening_X (New FY) = min(Max_Accumulation_Cap, Closing_Balance_X) if Checked, else 0.0"
    ),
    "Max_Accumulation_Cap": (
        "Note: Maximum allowable balance / carry-forward cap in days (set 0.0 for No Cap; default 180.0d for AL, 730.0d career cap for UL).\n"
        "Formula: Balance_X = min(Max_Accumulation_Cap, Raw_Balance_X) when Max_Accumulation_Cap > 0"
    ),
    "Affects_Service_Year": (
        "Note: Controls whether taking this leave delays the employee's Actual Service Year and 5-year UL milestones (default Yes for UL, No for CL/SL/AL).\n"
        "Formula: Service Year Penalty += Elapsed Leave Days when Affects_Service_Year is Checked"
    ),
    "Earned_During_Full_Year_UL": (
        "Note: Controls whether the annual quota is still credited if the staff member is on Unpaid Leave for the entire Financial Year (default Unchecked = 0.0 addition on full-year UL).\n"
        "Formula: Addition_X = Annual_Quota if Checked else 0.0 during Full-Year UL"
    ),
    "Allow_Encashment": (
        "Note: Controls whether unused balance of this leave type is eligible for financial encashment (default Checked for AL only).\n"
        "Formula: Balance_X = max(0, Opening_X + Addition_X - Penalty_X - Used_X - Encashed_X)"
    ),
    "Pro_Rata_UL_Penalty": (
        "Note: Number of days of this leave deducted for every 30 days of Unpaid Leave (UL) taken (default: CL = 0.75d, SL = 2.50d, AL = 2.50d).\n"
        "Formula: Penalty_X = ROUND((Used_UL / 30) * Pro_Rata_UL_Penalty * 2) / 2"
    ),
    "Strict_Balance_Required": (
        "Note: When checked, blocks leave applications if requested days exceed effective available balance (default Checked for CL, AL, UL; Unchecked for SL & Other).\n"
        "Formula: Effective_Balance_X = Balance_X - Net_Pending_X; requires Requested_Days <= Effective_Balance_X"
    ),
    "Max_Consecutive_Days": (
        "Note: Maximum consecutive days allowed in a single or back-to-back split request (set 0.0 for No Limit; default 5.0 days for CL).\n"
        "Formula: Hard block if Consecutive_Days > Max_Consecutive_Days (when > 0)"
    ),
    "Non_Combinable": (
        "Note: When checked, this leave cannot be chained immediately before/after another Non-Combinable leave without at least one working half-day in between (default Checked for CL, AL, UL).\n"
        "Formula: Blocks adjacent slots between two different Non_Combinable leave types"
    ),
    "UL_Milestone_Interval_Years": (
        "Note: Interval of Actual Service Years at which Unpaid Leave milestone grants are awarded (default every 5.0 years: 5, 10, 15, 20).\n"
        "Formula: Milestones = [Interval, 2 * Interval, ..., UL_Max_Milestone_Years]"
    ),
    "UL_Max_Milestone_Years": (
        "Note: Maximum Actual Service Year milestone eligible for UL grants (default 20.0 years).\n"
        "Formula: Milestone grants stop after Actual_Service_Year exceeds UL_Max_Milestone_Years"
    ),
    "UL_Grant_Per_Milestone": (
        "Note: Unpaid Leave days granted upon reaching each service milestone (default 182.5 days = 6 months per 5-year milestone).\n"
        "Formula: Addition_UL = min(UL_Grant_Per_Milestone, max(0.0, UL_Career_Max_Cap - (Opening_UL + Cumulative_UL)))"
    ),
    "UL_Career_Max_Cap": (
        "Note: Lifetime maximum Unpaid Leave entitlement across a staff member's entire career (default 730.0 days = 2 years).\n"
        "Formula: Remaining_Career_UL = max(0.0, UL_Career_Max_Cap - (Opening_UL + Cumulative_UL))"
    ),
    "Service_Freeze_Rule": (
        "Note: When active, only elapsed leave days up to today are deducted from calendar service days, freezing tenure smoothly during active leave so service years never regress backwards.\n"
        "Formula: Actual_Service_Year = max(0, (Today - JoiningDate) - Elapsed_Service_Penalty) / 365.25"
    ),
    "Auto_Apply_Pro_Rata_UL_Penalty": (
        "Note: When enabled, automatically calculates Penalty_CL(UL Effect), Penalty_SL(UL Effect), and Penalty_AL(UL Effect) from Used_UL whenever balances are synced.\n"
        "Formula: Penalty_X = ROUND((Used_UL / 30) * Pro_Rata_UL_Penalty_X * 2) / 2"
    ),
}


def get_default_leave_rules():
    """Returns a fresh copy of the official CTA Leave Rules & Policy Configuration dictionary."""
    return {
        "leave_types": {
            "CL": {
                "LeaveType": "CL",
                "Label": "Casual Leave (CL - ངེས་མེད་གུང་གསེང་།)",
                "Annual_Quota": 9.0,
                "Is_Accumulated": False,
                "Max_Accumulation_Cap": 0.0,
                "Affects_Service_Year": False,
                "Earned_During_Full_Year_UL": False,
                "Allow_Encashment": False,
                "Pro_Rata_UL_Penalty": 0.75,
                "Strict_Balance_Required": True,
                "Max_Consecutive_Days": 5.0,
                "Non_Combinable": True,
            },
            "SL": {
                "LeaveType": "SL",
                "Label": "Sick Leave (SL - ནད་དགོངས།)",
                "Annual_Quota": 30.0,
                "Is_Accumulated": False,
                "Max_Accumulation_Cap": 0.0,
                "Affects_Service_Year": False,
                "Earned_During_Full_Year_UL": False,
                "Allow_Encashment": False,
                "Pro_Rata_UL_Penalty": 2.50,
                "Strict_Balance_Required": False,
                "Max_Consecutive_Days": 0.0,
                "Non_Combinable": False,
            },
            "AL": {
                "LeaveType": "AL",
                "Label": "Annual / Earned Leave (AL - ཐོབ་སེང་།)",
                "Annual_Quota": 30.0,
                "Is_Accumulated": True,
                "Max_Accumulation_Cap": 180.0,
                "Affects_Service_Year": False,
                "Earned_During_Full_Year_UL": False,
                "Allow_Encashment": True,
                "Pro_Rata_UL_Penalty": 2.50,
                "Strict_Balance_Required": True,
                "Max_Consecutive_Days": 0.0,
                "Non_Combinable": True,
            },
            "UL": {
                "LeaveType": "UL",
                "Label": "Unpaid Leave (UL - དམིགས་བསལ་ཕོགས་མེད་གུང་གསེང་།)",
                "Annual_Quota": 0.0,
                "Is_Accumulated": True,
                "Max_Accumulation_Cap": 730.0,
                "Affects_Service_Year": True,
                "Earned_During_Full_Year_UL": False,
                "Allow_Encashment": False,
                "Pro_Rata_UL_Penalty": 0.0,
                "Strict_Balance_Required": True,
                "Max_Consecutive_Days": 0.0,
                "Non_Combinable": True,
            },
            "Other": {
                "LeaveType": "Other",
                "Label": "Other / Education / Special Leave (གཞན།)",
                "Annual_Quota": 0.0,
                "Is_Accumulated": False,
                "Max_Accumulation_Cap": 0.0,
                "Affects_Service_Year": False,
                "Earned_During_Full_Year_UL": False,
                "Allow_Encashment": False,
                "Pro_Rata_UL_Penalty": 0.0,
                "Strict_Balance_Required": False,
                "Max_Consecutive_Days": 0.0,
                "Non_Combinable": False,
            },
        },
        "global": {
            "UL_Milestone_Interval_Years": 5.0,
            "UL_Max_Milestone_Years": 20.0,
            "UL_Grant_Per_Milestone": 182.5,
            "UL_Career_Max_Cap": 730.0,
            "Service_Freeze_Rule": True,
            "Auto_Apply_Pro_Rata_UL_Penalty": False,
        },
    }


def _parse_rule_bool(val, default=False):
    if val is None:
        return bool(default)
    try:
        if pd.isnull(val):
            return bool(default)
    except Exception:
        pass
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val != 0)
    s = str(val).strip().lower()
    if s in ("true", "yes", "y", "1", "1.0", "active", "on"):
        return True
    if s in ("false", "no", "n", "0", "0.0", "inactive", "off", "nan", "none", ""):
        return False
    return bool(default)


def _parse_rule_float(val, default=0.0, min_val=0.0):
    if val is None:
        return float(default)
    try:
        if pd.isnull(val):
            return float(default)
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat") or s.startswith("="):
        return float(default)
    try:
        parsed = float(s)
        return max(float(min_val), parsed)
    except Exception:
        return float(default)


def normalize_leave_rules(rules=None):
    """Normalizes any partial dict or DataFrame into a complete, validated leave rules dict."""
    base = get_default_leave_rules()
    if rules is None:
        return base
    if isinstance(rules, pd.DataFrame):
        return leave_rules_from_df(rules)
    if not isinstance(rules, dict):
        return base

    src_lt = rules.get("leave_types", {})
    if isinstance(src_lt, dict):
        for lt_key, def_cfg in base["leave_types"].items():
            custom_cfg = src_lt.get(lt_key)
            if isinstance(custom_cfg, dict):
                if "Label" in custom_cfg and str(custom_cfg["Label"]).strip():
                    def_cfg["Label"] = str(custom_cfg["Label"]).strip()
                for f_key in ("Annual_Quota", "Max_Accumulation_Cap", "Pro_Rata_UL_Penalty", "Max_Consecutive_Days"):
                    if f_key in custom_cfg:
                        def_cfg[f_key] = _parse_rule_float(custom_cfg[f_key], def_cfg[f_key], min_val=0.0)
                for b_key in (
                    "Is_Accumulated",
                    "Affects_Service_Year",
                    "Earned_During_Full_Year_UL",
                    "Allow_Encashment",
                    "Strict_Balance_Required",
                    "Non_Combinable",
                ):
                    if b_key in custom_cfg:
                        def_cfg[b_key] = _parse_rule_bool(custom_cfg[b_key], def_cfg[b_key])

    src_gl = rules.get("global", {})
    if isinstance(src_gl, dict):
        for f_key in ("UL_Milestone_Interval_Years", "UL_Max_Milestone_Years", "UL_Grant_Per_Milestone", "UL_Career_Max_Cap"):
            if f_key in src_gl:
                min_b = 0.5 if f_key == "UL_Milestone_Interval_Years" else 0.0
                base["global"][f_key] = _parse_rule_float(src_gl[f_key], base["global"][f_key], min_val=min_b)
        for b_key in ("Service_Freeze_Rule", "Auto_Apply_Pro_Rata_UL_Penalty"):
            if b_key in src_gl:
                base["global"][b_key] = _parse_rule_bool(src_gl[b_key], base["global"][b_key])

    return base


def leave_rules_to_df(rules=None):
    """Converts a leave rules dictionary into a 5-row DataFrame matching LEAVE_RULES_COLUMNS."""
    norm = normalize_leave_rules(rules)
    gl = norm["global"]
    rows = []
    for lt_key in ("CL", "SL", "AL", "UL", "Other"):
        lt_cfg = norm["leave_types"][lt_key]
        rows.append({
            "LeaveType": lt_key,
            "Label": lt_cfg["Label"],
            "Annual_Quota": float(lt_cfg["Annual_Quota"]),
            "Is_Accumulated": bool(lt_cfg["Is_Accumulated"]),
            "Max_Accumulation_Cap": float(lt_cfg["Max_Accumulation_Cap"]),
            "Affects_Service_Year": bool(lt_cfg["Affects_Service_Year"]),
            "Earned_During_Full_Year_UL": bool(lt_cfg["Earned_During_Full_Year_UL"]),
            "Allow_Encashment": bool(lt_cfg["Allow_Encashment"]),
            "Pro_Rata_UL_Penalty": float(lt_cfg["Pro_Rata_UL_Penalty"]),
            "Strict_Balance_Required": bool(lt_cfg["Strict_Balance_Required"]),
            "Max_Consecutive_Days": float(lt_cfg["Max_Consecutive_Days"]),
            "Non_Combinable": bool(lt_cfg["Non_Combinable"]),
            "UL_Milestone_Interval_Years": float(gl["UL_Milestone_Interval_Years"]),
            "UL_Max_Milestone_Years": float(gl["UL_Max_Milestone_Years"]),
            "UL_Grant_Per_Milestone": float(gl["UL_Grant_Per_Milestone"]),
            "UL_Career_Max_Cap": float(gl["UL_Career_Max_Cap"]),
            "Service_Freeze_Rule": bool(gl["Service_Freeze_Rule"]),
            "Auto_Apply_Pro_Rata_UL_Penalty": bool(gl["Auto_Apply_Pro_Rata_UL_Penalty"]),
        })
    return pd.DataFrame(rows, columns=LEAVE_RULES_COLUMNS)


def leave_rules_from_df(df):
    """Parses a LeaveRules DataFrame into a normalized leave rules dictionary."""
    base = get_default_leave_rules()
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or "LeaveType" not in df.columns:
        return base

    gl_updated = False
    for _, row in df.iterrows():
        lt_key = str(row.get("LeaveType", "")).strip()
        if lt_key in base["leave_types"]:
            def_cfg = base["leave_types"][lt_key]
            raw_lbl = row.get("Label")
            if pd.notnull(raw_lbl) and str(raw_lbl).strip() and str(raw_lbl).strip().lower() != "nan":
                def_cfg["Label"] = str(raw_lbl).strip()
            for f_key in ("Annual_Quota", "Max_Accumulation_Cap", "Pro_Rata_UL_Penalty", "Max_Consecutive_Days"):
                if f_key in df.columns:
                    def_cfg[f_key] = _parse_rule_float(row.get(f_key), def_cfg[f_key], min_val=0.0)
            for b_key in (
                "Is_Accumulated",
                "Affects_Service_Year",
                "Earned_During_Full_Year_UL",
                "Allow_Encashment",
                "Strict_Balance_Required",
                "Non_Combinable",
            ):
                if b_key in df.columns:
                    def_cfg[b_key] = _parse_rule_bool(row.get(b_key), def_cfg[b_key])

        if not gl_updated or lt_key == "UL":
            for f_key in ("UL_Milestone_Interval_Years", "UL_Max_Milestone_Years", "UL_Grant_Per_Milestone", "UL_Career_Max_Cap"):
                if f_key in df.columns and pd.notnull(row.get(f_key)) and str(row.get(f_key)).strip() != "":
                    min_b = 0.5 if f_key == "UL_Milestone_Interval_Years" else 0.0
                    base["global"][f_key] = _parse_rule_float(row.get(f_key), base["global"][f_key], min_val=min_b)
            for b_key in ("Service_Freeze_Rule", "Auto_Apply_Pro_Rata_UL_Penalty"):
                if b_key in df.columns and pd.notnull(row.get(b_key)) and str(row.get(b_key)).strip() != "":
                    base["global"][b_key] = _parse_rule_bool(row.get(b_key), base["global"][b_key])
            gl_updated = True

    return base


def get_effective_leave_rules(rules_override=None):
    """Resolves active leave rules from rules_override, st.session_state['_custom_leave_rules'], or CTA defaults."""
    if rules_override is not None:
        return normalize_leave_rules(rules_override)
    try:
        st_obj = globals().get("st")
        if st_obj is not None and hasattr(st_obj, "session_state"):
            sess = st_obj.session_state
            if "_custom_leave_rules" in sess and sess["_custom_leave_rules"] is not None:
                return normalize_leave_rules(sess["_custom_leave_rules"])
    except Exception:
        pass
    return get_default_leave_rules()


def get_ul_milestone_years_desc(rules_override=None):
    """Returns descending tuple of UL milestone years (default (20.0, 15.0, 10.0, 5.0))."""
    eff = get_effective_leave_rules(rules_override)
    gl = eff.get("global", {})
    interval = _parse_rule_float(gl.get("UL_Milestone_Interval_Years", 5.0), 5.0, min_val=0.5)
    max_ms = _parse_rule_float(gl.get("UL_Max_Milestone_Years", 20.0), 20.0, min_val=0.0)
    if interval <= 0:
        interval = 5.0
    if max_ms < interval:
        return ()
    milestones = []
    cur = interval
    steps = 0
    while cur <= max_ms + 1e-6 and steps < 100:
        milestones.append(round(cur, 2))
        cur += interval
        steps += 1
    return tuple(reversed(milestones))


def calculate_pro_rata_ul_penalties(ul_days, rules_override=None):
    """Calculates pro-rata UL penalty deductions (rounded to nearest 0.5 day) for each leave type."""
    eff = get_effective_leave_rules(rules_override)
    lt_map = eff.get("leave_types", {})
    u_days = max(0.0, _parse_rule_float(ul_days, 0.0, min_val=0.0))
    penalties = {}
    for lt_key in ("CL", "SL", "AL", "UL", "Other"):
        rate = _parse_rule_float(lt_map.get(lt_key, {}).get("Pro_Rata_UL_Penalty", 0.0), 0.0, min_val=0.0)
        if u_days <= 0.0 or rate <= 0.0:
            penalties[lt_key] = 0.0
        else:
            penalties[lt_key] = round((u_days / 30.0) * rate * 2.0) / 2.0
    return penalties


@st.cache_data(ttl=60)
def _read_leave_rules_sheet():
    return _gsheets_read(conn, "LeaveRules")


def load_leave_rules(conn_override=None, force_refresh=False):
    """Loads LeaveRules from Google Sheets (with safe fallback to session_state / CTA defaults)."""
    active_conn = conn_override if conn_override is not None else globals().get("conn")
    if active_conn is not None:
        try:
            cached_reader = globals().get("_read_leave_rules_sheet")
            if (
                not force_refresh
                and conn_override is None
                and callable(cached_reader)
                and active_conn is globals().get("conn")
            ):
                df = cached_reader()
            else:
                _gs_read = globals().get("_gsheets_read")
                df = _gs_read(active_conn, "LeaveRules") if callable(_gs_read) else active_conn.read(worksheet="LeaveRules")
            if isinstance(df, pd.DataFrame) and not df.empty and "LeaveType" in df.columns:
                parsed = leave_rules_from_df(df)
                try:
                    st.session_state["_custom_leave_rules"] = parsed
                except Exception:
                    pass
                return parsed
        except Exception:
            pass
    return get_effective_leave_rules()


def save_leave_rules(rules_dict, conn_override=None):
    """Persists LeaveRules to Google Sheets (creating worksheet if needed) and updates session_state."""
    norm = normalize_leave_rules(rules_dict)
    df_out = leave_rules_to_df(norm)
    try:
        st.session_state["_custom_leave_rules"] = norm
    except Exception:
        pass

    active_conn = conn_override if conn_override is not None else globals().get("conn")
    if active_conn is None:
        return False, df_out

    _retry_fn = globals().get("_gsheets_retry")
    try:
        if hasattr(active_conn, "client") and hasattr(active_conn.client, "_open_spreadsheet"):
            spread = _retry_fn(active_conn.client._open_spreadsheet, spreadsheet=None, folder_id=None) if callable(_retry_fn) else active_conn.client._open_spreadsheet(spreadsheet=None, folder_id=None)
            if spread is not None:
                try:
                    spread.worksheet("LeaveRules")
                except Exception:
                    spread.add_worksheet(title="LeaveRules", rows="50", cols="25")
    except Exception:
        pass

    try:
        if callable(_retry_fn):
            _retry_fn(active_conn.update, worksheet="LeaveRules", data=df_out)
        else:
            active_conn.update(worksheet="LeaveRules", data=df_out)
        try:
            _inv = globals().get("_invalidate_sheet_cache")
            if callable(_inv):
                _inv("LeaveRules")
            else:
                _fn = globals().get("_read_leave_rules_sheet")
                if callable(getattr(_fn, "clear", None)):
                    _fn.clear()
        except Exception:
            pass
        return True, df_out
    except Exception:
        return False, df_out


def inject_balance_formulas(df, df_req_override=None, reference_date=None, rules_override=None):
    """
    Computes and syncs pure numerical values into Staff_Master without writing brittle formula strings (=AD2+AC2).
    Guarantees that Google Sheet cells contain clean, editable numbers, making them completely immune
    to column additions or accidental formula corruption.
    """
    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if _resolve_rules_fn is not None else (rules_override if isinstance(rules_override, dict) else {})
    _lt_rules = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}
    _gl_rules = _rules.get("global", {}) if isinstance(_rules, dict) else {}

    cl_cap = float(_lt_rules.get("CL", {}).get("Max_Accumulation_Cap", 0.0) or 0.0)
    sl_cap = float(_lt_rules.get("SL", {}).get("Max_Accumulation_Cap", 0.0) or 0.0)
    al_cap = float(_lt_rules.get("AL", {}).get("Max_Accumulation_Cap", 180.0) if "AL" in _lt_rules else 180.0)
    auto_pro_rata = bool(_gl_rules.get("Auto_Apply_Pro_Rata_UL_Penalty", False))
    cl_pr_rate = float(_lt_rules.get("CL", {}).get("Pro_Rata_UL_Penalty", 0.75) if "CL" in _lt_rules else 0.75)
    sl_pr_rate = float(_lt_rules.get("SL", {}).get("Pro_Rata_UL_Penalty", 2.50) if "SL" in _lt_rules else 2.50)
    al_pr_rate = float(_lt_rules.get("AL", {}).get("Pro_Rata_UL_Penalty", 2.50) if "AL" in _lt_rules else 2.50)

    df_copy = df.copy()
    for req_col in (
        "Post", "JoiningDate", "Branch", "Service Year", "Service Year Penalty", "Actual Service Year",
        "Opening_CL", "Addition_CL", "Penalty_CL(UL Effect)", "Used_CL", "Balance_CL",
        "Opening_SL", "Addition_SL", "Penalty_SL(UL Effect)", "Used_SL", "Balance_SL",
        "Opening_AL", "Addition_AL", "Penalty_AL(UL Effect)", "Used_AL", "Encashed_AL", "Balance_AL",
        "Opening_UL", "Addition_UL", "Used_UL", "Balance_UL",
        "Cumulative_UL", "Last_UL_Milestone",
        "Current Year Leave affecting Service Year",
        "Cummulative leave effecting Service year.(Education Leave & Etc).",
    ):
        if req_col not in df_copy.columns:
            df_copy[req_col] = "" if req_col in ("Post", "JoiningDate", "Branch") else 0.0
    for c in df_copy.columns:
        df_copy[c] = df_copy[c].astype(object)

    def sf(val):
        if pd.isnull(val):
            return 0.0
        s = str(val).strip()
        if s == "" or s.startswith("="):
            return 0.0
        try:
            return float(s)
        except Exception:
            return 0.0

    users_df = load_users_data()
    u_map = {}
    if users_df is not None and not users_df.empty:
        for _, u_row in users_df.iterrows():
            u_name = str(u_row.get("Name", "")).strip()
            u_map[u_name] = {
                "Post": str(u_row.get("Post", "")).strip(),
                "JoiningDate": str(u_row.get("JoiningDate", "")).strip(),
                "Branch": str(u_row.get("Branch", "HQ")).strip()
            }

    df_req_eff = df_req_override if df_req_override is not None else load_leave_requests()
    req_by_name = {}
    empty_req_df = pd.DataFrame(columns=df_req_eff.columns) if isinstance(df_req_eff, pd.DataFrame) else pd.DataFrame()
    if isinstance(df_req_eff, pd.DataFrame) and not df_req_eff.empty and "Name" in df_req_eff.columns:
        sub_req = df_req_eff
        if "Status" in sub_req.columns:
            sub_req = sub_req[sub_req["Status"].astype(str).str.strip() == "Approved"]
        if not sub_req.empty and "LeaveType" in sub_req.columns:
            extra_svc = {
                k for k, v in _lt_rules.items()
                if k != "UL" and isinstance(v, dict) and bool(v.get("Affects_Service_Year", False))
            }
            svc_names = {"UL", "Return-UL", "Rev-UL"} | set(extra_svc) | {f"Return-{k}" for k in extra_svc} | {f"Rev-{k}" for k in extra_svc}
            lt_mask = sub_req["LeaveType"].astype(str).str.strip().isin(svc_names)
            if "EffectsServiceYear" in sub_req.columns:
                eff_mask = sub_req["EffectsServiceYear"].astype(str).str.strip().str.lower() == "yes"
                sub_req = sub_req[lt_mask | eff_mask]
            else:
                sub_req = sub_req[lt_mask]
        if not sub_req.empty:
            for emp_k, grp in sub_req.groupby(sub_req["Name"].astype(str).str.strip(), sort=False):
                req_by_name[emp_k] = grp

    for i in range(len(df_copy)):
        name = str(df_copy.at[df_copy.index[i], "Name"]).strip()
        if name in u_map:
            cur_post = str(df_copy.at[df_copy.index[i], "Post"]).strip()
            if cur_post == "" or cur_post.startswith("=") or cur_post == "nan":
                df_copy.at[df_copy.index[i], "Post"] = u_map[name]["Post"]

            cur_join = str(df_copy.at[df_copy.index[i], "JoiningDate"]).strip()
            if cur_join == "" or cur_join.startswith("=") or cur_join == "nan":
                df_copy.at[df_copy.index[i], "JoiningDate"] = u_map[name]["JoiningDate"]

            if "Branch" in df_copy.columns:
                cur_br = str(df_copy.at[df_copy.index[i], "Branch"]).strip()
                if cur_br == "" or cur_br == "nan":
                    df_copy.at[df_copy.index[i], "Branch"] = u_map[name]["Branch"]

        # Calculate live non-regressive balances & tenure using the row being saved (never stale cache)
        emp_req_df = req_by_name.get(name, empty_req_df) if req_by_name or isinstance(df_req_eff, pd.DataFrame) else df_req_eff
        try:
            bal = calculate_balances(
                name,
                user_row_override=df_copy.iloc[i],
                df_req_override=emp_req_df,
                reference_date=reference_date,
                rules_override=rules_override,
            )
        except TypeError:
            bal = calculate_balances(
                name,
                user_row_override=df_copy.iloc[i],
                df_req_override=emp_req_df,
                reference_date=reference_date,
            )

        df_copy.at[df_copy.index[i], "Service Year"] = bal.get("Calendar_Service_Year", 0.0)
        df_copy.at[df_copy.index[i], "Service Year Penalty"] = bal.get("Service_Year_Penalty", 0.0)
        df_copy.at[df_copy.index[i], "Actual Service Year"] = bal.get("Actual_Service_Year", 0.0)

        o_ul = sf(df_copy.at[df_copy.index[i], "Opening_UL"])
        a_ul = sf(df_copy.at[df_copy.index[i], "Addition_UL"])
        u_ul = sf(df_copy.at[df_copy.index[i], "Used_UL"])
        cur_eff_ul = sf(df_copy.at[df_copy.index[i], "Current Year Leave affecting Service Year"]) if "Current Year Leave affecting Service Year" in df_copy.columns else 0.0
        tot_pr_days = u_ul + cur_eff_ul

        if auto_pro_rata:
            if tot_pr_days > 0.0:
                df_copy.at[df_copy.index[i], "Penalty_CL(UL Effect)"] = round((tot_pr_days / 30.0) * max(0.0, cl_pr_rate) * 2.0) / 2.0
                df_copy.at[df_copy.index[i], "Penalty_SL(UL Effect)"] = round((tot_pr_days / 30.0) * max(0.0, sl_pr_rate) * 2.0) / 2.0
                df_copy.at[df_copy.index[i], "Penalty_AL(UL Effect)"] = round((tot_pr_days / 30.0) * max(0.0, al_pr_rate) * 2.0) / 2.0
            else:
                df_copy.at[df_copy.index[i], "Penalty_CL(UL Effect)"] = 0.0
                df_copy.at[df_copy.index[i], "Penalty_SL(UL Effect)"] = 0.0
                df_copy.at[df_copy.index[i], "Penalty_AL(UL Effect)"] = 0.0

        # Clean numeric Balances (pure numbers, zero formula strings)
        o_cl = sf(df_copy.at[df_copy.index[i], "Opening_CL"])
        a_cl = sf(df_copy.at[df_copy.index[i], "Addition_CL"])
        p_cl = sf(df_copy.at[df_copy.index[i], "Penalty_CL(UL Effect)"])
        u_cl = sf(df_copy.at[df_copy.index[i], "Used_CL"])
        raw_b_cl = max(0.0, o_cl + a_cl - p_cl - u_cl)
        df_copy.at[df_copy.index[i], "Balance_CL"] = min(cl_cap, raw_b_cl) if cl_cap > 0.0 else raw_b_cl

        o_sl = sf(df_copy.at[df_copy.index[i], "Opening_SL"])
        a_sl = sf(df_copy.at[df_copy.index[i], "Addition_SL"])
        p_sl = sf(df_copy.at[df_copy.index[i], "Penalty_SL(UL Effect)"])
        u_sl = sf(df_copy.at[df_copy.index[i], "Used_SL"])
        raw_b_sl = max(0.0, o_sl + a_sl - p_sl - u_sl)
        df_copy.at[df_copy.index[i], "Balance_SL"] = min(sl_cap, raw_b_sl) if sl_cap > 0.0 else raw_b_sl

        o_al = sf(df_copy.at[df_copy.index[i], "Opening_AL"])
        a_al = sf(df_copy.at[df_copy.index[i], "Addition_AL"])
        p_al = sf(df_copy.at[df_copy.index[i], "Penalty_AL(UL Effect)"])
        u_al = sf(df_copy.at[df_copy.index[i], "Used_AL"])
        e_al = sf(df_copy.at[df_copy.index[i], "Encashed_AL"])
        raw_b_al = max(0.0, o_al + a_al - p_al - u_al - e_al)
        df_copy.at[df_copy.index[i], "Balance_AL"] = min(al_cap, raw_b_al) if al_cap > 0.0 else raw_b_al

        df_copy.at[df_copy.index[i], "Balance_UL"] = max(0.0, o_ul + a_ul - u_ul)

    return df_copy


def get_cta_now(now_utc=None):
    """Returns current timestamp converted to IST (Asia/Kolkata), CTA HQ's official timezone."""
    if now_utc is not None:
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        return now_utc.astimezone(CTA_TZ)
    return datetime.now(CTA_TZ)


def get_cta_today(now_utc=None):
    """Returns today's calendar date in IST (flips exactly at midnight March 31 -> April 1 IST)."""
    return get_cta_now(now_utc=now_utc).date()


def parse_fy_start_year(fy_value):
    """Parses labels like 'FY 2026-2027', 'FY2026-27', or '2026-2027' -> 2026, or None if missing."""
    if fy_value is None:
        return None
    try:
        if pd.isnull(fy_value):
            return None
    except Exception:
        pass
    s = str(fy_value).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    m = re.search(r"((?:19|20)\d{2})", s)
    return int(m.group(1)) if m else None


def format_fy_short(fy_label):
    """Formats 'FY 2026-2027' into concise CTA display label 'FY 2026-27'."""
    y = parse_fy_start_year(fy_label)
    if y is None:
        return str(fy_label or "").strip()
    return f"FY {y}-{(y + 1) % 100:02d}"


def get_current_financial_year_dates(as_of_date=None):
    today = as_of_date or get_cta_today()
    if today.month >= 4:
        start_date = date(today.year, 4, 1)
        end_date = date(today.year + 1, 3, 31)
        fy_label = f"FY {today.year}-{today.year + 1}"
    else:
        start_date = date(today.year - 1, 4, 1)
        end_date = date(today.year, 3, 31)
        fy_label = f"FY {today.year - 1}-{today.year}"
    return start_date, end_date, fy_label


def get_leave_fy_breakdown(start_date, end_date, total_days=None):
    """
    Partitions a leave range [start_date, end_date] into CTA Financial Years (April 1 to March 31).
    Returns an OrderedDict: {fy_start_year: {"fy_year": int, "fy_label": str, "fy_start": date, "fy_end": date, "seg_start": date, "seg_end": date, "days": float}}
    Handles multi-year spans and scales proportionally if total_days is provided (e.g. half-days or reversals).
    """
    if start_date is None or end_date is None:
        return {}
    try:
        if isinstance(start_date, str):
            start_date = pd.to_datetime(start_date).date()
        elif isinstance(start_date, datetime):
            start_date = start_date.date()
        if isinstance(end_date, str):
            end_date = pd.to_datetime(end_date).date()
        elif isinstance(end_date, datetime):
            end_date = end_date.date()
    except Exception:
        return {}

    if start_date > end_date:
        return {}

    total_cal_days = (end_date - start_date).days + 1
    if total_cal_days <= 0:
        return {}

    start_fy_yr = start_date.year if start_date.month >= 4 else start_date.year - 1
    end_fy_yr = end_date.year if end_date.month >= 4 else end_date.year - 1

    from collections import OrderedDict
    breakdown = OrderedDict()

    raw_segments = []
    for y in range(start_fy_yr, end_fy_yr + 1):
        cur_fy_start = date(y, 4, 1)
        cur_fy_end = date(y + 1, 3, 31)
        seg_start = max(start_date, cur_fy_start)
        seg_end = min(end_date, cur_fy_end)
        seg_cal_days = max(0, (seg_end - seg_start).days + 1)
        is_full_fy = (seg_start == cur_fy_start and seg_end == cur_fy_end)
        raw_segments.append({
            "fy_year": y,
            "fy_label": f"FY {y}-{y + 1}",
            "fy_start": cur_fy_start,
            "fy_end": cur_fy_end,
            "seg_start": seg_start,
            "seg_end": seg_end,
            "cal_days": seg_cal_days,
            "is_full_fy": is_full_fy,
        })

    if total_days is None:
        for seg in raw_segments:
            seg["days"] = round(float(seg["cal_days"]), 2)
    else:
        t_val = float(total_days)
        sign = -1.0 if t_val < 0 else 1.0
        abs_t = abs(t_val)
        if len(raw_segments) == 1:
            raw_segments[0]["days"] = round(t_val, 2)
        elif abs(abs_t - float(total_cal_days)) <= 2.0:
            full_cal_sum = sum(s["cal_days"] for s in raw_segments if s["is_full_fy"])
            if full_cal_sum <= abs_t:
                rem_abs = abs_t - float(full_cal_sum)
                partial_segs = [s for s in raw_segments if not s["is_full_fy"]]
                for s in raw_segments:
                    if s["is_full_fy"]:
                        s["days"] = round(sign * float(s["cal_days"]), 2)
                if len(partial_segs) == 1:
                    partial_segs[0]["days"] = round(sign * rem_abs, 2)
                elif len(partial_segs) >= 2:
                    first_d = min(float(partial_segs[0]["cal_days"]), rem_abs)
                    partial_segs[0]["days"] = round(sign * first_d, 2)
                    partial_segs[-1]["days"] = round(sign * max(0.0, rem_abs - first_d), 2)
            else:
                for seg in raw_segments:
                    seg["days"] = round(t_val * (float(seg["cal_days"]) / float(total_cal_days)), 2)
        else:
            for seg in raw_segments:
                seg["days"] = round(t_val * (float(seg["cal_days"]) / float(total_cal_days)), 2)

    for seg in raw_segments:
        y = seg["fy_year"]
        breakdown[y] = {
            "fy_year": y,
            "fy_label": seg["fy_label"],
            "fy_start": seg["fy_start"],
            "fy_end": seg["fy_end"],
            "seg_start": seg["seg_start"],
            "seg_end": seg["seg_end"],
            "days": seg["days"],
        }

    return breakdown


def _safe_num(val):
    if val is None:
        return 0.0
    try:
        if pd.isnull(val):
            return 0.0
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.startswith("="):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def process_auto_fy_rollover_df(staff_df, as_of_date=None, leave_df=None, rules_override=None):
    """
    Checks Staff_Master rows against the active IST Financial Year (April 1 - March 31).

    Migration guarantee:
    - Rows without an Active_FY value (existing live FY 2026-27 sheets) are stamped with the
      current active FY label WITHOUT altering balances or double-granting quotas.
    - Only when a row's Active_FY start year < target FY start year (e.g. at/after midnight
      March 31 -> April 1 IST) does automatic step-by-step FY rollover run:
        * Promotes Used_UL -> Cumulative_UL and resets Used_UL = 0
        * Promotes Current Year Leave affecting Service Year -> Cumulative Leave Affecting
          Service Year and resets Current Year Leave affecting Service Year = 0
        * Carries forward AL (capped at 180d) & UL; resets Opening CL/SL, Encashed_AL, and Penalties
        * Grants 9 CL, 30 SL, 30 AL for the new FY (or custom quotas configured in LeaveRules)
        * Recomputes Service Year Penalty, Calendar/Actual Service Year & 5/10/15/20-yr UL milestones
          (182.5d per milestone, capped at 730d lifetime UL)
    """
    if staff_df is None or staff_df.empty:
        _, _, target_fy = get_current_financial_year_dates(as_of_date)
        return staff_df, {
            "needs_write": False,
            "seeded": False,
            "rolled_over": False,
            "target_fy": target_fy,
            "rolled_from_fys": [],
            "rolled_count": 0,
        }

    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if _resolve_rules_fn is not None else (rules_override if isinstance(rules_override, dict) else {})
    _lt_rules = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}
    _gl_rules = _rules.get("global", {}) if isinstance(_rules, dict) else {}

    cl_cfg = _lt_rules.get("CL", {}) if isinstance(_lt_rules.get("CL"), dict) else {}
    sl_cfg = _lt_rules.get("SL", {}) if isinstance(_lt_rules.get("SL"), dict) else {}
    al_cfg = _lt_rules.get("AL", {}) if isinstance(_lt_rules.get("AL"), dict) else {}
    ul_cfg = _lt_rules.get("UL", {}) if isinstance(_lt_rules.get("UL"), dict) else {}

    cl_quota = float(cl_cfg.get("Annual_Quota", 9.0) if "Annual_Quota" in cl_cfg else 9.0)
    sl_quota = float(sl_cfg.get("Annual_Quota", 30.0) if "Annual_Quota" in sl_cfg else 30.0)
    al_quota = float(al_cfg.get("Annual_Quota", 30.0) if "Annual_Quota" in al_cfg else 30.0)
    ul_annual_quota = float(ul_cfg.get("Annual_Quota", 0.0) if "Annual_Quota" in ul_cfg else 0.0)

    cl_accum = bool(cl_cfg.get("Is_Accumulated", False))
    sl_accum = bool(sl_cfg.get("Is_Accumulated", False))
    al_accum = bool(al_cfg.get("Is_Accumulated", True) if "Is_Accumulated" in al_cfg else True)
    ul_accum = bool(ul_cfg.get("Is_Accumulated", True) if "Is_Accumulated" in ul_cfg else True)

    cl_cap = float(cl_cfg.get("Max_Accumulation_Cap", 0.0) or 0.0)
    sl_cap = float(sl_cfg.get("Max_Accumulation_Cap", 0.0) or 0.0)
    al_cap = float(al_cfg.get("Max_Accumulation_Cap", 180.0) if "Max_Accumulation_Cap" in al_cfg else 180.0)

    cl_earned_ul = bool(cl_cfg.get("Earned_During_Full_Year_UL", False))
    sl_earned_ul = bool(sl_cfg.get("Earned_During_Full_Year_UL", False))
    al_earned_ul = bool(al_cfg.get("Earned_During_Full_Year_UL", False))
    ul_affects_svc = bool(ul_cfg.get("Affects_Service_Year", True) if "Affects_Service_Year" in ul_cfg else True)

    ms_interval = float(_gl_rules.get("UL_Milestone_Interval_Years", 5.0) if "UL_Milestone_Interval_Years" in _gl_rules else 5.0)
    if ms_interval <= 0:
        ms_interval = 5.0
    ms_max = float(_gl_rules.get("UL_Max_Milestone_Years", 20.0) if "UL_Max_Milestone_Years" in _gl_rules else 20.0)
    ms_grant = float(_gl_rules.get("UL_Grant_Per_Milestone", 182.5) if "UL_Grant_Per_Milestone" in _gl_rules else 182.5)
    ul_career_cap = float(_gl_rules.get("UL_Career_Max_Cap", 730.0) if "UL_Career_Max_Cap" in _gl_rules else 730.0)
    service_freeze = bool(_gl_rules.get("Service_Freeze_Rule", True) if "Service_Freeze_Rule" in _gl_rules else True)

    milestones_desc = []
    if ms_max >= ms_interval:
        _cur_ms = ms_interval
        _steps = 0
        while _cur_ms <= ms_max + 1e-6 and _steps < 100:
            milestones_desc.append(round(_cur_ms, 2))
            _cur_ms += ms_interval
            _steps += 1
        milestones_desc = list(reversed(milestones_desc))

    eval_today = as_of_date or get_cta_today()
    fy_start, _, target_fy = get_current_financial_year_dates(eval_today)
    target_start_year = fy_start.year

    out_df = staff_df.copy()
    cum_eff_col = "Cummulative leave effecting Service year.(Education Leave & Etc)."
    curr_eff_col = "Current Year Leave affecting Service Year"

    numeric_defaults = [
        "Service Year", "Service Year Penalty", "Actual Service Year",
        "Opening_CL", "Addition_CL", "Penalty_CL(UL Effect)", "Used_CL", "Balance_CL",
        "Opening_SL", "Addition_SL", "Penalty_SL(UL Effect)", "Used_SL", "Balance_SL",
        "Opening_AL", "Addition_AL", "Penalty_AL(UL Effect)", "Used_AL", "Encashed_AL", "Balance_AL",
        "Opening_UL", "Addition_UL", "Used_UL", "Balance_UL",
        "Cumulative_UL", "Last_UL_Milestone", curr_eff_col, cum_eff_col,
    ]
    for col in numeric_defaults:
        if col not in out_df.columns:
            out_df[col] = 0.0
        else:
            out_df[col] = out_df[col].astype(object)
    if "Active_FY" not in out_df.columns:
        out_df["Active_FY"] = ""
    out_df["Active_FY"] = out_df["Active_FY"].astype(object)

    needs_write = False
    seeded = False
    rolled_over = False
    rolled_from_fys = set()
    rolled_count = 0

    import math as _math
    _tz = globals().get("CTA_TZ")

    def _roll_parse_date(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            if getattr(val, "tzinfo", None) is not None and _tz is not None:
                return val.astimezone(_tz).date()
            return val.date()
        if hasattr(val, "to_pydatetime"):
            try:
                if pd.isnull(val):
                    return None
                ts = val.to_pydatetime()
                if getattr(ts, "tzinfo", None) is not None and _tz is not None:
                    return ts.astimezone(_tz).date()
                return ts.date() if ts is not None else None
            except Exception:
                return None
        if isinstance(val, date):
            return val
        try:
            if pd.isnull(val):
                return None
        except Exception:
            pass
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "nat", "null"):
            return None
        if ("T" in s or s.endswith(("Z", "z"))) and len(s) > 10:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
                if dt.tzinfo is not None and _tz is not None:
                    return dt.astimezone(_tz).date()
                return dt.date()
            except Exception:
                pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None and _tz is not None:
                return dt.astimezone(_tz).date()
            return dt.date()
        except Exception:
            pass
        if len(s) >= 10:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except ValueError:
                    continue
        return None

    breakdown_func = globals().get("get_leave_fy_breakdown")
    if breakdown_func is None:
        def breakdown_func(s_d, e_d, t_d=None):
            if s_d is None or e_d is None:
                return {}
            try:
                s_d = pd.to_datetime(s_d).date() if not isinstance(s_d, date) else s_d
                e_d = pd.to_datetime(e_d).date() if not isinstance(e_d, date) else e_d
            except Exception:
                return {}
            if s_d > e_d:
                return {}
            tot_cal = (e_d - s_d).days + 1
            if tot_cal <= 0:
                return {}
            s_yr = s_d.year if s_d.month >= 4 else s_d.year - 1
            e_yr = e_d.year if e_d.month >= 4 else e_d.year - 1
            from collections import OrderedDict
            res = OrderedDict()
            for y in range(s_yr, e_yr + 1):
                c_s = max(s_d, date(y, 4, 1))
                c_e = min(e_d, date(y + 1, 3, 31))
                c_cal = max(0, (c_e - c_s).days + 1)
                d = float(t_d) * (float(c_cal) / float(tot_cal)) if t_d is not None else float(c_cal)
                res[y] = {"fy_year": y, "fy_label": f"FY {y}-{y + 1}", "seg_start": c_s, "seg_end": c_e, "days": round(d, 2)}
            return res

    _leave_loaded = False
    effective_leave_df = leave_df
    pen_leaves_by_name = None

    def _get_pen_leaves_index():
        nonlocal _leave_loaded, effective_leave_df, pen_leaves_by_name
        if pen_leaves_by_name is not None:
            return pen_leaves_by_name
        if effective_leave_df is None and not _leave_loaded:
            _leave_loaded = True
            try:
                effective_leave_df = load_leave_requests()
            except Exception:
                effective_leave_df = None
        pen_leaves_by_name = {}
        if effective_leave_df is not None and not effective_leave_df.empty and "Name" in effective_leave_df.columns:
            pen_src = effective_leave_df
            if "Status" in pen_src.columns:
                pen_src = pen_src[pen_src["Status"].astype(str).str.strip() == "Approved"]
            for _, l_row in pen_src.iterrows():
                try:
                    row_emp = str(l_row.get("Name", "")).strip()
                    if not row_emp:
                        continue
                    l_t = str(l_row.get("LeaveType", "")).strip()
                    if l_t.startswith("Return-"):
                        base_lt = l_t[len("Return-"):].strip()
                    elif l_t.startswith("Rev-"):
                        base_lt = l_t[len("Rev-"):].strip()
                    else:
                        base_lt = l_t
                    l_is_ul = (base_lt == "UL")
                    rule_eff = bool(_lt_rules.get(base_lt, {}).get("Affects_Service_Year", False)) if not l_is_ul else False
                    l_is_eff = False if l_is_ul else ((str(l_row.get("EffectsServiceYear", "")).strip().lower() == "yes") or rule_eff)
                    if not (l_is_ul or l_is_eff):
                        continue
                    raw_td = l_row.get("TotalDays", 0.0)
                    if raw_td is None or (isinstance(raw_td, float) and not _math.isfinite(raw_td)):
                        continue
                    try:
                        if pd.isnull(raw_td):
                            continue
                    except Exception:
                        pass
                    s_td = str(raw_td).strip()
                    if s_td == "" or s_td.startswith("="):
                        continue
                    l_d = float(s_td)
                    if not _math.isfinite(l_d):
                        continue
                    l_s = _roll_parse_date(l_row.get("StartDate"))
                    l_e = _roll_parse_date(l_row.get("EndDate"))
                    if l_s is None or l_e is None:
                        continue
                    bdown = breakdown_func(l_s, l_e, l_d)
                    if bdown:
                        pen_leaves_by_name.setdefault(row_emp, []).append((l_is_ul, l_is_eff, bdown))
                except Exception:
                    continue
        return pen_leaves_by_name

    for idx in out_df.index:
        raw_fy = out_df.at[idx, "Active_FY"]
        row_fy_year = parse_fy_start_year(raw_fy)

        # First-time deployment safety: stamp current FY without modifying active balances
        if row_fy_year is None:
            out_df.at[idx, "Active_FY"] = target_fy
            needs_write = True
            seeded = True
            continue

        if row_fy_year == target_start_year:
            if str(raw_fy).strip() != target_fy:
                out_df.at[idx, "Active_FY"] = target_fy
                needs_write = True
            continue

        if row_fy_year > target_start_year:
            continue

        # March 31 midnight IST has passed: auto-rollover year by year up to target_start_year
        j_raw = out_df.at[idx, "JoiningDate"] if "JoiningDate" in out_df.columns else None
        j_date = _roll_parse_date(j_raw)
        e_name = str(out_df.at[idx, "Name"]).strip() if "Name" in out_df.columns else ""
        emp_bdowns = _get_pen_leaves_index().get(e_name, ()) if e_name else ()
        row_rolled = False

        for step_year in range(row_fy_year, target_start_year):
            if j_date is not None and date(step_year + 1, 3, 31) < j_date:
                continue
            row_rolled = True
            rolled_from_fys.add(f"FY {step_year}-{step_year + 1}")
            o_cl_prev = _safe_num(out_df.at[idx, "Opening_CL"])
            a_cl_prev = _safe_num(out_df.at[idx, "Addition_CL"])
            p_cl_prev = _safe_num(out_df.at[idx, "Penalty_CL(UL Effect)"])
            u_cl_prev = _safe_num(out_df.at[idx, "Used_CL"])
            if pd.notnull(out_df.at[idx, "Balance_CL"]) and str(out_df.at[idx, "Balance_CL"]).strip() != "":
                curr_cl = _safe_num(out_df.at[idx, "Balance_CL"])
            else:
                curr_cl = max(0.0, o_cl_prev + a_cl_prev - p_cl_prev - u_cl_prev)

            o_sl_prev = _safe_num(out_df.at[idx, "Opening_SL"])
            a_sl_prev = _safe_num(out_df.at[idx, "Addition_SL"])
            p_sl_prev = _safe_num(out_df.at[idx, "Penalty_SL(UL Effect)"])
            u_sl_prev = _safe_num(out_df.at[idx, "Used_SL"])
            if pd.notnull(out_df.at[idx, "Balance_SL"]) and str(out_df.at[idx, "Balance_SL"]).strip() != "":
                curr_sl = _safe_num(out_df.at[idx, "Balance_SL"])
            else:
                curr_sl = max(0.0, o_sl_prev + a_sl_prev - p_sl_prev - u_sl_prev)

            o_al_prev = _safe_num(out_df.at[idx, "Opening_AL"])
            a_al_prev = _safe_num(out_df.at[idx, "Addition_AL"])
            p_al_prev = _safe_num(out_df.at[idx, "Penalty_AL(UL Effect)"])
            u_al_prev = _safe_num(out_df.at[idx, "Used_AL"])
            e_al_prev = _safe_num(out_df.at[idx, "Encashed_AL"])
            if pd.notnull(out_df.at[idx, "Balance_AL"]) and str(out_df.at[idx, "Balance_AL"]).strip() != "":
                curr_al = _safe_num(out_df.at[idx, "Balance_AL"])
            else:
                curr_al = max(0.0, o_al_prev + a_al_prev - p_al_prev - u_al_prev - e_al_prev)

            o_ul_prev = _safe_num(out_df.at[idx, "Opening_UL"])
            a_ul_prev = _safe_num(out_df.at[idx, "Addition_UL"])
            u_ul_prev = _safe_num(out_df.at[idx, "Used_UL"])
            if pd.notnull(out_df.at[idx, "Balance_UL"]) and str(out_df.at[idx, "Balance_UL"]).strip() != "":
                curr_ul = _safe_num(out_df.at[idx, "Balance_UL"])
            else:
                curr_ul = max(0.0, o_ul_prev + a_ul_prev - u_ul_prev)

            cum_ul_prev = _safe_num(out_df.at[idx, "Cumulative_UL"])
            curr_eff_prev = _safe_num(out_df.at[idx, curr_eff_col])
            cum_eff_prev = _safe_num(out_df.at[idx, cum_eff_col])

            # Inspect pre-indexed approved leaves spanning step_year and new_fy_year
            new_fy_year = step_year + 1
            step_eval_date = eval_today if (step_year + 1 == target_start_year) else date(step_year + 1, 4, 1)
            days_in_new_fy = (date(new_fy_year + 1, 3, 31) - date(new_fy_year, 4, 1)).days + 1
            step_fy_ul = 0.0
            step_fy_eff = 0.0
            unpart_step_ul = 0.0
            unpart_step_eff = 0.0
            new_fy_ul = 0.0
            new_fy_eff = 0.0
            new_fy_elapsed_pen = 0.0

            for l_is_ul, l_is_eff, bdown in emp_bdowns:
                try:
                    if step_year in bdown:
                        step_seg_d = float(bdown[step_year]["days"])
                        step_and_future_d = sum(float(v["days"]) for y, v in bdown.items() if y >= step_year)
                        if l_is_ul:
                            step_fy_ul += step_seg_d
                            unpart_step_ul += step_and_future_d
                        if l_is_eff:
                            step_fy_eff += step_seg_d
                            unpart_step_eff += step_and_future_d
                    if new_fy_year in bdown:
                        new_seg_info = bdown[new_fy_year]
                        seg_d = float(new_seg_info["days"])
                        if l_is_ul:
                            new_fy_ul += seg_d
                        if l_is_eff:
                            new_fy_eff += seg_d
                        pen_applies = (l_is_ul and ul_affects_svc) or l_is_eff
                        if pen_applies:
                            if not service_freeze:
                                new_fy_elapsed_pen += seg_d
                            else:
                                n_s = new_seg_info["seg_start"]
                                n_e = new_seg_info["seg_end"]
                                if step_eval_date < n_s:
                                    continue
                                elif step_eval_date >= n_e:
                                    new_fy_elapsed_pen += seg_d
                                else:
                                    seg_cal = max(1, (n_e - n_s).days + 1)
                                    el_cal = max(0, (step_eval_date - n_s).days + 1)
                                    sc_d = seg_d * (float(el_cal) / float(seg_cal))
                                    new_fy_elapsed_pen += (min(seg_d, sc_d) if seg_d >= 0 else max(seg_d, sc_d))
                except Exception:
                    continue

            # Reconcile ending-FY Used_UL / Service-Affecting counters if stamped with unpartitioned multi-FY totals
            # or if LeaveRequests had approved step_year UL not yet reflected in Used_UL
            if unpart_step_ul > step_fy_ul and abs(u_ul_prev - unpart_step_ul) < 1e-4:
                future_overdeducted = max(0.0, unpart_step_ul - max(0.0, step_fy_ul))
                u_ul_prev = max(0.0, step_fy_ul)
                if (o_ul_prev + a_ul_prev) > 0:
                    curr_ul = max(0.0, o_ul_prev + a_ul_prev - u_ul_prev)
                else:
                    curr_ul = max(0.0, curr_ul + future_overdeducted)
            elif u_ul_prev == 0.0 and step_fy_ul > 0.0:
                u_ul_prev = step_fy_ul
                curr_ul = max(0.0, curr_ul - step_fy_ul)

            if unpart_step_eff > step_fy_eff and abs(curr_eff_prev - unpart_step_eff) < 1e-4:
                curr_eff_prev = max(0.0, step_fy_eff)
            elif curr_eff_prev == 0.0 and step_fy_eff > 0.0:
                curr_eff_prev = step_fy_eff

            # 1. Promote ending-FY UL & Service-Affecting counters into historical cumulatives
            new_cum_ul = max(0.0, cum_ul_prev + u_ul_prev)
            new_cum_eff = max(0.0, cum_eff_prev + curr_eff_prev)
            new_service_penalty = (new_cum_ul if ul_affects_svc else 0.0) + new_cum_eff

            out_df.at[idx, "Cumulative_UL"] = new_cum_ul
            out_df.at[idx, cum_eff_col] = new_cum_eff
            out_df.at[idx, curr_eff_col] = 0.0
            out_df.at[idx, "Service Year Penalty"] = new_service_penalty

            # 2. Carry forward accumulated leaves & reset non-accumulated leaves, reset penalties & usage
            if al_accum:
                opening_al_new = min(al_cap, max(0.0, curr_al)) if al_cap > 0.0 else max(0.0, curr_al)
            else:
                opening_al_new = 0.0

            if ul_accum:
                opening_ul_new = max(0.0, curr_ul)
            else:
                opening_ul_new = 0.0

            if cl_accum:
                opening_cl_new = min(cl_cap, max(0.0, curr_cl)) if cl_cap > 0.0 else max(0.0, curr_cl)
            else:
                opening_cl_new = 0.0

            if sl_accum:
                opening_sl_new = min(sl_cap, max(0.0, curr_sl)) if sl_cap > 0.0 else max(0.0, curr_sl)
            else:
                opening_sl_new = 0.0

            out_df.at[idx, "Opening_AL"] = opening_al_new
            out_df.at[idx, "Opening_UL"] = opening_ul_new
            out_df.at[idx, "Opening_CL"] = opening_cl_new
            out_df.at[idx, "Opening_SL"] = opening_sl_new

            is_full_year_ul = (new_fy_ul >= (days_in_new_fy - 1e-4))

            eff_add_cl = cl_quota if (not is_full_year_ul or cl_earned_ul) else 0.0
            eff_add_sl = sl_quota if (not is_full_year_ul or sl_earned_ul) else 0.0
            eff_add_al = al_quota if (not is_full_year_ul or al_earned_ul) else 0.0

            raw_new_b_cl = opening_cl_new + eff_add_cl
            raw_new_b_sl = opening_sl_new + eff_add_sl
            raw_new_b_al = opening_al_new + eff_add_al

            new_b_cl = min(cl_cap, raw_new_b_cl) if cl_cap > 0.0 else raw_new_b_cl
            new_b_sl = min(sl_cap, raw_new_b_sl) if sl_cap > 0.0 else raw_new_b_sl
            new_b_al = min(al_cap, raw_new_b_al) if al_cap > 0.0 else raw_new_b_al

            out_df.at[idx, "Addition_AL"] = eff_add_al
            out_df.at[idx, "Addition_SL"] = eff_add_sl
            out_df.at[idx, "Addition_CL"] = eff_add_cl
            out_df.at[idx, "Balance_CL"] = new_b_cl
            out_df.at[idx, "Balance_SL"] = new_b_sl
            out_df.at[idx, "Balance_AL"] = new_b_al

            out_df.at[idx, "Used_AL"] = 0.0
            out_df.at[idx, "Used_SL"] = 0.0
            out_df.at[idx, "Used_CL"] = 0.0
            out_df.at[idx, "Used_UL"] = max(0.0, new_fy_ul)
            out_df.at[idx, curr_eff_col] = max(0.0, new_fy_eff)
            out_df.at[idx, "Encashed_AL"] = 0.0

            out_df.at[idx, "Penalty_CL(UL Effect)"] = 0.0
            out_df.at[idx, "Penalty_SL(UL Effect)"] = 0.0
            out_df.at[idx, "Penalty_AL(UL Effect)"] = 0.0

            # 3. Recompute Calendar & Actual Service Year and grant UL milestones
            ul_addition = ul_annual_quota
            if j_date is not None:
                try:
                    cal_days = max(0, (step_eval_date - j_date).days)
                    cal_years = round(cal_days / 365.25, 2)
                    actual_days = max(0, cal_days - (new_service_penalty + max(0.0, new_fy_elapsed_pen)))
                    tenure_years = round(actual_days / 365.25, 2)

                    out_df.at[idx, "Service Year"] = cal_years
                    out_df.at[idx, "Actual Service Year"] = tenure_years

                    last_ms = _safe_num(out_df.at[idx, "Last_UL_Milestone"])
                    lifetime_ul_so_far = opening_ul_new + new_cum_ul
                    remaining_ul_cap = max(0.0, ul_career_cap - lifetime_ul_so_far) if ul_career_cap > 0.0 else ms_grant

                    for ms in milestones_desc:
                        if tenure_years >= ms and last_ms < ms:
                            ul_addition += (min(ms_grant, remaining_ul_cap) if ul_career_cap > 0.0 else ms_grant)
                            out_df.at[idx, "Last_UL_Milestone"] = float(ms)
                            break
                except Exception:
                    pass

            out_df.at[idx, "Addition_UL"] = ul_addition
            out_df.at[idx, "Balance_CL"] = new_b_cl
            out_df.at[idx, "Balance_SL"] = new_b_sl
            out_df.at[idx, "Balance_AL"] = new_b_al
            out_df.at[idx, "Balance_UL"] = max(0.0, opening_ul_new + ul_addition - max(0.0, new_fy_ul))

        if row_rolled:
            rolled_over = True
            needs_write = True
            rolled_count += 1
        else:
            needs_write = True
            seeded = True
            if j_date is not None:
                try:
                    cal_days = max(0, (eval_today - j_date).days)
                    cal_years = round(cal_days / 365.25, 2)
                    cum_ul_v = _safe_num(out_df.at[idx, "Cumulative_UL"])
                    u_ul_v = _safe_num(out_df.at[idx, "Used_UL"])
                    cum_eff_v = _safe_num(out_df.at[idx, cum_eff_col])
                    curr_eff_v = _safe_num(out_df.at[idx, curr_eff_col])
                    pen_v = (max(0.0, cum_ul_v + u_ul_v) if ul_affects_svc else 0.0) + max(0.0, cum_eff_v + curr_eff_v)
                    actual_days = max(0, cal_days - pen_v)
                    out_df.at[idx, "Service Year"] = cal_years
                    out_df.at[idx, "Service Year Penalty"] = pen_v
                    out_df.at[idx, "Actual Service Year"] = round(actual_days / 365.25, 2)
                except Exception:
                    pass

        out_df.at[idx, "Active_FY"] = target_fy

    return out_df, {
        "needs_write": needs_write,
        "seeded": seeded,
        "rolled_over": rolled_over,
        "target_fy": target_fy,
        "rolled_from_fys": sorted(rolled_from_fys),
        "rolled_count": rolled_count,
    }


BALANCE_ADJUSTMENT_CATEGORIES = [
    ("AL", "AL (Annual Leave Balance)"),
    ("CL", "CL (Casual Leave Balance)"),
    ("SL", "SL (Sick Leave Balance)"),
    ("UL", "UL (Unpaid Leave Balance)"),
    ("Cumulative_UL", "Cumulative UL (Prior FYs Total Days)"),
    ("Used_UL", "Used UL (Current FY Days)"),
    ("Last_UL_Milestone", "Last UL Milestone (5, 10, 15, 20 Years)"),
    ("Cumulative_Eff_Service", "Cumulative Leave Affecting Service Year (Education Leave & Etc.)"),
    ("Current_Eff_Service", "Current FY Leave Affecting Service Year"),
]

AUDIT_LOG_COLUMNS = [
    "Timestamp", "Adjuster", "Employee", "Category",
    "Old_Value", "New_Value", "Net_Delta", "Reason"
]
AUDIT_LOG_MAX_ROWS = 2000
AUDIT_LOG_ROLLOVER_KEEP_ROWS = 1000
AUDIT_LOG_RETAIN_DAYS = 90


def apply_staff_balance_correction(
    staff_df,
    staff_name,
    category_key,
    adj_mode,
    amount,
    current_balances=None,
    authorized_branches=None,
    rules_override=None,
):
    """
    Pure helper applying a discrepancy correction to a single staff row in a Staff_Master DataFrame.
    Supports leave balances (AL/CL/SL/UL), Cumulative UL, Used UL, Last_UL_Milestone, and
    Cumulative / Current-FY Service-Affecting leave counters.
    Enforces server-side branch boundary verification if authorized_branches is provided.
    Returns (updated_df, old_val, new_val, net_delta).
    """
    if staff_df is None or staff_df.empty:
        raise ValueError("Staff Master table is empty.")

    matches = staff_df[staff_df["Name"].astype(str).str.strip() == str(staff_name).strip()]
    if matches.empty:
        raise ValueError(f"Employee '{staff_name}' not found in Staff_Master.")

    if authorized_branches is not None:
        auth_list = [authorized_branches] if isinstance(authorized_branches, str) else list(authorized_branches)
        norm_auth = {normalize_branch_code(b) for b in auth_list} if "normalize_branch_code" in globals() else set(auth_list)
        emp_branch = str(matches.iloc[0].get("Branch", "")).strip()
        emp_branch_norm = normalize_branch_code(emp_branch) if "normalize_branch_code" in globals() else emp_branch
        if emp_branch and emp_branch_norm not in norm_auth and emp_branch not in auth_list:
            raise PermissionError(
                f"Permission denied: Employee '{staff_name}' belongs to branch '{emp_branch}', "
                f"outside authorized branches {authorized_branches}."
            )

    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if _resolve_rules_fn is not None else (rules_override if isinstance(rules_override, dict) else {})
    _lt_rules = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}

    out_df = staff_df.copy()
    for c in out_df.columns:
        out_df[c] = out_df[c].astype(object)
    u_idx = matches.index[0]

    # Robust case-insensitive mode parsing
    mode_str = str(adj_mode).strip().lower()
    is_deduct_mode = mode_str in ("deduct", "deduction", "minus", "subtract")
    is_add_mode = "add" in mode_str or "+/-" in mode_str or is_deduct_mode

    if is_deduct_mode:
        effective_amount = -abs(float(amount))
    else:
        effective_amount = float(amount)

    cum_eff_col = "Cummulative leave effecting Service year.(Education Leave & Etc)."
    curr_eff_col = "Current Year Leave affecting Service Year"
    direct_col_map = {
        "Cumulative_UL": "Cumulative_UL",
        "Used_UL": "Used_UL",
        "Last_UL_Milestone": "Last_UL_Milestone",
        "Cumulative_Eff_Service": cum_eff_col,
        "Current_Eff_Service": curr_eff_col,
    }

    if category_key in ("CL", "SL", "AL", "UL"):
        open_col = f"Opening_{category_key}"
        bal_col = f"Balance_{category_key}"
        if open_col not in out_df.columns:
            out_df[open_col] = 0.0
        if bal_col not in out_df.columns:
            out_df[bal_col] = 0.0

        cur_open = _safe_num(out_df.at[u_idx, open_col])
        if current_balances and category_key in current_balances:
            old_val = _safe_num(current_balances.get(category_key, 0.0))
        else:
            old_val = _safe_num(out_df.at[u_idx, bal_col])

        diff = effective_amount if is_add_mode else (effective_amount - old_val)
        out_df.at[u_idx, open_col] = cur_open + diff
        new_val = max(0.0, old_val + diff)
        if category_key in ("CL", "SL", "AL"):
            default_cap = 180.0 if category_key == "AL" else 0.0
            cat_cfg = _lt_rules.get(category_key, {}) if isinstance(_lt_rules.get(category_key), dict) else {}
            cap_val = float(cat_cfg.get("Max_Accumulation_Cap", default_cap) if "Max_Accumulation_Cap" in cat_cfg else default_cap)
            if cap_val > 0.0:
                new_val = min(cap_val, new_val)
        out_df.at[u_idx, bal_col] = new_val
        return out_df, old_val, new_val, (new_val - old_val)

    if category_key in direct_col_map:
        col = direct_col_map[category_key]
        if col not in out_df.columns:
            out_df[col] = 0.0

        old_val = _safe_num(out_df.at[u_idx, col])
        raw_new = (old_val + effective_amount) if is_add_mode else effective_amount
        new_val = max(0.0, raw_new)
        out_df.at[u_idx, col] = new_val

        # Keep Balance_UL synchronized immediately when Used_UL changes
        if category_key == "Used_UL":
            o_ul = _safe_num(out_df.at[u_idx, "Opening_UL"]) if "Opening_UL" in out_df.columns else 0.0
            a_ul = _safe_num(out_df.at[u_idx, "Addition_UL"]) if "Addition_UL" in out_df.columns else 0.0
            if "Balance_UL" in out_df.columns:
                out_df.at[u_idx, "Balance_UL"] = max(0.0, o_ul + a_ul - new_val)

        # Keep Service Year Penalty synchronized with all 4 cumulative/current components (excluding milestone)
        if "Service Year Penalty" in out_df.columns:
            total_pen = (
                (_safe_num(out_df.at[u_idx, cum_eff_col]) if cum_eff_col in out_df.columns else 0.0)
                + (_safe_num(out_df.at[u_idx, curr_eff_col]) if curr_eff_col in out_df.columns else 0.0)
                + (_safe_num(out_df.at[u_idx, "Used_UL"]) if "Used_UL" in out_df.columns else 0.0)
                + (_safe_num(out_df.at[u_idx, "Cumulative_UL"]) if "Cumulative_UL" in out_df.columns else 0.0)
            )
            out_df.at[u_idx, "Service Year Penalty"] = total_pen

        return out_df, old_val, new_val, (new_val - old_val)

    raise ValueError(f"Unknown balance adjustment category: {category_key}")


@st.cache_data(ttl=30)
def _load_audit_log_cached():
    _gs_read = globals().get("_gsheets_read")
    try:
        df = _gs_read(conn, "AuditLog") if callable(_gs_read) else conn.read(worksheet="AuditLog")
        if df is None or df.empty:
            return pd.DataFrame(columns=AUDIT_LOG_COLUMNS)
        for c in AUDIT_LOG_COLUMNS:
            if c not in df.columns:
                df[c] = None
        return df[AUDIT_LOG_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=AUDIT_LOG_COLUMNS)


def load_audit_log(_conn_override=None, conn_override=None):
    """
    Loads balance adjustment audit logs from the persistent AuditLog worksheet in Google Sheets.
    Returns an empty DataFrame with AUDIT_LOG_COLUMNS schema if missing or empty.
    """
    effective_conn = _conn_override if _conn_override is not None else conn_override
    if effective_conn is None:
        cached_fn = globals().get("_load_audit_log_cached")
        if callable(cached_fn):
            return cached_fn()
    active_conn = effective_conn if effective_conn is not None else conn
    try:
        _gs_read = globals().get("_gsheets_read")
        df = _gs_read(active_conn, "AuditLog") if callable(_gs_read) else active_conn.read(worksheet="AuditLog")
        if df is None or df.empty:
            return pd.DataFrame(columns=AUDIT_LOG_COLUMNS)
        for c in AUDIT_LOG_COLUMNS:
            if c not in df.columns:
                df[c] = None
        return df[AUDIT_LOG_COLUMNS]
    except Exception:
        return pd.DataFrame(columns=AUDIT_LOG_COLUMNS)


load_audit_log.clear = _load_audit_log_cached.clear


def record_balance_audit_log(
    adjuster,
    employee,
    category,
    old_val,
    new_val,
    net_delta,
    reason,
    timestamp=None,
    conn_override=None,
):
    """
    Appends an audit record to the persistent AuditLog worksheet in Google Sheets.
    Creates the AuditLog worksheet defensively if missing.
    Returns (success: bool, updated_df: pd.DataFrame).
    """
    if not str(reason).strip():
        raise ValueError("Audit reason is mandatory.")

    ts = timestamp or get_cta_now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "Timestamp": ts,
        "Adjuster": str(adjuster or "Admin").strip(),
        "Employee": str(employee).strip(),
        "Category": str(category).strip(),
        "Old_Value": float(old_val),
        "New_Value": float(new_val),
        "Net_Delta": float(net_delta),
        "Reason": str(reason).strip(),
    }
    new_row_df = pd.DataFrame([entry])

    active_conn = conn_override if conn_override is not None else conn
    _gs_read = globals().get("_gsheets_read")
    _retry_fn = globals().get("_gsheets_retry")
    existing_df = None
    try:
        existing_df = _gs_read(active_conn, "AuditLog") if callable(_gs_read) else active_conn.read(worksheet="AuditLog")
    except Exception:
        existing_df = None

    if existing_df is not None and not existing_df.empty:
        updated_df = pd.concat([existing_df, new_row_df], ignore_index=True)
    else:
        updated_df = new_row_df

    for c in AUDIT_LOG_COLUMNS:
        if c not in updated_df.columns:
            updated_df[c] = None
    updated_df = updated_df[AUDIT_LOG_COLUMNS]

    max_rows = globals().get("AUDIT_LOG_MAX_ROWS", 2000)
    was_trimmed = False
    if len(updated_df) > max_rows:
        updated_df = updated_df.tail(max_rows).reset_index(drop=True)
        was_trimmed = True

    ws = None
    try:
        if hasattr(active_conn, "client") and hasattr(active_conn.client, "_open_spreadsheet"):
            spread = _retry_fn(active_conn.client._open_spreadsheet, spreadsheet=None, folder_id=None) if callable(_retry_fn) else active_conn.client._open_spreadsheet(spreadsheet=None, folder_id=None)
            if spread is not None:
                try:
                    ws = spread.worksheet("AuditLog")
                except Exception:
                    ws = spread.add_worksheet(title="AuditLog", rows="2000", cols="10")
    except Exception:
        ws = None

    try:
        used_append = False
        if (
            not was_trimmed
            and existing_df is not None
            and not existing_df.empty
            and ws is not None
            and getattr(type(ws), "__module__", "").startswith("gspread")
            and callable(getattr(ws, "append_row", None))
        ):
            try:
                hdr = [str(h).strip() for h in ws.row_values(1)[:len(AUDIT_LOG_COLUMNS)]]
                if hdr == AUDIT_LOG_COLUMNS:
                    row_vals = [entry[c] for c in AUDIT_LOG_COLUMNS]
                    if callable(_retry_fn):
                        _retry_fn(ws.append_row, row_vals, value_input_option="USER_ENTERED")
                    else:
                        ws.append_row(row_vals, value_input_option="USER_ENTERED")
                    used_append = True
            except Exception:
                used_append = False

        if not used_append:
            if callable(_retry_fn):
                _retry_fn(active_conn.update, worksheet="AuditLog", data=updated_df)
            else:
                active_conn.update(worksheet="AuditLog", data=updated_df)

        try:
            _inv = globals().get("_invalidate_sheet_cache")
            if callable(_inv):
                _inv("AuditLog")
            else:
                for _fn_n in ("_load_audit_log_cached", "load_audit_log"):
                    _fn_obj = globals().get(_fn_n)
                    if callable(getattr(_fn_obj, "clear", None)):
                        _fn_obj.clear()
        except Exception:
            pass
        return True, updated_df
    except Exception:
        return False, updated_df


def make_audit_log_entry(
    adjuster,
    adjuster_role=None,
    adjuster_branch=None,
    employee=None,
    employee_branch=None,
    category=None,
    adjustment_type=None,
    old_value=0.0,
    new_value=0.0,
    net_difference=0.0,
    reason="",
    timestamp=None,
    **kwargs,
):
    """
    Helper creating a validated audit entry dict.
    Raises ValueError if reason is empty or whitespace.
    """
    if not str(reason).strip():
        raise ValueError("Audit reason is mandatory.")

    ts = timestamp or get_cta_now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "Timestamp": ts,
        "AdjustedBy": str(adjuster or "Admin").strip(),
        "AdjusterRole": str(adjuster_role or "Admin").strip(),
        "AdjusterBranch": str(adjuster_branch or "HQ").strip(),
        "Employee": str(employee or "").strip(),
        "EmployeeBranch": str(employee_branch or "HQ").strip(),
        "Category": str(category or "").strip(),
        "AdjustmentType": str(adjustment_type or "").strip(),
        "OldValue": float(old_value),
        "NewValue": float(new_value),
        "NetDifference": float(net_difference),
        "Reason": str(reason).strip(),
    }


def append_audit_log(entry, conn_obj=None):
    """
    Appends an audit entry dict to the AuditLog worksheet.
    """
    active_conn = conn_obj if conn_obj is not None else conn
    adj = entry.get("Adjuster") or entry.get("AdjustedBy", "Admin")
    emp = entry.get("Employee", "")
    cat = entry.get("Category", "")
    old_v = entry.get("Old_Value") if "Old_Value" in entry else entry.get("OldValue", 0.0)
    new_v = entry.get("New_Value") if "New_Value" in entry else entry.get("NewValue", 0.0)
    diff = entry.get("Net_Delta") if "Net_Delta" in entry else entry.get("NetDifference", 0.0)
    rsn = entry.get("Reason", "")
    ts = entry.get("Timestamp")
    return record_balance_audit_log(
        adjuster=adj,
        employee=emp,
        category=cat,
        old_val=old_v,
        new_val=new_v,
        net_delta=diff,
        reason=rsn,
        timestamp=ts,
        conn_override=active_conn,
    )



def _parse_cta_date(val):
    """IST-safe date parser immune to pandas 3.0.2 mixed-format NaT drop and mixed-tz errors."""
    if val is None:
        return None
    try:
        if pd.isnull(val):
            return None
    except Exception:
        pass
    if isinstance(val, datetime):
        if getattr(val, "tzinfo", None) is not None:
            return val.astimezone(CTA_TZ).date()
        return val.date()
    if hasattr(val, "to_pydatetime"):
        try:
            if pd.isnull(val):
                return None
            ts = val.to_pydatetime()
            if ts is None:
                return None
            if getattr(ts, "tzinfo", None) is not None:
                return ts.astimezone(CTA_TZ).date()
            return ts.date()
        except Exception:
            return None
    if isinstance(val, date):
        return val
    try:
        if pd.isnull(val):
            return None
    except Exception:
        pass
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "nat", "null"}:
        return None
    if ("T" in s or s.endswith(("Z", "z")) or ("+" in s[10:] if len(s) > 10 else False) or ("-" in s[10:] and ":" in s[10:] if len(s) > 10 else False)):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(CTA_TZ).date()
            return dt.date()
        except Exception:
            pass
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.astimezone(CTA_TZ).date()
        return dt.date()
    except Exception:
        pass
    if len(s) >= 10:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except ValueError:
                continue
    return None


def filter_df_for_fy_archive(df, fy_start_year, start_col, end_col):
    """Returns rows whose [start_col, end_col] overlaps [Apr 1 fy_start_year, Mar 31 fy_start_year+1].
    Passes through schema-minimal DataFrames lacking both date columns; excludes dateless rows otherwise.
    """
    if df is None:
        return pd.DataFrame()
    if df.empty or fy_start_year is None:
        return df.copy()
    has_start = start_col in df.columns
    has_end = end_col in df.columns
    if not has_start and not has_end:
        return df.copy()

    fy_start = date(int(fy_start_year), 4, 1)
    fy_end = date(int(fy_start_year) + 1, 3, 31)
    start_vals = df[start_col].tolist() if has_start else [None] * len(df)
    end_vals = df[end_col].tolist() if has_end else [None] * len(df)

    keep_mask = []
    for raw_s, raw_e in zip(start_vals, end_vals):
        sd = _parse_cta_date(raw_s)
        ed = _parse_cta_date(raw_e)
        if sd is None and ed is None:
            keep_mask.append(False)
            continue
        s_d = sd if sd is not None else ed
        e_d = ed if ed is not None else sd
        if e_d < s_d:
            e_d = s_d
        keep_mask.append(e_d >= fy_start and s_d <= fy_end)

    return df.loc[keep_mask].copy().reset_index(drop=True)


def prune_closed_prior_fy_records(df, target_fy_start_year, kind="leave"):
    """Returns rows to RETAIN in the active sheet after FY rollover.
    Retains non-terminal statuses, unpunched/unendorsed records, cross-FY/future rows,
    a 14-day March continuity tail for leaves, and dateless/unparseable/schema-minimal rows.
    """
    if df is None:
        return pd.DataFrame()
    if df.empty or target_fy_start_year is None:
        return df.copy()

    new_fy_start = date(int(target_fy_start_year), 4, 1)
    cols = set(df.columns)
    kind_norm = str(kind or "leave").strip().lower()

    if kind_norm == "leave":
        if "StartDate" not in cols and "EndDate" not in cols:
            return df.copy()
        leave_continuity_cutoff = new_fy_start - timedelta(days=14)
        retain_mask = []
        for _, row in df.iterrows():
            status = str(row.get("Status", "")).strip()
            status_low = status.lower()
            if status_low not in {"approved", "rejected", "cancelled"}:
                retain_mask.append(True)
                continue
            if status_low == "approved" and "AccountsPunched" in cols:
                ap_val = row.get("AccountsPunched")
                if pd.isna(ap_val) or ap_val == "" or str(ap_val).strip().lower() in {"", "false", "no", "0", "nan", "none", "pending"}:
                    retain_mask.append(True)
                    continue
            if status_low in {"pending", "approved"} and "SelectedCoAdmin" in cols:
                sel_co = str(row.get("SelectedCoAdmin", "")).strip()
                if sel_co and sel_co.lower() not in {"", "nan", "none", "n/a", "not-applicable"}:
                    if "CoAdminAcknowledged" not in cols:
                        retain_mask.append(True)
                        continue
                    co_ack = row.get("CoAdminAcknowledged")
                    co_ack_s = "" if pd.isna(co_ack) else str(co_ack).strip().lower()
                    if co_ack_s in {"", "nan", "none", "no", "pending", "false", "0"}:
                        retain_mask.append(True)
                        continue
            sd = _parse_cta_date(row.get("StartDate")) if "StartDate" in cols else None
            ed = _parse_cta_date(row.get("EndDate")) if "EndDate" in cols else None
            eff_end = ed if ed is not None else sd
            if sd is not None and eff_end is not None and eff_end < sd:
                eff_end = sd
            if eff_end is None or eff_end >= leave_continuity_cutoff:
                retain_mask.append(True)
                continue
            retain_mask.append(False)
        return df.loc[retain_mask].copy().reset_index(drop=True)

    # kind == "tour"
    if "DepartureDate" not in cols and "ArrivalDate" not in cols:
        return df.copy()
    retain_mask = []
    for _, row in df.iterrows():
        status = str(row.get("Status", "")).strip()
        status_low = status.lower()
        if status_low not in {"sanctioned", "completed", "settled", "rejected", "cancelled"}:
            retain_mask.append(True)
            continue
        if status_low in {"sanctioned", "completed", "settled"}:
            punched = str(row.get("AccountsPunched", "")).strip().lower() if "AccountsPunched" in cols else ""
            if punched not in {"yes", "punched"}:
                retain_mask.append(True)
                continue
        dd = _parse_cta_date(row.get("DepartureDate")) if "DepartureDate" in cols else None
        ad = _parse_cta_date(row.get("ArrivalDate")) if "ArrivalDate" in cols else None
        eff_end = ad if ad is not None else dd
        if dd is not None and eff_end is not None and eff_end < dd:
            eff_end = dd
        if eff_end is None or eff_end >= new_fy_start:
            retain_mask.append(True)
            continue
        retain_mask.append(False)
    return df.loc[retain_mask].copy().reset_index(drop=True)


def _worksheet_has_data(ws, active_conn=None, sheet_name=None):
    """Returns True if an existing Google Sheets worksheet already has >=1 non-empty data row below header."""
    vals = None
    try:
        vals = _gsheets_retry(ws.get, "A1:AD2")
    except (AttributeError, NotImplementedError, TypeError):
        vals = None
    if not isinstance(vals, (list, tuple)):
        try:
            vals = ws.get_all_values()
        except (AttributeError, NotImplementedError, TypeError):
            vals = None
    if isinstance(vals, (list, tuple)):
        if len(vals) < 2:
            return False
        for row in vals[1:]:
            cells = row if isinstance(row, (list, tuple)) else [row]
            if any(str(c).strip() != "" for c in cells):
                return True
        return False
    if active_conn is not None and sheet_name:
        try:
            existing_df = _gsheets_read(active_conn, sheet_name)
            return existing_df is not None and not existing_df.empty
        except Exception:
            return False
    return False


def ensure_auto_fy_rollover(conn_override=None, today_override=None):
    """
    Runs automatically on dashboard entry. Fast-exits in memory when Staff_Master is already on
    the current IST Financial Year. Acquires a process lock only if initial Active_FY seeding or
    an automatic March 31 midnight IST rollover is required.
    """
    active_conn = conn_override if conn_override is not None else conn
    _gs_read = globals().get("_gsheets_read")
    _retry_fn = globals().get("_gsheets_retry")

    def _read_sheet(ws_name):
        if callable(_gs_read):
            return _gs_read(active_conn, ws_name)
        try:
            return active_conn.read(worksheet=ws_name, ttl=0)
        except TypeError as te:
            if "ttl" in str(te):
                return active_conn.read(worksheet=ws_name)
            raise

    def _retry_call(fn, *a, **kw):
        if callable(_retry_fn):
            return _retry_fn(fn, *a, **kw)
        return fn(*a, **kw)

    def _inv_data_caches():
        _inv = globals().get("_invalidate_all_data_caches")
        if callable(_inv):
            _inv()
            return
        for _fn_n in (
            "load_users_data", "load_staff_master", "_read_leave_rules_sheet",
            "_load_audit_log_cached", "load_audit_log", "load_leave_requests",
            "load_tour_sanctions", "_list_spreadsheet_sheet_titles",
        ):
            _fn_obj = globals().get(_fn_n)
            if callable(getattr(_fn_obj, "clear", None)):
                try:
                    _fn_obj.clear()
                except Exception:
                    pass

    try:
        if conn_override is None:
            staff_df = load_staff_master()
        else:
            staff_df = _read_sheet("Staff_Master")
        if staff_df is None or staff_df.empty:
            return None

        eval_today = today_override if today_override is not None else get_cta_today()
        fy_start, _, _ = get_current_financial_year_dates(eval_today)
        target_start_year = fy_start.year

        if "Active_FY" in staff_df.columns:
            parsed_years = [parse_fy_start_year(v) for v in staff_df["Active_FY"].tolist()]
            if all(y == target_start_year for y in parsed_years):
                return None

        with _AUTO_ROLLOVER_LOCK:
            _inv_data_caches()
            fresh_staff_df = _read_sheet("Staff_Master")
            if fresh_staff_df is None or fresh_staff_df.empty:
                return None
            if "Branch" not in fresh_staff_df.columns:
                fresh_staff_df["Branch"] = "HQ"

            # Load persisted LeaveRules directly from Google Sheets so cold-start cloud containers
            # honor custom quotas, accumulation flags, caps, and milestones immediately on April 1.
            _load_rules_fn = globals().get("load_leave_rules")
            active_rules = None
            if callable(_load_rules_fn):
                try:
                    active_rules = _load_rules_fn(conn_override=active_conn, force_refresh=True)
                except TypeError:
                    active_rules = _load_rules_fn(conn_override=active_conn)
                except Exception:
                    active_rules = None

            try:
                df_req_fresh = _read_sheet("LeaveRequests")
            except Exception as req_ex:
                # Fail safely rather than silently rolling forward with an empty LeaveRequests DataFrame
                # (which would miss multi-FY UL leaves). Next visit retries cleanly.
                try:
                    st.session_state["_last_rollover_error"] = (
                        f"Deferred automatic FY rollover because LeaveRequests could not be read: {req_ex}"
                    )
                except Exception:
                    pass
                return None

            try:
                updated_df, info = process_auto_fy_rollover_df(
                    fresh_staff_df,
                    as_of_date=eval_today,
                    leave_df=df_req_fresh,
                    rules_override=active_rules,
                )
            except TypeError:
                updated_df, info = process_auto_fy_rollover_df(
                    fresh_staff_df, as_of_date=eval_today, leave_df=df_req_fresh
                )
            if not info.get("needs_write"):
                try:
                    if isinstance(st.session_state, dict):
                        st.session_state.pop("_last_rollover_error", None)
                except Exception:
                    pass
                return info

            try:
                synced_df = inject_balance_formulas(
                    updated_df,
                    df_req_override=df_req_fresh,
                    reference_date=eval_today,
                    rules_override=active_rules,
                )
            except TypeError:
                synced_df = inject_balance_formulas(
                    updated_df,
                    df_req_override=df_req_fresh,
                    reference_date=eval_today,
                )

            archive_errors = []
            df_tour_fresh = None
            # Archive LeaveRequests and TourSanctions snapshots BEFORE committing Staff_Master
            # so transient archive failures block Staff_Master advancement and retry cleanly next load.
            if info.get("rolled_over"):
                try:
                    df_tour_fresh = _read_sheet("TourSanctions")
                except Exception as tour_read_ex:
                    df_tour_fresh = None
                    archive_errors.append(f"TourSanctions read failed before archive: {tour_read_ex}")

                _filter_arch = globals().get("filter_df_for_fy_archive")
                _has_data_fn = globals().get("_worksheet_has_data")

                archive_plan = []
                for ended_fy in info.get("rolled_from_fys", []):
                    clean_fy = ended_fy.replace(" ", "").replace("/", "_")
                    fy_yr = parse_fy_start_year(ended_fy)
                    req_fy_df = (
                        _filter_arch(df_req_fresh, fy_yr, "StartDate", "EndDate")
                        if callable(_filter_arch)
                        else (df_req_fresh.copy() if df_req_fresh is not None else pd.DataFrame())
                    )
                    tour_fy_df = (
                        _filter_arch(df_tour_fresh, fy_yr, "DepartureDate", "ArrivalDate")
                        if callable(_filter_arch)
                        else (df_tour_fresh.copy() if df_tour_fresh is not None else pd.DataFrame())
                    )
                    archive_plan.append((ended_fy, clean_fy, req_fy_df, tour_fy_df))

                needs_archive_write = any(
                    (r_df is not None and not r_df.empty) or (t_df is not None and not t_df.empty)
                    for _, _, r_df, t_df in archive_plan
                )

                spread = None
                if needs_archive_write:
                    try:
                        spread = _retry_call(active_conn.client._open_spreadsheet, spreadsheet=None, folder_id=None)
                    except Exception as open_ex:
                        spread = None
                        archive_errors.append(f"Archive spreadsheet open failed: {open_ex}")

                def _write_archive_tab(sheet_title, df_slice, cols_count):
                    if df_slice is None or df_slice.empty:
                        return
                    if spread is None:
                        archive_errors.append(f"Archive skipped for {sheet_title}: spreadsheet handle unavailable")
                        return
                    try:
                        ws_existing = None
                        try:
                            ws_existing = spread.worksheet(sheet_title)
                        except Exception as ws_ex:
                            ws_msg = str(ws_ex).lower()
                            ws_cls = type(ws_ex).__name__.lower()
                            if (
                                "not found" in ws_msg
                                or "worksheetnotfound" in ws_msg
                                or "worksheetnotfound" in ws_cls
                                or (
                                    "403" not in ws_msg
                                    and "429" not in ws_msg
                                    and "500" not in ws_msg
                                    and "502" not in ws_msg
                                    and "503" not in ws_msg
                                    and "504" not in ws_msg
                                    and "timeout" not in ws_msg
                                    and "permission" not in ws_msg
                                )
                            ):
                                ws_existing = None
                            else:
                                raise
                        if ws_existing is not None:
                            already_populated = (
                                _has_data_fn(ws_existing, active_conn=active_conn, sheet_name=sheet_title)
                                if callable(_has_data_fn)
                                else False
                            )
                            if already_populated:
                                return
                        else:
                            spread.add_worksheet(title=sheet_title, rows="1000", cols=cols_count)
                        _retry_call(active_conn.update, worksheet=sheet_title, data=df_slice)
                    except Exception as arch_ex:
                        archive_errors.append(f"Archive write failed for {sheet_title}: {arch_ex}")

                for _, clean_fy, req_fy_df, tour_fy_df in archive_plan:
                    _write_archive_tab(f"LeaveRequests_Archive_{clean_fy}", req_fy_df, "25")
                    _write_archive_tab(f"TourSanctions_Archive_{clean_fy}", tour_fy_df, "30")

                # Concurrent cloud container guard: verify Staff_Master wasn't already rolled over
                # by another Streamlit Cloud container while archives were being written.
                try:
                    recheck_df = _read_sheet("Staff_Master")
                    if recheck_df is not None and not recheck_df.empty and "Active_FY" in recheck_df.columns:
                        recheck_years = [parse_fy_start_year(v) for v in recheck_df["Active_FY"].tolist()]
                        if all(y == target_start_year for y in recheck_years):
                            _inv_data_caches()
                            return {
                                "needs_write": False,
                                "seeded": False,
                                "rolled_over": False,
                                "target_fy": info.get("target_fy"),
                                "rolled_from_fys": [],
                                "rolled_count": 0,
                            }
                except Exception:
                    pass

                if archive_errors:
                    try:
                        st.session_state["_last_rollover_error"] = " | ".join(archive_errors)
                    except Exception:
                        pass
                    _inv_data_caches()
                    return {
                        **info,
                        "needs_write": False,
                        "rolled_over": False,
                        "committed": False,
                        "archive_errors": archive_errors,
                    }

            _retry_call(active_conn.update, worksheet="Staff_Master", data=synced_df)
            try:
                if isinstance(st.session_state, dict):
                    st.session_state.pop("_last_rollover_error", None)
            except Exception:
                pass

            # Feature 5: Safe active-sheet pruning & AuditLog rollover bounding (ONLY after commit succeeds)
            if info.get("rolled_over") and not archive_errors:
                prune_errors = []
                _prune_fn = globals().get("prune_closed_prior_fy_records")
                if callable(_prune_fn):
                    try:
                        if df_req_fresh is not None and not df_req_fresh.empty:
                            pruned_req = _prune_fn(df_req_fresh, target_start_year, kind="leave")
                            if len(pruned_req) < len(df_req_fresh):
                                _retry_call(active_conn.update, worksheet="LeaveRequests", data=pruned_req)
                    except Exception as pr_req_ex:
                        prune_errors.append(f"LeaveRequests prune warning: {pr_req_ex}")

                    try:
                        if df_tour_fresh is not None and not df_tour_fresh.empty:
                            pruned_tour = _prune_fn(df_tour_fresh, target_start_year, kind="tour")
                            if len(pruned_tour) < len(df_tour_fresh):
                                _retry_call(active_conn.update, worksheet="TourSanctions", data=pruned_tour)
                    except Exception as pr_tour_ex:
                        prune_errors.append(f"TourSanctions prune warning: {pr_tour_ex}")

                # Non-fatal AuditLog rollover archiving/trimming
                try:
                    audit_keep_rows = globals().get("AUDIT_LOG_ROLLOVER_KEEP_ROWS", 1000)
                    audit_max_rows = globals().get("AUDIT_LOG_MAX_ROWS", 2000)
                    audit_retain_days = globals().get("AUDIT_LOG_RETAIN_DAYS", 90)
                    audit_df = _read_sheet("AuditLog")
                    if audit_df is not None and not audit_df.empty and len(audit_df) > audit_keep_rows:
                        _parse_d_fn = globals().get("_parse_cta_date")
                        cutoff_d = date(int(target_start_year), 4, 1) - timedelta(days=audit_retain_days)
                        old_indices = []
                        keep_indices = []
                        if callable(_parse_d_fn) and "Timestamp" in audit_df.columns:
                            ts_fast = pd.to_datetime(audit_df["Timestamp"], format="%Y-%m-%d %H:%M:%S", errors="coerce").dt.date
                            for a_idx, raw_ts, f_d in zip(audit_df.index, audit_df["Timestamp"].tolist(), ts_fast.tolist()):
                                ts_d = f_d if (f_d is not None and not pd.isna(f_d)) else _parse_d_fn(raw_ts)
                                if ts_d is not None and ts_d < cutoff_d:
                                    old_indices.append(a_idx)
                                else:
                                    keep_indices.append(a_idx)
                        else:
                            keep_indices = list(audit_df.index)

                        if len(keep_indices) > audit_max_rows:
                            overflow_count = len(keep_indices) - audit_keep_rows
                            old_indices.extend(keep_indices[:overflow_count])
                            keep_indices = keep_indices[overflow_count:]
                        elif not old_indices and len(audit_df) > audit_keep_rows:
                            split_at = len(audit_df) - audit_keep_rows
                            old_indices = list(audit_df.index[:split_at])
                            keep_indices = list(audit_df.index[split_at:])

                        if old_indices:
                            old_audit_df = audit_df.loc[old_indices].copy().reset_index(drop=True)
                            keep_audit_df = audit_df.loc[keep_indices].copy().reset_index(drop=True)
                            rolled_fys = info.get("rolled_from_fys", [])
                            last_clean_fy = rolled_fys[-1].replace(" ", "").replace("/", "_") if rolled_fys else f"FY{target_start_year - 1}-{target_start_year}"
                            audit_arch_title = f"AuditLog_Archive_{last_clean_fy}"
                            if hasattr(active_conn, "client") and hasattr(active_conn.client, "_open_spreadsheet"):
                                a_spread = _retry_call(active_conn.client._open_spreadsheet, spreadsheet=None, folder_id=None)
                                if a_spread is not None:
                                    a_ws = None
                                    try:
                                        a_ws = a_spread.worksheet(audit_arch_title)
                                    except Exception:
                                        a_ws = None
                                    if a_ws is None:
                                        a_spread.add_worksheet(title=audit_arch_title, rows="2000", cols="10")
                                        _retry_call(active_conn.update, worksheet=audit_arch_title, data=old_audit_df)
                                    elif callable(globals().get("_worksheet_has_data")) and not globals()["_worksheet_has_data"](a_ws, active_conn=active_conn, sheet_name=audit_arch_title):
                                        _retry_call(active_conn.update, worksheet=audit_arch_title, data=old_audit_df)
                            _retry_call(active_conn.update, worksheet="AuditLog", data=keep_audit_df)
                except Exception as audit_ex:
                    prune_errors.append(f"AuditLog trim warning: {audit_ex}")

                if prune_errors:
                    info["prune_errors"] = prune_errors

            info["committed"] = True
            _inv_data_caches()
            return info
    except Exception as ex:
        try:
            st.session_state["_last_rollover_error"] = f"Automatic FY rollover error: {ex}"
        except Exception:
            pass
        return None


@st.cache_data(ttl=60)
def load_leave_requests():
    try:
        df = _gsheets_read(conn, "LeaveRequests")
        # Prevent TypeError when assigning strings to newly created empty columns that pandas read as float64
        for col in ["SupportedBy", "ApprovedBy", "PunchedBy", "SelectedCoAdmin", "SelectedAdmin", "Department", "Status", "CoAdminAcknowledged", "AccountsPunched", "Reason", "StartHalf", "EndHalf", "ApplicationDate"]:
            if col in df.columns:
                df[col] = df[col].astype(object)
            else:
                df[col] = ""
        return df
    except Exception as e:
        return pd.DataFrame(columns=['ID', 'Name', 'LeaveType', 'StartDate', 'EndDate', 'TotalDays', 'Department', 'SelectedCoAdmin', 'SelectedAdmin', 'Status', 'CoAdminAcknowledged', 'SupportedBy', 'AccountsPunched', 'PunchedBy', 'ApprovedBy', 'CLBalance', 'ALBalance', 'SLBalance', 'ULBalance'])


@st.cache_data(ttl=30)
def load_tour_sanctions():
    try:
        df = _gsheets_read(conn, "TourSanctions")
        if df is None:
            df = pd.DataFrame()
        obsolete_cols = [
            c for c in (
                "Department", "SanctionOrderNo", "EstimatedAdvance", "SanctionedAdvance",
                "AdvanceVoucherNo", "TourDiary", "ActualExpense", "TicketDetails", "SettledBy"
            ) if c in df.columns
        ]
        if obsolete_cols:
            df = df.drop(columns=obsolete_cols)
        for col in ["ID", "Name", "Branch", "Role", "Post", "Purpose", "Destination", "DepartureDate", "ArrivalDate", "TotalDays", "ConveyanceMode", "Status", "SelectedCoAdmin", "SelectedAdmin", "BranchManager", "BMSupportedBy", "SupportedBy", "ApprovedBy", "SanctionedBy", "AccountsPunched", "PunchedBy", "ApplicationDate", "TravelLegs"]:
            if col in df.columns:
                df[col] = df[col].astype(object)
            else:
                df[col] = ""
        return df
    except Exception as e:
        return pd.DataFrame(columns=['ID', 'Name', 'Branch', 'Role', 'Post', 'Purpose', 'Destination', 'DepartureDate', 'ArrivalDate', 'TotalDays', 'ConveyanceMode', 'Status', 'SelectedCoAdmin', 'SelectedAdmin', 'BranchManager', 'BMSupportedBy', 'SupportedBy', 'ApprovedBy', 'SanctionedBy', 'AccountsPunched', 'PunchedBy', 'ApplicationDate', 'TravelLegs'])


def calculate_tour_days(dep_date, arr_date):
    if not dep_date or not arr_date:
        return 0
    if isinstance(dep_date, str):
        dep_date = datetime.strptime(dep_date.strip(), "%Y-%m-%d").date()
    if isinstance(arr_date, str):
        arr_date = datetime.strptime(arr_date.strip(), "%Y-%m-%d").date()
    delta = (arr_date - dep_date).days + 1
    return max(1, delta)


def generate_tour_sanction_order_no(branch_code="HQ", current_tours_df=None):
    """
    Generates an official CTA Tour Sanction Order Number.
    Standard CTA format: CTA/OAG/TOUR/{YYYY}/{XXXX}
    Example: CTA/OAG/TOUR/2026/0101
    """
    year = (get_cta_today() if "get_cta_today" in globals() else date.today()).year
    prefix = "OAG"

    seq = 1
    if current_tours_df is not None and not current_tours_df.empty and "SanctionOrderNo" in current_tours_df.columns:
        existing_orders = current_tours_df["SanctionOrderNo"].dropna().astype(str).tolist()
        year_str = str(year)
        existing_seqs = []
        for ord_no in existing_orders:
            m = re.search(r"CTA/(?:[A-Z]+)/TOUR/" + year_str + r"/(\d+)", ord_no)
            if m:
                try:
                    existing_seqs.append(int(m.group(1)))
                except ValueError:
                    pass
        if existing_seqs:
            seq = max(existing_seqs) + 1
        else:
            seq = len(current_tours_df) + 1

    return f"CTA/{prefix}/TOUR/{year}/{seq:04d}"


RESTRICTED_BALANCE_TYPES = {"CL", "AL", "UL"}
RESTRICTED_COMBO_TYPES = {"CL", "AL", "UL"}
MAX_CONSECUTIVE_CL_DAYS = 5.0


def _parse_leave_date(val):
    """Parse a date/datetime/Timestamp/string into datetime.date or return None (IST-safe)."""
    _cta_fn = globals().get("_parse_cta_date")
    if callable(_cta_fn):
        return _cta_fn(val)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val.date()
    if hasattr(val, "to_pydatetime"):
        try:
            ts = val.to_pydatetime()
            return ts.date() if ts is not None else None
        except Exception:
            pass
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat", "null"):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_leave_type_info(raw_type, raw_days=0.0):
    """Return (base_type, is_reversal, abs_days) from LeaveType and TotalDays."""
    lt = str(raw_type or "").strip()
    is_rev = False
    if lt.startswith("Return-"):
        lt = lt[len("Return-"):].strip()
        is_rev = True
    elif lt.startswith("Rev-"):
        lt = lt[len("Rev-"):].strip()
        is_rev = True
    try:
        d_val = float(raw_days) if pd.notna(raw_days) and str(raw_days).strip() != "" else 0.0
    except (ValueError, TypeError):
        d_val = 0.0
    if d_val < 0:
        is_rev = True
    return lt, is_rev, abs(d_val)


def get_leave_slot_range(start_d, start_half, end_d, end_half):
    """Map leave StartDate/StartHalf..EndDate/EndHalf to inclusive half-day integer slots.
    Slot index = date.toordinal() * 2 + half (0=Morning, 1=Evening). Returns (s, e) or None."""
    sd = _parse_leave_date(start_d)
    ed = _parse_leave_date(end_d)
    if sd is None or ed is None or sd > ed:
        return None
    sh = str(start_half or "").strip().lower()
    eh = str(end_half or "").strip().lower()
    if sd == ed:
        ord2 = sd.toordinal() * 2
        if "evening" in sh and "morning" not in sh:
            return (ord2 + 1, ord2 + 1)
        if "morning" in sh and "evening" not in sh:
            return (ord2, ord2)
        if "evening" in eh and "morning" not in eh:
            return (ord2 + 1, ord2 + 1)
        if "morning" in eh and "evening" not in eh:
            return (ord2, ord2)
        return (ord2, ord2 + 1)
    start_slot = sd.toordinal() * 2 + (1 if "evening" in sh and "morning" not in sh else 0)
    end_slot = ed.toordinal() * 2 + (0 if "morning" in eh and "evening" not in eh else 1)
    if start_slot > end_slot:
        return None
    return (start_slot, end_slot)


def _format_slot_label(slot):
    d = date.fromordinal(slot // 2)
    half = "Evening" if (slot % 2 == 1) else "Morning"
    return f"{d.strftime('%Y-%m-%d')} {half}"


def _format_slot_range(s_slot, e_slot):
    if s_slot == e_slot:
        return _format_slot_label(s_slot)
    sd = date.fromordinal(s_slot // 2)
    if s_slot // 2 == e_slot // 2 and (s_slot % 2 == 0) and (e_slot % 2 == 1):
        return f"{sd.strftime('%Y-%m-%d')} Full Day"
    return f"{_format_slot_label(s_slot)} to {_format_slot_label(e_slot)}"


def is_working_slot(slot):
    """Return True if the half-day slot is a CTA working half-day.
    Mon-Fri: Morning & Evening working.
    1st Saturday of month (day <= 7): Morning (slot % 2 == 0) is working; Evening non-working.
    2nd-5th Saturday & Sunday: non-working."""
    d = date.fromordinal(slot // 2)
    half = slot % 2
    wd = d.weekday()  # 0=Mon .. 5=Sat, 6=Sun
    if wd <= 4:
        return True
    if wd == 5:
        return d.day <= 7 and half == 0
    return False


def are_slots_continuous(end_slot_a, start_slot_b):
    """Return True if start_slot_b > end_slot_a and no working slot lies strictly between them.
    True for direct adjacency (start_slot_b == end_slot_a + 1) and non-working weekend gaps."""
    if start_slot_b <= end_slot_a:
        return False
    if start_slot_b == end_slot_a + 1:
        return True
    for s in range(end_slot_a + 1, start_slot_b):
        if is_working_slot(s):
            return False
    return True


def _slots_to_runs(slots):
    """Group a set/iterable of integer slots into maximal directly adjacent (start, end) runs."""
    if not slots:
        return []
    ordered = sorted(slots)
    runs = []
    cur_s = ordered[0]
    cur_e = ordered[0]
    for s in ordered[1:]:
        if s == cur_e + 1:
            cur_e = s
        else:
            runs.append((cur_s, cur_e))
            cur_s = s
            cur_e = s
    runs.append((cur_s, cur_e))
    return runs


def get_effective_leave_balances(user_name, raw_balances, df_requests, rules_override=None):
    """Calculate effective balances for CL, AL, UL (and any custom strict-balance types)
    by subtracting Pending requests (net of Pending reversals) from available balances."""
    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if callable(_resolve_rules_fn) else (rules_override if isinstance(rules_override, dict) else {})
    _types_cfg = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}

    tracked_types = set(RESTRICTED_BALANCE_TYPES) | set(_types_cfg.keys())
    pending_taken = {t: 0.0 for t in tracked_types}
    pending_reversed = {t: 0.0 for t in tracked_types}

    if df_requests is not None and not df_requests.empty and "Name" in df_requests.columns:
        target_name = str(user_name).strip()
        n_rows = len(df_requests)
        names_l = df_requests["Name"].tolist()
        status_l = df_requests["Status"].tolist() if "Status" in df_requests.columns else [""] * n_rows
        lt_l = df_requests["LeaveType"].tolist() if "LeaveType" in df_requests.columns else [""] * n_rows
        td_l = df_requests["TotalDays"].tolist() if "TotalDays" in df_requests.columns else [0.0] * n_rows
        for nm_v, st_v, lt_v, td_v in zip(names_l, status_l, lt_l, td_l):
            if str(nm_v).strip() != target_name or str(st_v).strip() != "Pending":
                continue
            base_lt, is_rev, days_abs = _parse_leave_type_info(lt_v, td_v)
            if base_lt in tracked_types and days_abs > 0:
                if is_rev:
                    pending_reversed[base_lt] += days_abs
                else:
                    pending_taken[base_lt] += days_abs

    raw_map = {}
    net_pending = {}
    effective = {}
    for t in tracked_types:
        try:
            raw_val = float((raw_balances or {}).get(t, 0.0))
        except (ValueError, TypeError):
            raw_val = 0.0
        raw_map[t] = round(raw_val, 4)
        net_p = max(0.0, round(pending_taken[t] - pending_reversed[t], 4))
        net_pending[t] = net_p
        effective[t] = round(raw_val - net_p, 4)

    return {
        "raw": raw_map,
        "pending_taken": {t: round(pending_taken[t], 4) for t in tracked_types},
        "pending_reversed": {t: round(pending_reversed[t], 4) for t in tracked_types},
        "net_pending": net_pending,
        "effective": effective,
    }


def build_user_active_leave_segments(user_name, df_requests, window_start=None, window_end=None):
    """Build active half-day slots and merged same-type contiguous segments for a user.
    Includes Pending & Approved requests and subtracts Approved reversals chronologically.
    Returns (slot_type_map, merged_segments)."""
    if df_requests is None or df_requests.empty or "Name" not in df_requests.columns:
        return {}, []

    target_name = str(user_name).strip()
    names_l = df_requests["Name"].tolist()
    has_status = "Status" in df_requests.columns
    if has_status:
        status_l = df_requests["Status"].tolist()
        keep_idx = [
            i for i, (n, s) in enumerate(zip(names_l, status_l))
            if str(n).strip() == target_name and str(s).strip() in ("Pending", "Approved")
        ]
    else:
        keep_idx = [i for i, n in enumerate(names_l) if str(n).strip() == target_name]
    if not keep_idx:
        return {}, []
    user_rows = df_requests.iloc[keep_idx]

    win_s_slot = None
    win_e_slot = None
    if window_start is not None and window_end is not None:
        win_s_slot = window_start.toordinal() * 2
        win_e_slot = window_end.toordinal() * 2 + 1
        if "StartDate" in user_rows.columns and "EndDate" in user_rows.columns:
            s_raw = user_rows["StartDate"].tolist()
            e_raw = user_rows["EndDate"].tolist()
            if len(user_rows) > 32:
                s_fast = pd.to_datetime(user_rows["StartDate"], format="%Y-%m-%d", errors="coerce").dt.date.tolist()
                e_fast = pd.to_datetime(user_rows["EndDate"], format="%Y-%m-%d", errors="coerce").dt.date.tolist()
            else:
                s_fast = [None] * len(s_raw)
                e_fast = [None] * len(e_raw)
            keep_w = []
            for rv_s, sf, rv_e, ef in zip(s_raw, s_fast, e_raw, e_fast):
                sd = sf if (sf is not None and not pd.isna(sf)) else _parse_leave_date(rv_s)
                ed = ef if (ef is not None and not pd.isna(ef)) else _parse_leave_date(rv_e)
                if sd is None or ed is None or sd > ed:
                    keep_w.append(False)
                else:
                    keep_w.append(ed >= window_start and sd <= window_end)
            user_rows = user_rows.loc[keep_w]
            if user_rows.empty:
                return {}, []

    slot_entries = {}
    n_u = len(user_rows)
    u_status_l = user_rows["Status"].tolist() if "Status" in user_rows.columns else [""] * n_u
    u_lt_l = user_rows["LeaveType"].tolist() if "LeaveType" in user_rows.columns else [""] * n_u
    u_td_l = user_rows["TotalDays"].tolist() if "TotalDays" in user_rows.columns else [0.0] * n_u
    u_sd_l = user_rows["StartDate"].tolist() if "StartDate" in user_rows.columns else [None] * n_u
    u_sh_l = user_rows["StartHalf"].tolist() if "StartHalf" in user_rows.columns else [None] * n_u
    u_ed_l = user_rows["EndDate"].tolist() if "EndDate" in user_rows.columns else [None] * n_u
    u_eh_l = user_rows["EndHalf"].tolist() if "EndHalf" in user_rows.columns else [None] * n_u

    for st_v, lt_v, td_v, sd_v, sh_v, ed_v, eh_v in zip(u_status_l, u_lt_l, u_td_l, u_sd_l, u_sh_l, u_ed_l, u_eh_l):
        status = str(st_v).strip()
        if status not in ("Pending", "Approved"):
            continue
        base_lt, is_rev, _ = _parse_leave_type_info(lt_v, td_v)
        slot_rng = get_leave_slot_range(sd_v, sh_v, ed_v, eh_v)
        if slot_rng is None:
            continue
        s_slot, e_slot = slot_rng
        if win_s_slot is not None:
            loop_s = max(s_slot, win_s_slot)
            loop_e = min(e_slot, win_e_slot)
            if loop_s > loop_e:
                continue
        else:
            loop_s, loop_e = s_slot, e_slot

        if is_rev:
            if status != "Approved":
                continue
            for s in range(loop_s, loop_e + 1):
                entries = slot_entries.get(s)
                if not entries:
                    continue
                idx_to_remove = None
                for i in range(len(entries) - 1, -1, -1):
                    if entries[i][0] == base_lt:
                        idx_to_remove = i
                        break
                if idx_to_remove is None:
                    for i in range(len(entries) - 1, -1, -1):
                        if entries[i][1] == "Approved":
                            idx_to_remove = i
                            break
                if idx_to_remove is not None:
                    entries.pop(idx_to_remove)
                    if not entries:
                        del slot_entries[s]
        else:
            for s in range(loop_s, loop_e + 1):
                slot_entries.setdefault(s, []).append((base_lt, status))

    if not slot_entries:
        return {}, []

    slot_type_map = {s: entries[-1][0] for s, entries in slot_entries.items()}
    sorted_slots = sorted(slot_type_map.keys())
    merged_segments = []
    cur_s = sorted_slots[0]
    cur_e = sorted_slots[0]
    cur_t = slot_type_map[cur_s]
    for s in sorted_slots[1:]:
        st_type = slot_type_map[s]
        if s == cur_e + 1 and st_type == cur_t:
            cur_e = s
        else:
            merged_segments.append((cur_s, cur_e, cur_t))
            cur_s = s
            cur_e = s
            cur_t = st_type
    merged_segments.append((cur_s, cur_e, cur_t))
    return slot_type_map, merged_segments


def _iter_weekend_bridged_segments(new_s, new_e, merged_segments, allowed_types):
    """Yield (seg_s, seg_e, seg_t, relation) for segments bridged across non-working weekend slots."""
    for seg_s, seg_e, seg_t in merged_segments:
        if seg_t not in allowed_types:
            continue
        if seg_e < new_s and (new_s - seg_e > 1) and are_slots_continuous(seg_e, new_s):
            yield seg_s, seg_e, seg_t, "before"
        elif new_e < seg_s and (seg_s - new_e > 1) and are_slots_continuous(new_e, seg_s):
            yield seg_s, seg_e, seg_t, "after"


def validate_leave_application(
    user_name,
    leave_type,
    start_date,
    start_half,
    end_date,
    end_half,
    total_days,
    raw_balances,
    df_requests,
    is_reversal=False,
    rules_override=None,
):
    """Validate a leave request against CTA two-tier policy rules (configurable via LeaveRules).
    Returns dict with keys:
      - 'hard_blocks': list[str] (Tier 1: never bypassable)
      - 'policy_alerts': list[str] (Tier 2: weekend-bridged alerts, overridable via Force Apply)
      - 'effective_balances': dict returned by get_effective_leave_balances
      - 'new_slot_range': (start_slot, end_slot) or None
    """
    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if callable(_resolve_rules_fn) else (rules_override if isinstance(rules_override, dict) else {})
    _types_cfg = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}

    if _types_cfg:
        active_strict_balance_types = {
            k for k, v in _types_cfg.items()
            if bool(v.get("Strict_Balance_Required", k in RESTRICTED_BALANCE_TYPES))
        }
        active_combo_types = {
            k for k, v in _types_cfg.items()
            if bool(v.get("Non_Combinable", k in RESTRICTED_COMBO_TYPES))
        }
    else:
        active_strict_balance_types = set(RESTRICTED_BALANCE_TYPES)
        active_combo_types = set(RESTRICTED_COMBO_TYPES)

    scoped_requests = df_requests
    if df_requests is not None and not df_requests.empty and "Name" in df_requests.columns:
        target_name = str(user_name).strip()
        names_l = df_requests["Name"].tolist()
        if "Status" in df_requests.columns:
            keep_sc = [
                i for i, (n, s) in enumerate(zip(names_l, df_requests["Status"].tolist()))
                if str(n).strip() == target_name and str(s).strip() in ("Pending", "Approved")
            ]
        else:
            keep_sc = [i for i, n in enumerate(names_l) if str(n).strip() == target_name]
        scoped_requests = df_requests.iloc[keep_sc]

    hard_blocks = []
    policy_alerts = []
    eff_bal_info = get_effective_leave_balances(user_name, raw_balances, scoped_requests, rules_override=rules_override)
    slot_range = get_leave_slot_range(start_date, start_half, end_date, end_half)

    if is_reversal:
        return {
            "hard_blocks": hard_blocks,
            "policy_alerts": policy_alerts,
            "effective_balances": eff_bal_info,
            "new_slot_range": slot_range,
        }

    lt = str(leave_type or "").strip()
    try:
        req_days = float(total_days or 0.0)
    except (ValueError, TypeError):
        req_days = 0.0

    # Tier 1.1: Strict balance limit for configured leave types (default CL, AL, UL; net of Pending)
    if lt in active_strict_balance_types:
        raw_b = eff_bal_info["raw"].get(lt, 0.0)
        net_p = eff_bal_info["net_pending"].get(lt, 0.0)
        eff_b = eff_bal_info["effective"].get(lt, 0.0)
        pending_note = (
            f" (Balance: {raw_b:g}, Pending net of reversals: {net_p:g})"
            if net_p > 0 else ""
        )
        if eff_b <= 1e-6:
            hard_blocks.append(
                f"❌ **Cannot Apply for {lt}:** Your effective available {lt} balance is {max(0.0, eff_b):g} day(s){pending_note}. You cannot apply without available balance."
            )
        elif req_days > 0 and req_days > eff_b + 1e-6:
            hard_blocks.append(
                f"❌ **Cannot Apply for {lt}:** You requested {req_days:g} day(s), but your effective available {lt} balance is {eff_b:g} day(s){pending_note}."
            )

    if slot_range is None or req_days <= 0:
        return {
            "hard_blocks": hard_blocks,
            "policy_alerts": policy_alerts,
            "effective_balances": eff_bal_info,
            "new_slot_range": slot_range,
        }

    max_consec_limit = float(
        _types_cfg.get(lt, {}).get("Max_Consecutive_Days", MAX_CONSECUTIVE_CL_DAYS if lt == "CL" else 0.0)
    ) if _types_cfg else (MAX_CONSECUTIVE_CL_DAYS if lt == "CL" else 0.0)

    new_s, new_e = slot_range
    from datetime import date as _win_date, timedelta as _win_td
    _win_pad = _win_td(days=max(14, int(max_consec_limit) + 7))
    _win_start = _win_date.fromordinal(new_s // 2) - _win_pad
    _win_end = _win_date.fromordinal(new_e // 2) + _win_pad
    try:
        slot_type_map, merged_segments = build_user_active_leave_segments(
            user_name, scoped_requests, window_start=_win_start, window_end=_win_end
        )
    except TypeError:
        slot_type_map, merged_segments = build_user_active_leave_segments(user_name, scoped_requests)

    # Tier 1.2: Overlapping dates / half-days with any active (Pending or Approved) leave
    overlap_slots = [s for s in range(new_s, new_e + 1) if s in slot_type_map]
    if overlap_slots:
        ov_types = sorted({slot_type_map[s] for s in overlap_slots})
        hard_blocks.append(
            f"❌ **Overlapping Leave Dates / Half-Days:** Your selected dates overlap with existing active ({', '.join(ov_types)}) leave from {_format_slot_label(overlap_slots[0])} to {_format_slot_label(overlap_slots[-1])}."
        )

    # Tier 1.3: Direct consecutive-day cap (default: CL exceeding 5.0 consecutive days)
    direct_consec_ok = True
    if max_consec_limit > 0:
        lt_label = "Casual Leave" if lt == "CL" else f"{lt} Leave"
        lt_full = "Casual Leave (CL)" if lt == "CL" else lt
        if req_days > max_consec_limit + 1e-6:
            direct_consec_ok = False
            hard_blocks.append(
                f"❌ **{lt_label} Exceeds {max_consec_limit:g} Consecutive Days:** A single {lt_full} request cannot exceed {max_consec_limit:g} consecutive days (requested {req_days:g} days)."
            )
        else:
            existing_lt_slots = {s for s, t in slot_type_map.items() if t == lt}
            all_lt_slots = existing_lt_slots | set(range(new_s, new_e + 1))
            lt_runs = _slots_to_runs(all_lt_slots)
            new_run_idx = next(
                (i for i, (rs, re) in enumerate(lt_runs) if rs <= new_s and new_e <= re),
                None,
            )
            if new_run_idx is not None:
                run_s, run_e = lt_runs[new_run_idx]
                direct_days = (run_e - run_s + 1) * 0.5
                if direct_days > max_consec_limit + 1e-6:
                    direct_consec_ok = False
                    hard_blocks.append(
                        f"❌ **Split {lt_label} Exceeds {max_consec_limit:g} Consecutive Days:** Combining this request with your directly adjacent {lt} ({_format_slot_range(run_s, run_e)}) totals {direct_days:g} consecutive days."
                    )

    # Tier 1.4: Direct chaining / combining among non-combinable leave types (gap == 1 slot)
    if lt in active_combo_types:
        direct_combo_hits = []
        for adj_slot, rel_label in ((new_s - 1, "immediately before"), (new_e + 1, "immediately after")):
            adj_type = slot_type_map.get(adj_slot)
            if adj_type in active_combo_types and adj_type != lt:
                direct_combo_hits.append(f"{adj_type} {rel_label} ({_format_slot_label(adj_slot)})")
        if direct_combo_hits:
            hard_blocks.append(
                f"❌ **Direct Combining Not Allowed (CL / AL / UL):** {lt} cannot directly touch another CL/AL/UL without a working half-day in between ({'; '.join(direct_combo_hits)})."
            )

    # Tier 2.1: Weekend-bridged CL Sandwich Rule (gap > 1 slot and all intermediate slots non-working)
    if lt == "CL":
        for seg_s, seg_e, _, relation in _iter_weekend_bridged_segments(new_s, new_e, merged_segments, {"CL"}):
            left_lbl = _format_slot_label(seg_e if relation == "before" else new_e)
            right_lbl = _format_slot_label(new_s if relation == "before" else seg_s)
            policy_alerts.append(
                f"⚠️ **CL Weekend Sandwich Policy Alert:** Continuous Casual Leave (CL) between {left_lbl} and {right_lbl} spans a non-working weekend without including Saturday–Sunday. Continuous CL across a non-working weekend must include Saturday–Sunday unless there was a working half-day in between."
            )

    # Tier 2.2: Combining non-combinable types across a non-working weekend
    if lt in active_combo_types:
        other_combo_types = active_combo_types - {lt}
        for seg_s, seg_e, seg_t, relation in _iter_weekend_bridged_segments(new_s, new_e, merged_segments, other_combo_types):
            if relation == "before":
                msg = f"existing {seg_t} ending {_format_slot_label(seg_e)} and new {lt} starting {_format_slot_label(new_s)}"
            else:
                msg = f"new {lt} ending {_format_slot_label(new_e)} and existing {seg_t} starting {_format_slot_label(seg_s)}"
            policy_alerts.append(
                f"⚠️ **Weekend Combination Policy Alert ({seg_t} / {lt}):** Your {msg} bridge a non-working weekend with no working half-day in between."
            )

    # Tier 2.3: Split consecutive days > cap across a non-working weekend
    if max_consec_limit > 0 and direct_consec_ok:
        existing_lt_slots = {s for s, t in slot_type_map.items() if t == lt}
        all_lt_slots = existing_lt_slots | set(range(new_s, new_e + 1))
        lt_runs = _slots_to_runs(all_lt_slots)
        new_run_idx = next(
            (i for i, (rs, re) in enumerate(lt_runs) if rs <= new_s and new_e <= re),
            None,
        )
        if new_run_idx is not None:
            lo = new_run_idx
            while lo > 0 and are_slots_continuous(lt_runs[lo - 1][1], lt_runs[lo][0]):
                lo -= 1
            hi = new_run_idx
            while hi < len(lt_runs) - 1 and are_slots_continuous(lt_runs[hi][1], lt_runs[hi + 1][0]):
                hi += 1
            if lo < hi:
                bridged_days = sum((lt_runs[k][1] - lt_runs[k][0] + 1) * 0.5 for k in range(lo, hi + 1))
                if bridged_days > max_consec_limit + 1e-6:
                    policy_alerts.append(
                        f"⚠️ **Split {lt} > {max_consec_limit:g} Consecutive Days Across Weekend:** Combining your {lt} requests across the non-working weekend ({_format_slot_label(lt_runs[lo][0])} to {_format_slot_label(lt_runs[hi][1])}) totals {bridged_days:g} days, exceeding {max_consec_limit:g} consecutive days."
                    )

    return {
        "hard_blocks": hard_blocks,
        "policy_alerts": policy_alerts,
        "effective_balances": eff_bal_info,
        "new_slot_range": slot_range,
    }


# --- HELPER FUNCS ---


def is_unacknowledged(val):
    if pd.isna(val) or val == "" or str(val).strip().lower() in ["false", "no", "0", "nan", "pending"]:
        return True
    return False


def sanitize_text(text):
    if pd.isna(text):
        return ""
    # Return string directly as we now have a Unicode font
    return str(text)


def to_tibetan_numeral(text):
    if not isinstance(text, str):
        text = str(text)
    mapping = {
        '0': '༠', '1': '༡', '2': '༢', '3': '༣', '4': '༤',
        '5': '༥', '6': '༦', '7': '༧', '8': '༨', '9': '༩'
    }
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


def format_tibetan_date(date_str):
    if not date_str or str(date_str) in ["nan", "NaT"]:
        return ""
    date_str = str(date_str)
    if len(date_str) >= 10 and date_str[4] == '-':
        y, m, d = date_str[:4], date_str[5:7], date_str[8:10]
        return f"ཕྱི་ལོ་ {y} ཟླ་ {m} ཚེས་ {d}"
    return date_str


@st.cache_data(show_spinner=False, max_entries=100)
def generate_leave_pdf(row):
    try:
        pdf = FPDF()
        pdf.set_text_shaping(True)
        # Load the custom Tibetan font
        pdf.add_font("Monlam", style="", fname="Monlam_Uni_OuChan2.ttf")
        pdf.add_page()

        # Header (Size 16)
        pdf.set_font("Monlam", "", 16)
        pdf.cell(0, 10, "སྤྱི་ཁྱབ་རྩིས་ཞིབ་ལས་ཁང་།",
                new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(0, 10, "(དགོངས་ཞུ་འགེངས་ཤོག)",
                new_x="LMARGIN", new_y="NEXT", align="C")

        pdf.ln(10)

        # Body (Size 12)
        pdf.set_font("Monlam", "", 12)

        staff_df = load_staff_master()
        joining_date = ""
        post = ""
        if staff_df is not None and not staff_df.empty:
            user_data = staff_df[staff_df["Name"] == row.get("Name", "")]
            if not user_data.empty:
                joining_date = str(user_data.iloc[0].get("JoiningDate", ""))
                post = str(user_data.iloc[0].get(
                    "Post", "") if "Post" in user_data.columns else "")
                if post == "nan":
                    post = ""
                if joining_date == "nan" or joining_date == "NaT":
                    joining_date = ""
        if not joining_date:
            raw_j = str(row.get("JoiningDate", "")).strip()
            joining_date = "" if raw_j in ("", "nan", "NaT", "None") else raw_j
        if not post:
            raw_p = str(row.get("Post", "")).strip()
            post = "" if raw_p in ("", "nan", "None") else raw_p

        x = pdf.get_x()
        y = pdf.get_y()
        
        pdf.write(10, "༡། མཚན། ")
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        pdf.write(10, f"{row.get('Name', '')}")
        if pdf.get_x() > start_x:
            pdf.line(start_x, start_y + 8, pdf.get_x(), start_y + 8)
        
        pdf.set_xy(x + 95, y)
        pdf.write(10, "༢། ལས་བྱེད་དུ་བསྐོ་གཞག་ཞུས་ཚེས། ")
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        pdf.write(10, f"{to_tibetan_numeral(format_tibetan_date(joining_date))}")
        if pdf.get_x() > start_x:
            pdf.line(start_x, start_y + 8, pdf.get_x(), start_y + 8)
        pdf.ln(10)

        x = pdf.get_x()
        y = pdf.get_y()
        
        pdf.write(10, "༣། གནས་རིམ།/གོ་གནས། ")
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        pdf.write(10, f"{to_tibetan_numeral(post)}")
        if pdf.get_x() > start_x:
            pdf.line(start_x, start_y + 8, pdf.get_x(), start_y + 8)
        
        pdf.set_xy(x + 95, y)
        pdf.write(10, "༤། ལས་ཁུངས་དང་སྡེ་ཚན། ")
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        pdf.write(10, f"{row.get('Department', '')}")
        if pdf.get_x() > start_x:
            pdf.line(start_x, start_y + 8, pdf.get_x(), start_y + 8)
        pdf.ln(10)

        pdf.ln(2)
        start = str(row.get('StartDate', ''))
        end = str(row.get('EndDate', ''))

        def get_tibetan_half(half_val):
            if not half_val or pd.isna(half_val):
                return ""
            if "Morning" in str(half_val):
                return " (ཞོགས་པ།)"
            if "Evening" in str(half_val):
                return " (ཉིན་རྒྱབ།)"
            if "Full Day" in str(half_val):
                return " (ཉིན་ཆ་ཚང་།)"
            return ""

        start_tib = get_tibetan_half(row.get('StartHalf', ''))
        end_tib = get_tibetan_half(row.get('EndHalf', ''))

        if len(start) >= 10 and len(end) >= 10:
            date_str = f"༥། ཕྱི་ལོ་ {start[:4]} ཟླ་ {start[5:7]} ཚེས་ {start[8:10]}{start_tib} ནས་ ཕྱི་ལོ་ {end[:4]} ཟླ་ {end[5:7]} ཚེས་ {end[8:10]}{end_tib} བར།"
        else:
            date_str = f"༥། ཕྱི་ལོ་ {start}{start_tib} ནས་ {end}{end_tib} བར།"
        pdf.cell(0, 10, to_tibetan_numeral(date_str),
                new_x="LMARGIN", new_y="NEXT")

        pdf.write(10, "གུང་གསེང་ཉིན་གྲངས་ཇི་ཞུས། ")
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        pdf.write(10, f"{to_tibetan_numeral(row.get('TotalDays', ''))}")
        if pdf.get_x() > start_x:
            pdf.line(start_x, start_y + 8, pdf.get_x(), start_y + 8)
        pdf.ln(10)

        l_type = str(row.get('LeaveType', '')).upper()

        pdf.set_font("Monlam", "", 10)
        pdf.write(6, "(གུང་གསེང་གི་ངོ་བོ། ")

        pdf.set_font("Monlam", "U" if "AL" in l_type else "", 10)
        pdf.write(6, "ཐོབ་སེང་། ")

        pdf.set_font("Monlam", "U" if "CL" in l_type else "", 10)
        pdf.write(6, "ངེས་མེད། ")

        pdf.set_font("Monlam", "U" if "SL" in l_type else "", 10)
        pdf.write(6, "ནད་དགོངས། ")

        pdf.set_font("Monlam", "U" if "ML" in l_type else "", 10)
        pdf.write(6, "ཕྲུ་གུ་བཙས་སྐབས། ")

        pdf.set_font("Monlam", "U" if "PL" in l_type else "", 10)
        pdf.write(6, "བཟའ་ཟླར་ཕྲུ་གུ་བཙས་སྐབས་རོགས་སྐྱོར། ")

        pdf.set_font("Monlam", "U" if "UL" in l_type else "", 10)
        pdf.write(6, "དམིགས་བསལ་ཕོགས་མེད་གུང་གསེང་། ")

        reason = row.get('Reason', '')
        if "OTHER" in l_type and reason:
            other_text = f"གཞན་ ({reason}) "
        else:
            other_text = "གཞན་ (.......................) "

        pdf.set_font("Monlam", "U" if "OTHER" in l_type else "", 10)
        pdf.write(6, other_text)

        pdf.set_font("Monlam", "", 10)
        pdf.write(6, "བཅས་གང་ཡིན་ལ་འགྲིག་རྟགས་རྒྱག་དགོས།)")
        pdf.ln(8)

        pdf.set_font("Monlam", "", 12)
        pdf.cell(0, 10, "༦། གུང་གསེང་ལྷག་བསྡད་གནས་སྟངས་གཤམ་གསལ།",
                new_x="LMARGIN", new_y="NEXT")

        cl_bal = row.get("CLBalance", "N/A")
        al_bal = row.get("ALBalance", "N/A")
        sl_bal = row.get("SLBalance", "N/A")
        ul_bal = row.get("ULBalance", "N/A")

        if pd.isna(cl_bal): cl_bal = "N/A"
        if pd.isna(al_bal): al_bal = "N/A"
        if pd.isna(sl_bal): sl_bal = "N/A"
        if pd.isna(ul_bal): ul_bal = "N/A"

        pdf.cell(95, 10, f"ཀ། ངེས་མེད། {to_tibetan_numeral(cl_bal)}")
        pdf.cell(
            95, 10, f"ཁ། ཐོབ་སེང་། {to_tibetan_numeral(al_bal)}", new_x="LMARGIN", new_y="NEXT")

        pdf.cell(95, 10, f"ག། ནད་དགོངས། {to_tibetan_numeral(sl_bal)}")
        pdf.cell(
            95, 10, f"ང། དམིགས་བསལ་ཕོགས་མེད། {to_tibetan_numeral(ul_bal)}", new_x="LMARGIN", new_y="NEXT")

        reason_val = str(row.get('Reason', ''))
        if reason_val.lower() == 'nan':
            reason_val = ''
        pdf.cell(
            0, 10, f"ཅ། གཞན། {to_tibetan_numeral(reason_val)}", new_x="LMARGIN", new_y="NEXT")

        # ApplicationDate is stored as the submission timestamp in LeaveRequests (hidden on the printed Leave PDF)
        pdf.ln(10)

        y = pdf.get_y()
        pdf.line(20, y, 95, y)
        pdf.line(115, y, 190, y)

        pdf.set_xy(20, y - 8)
        pdf.cell(75, 10, f"{row.get('Name', '')}", align="C")
        pdf.set_xy(20, y + 2)
        pdf.multi_cell(
            75, 6, "དགོངས་སྙན་འབུལ་མཁན་དོ་བདག་ལས་བྱེད་ཀྱི་ས་རྟགས།", align="C")

        pdf.set_xy(115, y - 8)
        punched_by = str(row.get('PunchedBy', '')).strip()
        if punched_by and punched_by != "nan":
            pdf.cell(75, 10, punched_by, align="C")
        pdf.set_xy(115, y + 2)
        pdf.multi_cell(75, 6, "རྩིས་པས་ཞིབ་འཇུག་ཟིན་པའི་དག་མཆན།", align="C")

        pdf.ln(20)
        y = pdf.get_y()
        pdf.line(20, y, 95, y)
        pdf.line(115, y, 190, y)

        pdf.set_xy(20, y - 8)
        supported_by = str(row.get('SupportedBy', '')).strip()
        if supported_by and supported_by != "nan":
            pdf.cell(75, 10, supported_by, align="C")
        pdf.set_xy(20, y + 2)
        pdf.multi_cell(
            75, 6, "བཀའ་འཁྲོལ་བརྒྱབ་གཉེར།\n(འབྲེལ་ཡོད་ཟུང་དྲུང་།)", align="C")

        pdf.set_xy(115, y - 8)
        approved_by = str(row.get('ApprovedBy', '')).strip()
        if approved_by and approved_by != "nan":
            pdf.cell(75, 10, approved_by, align="C")
        pdf.set_xy(115, y + 2)
        pdf.multi_cell(
            75, 6, "བཀའ་འཁྲོལ་ཆོག་མཆན།\nའགན་འཛིན།/དྲུང་ཆེ།", align="C")

        pdf.ln(15)
        pdf.set_font("Monlam", "", 10)
        pdf.cell(
            0, 7,
            "༼འདི་ནི་གྲངས་འཛིན་མཚན་རྟགས་བཀོད་པའི་ཡིག་ཆ་ཞིག་ཡིན་པས། དངོས་སུ་མཚན་རྟགས་རྒྱག་དགོས་མེད།༽",
            new_x="LMARGIN", new_y="NEXT", align="C",
        )
        pdf.cell(
            0, 7,
            "སྔ་ལོའི་ཐོབ་གསེང་ཡོད་ཚེ་སྔ་ལོ་ནས་ཞུ་རྒྱུ་དང་། དེ་མིན་འདི་ལོ་ནས་ཞུ་རྒྱུ།",
            new_x="LMARGIN", new_y="NEXT", align="C",
        )

        return bytes(pdf.output())
    except Exception as e:
        import streamlit as st
        st.error(f"Failed to generate PDF: {e}")
        return b""



@st.cache_data(show_spinner=False, max_entries=100)
def generate_tour_sanction_pdf(row):
    try:
        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.set_text_shaping(True)
        pdf.add_font("Monlam", style="", fname="Monlam_Uni_OuChan2.ttf")
        pdf.add_page()

        # Title
        pdf.set_font("Monlam", "", 18)
        pdf.cell(0, 12, "ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(6)

        # Salutation (dynamically tailored based on Branch vs HQ)
        pdf.set_font("Monlam", "", 13)
        applicant_branch = str(row.get('Branch', 'HQ')).strip()
        applicant_role = str(row.get('Role', 'Employee')).strip()

        if applicant_branch not in ["Head Office", "HQ"] and applicant_role != "BranchManager":
            salutation = "༄༅། །མཚུངས་མེད་ཡན་ལག་འགན་འཛིན་མཆོག་ལ།"
        else:
            salutation = "༄༅། །མཚུངས་མེད་སྤྱི་ཁྱབ་རྩིས་ཞིབ་འགན་འཛིན།/དྲུང་ཆེ་མཆོག་ལ།"

        pdf.cell(0, 10, salutation, new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(2)

        # Body Opening Text
        app_date = str(row.get('ApplicationDate', get_cta_today().strftime('%Y-%m-%d')))
        formatted_date = to_tibetan_numeral(format_tibetan_date(app_date))
        applicant_name = str(row.get('Name', ''))

        pdf.set_font("Monlam", "", 12)

        # Line 2: Indented ཆེད་ཞུ། གུས་(མིང་།) [Name] (matching official CTA placement)
        pdf.set_x(100)
        pdf.cell(0, 8.5, f"ཆེད་ཞུ། གུས་(མིང་།) {applicant_name}", new_x="LMARGIN", new_y="NEXT", align="L")

        # Line 3: Main petition sentence
        pdf.cell(0, 8.5, "གཤམ་གསལ་ལྟར་གཞུང་དོན་ཕྱོགས་བསྐྱོད་བྱ་འཐུས་བཀའ་འཁྲོལ་གནང་སྐྱོང་ཡོང་བ་ཞུ།", new_x="LMARGIN", new_y="NEXT", align="L")

        # Line 4: Date closing
        pdf.cell(0, 8.5, f"གོང་མིང་པས་{formatted_date} ལ་ཕུལ།།", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(5)

        # Parse Travel Legs if present
        raw_legs = row.get('TravelLegs', '')
        travel_legs = []
        if isinstance(raw_legs, list):
            travel_legs = raw_legs
        elif isinstance(raw_legs, str) and raw_legs.strip().startswith('['):
            try:
                travel_legs = json.loads(raw_legs)
            except Exception:
                travel_legs = []

        purpose = str(row.get('Purpose', 'གཞུང་དོན་ལས་རིམ།'))
        destination = str(row.get('Destination', ''))
        dep_date = to_tibetan_numeral(str(row.get('DepartureDate', '')))
        arr_date = to_tibetan_numeral(str(row.get('ArrivalDate', '')))

        # Helper to wrap Tibetan text cleanly at syllable / tsek boundaries without word breaking
        def format_tibetan_for_cell(text, max_width_mm):
            if not text:
                return ""
            if pdf.get_string_width(text) <= max_width_mm:
                return text
            import re
            tokens = re.findall(r'[^་།\s]+[་།\s]*|[་།\s]+', text)
            lines = []
            curr = ""
            for tok in tokens:
                test = curr + tok
                if pdf.get_string_width(test) <= max_width_mm:
                    curr = test
                else:
                    if curr:
                        lines.append(curr.rstrip())
                        curr = tok.lstrip()
                    else:
                        lines.append(tok)
                        curr = ""
            if curr:
                lines.append(curr.rstrip())
            return "\n".join(lines)

        from fpdf.fonts import FontFace
        headings_style = FontFace(family="Monlam", size_pt=12)
        pdf.set_font("Monlam", "", 11)

        # 4-Column Auto-Wrapping Table
        with pdf.table(
            col_widths=(75, 45, 35, 35),
            text_align=("CENTER", "CENTER", "CENTER", "CENTER"),
            headings_style=headings_style,
            line_height=7,
            padding=2
        ) as table:
            # Header Row
            h_row = table.row()
            for h in ['ལས་དོན་འགྲེལ་བརྗོད།', 'ས་གནས།', 'ཐོན་ཚེས།', 'འབྱོར་ཚེས།']:
                h_row.cell(h)

            if travel_legs and len(travel_legs) > 0:
                for leg in travel_legs:
                    l_purp = str(leg.get('purpose', purpose) or purpose)
                    l_dest = str(leg.get('destination', ''))
                    l_dep = to_tibetan_numeral(str(leg.get('departure_date', '')))
                    l_arr = to_tibetan_numeral(str(leg.get('arrival_date', '')))

                    r = table.row()
                    r.cell(format_tibetan_for_cell(l_purp, 69))
                    r.cell(format_tibetan_for_cell(l_dest, 40))
                    r.cell(l_dep)
                    r.cell(l_arr)
            else:
                r = table.row()
                r.cell(format_tibetan_for_cell(purpose, 69))
                r.cell(format_tibetan_for_cell(destination, 40))
                r.cell(dep_date)
                r.cell(arr_date)

        pdf.ln(18)

        # Signatures
        pdf.set_font("Monlam", "", 12)
        status = str(row.get('Status', '')).strip()
        supp_by = str(row.get('SupportedBy', '')).strip()
        sel_co = str(row.get('SelectedCoAdmin', '')).strip()
        appr_by = str(row.get('ApprovedBy', '')).strip()

        applicant_branch_code = normalize_branch_code(applicant_branch) if "normalize_branch_code" in globals() else applicant_branch
        applicant_role_norm = str(row.get('Role', 'Employee')).strip().lower()
        is_branch_subordinate = (
            applicant_branch_code in ["SB", "NB", "DB"] and 
            applicant_role_norm not in ["branch manager", "branchmanager", "admin", "co-admin", "coadmin"]
        )

        if is_branch_subordinate:
            # --- BRANCH EMPLOYEE & ACCOUNT 2x2 SIGNATURE FORMAT (Co-Admin + User name / BM + Admin) ---
            # Row 1: Selected Co-Admin (Left) & User name (Right)
            y = pdf.get_y()
            pdf.line(20, y, 85, y)
            pdf.line(125, y, 190, y)

            # Top-Left: Selected Co-admin (ONLY display signature if supported or if Not-Applicable)
            if sel_co == "Not-Applicable" or supp_by == "N/A (Direct Application)":
                pdf.set_xy(20, y - 7)
                pdf.cell(65, 6, "N/A (Direct Application)", align="C")
            elif status != "DRAFT PREVIEW" and supp_by and supp_by not in ['nan', 'None', '']:
                bm_check = str(row.get('BranchManager', '')).strip()
                if not (bm_check and supp_by.lower() == bm_check.lower()):
                    pdf.set_xy(20, y - 7)
                    pdf.cell(65, 6, supp_by, align="C")
            pdf.set_xy(20, y + 2)
            pdf.multi_cell(65, 6, "སྡེ་ཚན་ཟུང་དྲུང་གི་རྒྱབ་གཉེར།", align="C")

            # Top-Right: User name (Applicant)
            pdf.set_xy(125, y - 7)
            if applicant_name:
                pdf.cell(65, 6, applicant_name, align="C")
            pdf.set_xy(125, y + 2)
            pdf.multi_cell(65, 6, "འབྲེལ་ཡོད་ལས་བྱེད་པའི་མིང་རྟགས།", align="C")

            # Row 2: Branch Manager (Left, under Selected Co-admin) & Selected Admin (Right, under User name)
            pdf.ln(24)
            y2 = pdf.get_y()
            pdf.line(20, y2, 85, y2)
            pdf.line(125, y2, 190, y2)

            # Bottom-Left: Branch Manager (ONLY display signature if Branch Manager actually supported or endorsed it)
            bm_name = str(row.get('BranchManager', '')).strip()
            if not bm_name or bm_name in ['nan', 'None']:
                bm_found, _ = get_branch_manager(applicant_branch_code) if "get_branch_manager" in globals() else ("", "")
                bm_name = bm_found or ""
            bm_supp = str(row.get('BMSupportedBy', '')).strip()

            if status != "DRAFT PREVIEW":
                if bm_supp and bm_supp not in ['nan', 'None', '']:
                    pdf.set_xy(20, y2 - 7)
                    pdf.cell(65, 6, bm_supp, align="C")
                elif supp_by and bm_name and (bm_name.lower() == supp_by.lower()):
                    pdf.set_xy(20, y2 - 7)
                    pdf.cell(65, 6, bm_name, align="C")
                elif status in ['Sanctioned', 'Completed', 'Settled'] and str(row.get('SanctionedBy', '')).strip() == bm_name and bm_name:
                    pdf.set_xy(20, y2 - 7)
                    pdf.cell(65, 6, bm_name, align="C")

            pdf.set_xy(20, y2 + 2)
            pdf.multi_cell(65, 6, "ཡན་ལག་འགན་འཛིན་གྱི་རྒྱབ་གཉེར།", align="C")

            # Bottom-Right: Selected Admin
            if status in ['Sanctioned', 'Completed', 'Settled'] and appr_by and appr_by not in ['nan', 'None', '']:
                pdf.set_xy(125, y2 - 7)
                pdf.cell(65, 6, appr_by, align="C")
            pdf.set_xy(125, y2 + 2)
            auth_title = "བཀའ་འཁྲོལ་གནང་མཁན།\nརྩིས་ཞིབ་འགན་འཛིན།/དྲུང་ཆེའི་མཚན་རྟགས།"
            pdf.multi_cell(65, 6, auth_title, align="C")

        else:
            # --- HQ STAFF & STANDARD SIGNATURE FORMAT ---
            # 1. Recommendation (Left) & Staff Signature (Right)
            y = pdf.get_y()
            pdf.line(20, y, 85, y)
            pdf.line(125, y, 190, y)

            # Co-Admin (Left): ONLY display signature if supported or if Not-Applicable!
            if sel_co == "Not-Applicable" or supp_by == "N/A (Direct Application)":
                pdf.set_xy(20, y - 7)
                pdf.cell(65, 6, "N/A (Direct Application)", align="C")
            elif status in ['Pending Admin Sanction', 'Sanctioned', 'Completed', 'Settled'] and supp_by and supp_by not in ['nan', 'None']:
                pdf.set_xy(20, y - 7)
                pdf.cell(65, 6, supp_by, align="C")
            pdf.set_xy(20, y + 2)
            pdf.multi_cell(65, 6, "འབྲེལ་ཡོད་ཟུང་དྲུང་གྱི་རྒྱབ་གཉེར།", align="C")

            # Staff / Applicant (Right): ALWAYS display applicant signature
            pdf.set_xy(125, y - 7)
            if applicant_name:
                pdf.cell(65, 6, applicant_name, align="C")
            pdf.set_xy(125, y + 2)
            pdf.multi_cell(65, 6, "འབྲེལ་ཡོད་ལས་བྱེད་པའི་མིང་རྟགས།", align="C")

            # 2. Sanctioning Authority (Bottom Center): ONLY display signature if approved/sanctioned!
            pdf.ln(24)
            y2 = pdf.get_y()
            pdf.line(72, y2, 138, y2)

            if status in ['Sanctioned', 'Completed', 'Settled'] and appr_by and appr_by not in ['nan', 'None']:
                pdf.set_xy(72, y2 - 7)
                pdf.cell(66, 6, appr_by, align="C")
            pdf.set_xy(72, y2 + 2)
            
            if applicant_branch not in ["Head Office", "HQ"] and applicant_role != "BranchManager":
                auth_title = "བཀའ་འཁྲོལ་གནང་མཁན།\nཡན་ལག་འགན་འཛིན་གྱི་མཚན་རྟགས།"
            else:
                auth_title = "བཀའ་འཁྲོལ་གནང་མཁན།\nརྩིས་ཞིབ་འགན་འཛིན།/དྲུང་ཆེའི་མཚན་རྟགས།"
                
            pdf.multi_cell(66, 6, auth_title, align="C")

        # Digital signature notice like in leave form
        pdf.ln(14)
        pdf.set_font("Monlam", "", 10)
        pdf.cell(0, 10, "༼འདི་ནི་གྲངས་འཛིན་མཚན་རྟགས་བཀོད་པའི་ཡིག་ཆ་ཞིག་ཡིན་པས། དངོས་སུ་མཚན་རྟགས་རྒྱག་དགོས་མེད།༽", align="C")

        return bytes(pdf.output())
    except Exception as e:
        import streamlit as st
        st.error(f"Failed to generate Tour Sanction PDF: {e}")
        return b""


def preview_pdf(pdf_bytes):
    from streamlit_pdf_viewer import pdf_viewer
    import streamlit as st
    st.markdown('''
        <style>
            /* Force the PDF iframe to be wide enough to trigger parent scrolling */
            div[data-testid="stVerticalBlock"], 
            div[data-testid="stDialog"] div[data-testid="stVerticalBlock"] {
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch;
            }
            iframe[title*="streamlit_pdf_viewer"] {
                min-width: 720px !important;
            }
        </style>
    ''', unsafe_allow_html=True)
    pdf_viewer(input=pdf_bytes, width=700)


@st.dialog("📄 PDF Preview", width="large")
def open_pdf_dialog(pdf_bytes, filename="Leave.pdf"):
    preview_pdf(pdf_bytes)
    st.download_button("⬇️ Download PDF", data=pdf_bytes,
                        file_name=filename, mime="application/pdf")

# --- MATH ENGINE ---


def calculate_balances(user_name, user_row_override=None, df_req_override=None, reference_date=None, rules_override=None):
    import math as _math
    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if callable(_resolve_rules_fn) else (rules_override if isinstance(rules_override, dict) else {})
    _types_cfg = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}
    _global_cfg = _rules.get("global", {}) if isinstance(_rules, dict) else {}
    ul_affects_svc = bool(_types_cfg.get("UL", {}).get("Affects_Service_Year", True))
    service_freeze_active = bool(_global_cfg.get("Service_Freeze_Rule", True))
    extra_svc_types = {
        k for k, v in _types_cfg.items()
        if k != "UL" and isinstance(v, dict) and bool(v.get("Affects_Service_Year", False))
    }

    if user_row_override is not None:
        user_data = user_row_override
    else:
        staff_df = load_staff_master()
        if staff_df is None or staff_df.empty:
            return {
                "CL": 0.0, "SL": 0.0, "AL": 0.0, "UL": 0.0,
                "Actual_Service_Year": 0.0, "Service_Year_Penalty": 0.0, "Calendar_Service_Year": 0.0,
                "Cumulative_UL": 0.0, "Used_UL": 0.0,
                "Cumulative_Effecting_Service": 0.0, "Current_Year_Effecting_Service": 0.0,
                "Active_FY": get_current_financial_year_dates(reference_date)[2],
            }

        user_record = staff_df[staff_df["Name"] == user_name]
        if user_record.empty:
            return {
                "CL": 0.0, "SL": 0.0, "AL": 0.0, "UL": 0.0,
                "Actual_Service_Year": 0.0, "Service_Year_Penalty": 0.0, "Calendar_Service_Year": 0.0,
                "Cumulative_UL": 0.0, "Used_UL": 0.0,
                "Cumulative_Effecting_Service": 0.0, "Current_Year_Effecting_Service": 0.0,
                "Active_FY": get_current_financial_year_dates(reference_date)[2],
            }

        user_data = user_record.iloc[0]

    def safe_float(val):
        if pd.isnull(val):
            return 0.0
        val_str = str(val).strip()
        if val_str == "":
            return 0.0
        try:
            return float(val)
        except Exception:
            return 0.0

    today_d = reference_date or get_cta_today()
    default_fy_start, _, default_fy_label = get_current_financial_year_dates(today_d)
    active_fy_year = parse_fy_start_year(user_data.get("Active_FY"))
    fy_start = date(active_fy_year, 4, 1) if active_fy_year is not None else default_fy_start
    resolved_fy_label = f"FY {active_fy_year}-{active_fy_year + 1}" if active_fy_year is not None else default_fy_label

    cum_eff = safe_float(user_data.get("Cummulative leave effecting Service year.(Education Leave & Etc).", 0))
    cum_ul = safe_float(user_data.get("Cumulative_UL", 0))
    sheet_curr_eff = safe_float(user_data.get("Current Year Leave affecting Service Year", 0))
    sheet_used_ul = safe_float(user_data.get("Used_UL", 0))
    extra_sheet_svc = sum(safe_float(user_data.get(f"Used_{k}", 0)) for k in extra_svc_types)
    sheet_cur_total = max(0.0, sheet_curr_eff + (sheet_used_ul if ul_affects_svc else 0.0) + extra_sheet_svc)
    base_cum_penalty = cum_eff + (cum_ul if ul_affects_svc else 0.0)

    joining_date_str = str(user_data.get("JoiningDate", "")).strip()
    actual_svc_years = safe_float(user_data.get("Actual Service Year", 0))
    penalty_days_elapsed = safe_float(user_data.get("Service Year Penalty", 0))
    total_cal_years = safe_float(user_data.get("Service Year", 0))
    future_committed_ul = 0.0
    unpartitioned_ul_total = 0.0
    cur_fy_ul_total = 0.0

    _tz_cb = globals().get("CTA_TZ")

    def _parse_cb_date(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            if getattr(val, "tzinfo", None) is not None and _tz_cb is not None:
                return val.astimezone(_tz_cb).date()
            return val.date()
        if hasattr(val, "to_pydatetime"):
            try:
                if pd.isnull(val):
                    return None
                ts = val.to_pydatetime()
                if getattr(ts, "tzinfo", None) is not None and _tz_cb is not None:
                    return ts.astimezone(_tz_cb).date()
                return ts.date() if ts is not None else None
            except Exception:
                return None
        if isinstance(val, date):
            return val
        try:
            if pd.isnull(val):
                return None
        except Exception:
            pass
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "nat", "null"):
            return None
        if ("T" in s or s.endswith(("Z", "z"))) and len(s) > 10:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
                if dt.tzinfo is not None and _tz_cb is not None:
                    return dt.astimezone(_tz_cb).date()
                return dt.date()
            except Exception:
                pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None and _tz_cb is not None:
                return dt.astimezone(_tz_cb).date()
            return dt.date()
        except Exception:
            pass
        if len(s) >= 10:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except ValueError:
                    continue
        return None

    has_joining = bool(joining_date_str and joining_date_str not in ["nan", "None", "", "NaT", "NAT"])
    if has_joining or df_req_override is not None:
        try:
            df_req = df_req_override if df_req_override is not None else load_leave_requests()
            req_elapsed_penalty = 0.0
            req_total_penalty = 0.0
            unpartitioned_req_total = 0.0
            if df_req is not None and not df_req.empty:
                target_uname = str(user_name).strip()
                pen_lt_set = {"UL", "Return-UL", "Rev-UL"}
                if extra_svc_types:
                    pen_lt_set |= set(extra_svc_types) | {f"Return-{k}" for k in extra_svc_types} | {f"Rev-{k}" for k in extra_svc_types}
                n_req = len(df_req)
                r_names = df_req["Name"].tolist() if "Name" in df_req.columns else [""] * n_req
                r_stats = df_req["Status"].tolist() if "Status" in df_req.columns else [""] * n_req
                r_lts = df_req["LeaveType"].tolist() if "LeaveType" in df_req.columns else [""] * n_req
                r_effs = df_req["EffectsServiceYear"].tolist() if "EffectsServiceYear" in df_req.columns else [""] * n_req
                pen_idx = [
                    i for i, (nm_v, st_v, lt_v, ef_v) in enumerate(zip(r_names, r_stats, r_lts, r_effs))
                    if str(nm_v).strip() == target_uname
                    and str(st_v).strip() == "Approved"
                    and (str(lt_v).strip() in pen_lt_set or str(ef_v).strip().lower() == "yes")
                ]
                if pen_idx:
                    my_pen_leaves = df_req.iloc[pen_idx]
                    cur_fy_yr = fy_start.year
                    breakdown_func = globals().get("get_leave_fy_breakdown")
                    if breakdown_func is None:
                        def breakdown_func(s_d, e_d, t_d=None):
                            if s_d is None or e_d is None: return {}
                            try:
                                s_d = pd.to_datetime(s_d).date() if not isinstance(s_d, date) else s_d
                                e_d = pd.to_datetime(e_d).date() if not isinstance(e_d, date) else e_d
                            except Exception: return {}
                            if s_d > e_d: return {}
                            tot_cal = (e_d - s_d).days + 1
                            if tot_cal <= 0: return {}
                            s_yr = s_d.year if s_d.month >= 4 else s_d.year - 1
                            e_yr = e_d.year if e_d.month >= 4 else e_d.year - 1
                            from collections import OrderedDict
                            res = OrderedDict()
                            for y in range(s_yr, e_yr + 1):
                                c_s = max(s_d, date(y, 4, 1))
                                c_e = min(e_d, date(y + 1, 3, 31))
                                c_cal = max(0, (c_e - c_s).days + 1)
                                d = float(t_d) * (float(c_cal) / float(tot_cal)) if t_d is not None else float(c_cal)
                                res[y] = {"fy_year": y, "fy_label": f"FY {y}-{y + 1}", "seg_start": c_s, "seg_end": c_e, "days": round(d, 2)}
                            return res

                    for _, l_row in my_pen_leaves.iterrows():
                        try:
                            l_start = _parse_cb_date(l_row.get("StartDate"))
                            l_end = _parse_cb_date(l_row.get("EndDate"))
                            if l_start is None or l_end is None or l_end < fy_start:
                                continue
                            raw_td = l_row.get("TotalDays", 0.0)
                            if raw_td is None or (isinstance(raw_td, float) and not _math.isfinite(raw_td)):
                                continue
                            if pd.isnull(raw_td):
                                continue
                            s_td = str(raw_td).strip()
                            if s_td == "" or s_td.startswith("="):
                                continue
                            l_days = float(s_td)
                            if not _math.isfinite(l_days):
                                continue
                            l_type_str = str(l_row.get("LeaveType", "")).strip()
                            row_is_ul = l_type_str in ("UL", "Return-UL", "Rev-UL")
                            row_is_eff = str(l_row.get("EffectsServiceYear", "")).strip().lower() == "yes"
                            row_base_lt = l_type_str[len("Return-"):] if l_type_str.startswith("Return-") else (l_type_str[len("Rev-"):] if l_type_str.startswith("Rev-") else l_type_str)
                            row_counts_for_svc = (row_is_ul and ul_affects_svc) or row_is_eff or (row_base_lt in extra_svc_types)

                            bdown = breakdown_func(l_start, l_end, l_days)
                            if row_is_ul:
                                for b_yr, b_info in bdown.items():
                                    if b_yr > cur_fy_yr:
                                        future_committed_ul += float(b_info["days"])
                                    elif b_yr == cur_fy_yr:
                                        cur_fy_ul_total += float(b_info["days"])

                            if cur_fy_yr not in bdown:
                                continue
                            cur_and_future_days = sum(float(b_info["days"]) for b_yr, b_info in bdown.items() if b_yr >= cur_fy_yr)
                            if row_is_ul:
                                unpartitioned_ul_total += cur_and_future_days
                            if not row_counts_for_svc:
                                continue
                            unpartitioned_req_total += cur_and_future_days
                            cur_info = bdown[cur_fy_yr]
                            cur_fy_days = float(cur_info["days"])
                            seg_start = cur_info["seg_start"]
                            seg_end = cur_info["seg_end"]

                            req_total_penalty += cur_fy_days
                            if not service_freeze_active:
                                req_elapsed_penalty += cur_fy_days
                            elif today_d < seg_start:
                                continue
                            elif today_d >= seg_end:
                                req_elapsed_penalty += cur_fy_days
                            else:
                                seg_cal = max(1, (seg_end - seg_start).days + 1)
                                elapsed_cal = max(0, (today_d - seg_start).days + 1)
                                scaled_cur = cur_fy_days * (float(elapsed_cal) / float(seg_cal))
                                elapsed_cur = min(cur_fy_days, scaled_cur) if cur_fy_days >= 0 else max(cur_fy_days, scaled_cur)
                                req_elapsed_penalty += elapsed_cur
                        except Exception:
                            continue

            if has_joining:
                j_date = _parse_cb_date(joining_date_str) or pd.to_datetime(joining_date_str).date()
                total_cal_days = max(0, (today_d - j_date).days)
                total_cal_years = round(total_cal_days / 365.25, 2)

                # If sheet_cur_total was stamped with unpartitioned multi-year total from older approvals,
                # cap it to active-FY req_total_penalty so future years' unelapsed leave does not leak into current tenure
                effective_sheet_cur = sheet_cur_total
                if abs(sheet_cur_total - unpartitioned_req_total) < 1e-4 and unpartitioned_req_total > req_total_penalty:
                    effective_sheet_cur = req_total_penalty

                if service_freeze_active:
                    # Reconcile elapsed leave requests with any manual sheet discrepancy edits (+ or -)
                    current_elapsed_penalty = min(req_elapsed_penalty, effective_sheet_cur) + max(0.0, effective_sheet_cur - req_total_penalty)
                else:
                    current_elapsed_penalty = effective_sheet_cur

                penalty_days_elapsed = base_cum_penalty + current_elapsed_penalty
                actual_days = max(0, total_cal_days - penalty_days_elapsed)
                actual_svc_years = round(actual_days / 365.25, 2)
        except Exception:
            pass

    raw_sheet_ul_bal = safe_float(user_data.get("Balance_UL", 0))
    # If sheet_used_ul only reflects active-FY UL (Approach B), subtract future-FY committed UL so available UL is accurate
    if future_committed_ul > 0 and not (abs(sheet_used_ul - unpartitioned_ul_total) < 1e-4 and unpartitioned_ul_total > cur_fy_ul_total):
        effective_ul_bal = max(0.0, raw_sheet_ul_bal - max(0.0, future_committed_ul))
    else:
        effective_ul_bal = raw_sheet_ul_bal

    return {
        "CL": safe_float(user_data.get("Balance_CL", 0)),
        "SL": safe_float(user_data.get("Balance_SL", 0)),
        "AL": safe_float(user_data.get("Balance_AL", 0)),
        "UL": round(effective_ul_bal, 2),
        "FY_Closing_UL": raw_sheet_ul_bal,
        "Future_Committed_UL": round(max(0.0, future_committed_ul), 2),
        "JoiningDate": user_data.get("JoiningDate"),
        "Service_Year_Penalty": penalty_days_elapsed,
        "Actual_Service_Year": actual_svc_years,
        "Calendar_Service_Year": total_cal_years,
        "Cumulative_UL": cum_ul,
        "Used_UL": sheet_used_ul,
        "Cumulative_Effecting_Service": cum_eff,
        "Current_Year_Effecting_Service": sheet_curr_eff,
        "Active_FY": resolved_fy_label,
    }

# --- AUTHENTICATION & LOGIN UI ---


def is_google_oauth_configured():
    """Checks if Google OAuth credentials are fully provided in secrets.toml."""
    try:
        auth_conf = st.secrets.get("auth", {})
        cid = str(auth_conf.get("client_id", "")).strip()
        csec = str(auth_conf.get("client_secret", "")).strip()
        return bool(cid and csec and not cid.startswith("YOUR_") and not csec.startswith("YOUR_"))
    except Exception:
        return False


_DEV_BRANCH_SHORT_LABELS = {
    "HQ": "Central",
    "SB": "South",
    "NB": "Nepal",
    "DB": "Dekyiling",
}
_DEV_ROLE_SHORT_LABELS = {
    "Admin": "Admin",
    "Co-Admin": "Co-Admin",
    "CoAdmin": "Co-Admin",
    "Branch Manager": "BM",
    "Accounts": "Account",
    "Auditor": "Auditor",
    "Employee": "Employee",
}
_DEV_BRANCH_ORDER = ("HQ", "SB", "NB", "DB")
_DEV_ROLE_ORDER = ("Admin", "Co-Admin", "CoAdmin", "Branch Manager", "Accounts", "Auditor", "Employee")


def build_dev_quick_role_shortcuts(users_df):
    """Build ordered (label, branch, role, user_name, user_email) shortcuts covering every (Branch, Role) in users_df."""
    if users_df is None or users_df.empty:
        return []

    seen = {}
    for _, row in users_df.iterrows():
        raw_br = "" if pd.isna(row.get("Branch")) else str(row.get("Branch")).strip()
        br = raw_br if raw_br in ("HQ", "SB", "NB", "DB") else (raw_br or "HQ")
        raw_rl = "" if pd.isna(row.get("Role")) else str(row.get("Role")).strip()
        rl = raw_rl or "Employee"
        key = (br, rl)
        br_label = _DEV_BRANCH_SHORT_LABELS.get(br, br)
        rl_label = _DEV_ROLE_SHORT_LABELS.get(rl, rl)
        raw_nm = "" if pd.isna(row.get("Name")) else str(row.get("Name")).strip()
        raw_em = "" if pd.isna(row.get("Email")) else str(row.get("Email")).strip()
        if key not in seen:
            seen[key] = (f"{br_label} {rl_label}", br, rl, raw_nm, raw_em)
        elif br in ("SB", "NB", "DB") and rl == "Employee" and (br, "Employee#2") not in seen:
            seen[(br, "Employee#2")] = (f"{br_label} {rl_label} 2", br, rl, raw_nm, raw_em)

    def _sort_key(item):
        br, rl_key = item[0]
        base_rl = rl_key.split("#")[0]
        sub_idx = 1 if "#2" in rl_key else 0
        b_idx = _DEV_BRANCH_ORDER.index(br) if br in _DEV_BRANCH_ORDER else len(_DEV_BRANCH_ORDER)
        r_idx = _DEV_ROLE_ORDER.index(base_rl) if base_rl in _DEV_ROLE_ORDER else len(_DEV_ROLE_ORDER)
        return (b_idx, br, r_idx, sub_idx, rl_key)

    return [entry for _, entry in sorted(seen.items(), key=_sort_key)]


def render_dev_quick_role_buttons(users_df, key_prefix, group_by_branch=True, cols_per_row=4):
    """Render quick role shortcut buttons for every branch/role combination in DEV_MODE."""
    shortcuts = build_dev_quick_role_shortcuts(users_df)
    if not shortcuts:
        return

    if group_by_branch:
        batches = []
        current_branch = None
        branch_items = []
        for entry in shortcuts:
            if entry[1] != current_branch:
                if branch_items:
                    for i in range(0, len(branch_items), cols_per_row):
                        batches.append(branch_items[i : i + cols_per_row])
                current_branch = entry[1]
                branch_items = [entry]
            else:
                branch_items.append(entry)
        if branch_items:
            for i in range(0, len(branch_items), cols_per_row):
                batches.append(branch_items[i : i + cols_per_row])
    else:
        batches = [shortcuts[i : i + cols_per_row] for i in range(0, len(shortcuts), cols_per_row)]

    for batch in batches:
        cols = st.columns(cols_per_row)
        for col_idx, (label, br, rl, u_name, u_email) in enumerate(batch):
            btn_key_suffix = f"{br}_{rl}_2" if str(label).endswith(" 2") else f"{br}_{rl}"
            with cols[col_idx]:
                if st.button(
                    label,
                    use_container_width=True,
                    key=f"{key_prefix}_{btn_key_suffix}",
                    help=f"Login as {u_name} ({br} • {rl})",
                ):
                    st.session_state.logged_in = True
                    st.session_state.user_name = u_name
                    st.session_state.user_role = rl
                    st.session_state.user_email = u_email
                    st.rerun()


def login_screen():
    users_df = load_users_data()
    if users_df is None or users_df.empty:
        st.error("⚠️ Database connection error: Unable to load staff database.")
        return

    # --- EASY DEV LOGIN (DEVELOPMENT PHASE ONLY - REMOVE BEFORE FINAL DEPLOY) ---
    if DEV_MODE:
        st.markdown("""
        <div style="background: linear-gradient(90deg, #fef3c7 0%, #fffbeb 100%); border: 2px dashed #f59e0b; border-radius: 10px; padding: 14px 18px; margin-bottom: 22px;">
            <div style="font-weight: 700; color: #92400e; font-size: 16px;">
                🛠️ DEVELOPMENT MODE ACTIVE — EASY 1-CLICK LOGIN
            </div>
            <div style="font-size: 13px; color: #78350f; margin-top: 2px;">
                Bypass Google OAuth and PIN for rapid testing across all branches and roles. (Will be removed in final deploy).
            </div>
        </div>
        """, unsafe_allow_html=True)

        c_dev1, c_dev2 = st.columns([3.2, 1.2])
        with c_dev1:
            dev_user_options = []
            for _, u_r in users_df.iterrows():
                u_nm = u_r["Name"]
                u_rl = u_r.get("Role", "Employee")
                u_br = u_r.get("Branch", "HQ")
                dev_user_options.append((f"{u_nm} ({u_br} • {u_rl})", u_nm, u_rl, u_r.get("Email", "")))
            
            chosen_dev_user = st.selectbox(
                "⚡ Select Account to Login",
                dev_user_options,
                format_func=lambda x: x[0],
                key="dev_login_select",
                label_visibility="collapsed"
            )
        with c_dev2:
            if st.button("🚀 Instant Login", type="primary", use_container_width=True, key="btn_dev_instant_login"):
                st.session_state.logged_in = True
                st.session_state.user_name = chosen_dev_user[1]
                st.session_state.user_role = chosen_dev_user[2]
                st.session_state.user_email = chosen_dev_user[3]
                st.rerun()

        st.caption("⚡ **Quick Role Shortcuts (All Branches & Roles):**")
        render_dev_quick_role_buttons(users_df, key_prefix="quick_btn", group_by_branch=True, cols_per_row=4)

        st.write("---")

    oauth_active = is_google_oauth_configured()
    verified_email = None

    # --- TIER 1: GOOGLE / GMAIL PERIMETER CHECK ---
    # 1. Check if this browser already has a remembered verified device cookie
    remembered_email = cookie_manager.get("cta_verified_gmail")
    if not st.session_state.get("perimeter_verified_email") and remembered_email:
        matched_rem = users_df[users_df["Email"].str.strip().str.lower() == str(remembered_email).strip().lower()]
        if not matched_rem.empty:
            st.session_state["perimeter_verified_email"] = str(remembered_email).strip().lower()

    # 2. Check if user authenticated via native Google OAuth
    if oauth_active and getattr(st.user, "is_logged_in", False):
        oauth_email = str(st.user.email).strip().lower()
        st.session_state["perimeter_verified_email"] = oauth_email
        if str(remembered_email or "").strip().lower() != oauth_email:
            st.session_state["pending_gmail_cookie"] = oauth_email

    # 3. Determine if perimeter is verified
    if "perimeter_verified_email" in st.session_state and st.session_state["perimeter_verified_email"]:
        verified_email = st.session_state["perimeter_verified_email"]
    else:
        # User is not yet verified on this browser: show official Google sign-in
        st.markdown("""
        <div style="text-align: center; margin-top: 20px; margin-bottom: 25px;">
            <h1 style="font-size: 26px; margin-bottom: 4px; color: #1e3a8a;">བོད་མིའི་སྒྲིག་འཛུགས། རྩིས་ཞིབ་ལས་ཁུངས།</h1>
            <h3 style="font-size: 18px; margin-top: 0; color: #475569;">Office of the Auditor General — Central Tibetan Administration</h3>
            <h4 style="font-size: 15px; margin-top: 0; color: #64748b;">Staff Leave & Official Tour Sanction Management Portal</h4>
            <div style="display: inline-block; background-color: #fee2e2; color: #991b1b; padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 13px; margin-top: 10px; border: 1px solid #fecaca;">
                🔒 Secured Perimeter: Authorized CTA Gmail Access Only
            </div>
        </div>
        """, unsafe_allow_html=True)

        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            if oauth_active:
                st.info("👋 Welcome! Please authenticate with your authorized Google account to enter.")
                if st.button("🔴 Sign in with Google", type="primary", use_container_width=True):
                    st.login()
                st.caption("🛡️ Once signed in, this browser will automatically remember your device.")

                with st.expander("🔑 Alternative: Verify via registered Gmail directly", expanded=False):
                    with st.form("alt_gmail_form"):
                        alt_email = st.text_input("Enter registered Gmail", placeholder="e.g. tendar@gmail.com")
                        alt_rem = st.checkbox("Remember this browser (60 days)", value=True, key="alt_rem_cb")
                        if st.form_submit_button("Verify & Continue ➔", use_container_width=True):
                            inp = str(alt_email).strip().lower()
                            matched = users_df[users_df["Email"].str.strip().str.lower() == inp]
                            if matched.empty:
                                st.error(f"⛔ ACCESS DENIED: '{inp}' is not on the authorized OAG staff roster.")
                            else:
                                st.session_state["perimeter_verified_email"] = inp
                                if alt_rem:
                                    st.session_state["pending_gmail_cookie"] = inp
                                st.rerun()
            else:
                with st.form("gmail_perimeter_form"):
                    st.markdown("##### ✉️ Staff Gmail Verification")
                    st.caption("Enter your registered Gmail address once. This browser will remember you for 60 days.")
                    entered_email = st.text_input("Your Gmail Address", placeholder="e.g. tendar@gmail.com")
                    remember_device = st.checkbox("Remember this browser / device (don't ask for Gmail again)", value=True)
                    verify_btn = st.form_submit_button("Verify & Unlock Device ➔", type="primary", use_container_width=True)

                    if verify_btn:
                        input_email = str(entered_email).strip().lower()
                        if not input_email or "@" not in input_email:
                            st.error("Please enter a valid Gmail address.")
                        else:
                            matched = users_df[users_df["Email"].str.strip().str.lower() == input_email]
                            if matched.empty:
                                st.error(f"⛔ ACCESS DENIED: Account '{input_email}' is not on the authorized OAG staff roster. Outsiders are strictly blocked.")
                            else:
                                st.session_state["perimeter_verified_email"] = input_email
                                if remember_device:
                                    st.session_state["pending_gmail_cookie"] = input_email
                                st.success("Google account verified! Unlocking staff profile...")
                                st.rerun()
        return

    # --- TIER 1.5: WHITELIST AUTHORIZATION CHECK ---
    matched_records = users_df[users_df["Email"].str.strip().str.lower() == verified_email]
    if matched_records.empty:
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            st.error(f"🛑 ACCESS DENIED: Account '{verified_email}' is not recognized as an authorized OAG staff member.")
            st.warning("⚠️ **Security Notice:** Access is restricted solely to verified personnel of the Central Tibetan Administration. Outsiders are strictly prevented from viewing personnel records, leave profiles, or audit data.")
            st.info("If you are an active staff member, please contact the System Administrator to register your official Gmail address.")
            if oauth_active:
                if st.button("🚪 Sign out of Google Account", type="primary"):
                    st.logout()
            else:
                if st.button("🔄 Try another Gmail address", type="secondary"):
                    if "perimeter_verified_email" in st.session_state:
                        del st.session_state["perimeter_verified_email"]
                    st.rerun()
        return

    # Authorized Staff Recognized!
    user_record = matched_records.iloc[0]
    staff_name = user_record["Name"]
    staff_role = str(user_record["Role"]).strip()
    staff_post = str(user_record.get("Post", "")).strip()
    staff_branch = str(user_record.get("Branch", "HQ")).strip()
    stored_pin = str(user_record["PIN"]).split('.')[0].zfill(4)

    # --- TIER 2: FAST 4-DIGIT PASSCODE UNLOCK ---
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown(f"""
        <div style="background: linear-gradient(135deg, #1e3a8a 0%, #0f172a 100%); color: white; padding: 20px; border-radius: 12px; margin-bottom: 20px; border-left: 6px solid #f59e0b; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
            <div style="font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #93c5fd; font-weight: 600;">VERIFIED OAG STAFF IDENTITY</div>
            <h2 style="margin: 6px 0; color: white; font-size: 22px;">👋 {staff_name}</h2>
            <div style="font-size: 14px; opacity: 0.9; margin-top: 4px;">
                <strong>Role:</strong> {staff_role} &nbsp;•&nbsp; <strong>Post:</strong> {staff_post} &nbsp;•&nbsp; <strong>Branch:</strong> {staff_branch}
            </div>
            <div style="font-size: 12px; color: #86efac; margin-top: 8px;">
                🔒 Verified Device: <code>{verified_email}</code> &nbsp;•&nbsp; <span style="background: rgba(34,197,94,0.25); padding: 2px 8px; border-radius: 4px; font-weight: 500;">Browser Remembered (60 Days) ✅</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.form("pin_unlock_form"):
            st.markdown("##### 🔑 Enter 4-Digit Passcode")
            entered_pin = st.text_input("4-Digit Passcode", type="password", max_chars=4, placeholder="••••", label_visibility="collapsed")
            remember_me = st.checkbox("Keep me signed in on this device", value=True)
            unlock_submitted = st.form_submit_button("🔓 Unlock Dashboard", type="primary", use_container_width=True)

            if unlock_submitted:
                failed_key = f"pin_fails_{verified_email}"
                fails = st.session_state.get(failed_key, 0)
                if fails >= 5:
                    st.error("🔒 Account locked due to 5 failed passcode attempts. Please contact Administrator to verify identity.")
                elif str(entered_pin).strip() == stored_pin:
                    st.session_state.logged_in = True
                    st.session_state.user_name = staff_name
                    st.session_state.user_role = staff_role
                    st.session_state.user_email = verified_email
                    st.session_state[failed_key] = 0
                    if remember_me:
                        st.session_state.pending_cookie = f"{staff_name}|{entered_pin}"
                        st.session_state["pending_gmail_cookie"] = verified_email
                    st.success("Passcode verified! Access granted.")
                    st.rerun()
                else:
                    st.session_state[failed_key] = fails + 1
                    remaining = 5 - (fails + 1)
                    st.error(f"❌ Incorrect 4-digit passcode. {remaining} attempt(s) remaining.")

        col_b1, col_b2 = st.columns([1, 1])
        with col_b1:
            if st.button("🚪 Switch Account / Use another Gmail", help="Forget this browser and sign in with a different Gmail"):
                st.session_state.pending_cookie_delete = True
                st.session_state.pending_full_signout = True
                if "perimeter_verified_email" in st.session_state:
                    del st.session_state["perimeter_verified_email"]
                if oauth_active:
                    st.logout()
                else:
                    st.rerun()


# --- DASHBOARDS ---
SUBMISSION_FREEZE_MESSAGES = {
    "leave_request": (
        "Submitting Leave Request...",
        "Please wait while your leave request is being submitted and routed. Do not click again or refresh this page.",
    ),
    "leave_reversal": (
        "Submitting Leave Reversal...",
        "Please wait while your leave reversal request is being submitted and routed. Do not click again or refresh this page.",
    ),
    "tour_request": (
        "Submitting Tour Request...",
        "Please wait while your official tour sanction application is being submitted. Do not click again or refresh this page.",
    ),
    "leave_cancellation": (
        "Cancelling Leave Request...",
        "Please wait while your leave request is being cancelled. Do not click again or refresh this page.",
    ),
    "tour_cancellation": (
        "Cancelling Tour Request...",
        "Please wait while your official tour sanction application is being cancelled. Do not click again or refresh this page.",
    ),
}


def build_submission_freeze_overlay_html(submit_kind="leave_request"):
    """Returns full-viewport HTML/CSS overlay markup to freeze UI during leave, reversal, or tour submission."""
    title, subtitle = SUBMISSION_FREEZE_MESSAGES.get(
        str(submit_kind or "").strip(),
        (
            "Submitting Request...",
            "Please wait while your request is being submitted. Do not click again or refresh this page.",
        ),
    )
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle)
    return (
        "<style>"
        "@keyframes ctaSubSpin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }"
        ".cta-freeze-backdrop { position: fixed; inset: 0; width: 100vw; height: 100vh; "
        "background: rgba(15, 23, 42, 0.72); backdrop-filter: blur(5px); -webkit-backdrop-filter: blur(5px); "
        "z-index: 2147483647; display: flex; align-items: center; justify-content: center; "
        "pointer-events: all; cursor: wait; padding: 16px; box-sizing: border-box; }"
        ".cta-freeze-card { background: #ffffff; color: #0f172a; border-radius: 16px; "
        "padding: 32px 28px; max-width: 440px; width: 100%; text-align: center; "
        "box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35); border: 1px solid #e2e8f0; }"
        ".cta-freeze-spinner { width: 48px; height: 48px; margin: 0 auto 18px auto; "
        "border: 5px solid #e2e8f0; border-top: 5px solid #dc2626; border-radius: 50%; "
        "animation: ctaSubSpin 0.85s linear infinite; }"
        ".cta-freeze-title { font-size: 1.25rem; font-weight: 700; margin: 0 0 10px 0; color: #0f172a; }"
        ".cta-freeze-msg { font-size: 0.96rem; line-height: 1.5; margin: 0; color: #475569; }"
        "</style>"
        '<div class="cta-freeze-backdrop" aria-live="assertive" role="alertdialog" aria-modal="true">'
        '<div class="cta-freeze-card">'
        '<div class="cta-freeze-spinner"></div>'
        f'<div class="cta-freeze-title">{safe_title}</div>'
        f'<div class="cta-freeze-msg">{safe_subtitle}</div>'
        '</div>'
        '</div>'
    )


def should_append_idempotent_row(existing_df, row_id, id_col="ID"):
    """Returns True only when row_id is absent from existing_df to guard against duplicate submissions."""
    if existing_df is None or existing_df.empty or id_col not in existing_df.columns:
        return True
    clean_id = str(row_id or "").strip()
    if not clean_id:
        return True
    existing_ids = set(existing_df[id_col].dropna().astype(str).str.strip().tolist())
    return clean_id not in existing_ids


def _make_submission_token(submit_kind):
    if submit_kind in ("tour_request", "tour_cancellation"):
        now_dt = get_cta_now() if "get_cta_now" in globals() else datetime.now()
        return f"TR-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
    return str(uuid.uuid4())[:8].upper()


def _arm_submission_freeze(submit_kind, target_id=None):
    """Locks submission UI on first click before rerun starts; ignores duplicate clicks while busy."""
    if st.session_state.get("_submit_busy", False):
        return
    st.session_state["_submit_busy"] = True
    st.session_state["_submit_pending_kind"] = submit_kind
    st.session_state["_submit_request_id"] = str(target_id).strip() if target_id else _make_submission_token(submit_kind)


def _release_submission_freeze():
    """Releases submission freeze state after completion, validation error, or failure."""
    st.session_state["_submit_busy"] = False
    st.session_state["_submit_pending_kind"] = None
    st.session_state["_submit_request_id"] = None


def employee_dashboard(hide_title=False):
    freeze_placeholder = st.empty()
    is_submit_busy = bool(st.session_state.get("_submit_busy", False))
    if is_submit_busy:
        freeze_placeholder.markdown(
            build_submission_freeze_overlay_html(st.session_state.get("_submit_pending_kind", "leave_request")),
            unsafe_allow_html=True,
        )

    if not hide_title:
        render_active_leaves_banner(show_type=False)
        st.title("Employee Dashboard")

    emp_tab_options = ["🍃 Leave Management (དགོངས་ཞུ།)", "✈️ Official Tour Sanctions (གཞུང་དོན་ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ།)"]
    if "emp_tabs" not in st.session_state or st.session_state.emp_tabs not in emp_tab_options:
        st.session_state.emp_tabs = emp_tab_options[0]

    tab_leave, tab_tour = st.tabs(emp_tab_options, key="emp_tabs", on_change="rerun")

    with tab_leave:
        balances = calculate_balances(st.session_state.user_name)
        st.subheader("Your Leave Balances")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Casual Leave (CL)", balances["CL"])
        col2.metric("Sick Leave (SL)", balances["SL"])
        col3.metric("Annual Leave (AL)", balances["AL"])
        col4.metric("Unpaid Leave (UL)", balances["UL"])

        st.divider()

        # Live Actual Service Year Calculation
        st.subheader("Your Leave Profile")
        try:
            actual_service_year = balances.get("Actual_Service_Year", 0.0)

            if actual_service_year < 0:
                st.info("Service hasn't started yet based on penalties.")
            else:
                total_service_days = actual_service_year * 365.25
                years = int(total_service_days // 365.25)
                remaining_days = total_service_days - (years * 365.25)
                months = int(remaining_days // 30.41666667)
                days = int(remaining_days - (months * 30.41666667))

                st.metric("Actual Service Tenure",
                            f"{years} Years, {months} Months, {days} Days")

                penalty_days = balances.get("Service_Year_Penalty", 0)
                joining_date = balances.get("JoiningDate", "N/A")
                cum_ul_disp = balances.get("Cumulative_UL", 0.0)
                used_ul_disp = balances.get("Used_UL", 0.0)
                active_fy_disp = format_fy_short(balances.get("Active_FY", ""))
                st.caption(
                    f"Joining Date: {joining_date} | Service Penalties Applied: {penalty_days} days | "
                    f"Cumulative UL (Prior FYs): {cum_ul_disp:g} days | Used UL ({active_fy_disp}): {used_ul_disp:g} days"
                )
        except Exception as e:
            st.write("Service Year: N/A")

        st.divider()

        co_admins = []
        admins = []
        users_df = load_users_data()
        if users_df is not None and not users_df.empty:
            co_admins = users_df[users_df["Role"].isin(["Co-Admin", "CoAdmin"])]["Name"].dropna().tolist()
            admins = users_df[users_df["Role"] == "Admin"]["Name"].dropna().tolist()

        st.subheader("Submit a Request")

        action = st.radio("Select Action:", [
                            "Request Leave", "Leave Reversal / Return Early"], horizontal=True)
        is_reversal = (action == "Leave Reversal / Return Early")

        if is_reversal:
            st.info("💡 **Return Early:** Use this to restore days to your leave balance if you returned to work earlier than originally approved. The days you enter below will be added back to your balance.")

        leave_options = {
            "CL": "CL (Casual Leave)",
            "SL": "SL (Sick Leave)",
            "AL": "AL (Annual Leave)",
            "UL": "UL (Unpaid Leave)",
            "ML": "ML (Maternity Leave)",
            "PL": "PL (Paternity Leave)",
            "Other": "Other"
        }
        leave_type_selection = st.selectbox(
            "Leave Type", list(leave_options.values()))
        leave_type = [k for k, v in leave_options.items() if v ==
                    leave_type_selection][0]

        reason = ""
        effects_service_year = "No"
        if leave_type == "Other":
            reason = st.text_input("Specify Leave Reason/Type")
            if st.checkbox("Does this leave affect your service year? (Delays tenure)"):
                effects_service_year = "Yes"
                st.warning("⚠️ Warning: By checking this box (e.g. for Education Leave), your total Service Year and milestones will be delayed by the exact number of days you take.")

        department = st.text_input("Department (ལས་ཁུངས་དང་སྡེ་ཚན།)")
        u_branch = get_user_branch(st.session_state.user_name)
        is_branch_staff = (u_branch in ["SB", "NB", "DB"] and st.session_state.user_role in ["Employee", "Accounts"])
        is_coadmin_or_admin = (st.session_state.get("user_role") in ["Admin", "Co-Admin", "CoAdmin"])

        if is_branch_staff:
            bm_name, bm_topic = get_branch_manager(u_branch)
            st.info(f"🏢 **Branch Leave Approval:** As a branch staff member ({BRANCH_NAMES.get(u_branch, u_branch)}), your leave request will be routed directly to your Branch Manager: **{bm_name if bm_name else 'Branch Manager'}**.")
            selected_coadmin = "Not-Applicable"
            selected_admin = ""
        else:
            st.info(
                "ℹ️ **Leave Approval Notice / གསལ་བཤད།**: All HQ staff and Branch Managers need to choose Drungche as Admin, "
                "except Drungche itself (Drungche chooses Auditor General; Auditor General chooses Drungche).\n\n"
                "*(ལས་ཁུངས་དབུས་མའི་ལས་བྱེད་དང་ཡན་ལག་འགན་འཛིན་ཡོངས་ཀྱིས་དྲུང་ཆེ་འདེམས་དགོས། དྲུང་ཆེ་རང་ཉིད་ཀྱིས་རྩིས་ཞིབ་སྤྱི་ཁྱབ་དང་། རྩིས་ཞིབ་སྤྱི་ཁྱབ་ཀྱིས་དྲུང་ཆེ་འདེམས་དགོས།)*"
            )

            col_co, col_adm = st.columns(2)
            with col_co:
                if is_coadmin_or_admin:
                    selected_coadmin = "Not-Applicable"
                    st.text_input("Co-Admin Support", value="Not-Applicable (Direct Application)", disabled=True, key="leave_coadmin_disabled")
                else:
                    co_opts = ["--- Select Co-Admin for Support ---"] + [c for c in co_admins if c != st.session_state.user_name]
                    selected_coadmin = st.selectbox(
                        "Select Co-Admin for Support (རྒྱབ་གཉེར་གནང་མཁན་སྡེ་ཚན་འགན་འཛིན།) *",
                        co_opts,
                        key="leave_apply_coadmin"
                    )

            with col_adm:
                adm_opts = ["--- Select Admin for Approval ---"] + [a for a in admins if a != st.session_state.user_name]
                selected_admin = st.selectbox(
                    "Select Admin for Approval (བཀའ་འཁྲོལ་གནང་མཁན་འགན་འཛིན།) *",
                    adm_opts,
                    key="leave_apply_admin"
                )

        # ApplicationDate is captured automatically as submitted date & time in IST (hidden from applicant form)
        submitted_app_dt_str = get_cta_now().strftime("%Y-%m-%d %H:%M:%S")

        col_start, col_end = st.columns(2)
        start_date = col_start.date_input("Start Date")
        start_half = col_start.radio("Start Time", [
                                    "Full Day ཉིན་ཆ་ཚང་།", "Morning ཞོགས་པ།", "Evening ཉིན་རྒྱབ།"], index=0, horizontal=True)
        end_date = col_end.date_input("End Date")
    
        if start_date == end_date:
            if "Full Day" in start_half:
                end_options = ["Full Day ཉིན་ཆ་ཚང་།"]
            elif "Morning" in start_half:
                end_options = ["Morning ཞོགས་པ།"]
            elif "Evening" in start_half:
                end_options = ["Evening ཉིན་རྒྱབ།"]
            else:
                end_options = ["Full Day ཉིན་ཆ་ཚང་།", "Morning ཞོགས་པ།", "Evening ཉིན་རྒྱབ།"]
        else:
            end_options = ["Full Day ཉིན་ཆ་ཚང་།", "Morning ཞོགས་པ།", "Evening ཉིན་རྒྱབ།"]
        
        # Generate a dynamic key based on the options so Streamlit doesn't crash when options shrink
        dynamic_key = f"end_time_{len(end_options)}_{hash(tuple(end_options))}"
        end_half = col_end.radio("End Time", end_options, index=0, horizontal=True, key=dynamic_key)

        if start_date > end_date:
            calculated_days = 0.0
        elif start_date == end_date:
            if "Morning" in start_half or "Evening" in start_half:
                calculated_days = 0.5
            else:
                calculated_days = 1.0
        else:
            base_days = (end_date - start_date).days + 1
            calculated_days = float(base_days)
            if "Evening" in start_half:
                calculated_days -= 0.5
            if "Morning" in end_half:
                calculated_days -= 0.5

        st.metric("Total Days (to request or restore)", f"{calculated_days}")
        total_days = calculated_days
        final_days = -total_days if is_reversal else total_days

        if not is_reversal and calculated_days > 0 and start_date <= end_date:
            try:
                fy_bdown = get_leave_fy_breakdown(start_date, end_date, calculated_days)
                if len(fy_bdown) > 1:
                    parts = [f"**{info['fy_label']}**: {info['days']:g} day(s)" for info in fy_bdown.values()]
                    st.info(
                        f"🗓️ **Multi-Financial Year Leave Notice ({' + '.join(parts)}):**\n\n"
                        f"Your requested leave spans across multiple Financial Years. It is submitted as **1 single application**. "
                        f"At midnight on March 31, the system will automatically allocate elapsed days to the closing FY "
                        f"and rollover your leave seamlessly without splitting or penalty jumps."
                    )
            except Exception:
                pass

        df_requests_existing = load_leave_requests()
        validation_res = validate_leave_application(
            user_name=st.session_state.user_name,
            leave_type=leave_type,
            start_date=start_date,
            start_half=start_half,
            end_date=end_date,
            end_half=end_half,
            total_days=total_days,
            raw_balances=balances,
            df_requests=df_requests_existing,
            is_reversal=is_reversal,
        )

        if not is_reversal and leave_type in RESTRICTED_BALANCE_TYPES:
            eff_info = validation_res["effective_balances"]
            net_p = eff_info["net_pending"].get(leave_type, 0.0)
            eff_b = eff_info["effective"].get(leave_type, 0.0)
            raw_b = eff_info["raw"].get(leave_type, 0.0)
            if net_p > 0:
                st.caption(
                    f"ℹ️ Effective available {leave_type} balance: **{eff_b:g} day(s)** "
                    f"(Balance: {raw_b:g} - Net Pending Requests: {net_p:g})"
                )

        hard_blocks = validation_res["hard_blocks"] if not is_reversal else []
        policy_alerts = validation_res["policy_alerts"] if not is_reversal else []

        current_form_sig = f"{st.session_state.user_name}|{leave_type}|{start_date}|{start_half}|{end_date}|{end_half}|{is_reversal}"
        show_validation_on_submit = (st.session_state.get("_leave_submit_attempted_sig") == current_form_sig)

        force_apply = False
        if show_validation_on_submit:
            for hb_msg in hard_blocks:
                st.error(hb_msg)

            for pa_msg in policy_alerts:
                st.warning(pa_msg)

            if not hard_blocks and policy_alerts:
                force_apply = st.checkbox(
                    "Force Apply anyway (override weekend policy alert for regional/working calendars)",
                    key=f"force_apply_{st.session_state.user_name}_{leave_type}_{start_date}_{start_half}_{end_date}_{end_half}",
                )

        st.write("---")
        col_sub, col_prev = st.columns(2)

        preview_row = {
            "Name": st.session_state.user_name,
            "LeaveType": f"Return-{leave_type}" if is_reversal else leave_type,
            "Reason": reason,
            "EffectsServiceYear": effects_service_year,
            "ApplicationDate": submitted_app_dt_str,
            "StartDate": start_date.strftime("%Y-%m-%d"),
            "StartHalf": start_half,
            "EndDate": end_date.strftime("%Y-%m-%d"),
            "EndHalf": end_half,
            "TotalDays": final_days,
            "Department": department,
            "SelectedCoAdmin": selected_coadmin,
            "SelectedAdmin": selected_admin if not is_branch_staff else "",
            "SupportedBy": "",
            "ApprovedBy": "",
            "PunchedBy": "",
            "CLBalance": balances.get("CL", "N/A"),
            "ALBalance": balances.get("AL", "N/A"),
            "SLBalance": balances.get("SL", "N/A"),
            "ULBalance": balances.get("UL", "N/A")
        }

        leave_flash = st.session_state.pop("_submit_flash_leave", None)
        if leave_flash:
            lvl, msg = leave_flash
            if lvl == "success":
                st.success(msg)
            else:
                st.error(msg)

        if col_prev.button("👁️ Preview PDF Form", key="btn_prev_leave_pdf", disabled=is_submit_busy):
            preview_pdf_bytes = generate_leave_pdf(preview_row)
            open_pdf_dialog(preview_pdf_bytes, "Preview.pdf")

        leave_submit_kind = "leave_reversal" if is_reversal else "leave_request"
        leave_clicked = col_sub.button(
            "Submit Reversal" if is_reversal else "Submit Request",
            type="primary",
            key="btn_submit_leave",
            disabled=is_submit_busy,
            on_click=_arm_submission_freeze,
            args=(leave_submit_kind,),
        )
        if leave_clicked or st.session_state.get("_submit_pending_kind") in ("leave_request", "leave_reversal"):
            def _fail_leave_submit(err_msg, mark_attempted=False):
                _release_submission_freeze()
                if mark_attempted:
                    st.session_state["_leave_submit_attempted_sig"] = current_form_sig
                else:
                    st.session_state["_submit_flash_leave"] = ("error", err_msg)
                freeze_placeholder.empty()
                st.rerun()

            with st.spinner('Submitting... Please wait and do not click again.'):
                if total_days <= 0:
                    _fail_leave_submit("Total days must be greater than 0.")
                elif not department.strip():
                    _fail_leave_submit("Department cannot be blank.")
                elif not is_branch_staff and not is_coadmin_or_admin and (selected_coadmin.startswith("---") or selected_coadmin == "None"):
                    _fail_leave_submit("Please select a Co-Admin for support (རྒྱབ་གཉེར་གནང་མཁན་སྡེ་ཚན་འགན་འཛིན།).")
                elif not is_branch_staff and (not selected_admin or selected_admin.startswith("---")):
                    _fail_leave_submit("Please select an Admin for approval (དྲུང་ཆེ། / རྩིས་ཞིབ་སྤྱི་ཁྱབ།).")
                elif start_date > end_date:
                    _fail_leave_submit("End Date must be after or equal to Start Date.")
                elif start_date != end_date and total_days < 1:
                    _fail_leave_submit(
                        "Total days must be at least 1 when your leave spans multiple days.")
                else:
                    try:
                        load_leave_requests.clear()
                        df_requests = load_leave_requests()
                        fresh_val = validate_leave_application(
                            user_name=st.session_state.user_name,
                            leave_type=leave_type,
                            start_date=start_date,
                            start_half=start_half,
                            end_date=end_date,
                            end_half=end_half,
                            total_days=total_days,
                            raw_balances=calculate_balances(st.session_state.user_name),
                            df_requests=df_requests,
                            is_reversal=is_reversal,
                        )
                        if not is_reversal and fresh_val["hard_blocks"]:
                            _fail_leave_submit("\n\n".join(fresh_val["hard_blocks"]), mark_attempted=True)
                        elif not is_reversal and fresh_val["policy_alerts"] and not force_apply:
                            _fail_leave_submit(
                                "\n\n".join(fresh_val["policy_alerts"])
                                + "\n\nPlease tick 'Force Apply anyway' above to submit with a Policy Alert.",
                                mark_attempted=True,
                            )
                        else:
                            req_id = st.session_state.get("_submit_request_id") or _make_submission_token(leave_submit_kind)
                            exact_submit_dt = get_cta_now().strftime("%Y-%m-%d %H:%M:%S")
                            new_request = pd.DataFrame([{
                                "ID": req_id,
                                "Name": st.session_state.user_name,
                                "LeaveType": f"Return-{leave_type}" if is_reversal else leave_type,
                                "Reason": reason,
                                "EffectsServiceYear": effects_service_year,
                                "ApplicationDate": exact_submit_dt,
                                "StartDate": start_date.strftime("%Y-%m-%d"),
                                "StartHalf": start_half,
                                "EndDate": end_date.strftime("%Y-%m-%d"),
                                "EndHalf": end_half,
                                "TotalDays": final_days,
                                "Department": department,
                                "SelectedCoAdmin": selected_coadmin,
                                "SelectedAdmin": selected_admin if not is_branch_staff else "",
                                "Status": "Pending",
                                "CoAdminAcknowledged": "No",
                                "SupportedBy": "",
                                "AccountsPunched": "No",
                                "PunchedBy": "",
                                "ApprovedBy": "",
                                "CLBalance": balances.get("CL", "N/A"),
                                "ALBalance": balances.get("AL", "N/A"),
                                "SLBalance": balances.get("SL", "N/A"),
                                "ULBalance": balances.get("UL", "N/A")
                            }])
                            if should_append_idempotent_row(df_requests, req_id, id_col="ID"):
                                updated_df = pd.concat(
                                    [df for df in [df_requests, new_request] if df is not None],
                                    ignore_index=True,
                                )
                                _gsheets_retry(conn.update, worksheet="LeaveRequests", data=updated_df)
                            # Disarm pending action once sheet update succeeds so interruption during alerts cannot duplicate
                            st.session_state["_submit_pending_kind"] = None
                            _invalidate_sheet_cache("LeaveRequests")
                            action_word = "requested to RESTORE" if is_reversal else "requested"
                            req_title = "Leave Reversal Request 🔄" if is_reversal else "New Leave Request 📅"
                        
                            if selected_coadmin and selected_coadmin not in ["None", "--- Select ---", "--- Select Co-Admin for Support ---", "Not-Applicable"]:
                                admin_msg = f"{st.session_state.user_name} {action_word} {total_days} day(s) of {leave_type}. (Co-Admin: {selected_coadmin}). Pending approval!"
                            else:
                                admin_msg = f"{st.session_state.user_name} {action_word} {total_days} day(s) of {leave_type}. Pending approval!"

                            # Notification routing
                            if is_branch_staff:
                                bm_name, bm_topic = get_branch_manager(u_branch)
                                if bm_topic:
                                    send_ntfy_notification(
                                        bm_topic,
                                        "Branch Leave Request 📅",
                                        f"{st.session_state.user_name} ({u_branch}) {action_word} {total_days} day(s) of {leave_type}. Pending your approval as Branch Manager!"
                                    )
                            else:
                                # 1. Target selected Co-Admin
                                if selected_coadmin and selected_coadmin not in ["None", "--- Select ---", "--- Select Co-Admin for Support ---", "Not-Applicable"]:
                                    co_top = get_user_ntfy_topic(selected_coadmin)
                                    if co_top:
                                        send_ntfy_notification(
                                            co_top,
                                            "Leave Reversal Support 🔄" if is_reversal else "Leave Support Requested 🤝",
                                            f"{st.session_state.user_name} {action_word} {total_days} day(s) of {leave_type} and selected you for Co-Admin support."
                                        )
                                # 2. Target the specifically selected Admin
                                adm_top = get_user_ntfy_topic(selected_admin) if selected_admin else None
                                if not adm_top:
                                    adm_top = get_role_ntfy_topics("Admin")
                                send_ntfy_notification(
                                    adm_top,
                                    req_title,
                                    admin_msg
                                )
                            # 3. Target Accountants
                            send_ntfy_notification(
                                get_role_ntfy_topics("Accounts"),
                                req_title,
                                f"{st.session_state.user_name} {action_word} {total_days} day(s) of {leave_type}. (FYI - Pending Approval)"
                            )
                            st.session_state.pop("_leave_submit_attempted_sig", None)
                            st.session_state["_submit_flash_leave"] = (
                                "success",
                                "✅ Leave reversal request submitted successfully!" if is_reversal else "✅ Leave request submitted successfully!",
                            )
                            _release_submission_freeze()
                            freeze_placeholder.empty()
                            st.rerun()
                    except Exception as e:
                        _fail_leave_submit(f"Failed to submit request: {e}")

        st.divider()

        st.subheader("Your Past Requests")
        df_requests = load_leave_requests()
        emp_fy_sel = render_fy_scope_selector(
            key="emp_fy_scope",
            label="📅 Select Financial Year (FY) for Your Leave & Tour History",
            include_all=True,
            leave_df=df_requests,
        )
        emp_fy_yr = emp_fy_sel["fy_year"] if isinstance(emp_fy_sel, dict) else None
        emp_fy_records = load_fy_records(emp_fy_yr, leave_df_active=df_requests)
        emp_hist_leaves = emp_fy_records["leaves"]

        if emp_hist_leaves is not None and not emp_hist_leaves.empty and "Name" in emp_hist_leaves.columns:
            user_requests = emp_hist_leaves[emp_hist_leaves["Name"]
                                            == st.session_state.user_name]
            if not user_requests.empty:
                pending = user_requests[user_requests["Status"] == "Pending"]
                others = user_requests[user_requests["Status"] != "Pending"]

                if not pending.empty:
                    st.markdown("**⏳ Pending Requests (Can be deleted)**")
                    for idx, row in pending.iterrows():
                        is_row_active = _is_active_sheet_row(row)
                        col1, col2, col3 = st.columns([4, 1, 1])
                        col1.write(
                            f"**{row['LeaveType']}** for {row['TotalDays']} days ({row['StartDate']} to {row['EndDate']})")
                    
                        if col2.button("📄 Preview", key=f"prev_emp_pend_{row['ID']}", disabled=is_submit_busy):
                            pdf_bytes = generate_leave_pdf(row)
                            open_pdf_dialog(pdf_bytes, f"{row['Name']}_Leave.pdf")

                        target_req_id = str(row['ID']).strip()
                        del_clicked = col3.button(
                            "🗑️ Delete",
                            key=f"del_{target_req_id}",
                            disabled=(is_submit_busy or not is_row_active),
                            on_click=_arm_submission_freeze,
                            args=("leave_cancellation", target_req_id),
                        )
                        is_this_cancelling = is_row_active and (
                            del_clicked or (
                                st.session_state.get("_submit_pending_kind") == "leave_cancellation"
                                and st.session_state.get("_submit_request_id") == target_req_id
                            )
                        )
                        if is_this_cancelling:
                            try:
                                load_leave_requests.clear()
                                fresh_requests = load_leave_requests()
                                match_idx = fresh_requests[fresh_requests["ID"].map(_normalize_record_id) == _normalize_record_id(target_req_id)].index
                                if not match_idx.empty:
                                    curr_status = str(fresh_requests.at[match_idx[0], "Status"]).strip()
                                    if curr_status == "Pending":
                                        fresh_requests.at[match_idx[0], "Status"] = "Cancelled"
                                        _gsheets_retry(conn.update, worksheet="LeaveRequests", data=fresh_requests)
                                        _invalidate_sheet_cache("LeaveRequests")

                                        send_ntfy_notification(
                                            get_role_ntfy_topics("Admin"),
                                            "Leave Cancelled ❌",
                                            f"{row['Name']} has cancelled their pending {row['LeaveType']} request for {row['TotalDays']} day(s)."
                                        )
                                        selected_coadmin = str(row.get('SelectedCoAdmin', '')).strip()
                                        if selected_coadmin and selected_coadmin.lower() not in ['none', 'nan', '']:
                                            co_top = get_user_ntfy_topic(selected_coadmin)
                                            if co_top:
                                                send_ntfy_notification(
                                                    co_top,
                                                    "Leave Cancelled ❌",
                                                    f"{row['Name']} has cancelled their pending {row['LeaveType']} request for {row['TotalDays']} day(s)."
                                                )
                                        st.session_state["_submit_flash_leave"] = ("success", f"Leave request ({row['LeaveType']}) cancelled successfully.")
                                    else:
                                        st.session_state["_submit_flash_leave"] = ("error", f"Leave request is already {curr_status}.")
                                else:
                                    st.session_state["_submit_flash_leave"] = ("error", "Leave request not found in database.")
                                _release_submission_freeze()
                                freeze_placeholder.empty()
                                st.rerun()
                            except Exception as e:
                                _release_submission_freeze()
                                st.session_state["_submit_flash_leave"] = ("error", f"Failed to cancel leave request: {e}")
                                freeze_placeholder.empty()
                                st.rerun()

                if not others.empty:
                    st.markdown("**✅ Processed Requests**")
                    for idx, row in others.iterrows():
                        col1, col2 = st.columns([5, 1])
                        col1.write(
                            f"**{row['LeaveType']}** for {row['TotalDays']} days ({row['StartDate']} to {row['EndDate']}) - Status: {row['Status']}")
                        if row['Status'] == "Approved":
                            if col2.button("📄 Preview PDF", key=f"prev_emp_{row['ID']}", disabled=is_submit_busy):
                                pdf_bytes = generate_leave_pdf(row)
                                open_pdf_dialog(
                                    pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave.pdf")
            else:
                st.info("You have no past leave requests.")
        else:
            st.info("No leave requests found in the database.")




    with tab_tour:
        st.subheader("✈️ Official Duty Tour Sanctions (གཞུང་དོན་ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ།)")
        tour_flash = st.session_state.pop("_submit_flash_tour", None)
        if tour_flash:
            lvl, msg = tour_flash
            if lvl == "success":
                st.success(msg)
            else:
                st.error(msg)

        tours_df = emp_fy_records["tours"]
        my_tours = tours_df[tours_df["Name"] == st.session_state.user_name] if tours_df is not None and not tours_df.empty and "Name" in tours_df.columns else pd.DataFrame()

        # 1. Prominent Current Tour Sanction Card (only active/upcoming or pending tours)
        cur_tr = None
        if not my_tours.empty:
            today_d = get_cta_today()
            for _, cand_tr in my_tours.iterrows():
                cand_st = str(cand_tr.get("Status", "Pending")).strip()
                if cand_st in ("Rejected", "Cancelled", "Completed", "Settled"):
                    continue
                arr_d = _parse_cta_date(cand_tr.get("ArrivalDate")) or _parse_cta_date(cand_tr.get("DepartureDate"))
                # Show if still pending approval OR if sanctioned tour has not ended yet (ArrivalDate >= today)
                if cand_st not in ("Sanctioned", "Approved") or (arr_d is not None and arr_d >= today_d):
                    cur_tr = cand_tr
                    break

        if cur_tr is not None:
            tr_status = str(cur_tr.get("Status", "Pending"))
            is_cur_tr_active = _is_active_sheet_row(cur_tr)
            with st.container(border=True):
                st.markdown(f"#### 📌 Current Tour Sanction: **To {cur_tr.get('Destination', '')} ({cur_tr.get('TotalDays', 0)} Day(s))**")
                c_dt, c_st, c_pdf = st.columns([2.5, 1.5, 2])
                with c_dt:
                    st.markdown(f"**Travel Period:** `{cur_tr.get('DepartureDate', '')}` to `{cur_tr.get('ArrivalDate', '')}` | **Conveyance:** {cur_tr.get('ConveyanceMode', 'N/A')}")
                    raw_cur_legs = cur_tr.get('TravelLegs', '')
                    cur_legs = []
                    if isinstance(raw_cur_legs, list):
                        cur_legs = raw_cur_legs
                    elif isinstance(raw_cur_legs, str) and raw_cur_legs.strip().startswith('['):
                        try:
                            cur_legs = json.loads(raw_cur_legs)
                        except Exception:
                            cur_legs = []
                    if cur_legs and len(cur_legs) > 1:
                        st.caption(f"**Applied on:** {cur_tr.get('ApplicationDate', 'N/A')}")
                        for leg in cur_legs:
                            l_num = leg.get('leg_num', '')
                            l_dest = leg.get('destination', '')
                            l_dep = leg.get('departure_date', '')
                            l_arr = leg.get('arrival_date', '')
                            l_purp = str(leg.get('purpose', '')).strip()
                            if l_purp.endswith('.0') and l_purp[:-2].isdigit():
                                l_purp = l_purp[:-2]
                            st.caption(f"- **{l_num}. ({l_dest}):** `{l_dep}` to `{l_arr}` — {l_purp}")
                    else:
                        st.caption(f"Purpose: {cur_tr.get('Purpose', '')} | Applied on: {cur_tr.get('ApplicationDate', 'N/A')}")
                with c_st:
                    if tr_status in ['Sanctioned', 'Completed', 'Settled']:
                        st.success(f"✅ Sanctioned by {cur_tr.get('ApprovedBy', 'Admin')}")
                    elif tr_status == 'Rejected':
                        st.error("❌ Rejected")
                    elif tr_status == 'Cancelled':
                        st.info("❌ Cancelled")
                    else:
                        st.warning(f"⏳ Status: **{tr_status}**")
                        if cur_tr.get('SupportedBy'):
                            st.caption(f"Endorsed by: {cur_tr['SupportedBy']}")
                        elif cur_tr.get('SelectedCoAdmin'):
                            st.caption(f"Assigned Co-Admin: {cur_tr['SelectedCoAdmin']}")
                with c_pdf:
                    if st.button("👁️ Preview Current Tour PDF", key="btn_cur_tr_pdf", type="primary", use_container_width=True, disabled=is_submit_busy):
                        cur_tr_pdf = generate_tour_sanction_pdf(cur_tr)
                        if cur_tr_pdf:
                            open_pdf_dialog(cur_tr_pdf, f"{st.session_state.user_name}_Current_Tour_Sanction.pdf")
                    if st.button("⬇️ Prepare Tour PDF", key="btn_prep_cur_tr_dl", use_container_width=True, disabled=is_submit_busy):
                        cur_tr_pdf = generate_tour_sanction_pdf(cur_tr)
                        if cur_tr_pdf:
                            st.download_button("⬇️ Download Tour PDF", data=cur_tr_pdf, file_name=f"{st.session_state.user_name}_Current_Tour_Sanction.pdf", mime="application/pdf", key="dl_cur_tr_pdf", use_container_width=True)

                    if tr_status not in ['Sanctioned', 'Completed', 'Settled', 'Rejected', 'Cancelled']:
                        cur_tr_id = str(cur_tr.get('ID', '')).strip()
                        canc_clicked = st.button(
                            "❌ Cancel Pending Tour",
                            key=f"btn_cur_canc_{cur_tr_id}",
                            help="Withdraw this pending tour sanction application",
                            use_container_width=True,
                            disabled=(is_submit_busy or not is_cur_tr_active),
                            on_click=_arm_submission_freeze,
                            args=("tour_cancellation", cur_tr_id),
                        )
                        is_this_cancelling_tr = is_cur_tr_active and (
                            canc_clicked or (
                                st.session_state.get("_submit_pending_kind") == "tour_cancellation"
                                and st.session_state.get("_submit_request_id") == cur_tr_id
                            )
                        )
                        if is_this_cancelling_tr:
                            try:
                                with st.spinner("Cancelling tour application..."):
                                    load_tour_sanctions.clear()
                                    all_tours = load_tour_sanctions()
                                    match_idx = all_tours[all_tours["ID"].astype(str).str.strip() == cur_tr_id].index
                                    if not match_idx.empty:
                                        curr_status = str(all_tours.at[match_idx[0], "Status"]).strip()
                                        if curr_status not in ['Sanctioned', 'Completed', 'Settled', 'Rejected', 'Cancelled']:
                                            all_tours.at[match_idx[0], "Status"] = "Cancelled"
                                            _gsheets_retry(conn.update, worksheet="TourSanctions", data=all_tours)
                                            _invalidate_sheet_cache("TourSanctions")

                                            co_admin_name = str(cur_tr.get("SelectedCoAdmin", "")).strip()
                                            if co_admin_name and co_admin_name.lower() not in ["none", "nan", ""]:
                                                co_topic = get_user_ntfy_topic(co_admin_name)
                                                if co_topic:
                                                    send_ntfy_notification(
                                                        co_topic,
                                                        "Tour Request Cancelled ❌",
                                                        f"{st.session_state.user_name} cancelled their pending Tour to {cur_tr.get('Destination', '')} ({cur_tr.get('TotalDays', 0)} days)."
                                                    )

                                            admin_name = str(cur_tr.get("SelectedAdmin", "")).strip()
                                            adm_topic = get_user_ntfy_topic(admin_name) if admin_name else get_role_ntfy_topics("Admin")
                                            send_ntfy_notification(
                                                adm_topic,
                                                "Tour Request Cancelled ❌",
                                                f"{st.session_state.user_name} cancelled their pending Tour to {cur_tr.get('Destination', '')} ({cur_tr.get('TotalDays', 0)} days)."
                                            )

                                            st.session_state["_submit_flash_tour"] = ("success", "Tour Sanction application cancelled successfully.")
                                        else:
                                            st.session_state["_submit_flash_tour"] = ("error", f"Tour application is already {curr_status}.")
                                    else:
                                        st.session_state["_submit_flash_tour"] = ("error", "Tour application not found in database.")
                                _release_submission_freeze()
                                freeze_placeholder.empty()
                                st.rerun()
                            except Exception as e:
                                _release_submission_freeze()
                                st.session_state["_submit_flash_tour"] = ("error", f"Failed to cancel tour application: {e}")
                                freeze_placeholder.empty()
                                st.rerun()

        with st.expander("➕ Apply for Official Tour Sanction (ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག)", expanded=False):
            st.markdown("##### གཞུང་དོན་ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག")
            st.markdown("#### 🗺️ གཞུང་དོན་ཕྱོགས་བསྐྱོད་འགྲུལ་བཞུད་གནས་ཚུལ། (Travel Information)")
            st.caption("You can add multiple travel destinations for your tour. Each destination includes Work Description, Destination, Departure Date, and Arrival Date.")

            if "tour_legs_count" not in st.session_state:
                st.session_state.tour_legs_count = 1

            tour_legs_count = st.session_state.tour_legs_count
            legs_data = []

            for i in range(tour_legs_count):
                if tour_legs_count > 1:
                    st.markdown(f"**📍 Travel Information #{i+1} (འགྲུལ་བཞུད་གནས་ཚུལ་ཨང་ {i+1})**")

                t_purp_i = st.text_input(
                    "ལས་དོན་འགྲེལ་བརྗོད། (Work Description / Duty Purpose) *",
                    placeholder="རྩིས་ལོ་ ༢༠༢༥་་༢༦ ལོའི་ཅོན་ཏ་ར་ས་གནས་འགོ་འཛིན་རྩིས་ཞིབ་སླད།",
                    key=f"t_purp_{i}"
                )
                t_dest_i = st.text_input(
                    "ས་གནས། (Destination / Station) *",
                    placeholder="ཅོན་ཏ་ར།",
                    key=f"t_dest_{i}"
                )

                c1, c2 = st.columns(2)
                with c1:
                    t_dep_i = st.date_input("ཐོན་ཚེས། (Departure Date)", value=get_cta_today(), key=f"t_dep_{i}")
                with c2:
                    t_arr_i = st.date_input("འབྱོར་ཚེས། (Arrival Date)", value=get_cta_today(), key=f"t_arr_{i}")

                legs_data.append({
                    "leg_num": i + 1,
                    "purpose": t_purp_i.strip(),
                    "destination": t_dest_i.strip(),
                    "departure_date": t_dep_i.strftime("%Y-%m-%d") if hasattr(t_dep_i, "strftime") else str(t_dep_i),
                    "arrival_date": t_arr_i.strftime("%Y-%m-%d") if hasattr(t_arr_i, "strftime") else str(t_arr_i)
                })

            col_add_leg, col_rem_leg, _ = st.columns([2.5, 1.8, 2.5])
            with col_add_leg:
                if st.button("➕ Add More Travel Information (འགྲུལ་བཞུད་གནས་ཚུལ་ཁ་སྣོན།)", key="btn_add_leg", use_container_width=True):
                    st.session_state.tour_legs_count = tour_legs_count + 1
                    st.rerun()
            with col_rem_leg:
                if tour_legs_count > 1:
                    if st.button("➖ Remove Last Destination", key="btn_rem_leg", use_container_width=True):
                        st.session_state.tour_legs_count = max(1, tour_legs_count - 1)
                        st.rerun()

            st.write("---")
            st.markdown("#### 🚗 འགྲུལ་བཞུད་མཐུན་རྐྱེན། (Mode of Conveyance / Transportation)")
            conveyance_options = [
                "🚗 Own Vehicle — Car / Motorcycle (རང་སྒེར་གྱི་སྣུམ་འཁོར།)",
                "🚙 Official CTA / Department Vehicle (གཞུང་འབྲེལ་སྣུམ་འཁོར།)",
                "🚌 Public Bus (སྤྱི་སྤྱོད་རླངས་འཁོར།)",
                "🚆 Train / Rail (མེ་འཁོར།)",
                "✈️ Flight / Air (གནམ་གྲུ།)",
                "🚕 Taxi / Hired Cab (གླ་གཏོང་སྣུམ་འཁོར།)",
                "🔄 Combined / Other (གཞན་དག)"
            ]
            t_conveyance = st.selectbox(
                "Select Mode of Travel (འགྲུལ་བཞུད་མཐུན་རྐྱེན་འདེམས་རྒྱུ།) *",
                conveyance_options,
                key="t_apply_conveyance"
            )

            st.write("---")
            st.markdown("#### 👥 རྒྱབ་གཉེར་དང་བཀའ་འཁྲོལ་གནང་མཁན་འདེམས་རྒྱུ། (Approval Routing)")
            st.caption("Please select the Co-Admin / Section Head who will review and support your tour, and the Admin who will issue final sanction.")

            users_df = load_users_data()
            co_admins = []
            admins = []
            branch_managers = []
            user_branch_norm = normalize_branch_code(get_user_branch(st.session_state.user_name))
            if users_df is not None and not users_df.empty and "Role" in users_df.columns:
                co_admins = users_df[users_df["Role"].isin(["Co-Admin", "CoAdmin"])]["Name"].dropna().tolist()
                admins = users_df[users_df["Role"] == "Admin"]["Name"].dropna().tolist()
                if user_branch_norm != "HQ":
                    branch_managers = users_df[
                        (users_df["Role"].astype(str).str.strip().str.lower().isin(["branch manager", "branchmanager"])) &
                        (users_df["Branch"].apply(normalize_branch_code) == user_branch_norm) &
                        (users_df["Name"] != st.session_state.user_name)
                    ]["Name"].dropna().tolist()

            is_bm = (st.session_state.user_role == "Branch Manager")
            is_coadmin = (st.session_state.user_role in ["Co-Admin", "CoAdmin"])
            is_admin = (st.session_state.user_role == "Admin")
            is_branch_staff = bool(branch_managers) and not is_bm and not is_admin and not is_coadmin

            st.info(
                "ℹ️ **Tour Sanction Approval Notice / གསལ་བཤད།**: All Tour Sanctions must be approved by Drungche, "
                "except Sungdrung and higher posts. Sungdrung and higher posts will choose Admin as Auditor General.\n\n"
                "*(གཞུང་དོན་ཕྱོགས་བསྐྱོད་ཆོག་མཆན་ཡོངས་རྫོགས་དྲུང་ཆེས་གནང་དགོས། ཟུང་དྲུང་དང་དེ་ལས་མཐོ་བའི་གོ་གནས་རྣམས་ཀྱིས་རྩིས་ཞིབ་སྤྱི་ཁྱབ་འདེམས་དགོས།)*"
            )

            col_co, col_adm = st.columns(2)
            with col_co:
                if is_admin or is_coadmin:
                    st.text_input("Co-Admin Support", value="Not-Applicable (Direct Application)", disabled=True, key="tour_coadmin_disabled")
                    t_coadmin = "Not-Applicable"
                else:
                    co_options = ["--- Select Co-Admin for Support ---"] + [c for c in co_admins if c != st.session_state.user_name]
                    t_coadmin = st.selectbox(
                        "Select Co-Admin for Support (རྒྱབ་གཉེར་གནང་མཁན་སྡེ་ཚན་ཟུང་དྲུང་།) *", 
                        co_options, 
                        key="t_apply_coadmin"
                    )
            with col_adm:
                adm_options = ["--- Select Admin for Final Sanction ---"] + admins
                t_admin = st.selectbox(
                    "Select Admin for Final Sanction (བཀའ་འཁྲོལ་གནང་མཁན་འགན་འཛིན།/དྲུང་ཆེ།) *", 
                    adm_options, 
                    key="t_apply_admin"
                )

            staff_df = load_staff_master()
            user_post = ""
            user_branch = "HQ"
            if staff_df is not None and not staff_df.empty:
                u_row = staff_df[staff_df["Name"] == st.session_state.user_name]
                if not u_row.empty:
                    user_post = str(u_row.iloc[0].get("Designation", u_row.iloc[0].get("Post", "")))
                    user_branch = str(u_row.iloc[0].get("Branch", "HQ"))

            bm_auto = ""
            if user_branch_norm in ["SB", "NB", "DB"] or is_branch_staff:
                bm_found, _ = get_branch_manager(user_branch_norm)
                bm_auto = bm_found or (branch_managers[0] if branch_managers else "")

            st.write("---")
            tour_flash = st.session_state.pop("_submit_flash_tour", None)
            if tour_flash:
                lvl, msg = tour_flash
                if lvl == "success":
                    st.success(msg)
                else:
                    st.error(msg)

            c_tsub1, c_tsub2 = st.columns(2)
            with c_tsub2:
                if st.button("👁️ Preview Current Draft PDF", key="btn_draft_tr_pdf", use_container_width=True, disabled=is_submit_busy):
                    dest_list = [leg["destination"].strip() for leg in legs_data if leg["destination"].strip()]
                    comb_dest = " ➔ ".join(dest_list) if dest_list else "Destination Station"
                    first_dep = legs_data[0]["departure_date"]
                    last_arr = legs_data[-1]["arrival_date"]
                    try:
                        t_days = calculate_tour_days(first_dep, last_arr) or 1
                    except Exception:
                        t_days = 1

                    purp_list = [str(leg["purpose"]).strip() for leg in legs_data if str(leg["purpose"]).strip()]
                    purp_clean = [p[:-2] if p.endswith('.0') and p[:-2].isdigit() else p for p in purp_list]
                    if len(set(purp_clean)) == 1:
                        comb_purp = purp_clean[0]
                    else:
                        comb_purp = " ➔ ".join(purp_clean) if purp_clean else "གཞུང་དོན་ལས་རིམ།"

                    draft_tr = {
                        "ID": "DRAFT",
                        "Name": st.session_state.user_name,
                        "Branch": user_branch,
                        "Role": st.session_state.get("user_role", "Employee"),
                        "Post": user_post,
                        "Purpose": comb_purp,
                        "Destination": comb_dest,
                        "DepartureDate": first_dep,
                        "ArrivalDate": last_arr,
                        "TotalDays": t_days,
                        "ConveyanceMode": t_conveyance,
                        "Status": "DRAFT PREVIEW",
                        "SelectedCoAdmin": t_coadmin if not t_coadmin.startswith("---") else "",
                        "SelectedAdmin": t_admin if not t_admin.startswith("---") else "",
                        "BranchManager": bm_auto,
                        "BMSupportedBy": "",
                        "SupportedBy": "N/A (Direct Application)" if t_coadmin == "Not-Applicable" else "",
                        "ApprovedBy": "",
                        "SanctionedBy": "",
                        "AccountsPunched": "No",
                        "PunchedBy": "",
                        "ApplicationDate": get_cta_today().strftime("%Y-%m-%d"),
                        "TravelLegs": json.dumps(legs_data, ensure_ascii=False)
                    }
                    draft_tr_pdf = generate_tour_sanction_pdf(draft_tr)
                    if draft_tr_pdf:
                        open_pdf_dialog(draft_tr_pdf, f"{st.session_state.user_name}_Draft_Tour_Sanction.pdf")

            with c_tsub1:
                tour_clicked = st.button(
                    "Submit Tour Sanction Application",
                    type="primary",
                    key="btn_submit_tr",
                    use_container_width=True,
                    disabled=is_submit_busy,
                    on_click=_arm_submission_freeze,
                    args=("tour_request",),
                )
            if tour_clicked or st.session_state.get("_submit_pending_kind") == "tour_request":
                def _fail_tour_submit(err_msg):
                    _release_submission_freeze()
                    st.session_state["_submit_flash_tour"] = ("error", err_msg)
                    freeze_placeholder.empty()
                    st.rerun()

                dest_list = [leg["destination"].strip() for leg in legs_data if leg["destination"].strip()]
                missing_purps = [i+1 for i, leg in enumerate(legs_data) if not leg["purpose"].strip()]
                missing_legs = [i+1 for i, leg in enumerate(legs_data) if not leg["destination"].strip()]
                
                if missing_purps:
                    _fail_tour_submit(f"Please provide Work Description / Duty Purpose (ལས་དོན་འགྲེལ་བརྗོད།) for Travel Leg(s): #{missing_purps}")
                elif missing_legs:
                    _fail_tour_submit(f"Please fill Destination (ས་གནས།) for Travel Leg(s): #{missing_legs}")
                elif t_coadmin != "Not-Applicable" and t_coadmin.startswith("---"):
                    _fail_tour_submit("Please select a Co-Admin to support your tour application (or choose Not-Applicable).")
                elif t_admin.startswith("---"):
                    _fail_tour_submit("Please select a sanctioning authority to review and grant final sanction.")
                else:
                    date_errors = []
                    for i, leg in enumerate(legs_data):
                        try:
                            d_dep = datetime.strptime(leg["departure_date"], "%Y-%m-%d").date()
                            d_arr = datetime.strptime(leg["arrival_date"], "%Y-%m-%d").date()
                            if d_arr < d_dep:
                                date_errors.append(i + 1)
                        except Exception:
                            pass

                    if date_errors:
                        _fail_tour_submit(f"Arrival Date cannot be before Departure Date for Travel Leg(s): #{date_errors}")
                    else:
                        with st.spinner("Submitting Tour Application..."):
                            try:
                                comb_dest = " ➔ ".join(dest_list) if dest_list else "Destination Station"
                                first_dep = legs_data[0]["departure_date"]
                                last_arr = legs_data[-1]["arrival_date"]
                                try:
                                    t_days = calculate_tour_days(first_dep, last_arr) or 1
                                except Exception:
                                    t_days = 1

                                purp_list = [str(leg["purpose"]).strip() for leg in legs_data if str(leg["purpose"]).strip()]
                                purp_clean = [p[:-2] if p.endswith('.0') and p[:-2].isdigit() else p for p in purp_list]
                                if len(set(purp_clean)) == 1:
                                    comb_purp = purp_clean[0]
                                else:
                                    comb_purp = " ➔ ".join(purp_clean) if purp_clean else "གཞུང་དོན་ལས་རིམ།"

                                tour_id = st.session_state.get("_submit_request_id") or _make_submission_token("tour_request")

                                is_branch_applicant = bool(bm_auto and user_branch in ["SB", "NB", "DB"] and st.session_state.get("user_role") not in ["Branch Manager", "Admin", "Co-Admin", "CoAdmin"])
                                if is_branch_applicant:
                                    initial_status = "Pending Branch Manager Support"
                                elif t_coadmin == "Not-Applicable":
                                    initial_status = "Pending Admin Sanction"
                                else:
                                    initial_status = "Pending Co-Admin Support"

                                new_tour = {
                                    "ID": tour_id,
                                    "Name": st.session_state.user_name,
                                    "Branch": user_branch,
                                    "Role": st.session_state.get("user_role", "Employee"),
                                    "Post": user_post,
                                    "Purpose": comb_purp,
                                    "Destination": comb_dest,
                                    "DepartureDate": first_dep,
                                    "ArrivalDate": last_arr,
                                    "TotalDays": t_days,
                                    "ConveyanceMode": t_conveyance,
                                    "Status": initial_status,
                                    "SelectedCoAdmin": t_coadmin,
                                    "SelectedAdmin": t_admin,
                                    "BranchManager": bm_auto,
                                    "BMSupportedBy": "",
                                    "SupportedBy": "N/A (Direct Application)" if (t_coadmin == "Not-Applicable") else "",
                                    "ApprovedBy": "",
                                    "SanctionedBy": "",
                                    "AccountsPunched": "No",
                                    "PunchedBy": "",
                                    "ApplicationDate": get_cta_today().strftime("%Y-%m-%d"),
                                    "TravelLegs": json.dumps(legs_data, ensure_ascii=False)
                                }

                                load_tour_sanctions.clear()
                                current_tours = load_tour_sanctions()
                                if should_append_idempotent_row(current_tours, tour_id, id_col="ID"):
                                    updated_tours = pd.concat(
                                        [df for df in [current_tours, pd.DataFrame([new_tour])] if df is not None],
                                        ignore_index=True,
                                    )
                                    _gsheets_retry(conn.update, worksheet="TourSanctions", data=updated_tours)
                                st.session_state["_submit_pending_kind"] = None
                                _invalidate_sheet_cache("TourSanctions")
                                st.session_state.tour_legs_count = 1

                                is_direct_admin_tour = (t_coadmin == "Not-Applicable")
                                if initial_status == "Pending Branch Manager Support":
                                    # 1. Notify Branch Manager FIRST (Co-Admin cannot see it until BM approves)
                                    bm_name_notif, bm_topic = get_branch_manager(user_branch)
                                    if not bm_name_notif and branch_managers:
                                        bm_name_notif = branch_managers[0]
                                        bm_topic = get_user_ntfy_topic(bm_name_notif)
                                    if bm_topic and bm_name_notif != st.session_state.user_name:
                                        send_ntfy_notification(
                                            bm_topic,
                                            "New Branch Tour Application ✈️",
                                            f"{st.session_state.user_name} ({user_branch}) applied for Tour to {comb_dest} ({t_days} days). Awaiting your Branch Manager support."
                                        )
                                    # 2. Notify Admin that application was submitted and is pending BM support
                                    adm_top = get_user_ntfy_topic(t_admin) or get_role_ntfy_topics("Admin")
                                    send_ntfy_notification(
                                        adm_top,
                                        "New Branch Tour Application Submitted ✈️",
                                        f"{st.session_state.user_name} ({user_branch}) applied for Tour to {comb_dest} ({t_days} days). Status: Pending Branch Manager Support ➔ Co-Admin ({t_coadmin}) ➔ Admin ({t_admin})."
                                    )
                                else:
                                    # Non-branch staff / Direct Admin application
                                    if not is_direct_admin_tour:
                                        co_top = get_user_ntfy_topic(t_coadmin)
                                        if co_top:
                                            send_ntfy_notification(
                                                co_top,
                                                "Tour Request to Support ✈️",
                                                f"{st.session_state.user_name} requested your support/endorsement for Tour to {comb_dest} ({t_days} days). Assigned Admin for final sanction: {t_admin}."
                                            )
                                    adm_top = get_user_ntfy_topic(t_admin) or get_role_ntfy_topics("Admin")
                                    if is_direct_admin_tour:
                                        send_ntfy_notification(
                                            adm_top,
                                            "New Direct Tour Application Submitted ✈️",
                                            f"{st.session_state.user_name} ({st.session_state.get('user_role', 'Staff')}) applied for Tour to {comb_dest} ({t_days} days). Co-Admin: Not-Applicable. Assigned Admin: {t_admin} for final sanction."
                                        )
                                    else:
                                        send_ntfy_notification(
                                            adm_top,
                                            "New Tour Application Submitted ✈️",
                                            f"{st.session_state.user_name} applied for Tour to {comb_dest} ({t_days} days). Routing: Co-Admin ({t_coadmin}) Support ➔ Admin ({t_admin}) Sanction."
                                        )

                                st.session_state["_submit_flash_tour"] = (
                                    "success",
                                    "✅ Official Tour Sanction application submitted successfully!",
                                )
                                _release_submission_freeze()
                                freeze_placeholder.empty()
                                st.rerun()
                            except Exception as e:
                                _fail_tour_submit(f"Failed to submit tour request: {e}")

        # Tour History
        st.subheader("Your Tour Sanction Applications")
        st.info("💡 **Official Tour Document:** Click **'👁️ View Tour Form (PDF)'** on any application below to preview or download the official CTA Tour Sanction Form (`ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག`).")
        if my_tours.empty:
            st.info("No tour sanction applications on record.")
        else:
            for _, tr in my_tours.iterrows():
                is_tr_act = _is_active_sheet_row(tr)
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 1, 1.2])
                    with c1:
                        st.markdown(f"**✈️ Tour to {tr.get('Destination', '')} — {tr.get('TotalDays', 0)} Day(s)** ({tr.get('DepartureDate', '')} to {tr.get('ArrivalDate', '')})")
                        st.caption(f"Purpose: {tr.get('Purpose', '')} | Conveyance: {tr.get('ConveyanceMode', 'N/A')}")
                        if tr.get('SupportedBy'):
                            st.caption(f"Co-Admin Support: ✅ Endorsed by {tr['SupportedBy']}")
                        elif tr.get('SelectedCoAdmin'):
                            st.caption(f"Co-Admin Support: ⏳ Awaiting endorsement by {tr['SelectedCoAdmin']}")
                        if tr.get('ApprovedBy'):
                            st.caption(f"Admin Sanction: ✅ Approved by {tr['ApprovedBy']}")
                        elif tr.get('SelectedAdmin'):
                            st.caption(f"Admin Sanction: ⏳ Assigned to Admin {tr['SelectedAdmin']}")
                    with c2:
                        t_status = str(tr.get('Status', 'Pending'))
                        if t_status in ['Sanctioned', 'Completed', 'Settled']:
                            st.success(t_status)
                        elif t_status == 'Rejected':
                            st.error(t_status)
                        elif t_status == 'Cancelled':
                            st.info("❌ Cancelled")
                        else:
                            st.warning(t_status)
                    with c3:
                        if st.button("👁️ View Tour Form (PDF)", key=f"tour_pdf_{tr.get('ID', '')}", use_container_width=True, disabled=is_submit_busy):
                            pdf_data = generate_tour_sanction_pdf(tr)
                            if pdf_data:
                                open_pdf_dialog(pdf_data, f"{st.session_state.user_name}_Tour_Sanction_{tr.get('ID', '')}.pdf")

                        hist_tr_id = str(tr.get('ID', '')).strip()
                        if str(tr.get("Status", "")).strip() not in ["Sanctioned", "Completed", "Settled", "Rejected", "Cancelled"]:
                            canc_hist_clicked = st.button(
                                "❌ Cancel Request",
                                key=f"cancel_tr_{hist_tr_id}",
                                use_container_width=True,
                                help="Withdraw this pending application",
                                disabled=(is_submit_busy or not is_tr_act),
                                on_click=_arm_submission_freeze,
                                args=("tour_cancellation", hist_tr_id),
                            )
                            is_hist_cancelling = is_tr_act and (
                                canc_hist_clicked or (
                                    st.session_state.get("_submit_pending_kind") == "tour_cancellation"
                                    and st.session_state.get("_submit_request_id") == hist_tr_id
                                )
                            )
                            if is_hist_cancelling:
                                try:
                                    with st.spinner("Cancelling tour application..."):
                                        load_tour_sanctions.clear()
                                        all_tours = load_tour_sanctions()
                                        match_idx = all_tours[all_tours["ID"].astype(str).str.strip() == hist_tr_id].index
                                        if not match_idx.empty:
                                            curr_h_status = str(all_tours.at[match_idx[0], "Status"]).strip()
                                            if curr_h_status not in ['Sanctioned', 'Completed', 'Settled', 'Rejected', 'Cancelled']:
                                                all_tours.at[match_idx[0], "Status"] = "Cancelled"
                                                _gsheets_retry(conn.update, worksheet="TourSanctions", data=all_tours)
                                                _invalidate_sheet_cache("TourSanctions")

                                                co_admin_name = str(tr.get("SelectedCoAdmin", "")).strip()
                                                if co_admin_name and co_admin_name.lower() not in ["none", "nan", ""]:
                                                    co_top = get_user_ntfy_topic(co_admin_name)
                                                    if co_top:
                                                        send_ntfy_notification(
                                                            co_top,
                                                            "Tour Request Cancelled ❌",
                                                            f"{st.session_state.user_name} cancelled their pending Tour to {tr.get('Destination', '')}."
                                                        )

                                                admin_name = str(tr.get("SelectedAdmin", "")).strip()
                                                adm_top = get_user_ntfy_topic(admin_name) if admin_name else get_role_ntfy_topics("Admin")
                                                send_ntfy_notification(
                                                    adm_top,
                                                    "Tour Request Cancelled ❌",
                                                    f"{st.session_state.user_name} cancelled their pending Tour to {tr.get('Destination', '')}."
                                                )

                                                st.session_state["_submit_flash_tour"] = ("success", "Tour Sanction application cancelled successfully.")
                                            else:
                                                st.session_state["_submit_flash_tour"] = ("error", f"Tour application is already {curr_h_status}.")
                                        else:
                                            st.session_state["_submit_flash_tour"] = ("error", "Tour application not found in database.")
                                    _release_submission_freeze()
                                    freeze_placeholder.empty()
                                    st.rerun()
                                except Exception as e:
                                    _release_submission_freeze()
                                    st.session_state["_submit_flash_tour"] = ("error", f"Failed to cancel tour application: {e}")
                                    freeze_placeholder.empty()
                                    st.rerun()

    # Safety failsafe: never leave freeze overlay active if unconsumed
    if st.session_state.get("_submit_busy", False):
        _release_submission_freeze()
        freeze_placeholder.empty()
        st.rerun()


def render_balance_adjustment_panel(
    key_prefix,
    scoped_staff_df,
    full_staff_df=None,
    caption=None,
    allowed_branches=None,
    exclude_user=None,
):
    """
    Shared Balance & Cumulative Service Discrepancy Adjustment UI.
    Reused across Admin/Accounts Leave Accounting Engine, System Settings, and
    Branch Manager Dashboard (strictly scoped to the Branch Manager's own branch staff).
    """
    st.subheader("🛠️ Balance & Cumulative Service Discrepancy Adjustments")
    st.caption(
        caption
        or "Directly adjust leave balances (CL/SL/AL/UL), Cumulative UL, Used UL, Last UL Milestone, or Service-Affecting leave counters (Education Leave & Etc.) with a mandatory audit reason."
    )
    if scoped_staff_df is None or scoped_staff_df.empty:
        st.info("No employees found within your active branch scope.")
        return

    allowed_names = [str(n).strip() for n in scoped_staff_df["Name"].dropna().tolist() if str(n).strip()]
    if exclude_user:
        allowed_names = [name for name in allowed_names if name != str(exclude_user).strip()]

    if not allowed_names:
        st.info("No employees available for adjustment.")
        return

    ref_df = full_staff_df if (full_staff_df is not None and not full_staff_df.empty) else scoped_staff_df
    cat_labels = [lbl for _, lbl in BALANCE_ADJUSTMENT_CATEGORIES]
    label_to_key = {lbl: key for key, lbl in BALANCE_ADJUSTMENT_CATEGORIES}

    c_a1, c_a2 = st.columns([1.45, 2.55])
    with c_a1:
        adj_staff_name = st.selectbox("Select Employee", allowed_names, key=f"{key_prefix}_staff_sel")
        adj_category_label = st.selectbox("Select Field / Leave Category", cat_labels, key=f"{key_prefix}_lt_sel")

    cat_key = label_to_key[adj_category_label]
    sel_match = ref_df[ref_df["Name"].astype(str).str.strip() == adj_staff_name]
    sel_row_override = sel_match.iloc[0] if not sel_match.empty else None
    cur_bal = calculate_balances(adj_staff_name, user_row_override=sel_row_override)

    raw_milestone = 0.0
    if not sel_match.empty and "Last_UL_Milestone" in sel_match.columns:
        raw_milestone = _safe_num(sel_match.iloc[0].get("Last_UL_Milestone", 0.0))

    cur_val_map = {
        "CL": float(cur_bal.get("CL", 0.0)),
        "SL": float(cur_bal.get("SL", 0.0)),
        "AL": float(cur_bal.get("AL", 0.0)),
        "UL": float(cur_bal.get("UL", 0.0)),
        "Cumulative_UL": float(cur_bal.get("Cumulative_UL", 0.0)),
        "Used_UL": float(cur_bal.get("Used_UL", 0.0)),
        "Last_UL_Milestone": float(raw_milestone),
        "Cumulative_Eff_Service": float(cur_bal.get("Cumulative_Effecting_Service", 0.0)),
        "Current_Eff_Service": float(cur_bal.get("Current_Year_Effecting_Service", 0.0)),
    }

    with c_a2:
        st.markdown(f"**Current Balances & Cumulative Records for {adj_staff_name}:**")
        m_c1, m_c2, m_c3, m_c4 = st.columns(4)
        m_c1.metric("CL Balance", f"{cur_bal.get('CL', 0):g}d")
        m_c2.metric("SL Balance", f"{cur_bal.get('SL', 0):g}d")
        m_c3.metric("AL Balance", f"{cur_bal.get('AL', 0):g}d")
        m_c4.metric("UL Balance", f"{cur_bal.get('UL', 0):g}d")

        s_c1, s_c2, s_c3, s_c4 = st.columns(4)
        s_c1.metric("Cumulative UL", f"{cur_bal.get('Cumulative_UL', 0):g}d")
        s_c2.metric("Used UL (This FY)", f"{cur_bal.get('Used_UL', 0):g}d")
        s_c3.metric("Cum. Service Penalty", f"{cur_bal.get('Cumulative_Effecting_Service', 0):g}d")
        s_c4.metric("Current FY Service", f"{cur_bal.get('Current_Year_Effecting_Service', 0):g}d")

        st.caption(
            f"Active FY: **{format_fy_short(cur_bal.get('Active_FY', ''))}** | "
            f"Last UL Milestone: **{cur_val_map['Last_UL_Milestone']:g} yrs** | "
            f"Total Service Year Penalty: **{cur_bal.get('Service_Year_Penalty', 0):g}d** | "
            f"Actual Service Year: **{cur_bal.get('Actual_Service_Year', 0):.2f} yrs**"
        )
        st.write("---")

        adj_mode = st.radio(
            "Adjustment Type",
            ["Add / Deduct (+/- Days)", "Set Exact New Value"],
            horizontal=True,
            key=f"{key_prefix}_mode_rad",
        )

        c_amt, c_rsn = st.columns([1.2, 2.3])
        with c_amt:
            if adj_mode == "Add / Deduct (+/- Days)":
                adj_amount = st.number_input(
                    "Years to Adjust (e.g. +5.0 or -5.0)" if cat_key == "Last_UL_Milestone" else "Days to Adjust (e.g. +3.0 or -1.5)",
                    value=0.0,
                    step=1.0 if cat_key == "Last_UL_Milestone" else 0.5,
                    key=f"{key_prefix}_num_delta",
                )
            else:
                adj_amount = st.number_input(
                    "Exact Target Milestone (Years: 0, 5, 10, 15, 20)" if cat_key == "Last_UL_Milestone" else "Exact Target Value (Days)",
                    value=float(cur_val_map.get(cat_key, 0.0)),
                    min_value=0.0,
                    step=1.0 if cat_key == "Last_UL_Milestone" else 0.5,
                    key=f"{key_prefix}_num_target",
                )

        with c_rsn:
            adj_reason = st.text_input(
                "Reason for Adjustment (Mandatory) *",
                placeholder="e.g. Audit discrepancy fix / Cumulative UL or Education Leave reconciliation",
                key=f"{key_prefix}_txt_reason",
            )

        if st.button("💾 Apply Balance Correction", type="primary", key=f"{key_prefix}_btn_apply"):
            if not adj_reason.strip():
                st.error("Please enter a mandatory audit reason for this adjustment.")
            elif adj_staff_name not in allowed_names:
                st.error("Permission denied: selected employee is outside your authorized branch scope.")
            elif exclude_user and adj_staff_name == str(exclude_user).strip():
                st.error("Permission denied: self-adjustment of leave balances is prohibited.")
            else:
                with st.spinner("Applying discrepancy correction..."):
                    staff_df_fresh = load_staff_master()
                    if staff_df_fresh is None or staff_df_fresh.empty:
                        st.error("Staff Master could not be loaded.")
                    else:
                        cur_bal_fresh = calculate_balances(adj_staff_name)
                        try:
                            updated_df, old_val, new_val, net_diff = apply_staff_balance_correction(
                                staff_df_fresh,
                                adj_staff_name,
                                cat_key,
                                adj_mode,
                                adj_amount,
                                current_balances=cur_bal_fresh,
                                authorized_branches=allowed_branches,
                            )
                            _gsheets_retry(conn.update, worksheet="Staff_Master", data=inject_balance_formulas(updated_df))
                            adjuster_user = st.session_state.get("user_name", "Admin")
                            record_balance_audit_log(
                                adjuster=adjuster_user,
                                employee=adj_staff_name,
                                category=cat_key,
                                old_val=old_val,
                                new_val=new_val,
                                net_delta=net_diff,
                                reason=adj_reason.strip(),
                            )
                            _invalidate_sheet_cache("Staff_Master", "AuditLog")
                            st.success(
                                f"✅ Updated {adj_staff_name}'s {adj_category_label}: "
                                f"{old_val:g} -> {new_val:g} (Net: {net_diff:+g}). Reason: {adj_reason.strip()}"
                            )
                            st.rerun()
                        except PermissionError as pe:
                            st.error(str(pe))
                        except Exception as ex:
                            st.error(f"Correction failed: {ex}")

    st.write("---")
    with st.expander("📜 Balance Adjustment Audit Trail", expanded=False):
        audit_df = load_audit_log()
        if audit_df is not None and not audit_df.empty:
            if allowed_branches is not None:
                audit_disp = audit_df[audit_df["Employee"].astype(str).str.strip().isin(allowed_names)]
            else:
                audit_disp = audit_df
            if not audit_disp.empty:
                st.dataframe(audit_disp.iloc[::-1], use_container_width=True, hide_index=True)
            else:
                st.info("No audit logs found for employees within this scope.")
        else:
            st.info("No balance adjustment audit records recorded yet.")


def calculate_staff_service_years(staff_df, as_of_date=None, rules_override=None):
    """
    Recalculates Service Year, Service Year Penalty, Actual Service Year,
    and grants UL milestone additions up to the configured career cap (default 730.0 days).
    Operates on a copy of staff_df and returns the updated DataFrame.
    """
    if staff_df is None or staff_df.empty:
        return staff_df

    _resolve_rules_fn = globals().get("get_effective_leave_rules")
    _rules = _resolve_rules_fn(rules_override) if callable(_resolve_rules_fn) else (rules_override if isinstance(rules_override, dict) else {})
    _types_cfg = _rules.get("leave_types", {}) if isinstance(_rules, dict) else {}
    _global_cfg = _rules.get("global", {}) if isinstance(_rules, dict) else {}
    ul_affects_svc = bool(_types_cfg.get("UL", {}).get("Affects_Service_Year", True))
    extra_svc_types = [
        k for k in ("CL", "SL", "AL")
        if bool(_types_cfg.get(k, {}).get("Affects_Service_Year", False))
    ]
    ul_grant_per_ms = float(_global_cfg.get("UL_Grant_Per_Milestone", 182.5))
    ul_career_cap = float(
        _global_cfg.get(
            "UL_Career_Max_Cap",
            float(_types_cfg.get("UL", {}).get("Max_Accumulation_Cap", 730.0)),
        )
    )
    _ms_fn = globals().get("get_ul_milestone_years_desc")
    ms_list = _ms_fn(_rules) if callable(_ms_fn) else [20.0, 15.0, 10.0, 5.0]

    df = staff_df.copy()
    if as_of_date is None:
        today_d = get_cta_today()
    elif isinstance(as_of_date, datetime):
        today_d = as_of_date.date()
    else:
        today_d = as_of_date

    for col in ["Actual Service Year", "Service Year", "Service Year Penalty"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].astype(float)

    for idx, row in df.iterrows():
        try:
            j_raw = row.get("JoiningDate")
            j_str = str(j_raw).strip() if j_raw is not None else ""
            if not j_str or j_str.lower() in ("nan", "none", "nat", ""):
                continue

            j_date = pd.to_datetime(j_str).date()
            cal_days = max(0, (today_d - j_date).days)
            calendar_service_years = round(cal_days / 365.25, 2)
            df.loc[idx, "Service Year"] = calendar_service_years

            effect_days_curr = _safe_num(row.get("Current Year Leave affecting Service Year", 0))
            effect_days_cum = _safe_num(row.get("Cummulative leave effecting Service year.(Education Leave & Etc).", 0))
            ul_used = _safe_num(row.get("Used_UL", 0)) if ul_affects_svc else 0.0
            ul_cum = _safe_num(row.get("Cumulative_UL", 0)) if ul_affects_svc else 0.0
            extra_svc_days = sum(_safe_num(row.get(f"Used_{k}", 0)) for k in extra_svc_types)

            service_penalty = effect_days_cum + effect_days_curr + ul_used + ul_cum + extra_svc_days
            df.loc[idx, "Service Year Penalty"] = service_penalty

            actual_days = max(0, cal_days - service_penalty)
            tenure_years = round(actual_days / 365.25, 2)
            df.loc[idx, "Actual Service Year"] = tenure_years

            # Career UL milestone cap enforcement (identical to auto-rollover)
            last_ms = _safe_num(row.get("Last_UL_Milestone", 0))
            o_ul = _safe_num(row.get("Opening_UL", 0))
            curr_add = _safe_num(row.get("Addition_UL", 0))
            raw_cum_ul = _safe_num(row.get("Cumulative_UL", 0))
            lifetime_ul_so_far = o_ul + curr_add + raw_cum_ul
            remaining_ul_cap = max(0.0, ul_career_cap - lifetime_ul_so_far) if ul_career_cap > 0 else ul_grant_per_ms

            ul_addition = 0.0
            for ms in ms_list:
                if tenure_years >= ms and last_ms < ms:
                    ul_addition = min(ul_grant_per_ms, remaining_ul_cap) if ul_career_cap > 0 else ul_grant_per_ms
                    df.loc[idx, "Last_UL_Milestone"] = float(ms)
                    break

            if ul_addition > 0:
                df.loc[idx, "Addition_UL"] = curr_add + ul_addition

        except Exception:
            pass

    return df


def render_fy_rollover_simulator(scoped_staff_df, is_admin=True, key_prefix="sys_sim"):
    fy_start, _, fy_label = get_current_financial_year_dates()
    next_fy_start = date(fy_start.year + 1, 4, 1)
    next_fy_lbl = f"FY {fy_start.year + 1}-{fy_start.year + 2}"

    has_snap = "_pre_rollover_test_snapshot" in st.session_state
    if has_snap:
        active_test_fy = st.session_state.get("_applied_rollover_target_fy", next_fy_lbl)
        st.success(
            f"🟢 **Live Sheet Test Rollover (`{format_fy_short(fy_label)}` ➔ `{format_fy_short(active_test_fy)}`) is currently ACTIVE on `Staff_Master`!** "
            f"You can inspect the rolled-over `Cumulative_UL` and balances across the Master Balance Table, Employee Dashboard, and Google Sheets, "
            f"or click **'⏪ Restore Pre-Test FY Snapshot'** below to revert to `{format_fy_short(fy_label)}`."
        )

    st.caption(
        f"Simulate or test the automatic midnight March 31 ➔ April 1 rollover (`{format_fy_short(fy_label)}` ➔ `{format_fy_short(next_fy_lbl)}`) "
        f"to verify how `Used_UL` consolidates into `Cumulative_UL`, how multi-FY UL carries forward, and how `CL`, `SL`, `AL` (capped at 180d), `UL`, and 5/10/15/20-year milestones are calculated."
    )
    c_sim1, c_sim2 = st.columns([1.8, 2.2])
    with c_sim1:
        sim_test_ul_days = st.number_input(
            "Optional: Simulate extra `Used_UL` in current FY before rollover (days)",
            min_value=0.0,
            max_value=365.0,
            value=0.0,
            step=5.0,
            key=f"{key_prefix}_rollover_extra_ul",
            help="Set > 0 (e.g. 15 days) to see `Used_UL` (capped at each staff member's available Balance_UL) added into `Cumulative_UL`.",
        )
    with c_sim2:
        sim_target_date = st.date_input(
            "Target Rollover Evaluation Date (April 1 of Incoming FY)",
            value=next_fy_start,
            min_value=next_fy_start,
            key=f"{key_prefix}_rollover_eval_date",
        )

    if scoped_staff_df is not None and not scoped_staff_df.empty:
        snap_df_existing = st.session_state.get("_pre_rollover_test_snapshot")
        if has_snap and snap_df_existing is not None and not snap_df_existing.empty:
            scoped_names = set(scoped_staff_df["Name"].astype(str).str.strip().tolist())
            base_df = snap_df_existing[snap_df_existing["Name"].astype(str).str.strip().isin(scoped_names)].copy()
            if base_df.empty:
                base_df = scoped_staff_df.copy()
        else:
            base_df = scoped_staff_df.copy()

        sim_input_df = base_df.copy()
        for c in ("Used_UL", "Balance_UL"):
            if c in sim_input_df.columns:
                sim_input_df[c] = sim_input_df[c].astype(object)
        if sim_test_ul_days > 0:
            for s_idx in sim_input_df.index:
                cur_u_ul = _safe_num(sim_input_df.at[s_idx, "Used_UL"]) if "Used_UL" in sim_input_df.columns else 0.0
                cur_b_ul = _safe_num(sim_input_df.at[s_idx, "Balance_UL"]) if "Balance_UL" in sim_input_df.columns else 0.0
                eff_sim_ul = min(float(sim_test_ul_days), max(0.0, cur_b_ul))
                sim_input_df.at[s_idx, "Used_UL"] = cur_u_ul + eff_sim_ul
                sim_input_df.at[s_idx, "Balance_UL"] = max(0.0, cur_b_ul - eff_sim_ul)

        req_df_sim = load_leave_requests()
        rolled_sim_df, sim_info = process_auto_fy_rollover_df(
            sim_input_df, as_of_date=sim_target_date, leave_df=req_df_sim
        )
        synced_sim_df = inject_balance_formulas(
            rolled_sim_df, df_req_override=req_df_sim, reference_date=sim_target_date
        )

        preview_rows = []
        for s_idx in sim_input_df.index:
            pre_r = sim_input_df.loc[s_idx]
            post_r = synced_sim_df.loc[s_idx]
            preview_rows.append({
                "Name": pre_r.get("Name", ""),
                "Prev FY ➔ New FY": f"{format_fy_short(pre_r.get('Active_FY', fy_label))} ➔ {format_fy_short(post_r.get('Active_FY', ''))}",
                "Prev Used_UL": _safe_num(pre_r.get("Used_UL", 0.0)),
                "Prev Cum_UL": _safe_num(pre_r.get("Cumulative_UL", 0.0)),
                "New Cumulative_UL ✅": _safe_num(post_r.get("Cumulative_UL", 0.0)),
                "New FY Used_UL": _safe_num(post_r.get("Used_UL", 0.0)),
                "Milestone +UL": _safe_num(post_r.get("Addition_UL", 0.0)),
                "New Balance_UL": _safe_num(post_r.get("Balance_UL", 0.0)),
                "Prev AL ➔ New AL (≤180)": f"{_safe_num(pre_r.get('Balance_AL', 0.0)):g} ➔ {_safe_num(post_r.get('Balance_AL', 0.0)):g}",
                "New CL": _safe_num(post_r.get("Balance_CL", 0.0)),
                "New SL": _safe_num(post_r.get("Balance_SL", 0.0)),
                "Actual Service Yr": _safe_num(post_r.get("Actual Service Year", 0.0)),
                "UL Milestone": _safe_num(post_r.get("Last_UL_Milestone", 0.0)),
            })
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

        if is_admin or DEV_MODE:
            c_act1, c_act2 = st.columns(2)
            with c_act1:
                if st.button(
                    f"⚡ Apply Test FY Rollover ({format_fy_short(sim_info.get('target_fy', next_fy_lbl))})",
                    type="primary",
                    use_container_width=True,
                    key=f"{key_prefix}_btn_apply_live_rollover_test",
                ):
                    fresh_sm = st.session_state.get("_pre_rollover_test_snapshot")
                    if fresh_sm is None or fresh_sm.empty:
                        fresh_sm = load_staff_master()
                    if fresh_sm is not None and not fresh_sm.empty:
                        if "_pre_rollover_test_snapshot" not in st.session_state:
                            st.session_state["_pre_rollover_test_snapshot"] = fresh_sm.copy()
                        test_in = st.session_state["_pre_rollover_test_snapshot"].copy()
                        for c in ("Used_UL", "Balance_UL"):
                            if c in test_in.columns:
                                test_in[c] = test_in[c].astype(object)
                        if sim_test_ul_days > 0:
                            for s_idx in test_in.index:
                                cu = _safe_num(test_in.at[s_idx, "Used_UL"])
                                cb = _safe_num(test_in.at[s_idx, "Balance_UL"])
                                eff_u = min(float(sim_test_ul_days), max(0.0, cb))
                                test_in.at[s_idx, "Used_UL"] = cu + eff_u
                                test_in.at[s_idx, "Balance_UL"] = max(0.0, cb - eff_u)
                        rolled_live, live_info = process_auto_fy_rollover_df(
                            test_in, as_of_date=sim_target_date, leave_df=req_df_sim
                        )
                        synced_live = inject_balance_formulas(
                            rolled_live, df_req_override=req_df_sim, reference_date=sim_target_date
                        )
                        _gsheets_retry(conn.update, worksheet="Staff_Master", data=synced_live)
                        st.session_state["_applied_rollover_target_fy"] = live_info.get("target_fy", next_fy_lbl)
                        _invalidate_sheet_cache("Staff_Master")
                        st.success(
                            f"✅ Live Test FY Rollover applied ({format_fy_short(live_info.get('target_fy', next_fy_lbl))})! "
                            f"Click '⏪ Restore Pre-Test FY Snapshot' anytime to revert to `{format_fy_short(fy_label)}`."
                        )
                        st.rerun()
            with c_act2:
                if st.button(
                    "⏪ Restore Pre-Test FY Snapshot",
                    disabled=not has_snap,
                    use_container_width=True,
                    key=f"{key_prefix}_btn_restore_pre_rollover_snapshot",
                ):
                    snap_df = st.session_state.get("_pre_rollover_test_snapshot")
                    if snap_df is not None and not snap_df.empty:
                        _gsheets_retry(conn.update, worksheet="Staff_Master", data=snap_df)
                        del st.session_state["_pre_rollover_test_snapshot"]
                        st.session_state.pop("_applied_rollover_target_fy", None)
                        _invalidate_sheet_cache("Staff_Master")
                        st.success("✅ Restored Staff_Master to exact pre-test snapshot!")
                        st.rerun()


def render_master_balance_table():
    st.subheader("Master Balance Table")
    staff_df = load_staff_master()
    allowed_branches = get_active_visible_branches()
    branch_map = get_user_branch_map()
    if allowed_branches:
        st.caption(f"🔎 Showing staff for branch(es): **{', '.join(allowed_branches)}**")
    scoped_staff_df = filter_df_by_branches(staff_df, allowed_branches, branch_map, name_col="Name")
    if scoped_staff_df is not None and not scoped_staff_df.empty:
        df_req = load_leave_requests()
        rules_override = get_effective_leave_rules()
        req_by_name = {}
        empty_req = pd.DataFrame(columns=df_req.columns) if df_req is not None else pd.DataFrame()
        if df_req is not None and not df_req.empty and "Name" in df_req.columns:
            req_sub = df_req[df_req["Status"].astype(str).str.strip() == "Approved"] if "Status" in df_req.columns else df_req
            if not req_sub.empty:
                for emp_k, grp in req_sub.groupby(req_sub["Name"].astype(str).str.strip(), sort=False):
                    req_by_name[emp_k] = grp
        balances_list = []
        for _, s_row in scoped_staff_df.iterrows():
            name = s_row["Name"]
            name_key = str(name).strip()
            bal = calculate_balances(
                name,
                user_row_override=s_row,
                df_req_override=req_by_name.get(name_key, empty_req),
                rules_override=rules_override,
            )
            balances_list.append({
                "Name": name,
                "Branch": branch_map.get(str(name).strip(), "HQ"),
                "CL": bal["CL"],
                "SL": bal["SL"],
                "AL": bal["AL"],
                "UL": bal["UL"],
                "Used_UL": bal.get("Used_UL", 0.0),
                "Cumulative_UL": bal.get("Cumulative_UL", 0.0),
                "Actual_Service_Year": bal.get("Actual_Service_Year", 0.0),
                "Active_FY": format_fy_short(bal.get("Active_FY", "")),
            })
        st.dataframe(pd.DataFrame(balances_list),
                    width="stretch", hide_index=True)
        viewer_role = st.session_state.get("user_role", "")
        if viewer_role in ("Admin", "Accounts") or DEV_MODE:
            with st.expander("🧪 Test & Verify Automatic FY Rollover (Cumulative UL & Balance Check)", expanded=False):
                render_fy_rollover_simulator(
                    scoped_staff_df,
                    is_admin=(viewer_role == "Admin"),
                    key_prefix=f"mbt_sim_{viewer_role}",
                )
    else:
        st.info("No staff data found for the selected branch(es).")


def _ensure_sys_settings_branch_state(viewer_name):
    """Initializes independent System Settings branch checkboxes (HQ, DB, NB, SB) decoupled from the Calendar filter."""
    owner_key = str(viewer_name or "").strip()
    state = st.session_state.get("sys_settings_branch_filter_state")
    if not isinstance(state, dict) or state.get("owner") != owner_key:
        st.session_state["sys_settings_branch_filter_state"] = {
            "owner": owner_key,
            "HQ": True,
            "DB": True,
            "NB": True,
            "SB": True,
        }
        for k in list(st.session_state.keys()):
            if str(k).startswith("bf_sys_chk_"):
                del st.session_state[k]
    return st.session_state["sys_settings_branch_filter_state"]


def _on_sys_settings_checkbox_change(widget_key, branch_code):
    state = _ensure_sys_settings_branch_state(st.session_state.get("user_name", ""))
    state[branch_code] = bool(st.session_state.get(widget_key, True))


def render_system_settings_branch_filter(key_prefix="sys_settings"):
    """Renders an independent Branch Filter (HQ, DB, NB, SB) inside System Settings for Central Admin & Central Accounts."""
    viewer_name = st.session_state.get("user_name", "")
    viewer_role = st.session_state.get("user_role", "Employee")
    viewer_branch = get_user_branch(viewer_name) if viewer_name else "HQ"
    if not is_central_branch_filter_role(viewer_role, viewer_branch):
        return resolve_visible_branches(viewer_role, viewer_branch)

    sys_state = _ensure_sys_settings_branch_state(viewer_name)
    safe_user = "".join(ch if ch.isalnum() else "_" for ch in str(viewer_name))

    with st.container(border=True):
        cols = st.columns([1.4, 1, 1, 1, 1])
        with cols[0]:
            st.markdown("**Branch Filter:**")
        for idx, b_code in enumerate(BRANCH_FILTER_ORDER):
            wkey = f"bf_sys_chk_{key_prefix}_{safe_user}_{b_code}"
            desired_val = bool(sys_state.get(b_code, True))
            if wkey not in st.session_state:
                st.session_state[wkey] = desired_val
            with cols[idx + 1]:
                st.checkbox(
                    BRANCH_FILTER_LABELS.get(b_code, b_code),
                    key=wkey,
                    help=BRANCH_NAMES.get(b_code, b_code),
                    on_change=_on_sys_settings_checkbox_change,
                    args=(wkey, b_code),
                )
                # Sync state directly in case widget was toggled in a non-callback runner
                sys_state[b_code] = bool(st.session_state.get(wkey, desired_val))

    checked = [b for b in BRANCH_FILTER_ORDER if sys_state.get(b, True)]
    if not checked:
        st.warning("No branches selected. Check དབུས། (HQ), DB, NB, or SB above to view staff records in System Settings.")
    return resolve_visible_branches(viewer_role, viewer_branch, checked)


def render_service_leave_duration_calculator(key_prefix="svc_calc"):
    """Renders a standalone multi-period Date-Range Duration Calculator (Start Date to End Date with '+ Add Another Period')
    right below 'Current Year Leave affecting Service Year' and 'Cummulative leave effecting Service year.(Education Leave & Etc).'.
    Pure helper calculator — does not overwrite any Staff_Master field automatically.
    """
    count_key = f"{key_prefix}_period_count"
    if count_key not in st.session_state:
        st.session_state[count_key] = 1

    with st.expander("🧮 Leave Period Duration & Year Calculator (Start Date ➔ End Date Helper)", expanded=False):
        st.caption(
            "Use this standalone calculator to add one or multiple leave periods (**Start Date** to **End Date**, inclusive) "
            "and see the exact **Total Days** and **Total Years** (`Days ÷ 365.25`) so you can easily type the number above."
        )
        total_days = 0
        period_summaries = []
        num_periods = max(1, int(st.session_state.get(count_key, 1)))

        for i in range(num_periods):
            c_p1, c_p2, c_p3 = st.columns([1.8, 1.8, 2.4])
            with c_p1:
                sd = st.date_input(
                    f"Period #{i + 1} Start Date",
                    value=get_cta_today(),
                    min_value=date(1960, 1, 1),
                    max_value=date(2040, 12, 31),
                    key=f"{key_prefix}_sd_{i}",
                )
            with c_p2:
                ed = st.date_input(
                    f"Period #{i + 1} End Date",
                    value=get_cta_today(),
                    min_value=date(1960, 1, 1),
                    max_value=date(2040, 12, 31),
                    key=f"{key_prefix}_ed_{i}",
                )
            with c_p3:
                st.write("")
                if isinstance(sd, date) and isinstance(ed, date):
                    if ed >= sd:
                        p_days = (ed - sd).days + 1
                        p_yrs = p_days / 365.25
                        total_days += p_days
                        period_summaries.append((i + 1, sd, ed, p_days, p_yrs))
                        st.info(f"**Period #{i + 1}:** `{p_days:,} day(s)`  •  **`{p_yrs:.2f} year(s)`**")
                    else:
                        st.warning(f"⚠️ Period #{i + 1}: End Date is before Start Date")

        b_c1, b_c2, _ = st.columns([1.4, 1.4, 2.2])
        with b_c1:
            if st.button("➕ Add Another Period", key=f"{key_prefix}_btn_add_period", use_container_width=True):
                st.session_state[count_key] = num_periods + 1
                st.rerun()
        with b_c2:
            if num_periods > 1:
                if st.button("➖ Remove Last Period", key=f"{key_prefix}_btn_rem_period", use_container_width=True):
                    idx_rem = num_periods - 1
                    st.session_state.pop(f"{key_prefix}_sd_{idx_rem}", None)
                    st.session_state.pop(f"{key_prefix}_ed_{idx_rem}", None)
                    st.session_state[count_key] = max(1, num_periods - 1)
                    st.rerun()

        total_years = total_days / 365.25
        approx_yrs = int(total_days // 365.25)
        rem_days_after_yr = total_days - int(round(approx_yrs * 365.25))
        approx_months = int(max(0, rem_days_after_yr) // 30.4375)
        approx_rem_days = int(round(max(0, rem_days_after_yr) - (approx_months * 30.4375)))

        st.success(
            f"📊 **Calculator Result ({len(period_summaries)} period(s)):**  "
            f"**Total Days = `{total_days:,} days`**   |   "
            f"**Total Years = `{total_years:.2f} years`** "
            f"*(≈ {approx_yrs} yr, {approx_months} mo, {approx_rem_days} d)*\n\n"
            f"💡 *Note: The `Staff_Master` fields above store **Total Days** (e.g. enter `{total_days}` for `{total_years:.2f} years`).*"
        )


def render_system_settings(is_admin=True):
    st.subheader("⚙️ System Settings & Administration")
    st.caption("Complete administrative control panel for leave rules, staff profiles, balance adjustments, and offline backups.")

    branch_map = get_user_branch_map()
    viewer_name = st.session_state.get("user_name", "")
    viewer_role = st.session_state.get("user_role", "")
    viewer_branch = get_user_branch(viewer_name) if viewer_name else "HQ"
    is_central_viewer = is_central_branch_filter_role(viewer_role, viewer_branch)

    allowed_branches = render_system_settings_branch_filter(
        key_prefix=f"sys_set_{'adm' if is_admin else 'acct'}"
    )
    if allowed_branches:
        st.caption(f"🔎 Active Branch Scope: **{format_branch_scope(allowed_branches)}**")

    users_df = load_users_data()
    staff_df = load_staff_master()
    scoped_users_df = filter_df_by_branches(users_df, allowed_branches, branch_map, name_col="Name")
    scoped_staff_df = filter_df_by_branches(staff_df, allowed_branches, branch_map, name_col="Name")

    tab_rules, tab_users, tab_stmt, tab_adj, tab_ntfy, tab_backup = st.tabs([
        "📜 Leave Rules & Quotas",
        "👥 Staff Directory & Users",
        "📑 Statements & Encashment",
        "🛠️ Balance Adjustments",
        "🔔 Notifications & Mobile",
        "💾 1-Click System Backup"
    ])

    # --- TAB 1: RULES & QUOTAS ---
    with tab_rules:
        fy_start, fy_end, fy_label = get_current_financial_year_dates()
        active_rules = load_leave_rules()
        lt_cfg = active_rules.get("leave_types", {})
        gl_cfg = active_rules.get("global", {})

        cl_r = lt_cfg.get("CL", {})
        sl_r = lt_cfg.get("SL", {})
        al_r = lt_cfg.get("AL", {})
        ul_r = lt_cfg.get("UL", {})
        ot_r = lt_cfg.get("Other", {})

        st.info(
            f"🗓️ **Active Financial Year:** `{format_fy_short(fy_label)}` ({fy_start} to {fy_end})\n\n"
            f"Automatic FY Rollover runs silently at **midnight of 31 March IST (00:00 IST on 1 April)** — applying your configured **General Leave Rules & Policy Engine** below across all staff balances, accumulation caps, pro-rata UL penalties, and service tenure milestones."
        )

        rules_flash = st.session_state.pop("_leave_rules_flash_msg", None)
        if rules_flash:
            st.success(rules_flash)

        st.markdown("#### ⚖️ Active CTA Leave Allowances, Caps & Tenure Summary")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric(
            "Casual Leave (CL)",
            f"{float(cl_r.get('Annual_Quota', 9.0)):.1f} Days / FY",
            delta="Accumulated (Carries Forward)" if cl_r.get("Is_Accumulated", False) else "Non-Accumulated (Resets Apr 1)",
            delta_color="normal" if cl_r.get("Is_Accumulated", False) else "off",
            help=LEAVE_RULE_FIELD_HELP["Annual_Quota"],
        )
        r2.metric(
            "Sick Leave (SL)",
            f"{float(sl_r.get('Annual_Quota', 30.0)):.1f} Days / FY",
            delta="Accumulated (Carries Forward)" if sl_r.get("Is_Accumulated", False) else "Non-Accumulated (Resets Apr 1)",
            delta_color="normal" if sl_r.get("Is_Accumulated", False) else "off",
            help=LEAVE_RULE_FIELD_HELP["Annual_Quota"],
        )
        r3.metric(
            "Annual Leave (AL)",
            f"{float(al_r.get('Annual_Quota', 30.0)):.1f} Days / FY",
            delta="Accumulated (Carries Forward)" if al_r.get("Is_Accumulated", True) else "Non-Accumulated (Resets Apr 1)",
            delta_color="normal" if al_r.get("Is_Accumulated", True) else "off",
            help=LEAVE_RULE_FIELD_HELP["Annual_Quota"],
        )
        al_cap_val = float(al_r.get("Max_Accumulation_Cap", 180.0))
        r4.metric(
            "AL Max Cap",
            f"{al_cap_val:.1f} Days" if al_cap_val > 0 else "No Cap",
            help=LEAVE_RULE_FIELD_HELP["Max_Accumulation_Cap"],
        )

        p1, p2, p3, p4 = st.columns(4)
        ms_list_asc = sorted(get_ul_milestone_years_desc(active_rules))
        ms_str = ", ".join(f"{m:g}" for m in ms_list_asc) if ms_list_asc else "5, 10, 15, 20"
        p1.metric(
            f"UL {float(gl_cfg.get('UL_Milestone_Interval_Years', 5.0)):g}-Yr Milestone Grant",
            f"{float(gl_cfg.get('UL_Grant_Per_Milestone', 182.5)):.1f} Days",
            help=f"Granted at {ms_str} actual service years. {LEAVE_RULE_FIELD_HELP['UL_Grant_Per_Milestone']}",
        )
        p2.metric(
            "UL Lifetime Maximum",
            f"{float(gl_cfg.get('UL_Career_Max_Cap', 730.0)):.1f} Days",
            help=LEAVE_RULE_FIELD_HELP["UL_Career_Max_Cap"],
        )
        p3.metric(
            "Service Freeze Rule",
            "Active" if gl_cfg.get("Service_Freeze_Rule", True) else "Disabled",
            help=LEAVE_RULE_FIELD_HELP["Service_Freeze_Rule"],
        )
        p4.metric(
            "Auto Pro-Rata UL Penalty",
            "Active" if gl_cfg.get("Auto_Apply_Pro_Rata_UL_Penalty", False) else "Manual / Sheet",
            help=LEAVE_RULE_FIELD_HELP["Auto_Apply_Pro_Rata_UL_Penalty"],
        )

        st.caption(
            f"📌 **Active Pro-Rata UL Penalty Formula:** For every 30 days of Unpaid Leave (UL) taken in a year, staff lose "
            f"`{float(cl_r.get('Pro_Rata_UL_Penalty', 0.75)):.2f}d` CL, "
            f"`{float(sl_r.get('Pro_Rata_UL_Penalty', 2.50)):.2f}d` SL, and "
            f"`{float(al_r.get('Pro_Rata_UL_Penalty', 2.50)):.2f}d` AL."
        )

        st.divider()
        st.markdown("#### 🛠️ Editable General Leave Rules & Policy Configuration Engine")
        st.caption(
            "Configure universal policy rules for all leave categories (`CL`, `SL`, `AL`, `UL`, `Other`). "
            "Hover or click the **`(?)`** icon next to any rule or number to view its **Note & Exact Mathematical Formula**."
        )

        can_edit_rules = bool(is_admin or is_central_viewer or DEV_MODE)
        if not can_edit_rules:
            st.warning("🔒 View-Only Mode: Only Central Admin and Central Accounts can modify global leave rules.")

        edited_leave_types = {}
        leave_type_meta = [
            ("CL", "🟦 Casual Leave (CL)", "Short-term personal leave (Default: 9.0d/FY, non-accumulated, max 5 consecutive days)."),
            ("SL", "🟩 Sick Leave (SL)", "Medical / health leave (Default: 30.0d/FY, non-accumulated, no strict balance block)."),
            ("AL", "🟨 Annual Leave (AL / Earned Leave)", "Earned leave that carries forward across FYs (Default: 30.0d/FY, accumulated up to 180.0d cap, encashable)."),
            ("UL", "🟪 Unpaid Leave (UL / Extraordinary Leave)", "Milestone-granted leave without pay (Default: +182.5d every 5 actual service years, 730.0d career cap, delays service tenure)."),
            ("Other", "🟧 Other / Education & Special Leave", "Special, study, or maternity/paternity leave (Default: delays service tenure when marked as service-affecting)."),
        ]

        for lt_key, lt_title, lt_desc in leave_type_meta:
            cur_lt = lt_cfg.get(lt_key, get_default_leave_rules()["leave_types"].get(lt_key, {}))
            with st.container(border=True):
                c_hdr1, c_hdr2 = st.columns([2.2, 2.8])
                with c_hdr1:
                    st.markdown(f"##### {lt_title}")
                    st.caption(lt_desc)
                with c_hdr2:
                    accum_badge = "Carries Forward (`Opening = Prior Balance`)" if cur_lt.get("Is_Accumulated") else "Resets Every FY (`Opening = 0.0`)"
                    cap_v = float(cur_lt.get("Max_Accumulation_Cap", 0.0))
                    cap_badge = f"Cap ≤ `{cap_v:g}d`" if cap_v > 0 else "No Cap"
                    svc_badge = "Delays Service Year (`+Penalty`)" if cur_lt.get("Affects_Service_Year") else "No Service Year Penalty"
                    st.info(
                        f"**Live Rule Formula (`{lt_key}`):** {accum_badge} • {cap_badge} • {svc_badge}\n\n"
                        f"`Balance_{lt_key} = max(0, Opening_{lt_key} + Addition_{lt_key} - Penalty_{lt_key} - Used_{lt_key})"
                        + (f" - Encashed_{lt_key}" if cur_lt.get("Allow_Encashment") else "")
                        + (f"` *(capped at `{cap_v:g}d`)*" if cap_v > 0 else "`")
                    )

                rc1, rc2, rc3, rc4 = st.columns(4)
                with rc1:
                    val_quota = st.number_input(
                        f"{lt_key}: Annual Addition / Quota (Days/FY)",
                        min_value=0.0,
                        max_value=365.0,
                        value=float(cur_lt.get("Annual_Quota", 0.0)),
                        step=0.5,
                        help=LEAVE_RULE_FIELD_HELP["Annual_Quota"],
                        disabled=not can_edit_rules,
                        key=f"rule_quota_{lt_key}",
                    )
                with rc2:
                    val_cap = st.number_input(
                        f"{lt_key}: Max Accumulation Cap (0 = No Cap)",
                        min_value=0.0,
                        max_value=3650.0,
                        value=float(cur_lt.get("Max_Accumulation_Cap", 0.0)),
                        step=1.0,
                        help=LEAVE_RULE_FIELD_HELP["Max_Accumulation_Cap"],
                        disabled=not can_edit_rules,
                        key=f"rule_cap_{lt_key}",
                    )
                with rc3:
                    val_ul_pen = st.number_input(
                        f"{lt_key}: Pro-Rata UL Penalty (/30d UL)",
                        min_value=0.0,
                        max_value=30.0,
                        value=float(cur_lt.get("Pro_Rata_UL_Penalty", 0.0)),
                        step=0.25,
                        format="%.2f",
                        help=LEAVE_RULE_FIELD_HELP["Pro_Rata_UL_Penalty"],
                        disabled=not can_edit_rules,
                        key=f"rule_ulpen_{lt_key}",
                    )
                with rc4:
                    val_max_consec = st.number_input(
                        f"{lt_key}: Max Consecutive Days (0 = No Limit)",
                        min_value=0.0,
                        max_value=3650.0,
                        value=float(cur_lt.get("Max_Consecutive_Days", 0.0)),
                        step=0.5,
                        help=LEAVE_RULE_FIELD_HELP["Max_Consecutive_Days"],
                        disabled=not can_edit_rules,
                        key=f"rule_consec_{lt_key}",
                    )

                tc1, tc2, tc3, tc4, tc5, tc6 = st.columns(6)
                with tc1:
                    val_accum = st.checkbox(
                        "Is Accumulated (Carry Over)?",
                        value=bool(cur_lt.get("Is_Accumulated", False)),
                        help=LEAVE_RULE_FIELD_HELP["Is_Accumulated"],
                        disabled=not can_edit_rules,
                        key=f"rule_accum_{lt_key}",
                    )
                with tc2:
                    val_aff_svc = st.checkbox(
                        "Affects Service Year?",
                        value=bool(cur_lt.get("Affects_Service_Year", False)),
                        help=LEAVE_RULE_FIELD_HELP["Affects_Service_Year"],
                        disabled=not can_edit_rules,
                        key=f"rule_affsvc_{lt_key}",
                    )
                with tc3:
                    val_earn_ul = st.checkbox(
                        "Earned on Full-Yr UL?",
                        value=bool(cur_lt.get("Earned_During_Full_Year_UL", False)),
                        help=LEAVE_RULE_FIELD_HELP["Earned_During_Full_Year_UL"],
                        disabled=not can_edit_rules,
                        key=f"rule_earnul_{lt_key}",
                    )
                with tc4:
                    val_encash = st.checkbox(
                        "Allow Encashment?",
                        value=bool(cur_lt.get("Allow_Encashment", False)),
                        help=LEAVE_RULE_FIELD_HELP["Allow_Encashment"],
                        disabled=not can_edit_rules,
                        key=f"rule_encash_{lt_key}",
                    )
                with tc5:
                    val_strict_bal = st.checkbox(
                        "Strict Balance Check?",
                        value=bool(cur_lt.get("Strict_Balance_Required", False)),
                        help=LEAVE_RULE_FIELD_HELP["Strict_Balance_Required"],
                        disabled=not can_edit_rules,
                        key=f"rule_strict_{lt_key}",
                    )
                with tc6:
                    val_non_combo = st.checkbox(
                        "No Direct Combining?",
                        value=bool(cur_lt.get("Non_Combinable", False)),
                        help=LEAVE_RULE_FIELD_HELP["Non_Combinable"],
                        disabled=not can_edit_rules,
                        key=f"rule_combo_{lt_key}",
                    )

                edited_leave_types[lt_key] = {
                    "Annual_Quota": float(val_quota),
                    "Is_Accumulated": bool(val_accum),
                    "Max_Accumulation_Cap": float(val_cap),
                    "Affects_Service_Year": bool(val_aff_svc),
                    "Earned_During_Full_Year_UL": bool(val_earn_ul),
                    "Allow_Encashment": bool(val_encash),
                    "Pro_Rata_UL_Penalty": float(val_ul_pen),
                    "Strict_Balance_Required": bool(val_strict_bal),
                    "Max_Consecutive_Days": float(val_max_consec),
                    "Non_Combinable": bool(val_non_combo),
                }

        with st.container(border=True):
            st.markdown("##### ⏳ Global Unpaid Leave (UL) Milestone & Service Tenure Rules")
            st.caption(
                "Configure how Actual Service Years trigger Unpaid Leave milestone grants, the career UL ceiling, and whether active leave freezes tenure smoothly."
            )
            gc1, gc2, gc3, gc4 = st.columns(4)
            with gc1:
                g_ms_interval = st.number_input(
                    "UL Milestone Interval (Years)",
                    min_value=1.0,
                    max_value=50.0,
                    value=float(gl_cfg.get("UL_Milestone_Interval_Years", 5.0)),
                    step=1.0,
                    help=LEAVE_RULE_FIELD_HELP["UL_Milestone_Interval_Years"],
                    disabled=not can_edit_rules,
                    key="rule_gl_ms_interval",
                )
            with gc2:
                g_ms_max = st.number_input(
                    "UL Max Milestone Tenure (Years)",
                    min_value=1.0,
                    max_value=60.0,
                    value=float(gl_cfg.get("UL_Max_Milestone_Years", 20.0)),
                    step=5.0,
                    help=LEAVE_RULE_FIELD_HELP["UL_Max_Milestone_Years"],
                    disabled=not can_edit_rules,
                    key="rule_gl_ms_max",
                )
            with gc3:
                g_ms_grant = st.number_input(
                    "UL Grant Per Milestone (Days)",
                    min_value=0.0,
                    max_value=1000.0,
                    value=float(gl_cfg.get("UL_Grant_Per_Milestone", 182.5)),
                    step=0.5,
                    help=LEAVE_RULE_FIELD_HELP["UL_Grant_Per_Milestone"],
                    disabled=not can_edit_rules,
                    key="rule_gl_ms_grant",
                )
            with gc4:
                g_ul_career_cap = st.number_input(
                    "UL Career Maximum Cap (Days)",
                    min_value=0.0,
                    max_value=3650.0,
                    value=float(gl_cfg.get("UL_Career_Max_Cap", 730.0)),
                    step=10.0,
                    help=LEAVE_RULE_FIELD_HELP["UL_Career_Max_Cap"],
                    disabled=not can_edit_rules,
                    key="rule_gl_ul_career_cap",
                )

            gt1, gt2 = st.columns(2)
            with gt1:
                g_svc_freeze = st.checkbox(
                    "Freeze Service Year During Active Leave (Service Freeze Rule — tenure never regresses backwards)",
                    value=bool(gl_cfg.get("Service_Freeze_Rule", True)),
                    help=LEAVE_RULE_FIELD_HELP["Service_Freeze_Rule"],
                    disabled=not can_edit_rules,
                    key="rule_gl_svc_freeze",
                )
            with gt2:
                g_auto_ul_pen = st.checkbox(
                    "Automatically Apply Pro-Rata UL Penalties to Penalty_CL / Penalty_SL / Penalty_AL",
                    value=bool(gl_cfg.get("Auto_Apply_Pro_Rata_UL_Penalty", False)),
                    help=LEAVE_RULE_FIELD_HELP["Auto_Apply_Pro_Rata_UL_Penalty"],
                    disabled=not can_edit_rules,
                    key="rule_gl_auto_ul_pen",
                )

        if can_edit_rules:
            b_col1, b_col2 = st.columns([2, 1])
            with b_col1:
                if st.button(
                    "💾 Save Leave Rules & Policy Configuration",
                    type="primary",
                    use_container_width=True,
                    key="btn_save_leave_rules_cfg",
                ):
                    new_rules_payload = normalize_leave_rules({
                        "leave_types": edited_leave_types,
                        "global": {
                            "UL_Milestone_Interval_Years": float(g_ms_interval),
                            "UL_Max_Milestone_Years": float(g_ms_max),
                            "UL_Grant_Per_Milestone": float(g_ms_grant),
                            "UL_Career_Max_Cap": float(g_ul_career_cap),
                            "Service_Freeze_Rule": bool(g_svc_freeze),
                            "Auto_Apply_Pro_Rata_UL_Penalty": bool(g_auto_ul_pen),
                        },
                    })
                    save_leave_rules(new_rules_payload)
                    if staff_df is not None and not staff_df.empty:
                        try:
                            _gsheets_retry(
                                conn.update,
                                worksheet="Staff_Master",
                                data=inject_balance_formulas(staff_df, rules_override=new_rules_payload),
                            )
                        except Exception:
                            pass
                    _invalidate_sheet_cache("LeaveRules", "Staff_Master")
                    st.session_state["_leave_rules_flash_msg"] = (
                        "✅ Saved General Leave Rules & Policy Configuration! All formulas, caps, and rollover rules are now active."
                    )
                    st.rerun()
            with b_col2:
                if st.button(
                    "🔄 Reset to Default CTA Rules",
                    use_container_width=True,
                    key="btn_reset_leave_rules_default",
                ):
                    default_rules_payload = get_default_leave_rules()
                    save_leave_rules(default_rules_payload)
                    for lt_k in ("CL", "SL", "AL", "UL", "Other"):
                        for prefix in (
                            "rule_quota_", "rule_cap_", "rule_ulpen_", "rule_consec_",
                            "rule_accum_", "rule_affsvc_", "rule_earnul_", "rule_encash_",
                            "rule_strict_", "rule_combo_"
                        ):
                            st.session_state.pop(f"{prefix}{lt_k}", None)
                    for gk in (
                        "rule_gl_ms_interval", "rule_gl_ms_max", "rule_gl_ms_grant",
                        "rule_gl_ul_career_cap", "rule_gl_svc_freeze", "rule_gl_auto_ul_pen"
                    ):
                        st.session_state.pop(gk, None)
                    _invalidate_sheet_cache("LeaveRules")
                    st.session_state["_leave_rules_flash_msg"] = (
                        "✅ Reset all Leave Rules & Quotas to official CTA defaults!"
                    )
                    st.rerun()

        with st.expander("📋 View Complete Active Rule Matrix Table", expanded=False):
            st.dataframe(leave_rules_to_df(active_rules), use_container_width=True, hide_index=True)

        st.write("---")
        st.markdown("#### 🧪 Test & Verify Automatic FY Rollover (Cumulative UL & Balance Verification)")
        render_fy_rollover_simulator(
            scoped_staff_df,
            is_admin=is_admin,
            key_prefix=f"sys_sim_{'adm' if is_admin else 'acct'}",
        )

    # --- TAB 2: STAFF & USERS ---
    with tab_users:
        users_df = load_users_data()
        staff_df = load_staff_master()
        scoped_users_df = filter_df_by_branches(users_df, allowed_branches, branch_map, name_col="Name")
        scoped_staff_df = filter_df_by_branches(staff_df, allowed_branches, branch_map, name_col="Name")
        active_rules_users = get_effective_leave_rules()
        lt_rules_u = active_rules_users.get("leave_types", {})
        gl_rules_u = active_rules_users.get("global", {})

        # Section 1: Quick Staff Gmail Manager (Accessible to Admin & Accounts)
        st.markdown("### ✉️ Staff Gmail Authorization Manager")
        st.caption("Admin and Accountants can link, register, or update authorized Gmail addresses for staff members in their scope.")

        if scoped_users_df is not None and not scoped_users_df.empty:
            c_gm1, c_gm2 = st.columns([1.5, 2.5])
            with c_gm1:
                u_names = scoped_users_df["Name"].tolist()
                sel_u_gmail = st.selectbox(
                    "Select Staff Member to Link Gmail",
                    u_names,
                    help=STAFF_MASTER_FIELD_HELP["Name"],
                    key="quick_sel_u_gmail",
                )
            
            sel_u_row = users_df[users_df["Name"] == sel_u_gmail].iloc[0]
            sel_u_idx = users_df[users_df["Name"] == sel_u_gmail].index[0]
            curr_gmail = str(sel_u_row.get("Email", "")).strip()

            with c_gm2:
                col_in, col_btn = st.columns([3, 1.2])
                with col_in:
                    new_gmail_val = st.text_input(
                        "Authorized Gmail Address",
                        value=curr_gmail,
                        placeholder="e.g. staff.name@gmail.com",
                        help=STAFF_MASTER_FIELD_HELP["Email"],
                        key=f"quick_new_gmail_in_{sel_u_gmail}"
                    )
                with col_btn:
                    st.write("")
                    st.write("")
                    save_gmail_btn = st.button("💾 Save Gmail", type="primary", use_container_width=True, key=f"btn_quick_save_gmail_{sel_u_gmail}")

            if save_gmail_btn:
                clean_email = str(new_gmail_val).strip().lower()
                if not clean_email or "@" not in clean_email:
                    st.error("Please enter a valid Gmail address (e.g. user@gmail.com).")
                else:
                    with st.spinner(f"Updating Gmail for {sel_u_gmail}..."):
                        users_df.at[sel_u_idx, "Email"] = clean_email
                        _gsheets_retry(conn.update, worksheet="Users", data=users_df)
                        _invalidate_sheet_cache("Users")
                        st.success(f"✅ Successfully linked Gmail '{clean_email}' to {sel_u_gmail}!")
                        st.rerun()

        st.divider()

        # Section A0: Quick Add Auditor Account (Admin & Central HQ Accounts only — No Staff_Master Leave/Tour Balances)
        if is_admin or is_central_viewer:
            with st.expander("🕵️ Add Auditor Account (Read-Only Oversight — No Leave/Tour Balance)", expanded=False):
                st.caption(
                    "For **Admin** & **Central Account (HQ)**: Register an Auditor using their authorized Gmail address and a 4-digit PIN. "
                    "Auditors **do not** have leave or tour balances and are never added to `Staff_Master`."
                )
                with st.form("admin_add_auditor_form"):
                    c_au1, c_au2 = st.columns(2)
                    with c_au1:
                        aud_name = st.text_input("Auditor Name *", placeholder="e.g. Audit Observer / རྩིས་ཞིབ་པ།", help=STAFF_MASTER_FIELD_HELP["Name"])
                        aud_post = st.text_input("Designation / Title *", value="Auditor (རྩིས་ཞིབ་པ།)", help=STAFF_MASTER_FIELD_HELP["Post"])
                    with c_au2:
                        aud_email = st.text_input("Authorized Gmail Address *", placeholder="e.g. auditor@gmail.com", help=STAFF_MASTER_FIELD_HELP["Email"])
                        aud_pin = st.text_input("Initial 4-Digit PIN *", type="password", max_chars=4, help=STAFF_MASTER_FIELD_HELP["PIN"])
                    if st.form_submit_button("🕵️ Create Auditor Account (Gmail Only)", type="primary"):
                        if not aud_name.strip() or not aud_post.strip():
                            st.error("Auditor Name and Designation are required.")
                        elif not aud_email.strip() or "@" not in aud_email:
                            st.error("A valid Gmail address is required for Auditor login.")
                        elif len(aud_pin.strip()) != 4 or not aud_pin.strip().isdigit():
                            st.error("PIN must be exactly 4 numeric digits.")
                        elif users_df is not None and aud_name.strip() in users_df["Name"].astype(str).str.strip().values:
                            st.error(f"User with name '{aud_name.strip()}' already exists!")
                        elif users_df is not None and aud_email.strip().lower() in users_df["Email"].astype(str).str.strip().str.lower().values:
                            st.error(f"A user with Gmail '{aud_email.strip().lower()}' already exists!")
                        else:
                            with st.spinner("Creating Auditor account..."):
                                aud_topic = generate_user_ntfy_topic()
                                new_aud_row = pd.DataFrame([{
                                    "Name": aud_name.strip(),
                                    "PIN": str(aud_pin.strip()),
                                    "Role": "Auditor",
                                    "Post": aud_post.strip(),
                                    "JoiningDate": "",
                                    "NtfyTopic": aud_topic,
                                    "Branch": "HQ",
                                    "Email": aud_email.strip().lower(),
                                }])
                                _gsheets_retry(conn.update, worksheet="Users", data=pd.concat([users_df, new_aud_row], ignore_index=True))
                                _invalidate_sheet_cache("Users")
                                st.success(f"✅ Created Auditor account for **{aud_name.strip()}** (`{aud_email.strip().lower()}`) with zero leave/tour balance!")
                                st.rerun()

        # Section A: Add New Staff (Complete Onboarding with Full Leave & Service Ledger)
        with st.expander("➕ Add New Staff Member & Initial Leave Ledger", expanded=False):
            st.markdown("##### 1. Staff Profile & Login Credentials")
            st.caption("Hover over any **`(?)`** icon to see the field's **Note & Formula**. Only **🟨 Manual Input** columns are editable; all **🔒 Formula** columns calculate automatically.")
            c_n1, c_n2 = st.columns(2)
            with c_n1:
                new_name = st.text_input(
                    "Full Name (Tibetan Font) *",
                    placeholder="e.g. བསྟན་འཛིན་བསྟན་དར།",
                    help=STAFF_MASTER_FIELD_HELP["Name"],
                    key="add_stf_name",
                )
                new_post = st.selectbox(
                    "Designation / Post (Tibetan Font) *",
                    OFFICIAL_CTA_POSTS,
                    help=STAFF_MASTER_FIELD_HELP["Post"],
                    key="add_stf_post",
                )
                new_join = st.date_input(
                    "Official Joining Date *",
                    value=get_cta_today(),
                    min_value=date(1960, 1, 1),
                    max_value=date(2035, 12, 31),
                    help=STAFF_MASTER_FIELD_HELP["JoiningDate"],
                    key="add_stf_join",
                )
                new_active_fy = st.text_input(
                    "Active Financial Year (Active_FY) 🔒",
                    value=get_current_financial_year_dates()[2],
                    help=STAFF_MASTER_FIELD_HELP["Active_FY"],
                    disabled=True,
                    key="add_stf_fy",
                )
            with c_n2:
                add_role_opts = (
                    ["Employee", "Co-Admin", "Accounts", "Branch Manager", "Auditor", "Admin"]
                    if is_admin
                    else (["Employee", "Co-Admin", "Accounts", "Branch Manager", "Auditor"] if is_central_viewer else ["Employee", "Accounts"])
                )
                add_branch_opts = ["HQ", "DB", "NB", "SB", "Other"] if is_central_viewer else [viewer_branch]
                new_role = st.selectbox(
                    "System Role *",
                    add_role_opts,
                    help=STAFF_MASTER_FIELD_HELP["Role"],
                    key="add_stf_role",
                )
                new_branch = st.selectbox(
                    "Branch / Office *",
                    add_branch_opts,
                    format_func=lambda b: BRANCH_NAMES.get(b, b),
                    help=STAFF_MASTER_FIELD_HELP["Branch"],
                    key="add_stf_branch",
                )
                new_email = st.text_input(
                    "Staff Gmail Address *",
                    placeholder="e.g. tendar@gmail.com",
                    help=STAFF_MASTER_FIELD_HELP["Email"],
                    key="add_stf_email",
                )
                new_pin = st.text_input(
                    "Initial 4-Digit PIN *",
                    type="password",
                    max_chars=4,
                    help=STAFF_MASTER_FIELD_HELP["PIN"],
                    key="add_stf_pin",
                )

            st.markdown("##### 2. Initial Leave Balances & Historical Service Ledger (`Staff_Master`)")
            st.caption(
                "Only **🟨 Manual Input** columns are editable below. All **🔒 Locked Formula** columns calculate live automatically using the exact Excel formulas."
            )

            def_q_cl = float(lt_rules_u.get("CL", {}).get("Annual_Quota", 9.0))
            def_q_sl = float(lt_rules_u.get("SL", {}).get("Annual_Quota", 30.0))
            def_q_al = float(lt_rules_u.get("AL", {}).get("Annual_Quota", 30.0))
            def_cap_al = float(lt_rules_u.get("AL", {}).get("Max_Accumulation_Cap", 180.0))
            cl_pr_r = float(lt_rules_u.get("CL", {}).get("Pro_Rata_UL_Penalty", 0.75))
            sl_pr_r = float(lt_rules_u.get("SL", {}).get("Pro_Rata_UL_Penalty", 2.50))
            al_pr_r = float(lt_rules_u.get("AL", {}).get("Pro_Rata_UL_Penalty", 2.50))
            ul_cap_add = float(gl_rules_u.get("UL_Career_Max_Cap", 730.0))

            # Pre-read current UL & Service-Affecting inputs from session_state so Tenure & CL/SL/AL Penalty formulas update live
            cur_add_o_ul = float(st.session_state.get("add_o_ul", 0.0) or 0.0)
            cur_add_u_ul = float(st.session_state.get("add_u_ul", 0.0) or 0.0)
            cur_add_cum_ul = float(st.session_state.get("add_cum_ul", 0.0) or 0.0)
            cur_add_cur_eff = float(st.session_state.get("add_cur_eff", 0.0) or 0.0)
            cur_add_cum_eff = float(st.session_state.get("add_cum_eff", 0.0) or 0.0)

            add_cal_days = max(0, (get_cta_today() - new_join).days)
            add_svc_yr = round(add_cal_days / 365.25, 2)
            add_svc_pen = max(0.0, cur_add_cum_ul + cur_add_u_ul + cur_add_cur_eff + cur_add_cum_eff)
            add_act_svc = round(max(0.0, (float(add_cal_days) - add_svc_pen) / 365.25), 2)
            add_pen_yrs = add_svc_pen / 365.25

            st.session_state["add_svcyr_lock"] = float(add_svc_yr)
            st.session_state["add_svcpen_lock"] = float(add_svc_pen)
            st.session_state["add_actsvc_lock"] = float(add_act_svc)

            with st.container(border=True):
                st.markdown(
                    f"###### ⏳ Service Tenure & Penalty — "
                    f"`Actual Service Year ({add_act_svc:.2f}y) = Service Year ({add_svc_yr:.2f}y) - Penalty ({add_pen_yrs:.2f}y / {add_svc_pen:g}d)`"
                )
                c_as1, c_as2, c_as3 = st.columns(3)
                c_as1.number_input("Service Year 🔒", step=0.01, format="%.2f", help=STAFF_MASTER_FIELD_HELP["Service Year"], key="add_svcyr_lock", disabled=True)
                c_as2.number_input("Service Year Penalty 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Service Year Penalty"], key="add_svcpen_lock", disabled=True)
                c_as3.number_input("Actual Service Year 🔒", step=0.01, format="%.2f", help=STAFF_MASTER_FIELD_HELP["Actual Service Year"], key="add_actsvc_lock", disabled=True)

            new_p_cl = round(((cur_add_u_ul + cur_add_cur_eff) / 30.0) * cl_pr_r * 2.0) / 2.0 if (cur_add_u_ul + cur_add_cur_eff) > 0 else 0.0
            new_p_sl = round(((cur_add_u_ul + cur_add_cur_eff) / 30.0) * sl_pr_r * 2.0) / 2.0 if (cur_add_u_ul + cur_add_cur_eff) > 0 else 0.0
            new_p_al = round(((cur_add_u_ul + cur_add_cur_eff) / 30.0) * al_pr_r * 2.0) / 2.0 if (cur_add_u_ul + cur_add_cur_eff) > 0 else 0.0

            with st.container(border=True):
                cur_ao_cl = float(st.session_state.get("add_o_cl", 0.0) or 0.0)
                cur_aa_cl = float(st.session_state.get("add_a_cl", def_q_cl) if "add_a_cl" in st.session_state else def_q_cl)
                cur_au_cl = float(st.session_state.get("add_u_cl", 0.0) or 0.0)
                live_ab_cl = max(0.0, cur_ao_cl + cur_aa_cl - new_p_cl - cur_au_cl)
                st.session_state["add_p_cl_lock"] = float(new_p_cl)
                st.session_state["add_b_cl_lock"] = float(live_ab_cl)
                st.markdown(
                    f"###### 🟦 Casual Leave (CL) — "
                    f"`Formula: Balance_CL ({live_ab_cl:g}) = max(0, Opening ({cur_ao_cl:g}) + Addition ({cur_aa_cl:g}) - Penalty ({new_p_cl:g}) - Used ({cur_au_cl:g}))`"
                )
                c_cl1, c_cl2, c_cl3, c_cl4, c_cl5 = st.columns(5)
                new_o_cl = c_cl1.number_input("Opening_CL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_CL"], key="add_o_cl")
                new_a_cl = c_cl2.number_input("Addition_CL", value=def_q_cl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_CL"], key="add_a_cl")
                c_cl3.number_input("Penalty_CL(UL Effect) 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Penalty_CL(UL Effect)"], key="add_p_cl_lock", disabled=True)
                new_u_cl = c_cl4.number_input("Used_CL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_CL"], key="add_u_cl")
                c_cl5.number_input("Balance_CL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_CL"], key="add_b_cl_lock", disabled=True)

            with st.container(border=True):
                cur_ao_sl = float(st.session_state.get("add_o_sl", 0.0) or 0.0)
                cur_aa_sl = float(st.session_state.get("add_a_sl", def_q_sl) if "add_a_sl" in st.session_state else def_q_sl)
                cur_au_sl = float(st.session_state.get("add_u_sl", 0.0) or 0.0)
                live_ab_sl = max(0.0, cur_ao_sl + cur_aa_sl - new_p_sl - cur_au_sl)
                st.session_state["add_p_sl_lock"] = float(new_p_sl)
                st.session_state["add_b_sl_lock"] = float(live_ab_sl)
                st.markdown(
                    f"###### 🟩 Sick Leave (SL) — "
                    f"`Formula: Balance_SL ({live_ab_sl:g}) = max(0, Opening ({cur_ao_sl:g}) + Addition ({cur_aa_sl:g}) - Penalty ({new_p_sl:g}) - Used ({cur_au_sl:g}))`"
                )
                c_sl1, c_sl2, c_sl3, c_sl4, c_sl5 = st.columns(5)
                new_o_sl = c_sl1.number_input("Opening_SL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_SL"], key="add_o_sl")
                new_a_sl = c_sl2.number_input("Addition_SL", value=def_q_sl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_SL"], key="add_a_sl")
                c_sl3.number_input("Penalty_SL(UL Effect) 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Penalty_SL(UL Effect)"], key="add_p_sl_lock", disabled=True)
                new_u_sl = c_sl4.number_input("Used_SL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_SL"], key="add_u_sl")
                c_sl5.number_input("Balance_SL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_SL"], key="add_b_sl_lock", disabled=True)

            with st.container(border=True):
                cur_ao_al = float(st.session_state.get("add_o_al", 0.0) or 0.0)
                cur_aa_al = float(st.session_state.get("add_a_al", def_q_al) if "add_a_al" in st.session_state else def_q_al)
                cur_au_al = float(st.session_state.get("add_u_al", 0.0) or 0.0)
                cur_ae_al = float(st.session_state.get("add_e_al", 0.0) or 0.0)
                raw_ab_al = max(0.0, cur_ao_al + cur_aa_al - new_p_al - cur_au_al - cur_ae_al)
                live_ab_al = min(def_cap_al, raw_ab_al) if def_cap_al > 0 else raw_ab_al
                st.session_state["add_p_al_lock"] = float(new_p_al)
                st.session_state["add_b_al_lock"] = float(live_ab_al)
                st.markdown(
                    f"###### 🟨 Annual Leave (AL) — "
                    f"`Formula: Balance_AL ({live_ab_al:g}) = min({def_cap_al:g}, max(0, Opening ({cur_ao_al:g}) + Addition ({cur_aa_al:g}) - Penalty ({new_p_al:g}) - Used ({cur_au_al:g}) - Encashed ({cur_ae_al:g})))`"
                )
                c_al1, c_al2, c_al3, c_al4, c_al5, c_al6 = st.columns(6)
                new_o_al = c_al1.number_input("Opening_AL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_AL"], key="add_o_al")
                new_a_al = c_al2.number_input("Addition_AL", value=def_q_al, step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_AL"], key="add_a_al")
                c_al3.number_input("Penalty_AL(UL Effect) 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Penalty_AL(UL Effect)"], key="add_p_al_lock", disabled=True)
                new_u_al = c_al4.number_input("Used_AL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_AL"], key="add_u_al")
                new_e_al = c_al5.number_input("Encashed_AL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Encashed_AL"], key="add_e_al")
                c_al6.number_input("Balance_AL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_AL"], key="add_b_al_lock", disabled=True)

            with st.container(border=True):
                new_ms_ul = float(min(20, int(add_act_svc // 5) * 5))
                add_ul_pool = cur_add_o_ul + cur_add_cum_ul
                if (
                    (new_ms_ul == 5.0 and add_ul_pool < 182.5)
                    or (new_ms_ul == 10.0 and add_ul_pool < 365.0)
                    or (new_ms_ul == 15.0 and add_ul_pool < 547.5)
                    or (new_ms_ul >= 20.0 and add_ul_pool < 730.0)
                ):
                    new_a_ul = 182.5
                else:
                    new_a_ul = 0.0
                live_ab_ul = max(0.0, cur_add_o_ul + new_a_ul - cur_add_u_ul)
                st.session_state["add_a_ul_lock"] = float(new_a_ul)
                st.session_state["add_b_ul_lock"] = float(live_ab_ul)
                st.session_state["add_ms_ul_lock"] = float(new_ms_ul)
                st.markdown(
                    f"###### 🟪 Unpaid Leave (UL) & Service-Affecting Leave — "
                    f"`Formula: Balance_UL ({live_ab_ul:g}) = max(0, Opening ({cur_add_o_ul:g}) + Addition ({new_a_ul:g}) - Used ({cur_add_u_ul:g})) | Career Cap ≤ {ul_cap_add:g}d`"
                )
                c_ul1, c_ul2, c_ul3, c_ul4, c_ul5, c_ul6 = st.columns(6)
                new_o_ul = c_ul1.number_input("Opening_UL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_UL"], key="add_o_ul")
                c_ul2.number_input("Addition_UL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_UL"], key="add_a_ul_lock", disabled=True)
                new_u_ul = c_ul3.number_input("Used_UL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_UL"], key="add_u_ul")
                c_ul4.number_input("Balance_UL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_UL"], key="add_b_ul_lock", disabled=True)
                new_cum_ul = c_ul5.number_input("Cumulative_UL", value=0.0, step=0.5, help=STAFF_MASTER_FIELD_HELP["Cumulative_UL"], key="add_cum_ul")
                c_ul6.number_input("Last_UL_Milestone 🔒", step=5.0, help=STAFF_MASTER_FIELD_HELP["Last_UL_Milestone"], key="add_ms_ul_lock", disabled=True)

                c_ef1, c_ef2 = st.columns(2)
                new_cur_eff = c_ef1.number_input(
                    "Current Year Leave affecting Service Year",
                    value=0.0,
                    step=0.5,
                    help=STAFF_MASTER_FIELD_HELP["Current Year Leave affecting Service Year"],
                    key="add_cur_eff",
                )
                new_cum_eff = c_ef2.number_input(
                    "Cummulative leave effecting Service year.(Education Leave & Etc).",
                    value=0.0,
                    step=0.5,
                    help=STAFF_MASTER_FIELD_HELP["Cummulative leave effecting Service year.(Education Leave & Etc)."],
                    key="add_cum_eff",
                )
                render_service_leave_duration_calculator(key_prefix="add_stf_svc_calc")

            if st.button("Create Staff Member & Save All Balances", type="primary", key="btn_create_new_staff"):
                if not new_name.strip() or not new_post.strip():
                    st.error("Name and Post are required.")
                elif not new_email.strip() or "@" not in new_email:
                    st.error("A valid Gmail address is required for security.")
                elif len(new_pin.strip()) != 4 or not new_pin.strip().isdigit():
                    st.error("PIN must be exactly 4 numeric digits.")
                elif users_df is not None and new_name.strip() in users_df["Name"].astype(str).str.strip().values:
                    st.error(f"User with name '{new_name.strip()}' already exists!")
                elif users_df is not None and new_email.strip().lower() in users_df["Email"].astype(str).str.strip().str.lower().values:
                    st.error(f"A user with Gmail '{new_email.strip().lower()}' already exists!")
                else:
                    with st.spinner("Adding new staff member and calculating balances..."):
                        new_topic = generate_user_ntfy_topic()

                        init_bal_cl = max(0.0, new_o_cl + new_a_cl - new_p_cl - new_u_cl)
                        init_bal_sl = max(0.0, new_o_sl + new_a_sl - new_p_sl - new_u_sl)
                        raw_init_al = max(0.0, new_o_al + new_a_al - new_p_al - new_u_al - new_e_al)
                        init_bal_al = min(def_cap_al, raw_init_al) if def_cap_al > 0 else raw_init_al
                        init_bal_ul = max(0.0, new_o_ul + new_a_ul - new_u_ul)

                        is_auditor_role = (str(new_role).strip() == "Auditor")
                        new_u_row = pd.DataFrame([{
                            "Name": new_name.strip(),
                            "PIN": str(new_pin.strip()),
                            "Role": new_role,
                            "Post": new_post.strip(),
                            "JoiningDate": "" if is_auditor_role else new_join.strftime("%Y-%m-%d"),
                            "NtfyTopic": new_topic,
                            "Branch": new_branch,
                            "Email": new_email.strip().lower()
                        }])

                        _gsheets_retry(conn.update, worksheet="Users", data=pd.concat([users_df, new_u_row], ignore_index=True))
                        if is_auditor_role:
                            _invalidate_sheet_cache("Users")
                            st.success(f"✅ Added Auditor {new_name.strip()} ({new_email.strip().lower()}) with read-only oversight (no leave/tour balance) and private channel: {new_topic}")
                            st.rerun()
                        else:
                            new_sm_row = pd.DataFrame([{
                                "Name": new_name.strip(),
                                "Post": new_post.strip(),
                                "JoiningDate": new_join.strftime("%Y-%m-%d"),
                                "Service Year": add_svc_yr, "Service Year Penalty": add_svc_pen, "Actual Service Year": add_act_svc,
                                "Opening_CL": new_o_cl, "Addition_CL": new_a_cl, "Penalty_CL(UL Effect)": new_p_cl, "Used_CL": new_u_cl, "Balance_CL": init_bal_cl,
                                "Opening_SL": new_o_sl, "Addition_SL": new_a_sl, "Penalty_SL(UL Effect)": new_p_sl, "Used_SL": new_u_sl, "Balance_SL": init_bal_sl,
                                "Opening_AL": new_o_al, "Addition_AL": new_a_al, "Penalty_AL(UL Effect)": new_p_al, "Used_AL": new_u_al, "Encashed_AL": new_e_al, "Balance_AL": init_bal_al,
                                "Opening_UL": new_o_ul, "Addition_UL": new_a_ul, "Used_UL": new_u_ul, "Balance_UL": init_bal_ul,
                                "Cumulative_UL": new_cum_ul, "Last_UL_Milestone": float(new_ms_ul),
                                "Current Year Leave affecting Service Year": new_cur_eff,
                                "Cummulative leave effecting Service year.(Education Leave & Etc).": new_cum_eff,
                                "Branch": new_branch,
                                "Active_FY": str(new_active_fy).strip() or get_current_financial_year_dates()[2]
                            }])

                            merged_sm = pd.concat([staff_df, new_sm_row], ignore_index=True)
                            _gsheets_retry(conn.update, worksheet="Staff_Master", data=inject_balance_formulas(merged_sm))
                            _invalidate_sheet_cache("Users", "Staff_Master")
                            st.success(f"✅ Added {new_name} ({new_role}) with full leave ledger and private channel: {new_topic}")
                            st.rerun()

        # Section B: Edit Existing Staff (Profile + All 32 Staff_Master Columns for Central Admin & Central Account)
        st.markdown("#### ✏️ Edit Staff Profile, PIN & Complete Leave Ledger (`Users` + `Staff_Master`)")
        if scoped_users_df is not None and not scoped_users_df.empty:
            e_user = st.selectbox(
                "Select Staff Member to Edit",
                scoped_users_df["Name"].tolist(),
                help=STAFF_MASTER_FIELD_HELP["Name"],
                key="sel_edit_u",
            )
            u_row = users_df[users_df["Name"] == e_user].iloc[0]
            u_idx = users_df[users_df["Name"] == e_user].index[0]

            sm_match = staff_df[staff_df["Name"].astype(str).str.strip() == str(e_user).strip()] if (staff_df is not None and not staff_df.empty) else pd.DataFrame()
            sm_row = sm_match.iloc[0] if not sm_match.empty else pd.Series(dtype=object)
            sm_idx = sm_match.index[0] if not sm_match.empty else None

            # Parse current JoiningDate safely
            raw_join_str = str(sm_row.get("JoiningDate", u_row.get("JoiningDate", ""))).strip()
            try:
                cur_join_date = pd.to_datetime(raw_join_str).date() if raw_join_str and raw_join_str.lower() not in ("nan", "none", "nat", "") else get_cta_today()
            except Exception:
                cur_join_date = get_cta_today()

            with st.container(border=True):
                st.markdown("##### 1. Identity, Role, Branch & Login (`Users` & `Staff_Master`)")
                st.caption("Hover or click **`(?)`** next to any field label for its **Note & Formula**.")
                c_ed1, c_ed2, c_ed3, c_ed4 = st.columns(4)
                with c_ed1:
                    edit_name = st.text_input(
                        "Name",
                        value=str(u_row.get("Name", e_user)),
                        help=STAFF_MASTER_FIELD_HELP["Name"],
                        key=f"ed_name_{e_user}",
                        disabled=not is_central_viewer,
                    )
                with c_ed2:
                    cur_post_val = str(u_row.get("Post", sm_row.get("Post", ""))).strip()
                    if cur_post_val == "སྡེ་འགན":
                        cur_post_val = "སྡེ་འགན།"
                    post_opts_ed = list(OFFICIAL_CTA_POSTS)
                    if cur_post_val and cur_post_val not in post_opts_ed:
                        post_opts_ed.append(cur_post_val)
                    def_post_idx = post_opts_ed.index(cur_post_val) if cur_post_val in post_opts_ed else 0
                    edit_post = st.selectbox(
                        "Post",
                        post_opts_ed,
                        index=def_post_idx,
                        help=STAFF_MASTER_FIELD_HELP["Post"],
                        key=f"ed_post_{e_user}",
                    )
                with c_ed3:
                    edit_join = st.date_input(
                        "JoiningDate",
                        value=cur_join_date,
                        min_value=date(1960, 1, 1),
                        max_value=date(2035, 12, 31),
                        help=STAFF_MASTER_FIELD_HELP["JoiningDate"],
                        key=f"ed_join_{e_user}",
                        disabled=not is_central_viewer,
                    )
                with c_ed4:
                    branch_opts_ed = ["HQ", "DB", "NB", "SB", "Other"]
                    cur_br_val = str(sm_row.get("Branch", u_row.get("Branch", "HQ"))).strip()
                    if cur_br_val not in branch_opts_ed:
                        branch_opts_ed.append(cur_br_val)
                    edit_branch = st.selectbox("Branch", branch_opts_ed, index=branch_opts_ed.index(cur_br_val), format_func=lambda b: BRANCH_NAMES.get(b, b), help=STAFF_MASTER_FIELD_HELP["Branch"], key=f"ed_branch_{e_user}", disabled=not is_central_viewer)

                c_p1, c_p2, c_p3, c_p4 = st.columns(4)
                with c_p1:
                    role_opts = ["Employee", "Co-Admin", "Accounts", "Branch Manager", "Auditor", "Admin"] if is_admin else (["Employee", "Co-Admin", "Accounts", "Branch Manager", "Auditor"] if is_central_viewer else ["Employee", "Accounts", "Branch Manager"])
                    cur_role = str(u_row.get("Role", "Employee")).strip()
                    def_role_idx = role_opts.index(cur_role) if cur_role in role_opts else 0
                    edit_role = st.selectbox(
                        "Role",
                        role_opts,
                        index=def_role_idx,
                        help=STAFF_MASTER_FIELD_HELP["Role"],
                        key=f"ed_role_{e_user}",
                    )
                with c_p2:
                    cur_pin_str = str(u_row.get("PIN", ""))
                    if cur_pin_str.endswith(".0"):
                        cur_pin_str = cur_pin_str[:-2]
                    new_pin_reset = st.text_input(
                        "4-Digit PIN",
                        value=cur_pin_str,
                        type="password",
                        max_chars=4,
                        help=STAFF_MASTER_FIELD_HELP["PIN"],
                        key=f"ed_pin_{e_user}",
                    )
                with c_p3:
                    cur_email_val = str(u_row.get("Email", ""))
                    edit_email = st.text_input(
                        "Authorized Gmail Address",
                        value=cur_email_val,
                        help=STAFF_MASTER_FIELD_HELP["Email"],
                        key=f"ed_email_{e_user}",
                    )
                with c_p4:
                    cur_fy_val = str(sm_row.get("Active_FY", get_current_financial_year_dates()[2])).strip()
                    edit_active_fy = st.text_input(
                        "Active_FY",
                        value=cur_fy_val,
                        help=STAFF_MASTER_FIELD_HELP["Active_FY"],
                        key=f"ed_fy_{e_user}",
                        disabled=True,
                    )

            if is_central_viewer and str(edit_role).strip() == "Auditor":
                st.info("🕵️ **Auditor Account (Read-Only Oversight)** — Auditors do not have leave or tour balances and are excluded from `Staff_Master` and Annual FY Rollovers.")
            elif is_central_viewer and sm_idx is not None:
                st.markdown("##### 2. Complete `Staff_Master` Leave & Service Year Fields (Simplified Category Cards + `?` Formulas)")
                st.caption(
                    "Only **🟨 Manual Input** columns are editable below. All **🔒 Locked Formula** columns (`Service Year`, `Service Year Penalty`, `Actual Service Year`, `Penalty_*`, `Balance_*`, `Addition_UL`, `Last_UL_Milestone`) calculate live automatically as you type."
                )

                orig_o_cl = float(_safe_num(sm_row.get("Opening_CL", 0.0)))
                orig_a_cl = float(_safe_num(sm_row.get("Addition_CL", 0.0)))
                orig_u_cl = float(_safe_num(sm_row.get("Used_CL", 0.0)))

                orig_o_sl = float(_safe_num(sm_row.get("Opening_SL", 0.0)))
                orig_a_sl = float(_safe_num(sm_row.get("Addition_SL", 0.0)))
                orig_u_sl = float(_safe_num(sm_row.get("Used_SL", 0.0)))

                orig_o_al = float(_safe_num(sm_row.get("Opening_AL", 0.0)))
                orig_a_al = float(_safe_num(sm_row.get("Addition_AL", 0.0)))
                orig_u_al = float(_safe_num(sm_row.get("Used_AL", 0.0)))
                orig_e_al = float(_safe_num(sm_row.get("Encashed_AL", 0.0)))

                orig_o_ul = float(_safe_num(sm_row.get("Opening_UL", 0.0)))
                orig_u_ul = float(_safe_num(sm_row.get("Used_UL", 0.0)))
                orig_cum_ul = float(_safe_num(sm_row.get("Cumulative_UL", 0.0)))

                orig_cur_eff = float(_safe_num(sm_row.get("Current Year Leave affecting Service Year", 0.0)))
                orig_cum_eff = float(_safe_num(sm_row.get("Cummulative leave effecting Service year.(Education Leave & Etc).", 0.0)))

                cl_pr_ed = float(lt_rules_u.get("CL", {}).get("Pro_Rata_UL_Penalty", 0.75))
                sl_pr_ed = float(lt_rules_u.get("SL", {}).get("Pro_Rata_UL_Penalty", 2.50))
                al_pr_ed = float(lt_rules_u.get("AL", {}).get("Pro_Rata_UL_Penalty", 2.50))
                al_cap_disp = float(lt_rules_u.get("AL", {}).get("Max_Accumulation_Cap", 180.0))
                ul_cap_disp = float(gl_rules_u.get("UL_Career_Max_Cap", 730.0))

                # Read live Yellow UL & Service-Affecting inputs from session_state so Tenure & Pro-Rata Penalties update immediately
                cur_ed_o_ul = float(st.session_state.get(f"ed_oul_{e_user}", orig_o_ul))
                cur_ed_u_ul = float(st.session_state.get(f"ed_uul_{e_user}", orig_u_ul))
                cur_ed_cum_ul = float(st.session_state.get(f"ed_cumul_{e_user}", orig_cum_ul))
                cur_ed_cur_eff = float(st.session_state.get(f"ed_cureff_{e_user}", orig_cur_eff))
                cur_ed_cum_eff = float(st.session_state.get(f"ed_cumeff_{e_user}", orig_cum_eff))

                # Card 2A: Service Tenure & Penalty (All 3 Locked Formula Columns: Service Year, Service Year Penalty, Actual Service Year)
                live_cal_days = max(0, (get_cta_today() - edit_join).days)
                live_svc_yr = round(live_cal_days / 365.25, 2)
                ed_svc_pen = max(0.0, cur_ed_cum_ul + cur_ed_u_ul + cur_ed_cur_eff + cur_ed_cum_eff)
                pen_years_equiv = ed_svc_pen / 365.25
                live_act_svc = round(max(0.0, (float(live_cal_days) - ed_svc_pen) / 365.25), 2)

                st.session_state[f"ed_svcyr_{e_user}"] = float(live_svc_yr)
                st.session_state[f"ed_svcpen_{e_user}"] = float(ed_svc_pen)
                st.session_state[f"ed_actsvc_{e_user}"] = float(live_act_svc)

                with st.container(border=True):
                    st.markdown(
                        f"###### ⏳ Service Tenure & Penalty — "
                        f"`Actual Service Year ({live_act_svc:.2f}y) = Service Year ({live_svc_yr:.2f}y) - Penalty ({pen_years_equiv:.2f}y / {ed_svc_pen:g}d)`"
                    )
                    c_sv1, c_sv2, c_sv3 = st.columns(3)
                    ed_svc_yr = c_sv1.number_input(
                        "Service Year 🔒",
                        step=0.01,
                        format="%.2f",
                        help=STAFF_MASTER_FIELD_HELP["Service Year"],
                        key=f"ed_svcyr_{e_user}",
                        disabled=True,
                    )
                    c_sv2.number_input(
                        "Service Year Penalty 🔒",
                        step=0.5,
                        help=STAFF_MASTER_FIELD_HELP["Service Year Penalty"],
                        key=f"ed_svcpen_{e_user}",
                        disabled=True,
                    )
                    ed_act_svc = c_sv3.number_input(
                        "Actual Service Year 🔒",
                        step=0.01,
                        format="%.2f",
                        help=STAFF_MASTER_FIELD_HELP["Actual Service Year"],
                        key=f"ed_actsvc_{e_user}",
                        disabled=True,
                    )

                tot_pr_ed = cur_ed_u_ul + cur_ed_cur_eff
                ed_p_cl = round((tot_pr_ed / 30.0) * cl_pr_ed * 2.0) / 2.0 if tot_pr_ed > 0 else 0.0
                ed_p_sl = round((tot_pr_ed / 30.0) * sl_pr_ed * 2.0) / 2.0 if tot_pr_ed > 0 else 0.0
                ed_p_al = round((tot_pr_ed / 30.0) * al_pr_ed * 2.0) / 2.0 if tot_pr_ed > 0 else 0.0

                # Card 2B: Casual Leave (CL)
                with st.container(border=True):
                    cur_eo_cl = float(st.session_state.get(f"ed_ocl_{e_user}", orig_o_cl))
                    cur_ea_cl = float(st.session_state.get(f"ed_acl_{e_user}", orig_a_cl))
                    cur_eu_cl = float(st.session_state.get(f"ed_ucl_{e_user}", orig_u_cl))
                    ed_b_cl = max(0.0, cur_eo_cl + cur_ea_cl - ed_p_cl - cur_eu_cl)
                    st.session_state[f"ed_pcl_{e_user}"] = float(ed_p_cl)
                    st.session_state[f"ed_bcl_{e_user}"] = float(ed_b_cl)
                    st.markdown(
                        f"###### 🟦 Casual Leave (CL) — "
                        f"`Formula: Balance_CL ({ed_b_cl:g}) = max(0, Opening ({cur_eo_cl:g}) + Addition ({cur_ea_cl:g}) - Penalty ({ed_p_cl:g}) - Used ({cur_eu_cl:g}))`"
                    )
                    c_cl_1, c_cl_2, c_cl_3, c_cl_4, c_cl_5 = st.columns(5)
                    ed_o_cl = c_cl_1.number_input("Opening_CL", value=orig_o_cl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_CL"], key=f"ed_ocl_{e_user}")
                    ed_a_cl = c_cl_2.number_input("Addition_CL", value=orig_a_cl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_CL"], key=f"ed_acl_{e_user}")
                    c_cl_3.number_input("Penalty_CL(UL Effect) 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Penalty_CL(UL Effect)"], key=f"ed_pcl_{e_user}", disabled=True)
                    ed_u_cl = c_cl_4.number_input("Used_CL", value=orig_u_cl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_CL"], key=f"ed_ucl_{e_user}")
                    c_cl_5.number_input("Balance_CL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_CL"], key=f"ed_bcl_{e_user}", disabled=True)

                # Card 2C: Sick Leave (SL)
                with st.container(border=True):
                    cur_eo_sl = float(st.session_state.get(f"ed_osl_{e_user}", orig_o_sl))
                    cur_ea_sl = float(st.session_state.get(f"ed_asl_{e_user}", orig_a_sl))
                    cur_eu_sl = float(st.session_state.get(f"ed_usl_{e_user}", orig_u_sl))
                    ed_b_sl = max(0.0, cur_eo_sl + cur_ea_sl - ed_p_sl - cur_eu_sl)
                    st.session_state[f"ed_psl_{e_user}"] = float(ed_p_sl)
                    st.session_state[f"ed_bsl_{e_user}"] = float(ed_b_sl)
                    st.markdown(
                        f"###### 🟩 Sick Leave (SL) — "
                        f"`Formula: Balance_SL ({ed_b_sl:g}) = max(0, Opening ({cur_eo_sl:g}) + Addition ({cur_ea_sl:g}) - Penalty ({ed_p_sl:g}) - Used ({cur_eu_sl:g}))`"
                    )
                    c_sl_1, c_sl_2, c_sl_3, c_sl_4, c_sl_5 = st.columns(5)
                    ed_o_sl = c_sl_1.number_input("Opening_SL", value=orig_o_sl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_SL"], key=f"ed_osl_{e_user}")
                    ed_a_sl = c_sl_2.number_input("Addition_SL", value=orig_a_sl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_SL"], key=f"ed_asl_{e_user}")
                    c_sl_3.number_input("Penalty_SL(UL Effect) 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Penalty_SL(UL Effect)"], key=f"ed_psl_{e_user}", disabled=True)
                    ed_u_sl = c_sl_4.number_input("Used_SL", value=orig_u_sl, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_SL"], key=f"ed_usl_{e_user}")
                    c_sl_5.number_input("Balance_SL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_SL"], key=f"ed_bsl_{e_user}", disabled=True)

                # Card 2D: Annual Leave (AL)
                with st.container(border=True):
                    cur_eo_al = float(st.session_state.get(f"ed_oal_{e_user}", orig_o_al))
                    cur_ea_al = float(st.session_state.get(f"ed_aal_{e_user}", orig_a_al))
                    cur_eu_al = float(st.session_state.get(f"ed_ual_{e_user}", orig_u_al))
                    cur_ee_al = float(st.session_state.get(f"ed_eal_{e_user}", orig_e_al))
                    raw_eb_al = max(0.0, cur_eo_al + cur_ea_al - ed_p_al - cur_eu_al - cur_ee_al)
                    ed_b_al = min(al_cap_disp, raw_eb_al) if al_cap_disp > 0 else raw_eb_al
                    st.session_state[f"ed_pal_{e_user}"] = float(ed_p_al)
                    st.session_state[f"ed_bal_{e_user}"] = float(ed_b_al)
                    st.markdown(
                        f"###### 🟨 Annual Leave (AL) — "
                        f"`Formula: Balance_AL ({ed_b_al:g}) = min({al_cap_disp:g}, max(0, Opening ({cur_eo_al:g}) + Addition ({cur_ea_al:g}) - Penalty ({ed_p_al:g}) - Used ({cur_eu_al:g}) - Encashed ({cur_ee_al:g})))`"
                    )
                    c_al_1, c_al_2, c_al_3, c_al_4, c_al_5, c_al_6 = st.columns(6)
                    ed_o_al = c_al_1.number_input("Opening_AL", value=orig_o_al, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_AL"], key=f"ed_oal_{e_user}")
                    ed_a_al = c_al_2.number_input("Addition_AL", value=orig_a_al, step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_AL"], key=f"ed_aal_{e_user}")
                    c_al_3.number_input("Penalty_AL(UL Effect) 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Penalty_AL(UL Effect)"], key=f"ed_pal_{e_user}", disabled=True)
                    ed_u_al = c_al_4.number_input("Used_AL", value=orig_u_al, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_AL"], key=f"ed_ual_{e_user}")
                    ed_e_al = c_al_5.number_input("Encashed_AL", value=orig_e_al, step=0.5, help=STAFF_MASTER_FIELD_HELP["Encashed_AL"], key=f"ed_eal_{e_user}")
                    c_al_6.number_input("Balance_AL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_AL"], key=f"ed_bal_{e_user}", disabled=True)

                # Card 2E: Unpaid Leave (UL) & Service-Affecting Leave
                with st.container(border=True):
                    ed_ms_ul = float(min(20, int(live_act_svc // 5) * 5))
                    ed_ul_pool = cur_ed_o_ul + cur_ed_cum_ul
                    if (
                        (ed_ms_ul == 5.0 and ed_ul_pool < 182.5)
                        or (ed_ms_ul == 10.0 and ed_ul_pool < 365.0)
                        or (ed_ms_ul == 15.0 and ed_ul_pool < 547.5)
                        or (ed_ms_ul >= 20.0 and ed_ul_pool < 730.0)
                    ):
                        ed_a_ul = 182.5
                    else:
                        ed_a_ul = 0.0
                    ed_b_ul = max(0.0, cur_ed_o_ul + ed_a_ul - cur_ed_u_ul)
                    st.session_state[f"ed_aul_{e_user}"] = float(ed_a_ul)
                    st.session_state[f"ed_bul_{e_user}"] = float(ed_b_ul)
                    st.session_state[f"ed_msul_{e_user}"] = float(ed_ms_ul)
                    st.markdown(
                        f"###### 🟪 Unpaid Leave (UL) & Service-Affecting Leave — "
                        f"`Formula: Balance_UL ({ed_b_ul:g}) = max(0, Opening ({cur_ed_o_ul:g}) + Addition ({ed_a_ul:g}) - Used ({cur_ed_u_ul:g})) | Career Cap ≤ {ul_cap_disp:g}d`"
                    )
                    c_ul_1, c_ul_2, c_ul_3, c_ul_4, c_ul_5, c_ul_6 = st.columns(6)
                    ed_o_ul = c_ul_1.number_input("Opening_UL", value=orig_o_ul, step=0.5, help=STAFF_MASTER_FIELD_HELP["Opening_UL"], key=f"ed_oul_{e_user}")
                    c_ul_2.number_input("Addition_UL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Addition_UL"], key=f"ed_aul_{e_user}", disabled=True)
                    ed_u_ul = c_ul_3.number_input("Used_UL", value=orig_u_ul, step=0.5, help=STAFF_MASTER_FIELD_HELP["Used_UL"], key=f"ed_uul_{e_user}")
                    c_ul_4.number_input("Balance_UL 🔒", step=0.5, help=STAFF_MASTER_FIELD_HELP["Balance_UL"], key=f"ed_bul_{e_user}", disabled=True)
                    ed_cum_ul = c_ul_5.number_input("Cumulative_UL", value=orig_cum_ul, step=0.5, help=STAFF_MASTER_FIELD_HELP["Cumulative_UL"], key=f"ed_cumul_{e_user}")
                    c_ul_6.number_input("Last_UL_Milestone 🔒", step=5.0, help=STAFF_MASTER_FIELD_HELP["Last_UL_Milestone"], key=f"ed_msul_{e_user}", disabled=True)

                    c_sef1, c_sef2 = st.columns(2)
                    ed_cur_eff = c_sef1.number_input(
                        "Current Year Leave affecting Service Year",
                        value=orig_cur_eff,
                        step=0.5,
                        help=STAFF_MASTER_FIELD_HELP["Current Year Leave affecting Service Year"],
                        key=f"ed_cureff_{e_user}",
                    )
                    ed_cum_eff = c_sef2.number_input(
                        "Cummulative leave effecting Service year.(Education Leave & Etc).",
                        value=orig_cum_eff,
                        step=0.5,
                        help=STAFF_MASTER_FIELD_HELP["Cummulative leave effecting Service year.(Education Leave & Etc)."],
                        key=f"ed_cumeff_{e_user}",
                    )
                    render_service_leave_duration_calculator(key_prefix=f"ed_stf_svc_calc_{e_user}")

            if st.button("💾 Save Staff Profile & Complete Leave Ledger", type="primary", key="btn_save_u_edit"):
                with st.spinner("Updating staff profile and Staff_Master ledger..."):
                    clean_new_name = edit_name.strip() if is_central_viewer and edit_name.strip() else str(e_user).strip()
                    is_edit_auditor = (str(edit_role).strip() == "Auditor")
                    join_str_new = "" if is_edit_auditor else edit_join.strftime("%Y-%m-%d")

                    users_df.at[u_idx, "Name"] = clean_new_name
                    users_df.at[u_idx, "Role"] = edit_role
                    users_df.at[u_idx, "Post"] = edit_post.strip()
                    users_df.at[u_idx, "JoiningDate"] = join_str_new
                    users_df.at[u_idx, "Branch"] = edit_branch.strip()
                    users_df.at[u_idx, "PIN"] = str(new_pin_reset).strip()
                    users_df.at[u_idx, "Email"] = str(edit_email).strip().lower()
                    _gsheets_retry(conn.update, worksheet="Users", data=users_df)

                    if is_edit_auditor:
                        if staff_df is not None and not staff_df.empty and sm_idx is not None:
                            sm_after = staff_df[staff_df["Name"].astype(str).str.strip() != str(e_user).strip()]
                            _gsheets_retry(conn.update, worksheet="Staff_Master", data=sm_after)
                    elif staff_df is not None and not staff_df.empty and sm_idx is not None:
                        for c in staff_df.columns:
                            staff_df[c] = staff_df[c].astype(object)
                        staff_df.at[sm_idx, "Name"] = clean_new_name
                        staff_df.at[sm_idx, "Post"] = edit_post.strip()
                        staff_df.at[sm_idx, "JoiningDate"] = join_str_new
                        staff_df.at[sm_idx, "Branch"] = edit_branch.strip()

                        if is_central_viewer:
                            staff_df.at[sm_idx, "Active_FY"] = str(edit_active_fy).strip() or get_current_financial_year_dates()[2]

                            staff_df.at[sm_idx, "Opening_CL"] = ed_o_cl
                            staff_df.at[sm_idx, "Addition_CL"] = ed_a_cl
                            staff_df.at[sm_idx, "Penalty_CL(UL Effect)"] = ed_p_cl
                            staff_df.at[sm_idx, "Used_CL"] = ed_u_cl
                            staff_df.at[sm_idx, "Balance_CL"] = ed_b_cl

                            staff_df.at[sm_idx, "Opening_SL"] = ed_o_sl
                            staff_df.at[sm_idx, "Addition_SL"] = ed_a_sl
                            staff_df.at[sm_idx, "Penalty_SL(UL Effect)"] = ed_p_sl
                            staff_df.at[sm_idx, "Used_SL"] = ed_u_sl
                            staff_df.at[sm_idx, "Balance_SL"] = ed_b_sl

                            staff_df.at[sm_idx, "Opening_AL"] = ed_o_al
                            staff_df.at[sm_idx, "Addition_AL"] = ed_a_al
                            staff_df.at[sm_idx, "Penalty_AL(UL Effect)"] = ed_p_al
                            staff_df.at[sm_idx, "Used_AL"] = ed_u_al
                            staff_df.at[sm_idx, "Encashed_AL"] = ed_e_al
                            staff_df.at[sm_idx, "Balance_AL"] = ed_b_al

                            staff_df.at[sm_idx, "Opening_UL"] = ed_o_ul
                            staff_df.at[sm_idx, "Addition_UL"] = ed_a_ul
                            staff_df.at[sm_idx, "Used_UL"] = ed_u_ul
                            staff_df.at[sm_idx, "Balance_UL"] = ed_b_ul
                            staff_df.at[sm_idx, "Cumulative_UL"] = ed_cum_ul
                            staff_df.at[sm_idx, "Last_UL_Milestone"] = ed_ms_ul
                            staff_df.at[sm_idx, "Current Year Leave affecting Service Year"] = ed_cur_eff
                            staff_df.at[sm_idx, "Cummulative leave effecting Service year.(Education Leave & Etc)."] = ed_cum_eff
                            staff_df.at[sm_idx, "Service Year"] = live_svc_yr
                            staff_df.at[sm_idx, "Service Year Penalty"] = ed_svc_pen
                            staff_df.at[sm_idx, "Actual Service Year"] = live_act_svc

                        synced_sm = inject_balance_formulas(staff_df)
                        _gsheets_retry(conn.update, worksheet="Staff_Master", data=synced_sm)

                    _invalidate_sheet_cache("Users", "Staff_Master")
                    st.success(f"✅ Updated profile, PIN, and complete leave ledger for {clean_new_name}!")
                    st.rerun()

            if is_central_viewer:
                with st.expander("🗑️ Remove / Delete Staff Member from System", expanded=False):
                    st.warning(f"Removing **{e_user}** will delete their row from both `Users` and `Staff_Master`.")
                    confirm_del_staff = st.checkbox(f"I confirm I want to remove {e_user}", key=f"chk_del_{e_user}")
                    if st.button(f"🗑️ Permanently Delete {e_user}", key=f"btn_del_{e_user}"):
                        if not confirm_del_staff:
                            st.error("Please check the confirmation box first.")
                        else:
                            users_after = users_df[users_df["Name"].astype(str).str.strip() != str(e_user).strip()]
                            _gsheets_retry(conn.update, worksheet="Users", data=users_after)
                            if staff_df is not None and not staff_df.empty:
                                sm_after = staff_df[staff_df["Name"].astype(str).str.strip() != str(e_user).strip()]
                                _gsheets_retry(conn.update, worksheet="Staff_Master", data=sm_after)
                            _invalidate_sheet_cache("Users", "Staff_Master")
                            st.success(f"Removed {e_user} from Users and Staff_Master.")
                            st.rerun()

            st.write("---")
            st.markdown("#### 📋 Staff Directory & Authorized Gmail Roster")
            disp_cols = [c for c in ["Name", "Email", "Role", "Post", "Branch", "JoiningDate"] if c in scoped_users_df.columns]
            st.dataframe(scoped_users_df[disp_cols], use_container_width=True)

    # --- TAB 3: STATEMENTS & ENCASHMENT ---
    with tab_stmt:
        st.subheader("📑 Staff Leave Statements & Encashment")
        st.caption("Generate individual employee statements, scoped branch master summaries, or process Annual Leave (AL) encashments.")
        
        c_stmt1, c_stmt2 = st.columns(2)
        with c_stmt1:
            st.markdown("#### 💰 AL Encashment")
            if scoped_staff_df is not None and not scoped_staff_df.empty:
                encash_user = st.selectbox(
                    "Select Employee for Encashment", scoped_staff_df["Name"].tolist(), key="sys_encash_user")
                encash_days = st.number_input(
                    "Days to Encash", min_value=0.5, step=0.5, key="sys_encash_days")
                if st.button("Process Encashment", key="sys_btn_process_encash"):
                    idx = staff_df[staff_df["Name"] == encash_user].index[0]
                    curr_al = float(staff_df.at[idx, "Balance_AL"]) if pd.notnull(
                        staff_df.at[idx, "Balance_AL"]) else 0

                    if encash_days > curr_al:
                        st.error(
                            "Cannot encash more days than the current AL balance.")
                    else:
                        curr_encashed = float(staff_df.at[idx, "Encashed_AL"]) if "Encashed_AL" in staff_df.columns and pd.notnull(
                            staff_df.at[idx, "Encashed_AL"]) else 0
                        staff_df.at[idx,
                                    "Encashed_AL"] = curr_encashed + encash_days

                        _gsheets_retry(conn.update, worksheet="Staff_Master",
                                       data=inject_balance_formulas(staff_df))
                        _invalidate_sheet_cache("Staff_Master")
                        st.success(
                            f"Successfully encashed {encash_days} AL days for {encash_user}.")
                        st.rerun()
            else:
                st.info("No staff found for the selected branch(es).")

        with c_stmt2:
            st.markdown("#### 📊 Master Statement Export")
            st.write("Generates a single master Excel table containing the scoped branch employees and their complete leave balances.")
            if scoped_staff_df is not None and not scoped_staff_df.empty:
                import io
                buffer_all = io.BytesIO()
                with pd.ExcelWriter(buffer_all, engine='openpyxl') as writer:
                    fixed_cols = ["Name", "Post", "Branch", "JoiningDate", "Service Year",
                                "Service Year Penalty", "Actual Service Year"]
                    cl_cols = ["Opening_CL", "Addition_CL",
                                "Penalty_CL(UL Effect)", "Used_CL", "Balance_CL"]
                    sl_cols = ["Opening_SL", "Addition_SL",
                                "Penalty_SL(UL Effect)", "Used_SL", "Balance_SL"]
                    al_cols = ["Opening_AL", "Addition_AL",
                                "Penalty_AL(UL Effect)", "Used_AL", "Encashed_AL", "Balance_AL"]
                    ul_cols = ["Opening_UL", "Addition_UL", "Used_UL", "Balance_UL", "Cumulative_UL", "Last_UL_Milestone",
                                "Current Year Leave affecting Service Year", "Cummulative leave effecting Service year.(Education Leave & Etc)."]

                    export_cols = [c for c in fixed_cols + cl_cols +
                                    sl_cols + al_cols + ul_cols if c in scoped_staff_df.columns]
                    export_df = scoped_staff_df[export_cols]
                    export_df.to_excel(writer, index=False,
                                        sheet_name="Master Leave Statement")

                st.download_button(
                    label="⬇️ Download Scoped Employees Statement (.xlsx)",
                    data=buffer_all.getvalue(),
                    file_name="All_Employees_Master_Statement.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="sys_dl_all_stmt",
                    type="primary"
                )

        st.divider()
        st.markdown("#### 📄 Single Employee Statement Export")
        if scoped_staff_df is not None and not scoped_staff_df.empty:
            c_exp_u, c_exp_fy = st.columns([2, 2])
            with c_exp_u:
                export_user = st.selectbox(
                    "Select Employee for Detailed History Statement",
                    scoped_staff_df["Name"].tolist(),
                    key="sys_exp_user",
                )
            with c_exp_fy:
                stmt_fy_sel = render_fy_scope_selector(
                    key="sys_stmt_fy_scope",
                    label="📅 Select Financial Year (FY) for Statement History",
                    include_all=True,
                )
            user_record = scoped_staff_df[scoped_staff_df["Name"] == export_user].iloc[0]
            active_fy_lbl = str(user_record.get("Active_FY", "")).strip() or get_current_financial_year_dates()[2]
            data = {
                "Leave Type": ["Casual Leave (CL)", "Sick Leave (SL)", "Annual Leave (AL)", "Unpaid Leave (UL)"],
                "Opening Balance": [user_record.get("Opening_CL", 0), user_record.get("Opening_SL", 0), user_record.get("Opening_AL", 0), user_record.get("Opening_UL", 0)],
                "Addition": [user_record.get("Addition_CL", 0), user_record.get("Addition_SL", 0), user_record.get("Addition_AL", 0), user_record.get("Addition_UL", 0)],
                "Used": [user_record.get("Used_CL", 0), user_record.get("Used_SL", 0), user_record.get("Used_AL", 0), user_record.get("Used_UL", 0)],
                "Closing Balance": [user_record.get("Balance_CL", 0), user_record.get("Balance_SL", 0), user_record.get("Balance_AL", 0), user_record.get("Balance_UL", 0)]
            }
            stmt_df = pd.DataFrame(data)
            buffer_single = io.BytesIO()
            stmt_fy_yr = stmt_fy_sel["fy_year"] if isinstance(stmt_fy_sel, dict) else None
            stmt_fy_label = stmt_fy_sel["label"] if isinstance(stmt_fy_sel, dict) else "Current FY"
            fy_rec_stmt = load_fy_records(stmt_fy_yr)
            req_df = fy_rec_stmt["leaves"]
            past_leaves = (
                req_df[req_df["Name"].astype(str).str.strip() == str(export_user).strip()]
                if (req_df is not None and not req_df.empty and "Name" in req_df.columns)
                else pd.DataFrame()
            )
            from openpyxl import Workbook
            from openpyxl.styles import Font
            wb = Workbook()
            ws = wb.active
            ws.title = "Leave Statement"
            bold_font = Font(bold=True)
            ws.append([f"Leave Status of {export_user} (Active Ledger: {active_fy_lbl})"])
            ws.cell(row=1, column=1).font = bold_font
            headers_1 = ["Leave Type", "Opening Balance", "Addition", "Used", "Closing Balance"]
            ws.append(headers_1)
            for index, r in stmt_df.iterrows():
                ws.append(r.tolist())
            ws.append([])
            ws.append([])
            start_row = 9
            ws.append([f"Past Leave History of {export_user} — {stmt_fy_label} (FY Breakdown)"])
            ws.cell(row=start_row, column=1).font = bold_font
            headers_2 = ["StartDate", "StartHalf", "EndDate", "EndHalf", "Application date", "Leave Type", "TotalDays", "FY_Split", "Status", "CLBalance", "ALBalance", "SLBalance", "ULBalance"]
            ws.append(headers_2)
            if not past_leaves.empty:
                for _, r in past_leaves.iterrows():
                    sd_p = _parse_cta_date(r.get("StartDate"))
                    ed_p = _parse_cta_date(r.get("EndDate"))
                    sd_str = sd_p.strftime("%Y-%m-%d") if sd_p is not None else str(r.get("StartDate", ""))
                    ed_str = ed_p.strftime("%Y-%m-%d") if ed_p is not None else str(r.get("EndDate", ""))
                    bdown_s = get_leave_fy_breakdown(sd_p or r.get("StartDate"), ed_p or r.get("EndDate"), r.get("TotalDays"))
                    if len(bdown_s) > 1:
                        split_note = " | ".join(f"{v['fy_label']}: {v['days']:g}d" for v in bdown_s.values())
                    elif len(bdown_s) == 1:
                        split_note = next(iter(bdown_s.values()))["fy_label"]
                    else:
                        split_note = active_fy_lbl
                    ws.append([
                        sd_str,
                        r.get("StartHalf", ""),
                        ed_str,
                        r.get("EndHalf", ""),
                        r.get("ApplicationDate", ""),
                        r.get("LeaveType", ""),
                        r.get("TotalDays", ""),
                        split_note,
                        r.get("Status", ""),
                        r.get("CLBalance", ""),
                        r.get("ALBalance", ""),
                        r.get("SLBalance", ""),
                        r.get("ULBalance", "")
                    ])
            wb.save(buffer_single)
            st.download_button(
                label=f"⬇️ Download {export_user} Detailed Statement (.xlsx)",
                data=buffer_single.getvalue(),
                file_name=f"{export_user}_Leave_Statement.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="sys_dl_single_stmt"
            )

    # --- TAB 4: BALANCE ADJUSTMENTS ---
    with tab_adj:
        render_balance_adjustment_panel(
            key_prefix=f"sys_{'adm' if is_admin else 'acct'}_adj",
            scoped_staff_df=scoped_staff_df,
            full_staff_df=staff_df,
            allowed_branches=allowed_branches,
        )

    # --- TAB 4: NOTIFICATIONS & MOBILE ---
    with tab_ntfy:
        st.subheader("🔔 Notification Channels & Alert Routing")
        st.caption("Each user has a private ntfy channel. Staff subscribe by opening the ntfy app and tapping `+`.")
        if scoped_users_df is not None and not scoped_users_df.empty:
            st.dataframe(scoped_users_df[["Name", "Role", "Branch", "NtfyTopic"]], use_container_width=True)
            
            st.markdown("#### 📲 Send Test Notification")
            c_t1, c_t2 = st.columns([1.5, 2.5])
            with c_t1:
                test_staff = st.selectbox("Select Staff to Test", scoped_users_df["Name"].tolist(), key="test_ntfy_staff")
            with c_t2:
                test_msg = st.text_input("Test Message", value="This is a test notification from the CTA Leave & Tour App.", key="test_ntfy_msg")
            if st.button("Send Test Ping 🚀", key="btn_send_test_ping"):
                top = get_user_ntfy_topic(test_staff)
                if top:
                    send_ntfy_notification(top, "Test Notification 🔔", test_msg)
                    st.success(f"Sent test notification to {test_staff} ({top})!")
                else:
                    st.error(f"No ntfy topic found for {test_staff}.")
        else:
            st.info("No users found for the selected branch(es).")

    # --- TAB 5: 1-CLICK SYSTEM BACKUP ---
    with tab_backup:
        st.subheader("💾 1-Click Complete System Backup")
        st.caption("Download an offline copy of your database (staff, balances, leave requests, and tours) in a single Excel file.")
        include_archives_bk = st.checkbox(
            "📦 Include Historical FY Archive Worksheets in Backup",
            value=False,
            key="chk_backup_include_archives",
        )
        import io
        backup_buffer = io.BytesIO()
        with pd.ExcelWriter(backup_buffer, engine='openpyxl') as writer:
            u_df = load_users_data()
            sm_df = load_staff_master()
            lr_df = load_leave_requests()
            tr_df = load_tour_sanctions()
            b_map = build_user_branch_map(u_df, sm_df) if not is_central_viewer else None
            if not is_central_viewer:
                u_df = filter_df_by_branches(u_df, allowed_branches, b_map, name_col="Name")
                sm_df = filter_df_by_branches(sm_df, allowed_branches, b_map, name_col="Name")
                lr_df = filter_df_by_branches(lr_df, allowed_branches, b_map, name_col="Name")
                tr_df = filter_df_by_branches(tr_df, allowed_branches, b_map, name_col="Name")
            if u_df is not None:
                u_df.to_excel(writer, sheet_name="Users", index=False)
            if sm_df is not None:
                sm_df.to_excel(writer, sheet_name="Staff_Master", index=False)
            if lr_df is not None:
                lr_df.to_excel(writer, sheet_name="LeaveRequests", index=False)
            if tr_df is not None:
                tr_df.to_excel(writer, sheet_name="TourSanctions", index=False)
            if include_archives_bk:
                for arch_title in _resolve_archive_titles():
                    kind_a, _ = _extract_fy_year_from_archive_title(arch_title)
                    if not kind_a:
                        continue
                    arch_df = _read_cached_archive_sheet(arch_title)
                    if arch_df is not None:
                        if not is_central_viewer and b_map is not None:
                            arch_df = filter_df_by_branches(arch_df, allowed_branches, b_map, name_col="Name")
                        safe_sheet_name = str(arch_title)[:31]
                        arch_df.to_excel(writer, sheet_name=safe_sheet_name, index=False)
        
        backup_buffer.seek(0)
        today_str = get_cta_today().strftime("%Y-%m-%d")
        st.download_button(
            label="📥 Download Full System Backup (.xlsx)",
            data=backup_buffer,
            file_name=f"CTA_Leave_Tour_System_Backup_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="btn_dl_full_backup",
            use_container_width=True
        )
        st.info("💡 You can store this Excel file on your office hard drive or Google Drive anytime for offline backup.")


def render_organization_calendar(fy_year_override=None, leaves_override=None, tours_override=None, key_suffix=""):
    cal_filter_prefix = "org_cal" if not key_suffix else f"org_cal{key_suffix}"
    allowed_branches = render_branch_filter_checkboxes(key_prefix=cal_filter_prefix)
    branch_map = get_user_branch_map()
    scope_str = format_branch_scope(allowed_branches)
    hdr_col, export_col = st.columns([3.5, 1.5])
    with hdr_col:
        st.subheader("📅 Organization Leave & Tour Calendar", anchor="org-calendar")
        if allowed_branches:
            fy_note = f" | FY {int(fy_year_override)}-{int(fy_year_override) + 1}" if fy_year_override is not None else ""
            st.caption(f"🔎 Calendar Branch Scope: **{scope_str}**{fy_note}")
    export_placeholder = export_col.empty()

    # Interactive Event Type Filter (All, Leaves Only, Tours Only)
    cal_event_key = "org_cal_event_filter" if not key_suffix else f"org_cal_event_filter{key_suffix}"
    cal_event_filter = st.radio(
        "Event Display Filter",
        options=["All (Leaves & Tours)", "🍃 Leaves Only", "✈️ Tours Only"],
        index=0,
        horizontal=True,
        key=cal_event_key,
    )
    include_leaves = cal_event_filter in ("All (Leaves & Tours)", "🍃 Leaves Only")
    include_tours = cal_event_filter in ("All (Leaves & Tours)", "✈️ Tours Only")
    
    # Dynamic Financial Year Bounds (April 1 to March 31)
    if fy_year_override is not None:
        fy_yr_int = int(fy_year_override)
        s_d = date(fy_yr_int, 4, 1)
        e_d = date(fy_yr_int + 1, 3, 31)
    else:
        s_d, e_d, _ = get_current_financial_year_dates()
    fy_start = s_d.replace(day=1)
    fy_end = e_d.replace(day=1)

    cal_state_key = "cal_date" if not key_suffix else f"cal_date{key_suffix}"
    if cal_state_key not in st.session_state:
        # Default to today (IST), but if today is outside FY, default to FY start
        today_start = get_cta_today().replace(day=1)
        if today_start >= fy_start and today_start <= fy_end:
            st.session_state[cal_state_key] = today_start
        else:
            st.session_state[cal_state_key] = fy_start
    elif st.session_state[cal_state_key] < fy_start or st.session_state[cal_state_key] > fy_end:
        st.session_state[cal_state_key] = fy_start
            
    curr = st.session_state[cal_state_key]
            
    header_placeholder = st.empty()
    
    col1, col2, col3 = header_placeholder.columns([1, 2, 1])
    with col1:
        # Calculate prev month
        if curr.month == 1:
            prev_m = curr.replace(year=curr.year-1, month=12)
        else:
            prev_m = curr.replace(month=curr.month-1)
            
        if prev_m >= fy_start:
            prev_btn_key = "prev_cal_btn" if not key_suffix else f"prev_cal_btn{key_suffix}"
            if st.button("⬅️ Previous Month", key=prev_btn_key):
                st.session_state[cal_state_key] = prev_m
                st.rerun()
        else:
            st.write("") # Spacer
            
    with col2:
        st.markdown(f"<h3 style='text-align: center'>{calendar.month_name[st.session_state[cal_state_key].month]} {st.session_state[cal_state_key].year}</h3>", unsafe_allow_html=True)
        
    with col3:
        # Calculate next month
        if curr.month == 12:
            next_m = curr.replace(year=curr.year+1, month=1)
        else:
            next_m = curr.replace(month=curr.month+1)
            
        if next_m <= fy_end:
            next_btn_key = "next_cal_btn" if not key_suffix else f"next_cal_btn{key_suffix}"
            if st.button("Next Month ➡️", key=next_btn_key):
                st.session_state[cal_state_key] = next_m
                st.rerun()
        else:
            st.write("") # Spacer
            
    df_requests = leaves_override.copy() if leaves_override is not None else load_leave_requests()
    tours_df = tours_override.copy() if tours_override is not None else load_tour_sanctions()
    df_requests = filter_df_by_branches(df_requests, allowed_branches, branch_map, name_col="Name")
    tours_df = filter_df_by_branches(tours_df, allowed_branches, branch_map, name_col="Name")
    if (df_requests is None or df_requests.empty) and (tours_df is None or tours_df.empty):
        st.info("No leave or tour data to display for the selected branch(es).")
        if fy_year_override is None:
            return
        
    year = st.session_state[cal_state_key].year
    month = st.session_state[cal_state_key].month
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    def _cal_parse_date(val):
        _cta_fn = globals().get("_parse_cta_date")
        if callable(_cta_fn):
            return _cta_fn(val)
        if val is None:
            return None
        if isinstance(val, datetime):
            if getattr(val, "tzinfo", None) is not None:
                return val.astimezone(CTA_TZ).date() if "CTA_TZ" in globals() else val.date()
            return val.date()
        if hasattr(val, "to_pydatetime"):
            try:
                if pd.isnull(val):
                    return None
                ts = val.to_pydatetime()
                return ts.date() if ts is not None else None
            except Exception:
                return None
        if isinstance(val, date):
            return val
        try:
            if pd.isnull(val):
                return None
        except Exception:
            pass
        s = str(val).strip()
        if not s or s.lower() in ("nan", "none", "nat", "null"):
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
            if dt.tzinfo is not None and "CTA_TZ" in globals():
                return dt.astimezone(CTA_TZ).date()
            return dt.date()
        except Exception:
            pass
        if len(s) >= 10:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except ValueError:
                    continue
        return None

    def _cal_parse_series(series):
        return [_cal_parse_date(v) for v in series.tolist()]

    events = {day: [] for day in range(1, 32)}

    iter_requests = df_requests if (include_leaves and df_requests is not None and not df_requests.empty) else pd.DataFrame()
    if not iter_requests.empty:
        if "Status" in iter_requests.columns:
            iter_requests = iter_requests[iter_requests["Status"].astype(str).str.strip() != "Rejected"]
        if not iter_requests.empty and "StartDate" in iter_requests.columns and "EndDate" in iter_requests.columns:
            sd_list = _cal_parse_series(iter_requests["StartDate"])
            ed_list = _cal_parse_series(iter_requests["EndDate"])
            keep_l = [
                (sd is not None and ed is not None and sd <= month_end and ed >= month_start)
                for sd, ed in zip(sd_list, ed_list)
            ]
            iter_requests = iter_requests.loc[keep_l]
    for idx, row in iter_requests.iterrows():
        start_d = _cal_parse_date(row.get('StartDate'))
        end_d = _cal_parse_date(row.get('EndDate'))
        if start_d is None or end_d is None or start_d > month_end or end_d < month_start:
            continue

        status = str(row.get('Status', '')).strip()
        if status == "Rejected":
            continue

        name = str(row.get('Name', '')).strip()
        l_type = str(row.get('LeaveType', '')).strip()

        is_reversal = l_type.startswith("Return-") or l_type.startswith("Rev-")
        display_type = l_type if not is_reversal else f"Rev-{l_type.replace('Return-', '').replace('Rev-', '')}"

        if status == "Cancelled":
            bg = "#e0e0e0"
            color = "#777"
            border = "1px solid #ccc"
            text_decor = "line-through"
        elif status == "Pending":
            bg = "#FF9800"
            color = "white"
            border = "1px solid #F57C00"
            text_decor = "none"
        else:
            if is_reversal:
                bg = "#F44336"  # Red for reversals
                border = "1px solid #D32F2F"
            else:
                bg = "#4CAF50"
                border = "1px solid #388E3C"
            color = "white"
            text_decor = "none"

        curr_d = max(start_d, month_start)
        loop_end = min(end_d, month_end)
        while curr_d <= loop_end:
            day = curr_d.day

            is_half = False
            align = "center"
            grad = ""

            sh = str(row.get('StartHalf', ''))
            eh = str(row.get('EndHalf', ''))

            if curr_d == start_d and "Morning" in sh:
                is_half = True
                align = "left"
                grad = f"background: linear-gradient(to right, {bg} 50%, transparent 50%); border-left: {border};"
            elif curr_d == start_d and "Evening" in sh:
                is_half = True
                align = "right"
                grad = f"background: linear-gradient(to left, {bg} 50%, transparent 50%); border-right: {border};"
            elif curr_d == end_d and "Morning" in eh:
                is_half = True
                align = "left"
                grad = f"background: linear-gradient(to right, {bg} 50%, transparent 50%); border-left: {border};"
            elif curr_d == end_d and "Evening" in eh:
                is_half = True
                align = "right"
                grad = f"background: linear-gradient(to left, {bg} 50%, transparent 50%); border-right: {border};"

            event_txt = f"{name} - {display_type}"
            if is_half:
                half_str = str(row.get('StartHalf') if curr_d == start_d else row.get('EndHalf'))
                if "Morning" in half_str:
                    h_char = "Mor"
                elif "Evening" in half_str:
                    h_char = "Eve"
                else:
                    h_char = "Half"
                event_txt += f" ({h_char})"

            if is_half:
                style = f"{grad} color: #333; text-decoration: {text_decor}; font-size: 0.75em; padding: 2px 4px; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-radius: 2px; text-align: {align};"
            else:
                style = f"background: {bg}; color: {color}; border: {border}; text-decoration: {text_decor}; font-size: 0.75em; padding: 2px 4px; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-radius: 2px; text-align: center;"

            events[day].append(f'<div style="{style}" title="{status}">{event_txt}</div>')

            curr_d += timedelta(days=1)

    # Map Official Tours on Calendar (already filtered by allowed_branches above)
    if include_tours and tours_df is not None and not tours_df.empty:
        tour_iter = tours_df
        if "Status" in tour_iter.columns:
            tour_iter = tour_iter[~tour_iter["Status"].astype(str).str.strip().isin(["Rejected", "Cancelled"])]
        if not tour_iter.empty:
            has_dep = "DepartureDate" in tour_iter.columns
            has_arr = "ArrivalDate" in tour_iter.columns
            dep_list = _cal_parse_series(tour_iter["DepartureDate"]) if has_dep else [None] * len(tour_iter)
            arr_list = _cal_parse_series(tour_iter["ArrivalDate"]) if has_arr else [None] * len(tour_iter)
            legs_col = tour_iter["TravelLegs"].tolist() if "TravelLegs" in tour_iter.columns else [""] * len(tour_iter)
            keep_t = []
            for dd, ad, raw_l in zip(dep_list, arr_list, legs_col):
                has_legs = (
                    (isinstance(raw_l, list) and len(raw_l) > 0)
                    or (isinstance(raw_l, str) and raw_l.strip().startswith("[") and len(raw_l.strip()) > 2)
                )
                if dd is not None and ad is not None:
                    keep_t.append(dd <= month_end and ad >= month_start)
                elif has_legs:
                    keep_t.append(True)
                else:
                    keep_t.append(False)
            tour_iter = tour_iter.loc[keep_t]
        for idx, row in tour_iter.iterrows():
            status = str(row.get('Status', '')).strip()
            if status in ["Rejected", "Cancelled"]:
                continue
            name = str(row.get('Name', '')).strip()
            t_bg = "#0284c7" if status in ["Sanctioned", "Completed", "Settled"] else "#0ea5e9"
            t_border = "1px solid #0369a1"
            t_style = f"background: {t_bg}; color: white; border: {t_border}; font-size: 0.75em; padding: 2px 4px; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-radius: 2px; text-align: center;"

            # Check if TravelLegs is available for granular calendar mapping
            raw_legs = row.get('TravelLegs', '')
            legs_list = []
            if isinstance(raw_legs, list):
                legs_list = raw_legs
            elif isinstance(raw_legs, str) and raw_legs.strip().startswith('['):
                try:
                    legs_list = json.loads(raw_legs)
                except Exception:
                    legs_list = []

            if legs_list and len(legs_list) > 0:
                for leg in legs_list:
                    try:
                        start_d = _cal_parse_date(leg.get('departure_date', ''))
                        end_d = _cal_parse_date(leg.get('arrival_date', ''))
                        if start_d is None or end_d is None or start_d > month_end or end_d < month_start:
                            continue
                        dest = str(leg.get('destination', '')).strip()
                        t_txt = f"✈️ {name} ({dest})"
                        curr_d = max(start_d, month_start)
                        loop_end = min(end_d, month_end)
                        while curr_d <= loop_end:
                            events[curr_d.day].append(f'<div style="{t_style}" title="Tour: {status}">{t_txt}</div>')
                            curr_d += timedelta(days=1)
                    except Exception:
                        pass
            else:
                try:
                    start_d = _cal_parse_date(row.get('DepartureDate'))
                    end_d = _cal_parse_date(row.get('ArrivalDate'))
                    if start_d is None or end_d is None or start_d > month_end or end_d < month_start:
                        continue
                    dest = str(row.get('Destination', '')).strip()
                    t_txt = f"✈️ {name} ({dest})"
                    curr_d = max(start_d, month_start)
                    loop_end = min(end_d, month_end)
                    while curr_d <= loop_end:
                        events[curr_d.day].append(f'<div style="{t_style}" title="Tour: {status}">{t_txt}</div>')
                        curr_d += timedelta(days=1)
                except Exception:
                    continue
            
    first_weekday, num_days = calendar.monthrange(year, month)
    start_offset = (first_weekday + 1) % 7
    
    html = []
    html.append(f'<div style="width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; padding-bottom: 5px;">')
    html.append(f'<div style="display: grid; grid-template-columns: repeat(7, minmax(120px, max-content)); gap: 2px; background: #ddd; border: 1px solid #ddd; min-width: max-content;">')
    
    days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    for day in days:
        html.append(f'<div style="background: #f0f0f0; text-align: center; font-weight: bold; padding: 5px; color: #333;">{day}</div>')
        
    saturday_count = 0
    for i in range(start_offset):
        html.append(f'<div style="background: #fafafa; min-height: 100px;"></div>')
        
    for day in range(1, num_days + 1):
        col_idx = (start_offset + day - 1) % 7
        
        is_weekend = False
        if col_idx == 0:
            is_weekend = True
        elif col_idx == 6:
            saturday_count += 1
            if saturday_count != 1:
                is_weekend = True
                
        bg_color = "#f4f4f4" if is_weekend else "white"
        
        html.append(f'<div style="background: {bg_color}; min-height: 100px; padding: 5px; display: flex; flex-direction: column;">')
        html.append(f'<div style="font-weight: bold; font-size: 0.85em; color: #555; margin-bottom: 5px; text-align: right;">{day}</div>')
        for ev in events[day]:
            html.append(ev)
        html.append('</div>')
        
    end_offset = (7 - ((start_offset + num_days) % 7)) % 7
    for i in range(end_offset):
        html.append(f'<div style="background: #fafafa; min-height: 100px;"></div>')
        
    html.append('</div>')
    html.append('</div>')
    
    full_html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Organization Calendar ({scope_str}) - {calendar.month_name[month]} {year}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 20px; background: #fdfdfd; }}
        </style>
    </head>
    <body>
        <h2 style="text-align: center; color: #1e293b; margin-bottom: 4px;">Organization Leave & Tour Calendar: {calendar.month_name[month]} {year}</h2>
        <p style="text-align: center; color: #64748b; margin-top: 0; font-size: 14px;"><strong>Branch Scope:</strong> {scope_str} &nbsp;|&nbsp; <strong>Filter:</strong> {cal_event_filter}</p>
        {"\n".join(html)}
    </body>
    </html>
    """
    
    with export_placeholder:
        export_btn_key = "export_cal_btn" if not key_suffix else f"export_cal_btn{key_suffix}"
        st.download_button(
            label="📄 Export to PDF / Web",
            data=full_html,
            file_name=f"Leave_Calendar_{year}_{month}.html",
            mime="text/html",
            key=export_btn_key
        )
        
    st.markdown("\n".join(html), unsafe_allow_html=True)


def filter_active_banner_records(df, viewer_role, viewer_branch, allowed_branches, branch_map, role_map):
    """Filters Out-of-Office banner leaves/tours:
    - Central roles / Branch Managers checking org branches see `allowed_branches` (plus Admin/Co-Admin for Branch Manager).
    - Branch Employees & Branch Accounts see ONLY their own branch staff PLUS HQ Admin and Co-Admin
      when Admin/Co-Admin is on leave or tour (excluding regular HQ staff and other branch staff).
    """
    if df is None or df.empty or "Name" not in df.columns:
        return df
    allowed_set = {normalize_branch_code(b) for b in (allowed_branches or [])}
    norm_role = str(viewer_role or "").strip()
    is_branch_locked_banner = not is_central_branch_filter_role(norm_role, viewer_branch)

    def _keep(name_val):
        nm = str(name_val).strip() if pd.notnull(name_val) else ""
        if not nm:
            return False
        br = normalize_branch_code(branch_map.get(nm, "HQ"))
        if br in allowed_set:
            return True
        if is_branch_locked_banner:
            target_role = str(role_map.get(nm, "")).strip()
            if target_role in ("Admin", "Co-Admin"):
                return True
        return False

    return df[df["Name"].apply(_keep)]


def render_active_leaves_banner(show_type=True):
    viewer_name = st.session_state.get("user_name", "")
    viewer_role = st.session_state.get("user_role", "Employee")
    viewer_branch = get_user_branch(viewer_name) if viewer_name else "HQ"
    allowed_branches = get_active_visible_branches()
    branch_map = get_user_branch_map()

    users_df = load_users_data()
    role_map = {}
    if users_df is not None and not users_df.empty and "Name" in users_df.columns and "Role" in users_df.columns:
        for raw_nm, raw_rl in zip(users_df["Name"].tolist(), users_df["Role"].tolist()):
            nm = str(raw_nm).strip() if pd.notnull(raw_nm) else ""
            if nm:
                role_map[nm] = str(raw_rl).strip() if pd.notnull(raw_rl) else ""

    today_d = get_cta_today()
    _parse_d_fn = globals().get("_parse_cta_date") or globals().get("_parse_leave_date")

    def _banner_parse_series(series):
        fast = pd.to_datetime(series, format="%Y-%m-%d", errors="coerce").dt.date
        out = []
        for raw_v, fv in zip(series.tolist(), fast.tolist()):
            if fv is not None and not pd.isna(fv):
                out.append(fv)
            elif callable(_parse_d_fn):
                out.append(_parse_d_fn(raw_v))
            else:
                out.append(None)
        return out

    raw_leaves = load_leave_requests()
    if raw_leaves is not None and not raw_leaves.empty and "Status" in raw_leaves.columns and "StartDate" in raw_leaves.columns and "EndDate" in raw_leaves.columns:
        raw_leaves = raw_leaves[raw_leaves["Status"].astype(str).str.strip() == "Approved"]
        if not raw_leaves.empty:
            sd_l = _banner_parse_series(raw_leaves["StartDate"])
            ed_l = _banner_parse_series(raw_leaves["EndDate"])
            keep_l = [(sd is not None and ed is not None and sd <= today_d <= ed) for sd, ed in zip(sd_l, ed_l)]
            raw_leaves = raw_leaves.loc[keep_l]
    df = filter_active_banner_records(raw_leaves, viewer_role, viewer_branch, allowed_branches, branch_map, role_map)

    raw_tours = load_tour_sanctions()
    if raw_tours is not None and not raw_tours.empty and "Status" in raw_tours.columns and "DepartureDate" in raw_tours.columns and "ArrivalDate" in raw_tours.columns:
        raw_tours = raw_tours[raw_tours["Status"].astype(str).str.strip().isin(["Sanctioned", "Completed", "Settled"])]
        if not raw_tours.empty:
            dd_l = _banner_parse_series(raw_tours["DepartureDate"])
            ad_l = _banner_parse_series(raw_tours["ArrivalDate"])
            keep_t = [(dd is not None and ad is not None and dd <= today_d <= ad) for dd, ad in zip(dd_l, ad_l)]
            raw_tours = raw_tours.loc[keep_t]
    tours_df = filter_active_banner_records(raw_tours, viewer_role, viewer_branch, allowed_branches, branch_map, role_map)

    active_items = []
    if df is not None and not df.empty:
        for idx, row in df.iterrows():
            if str(row.get("Status", "")).strip() == "Approved":
                start = str(row.get("StartDate", ""))
                end = str(row.get("EndDate", ""))
                s_d = _parse_d_fn(start) if callable(_parse_d_fn) else None
                e_d = _parse_d_fn(end) if callable(_parse_d_fn) else None
                if s_d is not None and e_d is not None and s_d <= today_d <= e_d:
                    start_half = str(row.get("StartHalf", "Full Day")).strip()
                    end_half = str(row.get("EndHalf", "Full Day")).strip()
                    if start_half == end_half:
                        half_str = f"({start_half})"
                    else:
                        half_str = f"({start_half} Start, {end_half} End)"

                    emp_name = str(row['Name']).strip()
                    emp_role = role_map.get(emp_name, "")
                    is_other_branch_leadership = (
                        emp_role in ("Admin", "Co-Admin")
                        and normalize_branch_code(branch_map.get(emp_name, "HQ")) not in {normalize_branch_code(b) for b in (allowed_branches or [])}
                    )
                    role_badge = f" ({emp_role})" if is_other_branch_leadership else ""
                    icon = "👑" if emp_role == "Admin" and is_other_branch_leadership else ("🤝" if emp_role == "Co-Admin" and is_other_branch_leadership else "👤")
                    type_str = f" **{row['LeaveType']}**" if show_type else " leave"
                    active_items.append(f"{icon} **{emp_name}{role_badge}** is currently on{type_str} from {start} to {end} ({row['TotalDays']} days total) {half_str}")

    if tours_df is not None and not tours_df.empty:
        for idx, row in tours_df.iterrows():
            if str(row.get("Status", "")).strip() in ["Sanctioned", "Completed", "Settled"]:
                start = str(row.get("DepartureDate", ""))
                end = str(row.get("ArrivalDate", ""))
                s_d = _parse_d_fn(start) if callable(_parse_d_fn) else None
                e_d = _parse_d_fn(end) if callable(_parse_d_fn) else None
                if s_d is not None and e_d is not None and s_d <= today_d <= e_d:
                    emp_name = str(row['Name']).strip()
                    emp_role = role_map.get(emp_name, "")
                    is_other_branch_leadership = (
                        emp_role in ("Admin", "Co-Admin")
                        and normalize_branch_code(branch_map.get(emp_name, "HQ")) not in {normalize_branch_code(b) for b in (allowed_branches or [])}
                    )
                    role_badge = f" ({emp_role})" if is_other_branch_leadership else ""
                    active_items.append(f"✈️ **{emp_name}{role_badge}** is on Official Duty Tour to **{row.get('Destination', 'Station')}** from {start} to {end} ({row.get('TotalDays', 1)} day(s))")

    if active_items:
        branch_scope_str = format_branch_scope(allowed_branches)
        scope_note = f"{branch_scope_str} + Admin/Co-Admin" if not is_central_branch_filter_role(viewer_role, viewer_branch) else branch_scope_str
        st.info(f"### 📅 Today's Out of Office (Leaves & Official Tours — {scope_note})\n" + "\n".join([f"- {l}" for l in active_items]))

def branch_manager_dashboard():
    render_active_leaves_banner()
    bm_name = st.session_state.user_name
    bm_branch = get_user_branch(bm_name)
    branch_full_name = BRANCH_NAMES.get(bm_branch, f"Branch {bm_branch}")

    st.title(f"🏢 Branch Manager Dashboard — {branch_full_name}")
    st.caption(f"Logged in as: **{bm_name}** | Branch Code: `{bm_branch}`")

    bm_tab_options = [
        f"📋 {bm_branch} Leave Approvals",
        f"✈️ {bm_branch} Tour Sanctions",
        "🍃 My Leave & Tour Applications",
        f"👥 {bm_branch} Staff & Balances"
    ]
    if "bm_tabs" not in st.session_state or st.session_state.bm_tabs not in bm_tab_options:
        st.session_state.bm_tabs = bm_tab_options[0]

    tab_bm_approvals, tab_bm_tours, tab_bm_my, tab_bm_staff = st.tabs(
        [
            f"📋 {bm_branch} Leave Approvals",
            f"✈️ {bm_branch} Tour Sanctions",
            "🍃 My Leave & Tour Applications",
            f"👥 {bm_branch} Staff & Balances"
        ],
        key="bm_tabs",
        on_change="rerun"
    )

    users_df = load_users_data()
    branch_staff_names = []
    if users_df is not None and not users_df.empty:
        branch_staff_names = users_df[
            (users_df["Branch"].apply(normalize_branch_code) == bm_branch) &
            (users_df["Name"] != bm_name)
        ]["Name"].tolist()

    # --- TAB 1: BRANCH LEAVE APPROVALS ---
    with tab_bm_approvals:
        df_requests = load_leave_requests()
        tours_df_raw = load_tour_sanctions()
        bm_fy_sel = render_fy_scope_selector(
            key="bm_fy_scope",
            label="📅 Select Financial Year (FY) Scope for Branch Leaves & Tours",
            include_all=True,
            leave_df=df_requests,
            tour_df=tours_df_raw,
        )
        bm_fy_yr = bm_fy_sel["fy_year"] if isinstance(bm_fy_sel, dict) else None
        bm_fy_records = load_fy_records(bm_fy_yr, leave_df_active=df_requests, tour_df_active=tours_df_raw)
        bm_scoped_leaves = bm_fy_records["leaves"]
        bm_scoped_tours = bm_fy_records["tours"]

        with st.expander("📅 View Organization Calendar", expanded=False):
            render_organization_calendar(
                fy_year_override=bm_fy_yr,
                leaves_override=bm_scoped_leaves,
                tours_override=bm_scoped_tours,
            )

        with st.expander("🌐 Other Branches & Office Leave Availability (Co-Admin View)", expanded=False):
            bm_org_branches = render_branch_filter_checkboxes(key_prefix="bm_org_avail")
            bm_org_bmap = get_user_branch_map()
            st.caption(f"🔎 Showing leave & availability overview for: **{format_branch_scope(bm_org_branches)}**")
            df_req_bm_org = bm_scoped_leaves
            if df_req_bm_org is not None and not df_req_bm_org.empty:
                bm_overview = filter_df_by_branches(
                    df_req_bm_org[df_req_bm_org["Status"].isin(["Pending", "Approved"])],
                    bm_org_branches,
                    bm_org_bmap,
                    name_col="Name",
                )
                if not bm_overview.empty:
                    if "page_bm_org" not in st.session_state:
                        st.session_state.page_bm_org = 0
                    bm_per_page = 10
                    bm_tot = len(bm_overview)
                    bm_pages = max(1, (bm_tot - 1) // bm_per_page + 1)
                    if st.session_state.page_bm_org >= bm_pages:
                        st.session_state.page_bm_org = bm_pages - 1
                    b_s = st.session_state.page_bm_org * bm_per_page
                    b_e = b_s + bm_per_page
                    for _, ov_row in bm_overview.iloc[b_s:b_e].iterrows():
                        oc1, oc2 = st.columns([5, 1])
                        ov_br = bm_org_bmap.get(str(ov_row["Name"]).strip(), "HQ")
                        oc1.write(f"**{ov_row['Name']}** ({ov_br}) - {ov_row['LeaveType']} ({ov_row['StartDate']} to {ov_row['EndDate']}) - {ov_row['Status']}")
                        if ov_row["Status"] == "Approved":
                            if oc2.button("📄 Preview PDF", key=f"prev_bm_org_{ov_row['ID']}"):
                                pdf_b_ov = generate_leave_pdf(ov_row)
                                open_pdf_dialog(pdf_b_ov, f"{ov_row['Name']}_{ov_row['StartDate']}_Leave.pdf")
                    st.divider()
                    bp1, bp2, bp3 = st.columns([1, 2, 1])
                    with bp1:
                        if st.session_state.page_bm_org > 0:
                            if st.button("⬅️ Previous", key="prev_bm_org_btn"):
                                st.session_state.page_bm_org -= 1
                                st.rerun()
                    with bp2:
                        st.markdown(f"<div style='text-align: center'>Page {st.session_state.page_bm_org + 1} of {bm_pages}</div>", unsafe_allow_html=True)
                    with bp3:
                        if st.session_state.page_bm_org < bm_pages - 1:
                            if st.button("Next ➡️", key="next_bm_org_btn"):
                                st.session_state.page_bm_org += 1
                                st.rerun()
                else:
                    st.info("No Pending or Approved leaves in the selected branch(es).")
            else:
                st.info("No leave requests available.")
        st.divider()

        st.subheader(f"Pending Leave Requests from {bm_branch} Staff")
        st.caption(f"As Branch Manager, you have direct sanctioning authority for leaves submitted by {bm_branch} employees and accounts.")

        staff_df = load_staff_master()

        if bm_scoped_leaves is not None and not bm_scoped_leaves.empty and "Status" in bm_scoped_leaves.columns:
            pending_branch_leaves = bm_scoped_leaves[
                (bm_scoped_leaves["Name"].isin(branch_staff_names)) &
                (bm_scoped_leaves["Status"].astype(str).str.strip() == "Pending")
            ]

            if pending_branch_leaves.empty:
                st.info(f"✅ No pending leave requests from {bm_branch} staff. All caught up!")
            else:
                st.warning(f"🔔 You have {len(pending_branch_leaves)} pending leave request(s) from your branch staff.")
                for idx, row in pending_branch_leaves.iterrows():
                    is_bm_row_act = _is_active_sheet_row(row)
                    with st.expander(f"🍃 {row['Name']} — {row['LeaveType']} ({row['TotalDays']} Day(s))", expanded=True):
                        c_inf1, c_inf2 = st.columns([3, 2])
                        with c_inf1:
                            st.write(f"**Leave Dates:** `{row['StartDate']}` to `{row['EndDate']}`")
                            st.write(f"**Submitted Date & Time:** `{row.get('ApplicationDate', 'N/A')}`")
                            st.write(f"**Reason / Remarks:** {row.get('Reason', 'N/A')}")
                            if row.get("EffectsServiceYear") == "Yes":
                                st.warning("⚠️ This leave affects service tenure.")

                        with c_inf2:
                            emp_bal = calculate_balances(row['Name'])
                            st.markdown(f"**{row['Name']}'s Balances:**")
                            b1, b2, b3 = st.columns(3)
                            b1.metric("CL", f"{emp_bal.get('CL', 0):g}d")
                            b2.metric("SL", f"{emp_bal.get('SL', 0):g}d")
                            b3.metric("AL", f"{emp_bal.get('AL', 0):g}d")

                        if st.button("📄 Preview Official Leave Form (PDF)", key=f"prev_bm_pend_{row['ID']}"):
                            pdf_bytes = generate_leave_pdf(row)
                            if pdf_bytes:
                                open_pdf_dialog(pdf_bytes, f"{row['Name']}_Leave_Preview.pdf")

                        c_app, c_rej = st.columns(2)
                        with c_app:
                            if st.button("✅ Approve Leave", type="primary", key=f"bm_app_{row['ID']}", use_container_width=True, disabled=not is_bm_row_act):
                                with st.spinner("Approving leave & updating balances..."):
                                    act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                                    if not act_m.empty:
                                        df_requests.loc[act_m, "Status"] = "Approved"
                                        df_requests.loc[act_m, "ApprovedBy"] = f"{bm_name} (Branch Manager)"
                                        _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)

                                    if staff_df is not None:
                                        employee_idx = staff_df[staff_df["Name"] == row["Name"]].index
                                        if not employee_idx.empty:
                                            e_idx = employee_idx[0]
                                            leave_type = str(row["LeaveType"]).replace("Return-", "")
                                            tot_days = float(row["TotalDays"]) if pd.notnull(row["TotalDays"]) else 0.0
                                            try:
                                                l_start = _parse_cta_date(row["StartDate"]) or pd.to_datetime(row["StartDate"]).date()
                                                l_end = _parse_cta_date(row["EndDate"]) or pd.to_datetime(row["EndDate"]).date()
                                                bdown = get_leave_fy_breakdown(l_start, l_end, tot_days)
                                                cur_fy_s, _, _ = get_current_financial_year_dates()
                                                if bdown:
                                                    cur_fy_days = float(bdown[cur_fy_s.year]["days"]) if cur_fy_s.year in bdown else 0.0
                                                    prior_fy_days = sum(float(v["days"]) for y, v in bdown.items() if y < cur_fy_s.year)
                                                else:
                                                    cur_fy_days = tot_days
                                                    prior_fy_days = 0.0
                                            except Exception:
                                                cur_fy_days = tot_days
                                                prior_fy_days = 0.0

                                            if leave_type in ["CL", "SL", "AL", "UL"]:
                                                col_name = f"Used_{leave_type}"
                                                if col_name in staff_df.columns:
                                                    current_used = float(staff_df.at[e_idx, col_name]) if pd.notnull(staff_df.at[e_idx, col_name]) else 0
                                                    charge_days = cur_fy_days if leave_type == "UL" else tot_days
                                                    staff_df.at[e_idx, col_name] = max(0.0, current_used + charge_days)
                                                    balance_col = f"Balance_{leave_type}"
                                                    if balance_col in staff_df.columns:
                                                        current_bal = float(staff_df.at[e_idx, balance_col]) if pd.notnull(staff_df.at[e_idx, balance_col]) else 0
                                                        staff_df.at[e_idx, balance_col] = max(0.0, current_bal - charge_days - (prior_fy_days if leave_type == "UL" else 0.0))
                                                if leave_type == "UL" and prior_fy_days != 0.0:
                                                    if "Cumulative_UL" in staff_df.columns:
                                                        cur_cum_ul = float(staff_df.at[e_idx, "Cumulative_UL"]) if pd.notnull(staff_df.at[e_idx, "Cumulative_UL"]) else 0.0
                                                        staff_df.at[e_idx, "Cumulative_UL"] = max(0.0, cur_cum_ul + prior_fy_days)
                                                    if "Opening_UL" in staff_df.columns:
                                                        cur_open_ul = float(staff_df.at[e_idx, "Opening_UL"]) if pd.notnull(staff_df.at[e_idx, "Opening_UL"]) else 0.0
                                                        staff_df.at[e_idx, "Opening_UL"] = max(0.0, cur_open_ul - prior_fy_days)

                                            if row.get("EffectsServiceYear") == "Yes":
                                                cum_col = "Current Year Leave affecting Service Year"
                                                if cum_col in staff_df.columns:
                                                    current_cum = float(staff_df.at[e_idx, cum_col]) if pd.notnull(staff_df.at[e_idx, cum_col]) else 0
                                                    staff_df.at[e_idx, cum_col] = max(0.0, current_cum + cur_fy_days)
                                                cum_eff_col = "Cummulative leave effecting Service year.(Education Leave & Etc)."
                                                if prior_fy_days != 0.0 and cum_eff_col in staff_df.columns:
                                                    cur_eff_cum = float(staff_df.at[e_idx, cum_eff_col]) if pd.notnull(staff_df.at[e_idx, cum_eff_col]) else 0.0
                                                    staff_df.at[e_idx, cum_eff_col] = max(0.0, cur_eff_cum + prior_fy_days)

                                            penalty_col = "Service Year Penalty"
                                            if penalty_col in staff_df.columns:
                                                def sf_bm(val):
                                                    try: return float(val)
                                                    except: return 0.0
                                                curr_eff = sf_bm(staff_df.at[e_idx, "Current Year Leave affecting Service Year"]) if "Current Year Leave affecting Service Year" in staff_df.columns else 0.0
                                                eff = sf_bm(staff_df.at[e_idx, "Cummulative leave effecting Service year.(Education Leave & Etc)."]) if "Cummulative leave effecting Service year.(Education Leave & Etc)." in staff_df.columns else 0.0
                                                u_ul = sf_bm(staff_df.at[e_idx, "Used_UL"]) if "Used_UL" in staff_df.columns else 0.0
                                                c_ul = sf_bm(staff_df.at[e_idx, "Cumulative_UL"]) if "Cumulative_UL" in staff_df.columns else 0.0
                                                staff_df.at[e_idx, penalty_col] = eff + curr_eff + u_ul + c_ul

                                            _gsheets_retry(conn.update, worksheet="Staff_Master", data=inject_balance_formulas(staff_df))

                                    _invalidate_sheet_cache("LeaveRequests", "Staff_Master")
                                    emp_top = get_user_ntfy_topic(row['Name'])
                                    if emp_top:
                                        send_ntfy_notification(
                                            emp_top,
                                            "Leave Approved ✅",
                                            f"Your request for {row['TotalDays']} day(s) of {row['LeaveType']} has been Approved by Branch Manager {bm_name}!"
                                        )
                                    send_ntfy_notification(
                                        get_role_ntfy_topics("Accounts"),
                                        "Branch Leave Approved 📋",
                                        f"Branch Manager {bm_name} approved {row['TotalDays']} day(s) of {row['LeaveType']} for {row['Name']} ({bm_branch})."
                                    )
                                    st.success(f"✅ Approved {row['Name']}'s leave successfully!")
                                    st.rerun()

                        with c_rej:
                            if st.button("❌ Reject Leave", key=f"bm_rej_{row['ID']}", use_container_width=True, disabled=not is_bm_row_act):
                                with st.spinner("Rejecting leave..."):
                                    act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                                    if not act_m.empty:
                                        df_requests.loc[act_m, "Status"] = "Rejected"
                                        df_requests.loc[act_m, "ApprovedBy"] = f"Rejected by Branch Manager {bm_name}"
                                        _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)
                                        _invalidate_sheet_cache("LeaveRequests")
                                    emp_top = get_user_ntfy_topic(row['Name'])
                                    if emp_top:
                                        send_ntfy_notification(
                                            emp_top,
                                            "Leave Request Rejected ❌",
                                            f"Your {row['LeaveType']} request was rejected by Branch Manager {bm_name}."
                                        )
                                    st.warning(f"Rejected leave request for {row['Name']}.")
                                    st.rerun()

            st.divider()
            st.subheader(f"Approved Leaves (PDF Archive) — {bm_branch} Staff")
            branch_hist_leaves = bm_scoped_leaves[
                (bm_scoped_leaves["Name"].isin(branch_staff_names)) &
                (bm_scoped_leaves["Status"].astype(str).str.strip().isin(["Approved", "Rejected", "Cancelled"]))
            ]

            if branch_hist_leaves.empty:
                st.caption("No processed leave history for this branch yet.")
            else:
                if "page_bm_hist_leaves" not in st.session_state:
                    st.session_state.page_bm_hist_leaves = 0
                bm_lv_per_page = 10
                tot_bm_lv = len(branch_hist_leaves)
                tot_bm_lv_pages = max(1, (tot_bm_lv - 1) // bm_lv_per_page + 1)
                if st.session_state.page_bm_hist_leaves >= tot_bm_lv_pages:
                    st.session_state.page_bm_hist_leaves = tot_bm_lv_pages - 1
                bml_s = st.session_state.page_bm_hist_leaves * bm_lv_per_page
                bml_e = bml_s + bm_lv_per_page

                for _, row in branch_hist_leaves.iloc[bml_s:bml_e].iterrows():
                    row_st = str(row.get("Status", "")).strip()
                    col1, col2 = st.columns([5, 1])
                    st_suffix = "" if row_st == "Approved" else f" — Status: {row_st}"
                    col1.write(f"**{row['Name']}** — {row['LeaveType']}: **{row['TotalDays']} Day(s)** ({row['StartDate']} to {row['EndDate']}){st_suffix}")
                    if col2.button("📄 Preview", key=f"prev_bm_hist_{row['ID']}"):
                        pdf_bytes = generate_leave_pdf(row)
                        if pdf_bytes:
                            open_pdf_dialog(pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave.pdf")

                st.divider()
                p_bml1, p_bml2, p_bml3 = st.columns([1, 2, 1])
                with p_bml1:
                    if st.session_state.page_bm_hist_leaves > 0:
                        if st.button("⬅️ Previous", key="prev_bm_hist_lv_btn"):
                            st.session_state.page_bm_hist_leaves -= 1
                            st.rerun()
                with p_bml2:
                    st.markdown(f"<div style='text-align: center'>Page {st.session_state.page_bm_hist_leaves + 1} of {tot_bm_lv_pages}</div>", unsafe_allow_html=True)
                with p_bml3:
                    if st.session_state.page_bm_hist_leaves < tot_bm_lv_pages - 1:
                        if st.button("Next ➡️", key="next_bm_hist_lv_btn"):
                            st.session_state.page_bm_hist_leaves += 1
                            st.rerun()
        else:
            st.info("No leave requests found.")

    # --- TAB 2: BRANCH TOUR SANCTIONS ---
    with tab_bm_tours:
        st.subheader(f"✈️ Official Tour Sanctions for {branch_full_name} ({bm_branch}) Staff")
        st.caption(
            f"As Branch Manager, you review and support official duty tour applications submitted by {bm_branch} staff "
            f"before they are forwarded to Co-Admin and Admin (Drungche) for final sanction."
        )

        tours_df = tours_df_raw
        if bm_scoped_tours is not None and not bm_scoped_tours.empty:
            branch_tours = bm_scoped_tours[
                (bm_scoped_tours["Name"].isin(branch_staff_names)) |
                ((bm_scoped_tours["Branch"].apply(normalize_branch_code) == bm_branch) & (bm_scoped_tours["Name"] != bm_name))
            ]

            def _is_bm_already_supported(row_s):
                v = "" if pd.isna(row_s.get("BMSupportedBy")) else str(row_s.get("BMSupportedBy")).strip()
                return v not in ("", "nan", "None")

            bm_done_mask = branch_tours.apply(_is_bm_already_supported, axis=1) if not branch_tours.empty else pd.Series([], dtype=bool)
            terminal_st_mask = branch_tours["Status"].astype(str).str.strip().isin([
                "Sanctioned", "Completed", "Settled", "Rejected", "Cancelled",
                "Pending Co-Admin Support", "Pending Admin Sanction",
            ]) if not branch_tours.empty else pd.Series([], dtype=bool)

            pending_branch_tours = branch_tours[~terminal_st_mask & ~bm_done_mask]

            if pending_branch_tours.empty:
                st.info(f"✅ No pending tour requests from {bm_branch} staff. All caught up!")
            else:
                st.warning(f"🔔 You have {len(pending_branch_tours)} tour sanction request(s) waiting for your review & support from your branch staff.")
                for idx, tr in pending_branch_tours.iterrows():
                    is_bm_tr_act = _is_active_sheet_row(tr)
                    with st.expander(f"✈️ {tr['Name']} — Tour to {tr['Destination']} ({tr['TotalDays']} Day(s))", expanded=True):
                        st.write(f"**Travel Dates:** {tr['DepartureDate']} to {tr['ArrivalDate']}")
                        st.write(f"**Conveyance:** {tr.get('ConveyanceMode', 'Road')}")
                        st.write(f"**Assigned Routing:** Co-Admin Support: **{tr.get('SelectedCoAdmin', 'N/A')}** ➔ Sanctioning Admin: **{tr.get('SelectedAdmin', 'N/A')}**")
                        st.write(f"**Current Status:** `{tr.get('Status', 'Pending')}`")

                        raw_legs = tr.get('TravelLegs', '')
                        travel_legs = []
                        if isinstance(raw_legs, list):
                            travel_legs = raw_legs
                        elif isinstance(raw_legs, str) and raw_legs.strip().startswith('['):
                            try:
                                travel_legs = json.loads(raw_legs)
                            except Exception:
                                travel_legs = []

                        if travel_legs and len(travel_legs) > 1:
                            st.markdown("**📋 Destinations & Duty Purposes (ས་གནས་ཁག་དང་ལས་དོན་འགྲེལ་བརྗོད།):**")
                            for leg in travel_legs:
                                l_num = leg.get('leg_num', '')
                                l_purp = str(leg.get('purpose', '')).strip()
                                if l_purp.endswith('.0') and l_purp[:-2].isdigit():
                                    l_purp = l_purp[:-2]
                                l_dest = leg.get('destination', '')
                                l_dep = leg.get('departure_date', '')
                                l_arr = leg.get('arrival_date', '')
                                st.markdown(f"- **{l_num}. ({l_dest}):** `{l_dep}` to `{l_arr}` — **Purpose:** {l_purp}")
                        else:
                            purp_display = str(tr.get('Purpose', '')).strip()
                            if purp_display.endswith('.0') and purp_display[:-2].isdigit():
                                purp_display = purp_display[:-2]
                            st.write(f"**Duty Purpose:** {purp_display}")

                        bm_name_val = str(tr.get('BranchManager', '')).strip()
                        bm_supp_val = str(tr.get('BMSupportedBy', '')).strip()
                        if bm_name_val and bm_name_val not in ['nan', 'None', '']:
                            if bm_supp_val and bm_supp_val not in ['nan', 'None', '']:
                                st.success(f"✅ Branch Manager Recommendation: **Supported by {bm_supp_val}**")
                            else:
                                st.warning(f"⏳ Branch Manager Recommendation: **Pending Branch Manager Support (Or Approve) from {bm_name_val}**")

                        supp_by = str(tr.get('SupportedBy', '')).strip()
                        if supp_by and supp_by not in ['nan', 'None', '', 'N/A (Direct Application)']:
                            st.success(f"✅ Endorsed by Co-Admin: **{supp_by}**")

                        if st.button("📄 Preview Official Tour Form (PDF)", key=f"prev_tr_bm_{tr['ID']}"):
                            pdf_data = generate_tour_sanction_pdf(tr)
                            if pdf_data:
                                open_pdf_dialog(pdf_data, f"{tr['Name']}_Tour_Preview.pdf")

                        assigned_adm = str(tr.get("SelectedAdmin", "")).strip()
                        sel_co = str(tr.get("SelectedCoAdmin", "")).strip()
                        bm_supported = bool(tr.get("BMSupportedBy") and str(tr.get("BMSupportedBy")).strip() not in ["", "nan", "None"])

                        if bm_supported:
                            st.success(f"✅ You endorsed this tour as Branch Manager ({tr.get('BMSupportedBy')}). Current Status: `{tr.get('Status')}`")
                        else:
                            c_sup, c_rej = st.columns(2)
                            with c_sup:
                                if st.button("🤝 Support", type="primary", key=f"bm_sup_btn_{tr['ID']}", use_container_width=True, disabled=not is_bm_tr_act):
                                    with st.spinner("Supporting Tour & Forwarding..."):
                                        act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(tr["ID"])].index
                                        if sel_co and sel_co not in ["Not-Applicable", "N/A", "nan", "None", ""]:
                                            next_status = "Pending Co-Admin Support"
                                        else:
                                            next_status = "Pending Admin Sanction"
                                        if not act_tm.empty:
                                            tours_df.loc[act_tm, "BMSupportedBy"] = bm_name
                                            tours_df.loc[act_tm, "Status"] = next_status
                                            _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                            _invalidate_sheet_cache("TourSanctions")

                                        emp_top = get_user_ntfy_topic(tr['Name'])
                                        acct_topics = get_branch_accounts_topics(bm_branch)
                                        send_ntfy_notification(
                                            acct_topics,
                                            "Tour Endorsed by Branch Manager ✈️",
                                            f"Branch Manager {bm_name} ({bm_branch}) supported Tour for {tr['Name']} to {tr['Destination']}."
                                        )
                                        if next_status == "Pending Co-Admin Support":
                                            co_top = get_user_ntfy_topic(sel_co)
                                            if co_top:
                                                send_ntfy_notification(
                                                    co_top,
                                                    "Tour Request to Support ✈️",
                                                    f"Branch Manager {bm_name} ({bm_branch}) supported {tr['Name']}'s Tour to {tr['Destination']}. Now awaiting your Co-Admin support."
                                                )
                                            adm_top = get_user_ntfy_topic(assigned_adm) or get_role_ntfy_topics("Admin")
                                            send_ntfy_notification(
                                                adm_top,
                                                "Tour Request Supported by Branch Manager 🤝",
                                                f"Branch Manager {bm_name} ({bm_branch}) supported Tour to {tr['Destination']} for {tr['Name']}. Forwarded to Co-Admin ({sel_co}) & Admin for sanction."
                                            )
                                            if emp_top:
                                                send_ntfy_notification(
                                                    emp_top,
                                                    "Tour Supported by Branch Manager 🤝",
                                                    f"Branch Manager {bm_name} supported your Tour to {tr['Destination']}. Forwarded to Co-Admin ({sel_co}) & Admin ({assigned_adm})."
                                                )
                                            st.success(f"✅ Supported Tour for {tr['Name']}! Forwarded to Co-Admin {sel_co} & Admin {assigned_adm}.")
                                        else:
                                            adm_top = get_user_ntfy_topic(assigned_adm) or get_role_ntfy_topics("Admin")
                                            send_ntfy_notification(
                                                adm_top,
                                                "Tour Request Supported by Branch Manager 🤝",
                                                f"Branch Manager {bm_name} ({bm_branch}) supported Tour to {tr['Destination']} for {tr['Name']}. Forwarded for final sanction."
                                            )
                                            if emp_top:
                                                send_ntfy_notification(
                                                    emp_top,
                                                    "Tour Supported by Branch Manager 🤝",
                                                    f"Branch Manager {bm_name} supported your Tour to {tr['Destination']}. Forwarded to Admin ({assigned_adm}) for final sanction."
                                                )
                                            st.success(f"✅ Supported Tour for {tr['Name']}! Forwarded to Admin {assigned_adm}.")
                                        st.rerun()

                            with c_rej:
                                if st.button("❌ Reject Tour", key=f"bm_rej_btn_{tr['ID']}", use_container_width=True, disabled=not is_bm_tr_act):
                                    with st.spinner("Rejecting Tour..."):
                                        act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(tr["ID"])].index
                                        if not act_tm.empty:
                                            tours_df.loc[act_tm, "Status"] = "Rejected"
                                            tours_df.loc[act_tm, "ApprovedBy"] = f"Rejected by Branch Manager {bm_name}"
                                            tours_df.loc[act_tm, "SanctionedBy"] = f"Rejected by Branch Manager {bm_name}"
                                            _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                            _invalidate_sheet_cache("TourSanctions")

                                        emp_top = get_user_ntfy_topic(tr['Name'])
                                        acct_topics = get_branch_accounts_topics(bm_branch)
                                        send_ntfy_notification(
                                            acct_topics,
                                            "Tour Endorsed by Branch Manager ✈️",
                                            f"Branch Manager {bm_name} ({bm_branch}) supported Tour for {tr['Name']} to {tr['Destination']}."
                                        )
                                        if emp_top:
                                            send_ntfy_notification(
                                                emp_top,
                                                "Tour Request Rejected ❌",
                                                f"Your Tour application to {tr['Destination']} was rejected by Branch Manager {bm_name}."
                                            )

                                        st.warning(f"Rejected Tour request for {tr['Name']}.")
                                        st.rerun()

            st.divider()
            st.subheader(f"Sanctioned & Historical Tours for {bm_branch} Staff")
            branch_hist_tours = branch_tours[terminal_st_mask | bm_done_mask]

            if branch_hist_tours.empty:
                st.caption("No processed tour records for this branch yet.")
            else:
                if "page_bm_hist_tours" not in st.session_state:
                    st.session_state.page_bm_hist_tours = 0
                bm_tr_per_page = 10
                tot_bm_tr = len(branch_hist_tours)
                tot_bm_tr_pages = max(1, (tot_bm_tr - 1) // bm_tr_per_page + 1)
                if st.session_state.page_bm_hist_tours >= tot_bm_tr_pages:
                    st.session_state.page_bm_hist_tours = tot_bm_tr_pages - 1
                bmt_s = st.session_state.page_bm_hist_tours * bm_tr_per_page
                bmt_e = bmt_s + bm_tr_per_page

                for _, r in branch_hist_tours.iloc[bmt_s:bmt_e].iterrows():
                    c_info, c_pdf = st.columns([5, 1])
                    c_info.write(f"**{r['Name']}** — Tour to **{r['Destination']}** ({r['DepartureDate']} to {r['ArrivalDate']}) — Status: {r['Status']}")
                    if c_pdf.button("📄 PDF", key=f"bm_arch_tr_pdf_{r['ID']}"):
                        pdf_bytes = generate_tour_sanction_pdf(r)
                        if pdf_bytes:
                            open_pdf_dialog(pdf_bytes, f"{r['Name']}_{r['DepartureDate']}_Tour.pdf")

                st.divider()
                pt_bm1, pt_bm2, pt_bm3 = st.columns([1, 2, 1])
                with pt_bm1:
                    if st.session_state.page_bm_hist_tours > 0:
                        if st.button("⬅️ Previous Tours", key="prev_bm_hist_tr_btn"):
                            st.session_state.page_bm_hist_tours -= 1
                            st.rerun()
                with pt_bm2:
                    st.markdown(f"<div style='text-align: center'>Tour Page {st.session_state.page_bm_hist_tours + 1} of {tot_bm_tr_pages}</div>", unsafe_allow_html=True)
                with pt_bm3:
                    if st.session_state.page_bm_hist_tours < tot_bm_tr_pages - 1:
                        if st.button("Next Tours ➡️", key="next_bm_hist_tr_btn"):
                            st.session_state.page_bm_hist_tours += 1
                            st.rerun()
        else:
            st.info("No tour records found.")

    # --- TAB 3: MY LEAVE & TOUR APPLICATIONS ---
    with tab_bm_my:
        employee_dashboard(hide_title=True)

    # --- TAB 4: BRANCH STAFF & BALANCES ---
    with tab_bm_staff:
        st.subheader(f"👥 {branch_full_name} Staff Roster & Balances")
        staff_df = load_staff_master()
        if staff_df is not None and not staff_df.empty:
            b_staff = filter_df_by_branches(staff_df, [bm_branch], get_user_branch_map(), name_col="Name")
            if b_staff.empty:
                st.info(f"No staff records found for branch {bm_branch}.")
            else:
                disp_b = [
                    c for c in [
                        "Name", "Post", "Balance_CL", "Balance_SL", "Balance_AL", "Balance_UL",
                        "Used_UL", "Cumulative_UL", "Last_UL_Milestone",
                        "Current Year Leave affecting Service Year",
                        "Cummulative leave effecting Service year.(Education Leave & Etc).",
                        "Actual Service Year", "Active_FY", "Branch",
                    ] if c in b_staff.columns
                ]
                st.dataframe(b_staff[disp_b], use_container_width=True)
                st.divider()
                current_user = st.session_state.get("user_name", bm_name)
                # Anti-self-adjustment: exclude Branch Manager themself from branch adjustment roster
                bm_adj_staff = b_staff[b_staff["Name"].astype(str).str.strip() != str(current_user).strip()] if b_staff is not None else b_staff
                render_balance_adjustment_panel(
                    key_prefix=f"bm_adj_{bm_branch}",
                    scoped_staff_df=bm_adj_staff,
                    full_staff_df=staff_df,
                    caption=f"Branch Manager discrepancy corrections are strictly scoped to **{branch_full_name} ({bm_branch})** staff only.",
                    allowed_branches=[bm_branch],
                    exclude_user=current_user,
                )


def admin_dashboard():
    render_active_leaves_banner()
    st.title("Admin Dashboard")
    
    admin_tab_options = ["Admin Tasks (Leaves)", "✈️ Tour Sanctions", "My Leave Profile", "⚙️ System Settings"]
    if "admin_tabs" not in st.session_state or st.session_state.admin_tabs not in admin_tab_options:
        st.session_state.admin_tabs = "Admin Tasks (Leaves)"

    tab1, tab_trs, tab2, tab3 = st.tabs(
        admin_tab_options,
        key="admin_tabs",
        on_change="rerun"
    )
    
    with tab2:
        employee_dashboard(hide_title=True)
        
    with tab3:
        render_system_settings(is_admin=True)
        
    with tab1:
        df_requests = load_leave_requests()
        tours_df_adm_raw = load_tour_sanctions()
        adm_fy_sel = render_fy_scope_selector(
            key="admin_fy_scope",
            label="📅 Select Financial Year (FY) Scope for Leaves & Tours",
            include_all=True,
            leave_df=df_requests,
            tour_df=tours_df_adm_raw,
        )
        adm_fy_yr = adm_fy_sel["fy_year"] if isinstance(adm_fy_sel, dict) else None
        adm_fy_records = load_fy_records(adm_fy_yr, leave_df_active=df_requests, tour_df_active=tours_df_adm_raw)
        adm_scoped_leaves = adm_fy_records["leaves"]
        adm_scoped_tours = adm_fy_records["tours"]

        with st.expander("📅 View Organization Calendar", expanded=False):
            render_organization_calendar(
                fy_year_override=adm_fy_yr,
                leaves_override=adm_scoped_leaves,
                tours_override=adm_scoped_tours,
            )

        allowed_branches_adm = get_active_visible_branches()
        branch_map_adm = get_user_branch_map()

        with st.expander("📊 Master Balance Table", expanded=False):
            render_master_balance_table()
        st.divider()
        staff_df = load_staff_master()
        st.subheader("Pending Approvals", anchor="pending-approvals")
        if adm_scoped_leaves is not None and not adm_scoped_leaves.empty and "Status" in adm_scoped_leaves.columns:
            users_df = load_users_data()
            branch_subordinates = []
            if users_df is not None and not users_df.empty:
                branch_subordinates = users_df[
                    (users_df["Branch"].isin(["SB", "NB", "DB"])) & 
                    (users_df["Role"].isin(["Employee", "Accounts"]))
                ]["Name"].tolist()

            # Admin approves HQ staff leaves and Branch Manager leaves (branch subordinates are approved by their Branch Manager)
            pending_requests = adm_scoped_leaves[
                (adm_scoped_leaves["Status"] == "Pending") &
                (~adm_scoped_leaves["Name"].isin(branch_subordinates))
            ]
            if pending_requests.empty:
                st.info("No pending requests to approve. Good job!")
            else:
                st.error(f"🔔 You have {len(pending_requests)} leave request(s) waiting for approval!")
                for idx, row in pending_requests.iterrows():
                    is_adm_pend_act = _is_active_sheet_row(row)
                    with st.expander(f"{row['Name']} - {row['LeaveType']} ({row['TotalDays']} days)"):
                        st.write(f"**Dates:** {row['StartDate']} to {row['EndDate']}")
                        st.write(f"**Submitted Date & Time:** `{row.get('ApplicationDate', 'N/A')}`")
                        st.write(f"**Department:** {row.get('Department', '')}")
                        if "Other" in str(row['LeaveType']) or row.get("EffectsServiceYear") == "Yes":
                            st.warning(f"🚨 **ATTENTION:** This is an '{row['LeaveType']}' leave. The employee has checked 'EffectsServiceYear = {row.get('EffectsServiceYear')}'. Please double-check if this type of leave (e.g. Education Leave) should delay their service year before approving!")
                        co_status = "✅ Supported" if str(row.get('CoAdminAcknowledged', '')).strip().lower() == "supported" else "⏳ Pending"
                        co_admin_name = str(row.get('SelectedCoAdmin', '')).strip()
                        if co_admin_name and co_admin_name.lower() not in ['none', 'nan', '']:
                            st.caption(f"**Co-Admin ({co_admin_name}):** {co_status}")
                        else:
                            st.caption(f"**Co-Admin:** {co_status}")
                        if st.button("📄 Preview PDF", key=f"preview_pending_admin_{row['ID']}"):
                            pdf_bytes = generate_leave_pdf(row)
                            open_pdf_dialog(pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave_Preview.pdf")
                        col1, col2 = st.columns(2)
                        if col1.button("Approve", key=f"approve_{row['ID']}", disabled=not is_adm_pend_act):
                            with st.spinner('Processing... Please wait and do not click again.'):
                                act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                                if not act_m.empty:
                                    df_requests.loc[act_m, "Status"] = "Approved"
                                    df_requests.loc[act_m, "ApprovedBy"] = st.session_state.user_name
                                    _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)
                                if staff_df is not None:
                                    employee_idx = staff_df[staff_df["Name"] == row["Name"]].index
                                    if not employee_idx.empty:
                                        e_idx = employee_idx[0]
                                        leave_type = row["LeaveType"]
                                        actual_leave_type = leave_type.replace("Return-", "")
                                        tot_days = float(row["TotalDays"]) if pd.notnull(row["TotalDays"]) else 0.0
                                        try:
                                            l_start = _parse_cta_date(row["StartDate"]) or pd.to_datetime(row["StartDate"]).date()
                                            l_end = _parse_cta_date(row["EndDate"]) or pd.to_datetime(row["EndDate"]).date()
                                            bdown = get_leave_fy_breakdown(l_start, l_end, tot_days)
                                            cur_fy_s, _, _ = get_current_financial_year_dates()
                                            if bdown:
                                                cur_fy_days = float(bdown[cur_fy_s.year]["days"]) if cur_fy_s.year in bdown else 0.0
                                                prior_fy_days = sum(float(v["days"]) for y, v in bdown.items() if y < cur_fy_s.year)
                                            else:
                                                cur_fy_days = tot_days
                                                prior_fy_days = 0.0
                                        except Exception:
                                            cur_fy_days = tot_days
                                            prior_fy_days = 0.0

                                        if actual_leave_type in ["CL", "SL", "AL", "UL"]:
                                            col_name = f"Used_{actual_leave_type}"
                                            if col_name in staff_df.columns:
                                                current_used = float(staff_df.at[e_idx, col_name]) if pd.notnull(staff_df.at[e_idx, col_name]) else 0
                                                charge_days = cur_fy_days if actual_leave_type == "UL" else tot_days
                                                staff_df.at[e_idx, col_name] = max(0.0, current_used + charge_days)
                                                balance_col = f"Balance_{actual_leave_type}"
                                                if balance_col in staff_df.columns:
                                                    current_bal = float(staff_df.at[e_idx, balance_col]) if pd.notnull(staff_df.at[e_idx, balance_col]) else 0
                                                    staff_df.at[e_idx, balance_col] = max(0.0, current_bal - charge_days - (prior_fy_days if actual_leave_type == "UL" else 0.0))
                                            if actual_leave_type == "UL" and prior_fy_days != 0.0:
                                                if "Cumulative_UL" in staff_df.columns:
                                                    cur_cum_ul = float(staff_df.at[e_idx, "Cumulative_UL"]) if pd.notnull(staff_df.at[e_idx, "Cumulative_UL"]) else 0.0
                                                    staff_df.at[e_idx, "Cumulative_UL"] = max(0.0, cur_cum_ul + prior_fy_days)
                                                if "Opening_UL" in staff_df.columns:
                                                    cur_open_ul = float(staff_df.at[e_idx, "Opening_UL"]) if pd.notnull(staff_df.at[e_idx, "Opening_UL"]) else 0.0
                                                    staff_df.at[e_idx, "Opening_UL"] = max(0.0, cur_open_ul - prior_fy_days)
                                        if row.get("EffectsServiceYear") == "Yes":
                                            cum_col = "Current Year Leave affecting Service Year"
                                            if cum_col in staff_df.columns:
                                                current_cum = float(staff_df.at[e_idx, cum_col]) if pd.notnull(staff_df.at[e_idx, cum_col]) else 0
                                                staff_df.at[e_idx, cum_col] = max(0.0, current_cum + cur_fy_days)
                                            cum_eff_col = "Cummulative leave effecting Service year.(Education Leave & Etc)."
                                            if prior_fy_days != 0.0 and cum_eff_col in staff_df.columns:
                                                cur_eff_cum = float(staff_df.at[e_idx, cum_eff_col]) if pd.notnull(staff_df.at[e_idx, cum_eff_col]) else 0.0
                                                staff_df.at[e_idx, cum_eff_col] = max(0.0, cur_eff_cum + prior_fy_days)
                                        penalty_col = "Service Year Penalty"
                                        if penalty_col in staff_df.columns:
                                            def sf(val):
                                                try: return float(val)
                                                except: return 0.0
                                            curr_eff = sf(staff_df.at[e_idx, "Current Year Leave affecting Service Year"]) if "Current Year Leave affecting Service Year" in staff_df.columns else 0.0
                                            eff = sf(staff_df.at[e_idx, "Cummulative leave effecting Service year.(Education Leave & Etc)."]) if "Cummulative leave effecting Service year.(Education Leave & Etc)." in staff_df.columns else 0.0
                                            u_ul = sf(staff_df.at[e_idx, "Used_UL"]) if "Used_UL" in staff_df.columns else 0.0
                                            c_ul = sf(staff_df.at[e_idx, "Cumulative_UL"]) if "Cumulative_UL" in staff_df.columns else 0.0
                                            staff_df.at[e_idx, penalty_col] = eff + curr_eff + u_ul + c_ul
                                        _gsheets_retry(conn.update, worksheet="Staff_Master", data=inject_balance_formulas(staff_df))
                                _invalidate_sheet_cache("LeaveRequests", "Staff_Master")
                                st.success(f"Approved {row['Name']}'s request!")
                                # 1. Notify Employee (Applicant)
                                emp_top = get_user_ntfy_topic(row['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Leave Approved ✅",
                                        f"Your request for {row['TotalDays']} day(s) of {row['LeaveType']} has been Approved by Admin {st.session_state.user_name}!"
                                    )
                                # 2. Notify Accountants
                                send_ntfy_notification(
                                    get_role_ntfy_topics("Accounts"),
                                    "Leave Approved - Action Required 📝",
                                    f"Admin approved {row['TotalDays']} day(s) of {row['LeaveType']} for {row['Name']}. Please punch it in the register!"
                                )
                                # 3. Notify Selected Co-Admin
                                selected_co = str(row.get('SelectedCoAdmin', '')).strip()
                                if selected_co and selected_co.lower() not in ['none', 'nan', '']:
                                    co_top = get_user_ntfy_topic(selected_co)
                                    if co_top:
                                        send_ntfy_notification(
                                            co_top,
                                            "Leave Approved ✅",
                                            f"Admin approved {row['TotalDays']} day(s) of leave for {row['Name']}."
                                        )
                                st.rerun()
                        if col2.button("Reject", key=f"reject_{row['ID']}", disabled=not is_adm_pend_act):
                            with st.spinner('Processing... Please wait and do not click again.'):
                                act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                                if not act_m.empty:
                                    df_requests.loc[act_m, "Status"] = "Rejected"
                                    _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)
                                    _invalidate_sheet_cache("LeaveRequests")
                                emp_top = get_user_ntfy_topic(row['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Leave Not Approved ❌",
                                        f"Your {row['LeaveType']} request for {row['TotalDays']} day(s) was not approved by Admin {st.session_state.user_name}."
                                    )
                                st.warning(f"Rejected {row['Name']}'s request.")
                                st.rerun()
        st.divider()
        st.subheader("Approved Leaves (PDF Archive)")
        if allowed_branches_adm:
            st.caption(f"🔎 Showing branches: **{format_branch_scope(allowed_branches_adm)}**")
        approved_requests = filter_df_by_branches(
            adm_scoped_leaves[adm_scoped_leaves["Status"] == "Approved"] if (adm_scoped_leaves is not None and not adm_scoped_leaves.empty and "Status" in adm_scoped_leaves.columns) else pd.DataFrame(),
            allowed_branches_adm,
            branch_map_adm,
            name_col="Name"
        )
        if approved_requests is not None and not approved_requests.empty:
            if "page_admin" not in st.session_state:
                st.session_state.page_admin = 0
                
            items_per_page = 10
            total_items = len(approved_requests)
            total_pages = max(1, (total_items - 1) // items_per_page + 1)
            
            # Ensure page is within bounds
            if st.session_state.page_admin >= total_pages:
                st.session_state.page_admin = total_pages - 1
                
            start_idx = st.session_state.page_admin * items_per_page
            end_idx = start_idx + items_per_page
            current_page_df = approved_requests.iloc[start_idx:end_idx]
            
            for idx, row in current_page_df.iterrows():
                col1, col2 = st.columns([5, 1])
                col1.write(f"**{row['Name']}** — {row['LeaveType']}: **{row['TotalDays']} Day(s)** ({row['StartDate']} to {row['EndDate']})")
                if col2.button("📄 Preview", key=f"prev_admin_{row['ID']}"):
                    pdf_bytes = generate_leave_pdf(row)
                    open_pdf_dialog(pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave.pdf")
            
            # Pagination Controls
            st.divider()
            p_col1, p_col2, p_col3 = st.columns([1, 2, 1])
            with p_col1:
                if st.session_state.page_admin > 0:
                    if st.button("⬅️ Previous", key="prev_admin_btn"):
                        st.session_state.page_admin -= 1
                        st.rerun()
            with p_col2:
                st.markdown(f"<div style='text-align: center'>Page {st.session_state.page_admin + 1} of {total_pages}</div>", unsafe_allow_html=True)
            with p_col3:
                if st.session_state.page_admin < total_pages - 1:
                    if st.button("Next ➡️", key="next_admin_btn"):
                        st.session_state.page_admin += 1
                        st.rerun()
        else:
            st.info("No approved leaves found.")
    with tab_trs:
        st.subheader("Official Tour Sanctions (Final Approval Authority)", anchor="tour-sanctions-admin")
        st.caption("Central Admin holds sole sanctioning authority for official duty tours.")
        tours_df = tours_df_adm_raw
        if adm_scoped_tours is not None and not adm_scoped_tours.empty:
            def _admin_can_see_pending_tour(row_t):
                st_clean = str(row_t.get("Status", "")).strip()
                if st_clean not in (
                    "Pending Admin Sanction", "Pending Sanction", "Pending",
                    "Pending Recommendation", "Pending Co-Admin Support",
                ):
                    return False
                br_t = normalize_branch_code(str(row_t.get("Branch", "")))
                bm_assigned = "" if pd.isna(row_t.get("BranchManager")) else str(row_t.get("BranchManager")).strip()
                bm_supported = "" if pd.isna(row_t.get("BMSupportedBy")) else str(row_t.get("BMSupportedBy")).strip()
                if bm_assigned in ("nan", "None"):
                    bm_assigned = ""
                if bm_supported in ("nan", "None"):
                    bm_supported = ""
                # Branch subordinate tour must be supported by Branch Manager first before Co-Admin & Admin see it
                if bm_assigned and not bm_supported:
                    return False
                return True

            pending_tours = adm_scoped_tours[adm_scoped_tours.apply(_admin_can_see_pending_tour, axis=1)]
            if pending_tours.empty:
                st.info("No pending tour sanctions to approve. Good job!")
            else:
                st.error(f"🔔 You have {len(pending_tours)} tour sanction request(s) waiting for approval!")
                for idx, tr in pending_tours.iterrows():
                    is_adm_tr_act = _is_active_sheet_row(tr)
                    with st.expander(f"✈️ {tr['Name']} — Tour to {tr['Destination']} ({tr['TotalDays']} Day(s))", expanded=True):
                        st.write(f"**Travel Dates:** {tr['DepartureDate']} to {tr['ArrivalDate']}")
                        st.write(f"**Conveyance:** {tr.get('ConveyanceMode', 'Road')}")
                        st.write(f"**Assigned Routing:** Co-Admin Support: **{tr.get('SelectedCoAdmin', 'N/A')}** ➔ Sanctioning Admin: **{tr.get('SelectedAdmin', 'N/A')}**")
                        st.write(f"**Current Status:** `{tr.get('Status', 'Pending')}`")
                        
                        raw_legs = tr.get('TravelLegs', '')
                        travel_legs = []
                        if isinstance(raw_legs, list):
                            travel_legs = raw_legs
                        elif isinstance(raw_legs, str) and raw_legs.strip().startswith('['):
                            try:
                                travel_legs = json.loads(raw_legs)
                            except Exception:
                                travel_legs = []

                        if travel_legs and len(travel_legs) > 1:
                            st.markdown("**📋 Destinations & Duty Purposes (ས་གནས་ཁག་དང་ལས་དོན་འགྲེལ་བརྗོད།):**")
                            for leg in travel_legs:
                                l_num = leg.get('leg_num', '')
                                l_purp = str(leg.get('purpose', '')).strip()
                                if l_purp.endswith('.0') and l_purp[:-2].isdigit():
                                    l_purp = l_purp[:-2]
                                l_dest = leg.get('destination', '')
                                l_dep = leg.get('departure_date', '')
                                l_arr = leg.get('arrival_date', '')
                                st.markdown(f"- **{l_num}. ({l_dest}):** `{l_dep}` to `{l_arr}` — **Purpose:** {l_purp}")
                        else:
                            purp_display = str(tr.get('Purpose', '')).strip()
                            if purp_display.endswith('.0') and purp_display[:-2].isdigit():
                                purp_display = purp_display[:-2]
                            st.write(f"**Duty Purpose:** {purp_display}")
                        
                        bm_name_val = str(tr.get('BranchManager', '')).strip()
                        bm_supp_val = str(tr.get('BMSupportedBy', '')).strip()
                        if bm_name_val and bm_name_val not in ['nan', 'None', '']:
                            if bm_supp_val and bm_supp_val not in ['nan', 'None', '']:
                                st.success(f"✅ Branch Manager Recommendation: **Supported by {bm_supp_val}**")
                            else:
                                st.warning(f"⏳ Branch Manager Recommendation: **Pending Branch Manager Support (Or Approve) from {bm_name_val}**")

                        supp_by = str(tr.get('SupportedBy', '')).strip()
                        assigned_co = str(tr.get('SelectedCoAdmin', '')).strip()
                        if supp_by and supp_by not in ['nan', 'None', '', 'N/A (Direct Application)']:
                            st.success(f"✅ Co-Admin Recommendation: **Supported by {supp_by}**")
                        elif assigned_co == "Not-Applicable" or supp_by == "N/A (Direct Application)":
                            st.info("ℹ️ Co-Admin Support: **Not Applicable (Direct Application)**")
                        else:
                            st.warning(f"⏳ Co-Admin Recommendation: **Awaiting review by {assigned_co if assigned_co else 'Co-Admin'}**")

                        assigned_adm = str(tr.get('SelectedAdmin', '')).strip()
                        if assigned_adm and assigned_adm not in ['nan', 'None', '']:
                            st.caption(f"**Assigned Sanctioning Authority:** Admin {assigned_adm}")
                            
                        if st.button("📄 Preview Tour PDF", key=f"prev_tr_admin_{tr['ID']}"):
                            pdf_data = generate_tour_sanction_pdf(tr)
                            if pdf_data:
                                open_pdf_dialog(pdf_data, f"{tr['Name']}_Tour_Preview.pdf")
                                
                        c1, c2 = st.columns(2)
                        if c1.button("Sanction Tour", type="primary", key=f"sanc_btn_{tr['ID']}", disabled=not is_adm_tr_act):
                            with st.spinner("Granting Official Tour Sanction..."):
                                act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(tr["ID"])].index
                                if not act_tm.empty:
                                    tours_df.loc[act_tm, "Status"] = "Sanctioned"
                                    tours_df.loc[act_tm, "ApprovedBy"] = st.session_state.user_name
                                    _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                    _invalidate_sheet_cache("TourSanctions")
                                # 1. Notify Employee (Applicant)
                                emp_top = get_user_ntfy_topic(tr['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Tour Sanctioned Officially! ✈️",
                                        f"Your Tour to {tr['Destination']} ({tr['DepartureDate']} to {tr['ArrivalDate']}) has been officially Sanctioned by Admin {st.session_state.user_name}!"
                                    )
                                # 2. Notify Accountants
                                send_ntfy_notification(
                                    get_role_ntfy_topics("Accounts"),
                                    "Tour Sanctioned — Action Required ✈️",
                                    f"Admin sanctioned Tour for {tr['Name']} to {tr['Destination']}. Please punch in Tour Register!"
                                )
                                # 3. Notify Selected Co-Admin
                                co_top = get_user_ntfy_topic(tr.get('SupportedBy') or tr.get('SelectedCoAdmin'))
                                if co_top:
                                    send_ntfy_notification(
                                        co_top,
                                        "Tour Sanctioned ✅",
                                        f"Admin sanctioned Tour for {tr['Name']} to {tr['Destination']}."
                                    )
                                st.success(f"Sanctioned Tour for {tr['Name']}!")
                                st.rerun()
                                
                        if c2.button("Reject Tour", key=f"rej_btn_admin_{tr['ID']}", disabled=not is_adm_tr_act):
                            with st.spinner("Rejecting Tour..."):
                                act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(tr["ID"])].index
                                if not act_tm.empty:
                                    tours_df.loc[act_tm, "Status"] = "Rejected"
                                    tours_df.loc[act_tm, "ApprovedBy"] = f"Rejected by {st.session_state.user_name}"
                                    _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                    _invalidate_sheet_cache("TourSanctions")
                                emp_top = get_user_ntfy_topic(tr['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Tour Request Rejected ❌",
                                        f"Your Tour application to {tr['Destination']} was rejected by Admin {st.session_state.user_name}."
                                    )
                                st.warning(f"Rejected Tour for {tr['Name']}.")
                                st.rerun()

            st.divider()
            st.subheader("Sanctioned & Historical Tours")
            if allowed_branches_adm:
                st.caption(f"🔎 Showing branches: **{', '.join(allowed_branches_adm)}**")
            all_sanc = filter_df_by_branches(
                adm_scoped_tours[adm_scoped_tours["Status"].isin(["Sanctioned", "Completed", "Settled"])],
                allowed_branches_adm,
                branch_map_adm,
                name_col="Name"
            )
            if all_sanc is None or all_sanc.empty:
                st.info("No sanctioned tours on record.")
            else:
                if "page_admin_tours" not in st.session_state:
                    st.session_state.page_admin_tours = 0
                tr_per_page = 10
                tot_tr_items = len(all_sanc)
                tot_tr_pages = max(1, (tot_tr_items - 1) // tr_per_page + 1)
                if st.session_state.page_admin_tours >= tot_tr_pages:
                    st.session_state.page_admin_tours = tot_tr_pages - 1
                t_s_idx = st.session_state.page_admin_tours * tr_per_page
                t_e_idx = t_s_idx + tr_per_page
                for _, r in all_sanc.iloc[t_s_idx:t_e_idx].iterrows():
                    c_info, c_pdf = st.columns([5, 1])
                    c_info.write(f"**{r['Name']}** — Tour to **{r['Destination']}** ({r['DepartureDate']} to {r['ArrivalDate']}) — Status: {r['Status']}")
                    if c_pdf.button("📄 PDF", key=f"arch_tr_pdf_{r['ID']}"):
                        pdf_bytes = generate_tour_sanction_pdf(r)
                        if pdf_bytes:
                            open_pdf_dialog(pdf_bytes, f"{r['Name']}_{r['DepartureDate']}_Tour.pdf")
                if tot_tr_pages > 1:
                    pt1, pt2, pt3 = st.columns([1, 2, 1])
                    with pt1:
                        if st.session_state.page_admin_tours > 0:
                            if st.button("⬅️ Previous Tours", key="prev_admin_tr_btn"):
                                st.session_state.page_admin_tours -= 1
                                st.rerun()
                    with pt2:
                        st.markdown(f"<div style='text-align: center'>Tour Page {st.session_state.page_admin_tours + 1} of {tot_tr_pages}</div>", unsafe_allow_html=True)
                    with pt3:
                        if st.session_state.page_admin_tours < tot_tr_pages - 1:
                            if st.button("Next Tours ➡️", key="next_admin_tr_btn"):
                                st.session_state.page_admin_tours += 1
                                st.rerun()
        else:
            st.info("No tour data available.")



def coadmin_dashboard():
    render_active_leaves_banner()
    st.title("Co-Admin Dashboard")

    coadmin_tab_options = ["🍃 Leaves to Support", "✈️ Tours to Support", "📅 Calendar & Availability", "My Leave Profile"]
    if "coadmin_tabs" not in st.session_state or st.session_state.coadmin_tabs not in coadmin_tab_options:
        st.session_state.coadmin_tabs = "🍃 Leaves to Support"

    tab_lv_sup, tab_tr_sup, tab_cal, tab_profile = st.tabs(
        coadmin_tab_options,
        key="coadmin_tabs",
        on_change="rerun"
    )

    with tab_lv_sup:
        st.subheader("Leaves to Support", anchor="leaves-to-support")
        df_requests = load_leave_requests()
        tours_df_co_raw = load_tour_sanctions()
        co_fy_sel = render_fy_scope_selector(
            key="coadmin_fy_scope",
            label="📅 Select Financial Year (FY) Scope for Co-Admin Leaves & Tours",
            include_all=True,
            leave_df=df_requests,
            tour_df=tours_df_co_raw,
        )
        co_fy_yr = co_fy_sel["fy_year"] if isinstance(co_fy_sel, dict) else None
        co_fy_records = load_fy_records(co_fy_yr, leave_df_active=df_requests, tour_df_active=tours_df_co_raw)
        co_scoped_leaves = co_fy_records["leaves"]
        co_scoped_tours = co_fy_records["tours"]

        if "CoAdminAcknowledged" not in df_requests.columns:
            df_requests["CoAdminAcknowledged"] = ""
        if "SelectedCoAdmin" not in df_requests.columns:
            df_requests["SelectedCoAdmin"] = ""
        if co_scoped_leaves is not None and not co_scoped_leaves.empty:
            if "CoAdminAcknowledged" not in co_scoped_leaves.columns:
                co_scoped_leaves["CoAdminAcknowledged"] = ""
            if "SelectedCoAdmin" not in co_scoped_leaves.columns:
                co_scoped_leaves["SelectedCoAdmin"] = ""
            to_review = co_scoped_leaves[
                (co_scoped_leaves["SelectedCoAdmin"].astype(str).str.strip() == str(st.session_state.user_name).strip()) &
                (co_scoped_leaves["CoAdminAcknowledged"].apply(is_unacknowledged)) &
                (co_scoped_leaves["Status"].astype(str).str.strip().isin(["Pending", "Approved"]))
            ]

            if to_review.empty:
                st.info("No requests currently assigned to you for support.")
            else:
                for idx, row in to_review.iterrows():
                    is_co_lv_act = _is_active_sheet_row(row)
                    with st.expander(f"{row['Name']} requested {row['TotalDays']} days of {row['LeaveType']}"):
                        st.write(
                            f"**Dates:** {row['StartDate']} to {row['EndDate']}")
                        st.write(f"**Submitted Date & Time:** `{row.get('ApplicationDate', 'N/A')}`")
                        st.write(f"**Department:** {row.get('Department', '')}")
                        if "Other" in str(row['LeaveType']) or row.get("EffectsServiceYear") == "Yes":
                            st.warning(
                                f"🚨 **ATTENTION:** This is an '{row['LeaveType']}' leave. The employee has checked 'EffectsServiceYear = {row.get('EffectsServiceYear')}'. Please double-check if this type of leave (e.g. Education Leave) should delay their service year before approving!")
                        if st.button("Support Request", type="primary", key=f"ack_{row['ID']}", disabled=not is_co_lv_act):
                            with st.spinner('Processing... Please wait and do not click again.'):
                                act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                                if not act_m.empty:
                                    df_requests.loc[act_m, "CoAdminAcknowledged"] = "Supported"
                                    df_requests.loc[act_m, "SupportedBy"] = st.session_state.user_name
                                    _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)
                                    _invalidate_sheet_cache("LeaveRequests")
                                # 1. Notify Admins
                                send_ntfy_notification(
                                    get_role_ntfy_topics("Admin"),
                                    "Leave Supported 🤝",
                                    f"Co-Admin {st.session_state.user_name} supported {row['TotalDays']} day(s) of leave for {row['Name']}. Awaiting final approval."
                                )
                                # 2. Notify Employee (Applicant)
                                emp_top = get_user_ntfy_topic(row['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Leave Supported 🤝",
                                        f"Co-Admin {st.session_state.user_name} endorsed your {row['TotalDays']} day(s) of {row['LeaveType']}. Forwarded to Admin for final approval."
                                    )
                                st.rerun()
        else:
            st.info("No leave requests found.")

    with tab_tr_sup:
        st.subheader("Official Tours to Support", anchor="tours-to-support")
        tours_df = tours_df_co_raw
        if co_scoped_tours is not None and not co_scoped_tours.empty:
            def _co_can_see_tour(row):
                if str(row.get("SelectedCoAdmin", "")).strip() != str(st.session_state.user_name).strip():
                    return False
                if str(row.get("SupportedBy", "")).strip() not in ["", "nan", "None"]:
                    return False
                status_clean = str(row.get("Status", "")).strip()
                if status_clean in ["Sanctioned", "Rejected", "Settled", "Cancelled", "Pending Branch Manager Support"]:
                    return False
                br = normalize_branch_code(str(row.get("Branch", "")))
                bm_assigned = str(row.get("BranchManager", "")).strip()
                bm_supported = str(row.get("BMSupportedBy", "")).strip()
                # Branch staff tour cannot be seen without Branch Manager approval
                if (br in ["SB", "NB", "DB"] or bm_assigned) and not bm_supported:
                    return False
                return True

            to_support_tours = co_scoped_tours[co_scoped_tours.apply(_co_can_see_tour, axis=1)]

            if to_support_tours.empty:
                st.info("No tour applications awaiting your endorsement.")
            else:
                for idx, tr in to_support_tours.iterrows():
                    is_co_tr_act = _is_active_sheet_row(tr)
                    with st.expander(f"✈️ {tr['Name']} — Tour to {tr['Destination']} ({tr['TotalDays']} Day(s))", expanded=True):
                        st.write(f"**Travel Dates:** {tr['DepartureDate']} to {tr['ArrivalDate']}")
                        st.write(f"**Conveyance:** {tr.get('ConveyanceMode', 'Road')}")
                        st.write(f"**Assigned Routing:** Co-Admin Support: **{tr.get('SelectedCoAdmin', 'N/A')}** ➔ Sanctioning Admin: **{tr.get('SelectedAdmin', 'N/A')}**")
                        st.write(f"**Current Status:** `{tr.get('Status', 'Pending')}`")
                        
                        raw_legs = tr.get('TravelLegs', '')
                        travel_legs = []
                        if isinstance(raw_legs, list):
                            travel_legs = raw_legs
                        elif isinstance(raw_legs, str) and raw_legs.strip().startswith('['):
                            try:
                                travel_legs = json.loads(raw_legs)
                            except Exception:
                                travel_legs = []

                        if travel_legs and len(travel_legs) > 1:
                            st.markdown("**📋 Destinations & Work Description (ས་གནས་ཁག་དང་ལས་དོན་འགྲེལ་བརྗོད།):**")
                            for leg in travel_legs:
                                l_num = leg.get('leg_num', '')
                                l_purp = str(leg.get('purpose', '')).strip()
                                if l_purp.endswith('.0') and l_purp[:-2].isdigit():
                                    l_purp = l_purp[:-2]
                                l_dest = leg.get('destination', '')
                                l_dep = leg.get('departure_date', '')
                                l_arr = leg.get('arrival_date', '')
                                st.markdown(f"- **{l_num}. ({l_dest}):** `{l_dep}` to `{l_arr}` — **Work Description:** {l_purp}")
                        else:
                            purp_display = str(tr.get('Purpose', '')).strip()
                            if purp_display.endswith('.0') and purp_display[:-2].isdigit():
                                purp_display = purp_display[:-2]
                            st.write(f"**Work Description:** {purp_display}")
                        
                        if st.button("📄 Preview Tour PDF", key=f"prev_tr_co_{tr['ID']}"):
                            pdf_data = generate_tour_sanction_pdf(tr)
                            if pdf_data:
                                open_pdf_dialog(pdf_data, f"{tr['Name']}_Tour_Preview.pdf")
                                
                        c_sup, c_rej = st.columns(2)
                        if c_sup.button("🤝 Support", type="primary", key=f"sup_tr_btn_{tr['ID']}", use_container_width=True, disabled=not is_co_tr_act):
                            with st.spinner("Endorsing Tour Request..."):
                                act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(tr["ID"])].index
                                if not act_tm.empty:
                                    tours_df.loc[act_tm, "SupportedBy"] = st.session_state.user_name
                                    tours_df.loc[act_tm, "Status"] = "Pending Admin Sanction"
                                    _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                    _invalidate_sheet_cache("TourSanctions")
                                target_admin = tr.get("SelectedAdmin", "Admin")
                                adm_top = get_user_ntfy_topic(target_admin) or get_role_ntfy_topics("Admin")
                                send_ntfy_notification(
                                    adm_top,
                                    "Tour Request Endorsed 🤝",
                                    f"Co-Admin {st.session_state.user_name} endorsed Tour to {tr['Destination']} for {tr['Name']}. Forwarded for final sanction."
                                )
                                emp_top = get_user_ntfy_topic(tr['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Tour Endorsed by Co-Admin 🤝",
                                        f"Co-Admin {st.session_state.user_name} endorsed your Tour to {tr['Destination']}. Forwarded to Admin ({target_admin}) for final sanction."
                                    )
                                st.success(f"Supported Tour request for {tr['Name']}! Forwarded to Admin ({target_admin}) for final sanction.")
                                st.rerun()
                                
                        if c_rej.button("❌ Reject Tour", key=f"rej_tr_co_{tr['ID']}", use_container_width=True, disabled=not is_co_tr_act):
                            with st.spinner("Rejecting Tour Request..."):
                                act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(tr["ID"])].index
                                if not act_tm.empty:
                                    tours_df.loc[act_tm, "Status"] = "Rejected"
                                    tours_df.loc[act_tm, "SupportedBy"] = f"Rejected by {st.session_state.user_name}"
                                    _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                    _invalidate_sheet_cache("TourSanctions")
                                emp_top = get_user_ntfy_topic(tr['Name'])
                                if emp_top:
                                    send_ntfy_notification(
                                        emp_top,
                                        "Tour Rejected by Co-Admin ❌",
                                        f"Co-Admin {st.session_state.user_name} rejected your Tour application to {tr['Destination']}."
                                    )
                                st.warning(f"Rejected Tour request for {tr['Name']}.")
                                st.rerun()
        else:
            st.info("No tour applications found.")

    with tab_cal:
        with st.expander("📅 Organizational Calendar", expanded=False):
            render_organization_calendar(
                fy_year_override=co_fy_yr,
                leaves_override=co_scoped_leaves,
                tours_override=co_scoped_tours,
            )

        allowed_branches_co = get_active_visible_branches()
        branch_map_co = get_user_branch_map()
        
        st.subheader("Office Availability Overview")
        if allowed_branches_co:
            st.caption(f"🔎 Showing branches: **{format_branch_scope(allowed_branches_co)}**")
        df_requests_co = co_scoped_leaves
        if df_requests_co is not None and not df_requests_co.empty:
            overview = filter_df_by_branches(
                df_requests_co[df_requests_co["Status"].isin(["Pending", "Approved"])],
                allowed_branches_co,
                branch_map_co,
                name_col="Name"
            )
            if not overview.empty:
                if "page_coadmin" not in st.session_state:
                    st.session_state.page_coadmin = 0
                    
                items_per_page = 10
                total_items = len(overview)
                total_pages = max(1, (total_items - 1) // items_per_page + 1)
                
                if st.session_state.page_coadmin >= total_pages:
                    st.session_state.page_coadmin = total_pages - 1
                    
                start_idx = st.session_state.page_coadmin * items_per_page
                end_idx = start_idx + items_per_page
                current_page_df = overview.iloc[start_idx:end_idx]
                
                for idx, row in current_page_df.iterrows():
                    col1, col2 = st.columns([5, 1])
                    col1.write(f"**{row['Name']}** - {row['LeaveType']} ({row['StartDate']} to {row['EndDate']}) - {row['Status']}")
                    if row['Status'] == "Approved":
                        if col2.button("📄 Preview PDF", key=f"prev_co_{row['ID']}"):
                            pdf_bytes = generate_leave_pdf(row)
                            open_pdf_dialog(pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave.pdf")
                
                st.divider()
                p_col1, p_col2, p_col3 = st.columns([1, 2, 1])
                with p_col1:
                    if st.session_state.page_coadmin > 0:
                        if st.button("⬅️ Previous", key="prev_coadmin_btn"):
                            st.session_state.page_coadmin -= 1
                            st.rerun()
                with p_col2:
                    st.markdown(f"<div style='text-align: center'>Page {st.session_state.page_coadmin + 1} of {total_pages}</div>", unsafe_allow_html=True)
                with p_col3:
                    if st.session_state.page_coadmin < total_pages - 1:
                        if st.button("Next ➡️", key="next_coadmin_btn"):
                            st.session_state.page_coadmin += 1
                            st.rerun()
            else:
                st.info("No data available.")
        else:
            st.info("No data available.")

        st.divider()
        with st.expander("📊 Master Balance Table", expanded=False):
            render_master_balance_table()

    with tab_profile:
        st.subheader("My Leave Profile")
        employee_dashboard(hide_title=True)


def accounts_dashboard():
    render_active_leaves_banner()
    acct_name = st.session_state.user_name
    acct_branch = get_user_branch(acct_name) if acct_name else "HQ"
    branch_full_name = BRANCH_NAMES.get(acct_branch, acct_branch)
    st.title("Accounts Dashboard")
    if is_central_branch_filter_role("Accounts", acct_branch):
        st.caption(f"Logged in as: **{acct_name}** | Central Accounts (དབུས། / HQ)")
    else:
        st.caption(f"Logged in as: **{acct_name}** | Branch Scope: **{branch_full_name}** (`{acct_branch}`)")
    
    accounts_tab_options = ["Accounts Tasks (Leaves)", "✈️ Tour Register & Claims", "My Leave Profile", "⚙️ System Settings"]
    if "accounts_tabs" not in st.session_state or st.session_state.accounts_tabs not in accounts_tab_options:
        st.session_state.accounts_tabs = "Accounts Tasks (Leaves)"

    tab1, tab_trs, tab2, tab3 = st.tabs(
        accounts_tab_options,
        key="accounts_tabs",
        on_change="rerun"
    )
    
    with tab2:
        employee_dashboard(hide_title=True)
        
    with tab3:
        render_system_settings(is_admin=False)
        
    with tab1:
        df_requests = load_leave_requests()
        tours_df_acct_raw = load_tour_sanctions()
        acct_fy_sel = render_fy_scope_selector(
            key="accounts_fy_scope",
            label="📅 Select Financial Year (FY) Scope for Accounts Registers",
            include_all=True,
            leave_df=df_requests,
            tour_df=tours_df_acct_raw,
        )
        acct_fy_yr = acct_fy_sel["fy_year"] if isinstance(acct_fy_sel, dict) else None
        acct_fy_records = load_fy_records(acct_fy_yr, leave_df_active=df_requests, tour_df_active=tours_df_acct_raw)
        acct_fy_leaves = acct_fy_records["leaves"]
        acct_fy_tours = acct_fy_records["tours"]

        with st.expander("📅 View Organization Calendar", expanded=False):
            render_organization_calendar(
                fy_year_override=acct_fy_yr,
                leaves_override=acct_fy_leaves,
                tours_override=acct_fy_tours,
            )
        with st.expander("📊 Master Balance Table", expanded=False):
            render_master_balance_table()
        st.divider()
        
        allowed_branches_acct = get_active_visible_branches()
        branch_map_acct = get_user_branch_map()
        scoped_df_requests = filter_df_by_branches(acct_fy_leaves, allowed_branches_acct, branch_map_acct, name_col="Name")
        
        st.subheader("All Leaves Overview")
        if allowed_branches_acct:
            st.caption(f"🔎 Showing branches: **{format_branch_scope(allowed_branches_acct)}**")
        if scoped_df_requests is not None and not scoped_df_requests.empty:
            all_leaves = scoped_df_requests
            
            if "page_accounts" not in st.session_state:
                st.session_state.page_accounts = 0
                
            items_per_page = 10
            total_items = len(all_leaves)
            total_pages = max(1, (total_items - 1) // items_per_page + 1)
            
            if st.session_state.page_accounts >= total_pages:
                st.session_state.page_accounts = total_pages - 1
                
            start_idx = st.session_state.page_accounts * items_per_page
            end_idx = start_idx + items_per_page
            current_page_df = all_leaves.iloc[start_idx:end_idx]
            
            for idx, row in current_page_df.iterrows():
                    is_acct_row_act = _is_active_sheet_row(row)
                    col1, col2, col3, col4 = st.columns([4.5, 1, 1, 2])

                    raw_status = str(row.get('Status', '')).strip()
                    co_admin_name = str(row.get('SelectedCoAdmin', '')).strip()
                    co_status_raw = str(row.get('CoAdminAcknowledged', '')).strip().lower()

                    if raw_status == "Pending":
                        if co_admin_name and co_admin_name.lower() not in ['none', 'nan', '']:
                            if co_status_raw == "supported":
                                display_status = f"Supported by Co-Admin ({co_admin_name}) and Pending from Admin"
                            else:
                                display_status = f"Pending from Co-Admin ({co_admin_name}) and Admin"
                        else:
                            display_status = "Pending from Admin"
                    else:
                        display_status = raw_status

                    col1.write(f"**{row['Name']}** - {row['LeaveType']} ({row['StartDate']} to {row['EndDate']}) - Status: {display_status}")

                    if col2.button(" Preview", key=f"prev_acc_all_{row['ID']}"):
                        pdf_bytes = generate_leave_pdf(row)
                        open_pdf_dialog(pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave.pdf")
                    if col3.button(" PDF", key=f"prep_dl_acc_{row['ID']}"):
                        pdf_bytes = generate_leave_pdf(row)
                        col3.download_button(" ⬇️ Download", data=pdf_bytes, file_name=f"{row['Name']}_{row['StartDate']}_Leave.pdf", mime="application/pdf", key=f"dl_acc_{row['ID']}")
                    
                    is_punched = str(row.get('AccountsPunched', 'No')).strip().lower() in ['yes', 'punched']
                    if is_punched:
                        col4.success("✅ Punched")
                    elif raw_status == "Approved":
                        if col4.button("Punch in Register", key=f"punch_acc_all_{row['ID']}", type="primary", disabled=not is_acct_row_act):
                            act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                            if not act_m.empty:
                                df_requests.loc[act_m, "AccountsPunched"] = "Punched"
                                df_requests.loc[act_m, "PunchedBy"] = st.session_state.user_name
                                _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)
                                _invalidate_sheet_cache("LeaveRequests")
                            send_ntfy_notification(
                                get_role_ntfy_topics("Admin"),
                                "Leave Punched in Register 📇",
                                f"Accountant {st.session_state.user_name} punched in {row['TotalDays']} day(s) of leave for {row['Name']}."
                            )
                            emp_top = get_user_ntfy_topic(row['Name'])
                            if emp_top:
                                send_ntfy_notification(
                                    emp_top,
                                    "Leave Recorded in Official Register 📇",
                                    f"Accountant {st.session_state.user_name} has punched your {row['TotalDays']} day(s) of {row['LeaveType']} into the register."
                                )
                            st.rerun()
                    elif raw_status not in ["Cancelled", "Rejected"]:
                        col4.button("Punch Pending", key=f"punch_pend_all_{row['ID']}", disabled=True, help="Punch in register will be available after Admin approves the leave.")
            st.divider()
            p_col1, p_col2, p_col3 = st.columns([1, 2, 1])
            with p_col1:
                if st.session_state.page_accounts > 0:
                    if st.button("⬅️ Previous", key="prev_accounts_btn"):
                        st.session_state.page_accounts -= 1
                        st.rerun()
            with p_col2:
                st.markdown(f"<div style='text-align: center'>Page {st.session_state.page_accounts + 1} of {total_pages}</div>", unsafe_allow_html=True)
            with p_col3:
                if st.session_state.page_accounts < total_pages - 1:
                    if st.button("Next ➡️", key="next_accounts_btn"):
                        st.session_state.page_accounts += 1
                        st.rerun()
        else:
            st.info("No data available.")
            
        st.divider()
        
        st.subheader("Action Required: Manual Attendance Register")
        if scoped_df_requests is not None and not scoped_df_requests.empty:
            action_required = scoped_df_requests[
                (scoped_df_requests["Status"] == "Approved") & 
                (scoped_df_requests["AccountsPunched"].apply(is_unacknowledged))
            ]
            
            if action_required.empty:
                st.info("No approved requests require manual punching right now.")
            else:
                st.error(f"🔔 You have {len(action_required)} approved leave request(s) waiting to be punched in the register!")
                for idx, row in action_required.iterrows():
                    is_act_req_row = _is_active_sheet_row(row)
                    with st.expander(f"{row['Name']} took {row['TotalDays']} days of {row['LeaveType']}"):
                        st.write(f"**Dates:** {row['StartDate']} to {row['EndDate']}")
                        st.write(f"**Department:** {row.get('Department', '')}")
                        if "Other" in str(row['LeaveType']) or row.get("EffectsServiceYear") == "Yes":
                            st.warning(f"🚨 **ATTENTION:** This is an '{row['LeaveType']}' leave. The employee has checked 'EffectsServiceYear = {row.get('EffectsServiceYear')}'. This will permanently delay their Service Year!")
                        
                        co_status = "✅ Supported" if str(row.get('CoAdminAcknowledged', '')).strip().lower() == "supported" else "⏳ Pending"
                        admin_status = "✅ Approved" if str(row.get('Status', '')).strip().lower() == "approved" else f"ℹ️ {row.get('Status', 'Unknown')}"
                        
                        co_admin_name = str(row.get('SelectedCoAdmin', '')).strip()
                        if co_admin_name and co_admin_name.lower() not in ['none', 'nan', '']:
                            st.caption(f"**Admin:** {admin_status} &nbsp;|&nbsp; **Co-Admin ({co_admin_name}):** {co_status}")
                        else:
                            st.caption(f"**Admin:** {admin_status} &nbsp;|&nbsp; **Co-Admin:** {co_status}")
                        
                        col_btn1, col_btn2 = st.columns(2)
                        
                        if col_btn1.button("📄 Preview PDF", key=f"prev_act_{row['ID']}"):
                            pdf_bytes = generate_leave_pdf(row)
                            open_pdf_dialog(pdf_bytes, f"{row['Name']}_{row['StartDate']}_Leave.pdf")
                            
                        if col_btn2.button("Mark Punched in Register", type="primary", key=f"punch_{row['ID']}", disabled=not is_act_req_row):
                            act_m = df_requests[df_requests["ID"].map(_normalize_record_id) == _normalize_record_id(row["ID"])].index
                            if not act_m.empty:
                                df_requests.loc[act_m, "AccountsPunched"] = "Punched"
                                df_requests.loc[act_m, "PunchedBy"] = st.session_state.user_name
                                _gsheets_retry(conn.update, worksheet="LeaveRequests", data=df_requests)
                                _invalidate_sheet_cache("LeaveRequests")
                            send_ntfy_notification(
                                get_role_ntfy_topics("Admin"),
                                "Leave Punched in Register 📚",
                                f"Accountant {st.session_state.user_name} punched in {row['TotalDays']} day(s) of leave for {row['Name']}."
                            )
                            emp_top = get_user_ntfy_topic(row['Name'])
                            if emp_top:
                                send_ntfy_notification(
                                    emp_top,
                                    "Leave Recorded in Official Register 📚",
                                    f"Accountant {st.session_state.user_name} has punched your {row['TotalDays']} day(s) of {row['LeaveType']} into the register."
                                )
                            st.rerun()

    with tab_trs:
        st.subheader("Sanctioned Tours — Tour Register Entry", anchor="tour-register-accounts")
        if allowed_branches_acct:
            st.caption(f"🔎 Showing branches: **{format_branch_scope(allowed_branches_acct)}**")
        tours_df = tours_df_acct_raw
        scoped_tours_df = filter_df_by_branches(acct_fy_tours, allowed_branches_acct, branch_map_acct, name_col="Name")
        if scoped_tours_df is not None and not scoped_tours_df.empty:
            to_punch = scoped_tours_df[
                (scoped_tours_df["Status"].isin(["Sanctioned", "Completed", "Settled"])) &
                (~scoped_tours_df["AccountsPunched"].astype(str).str.strip().str.lower().isin(["yes", "punched"]))
            ]
            if to_punch.empty:
                st.success("✅ All sanctioned tours have been punched into the Official Tour Register!")
            else:
                st.warning(f"⚠️ {len(to_punch)} tour(s) awaiting Tour Register entry:")
                for idx, r in to_punch.iterrows():
                    is_acct_tr_act = _is_active_sheet_row(r)
                    c1, c2, c3 = st.columns([4, 1.5, 1.5])
                    c1.write(f"**{r['Name']}** — Tour to **{r['Destination']}** ({r['DepartureDate']} to {r['ArrivalDate']})")
                    if c2.button("📄 Preview", key=f"prev_tr_acc_{r['ID']}"):
                        pdf_bytes = generate_tour_sanction_pdf(r)
                        if pdf_bytes:
                            open_pdf_dialog(pdf_bytes, f"{r['Name']}_Tour_{r['ID']}.pdf")
                    if c3.button("Punch in Register", type="primary", key=f"punch_tr_acc_{r['ID']}", disabled=not is_acct_tr_act):
                        with st.spinner("Punching in register..."):
                            act_tm = tours_df[tours_df["ID"].map(_normalize_record_id) == _normalize_record_id(r["ID"])].index
                            if not act_tm.empty:
                                tours_df.loc[act_tm, "AccountsPunched"] = "Yes"
                                tours_df.loc[act_tm, "PunchedBy"] = st.session_state.user_name
                                _gsheets_retry(conn.update, worksheet="TourSanctions", data=tours_df)
                                _invalidate_sheet_cache("TourSanctions")
                            send_ntfy_notification(
                                get_role_ntfy_topics("Admin"),
                                "Tour Punched in Register 📇",
                                f"Accountant {st.session_state.user_name} punched Tour to {r['Destination']} for {r['Name']} in the register."
                            )
                            emp_top = get_user_ntfy_topic(r['Name'])
                            if emp_top:
                                send_ntfy_notification(
                                    emp_top,
                                    "Tour Punched in Register 📇",
                                    f"Accountant {st.session_state.user_name} punched your Tour to {r['Destination']} in the official Tour Register."
                                )
                            st.success(f"Punched Tour #{r['ID']} in Tour Register!")
                            st.rerun()

            st.divider()
            st.subheader("All Tour Records")
            all_trs = scoped_tours_df
            if "page_accounts_tours" not in st.session_state:
                st.session_state.page_accounts_tours = 0
            acc_tr_per_page = 10
            tot_acc_tr = len(all_trs)
            tot_acc_tr_pages = max(1, (tot_acc_tr - 1) // acc_tr_per_page + 1)
            if st.session_state.page_accounts_tours >= tot_acc_tr_pages:
                st.session_state.page_accounts_tours = tot_acc_tr_pages - 1
            at_s = st.session_state.page_accounts_tours * acc_tr_per_page
            at_e = at_s + acc_tr_per_page
            for _, r in all_trs.iloc[at_s:at_e].iterrows():
                c1, c2 = st.columns([5, 1])
                c1.write(f"**{r['Name']}** — Tour to **{r['Destination']}** ({r['DepartureDate']} to {r['ArrivalDate']}) — Status: {r['Status']} — Punched: {r.get('AccountsPunched', 'No')}")
                if c2.button("📄 PDF", key=f"acc_all_tr_pdf_{r['ID']}"):
                    pdf_bytes = generate_tour_sanction_pdf(r)
                    if pdf_bytes:
                        open_pdf_dialog(pdf_bytes, f"{r['Name']}_Tour_{r['ID']}.pdf")
            if tot_acc_tr_pages > 1:
                apt1, apt2, apt3 = st.columns([1, 2, 1])
                with apt1:
                    if st.session_state.page_accounts_tours > 0:
                        if st.button("⬅️ Previous Tours", key="prev_acc_tr_btn"):
                            st.session_state.page_accounts_tours -= 1
                            st.rerun()
                with apt2:
                    st.markdown(f"<div style='text-align: center'>Tour Page {st.session_state.page_accounts_tours + 1} of {tot_acc_tr_pages}</div>", unsafe_allow_html=True)
                with apt3:
                    if st.session_state.page_accounts_tours < tot_acc_tr_pages - 1:
                        if st.button("Next Tours ➡️", key="next_acc_tr_btn"):
                            st.session_state.page_accounts_tours += 1
                            st.rerun()
        else:
            st.info("No tour data available.")




def render_smart_notifications(role, user_name):
    try:
        df_requests = load_leave_requests()
    except Exception:
        df_requests = None

    try:
        tours_df = load_tour_sanctions()
    except Exception:
        tours_df = None

    def is_coadmin_pending(x):
        if pd.isna(x):
            return True
        v = str(x).strip().lower()
        return v in ["", "nan", "none", "no", "pending", "false", "0"]

    if role == "Branch Manager":
        bm_branch = get_user_branch(user_name)
        users_df = load_users_data()
        b_staff = []
        if users_df is not None and not users_df.empty:
            b_staff = users_df[
                (users_df["Branch"].apply(normalize_branch_code) == normalize_branch_code(bm_branch)) &
                (users_df["Name"] != user_name)
            ]["Name"].tolist()
        
        if df_requests is not None and not df_requests.empty and "Status" in df_requests.columns:
            b_pending = df_requests[(df_requests["Name"].isin(b_staff)) & (df_requests["Status"].astype(str).str.strip() == "Pending")]
            if not b_pending.empty:
                cnt = len(b_pending)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.warning(f"🏢 **{bm_branch} Leave Approvals:** You have **{cnt}** pending leave request(s) from your branch staff waiting for your approval!")
                    with c_act:
                        if st.button("Review Leaves 📋", key="top_notif_bm_leaves", use_container_width=True):
                            st.session_state.bm_tabs = f"📋 {bm_branch} Leave Approvals"
                            st.rerun()

        if tours_df is not None and not tours_df.empty and "Status" in tours_df.columns:
            bm_sup_col = tours_df["BMSupportedBy"].fillna("").astype(str).str.strip() if "BMSupportedBy" in tours_df.columns else pd.Series([""] * len(tours_df), index=tours_df.index)
            bm_pending_tours = tours_df[
                (tours_df["Name"].isin(b_staff)) &
                (bm_sup_col.isin(["", "nan", "None"])) &
                (~tours_df["Status"].astype(str).str.strip().isin([
                    "Sanctioned", "Completed", "Settled", "Rejected", "Cancelled",
                    "Pending Co-Admin Support", "Pending Admin Sanction",
                ]))
            ]
            if not bm_pending_tours.empty:
                cnt_bm_tr = len(bm_pending_tours)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.warning(f"✈️ **{bm_branch} Tour Sanctions:** You have **{cnt_bm_tr}** tour sanction request(s) from your branch staff waiting for your review & support!")
                    with c_act:
                        if st.button("Review Tours ✈️", key="top_notif_bm_tours", type="primary", use_container_width=True):
                            st.session_state.bm_tabs = f"✈️ {bm_branch} Tour Sanctions"
                            st.rerun()

    elif role == "Admin":
        # 1. Official Tour Sanctions pending approval (after passing Branch Manager support if branch staff)
        if tours_df is not None and not tours_df.empty and "Status" in tours_df.columns:
            def _adm_notif_tour(row_t):
                st_clean = str(row_t.get("Status", "")).strip()
                if st_clean not in (
                    "Pending Admin Sanction", "Pending Sanction", "Pending",
                    "Pending Recommendation", "Pending Co-Admin Support",
                ):
                    return False
                bm_assigned = "" if pd.isna(row_t.get("BranchManager")) else str(row_t.get("BranchManager")).strip()
                bm_supported = "" if pd.isna(row_t.get("BMSupportedBy")) else str(row_t.get("BMSupportedBy")).strip()
                if bm_assigned not in ("", "nan", "None") and bm_supported in ("", "nan", "None"):
                    return False
                return True

            pending_tours = tours_df[tours_df.apply(_adm_notif_tour, axis=1)]
            if not pending_tours.empty:
                cnt_tr = len(pending_tours)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.error(f"✈️ **Official Tour Sanctions:** You have **{cnt_tr}** tour sanction request(s) waiting for your approval!")
                    with c_act:
                        if st.button("Review Tours ✈️", key="top_notif_admin_tours", type="primary", use_container_width=True):
                            st.session_state.admin_tabs = "✈️ Tour Sanctions"
                            st.rerun()

        # 2. Leave Requests pending approval
        if df_requests is not None and not df_requests.empty and "Status" in df_requests.columns:
            users_df_notif = load_users_data()
            branch_subordinates_notif = []
            if users_df_notif is not None and not users_df_notif.empty:
                branch_subordinates_notif = users_df_notif[
                    (users_df_notif["Branch"].isin(["SB", "NB", "DB"])) & 
                    (users_df_notif["Role"].isin(["Employee", "Accounts"]))
                ]["Name"].tolist()
            pending_leaves = df_requests[
                (df_requests["Status"].astype(str).str.strip() == "Pending") &
                (~df_requests["Name"].isin(branch_subordinates_notif))
            ]
            if not pending_leaves.empty:
                cnt_lv = len(pending_leaves)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.warning(f"📝 **Leave Approvals:** You have **{cnt_lv}** pending leave request(s) waiting for your approval!")
                    with c_act:
                        if st.button("Review Leaves 📝", key="top_notif_admin_leaves", use_container_width=True):
                            st.session_state.admin_tabs = "Admin Tasks (Leaves)"
                            st.rerun()

    elif role == "Co-Admin":
        # 1. Tour Sanctions pending Co-Admin endorsement
        if tours_df is not None and not tours_df.empty:
            co_tours = tours_df[
                (tours_df["SelectedCoAdmin"].astype(str).str.strip() == str(user_name).strip()) &
                (tours_df["SupportedBy"].astype(str).str.strip().isin(["", "nan", "None"])) &
                (~tours_df["Status"].astype(str).str.strip().isin(["Sanctioned", "Rejected", "Settled", "Cancelled", "Pending Branch Manager Support"]))
            ]
            if not co_tours.empty:
                cnt_tr = len(co_tours)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.error(f"✈️ **Tour Support:** You have **{cnt_tr}** tour request(s) waiting for your endorsement!")
                    with c_act:
                        if st.button("Endorse Tours ✈️", key="top_notif_co_tours", type="primary", use_container_width=True):
                            st.session_state.coadmin_tabs = "✈️ Tours to Support"
                            st.rerun()

        # 2. Leave Requests pending Co-Admin support
        if df_requests is not None and not df_requests.empty:
            req_copy = df_requests.copy()
            if "CoAdminAcknowledged" not in req_copy.columns:
                req_copy["CoAdminAcknowledged"] = ""
            if "SelectedCoAdmin" not in req_copy.columns:
                req_copy["SelectedCoAdmin"] = ""
            co_leaves = req_copy[
                (req_copy["SelectedCoAdmin"].astype(str).str.strip() == str(user_name).strip()) &
                (req_copy["Status"].astype(str).str.strip().isin(["Pending", "Approved"])) &
                (req_copy["CoAdminAcknowledged"].apply(is_coadmin_pending))
            ]
            if not co_leaves.empty:
                cnt_lv = len(co_leaves)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.warning(f"📝 **Leave Support:** You have **{cnt_lv}** leave request(s) waiting for your support!")
                    with c_act:
                        if st.button("Endorse Leaves 📝", key="top_notif_co_leaves", use_container_width=True):
                            st.session_state.coadmin_tabs = "🍃 Leaves to Support"
                            st.rerun()

    elif role in ["Accounts", "Accountant"]:
        acct_allowed_branches = get_active_visible_branches()
        acct_branch_map = get_user_branch_map()
        acct_branch_label = format_branch_scope(acct_allowed_branches)
        scoped_acct_tours = filter_df_by_branches(tours_df, acct_allowed_branches, acct_branch_map, name_col="Name")
        scoped_acct_leaves = filter_df_by_branches(df_requests, acct_allowed_branches, acct_branch_map, name_col="Name")

        # 1. Tours pending Tour Register entry
        if scoped_acct_tours is not None and not scoped_acct_tours.empty:
            unpunched_tours = scoped_acct_tours[
                (scoped_acct_tours["Status"].isin(["Sanctioned", "Completed", "Settled"])) &
                (~scoped_acct_tours["AccountsPunched"].astype(str).str.strip().str.lower().isin(["yes", "punched"]))
            ]
            if not unpunched_tours.empty:
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.error(f"✈️ **Official Tours ({acct_branch_label}):** You have **{len(unpunched_tours)}** tour(s) to punch in the Tour Register!")
                    with c_act:
                        if st.button("Tour Register ✈️", key="top_notif_acct_tours", type="primary", use_container_width=True):
                            st.session_state.accounts_tabs = "✈️ Tour Register & Claims"
                            st.rerun()

        # 2. Leaves pending register punching
        if scoped_acct_leaves is not None and not scoped_acct_leaves.empty and "Status" in scoped_acct_leaves.columns and "AccountsPunched" in scoped_acct_leaves.columns:
            acct_leaves = scoped_acct_leaves[
                (scoped_acct_leaves["Status"].astype(str).str.strip() == "Approved") &
                (scoped_acct_leaves["AccountsPunched"].apply(is_unacknowledged))
            ]
            if not acct_leaves.empty:
                cnt_acct = len(acct_leaves)
                with st.container():
                    c_msg, c_act = st.columns([4, 1.2])
                    with c_msg:
                        st.warning(f"📝 **Leave Register ({acct_branch_label}):** You have **{cnt_acct}** approved leave(s) waiting to be punched in the register!")
                    with c_act:
                        if st.button("Punch Leaves 📝", key="top_notif_acct_leaves", use_container_width=True):
                            st.session_state.accounts_tabs = "Accounts Tasks (Leaves)"
                            st.rerun()


# ==============================================================================
# AUDITOR OVERSIGHT HELPERS, DEMO SANDBOX & DASHBOARD (100% READ-ONLY)
# ==============================================================================

_ARCHIVE_TITLE_RE = re.compile(
    r"^(LeaveRequests|TourSanctions)_Archive_FY(\d{4})[-_/](\d{2,4})$",
    re.IGNORECASE,
)

_CANONICAL_LEAVE_COLS = [
    "ID", "Name", "LeaveType", "Reason", "EffectsServiceYear", "ApplicationDate",
    "StartDate", "StartHalf", "EndDate", "EndHalf", "TotalDays", "Department",
    "SelectedCoAdmin", "SelectedAdmin", "Status", "CoAdminAcknowledged",
    "SupportedBy", "AccountsPunched", "PunchedBy", "ApprovedBy",
    "CLBalance", "ALBalance", "SLBalance", "ULBalance",
]

_CANONICAL_TOUR_COLS = [
    "ID", "Name", "Branch", "Role", "Post", "Purpose", "Destination",
    "DepartureDate", "ArrivalDate", "TotalDays", "ConveyanceMode", "Status",
    "SelectedCoAdmin", "SelectedAdmin", "BranchManager", "BMSupportedBy",
    "SupportedBy", "ApprovedBy", "SanctionedBy", "AccountsPunched", "PunchedBy",
    "ApplicationDate", "TravelLegs",
]


def _extract_fy_year_from_archive_title(title):
    """Returns (kind, fy_start_year) for archive worksheet names, else (None, None)."""
    m = _ARCHIVE_TITLE_RE.match(str(title or "").strip())
    if not m:
        return None, None
    raw_kind = m.group(1).lower()
    kind = "LeaveRequests" if raw_kind.startswith("leave") else "TourSanctions"
    return kind, int(m.group(2))


def _normalize_record_id(val):
    """Normalizes record IDs so '101', '101.0', and 101 match identically while empty/null returns ''."""
    if val is None:
        return ""
    try:
        if pd.isnull(val):
            return ""
    except Exception:
        pass
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val.is_integer():
            return str(int(val))
        return str(val)
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null", "nat"):
        return ""
    if re.match(r"^-?\d+\.0+$", s):
        return s.split(".", 1)[0]
    return s


def _is_active_sheet_row(row):
    """Returns True when a record came from the live active worksheet (not a read-only archive)."""
    if row is None or not hasattr(row, "get"):
        return True
    return str(row.get("_source_sheet", "active")).strip().lower() != "archive"


@st.cache_data(ttl=120, show_spinner=False)
def _list_spreadsheet_sheet_titles():
    """Fetches existing worksheet titles from Google Sheets for archive FY discovery."""
    _retry_fn = globals().get("_gsheets_retry")
    try:
        spread = _retry_fn(conn.client._open_spreadsheet, spreadsheet=None, folder_id=None) if callable(_retry_fn) else conn.client._open_spreadsheet(spreadsheet=None, folder_id=None)
        return [str(ws.title).strip() for ws in spread.worksheets()]
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _read_cached_archive_sheet(sheet_name):
    """Cached reader for immutable historical FY archive worksheets."""
    _gs_read = globals().get("_gsheets_read")
    try:
        df = _gs_read(conn, sheet_name) if callable(_gs_read) else conn.read(worksheet=sheet_name)
        return df.copy() if df is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _resolve_archive_titles(conn_override=None, archive_titles=None):
    """Returns worksheet title list from explicit override, custom conn_override, or cached Sheets list."""
    if archive_titles is not None:
        return list(archive_titles)
    if conn_override is not None:
        _retry_fn = globals().get("_gsheets_retry")
        try:
            spread = _retry_fn(conn_override.client._open_spreadsheet, spreadsheet=None, folder_id=None) if callable(_retry_fn) else conn_override.client._open_spreadsheet(spreadsheet=None, folder_id=None)
            return [str(ws.title).strip() for ws in spread.worksheets()]
        except Exception:
            return []
    return _list_spreadsheet_sheet_titles()


def find_users_missing_from_staff_master(users_df, staff_df, active_fy=None):
    """Builds Staff_Master seed rows for active staff present in Users but missing in Staff_Master.
    Strictly excludes 'Auditor' role accounts so auditors never receive leave/tour balances or rollovers.
    """
    if users_df is None or staff_df is None:
        return []
    staff_names = (
        {str(x).strip() for x in staff_df["Name"].dropna().tolist()}
        if "Name" in staff_df.columns else set()
    )
    fy_val = active_fy or get_current_financial_year_dates()[2]
    missing_users = []
    for _, user_row in users_df.iterrows():
        role_val = str(user_row.get("Role", "Employee")).strip()
        if role_val == "Auditor":
            continue
        name = user_row.get("Name")
        if pd.notnull(name) and str(name).strip() != "" and str(name).strip() not in staff_names:
            missing_users.append({
                "Name": str(name).strip(),
                "Post": user_row.get("Post", ""),
                "JoiningDate": user_row.get("JoiningDate", ""),
                "Branch": normalize_branch_code(user_row.get("Branch", "HQ")),
                "Opening_CL": 0, "Addition_CL": 0, "Used_CL": 0, "Balance_CL": 0, "Penalty_CL(UL Effect)": 0,
                "Opening_SL": 0, "Addition_SL": 0, "Used_SL": 0, "Balance_SL": 0, "Penalty_SL(UL Effect)": 0,
                "Opening_AL": 0, "Addition_AL": 0, "Used_AL": 0, "Balance_AL": 0, "Penalty_AL(UL Effect)": 0, "Encashed_AL": 0,
                "Opening_UL": 0, "Addition_UL": 0, "Used_UL": 0, "Balance_UL": 0,
                "Cumulative_UL": 0, "Last_UL_Milestone": 0,
                "Current Year Leave affecting Service Year": 0,
                "Cummulative leave effecting Service year.(Education Leave & Etc).": 0,
                "Service Year Penalty": 0,
                "Active_FY": fy_val,
            })
    return missing_users


def _row_date_span_fy_years(df, start_col, end_col):
    """Collects financial start years covered by [start_col, end_col] spans in a DataFrame (IST-safe)."""
    years = set()
    if df is None or df.empty or start_col not in df.columns:
        return years
    s_vals = df[start_col].tolist()
    e_vals = df[end_col].tolist() if end_col in df.columns else s_vals
    for raw_s, raw_e in zip(s_vals, e_vals):
        sd = _parse_cta_date(raw_s)
        ed = _parse_cta_date(raw_e)
        if sd is None and ed is None:
            continue
        s_d = sd if sd is not None else ed
        e_d = ed if ed is not None else s_d
        if e_d < s_d:
            s_d, e_d = e_d, s_d
        sy = s_d.year if s_d.month >= 4 else s_d.year - 1
        ey = e_d.year if e_d.month >= 4 else e_d.year - 1
        for y in range(sy, min(ey, sy + 10) + 1):
            if 1990 <= y <= 2100:
                years.add(y)
    return years


def get_available_fys(
    conn_override=None,
    as_of_date=None,
    leave_df=None,
    tour_df=None,
    archive_titles=None,
    df_req_active=None,
    df_tour_active=None,
):
    """Discovers selectable Financial Years across Current FY, Archive worksheets, and active records."""
    cur_s, _, _ = get_current_financial_year_dates(as_of_date)
    cur_fy_yr = cur_s.year

    titles = _resolve_archive_titles(conn_override=conn_override, archive_titles=archive_titles)
    lr_archives = {}
    tr_archives = {}
    for t in (titles or []):
        kind, yr = _extract_fy_year_from_archive_title(t)
        if kind and yr is not None:
            if kind == "LeaveRequests":
                lr_archives[yr] = t
            elif kind == "TourSanctions":
                tr_archives[yr] = t

    eff_l = leave_df if leave_df is not None else df_req_active
    eff_t = tour_df if tour_df is not None else df_tour_active
    active_leaves = eff_l if eff_l is not None else load_leave_requests()
    active_tours = eff_t if eff_t is not None else load_tour_sanctions()

    discovered_yrs = {cur_fy_yr} | set(lr_archives.keys()) | set(tr_archives.keys())
    discovered_yrs |= {
        y for y in _row_date_span_fy_years(active_leaves, "StartDate", "EndDate")
        if 1990 <= y <= cur_fy_yr + 5
    }
    discovered_yrs |= {
        y for y in _row_date_span_fy_years(active_tours, "DepartureDate", "ArrivalDate")
        if 1990 <= y <= cur_fy_yr + 5
    }

    result = []
    for y in sorted(discovered_yrs, reverse=True):
        result.append({
            "fy_year": y,
            "fy_label": f"FY {y}-{y + 1}",
            "short_label": f"FY {y}-{(y + 1) % 100:02d}",
            "is_current": (y == cur_fy_yr),
            "leave_archive_sheet": lr_archives.get(y),
            "tour_archive_sheet": tr_archives.get(y),
        })
    return result


def get_auditor_available_fys(
    conn_override=None,
    as_of_date=None,
    leave_df=None,
    tour_df=None,
    archive_titles=None,
    df_req_active=None,
    df_tour_active=None,
):
    """Compatible alias for get_available_fys."""
    return get_available_fys(
        conn_override=conn_override,
        as_of_date=as_of_date,
        leave_df=leave_df,
        tour_df=tour_df,
        archive_titles=archive_titles,
        df_req_active=df_req_active,
        df_tour_active=df_tour_active,
    )


def _merge_and_dedupe_records(frames, canonical_cols=None):
    """Combines active and archived DataFrames cleanly, normalizing schema and deduplicating by normalized ID."""
    valid = [f.copy() for f in frames if f is not None and not f.empty]
    if not valid:
        cols = list(canonical_cols or [])
        if "_source_sheet" not in cols:
            cols.append("_source_sheet")
        return pd.DataFrame(columns=cols)
    combined = pd.concat(valid, ignore_index=True)
    if canonical_cols:
        for col in canonical_cols:
            if col not in combined.columns:
                combined[col] = ""
    if "_source_sheet" not in combined.columns:
        combined["_source_sheet"] = "active"
    else:
        combined["_source_sheet"] = combined["_source_sheet"].fillna("active").replace("", "active")

    if "ID" in combined.columns:
        norm_ids = [
            _normalize_record_id(v)
            for v in combined["ID"].tolist()
        ]
        combined["_dedupe_id"] = norm_ids
        has_id_mask = pd.Series([bool(x) for x in norm_ids], index=combined.index)
        with_id = combined[has_id_mask].copy()
        with_id["ID"] = with_id["_dedupe_id"]
        with_id = with_id.drop_duplicates(subset=["_dedupe_id"], keep="first").drop(columns=["_dedupe_id"])
        without_id = combined[~has_id_mask].drop(columns=["_dedupe_id"])
        out = pd.concat([with_id, without_id], ignore_index=True)
        return out.reset_index(drop=True)
    return combined.drop_duplicates().reset_index(drop=True)


def _filter_df_overlapping_fy(df, fy_yr, start_col, end_col):
    """Filters rows whose [start_col, end_col] overlaps CTA Financial Year [Apr 1 fy_yr .. Mar 31 fy_yr+1] (IST-safe)."""
    if df is None or df.empty or fy_yr is None:
        if df is not None and "_from_target_archive" in df.columns:
            return df.drop(columns=["_from_target_archive"]).reset_index(drop=True)
        return df if df is not None else pd.DataFrame()
    if start_col not in df.columns:
        return df
    fy_start = date(int(fy_yr), 4, 1)
    fy_end = date(int(fy_yr) + 1, 3, 31)
    s_raw = df[start_col].tolist()
    e_raw = df[end_col].tolist() if end_col in df.columns else s_raw

    keep_mask = []
    has_src_flag = "_from_target_archive" in df.columns
    src_flags = df["_from_target_archive"].tolist() if has_src_flag else [False] * len(df)
    for idx in range(len(df)):
        sd = _parse_cta_date(s_raw[idx])
        ed = _parse_cta_date(e_raw[idx])
        if sd is None and ed is None:
            keep_mask.append(bool(src_flags[idx]))
            continue
        s_d = sd if sd is not None else ed
        e_d = ed if ed is not None else s_d
        if e_d < s_d:
            s_d, e_d = e_d, s_d
        keep_mask.append(e_d >= fy_start and s_d <= fy_end)

    filtered = df[keep_mask].copy()
    if "_from_target_archive" in filtered.columns:
        filtered = filtered.drop(columns=["_from_target_archive"])
    return filtered.reset_index(drop=True)


def _sort_records_desc(df, primary_date_col, secondary_date_col=None):
    """Sorts records chronologically descending by primary_date_col (IST-safe)."""
    if df is None or df.empty or primary_date_col not in df.columns:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    p_dates = [_parse_cta_date(v) or date.min for v in out[primary_date_col].tolist()]
    if secondary_date_col and secondary_date_col in out.columns:
        s_dates = [_parse_cta_date(v) or date.min for v in out[secondary_date_col].tolist()]
    else:
        s_dates = p_dates
    out["_sort_p"] = p_dates
    out["_sort_s"] = s_dates
    out["_sort_idx"] = list(range(len(out)))
    out = out.sort_values(
        by=["_sort_p", "_sort_s", "_sort_idx"],
        ascending=[False, False, False],
        kind="mergesort",
    ).drop(columns=["_sort_p", "_sort_s", "_sort_idx"]).reset_index(drop=True)
    return out


def load_fy_records(
    selected_fy_yr=None,
    conn_override=None,
    leave_df_active=None,
    tour_df_active=None,
    archive_titles=None,
    df_req_active=None,
    df_tour_active=None,
):
    """Shared cached loader merging active records and FY archives for any selected FY (or None for All FYs)."""
    active_conn = conn_override if conn_override is not None else conn
    eff_l = leave_df_active if leave_df_active is not None else df_req_active
    eff_t = tour_df_active if tour_df_active is not None else df_tour_active
    df_l_active = eff_l.copy() if eff_l is not None else load_leave_requests()
    df_t_active = eff_t.copy() if eff_t is not None else load_tour_sanctions()

    if df_l_active is not None and not df_l_active.empty:
        df_l_active = df_l_active.copy()
        df_l_active["_source_sheet"] = "active"
        df_l_active["_from_target_archive"] = False
    if df_t_active is not None and not df_t_active.empty:
        df_t_active = df_t_active.copy()
        df_t_active["_source_sheet"] = "active"
        df_t_active["_from_target_archive"] = False

    titles = _resolve_archive_titles(conn_override=conn_override, archive_titles=archive_titles)
    lr_archives = {}
    tr_archives = {}
    for t in (titles or []):
        kind, yr = _extract_fy_year_from_archive_title(t)
        if kind and yr is not None:
            if kind == "LeaveRequests":
                lr_archives[yr] = t
            elif kind == "TourSanctions":
                tr_archives[yr] = t

    l_frames = [df_l_active] if df_l_active is not None and not df_l_active.empty else []
    t_frames = [df_t_active] if df_t_active is not None and not df_t_active.empty else []
    from_lr_arch = False
    from_tr_arch = False

    def _safe_read_arch(sheet_name, mark_target=False):
        try:
            if conn_override is None:
                arch_df = _read_cached_archive_sheet(sheet_name)
            else:
                _gs_read = globals().get("_gsheets_read")
                arch_df = _gs_read(active_conn, sheet_name) if callable(_gs_read) else active_conn.read(worksheet=sheet_name)
            if arch_df is not None and not arch_df.empty:
                arch_df = arch_df.copy()
                arch_df["_source_sheet"] = "archive"
                arch_df["_from_target_archive"] = bool(mark_target)
                return arch_df
        except Exception:
            return None
        return None

    if selected_fy_yr is not None:
        fy_int = int(selected_fy_yr)
        if fy_int in lr_archives:
            arch_df = _safe_read_arch(lr_archives[fy_int], mark_target=True)
            if arch_df is not None:
                l_frames.append(arch_df)
                from_lr_arch = True
        if fy_int in tr_archives:
            arch_df = _safe_read_arch(tr_archives[fy_int], mark_target=True)
            if arch_df is not None:
                t_frames.append(arch_df)
                from_tr_arch = True

        merged_l = _merge_and_dedupe_records(l_frames, canonical_cols=_CANONICAL_LEAVE_COLS)
        merged_t = _merge_and_dedupe_records(t_frames, canonical_cols=_CANONICAL_TOUR_COLS)
        filt_l = _filter_df_overlapping_fy(merged_l, fy_int, "StartDate", "EndDate")
        filt_t = _filter_df_overlapping_fy(merged_t, fy_int, "DepartureDate", "ArrivalDate")
        return {
            "leaves": _sort_records_desc(filt_l, "StartDate", "EndDate"),
            "tours": _sort_records_desc(filt_t, "DepartureDate", "ArrivalDate"),
            "from_leave_archive": from_lr_arch,
            "from_tour_archive": from_tr_arch,
        }

    # selected_fy_yr is None -> Combine Active + All Archive Worksheets
    for yr in sorted(lr_archives.keys(), reverse=True):
        arch_df = _safe_read_arch(lr_archives[yr], mark_target=False)
        if arch_df is not None:
            l_frames.append(arch_df)
            from_lr_arch = True
    for yr in sorted(tr_archives.keys(), reverse=True):
        arch_df = _safe_read_arch(tr_archives[yr], mark_target=False)
        if arch_df is not None:
            t_frames.append(arch_df)
            from_tr_arch = True

    merged_l_all = _merge_and_dedupe_records(l_frames, canonical_cols=_CANONICAL_LEAVE_COLS)
    merged_t_all = _merge_and_dedupe_records(t_frames, canonical_cols=_CANONICAL_TOUR_COLS)
    if "_from_target_archive" in merged_l_all.columns:
        merged_l_all = merged_l_all.drop(columns=["_from_target_archive"])
    if "_from_target_archive" in merged_t_all.columns:
        merged_t_all = merged_t_all.drop(columns=["_from_target_archive"])
    return {
        "leaves": _sort_records_desc(merged_l_all, "StartDate", "EndDate"),
        "tours": _sort_records_desc(merged_t_all, "DepartureDate", "ArrivalDate"),
        "from_leave_archive": from_lr_arch,
        "from_tour_archive": from_tr_arch,
    }


def load_auditor_fy_records(
    selected_fy_yr=None,
    conn_override=None,
    leave_df_active=None,
    tour_df_active=None,
    archive_titles=None,
    df_req_active=None,
    df_tour_active=None,
):
    """Compatible alias for load_fy_records."""
    return load_fy_records(
        selected_fy_yr=selected_fy_yr,
        conn_override=conn_override,
        leave_df_active=leave_df_active,
        tour_df_active=tour_df_active,
        archive_titles=archive_titles,
        df_req_active=df_req_active,
        df_tour_active=df_tour_active,
    )


def render_fy_scope_selector(
    key,
    label="📅 Select Financial Year (FY) Scope (April 1 – March 31)",
    include_all=True,
    as_of_date=None,
    leave_df=None,
    tour_df=None,
):
    """Renders a standardized Financial Year selector (defaulting to Current FY) across dashboards."""
    avail_fys = get_available_fys(as_of_date=as_of_date, leave_df=leave_df, tour_df=tour_df)
    fy_options = []
    for item in avail_fys:
        tag_parts = []
        if item["is_current"]:
            tag_parts.append("Current")
        if item["leave_archive_sheet"] or item["tour_archive_sheet"]:
            tag_parts.append("Archive")
        tag_str = f" ({', '.join(tag_parts)})" if tag_parts else ""
        fy_options.append({
            "fy_year": item["fy_year"],
            "label": f"{item['fy_label']}{tag_str}",
            "info": item,
        })
    if include_all:
        fy_options.append({"fy_year": None, "label": "All Available FYs", "info": None})
    selected = st.selectbox(
        label,
        fy_options,
        index=0,
        format_func=lambda x: x["label"] if isinstance(x, dict) else str(x),
        key=key,
    )
    if isinstance(selected, dict):
        return selected
    return fy_options[0] if fy_options else {"fy_year": get_current_financial_year_dates()[0].year, "label": "Current FY", "info": None}


def build_auditor_demo_defaults(reference_date=None):
    """Creates an isolated in-memory sandbox state for Auditor demonstrations (zero Google Sheets writes)."""
    ref_d = reference_date or get_cta_today()
    cur_s, _, cur_fy_label = get_current_financial_year_dates(ref_d)

    def _years_ago(yrs):
        d = date(ref_d.year - int(yrs), ref_d.month, min(ref_d.day, 28))
        rem_days = int(round((yrs - int(yrs)) * 365.25))
        return (d - timedelta(days=rem_days)).strftime("%Y-%m-%d")

    sample_leaves = pd.DataFrame([
        {
            "ID": "DEMO-LV-001",
            "Name": "Tenzin Demo",
            "Department": "HQ",
            "LeaveType": "CL",
            "StartDate": (cur_s + timedelta(days=14)).strftime("%Y-%m-%d"),
            "StartHalf": "Full Day ཉིན་ཆ་ཚང་།",
            "EndDate": (cur_s + timedelta(days=15)).strftime("%Y-%m-%d"),
            "EndHalf": "Full Day ཉིན་ཆ་ཚང་།",
            "TotalDays": 2.0,
            "Reason": "Demo family errand",
            "ApplicationDate": (cur_s + timedelta(days=10)).strftime("%Y-%m-%d"),
            "Status": "Approved",
            "ApprovedBy": "Admin (Demo)",
            "CLBalance": 7.0,
            "SLBalance": 30.0,
            "ALBalance": 68.0,
            "ULBalance": 164.5,
            "SelectedCoAdmin": "Demo Co-Admin",
            "CoAdminAcknowledged": "Yes",
            "AccountsPunched": "Yes",
        },
        {
            "ID": "DEMO-LV-002",
            "Name": "Dolma Demo",
            "Department": "Delhi",
            "LeaveType": "AL",
            "StartDate": (ref_d + timedelta(days=5)).strftime("%Y-%m-%d"),
            "StartHalf": "Full Day ཉིན་ཆ་ཚང་།",
            "EndDate": (ref_d + timedelta(days=7)).strftime("%Y-%m-%d"),
            "EndHalf": "Full Day ཉིན་ཆ་ཚང་།",
            "TotalDays": 3.0,
            "Reason": "Demo annual leave travel",
            "ApplicationDate": ref_d.strftime("%Y-%m-%d"),
            "Status": "Pending",
            "ApprovedBy": "",
            "CLBalance": 9.0,
            "SLBalance": 30.0,
            "ALBalance": 54.0,
            "ULBalance": 0.0,
            "SelectedCoAdmin": "Demo Co-Admin",
            "CoAdminAcknowledged": "",
            "AccountsPunched": "",
        },
    ])

    raw_staff = pd.DataFrame([
        {
            "Name": "Tenzin Demo",
            "Post": "Senior Auditor (Demo)",
            "Branch": "HQ",
            "JoiningDate": _years_ago(7.5),
            "Opening_CL": 0.0, "Addition_CL": 9.0, "Penalty_CL(UL Effect)": 0.0, "Used_CL": 2.0,
            "Opening_SL": 0.0, "Addition_SL": 30.0, "Penalty_SL(UL Effect)": 0.0, "Used_SL": 0.0,
            "Opening_AL": 38.0, "Addition_AL": 30.0, "Penalty_AL(UL Effect)": 0.0, "Used_AL": 0.0, "Encashed_AL": 0.0,
            "Opening_UL": 164.5, "Addition_UL": 0.0, "Used_UL": 0.0,
            "Cumulative_UL": 18.0, "Last_UL_Milestone": 5.0,
            "Current Year Leave affecting Service Year": 0.0,
            "Cummulative leave effecting Service year.(Education Leave & Etc).": 0.0,
            "Service Year Penalty": 0.0,
            "Active_FY": cur_fy_label,
        },
        {
            "Name": "Dolma Demo",
            "Post": "Accounts Officer (Demo)",
            "Branch": "Delhi",
            "JoiningDate": _years_ago(4.8),
            "Opening_CL": 0.0, "Addition_CL": 9.0, "Penalty_CL(UL Effect)": 0.0, "Used_CL": 0.0,
            "Opening_SL": 0.0, "Addition_SL": 30.0, "Penalty_SL(UL Effect)": 0.0, "Used_SL": 0.0,
            "Opening_AL": 24.0, "Addition_AL": 30.0, "Penalty_AL(UL Effect)": 0.0, "Used_AL": 0.0, "Encashed_AL": 0.0,
            "Opening_UL": 0.0, "Addition_UL": 0.0, "Used_UL": 0.0,
            "Cumulative_UL": 0.0, "Last_UL_Milestone": 0.0,
            "Current Year Leave affecting Service Year": 0.0,
            "Cummulative leave effecting Service year.(Education Leave & Etc).": 0.0,
            "Service Year Penalty": 0.0,
            "Active_FY": cur_fy_label,
        },
        {
            "Name": "Karma Probation",
            "Post": "Junior Clerk (Demo)",
            "Branch": "HQ",
            "JoiningDate": _years_ago(0.4),
            "Opening_CL": 0.0, "Addition_CL": 4.5, "Penalty_CL(UL Effect)": 0.0, "Used_CL": 0.0,
            "Opening_SL": 0.0, "Addition_SL": 15.0, "Penalty_SL(UL Effect)": 0.0, "Used_SL": 0.0,
            "Opening_AL": 0.0, "Addition_AL": 15.0, "Penalty_AL(UL Effect)": 0.0, "Used_AL": 0.0, "Encashed_AL": 0.0,
            "Opening_UL": 0.0, "Addition_UL": 0.0, "Used_UL": 0.0,
            "Cumulative_UL": 0.0, "Last_UL_Milestone": 0.0,
            "Current Year Leave affecting Service Year": 0.0,
            "Cummulative leave effecting Service year.(Education Leave & Etc).": 0.0,
            "Service Year Penalty": 0.0,
            "Active_FY": cur_fy_label,
        },
    ])
    demo_staff = inject_balance_formulas(raw_staff, df_req_override=sample_leaves, reference_date=ref_d)

    demo_legs = [
        {
            "origin": "Dharamshala",
            "destination": "Delhi",
            "departure_date": (ref_d + timedelta(days=10)).strftime("%Y-%m-%d"),
            "arrival_date": (ref_d + timedelta(days=12)).strftime("%Y-%m-%d"),
            "purpose": "Demo Departmental Audit Meeting",
            "mode": "Train",
            "advance": 5000.0,
        },
        {
            "origin": "Delhi",
            "destination": "Bengaluru",
            "departure_date": (ref_d + timedelta(days=13)).strftime("%Y-%m-%d"),
            "arrival_date": (ref_d + timedelta(days=16)).strftime("%Y-%m-%d"),
            "purpose": "Demo Branch Ledger Inspection",
            "mode": "Flight",
            "advance": 12000.0,
        },
    ]
    sample_tours = pd.DataFrame([
        {
            "ID": "DEMO-TR-001",
            "Name": "Tenzin Demo",
            "Branch": "HQ",
            "Role": "Employee",
            "Post": "Senior Auditor (Demo)",
            "Destination": "Delhi -> Bengaluru",
            "DepartureDate": demo_legs[0]["departure_date"],
            "ArrivalDate": demo_legs[1]["arrival_date"],
            "Purpose": "Demo Departmental Audit Meeting | Demo Branch Ledger Inspection",
            "TravelMode": "Train / Flight",
            "AdvanceRequested": 17000.0,
            "ApplicationDate": ref_d.strftime("%Y-%m-%d"),
            "Status": "Pending Co-Admin Support",
            "SelectedCoAdmin": "Demo Co-Admin",
            "SelectedAdmin": "Demo Admin",
            "BranchManagerSupportedBy": "",
            "SupportedBy": "",
            "SanctionedBy": "",
            "AccountsPunched": "No",
            "TravelLegs": json.dumps(demo_legs),
        }
    ])

    return {
        "staff_df": demo_staff,
        "leaves_df": sample_leaves,
        "tours_df": sample_tours,
        "reference_date": ref_d,
        "demo_seq": 100,
    }


def auditor_dashboard():
    """Read-Only Oversight Dashboard for Authorized Auditors:
    1. Multi-FY Organization Calendar & Historical Archive Registers (Read-Only)
    2. Scoped Master Balances, Excel Statements, Active Policy Rules & AuditLog (Read-Only)
    3. Isolated Interactive Demo Sandbox (Leave Application, Rollover & Multi-Leg Tour Workflows)
    4. Complete App Architecture Guide & Policy Manual
    ZERO writes to Google Sheets.
    """
    auditor_name = st.session_state.get("user_name", "Auditor")
    st.title(f"🕵️ Auditor Oversight Dashboard — {auditor_name}")
    st.caption(
        "Read-Only CTA Oversight Portal • Full Organization Visibility (HQ + All Branches) • "
        "Zero Personal Leave/Tour Balance & Excluded from Annual Rollovers"
    )

    render_active_leaves_banner(show_type=True)

    # Top-level Financial Year Discovery & Selector
    avail_fys = get_auditor_available_fys()
    fy_options = []
    for item in avail_fys:
        tag_parts = []
        if item["is_current"]:
            tag_parts.append("Current")
        if item["leave_archive_sheet"] or item["tour_archive_sheet"]:
            tag_parts.append("Archive")
        tag_str = f" ({', '.join(tag_parts)})" if tag_parts else ""
        fy_options.append({
            "fy_year": item["fy_year"],
            "label": f"{item['fy_label']}{tag_str}",
            "info": item,
        })
    fy_options.append({"fy_year": None, "label": "All Available FYs (Combined Registers)", "info": None})

    c_fy_sel, c_fy_badge = st.columns([2.2, 2.8])
    with c_fy_sel:
        selected_fy_opt = st.selectbox(
            "📅 Select Financial Year Scope (April 1 – March 31)",
            fy_options,
            index=0,
            format_func=lambda x: x["label"],
            key="auditor_selected_fy_opt",
        )
    selected_fy_yr = selected_fy_opt["fy_year"]
    fy_meta = load_auditor_fy_records(selected_fy_yr)
    fy_leaves_raw = fy_meta["leaves"]
    fy_tours_raw = fy_meta["tours"]

    with c_fy_badge:
        src_flags = ["Active Sheet"]
        if fy_meta["from_leave_archive"]:
            src_flags.append("LeaveRequests Archive")
        if fy_meta["from_tour_archive"]:
            src_flags.append("TourSanctions Archive")
        st.info(f"🛡️ **Audit Read-Only Mode** | Sources loaded: `{' + '.join(src_flags)}`")

    staff_df = load_staff_master()
    # Defensive guarantee: never treat any Auditor row as holding staff balances
    users_df = load_users_data()
    auditor_names = set()
    if users_df is not None and not users_df.empty and "Role" in users_df.columns:
        auditor_names = {
            str(n).strip()
            for n in users_df[users_df["Role"].astype(str).str.strip() == "Auditor"]["Name"].dropna().tolist()
        }
    if staff_df is not None and not staff_df.empty and auditor_names:
        staff_df = staff_df[~staff_df["Name"].astype(str).str.strip().isin(auditor_names)].copy()

    n_staff = len(staff_df) if staff_df is not None else 0
    n_leaves = len(fy_leaves_raw) if fy_leaves_raw is not None else 0
    approved_days = 0.0
    if fy_leaves_raw is not None and not fy_leaves_raw.empty and "Status" in fy_leaves_raw.columns:
        app_l = fy_leaves_raw[fy_leaves_raw["Status"].astype(str).str.strip() == "Approved"]
        if not app_l.empty:
            for _, ar_row in app_l.iterrows():
                _, is_rev_l, abs_d = _parse_leave_type_info(ar_row.get("LeaveType", ""), ar_row.get("TotalDays", 0.0))
                signed_d = -abs_d if is_rev_l else abs_d
                if selected_fy_yr is not None:
                    bdown_a = get_leave_fy_breakdown(
                        _parse_cta_date(ar_row.get("StartDate")) or ar_row.get("StartDate"),
                        _parse_cta_date(ar_row.get("EndDate")) or ar_row.get("EndDate"),
                        signed_d,
                    )
                    if bdown_a and int(selected_fy_yr) in bdown_a:
                        approved_days += float(bdown_a[int(selected_fy_yr)]["days"])
                    elif not bdown_a:
                        approved_days += signed_d
                else:
                    approved_days += signed_d
    n_tours = len(fy_tours_raw) if fy_tours_raw is not None else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Staff on Active Balance Ledger", f"{n_staff}")
    m2.metric("Leave Records (Selected FY)", f"{n_leaves}")
    m3.metric("Net Approved Leave Days", f"{approved_days:g}")
    m4.metric("Tour Records (Selected FY)", f"{n_tours}")

    tab_cal, tab_reg, tab_bal, tab_demo, tab_guide = st.tabs([
        "📅 Organization Calendar",
        "📋 Leave & Tour Registers (Read-Only)",
        "📊 Master Balances, Policy & Audit Trail",
        "🧪 Interactive Demo Sandbox (Leave & Tour)",
        "📖 Complete App Guide & Policy Manual",
    ])

    # ------------------------------------------------------------------
    # TAB 1: ORGANIZATION CALENDAR (BY SELECTED FY)
    # ------------------------------------------------------------------
    with tab_cal:
        cur_fy_start_yr = avail_fys[0]["fy_year"] if avail_fys else get_current_financial_year_dates()[0].year
        if selected_fy_yr is not None:
            cal_fy_yr = selected_fy_yr
            cal_leaves = fy_leaves_raw
            cal_tours = fy_tours_raw
        else:
            single_fys = [item for item in avail_fys]
            cal_pick = st.selectbox(
                "Pick Specific FY for Monthly Calendar Grid",
                single_fys,
                index=0,
                format_func=lambda x: f"{x['fy_label']}" + (" (Current)" if x["is_current"] else ""),
                key="aud_cal_grid_fy_pick",
            )
            cal_fy_yr = cal_pick["fy_year"] if cal_pick else cur_fy_start_yr
            cal_meta = load_auditor_fy_records(cal_fy_yr)
            cal_leaves = cal_meta["leaves"]
            cal_tours = cal_meta["tours"]

        render_organization_calendar(
            fy_year_override=cal_fy_yr,
            leaves_override=cal_leaves,
            tours_override=cal_tours,
            key_suffix="_aud",
        )
        st.caption(
            "**Legend:** 🟢 Approved Leave | 🟠 Pending Leave | 🔴 Leave Return / Reversal (`Rev-*`) | "
            "⬜ Cancelled Leave | 🔵 Official Tour (`✈️`)"
        )

    # ------------------------------------------------------------------
    # TAB 2: LEAVE & TOUR REGISTERS (READ-ONLY)
    # ------------------------------------------------------------------
    with tab_reg:
        st.subheader("📋 Read-Only Organization Leave & Tour Registers")
        reg_branches = render_branch_filter_checkboxes(key_prefix="aud_reg")
        branch_map = get_user_branch_map()
        scoped_leaves = filter_df_by_branches(fy_leaves_raw, reg_branches, branch_map, name_col="Name")
        scoped_tours = filter_df_by_branches(fy_tours_raw, reg_branches, branch_map, name_col="Name")

        f_c1, f_c2, f_c3 = st.columns(3)
        status_opts = ["All", "Approved", "Pending", "Rejected", "Cancelled", "Sanctioned", "Completed", "Settled"]
        sel_reg_status = f_c1.selectbox("Filter Status", status_opts, index=0, key="aud_reg_status")
        leave_type_opts = ["All", "CL", "SL", "AL", "ML", "PL", "UL", "Other", "Reversals (Return-/Rev-)"]
        sel_reg_lt = f_c2.selectbox("Filter Leave Type", leave_type_opts, index=0, key="aud_reg_lt")
        reg_search = f_c3.text_input("Search Staff Name / Reason / Destination", key="aud_reg_search").strip().lower()

        st.markdown("#### 🍃 Leave Requests Register")
        disp_l = scoped_leaves.copy() if scoped_leaves is not None else pd.DataFrame()
        if not disp_l.empty:
            if sel_reg_status != "All" and "Status" in disp_l.columns:
                disp_l = disp_l[disp_l["Status"].astype(str).str.strip() == sel_reg_status]
            if sel_reg_lt == "Reversals (Return-/Rev-)" and "LeaveType" in disp_l.columns:
                disp_l = disp_l[disp_l["LeaveType"].astype(str).str.startswith(("Return-", "Rev-"))]
            elif sel_reg_lt != "All" and "LeaveType" in disp_l.columns:
                disp_l = disp_l[disp_l["LeaveType"].astype(str).str.strip() == sel_reg_lt]
            if reg_search:
                mask = (
                    disp_l.get("Name", pd.Series("", index=disp_l.index)).astype(str).str.lower().str.contains(reg_search, na=False)
                    | disp_l.get("Reason", pd.Series("", index=disp_l.index)).astype(str).str.lower().str.contains(reg_search, na=False)
                )
                disp_l = disp_l[mask]

        if not disp_l.empty:
            # Annotate FY attribution cleanly (highlights multi-FY spans)
            fy_split_notes = []
            for _, lr_row in disp_l.iterrows():
                bdown = get_leave_fy_breakdown(lr_row.get("StartDate"), lr_row.get("EndDate"), lr_row.get("TotalDays"))
                if len(bdown) > 1:
                    fy_split_notes.append(" | ".join(f"{v['fy_label']}: {v['days']:g}d" for v in bdown.values()))
                elif len(bdown) == 1:
                    only_v = next(iter(bdown.values()))
                    fy_split_notes.append(only_v["fy_label"])
                else:
                    fy_split_notes.append("")
            disp_l_view = disp_l.copy()
            disp_l_view["FY_Split"] = fy_split_notes
            pref_l_cols = [
                c for c in [
                    "ID", "Name", "Department", "LeaveType", "StartDate", "StartHalf",
                    "EndDate", "EndHalf", "TotalDays", "FY_Split", "Status",
                    "Reason", "ApplicationDate", "ApprovedBy", "CoAdminAcknowledged", "AccountsPunched",
                ]
                if c in disp_l_view.columns
            ]
            st.dataframe(disp_l_view[pref_l_cols], use_container_width=True, hide_index=True)

            # Read-only Tibetan Leave PDF Preview & Download
            l_pick_labels = [
                f"{r.get('ID', '')} — {r.get('Name', '')} ({r.get('LeaveType', '')}: {r.get('StartDate', '')} to {r.get('EndDate', '')}, {r.get('Status', '')})"
                for _, r in disp_l.iterrows()
            ]
            c_lp1, c_lp2 = st.columns([4, 1.3])
            sel_l_idx = c_lp1.selectbox(
                "Inspect Official Tibetan Leave PDF (Read-Only)",
                range(len(l_pick_labels)),
                format_func=lambda i: l_pick_labels[i],
                key="aud_leave_pdf_pick",
            )
            chosen_l_row = disp_l.iloc[sel_l_idx]
            if c_lp2.button("📄 Preview PDF", key="aud_btn_prev_lpdf", use_container_width=True):
                l_pdf_bytes = generate_leave_pdf(chosen_l_row)
                if l_pdf_bytes:
                    open_pdf_dialog(l_pdf_bytes, f"{chosen_l_row.get('Name', 'Leave')}_{chosen_l_row.get('ID', '')}.pdf")
        else:
            st.info("No leave records match the selected FY, branch, and filter criteria.")

        st.divider()
        st.markdown("#### ✈️ Official Tour Sanctions Register")
        disp_t = scoped_tours.copy() if scoped_tours is not None else pd.DataFrame()
        if not disp_t.empty:
            if sel_reg_status != "All" and "Status" in disp_t.columns:
                disp_t = disp_t[disp_t["Status"].astype(str).str.strip() == sel_reg_status]
            if reg_search:
                mask_t = (
                    disp_t.get("Name", pd.Series("", index=disp_t.index)).astype(str).str.lower().str.contains(reg_search, na=False)
                    | disp_t.get("Destination", pd.Series("", index=disp_t.index)).astype(str).str.lower().str.contains(reg_search, na=False)
                    | disp_t.get("Purpose", pd.Series("", index=disp_t.index)).astype(str).str.lower().str.contains(reg_search, na=False)
                )
                disp_t = disp_t[mask_t]

        if not disp_t.empty:
            pref_t_cols = [
                c for c in [
                    "ID", "Name", "Branch", "Destination", "DepartureDate", "ArrivalDate",
                    "TotalDays", "ConveyanceMode", "TravelMode", "AdvanceRequested", "Status",
                    "BMSupportedBy", "BranchManagerSupportedBy",
                    "SupportedBy", "ApprovedBy", "SanctionedBy", "AccountsPunched", "Purpose",
                ]
                if c in disp_t.columns
            ]
            st.dataframe(disp_t[pref_t_cols], use_container_width=True, hide_index=True)

            t_pick_labels = [
                f"{r.get('ID', '')} — {r.get('Name', '')} -> {r.get('Destination', '')} ({r.get('DepartureDate', '')} to {r.get('ArrivalDate', '')}, {r.get('Status', '')})"
                for _, r in disp_t.iterrows()
            ]
            c_tp1, c_tp2 = st.columns([4, 1.3])
            sel_t_idx = c_tp1.selectbox(
                "Inspect Official Tibetan Tour PDF (Read-Only)",
                range(len(t_pick_labels)),
                format_func=lambda i: t_pick_labels[i],
                key="aud_tour_pdf_pick",
            )
            chosen_t_row = disp_t.iloc[sel_t_idx]
            if c_tp2.button("📄 Preview PDF", key="aud_btn_prev_tpdf", use_container_width=True):
                t_pdf_bytes = generate_tour_sanction_pdf(chosen_t_row)
                if t_pdf_bytes:
                    open_pdf_dialog(t_pdf_bytes, f"{chosen_t_row.get('Name', 'Tour')}_{chosen_t_row.get('ID', '')}.pdf")
        else:
            st.info("No tour sanction records match the selected FY, branch, and filter criteria.")

    # ------------------------------------------------------------------
    # TAB 3: MASTER BALANCES, EXCEL EXPORTS, POLICY RULES & AUDIT TRAIL
    # ------------------------------------------------------------------
    with tab_bal:
        st.subheader("📊 Master Balance Ledger, Policy Parameters & Audit Trail (Read-Only)")
        bal_branches = render_branch_filter_checkboxes(key_prefix="aud_bal")
        branch_map_b = get_user_branch_map()
        scoped_staff = filter_df_by_branches(staff_df, bal_branches, branch_map_b, name_col="Name")

        if scoped_staff is not None and not scoped_staff.empty:
            st.dataframe(scoped_staff, use_container_width=True, hide_index=True)

            # Read-Only Excel Statement Downloads
            import io
            from openpyxl import Workbook
            from openpyxl.styles import Font

            ex1, ex2 = st.columns(2)
            with ex1:
                buf_master = io.BytesIO()
                with pd.ExcelWriter(buf_master, engine="openpyxl") as writer:
                    scoped_staff.to_excel(writer, index=False, sheet_name="Master Leave Statement")
                st.download_button(
                    label="⬇️ Download Scoped Master Balance Statement (.xlsx)",
                    data=buf_master.getvalue(),
                    file_name=f"CTA_Auditor_Master_Statement_{get_cta_today().strftime('%Y-%m-%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="aud_dl_master_stmt",
                    use_container_width=True,
                )

            with ex2:
                emp_pick = st.selectbox(
                    "Single Employee Statement Export",
                    scoped_staff["Name"].tolist(),
                    key="aud_single_emp_pick",
                )
                u_rec = scoped_staff[scoped_staff["Name"] == emp_pick].iloc[0]
                stmt_rows = pd.DataFrame({
                    "Leave Type": ["Casual Leave (CL)", "Sick Leave (SL)", "Annual Leave (AL)", "Unpaid Leave (UL)"],
                    "Opening": [u_rec.get("Opening_CL", 0), u_rec.get("Opening_SL", 0), u_rec.get("Opening_AL", 0), u_rec.get("Opening_UL", 0)],
                    "Addition": [u_rec.get("Addition_CL", 0), u_rec.get("Addition_SL", 0), u_rec.get("Addition_AL", 0), u_rec.get("Addition_UL", 0)],
                    "Used": [u_rec.get("Used_CL", 0), u_rec.get("Used_SL", 0), u_rec.get("Used_AL", 0), u_rec.get("Used_UL", 0)],
                    "Balance": [u_rec.get("Balance_CL", 0), u_rec.get("Balance_SL", 0), u_rec.get("Balance_AL", 0), u_rec.get("Balance_UL", 0)],
                })
                buf_emp = io.BytesIO()
                wb = Workbook()
                ws = wb.active
                ws.title = "Employee Audit Statement"
                ws.append([f"Audit Leave Statement — {emp_pick}"])
                ws.cell(row=1, column=1).font = Font(bold=True)
                ws.append(["Leave Type", "Opening", "Addition", "Used", "Balance"])
                for _, sr in stmt_rows.iterrows():
                    ws.append(sr.tolist())
                ws.append([])
                ws.append([f"Leave History in Selected FY ({selected_fy_opt['label']})"])
                ws.append(["ID", "LeaveType", "StartDate", "EndDate", "TotalDays", "Status", "ApprovedBy", "AccountsPunched"])
                emp_hist = (
                    fy_leaves_raw[fy_leaves_raw["Name"].astype(str).str.strip() == str(emp_pick).strip()]
                    if (fy_leaves_raw is not None and not fy_leaves_raw.empty and "Name" in fy_leaves_raw.columns)
                    else pd.DataFrame()
                )
                for _, er in emp_hist.iterrows():
                    ws.append([
                        er.get("ID", ""), er.get("LeaveType", ""), er.get("StartDate", ""),
                        er.get("EndDate", ""), er.get("TotalDays", ""), er.get("Status", ""),
                        er.get("ApprovedBy", ""), er.get("AccountsPunched", ""),
                    ])
                wb.save(buf_emp)
                st.download_button(
                    label=f"📄 Download {emp_pick} Statement (.xlsx)",
                    data=buf_emp.getvalue(),
                    file_name=f"CTA_Audit_{emp_pick}_Statement.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="aud_dl_single_stmt",
                    use_container_width=True,
                )
        else:
            st.info("No Staff_Master rows found for the selected branch scope.")

        st.divider()
        st.markdown("#### ⚖️ Active System Policy Rules (`LeaveRules` Live Configuration)")
        eff_rules = get_effective_leave_rules()
        rules_df = leave_rules_to_df(eff_rules)
        st.dataframe(rules_df, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("#### 📜 Balance Adjustment & Rollover Audit Log (`AuditLog`)")
        audit_df = load_audit_log()
        if audit_df is not None and not audit_df.empty:
            audit_q = st.text_input("Filter AuditLog (Employee, Adjuster, Category, or Reason)", key="aud_log_filter").strip().lower()
            disp_audit = audit_df.copy()
            if audit_q:
                a_mask = (
                    disp_audit.get("Employee", pd.Series("", index=disp_audit.index)).astype(str).str.lower().str.contains(audit_q, na=False)
                    | disp_audit.get("Adjuster", pd.Series("", index=disp_audit.index)).astype(str).str.lower().str.contains(audit_q, na=False)
                    | disp_audit.get("Category", pd.Series("", index=disp_audit.index)).astype(str).str.lower().str.contains(audit_q, na=False)
                    | disp_audit.get("Reason", pd.Series("", index=disp_audit.index)).astype(str).str.lower().str.contains(audit_q, na=False)
                )
                disp_audit = disp_audit[a_mask]
            if "Timestamp" in disp_audit.columns:
                disp_audit = disp_audit.sort_values(
                    by="Timestamp",
                    ascending=False,
                    kind="mergesort",
                ).reset_index(drop=True)
            st.dataframe(disp_audit, use_container_width=True, hide_index=True)
        else:
            st.info("No entries recorded in `AuditLog` yet.")

    # ------------------------------------------------------------------
    # TAB 4: INTERACTIVE DEMO SANDBOX (ZERO GOOGLE SHEETS WRITES)
    # ------------------------------------------------------------------
    with tab_demo:
        st.subheader("🧪 Interactive Demo Sandbox — Test Full Leave, Rollover & Tour Workflows")
        st.warning(
            "🛡️ **Isolated Session Sandbox:** Everything you do below happens strictly in browser session memory "
            "(`st.session_state._auditor_demo_state`). Zero writes are made to Google Sheets or live staff ledgers."
        )

        if "_auditor_demo_state" not in st.session_state:
            st.session_state["_auditor_demo_state"] = build_auditor_demo_defaults()

        demo_state = st.session_state["_auditor_demo_state"]
        d_top1, d_top2 = st.columns([3.5, 1.2])
        with d_top1:
            st.caption("Test realistic scenarios: Probation minimum-service hard blocks, weekend-bridge alerts, multi-FY crossing, leave reversals, midnight March 31 IST rollover, and multi-leg Tour Sanctions.")
        with d_top2:
            if st.button("🔄 Reset Demo Sandbox", key="aud_demo_reset", use_container_width=True):
                st.session_state["_auditor_demo_state"] = build_auditor_demo_defaults()
                st.session_state.pop("_auditor_demo_rollover_preview", None)
                st.rerun()

        # Recompute live formulas from demo state
        demo_ref_d = demo_state["reference_date"]
        demo_staff_df = inject_balance_formulas(
            demo_state["staff_df"],
            df_req_override=demo_state["leaves_df"],
            reference_date=demo_ref_d,
        )
        demo_state["staff_df"] = demo_staff_df

        st.markdown("##### 👥 Sandbox Staff Balances (In-Memory)")
        demo_summary_cols = [
            c for c in [
                "Name", "Branch", "Post", "JoiningDate", "Service Year", "Actual Service Year",
                "Balance_CL", "Balance_SL", "Balance_AL", "Balance_UL", "Cumulative_UL", "Active_FY",
            ]
            if c in demo_staff_df.columns
        ]
        st.dataframe(demo_staff_df[demo_summary_cols], use_container_width=True, hide_index=True)

        # --- PART A: DEMO LEAVE APPLICATION + LIVE POLICY VALIDATOR ---
        st.markdown("##### A. Simulate Leave Application & Policy Engine")
        dl_c1, dl_c2, dl_c3 = st.columns(3)
        demo_emp_name = dl_c1.selectbox("Demo Applicant", demo_staff_df["Name"].tolist(), key="aud_demo_l_emp")
        demo_lt = dl_c2.selectbox("Leave Type", ["CL", "SL", "AL", "ML", "PL", "UL", "Other"], key="aud_demo_l_type")
        demo_reason = dl_c3.text_input("Reason / Purpose", value="Auditor walkthrough simulation", key="aud_demo_l_reason")

        dc1, dc2, dc3, dc4 = st.columns(4)
        d_start = dc1.date_input("Start Date", value=demo_ref_d + timedelta(days=2), key="aud_demo_l_sd")
        d_sh = dc2.selectbox("Start Half", ["Full Day ཉིན་ཆ་ཚང་།", "Morning ཞོགས་པ།", "Evening ཉིན་རྒྱབ།"], key="aud_demo_l_sh")
        d_end = dc3.date_input("End Date", value=demo_ref_d + timedelta(days=3), key="aud_demo_l_ed")
        if d_start == d_end:
            d_eh_opts = [d_sh]
        else:
            d_eh_opts = ["Full Day ཉིན་ཆ་ཚང་།", "Morning ཞོགས་པ།", "Evening ཉིན་རྒྱབ།"]
        d_eh = dc4.selectbox("End Half", d_eh_opts, key="aud_demo_l_eh")

        if d_start > d_end:
            auto_days = 0.0
        elif d_start == d_end:
            auto_days = 0.5 if ("Morning" in d_sh or "Evening" in d_sh) else 1.0
        else:
            auto_days = float((d_end - d_start).days + 1)
            if "Evening" in d_sh:
                auto_days -= 0.5
            if "Morning" in d_eh:
                auto_days -= 0.5

        dl_d1, dl_d2 = st.columns([1.4, 2.6])
        demo_days = dl_d1.number_input("Total Leave Days (editable for holidays)", min_value=0.0, value=float(auto_days), step=0.5, key="aud_demo_l_days")
        with dl_d2:
            if d_start <= d_end and demo_days > 0:
                d_bdown = get_leave_fy_breakdown(d_start, d_end, demo_days)
                if d_bdown:
                    parts = [f"**{v['fy_label']}**: {v['days']:g}d" for v in d_bdown.values()]
                    st.info("📅 **Multi-FY Attribution:** " + " &nbsp;|&nbsp; ".join(parts))

        sel_demo_row = demo_staff_df[demo_staff_df["Name"] == demo_emp_name].iloc[0]
        demo_raw_bals = {
            "CL": _safe_num(sel_demo_row.get("Balance_CL", 0.0)),
            "SL": _safe_num(sel_demo_row.get("Balance_SL", 0.0)),
            "AL": _safe_num(sel_demo_row.get("Balance_AL", 0.0)),
            "UL": _safe_num(sel_demo_row.get("Balance_UL", 0.0)),
            "Service Year": _safe_num(sel_demo_row.get("Actual Service Year", sel_demo_row.get("Service Year", 0.0))),
        }

        val_res = validate_leave_application(
            user_name=demo_emp_name,
            leave_type=demo_lt,
            start_date=d_start,
            start_half=d_sh,
            end_date=d_end,
            end_half=d_eh,
            total_days=demo_days,
            raw_balances=demo_raw_bals,
            df_requests=demo_state["leaves_df"],
        )

        if val_res["hard_blocks"]:
            for blk in val_res["hard_blocks"]:
                st.error(blk)
        else:
            st.success("✅ **Tier 1 Hard-Block Check Passed:** No balance, minimum-service, cap, or date-overlap violations.")
        for alert in val_res["policy_alerts"]:
            st.warning(alert)

        btn_dl1, btn_dl2 = st.columns(2)
        if btn_dl1.button(
            "🧪 Submit Demo Leave to Sandbox Queue",
            disabled=bool(val_res["hard_blocks"] or demo_days <= 0 or d_start > d_end),
            type="primary",
            key="aud_demo_submit_l",
            use_container_width=True,
        ):
            demo_state["demo_seq"] += 1
            new_id = f"DEMO-LV-{demo_state['demo_seq']}"
            new_l = pd.DataFrame([{
                "ID": new_id,
                "Name": demo_emp_name,
                "Department": str(sel_demo_row.get("Branch", "HQ")),
                "LeaveType": demo_lt,
                "StartDate": d_start.strftime("%Y-%m-%d"),
                "StartHalf": d_sh,
                "EndDate": d_end.strftime("%Y-%m-%d"),
                "EndHalf": d_eh,
                "TotalDays": float(demo_days),
                "Reason": demo_reason,
                "ApplicationDate": demo_ref_d.strftime("%Y-%m-%d"),
                "Status": "Pending",
                "ApprovedBy": "",
                "CLBalance": demo_raw_bals["CL"],
                "SLBalance": demo_raw_bals["SL"],
                "ALBalance": demo_raw_bals["AL"],
                "ULBalance": demo_raw_bals["UL"],
                "SelectedCoAdmin": "Demo Co-Admin",
                "CoAdminAcknowledged": "",
                "AccountsPunched": "",
            }])
            demo_state["leaves_df"] = pd.concat([demo_state["leaves_df"], new_l], ignore_index=True)
            st.success(f"Added sandbox leave `{new_id}` as Pending!")
            st.rerun()

        preview_demo_l_row = {
            "Name": demo_emp_name,
            "Post": str(sel_demo_row.get("Post", "Demo Staff")),
            "JoiningDate": str(sel_demo_row.get("JoiningDate", "")),
            "Department": str(sel_demo_row.get("Branch", "HQ")),
            "StartDate": d_start.strftime("%Y-%m-%d"),
            "StartHalf": d_sh,
            "EndDate": d_end.strftime("%Y-%m-%d"),
            "EndHalf": d_eh,
            "TotalDays": demo_days,
            "LeaveType": demo_lt,
            "Reason": demo_reason,
            "ApplicationDate": demo_ref_d.strftime("%Y-%m-%d"),
            "CLBalance": demo_raw_bals["CL"],
            "ALBalance": demo_raw_bals["AL"],
            "SLBalance": demo_raw_bals["SL"],
            "ULBalance": demo_raw_bals["UL"],
        }
        if btn_dl2.button("📄 Generate Demo Tibetan Leave PDF", key="aud_demo_prev_lpdf", use_container_width=True):
            demo_l_pdf = generate_leave_pdf(preview_demo_l_row)
            if demo_l_pdf:
                open_pdf_dialog(demo_l_pdf, f"DEMO_{demo_emp_name}_Leave.pdf")

        # --- PART B: DEMO APPROVAL, REVERSAL & MARCH 31 FY ROLLOVER SIMULATOR ---
        st.markdown("##### B. Simulate Approvals, Returns/Reversals & Midnight March 31 IST FY Rollover")
        d_leaves = demo_state["leaves_df"]
        if d_leaves is not None and not d_leaves.empty:
            for l_i, lr in d_leaves.iterrows():
                col_txt, col_b1, col_b2, col_b3 = st.columns([3.2, 0.9, 0.9, 1.0])
                col_txt.write(
                    f"`{lr['ID']}` **{lr['Name']}** — `{lr['LeaveType']}` "
                    f"({lr['StartDate']} to {lr['EndDate']}, {lr['TotalDays']:g}d) • **{lr['Status']}**"
                )
                if lr["Status"] == "Pending":
                    if col_b1.button("✅ Approve", key=f"aud_demo_app_{l_i}", use_container_width=True):
                        demo_state["leaves_df"].at[l_i, "Status"] = "Approved"
                        demo_state["leaves_df"].at[l_i, "ApprovedBy"] = "Demo Admin"
                        lt_code = str(lr["LeaveType"]).strip()
                        used_col = f"Used_{lt_code}"
                        sm_m = demo_state["staff_df"]["Name"] == lr["Name"]
                        if used_col in demo_state["staff_df"].columns and sm_m.any():
                            demo_state["staff_df"].loc[sm_m, used_col] = (
                                pd.to_numeric(demo_state["staff_df"].loc[sm_m, used_col], errors="coerce").fillna(0.0)
                                + float(lr["TotalDays"])
                            )
                        st.rerun()
                    if col_b2.button("❌ Reject", key=f"aud_demo_rej_{l_i}", use_container_width=True):
                        demo_state["leaves_df"].at[l_i, "Status"] = "Rejected"
                        st.rerun()
                elif lr["Status"] == "Approved" and not str(lr["LeaveType"]).startswith(("Return-", "Rev-")):
                    if col_b3.button("↩️ Reverse", key=f"aud_demo_rev_{l_i}", use_container_width=True):
                        demo_state["demo_seq"] += 1
                        rev_id = f"DEMO-REV-{demo_state['demo_seq']}"
                        rev_lt = f"Return-{lr['LeaveType']}"
                        rev_days = -abs(float(lr["TotalDays"]))
                        rev_row = pd.DataFrame([{
                            "ID": rev_id,
                            "Name": lr["Name"],
                            "Department": lr.get("Department", "HQ"),
                            "LeaveType": rev_lt,
                            "StartDate": lr["StartDate"],
                            "StartHalf": lr.get("StartHalf", "Full Day ཉིན་ཆ་ཚང་།"),
                            "EndDate": lr["EndDate"],
                            "EndHalf": lr.get("EndHalf", "Full Day ཉིན་ཆ་ཚང་།"),
                            "TotalDays": rev_days,
                            "Reason": f"Auditor Demo Reversal of {lr['ID']}",
                            "ApplicationDate": demo_ref_d.strftime("%Y-%m-%d"),
                            "Status": "Approved",
                            "ApprovedBy": "Demo Admin",
                            "SelectedCoAdmin": "Demo Co-Admin",
                            "CoAdminAcknowledged": "Yes",
                            "AccountsPunched": "Yes",
                        }])
                        demo_state["leaves_df"] = pd.concat([demo_state["leaves_df"], rev_row], ignore_index=True)
                        used_col = f"Used_{str(lr['LeaveType']).strip()}"
                        sm_m = demo_state["staff_df"]["Name"] == lr["Name"]
                        if used_col in demo_state["staff_df"].columns and sm_m.any():
                            demo_state["staff_df"].loc[sm_m, used_col] = (
                                pd.to_numeric(demo_state["staff_df"].loc[sm_m, used_col], errors="coerce").fillna(0.0)
                                + rev_days
                            )
                        st.rerun()

        cur_fy_start, _, _ = get_current_financial_year_dates(demo_ref_d)
        sim_roll_date = date(cur_fy_start.year + 1, 4, 1)
        if st.button(
            f"🕒 Simulate Midnight March 31 IST FY Rollover (Advance Sandbox -> FY {cur_fy_start.year + 1}-{cur_fy_start.year + 2})",
            key="aud_demo_rollover_btn",
        ):
            before_snap = demo_state["staff_df"].copy()
            after_df, roll_info = process_auto_fy_rollover_df(
                before_snap,
                as_of_date=sim_roll_date,
                leave_df=demo_state["leaves_df"],
            )
            st.session_state["_auditor_demo_rollover_preview"] = {
                "before": before_snap,
                "after": after_df,
                "info": roll_info,
            }

        roll_prev = st.session_state.get("_auditor_demo_rollover_preview")
        if roll_prev:
            r_info = roll_prev["info"]
            st.success(
                f"✅ **Simulated Rollover Result:** Rolled **{r_info.get('rolled_count', 0)}** sandbox staff "
                f"to `{r_info.get('target_fy', '')}` (Pro-rata first-year resets, CL/SL annual grants, AL carry-forward capped at rule limit, and UL milestone checks applied)."
            )
            comp_rows = []
            b_df = roll_prev["before"]
            a_df = roll_prev["after"]
            for idx_r in range(len(a_df)):
                ar = a_df.iloc[idx_r]
                br = b_df.iloc[idx_r] if idx_r < len(b_df) else ar
                comp_rows.append({
                    "Name": ar.get("Name"),
                    "FY Before -> After": f"{br.get('Active_FY')} -> {ar.get('Active_FY')}",
                    "Actual Svc Yr (Before -> After)": f"{_safe_num(br.get('Actual Service Year')):.2f} -> {_safe_num(ar.get('Actual Service Year')):.2f}",
                    "CL (Before -> Next Opening+Add)": f"{_safe_num(br.get('Balance_CL')):g} -> {_safe_num(ar.get('Balance_CL')):g}",
                    "SL (Before -> Next Balance)": f"{_safe_num(br.get('Balance_SL')):g} -> {_safe_num(ar.get('Balance_SL')):g}",
                    "AL (Closing -> Carry+Add)": f"{_safe_num(br.get('Balance_AL')):g} -> {_safe_num(ar.get('Opening_AL')):g} + {_safe_num(ar.get('Addition_AL')):g} = {_safe_num(ar.get('Balance_AL')):g}",
                    "Svc Penalty": f"{_safe_num(br.get('Service Year Penalty')):g} -> {_safe_num(ar.get('Service Year Penalty')):g}",
                })
            st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)

        # --- PART C: DEMO MULTI-LEG TOUR SANCTION WORKFLOW ---
        st.markdown("##### C. Simulate Multi-Leg Tour Sanction & 4-Step Approval Chain")
        dt_c1, dt_c2, dt_c3, dt_c4 = st.columns(4)
        demo_t_emp = dt_c1.selectbox("Tour Applicant", demo_staff_df["Name"].tolist(), key="aud_demo_t_emp")
        t_orig = dt_c2.text_input("Leg 1 Origin", value="Dharamshala", key="aud_demo_t_orig")
        t_dest = dt_c3.text_input("Leg 1 Destination", value="Delhi", key="aud_demo_t_dest")
        t_mode = dt_c4.selectbox("Mode", ["Train", "Flight", "Bus", "Official Vehicle", "Taxi"], key="aud_demo_t_mode")

        dt2_c1, dt2_c2, dt2_c3, dt2_c4 = st.columns(4)
        t_dep = dt2_c1.date_input("Departure Date", value=demo_ref_d + timedelta(days=7), key="aud_demo_t_dep")
        t_arr = dt2_c2.date_input("Arrival Date", value=demo_ref_d + timedelta(days=10), key="aud_demo_t_arr")
        t_adv = dt2_c3.number_input("Advance Requested (INR)", min_value=0.0, value=8000.0, step=500.0, key="aud_demo_t_adv")
        t_purp = dt2_c4.text_input("Tour Purpose", value="Sandbox Field Audit Inspection", key="aud_demo_t_purp")

        if st.button("✈️ Submit Demo Tour Request", key="aud_demo_t_submit"):
            emp_br_row = demo_staff_df[demo_staff_df["Name"] == demo_t_emp].iloc[0]
            emp_br = str(emp_br_row.get("Branch", "HQ")).strip()
            init_status = "Pending Co-Admin Support" if normalize_branch_code(emp_br) == "HQ" else "Pending Branch Manager Support"
            demo_state["demo_seq"] += 1
            t_id = f"DEMO-TR-{demo_state['demo_seq']}"
            legs_payload = [{
                "origin": t_orig,
                "destination": t_dest,
                "departure_date": t_dep.strftime("%Y-%m-%d"),
                "arrival_date": t_arr.strftime("%Y-%m-%d"),
                "purpose": t_purp,
                "mode": t_mode,
                "advance": float(t_adv),
            }]
            new_tr = pd.DataFrame([{
                "ID": t_id,
                "Name": demo_t_emp,
                "Branch": emp_br,
                "Role": "Employee",
                "Post": str(emp_br_row.get("Post", "Demo Staff")),
                "Destination": t_dest,
                "DepartureDate": t_dep.strftime("%Y-%m-%d"),
                "ArrivalDate": t_arr.strftime("%Y-%m-%d"),
                "Purpose": t_purp,
                "TravelMode": t_mode,
                "AdvanceRequested": float(t_adv),
                "ApplicationDate": demo_ref_d.strftime("%Y-%m-%d"),
                "Status": init_status,
                "SelectedCoAdmin": "Demo Co-Admin",
                "SelectedAdmin": "Demo Admin",
                "BranchManagerSupportedBy": "",
                "SupportedBy": "",
                "SanctionedBy": "",
                "AccountsPunched": "No",
                "TravelLegs": json.dumps(legs_payload),
            }])
            demo_state["tours_df"] = pd.concat([demo_state["tours_df"], new_tr], ignore_index=True)
            st.success(f"Created sandbox tour `{t_id}` ({init_status})!")
            st.rerun()

        d_tours = demo_state["tours_df"]
        if d_tours is not None and not d_tours.empty:
            for t_i, tr in d_tours.iterrows():
                tc_txt, tc_step, tc_pdf = st.columns([3.2, 1.6, 1.0])
                tc_txt.write(
                    f"`{tr['ID']}` **{tr['Name']}** ({tr['Branch']}) -> **{tr['Destination']}** "
                    f"({tr['DepartureDate']} to {tr['ArrivalDate']}) • **{tr['Status']}** | Punched: {tr.get('AccountsPunched', 'No')}"
                )
                cur_ts = str(tr["Status"]).strip()
                if cur_ts == "Pending Branch Manager Support":
                    if tc_step.button("🏢 BM Support", key=f"aud_demo_t_bm_{t_i}", use_container_width=True):
                        demo_state["tours_df"].at[t_i, "BranchManagerSupportedBy"] = "Demo Branch Manager"
                        demo_state["tours_df"].at[t_i, "Status"] = "Pending Co-Admin Support"
                        st.rerun()
                elif cur_ts == "Pending Co-Admin Support":
                    if tc_step.button("👍 Co-Admin Endorse", key=f"aud_demo_t_co_{t_i}", use_container_width=True):
                        demo_state["tours_df"].at[t_i, "SupportedBy"] = "Demo Co-Admin"
                        demo_state["tours_df"].at[t_i, "Status"] = "Pending Admin Sanction"
                        st.rerun()
                elif cur_ts == "Pending Admin Sanction":
                    if tc_step.button("🏛️ Admin Sanction", key=f"aud_demo_t_adm_{t_i}", use_container_width=True):
                        demo_state["tours_df"].at[t_i, "SanctionedBy"] = "Demo Admin"
                        demo_state["tours_df"].at[t_i, "Status"] = "Sanctioned"
                        st.rerun()
                elif cur_ts == "Sanctioned" and str(tr.get("AccountsPunched", "No")).lower() != "yes":
                    if tc_step.button("💳 Accounts Punch", key=f"aud_demo_t_acc_{t_i}", use_container_width=True):
                        demo_state["tours_df"].at[t_i, "AccountsPunched"] = "Yes"
                        demo_state["tours_df"].at[t_i, "Status"] = "Completed"
                        st.rerun()
                else:
                    tc_step.caption("Workflow Complete ✅")

                if tc_pdf.button("📄 PDF", key=f"aud_demo_t_pdf_{t_i}", use_container_width=True):
                    tr_pdf = generate_tour_sanction_pdf(dict(tr))
                    if tr_pdf:
                        open_pdf_dialog(tr_pdf, f"DEMO_{tr['Name']}_{tr['ID']}.pdf")

    # ------------------------------------------------------------------
    # TAB 5: COMPLETE SYSTEM GUIDE & POLICY MANUAL
    # ------------------------------------------------------------------
    with tab_guide:
        st.subheader("📖 Complete CTA Leave & Tour Sanction System Guide")
        guide_rules = get_effective_leave_rules()
        lt_cfg = guide_rules.get("leave_types", {})
        gl_cfg = guide_rules.get("global", {})

        cl_q = _safe_num(lt_cfg.get("CL", {}).get("Annual_Quota", 9.0))
        sl_q = _safe_num(lt_cfg.get("SL", {}).get("Annual_Quota", 30.0))
        al_q = _safe_num(lt_cfg.get("AL", {}).get("Annual_Quota", 30.0))
        ul_q = _safe_num(lt_cfg.get("UL", {}).get("Annual_Quota", 0.0))
        al_cap = _safe_num(lt_cfg.get("AL", {}).get("Max_Accumulation_Cap", 180.0))
        cl_consec = _safe_num(lt_cfg.get("CL", {}).get("Max_Consecutive_Days", 5.0))
        sl_consec = _safe_num(lt_cfg.get("SL", {}).get("Max_Consecutive_Days", 0.0))
        cl_pr_pen = _safe_num(lt_cfg.get("CL", {}).get("Pro_Rata_UL_Penalty", 0.75))
        sl_pr_pen = _safe_num(lt_cfg.get("SL", {}).get("Pro_Rata_UL_Penalty", 2.50))
        al_pr_pen = _safe_num(lt_cfg.get("AL", {}).get("Pro_Rata_UL_Penalty", 2.50))
        ul_ms_int = _safe_num(gl_cfg.get("UL_Milestone_Interval_Years", 5.0))
        ul_ms_max = _safe_num(gl_cfg.get("UL_Max_Milestone_Years", 20.0))
        ul_ms_grant = _safe_num(gl_cfg.get("UL_Grant_Per_Milestone", 182.5))
        ul_career_cap = _safe_num(gl_cfg.get("UL_Career_Max_Cap", 730.0))
        svc_freeze_on = bool(gl_cfg.get("Service_Freeze_Rule", True))

        sl_consec_note = f"{sl_consec:g} days" if sl_consec > 0 else "No fixed per-spell cap (0 = disabled)"
        ul_annual_note = f"{ul_q:g} days/FY + " if ul_q > 0 else ""

        st.markdown(f"""
### 1. System Architecture & Security Perimeter
- **Identity Verification:** Uses two-layer authentication when Google OAuth is active (`st.user.email` matched case-insensitively against the authorized Gmail column in `Users`) paired with a personal **4-digit numeric PIN** and 60-day browser session cookies (`extra_streamlit_components.CookieManager`).
- **Google Sheets Database Tables:**
  - `Users`: Account identities, Gmail perimeter whitelist, 4-digit PIN, `Role`, `Post`, `Branch`, `JoiningDate`, `NtfyTopic`.
  - `Staff_Master`: Complete 32-column live ledger tracking tenure, Opening/Addition/Penalty/Used/Encashed/Balance fields across `CL`, `SL`, `AL`, `UL`, UL milestone history, service-affecting freezes, and `Active_FY`.
  - `LeaveRequests`: Full history of leave applications, half-day segments, statuses, Co-Admin endorsements, Admin approvals, Accounts register punch state, and reversal records (`Return-*` / `Rev-*`).
  - `TourSanctions`: Multi-leg travel itineraries (`TravelLegs` JSON), advances requested, Branch Manager / Co-Admin / Admin approvals, and Accounts claims settlement state.
  - `LeaveRules`: Centralized dynamic configuration storing all active quotas, maximum caps, UL penalty rates, and milestone parameters (loaded automatically on cloud cold start prior to any rollover check).
  - `AuditLog`: Append-only record (`Timestamp`, `Adjuster`, `Employee`, `Category`, `Old_Value`, `New_Value`, `Net_Delta`, `Reason`) capturing manual ledger edits and annual FY rollovers.
  - **FY Archive Snapshots:** `LeaveRequests_Archive_FY<Y1>-<Y2>` and `TourSanctions_Archive_FY<Y1>-<Y2>`, written automatically *before* committing FY rollover ledger updates.
- **Auditor Account Isolation:** Accounts with `Role = "Auditor"` exist strictly in `Users`. They are automatically excluded from `Staff_Master` creation, hold zero personal leave or tour balances, never appear in leave/tour encashment ledgers, and possess zero Google Sheets write controls in their dashboard.

---

### 2. User Roles & Visibility Matrix
| Role | Primary Capabilities | Branch & Calendar Scope | Out-of-Office Banner Visibility |
| :--- | :--- | :--- | :--- |
| **Employee** | Apply Leave (Half/Full Day) & Multi-Leg Tours, request returns/cancellations, view own ledger & Tibetan PDFs | Own records & Home Branch | Own branch staff + **HQ Admin & Co-Admin** absences only |
| **Branch Manager** | Support Branch Leaves & Branch Tours, inspect Org Calendar & **all-branch leave comparison view** | Cross-branch Leave/Calendar visibility + Branch staff management | Selected visible branches + Admin/Co-Admin |
| **Co-Admin** | Endorse HQ & Branch Leave/Tour petitions before Admin sanction; inspect Org Calendar | **Central / Organization-wide** (HQ + all branches) | HQ + all checked branches |
| **Accounts** | Punch approved Leaves & sanctioned Tours in Registers, process claims & **AL Encashment** | Branch-scoped by default (or HQ Central Accounts when HQ branch) | Scoped branch (Central HQ sees all) |
| **Admin** | Final Leave Approval/Rejection, Tour Sanction, Staff & Ledger CRUD, LeaveRules config, Backup/Audit | **Full Organization-wide** (`HQ` + all branches) | Full organization |
| **Auditor** | **Read-Only Oversight** across selectable Financial Years + Archives, Excel Exports, Policy & AuditLog inspection, **Interactive Demo Sandbox** | **Full Organization-wide** (`HQ` + all branches, Zero Writes) | Full organization |

---

### 3. Active Leave Quotas, Mathematical Formulas & Tenure Rules
Live active policy numbers loaded from `LeaveRules`:
- **Casual Leave (CL):** Annual Grant = **`{cl_q:g}` days/FY** (no carry-forward), Max consecutive cap = **`{cl_consec:g}` days**, Pro-rata UL penalty = **`{cl_pr_pen:g}` days per 30d UL**.
  - Formula: `Balance_CL = max(0, Opening_CL + Addition_CL - Penalty_CL(UL Effect) - Used_CL)`
- **Sick Leave (SL):** Annual Grant = **`{sl_q:g}` days/FY** (no carry-forward by default), Consecutive limit = **{sl_consec_note}**, Pro-rata UL penalty = **`{sl_pr_pen:g}` days per 30d UL**.
  - Formula: `Balance_SL = max(0, Opening_SL + Addition_SL - Penalty_SL(UL Effect) - Used_SL)`
- **Annual Leave (AL):** Annual Grant = **`{al_q:g}` days/FY**, Carries forward across FYs up to Max Accumulation Cap = **`{al_cap:g}` days**, Pro-rata UL penalty = **`{al_pr_pen:g}` days per 30d UL**, Encashable = **Yes**.
  - Formula: `Balance_AL = min({al_cap:g}, max(0, Opening_AL + Addition_AL - Penalty_AL(UL Effect) - Used_AL - Encashed_AL))`
- **Unpaid Leave (UL):** {ul_annual_note}Milestone Grant = **`{ul_ms_grant:g}` days** every **`{ul_ms_int:g}` Actual Service Years** up to Year **`{ul_ms_max:g}`** (Career Cap = **`{ul_career_cap:g}` days**).
  - Formula: `Balance_UL = max(0, Opening_UL + Addition_UL - Used_UL)`
- **Service Year & Freezes:**
  - `Service Year = (Today - JoiningDate) in days / 365.25`
  - `Service Year Penalty = (Cumulative_UL + Cumulative_Service_Affecting_Leave) / 365.25`
  - `Actual Service Year = max(0, Service Year - Service Year Penalty)`
  - **Service-Freeze Rule (`{'Enabled' if svc_freeze_on else 'Disabled'}`):** Unpaid leave (`Used_UL` added to `Cumulative_UL`) and Service-Affecting Leave (`Current Year Leave affecting Service Year` carried into `Cummulative leave effecting Service year.(Education Leave & Etc).`) pause/deduct countable service tenure day-for-day.
  - **Pro-Rata UL Deduction Formula:** `Penalty_X = ROUND((Used_UL / 30) * Pro_Rata_UL_Penalty_X * 2) / 2` (nearest 0.5 day).

---

### 4. Two-Tier Leave Validation Engine & Half-Day Slot Math
1. **Half-Day Time-Slot Precision:** Every calendar date splits into 2 integer slots (`Morning` = even slot `2*d`, `Evening` = odd slot `2*d + 1`; `Full Day` spans `[2*d, 2*d + 1]`). Adjacent half-days (`Day 1 Morning` + `Day 1 Evening`) never collide, while true overlapping slots (`Approved` or `Pending`, net of approved `Return-*` reversals) trigger a hard block.
2. **Tier 1 — Hard Blocks (Cannot Be Submitted):**
   - Insufficient effective balance (`Raw Balance - Net Pending Leaves`) for strict-balance types (configured via `Strict_Balance_Required`, e.g., `CL`, `AL`, `UL`).
   - Exact date/slot overlap with another active `Pending` or `Approved` leave.
   - Single request exceeding configured maximum consecutive days (`Max_Consecutive_Days`, e.g., `CL > {cl_consec:g}`).
3. **Tier 2 — Policy Alerts (Weekend-Bridging / Soft Warnings):**
   - **Weekend Combination Policy:** Detects when `Non_Combinable` leave types (e.g. `CL` + `AL` or `CL` + `UL`) sit directly before/after a non-working weekend (2nd/3rd/4th/5th Saturday + Sunday; note **1st Saturday is a working day** at CTA) without a working half-day in between.
   - **Split Consecutive Cap Alert:** Detects separate `CL` applications bridging a non-working weekend whose combined working duration exceeds `{cl_consec:g}` consecutive days.

---

### 5. Annual Financial Year Rollover & Multi-FY Leave Partitioning
- **CTA Financial Year Window:** Runs **April 1 through March 31** in **IST (`Asia/Kolkata`, UTC+5:30)**.
- **Midnight March 31 -> April 1 00:00 IST Rollover (`process_auto_fy_rollover_df`):**
  1. On cold start/session check, loads custom `LeaveRules` from Google Sheets before evaluating FY dates.
  2. Checks each staff member's `Active_FY` against the current IST Financial Year (`April 1` onwards).
  3. **Archive-Before-Commit:** Snapshots existing `LeaveRequests` and `TourSanctions` into `LeaveRequests_Archive_FY...` and `TourSanctions_Archive_FY...` *before* committing updated `Staff_Master` balances (with a concurrency re-check against double rollover across multiple cloud workers).
  4. **Year-1 Pro-Rata Protection & Full-Year UL Guard:** Preserves prorated joining-year handling and zeroes annual quotas when an employee spent a full year on service-affecting/unpaid absence without earning new leave.
  5. **Multi-FY Leave Breakdown (`get_leave_fy_breakdown`):** If an employee applies for a leave spanning e.g. March 28 – April 4, the engine automatically partitions days into the old FY (`March 28–31`) and the new FY (`April 1–4`), preventing post-rollover double-deductions.

---

### 6. End-to-End Workflows (Leaves, Multi-Leg Tours & Encashment)
- **Leave Petition Flow:**
  - `Employee Submits` -> *(Branch Manager visibility/review for branches)* -> `Co-Admin Acknowledgement` -> `Admin Approval / Rejection` -> `Accounts Register Punch` -> *(optional later)* `Return-* / Rev-* Reversal` restoring `Balance_*`.
- **Multi-Leg Tour Sanction Flow:**
  - `Employee Builds Itinerary (Leg 1..N with Mode & Advance)` -> `Branch Manager Support` *(for regional Branch staff)* -> `Co-Admin Endorsement` -> `Admin Final Sanction` -> `Accounts Tour Register Punch & TA/DA Claim Settlement`.
- **AL Encashment:**
  - Processed centrally by Accounts/Admin (`Encashed_AL`), verified strictly against `Balance_AL`, subtracting from remaining encashable AL in `Balance_AL`.

---

### 7. Official Tibetan Unicode PDF Pipeline
- Powered by **`fpdf2` + `uharfbuzz` HarfBuzz Shaping Engine** using the **`Monlam_Uni_OuChan2.ttf`** font bundled inside the repository.
- Automatically converts Gregorian dates & leave counts into **Tibetan numerals (`༠–༩`)** (`to_tibetan_numeral`, `format_tibetan_date`), shapes complex stacked conjuncts accurately, wraps at Tibetan **tsek (`་`)** syllable boundaries (`generate_leave_pdf` / `generate_tour_sanction_pdf`), and renders A4 official CTA forms (`དགོངས་ཞུ་འགེངས་ཤོག` and `ཕྱོགས་བསྐྱོད་བཀའ་འཁྲོལ་ཞུ་ཤོག`).
""")


def main_dashboard():

    # AUTO-SYNC USERS -> STAFF MASTER (in-place renames, new users, and direct Google Sheet edits to Post / JoiningDate / Branch)
    users_df = load_users_data()
    staff_df = load_staff_master()
    if users_df is not None and staff_df is not None:
        sm_needs_sync = False

        # Detect in-place row renames in Users (when a user overwrites an existing row's Name in Users)
        if not users_df.empty and not staff_df.empty and "Name" in users_df.columns and "Name" in staff_df.columns:
            non_aud_users = users_df[users_df["Role"].astype(str).str.strip() != "Auditor"].reset_index(drop=True) if "Role" in users_df.columns else users_df.reset_index(drop=True)
            all_u_names = {str(x).strip() for x in non_aud_users["Name"].dropna().tolist() if str(x).strip()}
            all_sm_names = {str(x).strip() for x in staff_df["Name"].dropna().tolist() if str(x).strip()}
            min_len = min(len(non_aud_users), len(staff_df))
            for pos_i in range(min_len):
                u_nm_i = "" if pd.isna(non_aud_users.at[pos_i, "Name"]) else str(non_aud_users.at[pos_i, "Name"]).strip()
                s_idx_i = staff_df.index[pos_i]
                s_nm_i = "" if pd.isna(staff_df.at[s_idx_i, "Name"]) else str(staff_df.at[s_idx_i, "Name"]).strip()
                if u_nm_i and s_nm_i and u_nm_i != s_nm_i and (u_nm_i not in all_sm_names) and (s_nm_i not in all_u_names):
                    staff_df.at[s_idx_i, "Name"] = u_nm_i
                    all_sm_names.discard(s_nm_i)
                    all_sm_names.add(u_nm_i)
                    sm_needs_sync = True

        missing_users = find_users_missing_from_staff_master(users_df, staff_df)
        if missing_users:
            new_df = pd.DataFrame(missing_users)
            staff_df = pd.concat([staff_df, new_df], ignore_index=True)
            sm_needs_sync = True

        # Sync Post, JoiningDate, and Branch from Users -> Staff_Master when edited directly in Google Sheets
        if not users_df.empty and not staff_df.empty and "Name" in users_df.columns and "Name" in staff_df.columns:
            u_profile_map = {}
            for _, ur in users_df.iterrows():
                unm = "" if pd.isna(ur.get("Name")) else str(ur.get("Name")).strip()
                if unm:
                    u_profile_map[unm] = ur
            for s_idx in staff_df.index:
                snm = "" if pd.isna(staff_df.at[s_idx, "Name"]) else str(staff_df.at[s_idx, "Name"]).strip()
                if snm in u_profile_map:
                    ur = u_profile_map[snm]
                    for sync_col in ("Post", "JoiningDate", "Branch"):
                        if sync_col in ur and sync_col in staff_df.columns:
                            u_val = "" if pd.isna(ur.get(sync_col)) else str(ur.get(sync_col)).strip()
                            s_val = "" if pd.isna(staff_df.at[s_idx, sync_col]) else str(staff_df.at[s_idx, sync_col]).strip()
                            if sync_col == "JoiningDate" and u_val and s_val:
                                # Normalize YYYY/MM/DD vs YYYY-MM-DD before comparing
                                u_d = _parse_cta_date(u_val)
                                s_d = _parse_cta_date(s_val)
                                if u_d is not None and s_d == u_d:
                                    continue
                                if u_d is not None:
                                    u_val = u_d.strftime("%Y-%m-%d")
                            if u_val and u_val not in ("nan", "None") and u_val != s_val:
                                staff_df.at[s_idx, sync_col] = u_val
                                sm_needs_sync = True

        if sm_needs_sync:
            _gsheets_retry(conn.update, worksheet="Staff_Master",
                           data=inject_balance_formulas(staff_df))
            _invalidate_sheet_cache("Staff_Master")
            st.rerun()
        elif not staff_df.empty:
            # Auto-recalculate all Grey Python-calculated columns whenever any Yellow input column was edited directly in Google Sheets
            synced_check_df = inject_balance_formulas(staff_df)
            grey_num_cols = (
                "Service Year", "Service Year Penalty", "Actual Service Year",
                "Penalty_CL(UL Effect)", "Balance_CL",
                "Penalty_SL(UL Effect)", "Balance_SL",
                "Penalty_AL(UL Effect)", "Balance_AL",
                "Balance_UL",
            )
            grey_diff = False
            for idx_s in staff_df.index:
                # Recompute Last_UL_Milestone and Addition_UL from Actual Service Year & Opening_UL + Cumulative_UL
                act_y_s = float(_safe_num(synced_check_df.at[idx_s, "Actual Service Year"])) if "Actual Service Year" in synced_check_df.columns else 0.0
                o_ul_s = float(_safe_num(synced_check_df.at[idx_s, "Opening_UL"])) if "Opening_UL" in synced_check_df.columns else 0.0
                c_ul_s = float(_safe_num(synced_check_df.at[idx_s, "Cumulative_UL"])) if "Cumulative_UL" in synced_check_df.columns else 0.0
                u_ul_s = float(_safe_num(synced_check_df.at[idx_s, "Used_UL"])) if "Used_UL" in synced_check_df.columns else 0.0
                calc_ms_s = float(min(20, int(act_y_s // 5) * 5))
                pool_s = o_ul_s + c_ul_s
                if (
                    (calc_ms_s == 5.0 and pool_s < 182.5)
                    or (calc_ms_s == 10.0 and pool_s < 365.0)
                    or (calc_ms_s == 15.0 and pool_s < 547.5)
                    or (calc_ms_s >= 20.0 and pool_s < 730.0)
                ):
                    calc_add_ul_s = 182.5
                else:
                    calc_add_ul_s = 0.0
                calc_bal_ul_s = max(0.0, o_ul_s + calc_add_ul_s - u_ul_s)
                if "Last_UL_Milestone" in synced_check_df.columns:
                    synced_check_df.at[idx_s, "Last_UL_Milestone"] = calc_ms_s
                if "Addition_UL" in synced_check_df.columns:
                    synced_check_df.at[idx_s, "Addition_UL"] = calc_add_ul_s
                if "Balance_UL" in synced_check_df.columns:
                    synced_check_df.at[idx_s, "Balance_UL"] = calc_bal_ul_s

                for g_col in grey_num_cols + ("Addition_UL", "Last_UL_Milestone"):
                    if g_col in staff_df.columns and g_col in synced_check_df.columns:
                        old_g = float(_safe_num(staff_df.at[idx_s, g_col]))
                        new_g = float(_safe_num(synced_check_df.at[idx_s, g_col]))
                        if abs(old_g - new_g) > 1e-4:
                            grey_diff = True
                            break
                if grey_diff:
                    break

            if grey_diff:
                _gsheets_retry(conn.update, worksheet="Staff_Master", data=synced_check_df)
                _invalidate_sheet_cache("Staff_Master")
                st.rerun()

    # Pre-load custom LeaveRules from Google Sheets on cold start before rollover & balance calculations
    try:
        load_leave_rules()
    except Exception:
        pass

    # Check Active Financial Year (IST) & automatically roll over at midnight of 31 March (April 1 00:00 IST)
    try:
        rollover_info = ensure_auto_fy_rollover()
        if rollover_info and rollover_info.get("rolled_over"):
            st.toast(
                f"📅 Automatic FY Rollover completed for {format_fy_short(rollover_info.get('target_fy', ''))} "
                f"({rollover_info.get('rolled_count', 0)} staff updated).",
                icon="🎉",
            )
    except Exception as e:
        rollover_info = None
        if st.session_state.get("user_role") in ("Admin", "Accounts"):
            st.warning(f"⚠️ Automatic FY Rollover check encountered an issue: {e}")

    rollover_err = st.session_state.get("_last_rollover_error")
    if rollover_err and st.session_state.get("user_role") in ("Admin", "Accounts"):
        st.warning(f"⚠️ FY Rollover Notice: {rollover_err}")

    st.sidebar.title(f"Welcome, {st.session_state.user_name}")
    st.sidebar.write(f"**Role:** {st.session_state.user_role}")

    # DEV MODE QUICK SWITCHER (Active for DEV_MODE or exclusively for authenticated owner tendar6666@gmail.com)
    _owner_dev_email = "tendar6666@gmail.com"
    _st_user_obj = getattr(st, "user", None)
    _oauth_em = str(getattr(_st_user_obj, "email", "") or "").strip().lower() if getattr(_st_user_obj, "is_logged_in", False) else ""
    _perim_em = str(st.session_state.get("perimeter_verified_email", "") or "").strip().lower()
    _cur_em = str(st.session_state.get("user_email", "") or "").strip().lower()
    if _oauth_em == _owner_dev_email or _perim_em == _owner_dev_email or _cur_em == _owner_dev_email:
        st.session_state["_is_owner_dev_session"] = True

    if DEV_MODE or st.session_state.get("_is_owner_dev_session", False):
        with st.sidebar.expander("🛠️ DEV: Switch Role / User", expanded=True):
            st.caption("⚡ Switch account instantly without logging out:")
            u_df_dev = load_users_data()
            if u_df_dev is not None and not u_df_dev.empty:
                user_options = []
                for _, u_r in u_df_dev.iterrows():
                    u_nm = u_r["Name"]
                    u_rl = u_r.get("Role", "Employee")
                    u_br = u_r.get("Branch", "HQ")
                    user_options.append((f"{u_nm} ({u_br} • {u_rl})", u_nm, u_rl, u_r.get("Email", "")))
                
                curr_idx = 0
                for i, opt in enumerate(user_options):
                    if opt[1] == st.session_state.user_name:
                        curr_idx = i
                        break

                selected_opt = st.selectbox(
                    "Switch User",
                    user_options,
                    index=curr_idx,
                    format_func=lambda x: x[0],
                    key="sidebar_dev_switch_user",
                    label_visibility="collapsed"
                )
                if st.button("⚡ Switch User Now", type="primary", use_container_width=True, key="btn_dev_switch_now"):
                    st.session_state.logged_in = True
                    st.session_state.user_name = selected_opt[1]
                    st.session_state.user_role = selected_opt[2]
                    st.session_state.user_email = selected_opt[3]
                    st.rerun()
                st.caption("⚡ **Quick Role Shortcuts:**")
                render_dev_quick_role_buttons(u_df_dev, key_prefix="sb_quick_btn", group_by_branch=False, cols_per_row=2)
    
    # Personal NTFY Channel Card in Sidebar
    my_topic = get_user_ntfy_topic(st.session_state.user_name)
    if my_topic:
        with st.sidebar.expander("🔔 Mobile Notifications (ntfy)", expanded=False):
            st.markdown("**Your Notification Topic:**")
            st.code(my_topic, language=None)
            st.markdown(f"[🌐 Open in Web Browser](https://ntfy.sh/{my_topic})")
            st.caption(
                "📱 **Mobile Phone Setup (2 Easy Steps):**\n"
                "1. Open the free **ntfy** app on your phone.\n"
                "2. Tap **`+`** (Subscribe to topic) and paste the topic code above.\n\n"
                "You'll get instant alerts whenever your leaves or tours are endorsed, sanctioned, or punched!"
            )
    
    if st.sidebar.button("🔄 Refresh Data", type="primary"):
        _invalidate_all_data_caches()
        st.rerun()

    with st.sidebar.expander("🔑 Change PIN"):
        new_pin = st.text_input(
            "New 4-digit PIN", type="password", max_chars=4)
        confirm_pin = st.text_input(
            "Confirm New PIN", type="password", max_chars=4)
        if st.button("Update PIN"):
            clean_new_pin = new_pin.strip()
            clean_confirm_pin = confirm_pin.strip()
            if len(clean_new_pin) != 4 or not clean_new_pin.isdigit():
                st.error("PIN must be exactly 4 numeric digits.")
            elif clean_new_pin != clean_confirm_pin:
                st.error("PINs do not match!")
            else:
                users_df = load_users_data()
                if users_df is not None:
                    user_idx = users_df[users_df["Name"] ==
                                        st.session_state.user_name].index
                    if not user_idx.empty:
                        users_df.at[user_idx[0], "PIN"] = clean_new_pin
                        _gsheets_retry(conn.update, worksheet="Users", data=users_df)
                        _invalidate_sheet_cache("Users")
                        st.success("PIN updated successfully!")

    st.sidebar.divider()
    cur_u_email = st.session_state.get("user_email", "")
    if cur_u_email:
        st.sidebar.caption(f"🔒 Signed in as: `{cur_u_email}`")

    c_sb1, c_sb2 = st.sidebar.columns(2)
    with c_sb1:
        if st.button("🔒 Lock PIN", use_container_width=True, help="Lock dashboard with 4-digit PIN"):
            _release_submission_freeze()
            st.session_state.pop("branch_filter_state", None)
            st.session_state.pop("_is_owner_dev_session", None)
            st.session_state.pending_cookie_delete = True
            st.session_state.logged_in = False
            st.rerun()
    with c_sb2:
        if st.button("🚪 Sign Out", use_container_width=True, help="Forget this browser & sign out completely"):
            _release_submission_freeze()
            st.session_state.pop("branch_filter_state", None)
            st.session_state.pop("_is_owner_dev_session", None)
            st.session_state.pending_cookie_delete = True
            st.session_state.pending_full_signout = True
            st.session_state.logged_in = False
            st.session_state.user_name = None
            st.session_state.user_role = None
            st.session_state.user_email = None
            if "perimeter_verified_email" in st.session_state:
                del st.session_state["perimeter_verified_email"]
            _invalidate_all_data_caches()
            if is_google_oauth_configured():
                st.logout()
            else:
                st.rerun()

    role = st.session_state.user_role

    render_smart_notifications(role, st.session_state.user_name)

    if role == "Employee":
        employee_dashboard()
    elif role == "Admin":
        admin_dashboard()
    elif role == "Co-Admin":
        coadmin_dashboard()
    elif role == "Accounts":
        accounts_dashboard()
    elif role == "Branch Manager":
        branch_manager_dashboard()
    elif role == "Auditor":
        auditor_dashboard()
    else:
        st.title("Dashboard")
        st.error("Welcome! Role not recognized.")


# --- MAIN APP FLOW ---

def run_app():
    if not st.session_state.logged_in:
        # Attempt auto-login via cookie
        auth_token = cookie_manager.get("auth_token")
        if auth_token and '|' in str(auth_token):
            saved_user, saved_pin = str(auth_token).split('|', 1)
            users_df = load_users_data()
            if users_df is not None and not users_df.empty:
                user_row = users_df[users_df["Name"] == saved_user]
                if not user_row.empty:
                    stored_pin = str(user_row.iloc[0]["PIN"]).split('.')[0].zfill(4)
                    if str(saved_pin) == stored_pin:
                        # Verify perimeter match if OAuth is active
                        saved_email = str(user_row.iloc[0].get("Email", "")).strip().lower()
                        if is_google_oauth_configured():
                            if getattr(st.user, "is_logged_in", False) and str(st.user.email).strip().lower() == saved_email:
                                st.session_state.logged_in = True
                                st.session_state.user_name = saved_user
                                st.session_state.user_role = str(user_row.iloc[0]["Role"]).strip()
                                st.session_state.user_email = saved_email
                                st.rerun()
                        else:
                            st.session_state.logged_in = True
                            st.session_state.user_name = saved_user
                            st.session_state.user_role = str(user_row.iloc[0]["Role"]).strip()
                            st.session_state.user_email = saved_email
                            st.rerun()

    if not st.session_state.logged_in:
        login_screen()
    else:
        main_dashboard()


if __name__ == "__main__":
    run_app()
