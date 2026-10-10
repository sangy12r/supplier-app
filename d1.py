import requests, streamlit as st

def q(sql, params=None):
    s = st.secrets
    url = f"https://api.cloudflare.com/client/v4/accounts/{s['CF_ACCOUNT_ID']}/d1/database/{s['CF_D1_ID']}/query"
    r = requests.post(url, headers={"Authorization": f"Bearer {s['CF_API_TOKEN']}"},
                      json={"sql": sql, "params": params or []}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(d.get("errors"))
    return d["result"][0].get("results", [])
