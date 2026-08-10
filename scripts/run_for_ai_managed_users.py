"""
Runs the same paper-trading logic as trade_manager.py (enter today's
signals, then manage exits), but once per trader who has selected
AI-managed mode and connected their own Alpaca paper account - instead of
the single hardcoded account the CLI usage manages.

Each user gets their own state/log files under data/users/<user_id>/, so
one trader's positions can never collide with another's or with the site's
own demo account (data/positions_state.json, trades.md stay untouched by
this script entirely).

Requires these GitHub Actions secrets:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY   (server-side only - bypasses RLS by design,
                                  since alpaca_connections has no RLS policies
                                  for any other role)
    CREDENTIAL_ENCRYPTION_KEY   (same key api/alpaca-connection.js encrypts
                                  with - base64, 32 bytes)

If any of these aren't set, this script is a no-op: AI-managed mode is
optional infrastructure layered on top of the core scanner/demo-account
pipeline, not a required part of it.
"""

import base64
import os
import sys
import time
import traceback

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import trade_manager

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
USERS_DIR = os.path.join(DATA_DIR, "users")


def decrypt(blob_b64, key_b64):
    key = base64.b64decode(key_b64)
    blob = base64.b64decode(blob_b64)
    iv, ciphertext_and_tag = blob[:12], blob[12:]
    return AESGCM(key).decrypt(iv, ciphertext_and_tag, None).decode("utf-8")


def fetch_ai_managed_users(supabase_url, service_key):
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    r = requests.get(
        f"{supabase_url}/rest/v1/profiles",
        params={"mode": "eq.ai_managed", "select": "id,email"},
        headers=headers, timeout=20,
    )
    r.raise_for_status()
    profiles = {p["id"]: p["email"] for p in r.json()}
    if not profiles:
        return []

    r = requests.get(
        f"{supabase_url}/rest/v1/alpaca_connections",
        params={"select": "user_id,encrypted_api_key,encrypted_api_secret"},
        headers=headers, timeout=20,
    )
    r.raise_for_status()
    connections = {c["user_id"]: c for c in r.json()}

    users = []
    for user_id, email in profiles.items():
        conn = connections.get(user_id)
        if conn:
            users.append({"id": user_id, "email": email, "connection": conn})
    return users


def process_user(user, encryption_key, latest_signals_path):
    api_key = decrypt(user["connection"]["encrypted_api_key"], encryption_key)
    api_secret = decrypt(user["connection"]["encrypted_api_secret"], encryption_key)

    user_dir = os.path.join(USERS_DIR, user["id"])
    os.makedirs(user_dir, exist_ok=True)
    trade_manager.configure(
        api_key=api_key,
        api_secret=api_secret,
        state_path=os.path.join(user_dir, "positions_state.json"),
        trades_md_path=os.path.join(user_dir, "trades.md"),
        trades_json_path=os.path.join(user_dir, "trades.json"),
    )

    trade_manager.manage_exits()
    trade_manager.enter_new_signals(latest_signals_path)
    # manage_exits() only places a position's first stop order once it sees
    # stop_order_id still null - true right after a same-day entry. Give the
    # paper-market fill a few seconds, then check again, mirroring the same
    # enter-then-place-stops pattern the main daily job uses for the demo
    # account, so a fresh per-user entry isn't left unprotected until
    # tomorrow's run.
    time.sleep(15)
    trade_manager.manage_exits()


def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    encryption_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not (supabase_url and service_key and encryption_key):
        print("AI-managed trading not configured (missing Supabase/encryption secrets) - skipping.")
        return

    latest_signals_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_DIR, "latest.json")

    users = fetch_ai_managed_users(supabase_url, service_key)
    print(f"Found {len(users)} AI-managed trader(s) with a connected Alpaca account.")

    for user in users:
        try:
            process_user(user, encryption_key, latest_signals_path)
            print(f"  {user['email']}: ok")
        except Exception as e:
            # One trader's bad credentials or a transient API error must
            # never take down every other trader's run.
            print(f"  [warn] {user['email']}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
