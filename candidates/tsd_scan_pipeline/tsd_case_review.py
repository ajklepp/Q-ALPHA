"""
Peak Hour — post-signal Case Review (veto-capable).

1H continuation_score nominates; case review decides ENTER / WAIT / REJECT.
Deep-swing scores stay preferential; momentum context + structure must agree.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests

from tsd_scan_pipeline.tsd_attention import ROOM_TO_HIGH_MIN

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"
CASE_MAX_TOKENS = 450
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")

# Structure thresholds for deterministic room class / vetoes
WRECKAGE_RANGE_MIN = 1.50  # 150% 20d range
WRECKAGE_ROOM_MIN = 0.50  # 50% under 20d high after expansion
CLIMAX_VOL_MIN = 3.0  # vol_ratio_20 extreme (when still elevated)
DEAD_VOL_MAX = 0.15  # near-dead tape after a spike


def classify_room_class(row: dict[str, Any]) -> str:
    """
    CONSTRUCTIVE_ROOM | WRECKAGE_ROOM | TIGHT | UNKNOWN

    Preserves 'room' as a feature: wreckage is dump-under-spike, not base room.
    """
    try:
        room = float(row.get("dist_20d_high_pct") or 0.0)
    except (TypeError, ValueError):
        room = 0.0
    try:
        range_pct = float(
            row.get("htf_range_20d_pct") or row.get("range_20d_pct") or 0.0
        )
    except (TypeError, ValueError):
        range_pct = 0.0
    try:
        vol = float(row.get("vol_ratio_20") or 0.0)
    except (TypeError, ValueError):
        vol = 0.0
    try:
        bounce = float(row.get("dist_20d_low_bounce") or 0.0)
    except (TypeError, ValueError):
        bounce = 0.0

    if room < 0:
        return "TIGHT"
    if range_pct >= WRECKAGE_RANGE_MIN and room >= WRECKAGE_ROOM_MIN:
        return "WRECKAGE_ROOM"
    if range_pct >= 1.0 and room >= 0.35 and (vol <= DEAD_VOL_MAX or bounce <= 0.02):
        return "WRECKAGE_ROOM"
    if room >= ROOM_TO_HIGH_MIN and range_pct < WRECKAGE_RANGE_MIN:
        return "CONSTRUCTIVE_ROOM"
    if room < 0.05:
        return "TIGHT"
    return "UNKNOWN"


def _toxic_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if int(row.get("dilution_flag") or 0) == 1:
        flags.append("dilution")
    if int(row.get("distress_flag") or 0) == 1:
        flags.append("distress")
    if row.get("guidance_cut") is True or str(row.get("outlook") or "").lower() in (
        "lowered",
        "withdrawn",
    ):
        flags.append("guidance_cut")
    deep = row.get("deep_catalyst") if isinstance(row.get("deep_catalyst"), dict) else {}
    for rf in deep.get("risk_flags") or []:
        s = str(rf).lower()
        if s in ("dilution", "distress", "guidance_cut", "offering") and s not in flags:
            flags.append(s)
    # Situational news taxonomy (pt_cut, downgrade, offering, …)
    try:
        from tsd_scan_pipeline.tsd_news_schema import (
            assess_news_situation,
            collect_row_headlines,
        )

        sit = assess_news_situation(collect_row_headlines(row))
        row["_news_situation"] = sit  # stash for dossier / ledger
        for f in sit.get("toxic_news_flags") or []:
            if f not in flags:
                flags.append(str(f))
    except Exception:
        pass
    return flags


def _thin_deep_contradiction(row: dict[str, Any]) -> bool:
    """FGI-style: thin upgrade narrative vs deep 'quiet tape' with no catalyst."""
    outlook = str(row.get("outlook") or "").lower()
    thin_hot = outlook in ("raised", "maintained") or int(row.get("catalyst_tier") or 0) >= 2
    deep = row.get("deep_catalyst") if isinstance(row.get("deep_catalyst"), dict) else {}
    deep_line = str(
        row.get("deep_summary_line") or deep.get("deep_summary_line") or deep.get("narrative") or ""
    ).lower()
    quiet = (
        "quiet tape" in deep_line
        or "no catalyst" in deep_line
        or (
            int(deep.get("deep_ok") or deep.get("fresh_catalyst") or 0) == 0
            and int(row.get("fresh_catalyst") or 0) == 0
            and float(row.get("headline_count") or row.get("news_velocity_24h") or 0) == 0
        )
    )
    # Contradiction: thin claims catalyst heat while deep says quiet / empty
    if thin_hot and quiet and float(row.get("news_velocity_24h") or 0) <= 0:
        # thin tags alone without headlines
        return True
    if thin_hot and "quiet tape" in deep_line:
        return True
    return False


def build_case_dossier(row: dict[str, Any]) -> dict[str, Any]:
    """Deterministic evidence pack for fusion / ledger (no LLM)."""
    room_class = classify_room_class(row)
    toxic = _toxic_flags(row)
    sit = row.get("_news_situation") if isinstance(row.get("_news_situation"), dict) else {}
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "continuation_score": float(row.get("continuation_score") or 0.0),
        "scan_score": float(row.get("scan_score") or 0.0),
        "bar_state": row.get("bar_state"),
        "htf_score": float(row.get("htf_score") or 0.0),
        "htf_range_20d_pct": float(row.get("htf_range_20d_pct") or 0.0),
        "dist_20d_high_pct": float(row.get("dist_20d_high_pct") or 0.0),
        "dist_20d_low_bounce": float(row.get("dist_20d_low_bounce") or 0.0),
        "vol_ratio_20": float(row.get("vol_ratio_20") or 0.0),
        "room_class": room_class,
        "on_gainers": bool(row.get("on_gainers")),
        "recent_leaderboard": bool(row.get("recent_leaderboard")),
        "tws_popular": bool(row.get("tws_popular")),
        "tradable_popular": bool(row.get("tradable_popular")),
        "buzz_accel": bool(row.get("buzz_accel")),
        "momentum_context": bool(row.get("momentum_context")),
        "attention_reasons": list(row.get("attention_reasons") or []),
        "st_msg_24h": float(row.get("st_msg_24h") or 0.0),
        "st_bull_ratio": float(row.get("st_bull_ratio") or 0.5),
        "news_velocity_24h": float(row.get("news_velocity_24h") or 0.0),
        "print": row.get("print"),
        "outlook": row.get("outlook"),
        "deep_summary_line": row.get("deep_summary_line") or "",
        "toxic_flags": toxic,
        "thin_deep_contradiction": _thin_deep_contradiction(row),
        "news_situation": sit,
        "news_score_delta": int(sit.get("score_delta") or 0),
    }


def deterministic_case_verdict(dossier: dict[str, Any]) -> dict[str, Any] | None:
    """
    Hard structural / toxic decisions without LLM.

    Returns a full case dict if decided, else None (LLM may refine).
    """
    sym = dossier["symbol"]
    risks = list(dossier.get("toxic_flags") or [])
    evidence: list[str] = []

    if dossier.get("room_class") == "WRECKAGE_ROOM":
        evidence.append(
            f"wreckage_room range={dossier.get('htf_range_20d_pct')} "
            f"off_high={dossier.get('dist_20d_high_pct')}"
        )
        return _case(
            sym,
            "REJECT",
            0.9,
            dossier,
            structure_note="Post-expansion dump under 20d high — not constructive room",
            sentiment_note="",
            risks=risks + ["wreckage_room"],
            evidence=evidence,
            source="rules",
        )

    if risks:
        evidence.append(f"toxic={','.join(risks)}")
        sit = dossier.get("news_situation") if isinstance(dossier.get("news_situation"), dict) else {}
        worst = sit.get("worst") if isinstance(sit.get("worst"), dict) else {}
        note = "Toxic catalyst / situational news flags"
        if worst.get("label"):
            note = f"Toxic news: {worst.get('label')} (same-day p50={worst.get('same_day_ret_p50')})"
        return _case(
            sym,
            "REJECT",
            0.85,
            dossier,
            structure_note=note,
            sentiment_note="",
            risks=risks,
            evidence=evidence,
            source="rules",
        )

    if dossier.get("thin_deep_contradiction"):
        evidence.append("thin_vs_deep_contradiction")
        return _case(
            sym,
            "REJECT",
            0.8,
            dossier,
            structure_note="Thin catalyst tags contradict deep quiet-tape brief",
            sentiment_note="conflicting AI briefs",
            risks=["catalyst_contradiction"],
            evidence=evidence,
            source="rules",
        )

    # High deep-swing score without momentum → WAIT (do not auto-buy quiet tape)
    cont = float(dossier.get("continuation_score") or 0.0)
    if cont >= 70.0 and not dossier.get("momentum_context"):
        evidence.append("high_score_no_momentum_context")
        return _case(
            sym,
            "WAIT",
            0.7,
            dossier,
            structure_note="Deep-swing score without trend/momentum confirmation",
            sentiment_note="momentum_context=false",
            risks=[],
            evidence=evidence,
            source="rules",
        )

    # Strong momentum + constructive room + early/deep-swing scan → ENTER without LLM
    scan = float(dossier.get("scan_score") or 99.0)
    popular = bool(
        dossier.get("tradable_popular")
        or dossier.get("recent_leaderboard")
        or dossier.get("tws_popular")
        or dossier.get("on_gainers")
        or dossier.get("buzz_accel")
    )
    if (
        dossier.get("room_class") == "CONSTRUCTIVE_ROOM"
        and dossier.get("momentum_context")
        and scan <= 55.0
        and popular
    ):
        evidence.append("constructive_room+momentum+popular+early_scan")
        return _case(
            sym,
            "ENTER",
            0.72,
            dossier,
            structure_note="Constructive room with tradable-popularity confirmation on early swing",
            sentiment_note=(
                f"popular={dossier.get('tradable_popular')} "
                f"recent_lb={dossier.get('recent_leaderboard')} "
                f"tws={dossier.get('tws_popular')} "
                f"today_gainer={dossier.get('on_gainers')} "
                f"news_Δ={dossier.get('news_score_delta')}"
            ),
            risks=[],
            evidence=evidence,
            source="rules_momentum",
        )

    return None


def _case(
    symbol: str,
    verdict: str,
    confidence: float,
    dossier: dict[str, Any],
    *,
    structure_note: str,
    sentiment_note: str,
    risks: list[str],
    evidence: list[str],
    source: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "verdict": verdict,
        "confidence": round(float(confidence), 3),
        "room_class": dossier.get("room_class"),
        "structure_note": structure_note,
        "sentiment_note": sentiment_note,
        "risks": risks,
        "evidence": evidence,
        "momentum_context": bool(dossier.get("momentum_context")),
        "on_gainers": bool(dossier.get("on_gainers")),
        "recent_leaderboard": bool(dossier.get("recent_leaderboard")),
        "tws_popular": bool(dossier.get("tws_popular")),
        "tradable_popular": bool(dossier.get("tradable_popular")),
        "buzz_accel": bool(dossier.get("buzz_accel")),
        "continuation_score": dossier.get("continuation_score"),
        "attention_reasons": dossier.get("attention_reasons") or [],
        "source": source,
        "dossier": dossier,
    }


def _openrouter_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


def _parse_case_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = _JSON_BLOCK.search(text)
    raw = m.group(0) if m else text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").upper()
    if verdict not in ("ENTER", "WAIT", "REJECT"):
        return None
    data["verdict"] = verdict
    try:
        data["confidence"] = float(data.get("confidence") or 0.5)
    except (TypeError, ValueError):
        data["confidence"] = 0.5
    return data


def fusion_case_llm(
    dossier: dict[str, Any],
    *,
    use_web_search: bool = True,
) -> dict[str, Any] | None:
    """
    OpenRouter fusion with optional web search for live chatter / leaderboard context.
    """
    api_key = _openrouter_key()
    if not api_key:
        return None

    model = (os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL).strip()
    sym = dossier["symbol"]
    prompt = f"""You are the Peak Hour case reviewer for a LONG-ONLY momentum system.
Decide ENTER, WAIT, or REJECT for {sym} using ONLY the dossier plus any web search you run.
Deep-swing / early 1H scores nominate; you decide. Prefer ENTER only when structure is constructive AND momentum/trend/chatter support continuation.
REJECT wreckage after parabolic dumps, toxic dilution/distress, or contradictory catalyst briefs.
WAIT if setup is OK but momentum/chatter is missing.

DOSSIER JSON:
{json.dumps(dossier, indent=2, default=str)}

Return ONLY JSON:
{{"verdict":"ENTER|WAIT|REJECT","confidence":0.0-1.0,"room_class":"...","structure_note":"...","sentiment_note":"...","risks":[],"evidence":[]}}
"""
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": CASE_MAX_TOKENS,
        "temperature": 0.1,
    }
    if use_web_search:
        # OpenRouter server tool — real web chatter for leaderboard names
        body["tools"] = [{
            "type": "openrouter:web_search",
            "parameters": {
                "engine": "perplexity",
                "max_results": 5,
            },
        }]

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ajklepp/Q-ALPHA",
            },
            json=body,
            timeout=45,
        )
        if resp.status_code == 400 and use_web_search:
            # Retry without tools if provider rejects tool schema
            body.pop("tools", None)
            resp = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/ajklepp/Q-ALPHA",
                },
                json=body,
                timeout=45,
            )
        if resp.status_code == 429:
            print(f"  case LLM rate-limited {sym}")
            return None
        resp.raise_for_status()
        msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
        content = str(msg.get("content") or "")
        parsed = _parse_case_json(content)
        if not parsed:
            print(f"  case LLM bad JSON {sym}: {content[:120]!r}")
            return None
        return _case(
            sym,
            parsed["verdict"],
            float(parsed.get("confidence") or 0.5),
            dossier,
            structure_note=str(parsed.get("structure_note") or "")[:240],
            sentiment_note=str(parsed.get("sentiment_note") or "")[:240],
            risks=[str(x) for x in (parsed.get("risks") or [])][:8],
            evidence=[str(x) for x in (parsed.get("evidence") or [])][:10],
            source="llm",
        )
    except Exception as exc:
        print(f"  case LLM fail {sym}: {exc}")
        return None


# Hard REJECT risks that must never soften even for CONSTRUCTIVE leaders.
_HARD_REJECT_RISKS = frozenset({
    "wreckage_room",
    "wreckage_override",
    "dilution",
    "distress",
    "guidance_cut",
    "news_veto",
})


def _soften_leader_reject(case: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    """
    Autopsy P1: CONSTRUCTIVE_ROOM + momentum leaders should not hard-REJECT
    on soft contradictions — WAIT so rank/cap can still consider them later.
    Keep toxic / wreckage REJECT intact.
    """
    if str(case.get("verdict") or "").upper() != "REJECT":
        return case
    if dossier.get("room_class") != "CONSTRUCTIVE_ROOM":
        return case
    if not dossier.get("momentum_context"):
        return case
    risks = {str(r) for r in (case.get("risks") or [])}
    if risks & _HARD_REJECT_RISKS:
        return case
    if dossier.get("room_class") == "WRECKAGE_ROOM":
        return case
    out = dict(case)
    out["verdict"] = "WAIT"
    out["confidence"] = min(float(out.get("confidence") or 0.7), 0.65)
    out["evidence"] = list(out.get("evidence") or []) + ["leader_reject_softened_to_wait"]
    out["structure_note"] = (
        f"{out.get('structure_note') or ''} | softened REJECT→WAIT "
        "(constructive+momentum leader)"
    ).strip(" |")
    out["source"] = f"{out.get('source') or 'rules'}_leader_soften"
    return out


def alert_case_rank_disagreement(
    reviewed: list[dict[str, Any]],
    *,
    notify: bool = True,
) -> dict[str, Any] | None:
    """
    Autopsy P1: log/alert when top continuation is CASE_REJECT while a
    lower-ranked name is ENTER — rank and case gate disagree.
    """
    if not reviewed:
        return None
    by_cont = sorted(
        reviewed,
        key=lambda r: -float(r.get("continuation_score") or 0),
    )
    top = by_cont[0]
    top_v = str(
        top.get("case_verdict")
        or (top.get("case_review") or {}).get("verdict")
        or ""
    ).upper()
    if top_v != "REJECT":
        return None
    lower_enter = next(
        (
            r for r in by_cont[1:]
            if str(
                r.get("case_verdict")
                or (r.get("case_review") or {}).get("verdict")
                or ""
            ).upper()
            == "ENTER"
        ),
        None,
    )
    if lower_enter is None:
        return None
    payload = {
        "top_symbol": str(top.get("symbol") or "").upper(),
        "top_continuation": top.get("continuation_score"),
        "top_verdict": top_v,
        "enter_symbol": str(lower_enter.get("symbol") or "").upper(),
        "enter_continuation": lower_enter.get("continuation_score"),
    }
    msg = (
        f"CASE/rank disagree: top={payload['top_symbol']} REJECT "
        f"(cont={payload['top_continuation']}) but "
        f"{payload['enter_symbol']} ENTER "
        f"(cont={payload['enter_continuation']})"
    )
    print(f"  ALERT {msg}")
    if notify:
        try:
            from tsd_scan_pipeline.tsd_notify import notify_tsd

            notify_tsd(f"Peak Hour {msg}")
        except Exception:
            pass
    return payload


def review_case(
    row: dict[str, Any],
    *,
    use_web_search: bool = True,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """
    Full case review for one attention-pool row.

    Rules fire first (wreckage / toxic / contradiction / no-momentum WAIT).
    Otherwise LLM fusion; on LLM failure → WAIT (fail-closed, never BUY on score alone).
    Soften soft REJECT→WAIT for constructive+momentum leaders (autopsy P1).
    """
    dossier = build_case_dossier(row)
    ruled = deterministic_case_verdict(dossier)
    if ruled is not None:
        # Still allow LLM to upgrade WAIT→ENTER when momentum is strong and web agrees
        if (
            allow_llm
            and ruled["verdict"] == "WAIT"
            and dossier.get("momentum_context")
            and dossier.get("room_class") == "CONSTRUCTIVE_ROOM"
        ):
            llm = fusion_case_llm(dossier, use_web_search=use_web_search)
            if llm and llm["verdict"] == "ENTER" and float(llm.get("confidence") or 0) >= 0.6:
                llm["source"] = "llm_override_wait"
                return llm
        return _soften_leader_reject(ruled, dossier)

    if allow_llm:
        llm = fusion_case_llm(dossier, use_web_search=use_web_search)
        if llm is not None:
            # Never let LLM ENTER wreckage / toxic news if rules missed
            if classify_room_class(row) == "WRECKAGE_ROOM" and llm["verdict"] == "ENTER":
                llm["verdict"] = "REJECT"
                llm["risks"] = list(llm.get("risks") or []) + ["wreckage_override"]
            sit = dossier.get("news_situation") if isinstance(dossier.get("news_situation"), dict) else {}
            if sit.get("veto_long") and llm["verdict"] == "ENTER":
                llm["verdict"] = "REJECT"
                llm["risks"] = list(llm.get("risks") or []) + list(
                    sit.get("toxic_news_flags") or ["news_veto"]
                )
                llm["structure_note"] = (
                    f"Toxic news veto override: {sit.get('toxic_news_flags')}"
                )
            return _soften_leader_reject(llm, dossier)

    return _case(
        dossier["symbol"],
        "WAIT",
        0.55,
        dossier,
        structure_note="Case incomplete — fail-closed WAIT (no BUY on score alone)",
        sentiment_note="",
        risks=["case_incomplete"],
        evidence=["llm_unavailable_or_undecided"],
        source="fail_closed",
    )


def review_attention_pool(
    pool: list[dict[str, Any]],
    *,
    use_web_search: bool = True,
    allow_llm: bool = True,
    enrich_catalyst: bool = True,
    polygon_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Run case review on attention pool. Mutates rows with case_review dict.
    Returns pool rows with case attached, sorted ENTER-first.
    """
    if enrich_catalyst and pool:
        try:
            from tsd_scan_pipeline.quality_history_gate import enrich_queue_row

            enriched: list[dict[str, Any]] = []
            for row in pool:
                erow, ok, _gates, reasons = enrich_queue_row(
                    row, polygon_key=polygon_key, fetch_news=True,
                )
                # Preserve attention flags
                for k in (
                    "on_gainers",
                    "recent_leaderboard",
                    "tws_popular",
                    "tradable_popular",
                    "buzz_accel",
                    "momentum_context",
                    "attention_reasons",
                ):
                    if k in row:
                        erow[k] = row[k]
                erow["quality_passed"] = ok
                if not ok:
                    dossier = build_case_dossier(erow)
                    q_case = _case(
                        str(erow.get("symbol") or "").upper(),
                        "REJECT",
                        0.95,
                        dossier,
                        structure_note=f"quality_gate:{','.join(reasons[:3])}",
                        sentiment_note="",
                        risks=list(reasons[:5]),
                        evidence=reasons[:5],
                        source="quality_gate",
                    )
                    erow["case_review"] = _soften_leader_reject(q_case, dossier)
                    erow["case_verdict"] = erow["case_review"].get("verdict")
                enriched.append(erow)
                time.sleep(0.05)
            pool = enriched
        except Exception as exc:
            print(f"  case enrich warn: {exc}")

    out: list[dict[str, Any]] = []
    for row in pool:
        if row.get("case_review"):
            out.append(row)
            continue
        case = review_case(row, use_web_search=use_web_search, allow_llm=allow_llm)
        row = dict(row)
        row["case_review"] = case
        row["case_verdict"] = case.get("verdict")
        row["case_confidence"] = case.get("confidence")
        print(
            f"  CASE {row.get('symbol')}: {case.get('verdict')} "
            f"conf={case.get('confidence')} src={case.get('source')} "
            f"room={case.get('room_class')} mom={case.get('momentum_context')}"
        )
        out.append(row)

    order = {"ENTER": 0, "WAIT": 1, "REJECT": 2}
    out.sort(
        key=lambda r: (
            order.get(str(r.get("case_verdict") or ""), 9),
            -(float(r.get("case_confidence") or 0)),
            -(float(r.get("continuation_score") or 0)),
        )
    )
    return out


def select_enter_rows(
    reviewed: list[dict[str, Any]],
    *,
    max_n: int,
    exclude_symbols: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Only case ENTER rows with tradable popularity (momentum confirmation).

    Autopsy: popular+structure filter kept the green keep-profit book;
    obscure non-popular ENTERs were a drag.

    exclude_symbols: already OPEN / already_confirmed — do not consume take
    slots (autopsy P0 cap accounting).
    """
    skip = {str(s).upper() for s in (exclude_symbols or set())}
    enters = [
        r for r in reviewed
        if str(r.get("case_verdict") or r.get("case_review", {}).get("verdict") or "").upper()
        == "ENTER"
        and bool(r.get("tradable_popular") or r.get("recent_leaderboard") or r.get("on_gainers"))
        and str(r.get("symbol") or "").upper() not in skip
    ]
    enters.sort(
        key=lambda r: (
            -(float(r.get("case_confidence") or 0)),
            -(float(r.get("continuation_score") or 0)),
        )
    )
    return enters[: max(0, int(max_n))]
