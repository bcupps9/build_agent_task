
# milestone1_sitesourcing_langgraph_real.py
"""
Option B — Site Sourcing (Industrial)
Real-API version using LangGraph + LangChain
"""


from __future__ import annotations
import random
import threading
import argparse
import json
import math
import os
from dotenv import load_dotenv
import time
from typing import Any, Dict, List, Tuple, TypedDict

import requests
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

# -----------------------------
# Config / helpers
# -----------------------------
load_dotenv()  # Loads from .env file

NOMINATIM_UA = os.environ.get("NOMINATIM_UA", "site-sourcing-langgraph/1.0 (contact: robertcupps19@gmail.com)")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]
# Keep a small client-side throttle so parallel nodes don’t hammer the same host
OVERPASS_SEM = threading.BoundedSemaphore(2)

def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    R = 6371.0088
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(h))

# -----------------------------
# External API wrappers (real) - tools
# -----------------------------

def geocode_nominatim(query: str) -> Tuple[float, float]:
    url = "https://nominatim.openstreetmap.org/search"
    resp = requests.get(url, params={"q": query, "format": "json", "limit": 1}, headers={"User-Agent": NOMINATIM_UA}, timeout=30)
    resp.raise_for_status()
    js = resp.json()
    if not js:
        raise ValueError(f"No geocoding results for '{query}'")
    return float(js[0]["lat"]), float(js[0]["lon"])

def overpass(query: str, tries: int = 4, base_timeout: int = 60) -> Dict[str, Any]:
    last_err = None
    # Shuffle endpoints each call to spread load
    endpoints = OVERPASS_ENDPOINTS[:]
    random.shuffle(endpoints)

    for attempt in range(tries):
        url = endpoints[attempt % len(endpoints)]
        # jittered backoff: 0s, ~0.6s, ~1.4s, ~3s...
        backoff = 0.3 * (2 ** attempt) * (0.75 + random.random() * 0.5)

        try:
            with OVERPASS_SEM:
                resp = requests.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": NOMINATIM_UA},
                    timeout=base_timeout,
                )
            if resp.status_code in (429, 502, 503, 504):
                # transient server throttling/errors
                last_err = requests.HTTPError(f"{resp.status_code} from {url}")
            else:
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_err = e

        time.sleep(backoff)

    # Final attempt: try to slightly reduce the server load by lowering 'out' limit if present
    slim_q = query.replace("out center 60;", "out center 30;").replace("out center 100;", "out center 60;")
    try:
        with OVERPASS_SEM:
            resp = requests.post(
                endpoints[0],
                data={"data": slim_q},
                headers={"User-Agent": NOMINATIM_UA},
                timeout=base_timeout,
            )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        pass

    raise last_err or RuntimeError("Overpass request failed after retries")


def fcc_county_fips(lat: float, lon: float) -> Dict[str, Any]:
    url = "https://geo.fcc.gov/api/census/block/find"
    resp = requests.get(url, params={"latitude": lat, "longitude": lon, "format": "json", "showall": True}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def bls_unemployment_series(county_fips: str) -> float | None:
    if len(county_fips) != 5:
        return None
    state = county_fips[:2]
    county = county_fips[2:]
    series = f"LAUCN{state}{county}000000006A"
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    payload = {"seriesid": [series]}
    api_key = os.environ.get("BLS_API_KEY")
    if api_key:
        payload["registrationkey"] = api_key
    resp = requests.post(url, json=payload, timeout=60)
    if resp.status_code == 429:
        return None
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "REQUEST_SUCCEEDED":
        return None
    series_data = data["Results"]["series"][0]["data"]
    if not series_data:
        return None
    latest = series_data[0]
    try:
        return float(latest["value"])
    except Exception:
        return None

# -----------------------------
# LangGraph workflow state
# -----------------------------

class SGState(TypedDict, total=False):
    prompt: str
    location: str
    center: Tuple[float, float]
    candidates: List[Tuple[float, float]]
    zoning: Dict[str, Any]
    infra: Dict[str, Any]
    labor: Dict[str, Any]
    report_md: str
    # logs: List[str]

def log(state: SGState, msg: str):
    print(msg, flush=True)

# -----------------------------
# Nodes
# -----------------------------

def input_parser(state: SGState) -> SGState:
    log(state, "📥 [Input Parser] Reading prompt & geocoding location...")
    text = state.get("prompt", "").strip()
    loc = state.get("location")
    if not loc:
        if "near" in text.lower():
            loc = text.split("near", 1)[1]
        else:
            loc = text
        loc = loc.strip().strip(".")
    lat, lon = geocode_nominatim(loc)
    time.sleep(1.0)
    log(state, f"  Geocoded '{loc}' to ({lat}, {lon})")
    return {"location": loc, "center": (lat, lon)}

def ideation_node(state: SGState) -> SGState:
    lat, lon = state["center"]
    log(state, "✨ [Ideation] Finding industrial landuse polygons via Overpass...")
    q = f"""
    [out:json][timeout:60];
    (
      way(around:20000,{lat},{lon})["landuse"="industrial"];
      relation(around:20000,{lat},{lon})["landuse"="industrial"];
    );
    out center 30;
    """
    js = overpass(q)
    centers = []
    for el in js.get("elements", []):
        if "center" in el:
            centers.append( (el["center"]["lat"], el["center"]["lon"]) )
    uniq = []
    for c in centers:
        if all(haversine_km(c, u) > 0.5 for u in uniq):
            uniq.append(c)
        if len(uniq) >= 10:
            break
    if not uniq:
        raise RuntimeError("No industrial sites found within 20km. Try another area.")
    for i, c in enumerate(uniq, 1):
        log(None, f"→ Candidate {i}: {c}")   # or simply log(_, msg)
    time.sleep(1.0)
    return {"candidates": uniq}

def zoning_ranker(state: SGState) -> SGState:
    log(state, "🏷️ [Zoning Ranker] Checking industrial compatibility and highway proximity...")
    results = {}
    for c in state["candidates"]:
        q = f"""
        [out:json][timeout:60];
        way(around:15000,{c[0]},{c[1]})["highway"~"motorway|trunk"];
        out center 60;
        """
        js = overpass(q)
        nearest = None
        for w in js.get("elements", []):
            if "center" in w:
                d = haversine_km((w["center"]["lat"], w["center"]["lon"]), c)
                nearest = d if (nearest is None or d < nearest) else nearest

        # continuous proximity score on [0,1], exponential decay with 8km length scale
        # nearer = much higher, >25km ~ 0
        if nearest is None:
            prox = 0.0
            nearest_val = 99.0
        else:
            nearest_val = round(nearest, 2)
            prox = math.exp(-(nearest / 8.0))          # 0km→1.0, 8km→~0.37, 16km→~0.14

        compat = True  # ideation filtered landuse=industrial
        compat_score = 1.0 if compat else 0.0

        # weight industrial compatibility and proximity (tunable)
        score = round(0.4 * compat_score + 0.6 * prox, 3)

        results[str(c)] = {
            "compatible": compat,
            "nearest_motorway_km": nearest_val,
            "proximity_score": round(prox, 3),
            "score": score
        }
        log(None, f"  {c} → motorway {nearest_val} km | prox={round(prox,3)} | score={score}")
    return {"zoning": results}
    
def infrastructure_ranker(state: SGState) -> SGState:
    log(state, "⚡ [Infrastructure Ranker] Weighted log-scale of nearby infra features (batch-normalized)...")
    weights = {
        "generator": 4.0, "substation": 3.0, "pipeline": 2.0,
        "mast": 1.0, "communications_tower": 1.0, "monitoring_station": 1.0, "water_tower": 1.0
    }

    wsums: Dict[str, float] = {}
    details: Dict[str, Dict[str, Any]] = {}

    for c in state["candidates"]:
        lat, lon = c
        q = f"""
        [out:json][timeout:60];
        (
          node(around:10000,{lat},{lon})["power"~"substation|generator"];
          way(around:10000,{lat},{lon})["power"~"substation|generator"];
          node(around:10000,{lat},{lon})["man_made"="water_tower"];
          way(around:10000,{lat},{lon})["man_made"="water_tower"];
          node(around:10000,{lat},{lon})["man_made"~"mast|communications_tower|monitoring_station"];
          way(around:10000,{lat},{lon})["man_made"~"mast|communications_tower|monitoring_station"];
          way(around:10000,{lat},{lon})["pipeline"];
        );
        out tags center;
        """
        js = overpass(q)

        wsum = 0.0
        for el in js.get("elements", []):
            tags = el.get("tags", {})
            if not tags:
                continue
            if tags.get("power") == "generator":
                wsum += weights["generator"]
            elif tags.get("power") == "substation":
                wsum += weights["substation"]
            elif "pipeline" in tags:
                wsum += weights["pipeline"]
            else:
                mm = tags.get("man_made")
                if mm in weights:
                    wsum += weights[mm]

        key = str(c)
        wsums[key] = wsum
        details[key] = {"weighted_sum": round(wsum, 1)}

    # ------- batch normalization (log1p, relative to 95th percentile) -------
    vals = sorted(wsums.values())
    if vals:
        p95 = vals[int(0.95 * (len(vals) - 1))] if len(vals) > 1 else (vals[0] or 1.0)
        denom = max(p95, 1.0)  # avoid div/0
    else:
        denom = 1.0

    results: Dict[str, Any] = {}
    for key, wsum in wsums.items():
        score = math.log1p(wsum) / math.log1p(denom)
        score = max(0.0, min(1.0, score))
        score = round(score, 3)
        results[key] = {**details[key], "score": score}

    # logs
    for c in state["candidates"]:
        key = str(c)
        log(None, f"  {c} → infra weighted={results[key]['weighted_sum']} | score={results[key]['score']}")

    return {"infra": results}



def labor_market_ranker(state: SGState) -> SGState:
    log(state, "👷 [Labor Market Ranker] Combining unemployment and proximity-to-center...")
    center = state["center"]
    results = {}
    # For normalization of distance, we’ll score 0km→1.0 and 40km→~0.135 (exp decay).
    for c in state["candidates"]:
        js = fcc_county_fips(c[0], c[1])
        county_fips = js["County"]["FIPS"]
        county_name = js["County"]["name"]
        rate = bls_unemployment_series(county_fips)

        # unemployment score: center around 5% (neutral ~0.5), nicer spread
        # 2% → ~0.875, 5% → 0.5, 10% → ~0.0 (clipped)
        if rate is None:
            unemp_score = 0.5  # impute neutral instead of blanket ties
        else:
            unemp_score = 1.0 - (max(0.0, rate - 2.0) / 8.0)  # >10% floors near 0
            unemp_score = max(0.0, min(1.0, unemp_score))

        # proximity to workforce: closer to city center → larger available labor pool (proxy)
        d_center = haversine_km(c, center)
        prox_work = math.exp(-(d_center / 20.0))  # 0km→1.0, 20km→0.37, 40km→0.14

        # combine (tunable): unemployment 60%, proximity 40%
        score = round(0.6 * unemp_score + 0.4 * prox_work, 3)

        results[str(c)] = {
            "county": county_name, "county_fips": county_fips,
            "unemployment_rate": rate, "unemp_score": round(unemp_score, 3),
            "distance_to_center_km": round(d_center, 2), "workforce_prox": round(prox_work, 3),
            "score": score
        }
        log(None, f"  {c} → {county_name} ({county_fips}) unemp={rate}% unemp_s={round(unemp_score,3)} "
                  f"d_center={round(d_center,2)}km prox={round(prox_work,3)} | score={score}")
    return {"labor": results}

def report_aggregator(state: SGState) -> SGState:
    log(state, "🧾 [Report Aggregator] Combining scores and drafting report...")
    combined = []
    for c in state["candidates"]:
        key = str(c)
        z = state["zoning"][key]["score"]
        i = state["infra"][key]["score"]
        l = state["labor"][key]["score"]
        
        
        total = 0.4*z + 0.35*i + 0.25*l         # keep [0,1]
        disp = round(100.0 * total, 2)           # pretty print
        combined.append((c, total, disp))
    combined.sort(key=lambda x: x[1], reverse=True)

    context = {
        "location": state["location"],
        "candidates": [
            {
                "coords": list(map(float, map(str, c))),
                "zoning": state["zoning"][str(c)],
                "infra": state["infra"][str(c)],
                "labor": state["labor"][str(c)],
                "score": s,
                "score_display": disp
            }
            for c, s, disp in combined
        ],
    }

    report_md = None
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
        prompt = (
            "You are drafting a concise due-diligence note for industrial site selection. "
            "Use only the provided JSON facts; do not fabricate. "
            "Rank sites by score and explain briefly (3–5 bullets per site). "
            "Return GitHub-flavored Markdown.\n\n"
            f"FACTS JSON:\n```json\n{json.dumps(context, indent=2)}\n```"
        )
        msg = llm.invoke([HumanMessage(content=prompt)])
        report_md = msg.content
    else:
        lines = [f"# Site Sourcing Report — {state['location']}", ""]
        for i, (c, s, disp) in enumerate(combined, 1):
            z = state["zoning"][str(c)]
            i_det = state["infra"][str(c)]
            l = state["labor"][str(c)]
            lines += [
                f"**{i}. {tuple(c)} — Score {disp}/100.00**",
                f"- Zoning/Access: motorway {z['nearest_motorway_km']} km; compatible industrial = {z['compatible']}",
                f"- Infrastructure (10km radius): {i_det['infra_objects_8km']} relevant OSM features" if 'infra_objects_8km' in i_det else f"- Infrastructure score: {i_det['score']}",
                f"- Labor: county {l['county']} ({l['county_fips']}), unemployment {l['unemployment_rate']}%",
                ""
            ]
        report_md = "\n".join(lines)

    print("\n" + report_md + "\n", flush=True)
    return {"report_md": report_md}

# -----------------------------
# DAG construction
# -----------------------------

def build_graph():
    g = StateGraph(SGState)
    g.add_node("input_parser", input_parser)
    g.add_node("ideation", ideation_node)
    g.add_node("zoning_ranker", zoning_ranker)
    g.add_node("infra_ranker", infrastructure_ranker)
    g.add_node("labor_ranker", labor_market_ranker)
    g.add_node("report", report_aggregator)

    g.set_entry_point("input_parser")
    g.add_edge("input_parser", "ideation")
    g.add_edge("ideation", "zoning_ranker")
    g.add_edge("ideation", "infra_ranker")
    g.add_edge("ideation", "labor_ranker")
    g.add_edge("zoning_ranker", "report")
    g.add_edge("infra_ranker", "report")
    g.add_edge("labor_ranker", "report")
    g.add_edge("report", END)
    return g.compile()

# -----------------------------
# Main
# -----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=str, default="Find industrial sites near Phoenix, AZ")
    parser.add_argument("--location", type=str, default=None)  # ✅ add this line
    args = parser.parse_args()

    app = build_graph()
    init: SGState = {"prompt": args.prompt}
    if args.location:
        init["location"] = args.location  # ✅ pass location into initial state

    print("=== Executing Real-API LangGraph ===", flush=True)
    final_state = app.invoke(init)
    print("=== DONE ===", flush=True)
    return final_state

if __name__ == "__main__":
    main()
