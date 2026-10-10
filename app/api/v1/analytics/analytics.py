"""
Executive Analytics & Call Quality Monitoring API
Computes core KPIs: Containment Rate, Lead Capture Rate, AHT, Sentiment Breakdown, and Cost.
Runs asynchronously via AsyncSession to prevent blocking FastAPI event loop.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
from app.db.session import get_db
from app.config import settings

router = APIRouter(prefix="/api/analytics", tags=["Executive Analytics"])


@router.get("/overview")
async def get_analytics_overview(
    tenant_id: Optional[str] = Query(None, description="Filter metrics by tenant ID"),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Returns aggregated executive metrics for the dashboard asynchronously,
    strictly scoped by tenant when tenant_id is provided.
    """
    try:
        where_clause = ""
        params = {}
        if tenant_id and tenant_id.strip():
            tid = tenant_id.strip()
            where_clause = """
                WHERE tenant_id = :tid 
                   OR tenant_id IN (
                       SELECT t2.tenant_id FROM tenants t2 
                       WHERE t2.business_name ILIKE (SELECT t1.business_name FROM tenants t1 WHERE t1.tenant_id = :tid LIMIT 1)
                   )
            """
            params["tid"] = tid

        # 1. Calls aggregate
        result = await db.execute(text(f"""
            SELECT 
                COUNT(*) as total_calls,
                COUNT(CASE WHEN transferred = true THEN 1 END) as transferred_calls,
                COALESCE(SUM(
                    CASE 
                        WHEN duration > 0 THEN duration 
                        WHEN end_time IS NOT NULL AND start_time IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (end_time - start_time))
                        ELSE 0 
                    END
                ), 0) as total_duration,
                COALESCE(AVG(
                    CASE 
                        WHEN duration > 0 THEN duration 
                        WHEN end_time IS NOT NULL AND start_time IS NOT NULL 
                            THEN EXTRACT(EPOCH FROM (end_time - start_time))
                        ELSE NULL 
                    END
                ), 0) as avg_duration
            FROM calls
            {where_clause}
        """), params)
        calls_agg = result.mappings().first()

        total_calls = calls_agg["total_calls"] or 0
        transferred_calls = calls_agg["transferred_calls"] or 0
        total_duration_sec = float(calls_agg["total_duration"] or 0)
        avg_duration_sec = float(calls_agg["avg_duration"] or 0)

        # 2. Leads count
        leads_result = await db.execute(text(f"SELECT COUNT(*) FROM leads {where_clause}"), params)
        leads_count = leads_result.scalar() or 0

        # 3. Containment & Lead Conversion Rates
        if total_calls > 0:
            ai_resolved = total_calls - transferred_calls
            containment_rate = round((ai_resolved / total_calls) * 100, 1)
            lead_capture_rate = min(100.0, round((leads_count / total_calls) * 100, 1))
        else:
            containment_rate = 100.0
            lead_capture_rate = 0.0

        # 4. Sentiment breakdown
        sent_where = f"{where_clause} AND caller_sentiment IS NOT NULL AND caller_sentiment != ''" if where_clause else "WHERE caller_sentiment IS NOT NULL AND caller_sentiment != ''"
        sentiment_result = await db.execute(text(f"""
            SELECT caller_sentiment, COUNT(*) as cnt 
            FROM calls 
            {sent_where}
            GROUP BY caller_sentiment 
            ORDER BY cnt DESC
        """), params)
        sentiment_rows = sentiment_result.fetchall()
        sentiment_breakdown = {r[0]: r[1] for r in sentiment_rows}

        # 5. Outcome breakdown
        outcome_result = await db.execute(text(f"""
            SELECT COALESCE(outcome_tag, 'in_progress') as outcome, COUNT(*) as cnt 
            FROM calls 
            {where_clause}
            GROUP BY outcome_tag 
            ORDER BY cnt DESC
        """), params)
        outcome_rows = outcome_result.fetchall()
        outcome_breakdown = {r[0]: r[1] for r in outcome_rows}

        total_mins = int(total_duration_sec // 60)
        total_secs = int(total_duration_sec % 60)
        formatted_airtime = f"{total_mins}m {total_secs}s" if total_mins < 60 else f"{total_mins // 60}h {total_mins % 60}m"

        base_cost = getattr(settings, "PLATFORM_BASE_COST_PER_MIN", 2.00)
        est_cost_inr = round((total_duration_sec / 60.0) * base_cost, 2)

        return {
            "status": "success",
            "metrics": {
                "total_calls": total_calls,
                "transferred_calls": transferred_calls,
                "containment_rate": containment_rate,
                "total_leads": leads_count,
                "lead_capture_rate": lead_capture_rate,
                "total_airtime_seconds": round(total_duration_sec, 1),
                "total_airtime_formatted": formatted_airtime,
                "average_handling_time_seconds": round(avg_duration_sec, 1),
                "estimated_cost_inr": est_cost_inr,
                "sentiment_breakdown": sentiment_breakdown,
                "outcome_breakdown": outcome_breakdown
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "metrics": {
                "total_calls": 0,
                "transferred_calls": 0,
                "containment_rate": 100.0,
                "total_leads": 0,
                "lead_capture_rate": 0.0,
                "total_airtime_seconds": 0,
                "total_airtime_formatted": "0m 0s",
                "average_handling_time_seconds": 0,
                "estimated_cost_inr": 0.0,
                "sentiment_breakdown": {},
                "outcome_breakdown": {}
            }
        }
