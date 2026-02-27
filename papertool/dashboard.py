from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from papertool.db import PaperDB


def _medal_badge(kind: str, earned: bool, label: str) -> str:
    medal_kind = kind if kind in {"bronze", "silver", "gold"} else "muted"
    class_name = f"medal-{medal_kind}" if earned else "medal-muted"
    return f'<span class="badge {class_name}">{label}</span>'


def _build_daily_activity_series(
    db: PaperDB,
    progress_rows: list[Any],
    *,
    tz_name: str,
    day_window: int,
) -> list[dict[str, Any]]:
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        zone = timezone.utc

    progress_by_day: dict[str, dict[str, int]] = {}
    for row in progress_rows:
        day = str(row["day_key"])
        progress_by_day[day] = {
            "qualified_count": int(row["qualified_count"] or 0),
            "goal_target": int(row["goal_target"] or 0),
            "streak_value": int(row["streak_value"] or 0),
        }

    review_by_day: dict[str, set[str]] = {}
    review_rows = db.conn.execute(
        """
        SELECT created_at, paper_id
        FROM quiz_history
        WHERE source = 'review' AND user_answer IS NOT NULL
        """
    ).fetchall()
    for row in review_rows:
        created_at = str(row["created_at"] or "").strip()
        paper_id = str(row["paper_id"] or "").strip()
        if not created_at or not paper_id:
            continue
        try:
            stamp = datetime.fromisoformat(created_at)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        day_key = stamp.astimezone(zone).date().isoformat()
        review_by_day.setdefault(day_key, set()).add(paper_id)

    today = datetime.now(zone).date()
    total_days = max(1, day_window)
    keys = [(today - timedelta(days=offset)).isoformat() for offset in range(total_days - 1, -1, -1)]

    series: list[dict[str, Any]] = []
    for day in keys:
        progress = progress_by_day.get(day, {})
        qualified = int(progress.get("qualified_count") or 0)
        reviewed = len(review_by_day.get(day, set()))
        series.append(
            {
                "day_key": day,
                "qualified_count": qualified,
                "reviewed_count": reviewed,
                "activity_total": qualified + reviewed,
                "goal_target": int(progress.get("goal_target") or 0),
                "streak_value": int(progress.get("streak_value") or 0),
            }
        )
    return series


def build_medals_dashboard(db: PaperDB, output_path: Path, day_window: int = 60) -> Path:
    goal = db.get_goal_settings()
    progress_rows = db.daily_progress_rows(limit=max(1, day_window))
    progress_rows = sorted(progress_rows, key=lambda row: str(row["day_key"]))
    medals = db.medal_overview(limit=10000)
    resources = db.recent_resources(limit=20)
    resource_topics = db.resource_topic_summary(limit=20)

    current_streak = progress_rows[-1]["streak_value"] if progress_rows else 0
    longest_streak = max((int(row["streak_value"]) for row in progress_rows), default=0)
    today = progress_rows[-1] if progress_rows else {
        "day_key": "",
        "goal_target": int(goal["daily_goal"]),
        "qualified_count": 0,
    }

    bronze_count = sum(1 for row in medals if row.get("bronze_awarded_at"))
    silver_count = sum(1 for row in medals if int(row.get("silver_active") or 0) == 1)
    gold_count = sum(1 for row in medals if row.get("gold_awarded_at"))

    payload = {
        "goal": goal,
        "progress": progress_rows,
        "activity": _build_daily_activity_series(
            db,
            progress_rows,
            tz_name=str(goal.get("timezone") or "UTC"),
            day_window=max(1, day_window),
        ),
        "medals": medals,
        "resources": resources,
        "resource_topics": resource_topics,
    }
    data_json = json.dumps(payload, ensure_ascii=True)

    rows_html: list[str] = []
    for row in medals:
        bronze = _medal_badge("bronze", bool(row.get("bronze_awarded_at")), "Bronze")
        silver = _medal_badge("silver", bool(int(row.get("silver_active") or 0)), "Silver")
        gold = _medal_badge("gold", bool(row.get("gold_awarded_at")), "Gold")
        score = row.get("latest_review_score")
        score_text = "-" if score is None else f"{float(score):.2f}"
        rows_html.append(
            "<tr>"
            f"<td>{row.get('title','')}</td>"
            f"<td>{row.get('queue_status','')}</td>"
            f"<td>{bronze} {silver} {gold}</td>"
            f"<td>{score_text}</td>"
            f"<td>{row.get('gold_repo_url') or ''}</td>"
            "</tr>"
        )

    resources_html: list[str] = []
    for row in resources:
        resources_html.append(
            "<tr>"
            f"<td>{row.get('kind','')}</td>"
            f"<td>{row.get('title','')}</td>"
            f"<td>{row.get('url','')}</td>"
            f"<td>{row.get('updated_at','')}</td>"
            "</tr>"
        )

    topic_rows_html: list[str] = []
    for row in resource_topics:
        topic_rows_html.append(
            "<tr>"
            f"<td>{row.get('topic_label','')}</td>"
            f"<td>{row.get('resource_count','0')}</td>"
            "</tr>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PaperTool Medals Dashboard</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
    :root {{
      --bg: #eef4ff;
      --text: #0f172a;
      --card: #ffffff;
      --muted: #64748b;
      --accent: #0ea5e9;
      --medal-bronze: #b45309;
      --medal-silver: #9ca3af;
      --medal-gold: #ca8a04;
      --medal-muted: #94a3b8;
      --shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
      background:
        radial-gradient(circle at 8% 12%, #bfdbfe 0%, rgba(191, 219, 254, 0) 36%),
        radial-gradient(circle at 92% 0%, #e0f2fe 0%, rgba(224, 242, 254, 0) 34%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--text);
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 28px 18px 40px;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0;
      font-family: "Space Grotesk", "IBM Plex Sans", ui-sans-serif, system-ui;
      font-size: clamp(1.6rem, 2.6vw, 2.2rem);
      letter-spacing: 0.01em;
    }}
    .subtitle {{
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 14px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 14px;
      margin-bottom: 16px;
    }}
    .card {{
      background: var(--card);
      border-radius: 14px;
      padding: 14px 16px;
      box-shadow: var(--shadow);
      border: 1px solid #dbe7f5;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-weight: 600;
    }}
    .value {{
      font-size: clamp(1.35rem, 3vw, 1.8rem);
      font-weight: 700;
      font-family: "Space Grotesk", "IBM Plex Sans", ui-sans-serif, system-ui;
    }}
    .charts-grid {{
      display: grid;
      grid-template-columns: 1.6fr 1.1fr;
      gap: 14px;
      margin-bottom: 18px;
    }}
    .chart-card {{
      background: var(--card);
      border-radius: 14px;
      box-shadow: var(--shadow);
      border: 1px solid #dbe7f5;
      padding: 14px;
      min-height: 220px;
    }}
    .chart-card h3 {{
      margin: 0 0 8px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #475569;
    }}
    .chart-card canvas {{
      width: 100%;
      height: 220px;
      display: block;
    }}
    .chart-legend {{
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: #475569;
      font-size: 12px;
    }}
    .legend-chip {{ display: inline-flex; align-items: center; gap: 6px; }}
    .legend-dot {{ width: 10px; height: 10px; border-radius: 999px; display: inline-block; }}
    .legend-line {{
      width: 14px;
      height: 0;
      border-top: 2px dashed #ef4444;
      display: inline-block;
    }}
    .tables {{
      display: grid;
      gap: 14px;
    }}
    .table-card {{
      background: var(--card);
      border-radius: 14px;
      box-shadow: var(--shadow);
      border: 1px solid #dbe7f5;
      overflow: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
    th {{
      background: #edf3fb;
      font-size: 12px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      position: sticky;
      top: 0;
    }}
    tr:last-child td {{ border-bottom: none; }}
    .section-title {{
      margin: 0;
      padding: 12px 14px 0;
      font-family: "Space Grotesk", "IBM Plex Sans", ui-sans-serif, system-ui;
      font-size: 1.04rem;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      line-height: 1.4;
      margin-right: 4px;
      vertical-align: middle;
    }}
    .medal-bronze {{ background: var(--medal-bronze); color: #ffffff; }}
    .medal-silver {{ background: var(--medal-silver); color: #111827; }}
    .medal-gold {{ background: var(--medal-gold); color: #111827; }}
    .medal-muted {{ background: var(--medal-muted); color: #0f172a; }}
    @media (max-width: 920px) {{
      .charts-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="hero">
      <div>
        <h1>PaperTool Streaks & Medals</h1>
        <p class="subtitle">Daily goal: {int(goal["daily_goal"])} paper(s), timezone: {goal["timezone"]}</p>
      </div>
    </div>
    <div class="cards">
      <div class="card"><div class="label">Current Streak</div><div class="value">{int(current_streak)}</div></div>
      <div class="card"><div class="label">Longest Streak</div><div class="value">{int(longest_streak)}</div></div>
      <div class="card"><div class="label">Today Progress</div><div class="value">{int(today["qualified_count"])} / {int(today["goal_target"])}</div></div>
      <div class="card"><div class="label">Bronze / Silver / Gold</div><div class="value">{bronze_count} / {silver_count} / {gold_count}</div></div>
    </div>
    <div class="charts-grid">
      <section class="chart-card">
        <h3>Daily Activity (Read+Quizzed and Reviewed)</h3>
        <canvas id="progressChart"></canvas>
        <div class="chart-legend">
          <span class="legend-chip"><span class="legend-dot" style="background:#0ea5e9;"></span>Read + quizzed</span>
          <span class="legend-chip"><span class="legend-dot" style="background:#1d4ed8;"></span>Reviewed</span>
          <span class="legend-chip"><span class="legend-line"></span>Goal target</span>
        </div>
      </section>
      <section class="chart-card">
        <h3>Streak Momentum</h3>
        <canvas id="streakChart"></canvas>
      </section>
      <section class="chart-card" style="grid-column: 1 / -1;">
        <h3>Medal Mix</h3>
        <canvas id="medalChart"></canvas>
        <div class="chart-legend">
          <span class="legend-chip"><span class="legend-dot" style="background:var(--medal-bronze);"></span>Bronze {bronze_count}</span>
          <span class="legend-chip"><span class="legend-dot" style="background:var(--medal-silver);"></span>Silver {silver_count}</span>
          <span class="legend-chip"><span class="legend-dot" style="background:var(--medal-gold);"></span>Gold {gold_count}</span>
        </div>
      </section>
    </div>
    <div class="tables">
      <section class="table-card">
        <h2 class="section-title">Paper Medals</h2>
        <table>
          <thead>
            <tr>
              <th>Paper</th>
              <th>Queue</th>
              <th>Medals</th>
              <th>Latest Review</th>
              <th>Gold Repo</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </section>
      <section class="table-card">
        <h2 class="section-title">Resource Topics</h2>
        <table>
          <thead>
            <tr>
              <th>Topic</th>
              <th>Resources</th>
            </tr>
          </thead>
          <tbody>
            {''.join(topic_rows_html)}
          </tbody>
        </table>
      </section>
      <section class="table-card">
        <h2 class="section-title">Recent Resources</h2>
        <table>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Title</th>
              <th>URL</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {''.join(resources_html)}
          </tbody>
        </table>
      </section>
    </div>
  </main>
  <script>
    const payload = {data_json};
    const rows = payload.activity.slice(-60);
    const palette = {{
      accent: "#0ea5e9",
      missed: "#cbd5e1",
      streak: "#1d4ed8",
      streakFill: "rgba(29, 78, 216, 0.16)",
      goalLine: "#ef4444",
      grid: "#dbe5ef",
      ink: "#0f172a",
      bronze: "#b45309",
      silver: "#9ca3af",
      gold: "#ca8a04",
      muted: "#94a3b8",
    }};

    function setupCanvas(id) {{
      const canvas = document.getElementById(id);
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const width = Math.max(200, canvas.clientWidth);
      const height = Math.max(160, canvas.clientHeight);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return {{ ctx, width, height }};
    }}

    function drawGrid(ctx, left, top, right, bottom, maxValue, ticks) {{
      ctx.strokeStyle = palette.grid;
      ctx.lineWidth = 1;
      ctx.fillStyle = "#64748b";
      ctx.font = "11px IBM Plex Sans";
      for (let i = 0; i <= ticks; i += 1) {{
        const t = i / ticks;
        const y = bottom - t * (bottom - top);
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(right, y);
        ctx.stroke();
        const value = Math.round(maxValue * t);
        ctx.fillText(String(value), 8, y + 4);
      }}
      ctx.fillText("count", 8, top - 2);
    }}

    function drawDateTicks(ctx, left, right, bottom, points) {{
      if (!points.length) {{
        return;
      }}
      const span = Math.max(1, points.length - 1);
      const step = Math.max(1, Math.floor(points.length / 6));
      ctx.fillStyle = "#64748b";
      ctx.font = "11px IBM Plex Sans";
      ctx.textAlign = "center";
      for (let i = 0; i < points.length; i += step) {{
        const x = left + (i / span) * (right - left);
        const day = String(points[i].day_key || "");
        const label = day.length >= 10 ? day.slice(5) : day;
        ctx.fillText(label, x, bottom + 14);
      }}
      ctx.textAlign = "start";
    }}

    function drawProgressChart() {{
      const {{ ctx, width, height }} = setupCanvas("progressChart");
      ctx.clearRect(0, 0, width, height);
      const left = 34;
      const right = width - 8;
      const top = 10;
      const bottom = height - 26;
      if (!rows.length) {{
        ctx.fillStyle = "#64748b";
        ctx.font = "13px IBM Plex Sans";
        ctx.fillText("No progress data yet.", 16, 30);
        return;
      }}
      const maxValue = Math.max(
        1,
        ...rows.map(r => Number(r.goal_target || 0)),
        ...rows.map(r => Number(r.activity_total || 0))
      );
      drawGrid(ctx, left, top, right, bottom, maxValue, 4);
      const slot = (right - left) / rows.length;
      const barW = Math.max(2, slot * 0.72);

      rows.forEach((row, i) => {{
        const qualified = Number(row.qualified_count || 0);
        const reviewed = Number(row.reviewed_count || 0);
        const x = left + i * slot + (slot - barW) / 2;
        const available = bottom - top;
        const qh = maxValue ? (qualified / maxValue) * available : 0;
        const rh = maxValue ? (reviewed / maxValue) * available : 0;
        const qy = bottom - qh;
        const ry = qy - rh;
        ctx.fillStyle = palette.accent;
        ctx.fillRect(x, qy, barW, qh);
        if (reviewed > 0) {{
          ctx.fillStyle = palette.streak;
          ctx.fillRect(x, ry, barW, rh);
        }}
      }});

      ctx.save();
      ctx.strokeStyle = palette.goalLine;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      rows.forEach((row, i) => {{
        const goalTarget = Number(row.goal_target || 0);
        const y = bottom - ((goalTarget / maxValue) * (bottom - top));
        const x = left + i * slot + slot / 2;
        if (i === 0) {{
          ctx.moveTo(x, y);
        }} else {{
          ctx.lineTo(x, y);
        }}
      }});
      ctx.stroke();
      ctx.restore();
      drawDateTicks(ctx, left, right, bottom, rows);
    }}

    function drawStreakChart() {{
      const {{ ctx, width, height }} = setupCanvas("streakChart");
      ctx.clearRect(0, 0, width, height);
      const left = 30;
      const right = width - 10;
      const top = 10;
      const bottom = height - 26;
      if (!rows.length) {{
        ctx.fillStyle = "#64748b";
        ctx.font = "13px IBM Plex Sans";
        ctx.fillText("No streak data yet.", 16, 30);
        return;
      }}
      const maxStreak = Math.max(1, ...rows.map(r => Number(r.streak_value || 0)));
      drawGrid(ctx, left, top, right, bottom, maxStreak, 4);
      const span = Math.max(1, rows.length - 1);

      ctx.beginPath();
      rows.forEach((row, i) => {{
        const streak = Number(row.streak_value || 0);
        const x = left + (i / span) * (right - left);
        const y = bottom - ((streak / maxStreak) * (bottom - top));
        if (i === 0) {{
          ctx.moveTo(x, y);
        }} else {{
          ctx.lineTo(x, y);
        }}
      }});
      ctx.lineWidth = 2.4;
      ctx.strokeStyle = palette.streak;
      ctx.stroke();

      ctx.lineTo(right, bottom);
      ctx.lineTo(left, bottom);
      ctx.closePath();
      ctx.fillStyle = palette.streakFill;
      ctx.fill();
      drawDateTicks(ctx, left, right, bottom, rows);
    }}

    function drawMedalMix() {{
      const {{ ctx, width, height }} = setupCanvas("medalChart");
      ctx.clearRect(0, 0, width, height);
      const values = [{bronze_count}, {silver_count}, {gold_count}];
      const colors = [palette.bronze, palette.silver, palette.gold];
      const total = values.reduce((acc, n) => acc + n, 0);
      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.min(width, height) * 0.3;
      const ring = Math.max(18, radius * 0.4);
      if (total <= 0) {{
        ctx.strokeStyle = palette.muted;
        ctx.lineWidth = ring;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = palette.ink;
        ctx.font = "600 14px IBM Plex Sans";
        ctx.textAlign = "center";
        ctx.fillText("No medals yet", cx, cy + 4);
        return;
      }}

      let start = -Math.PI / 2;
      values.forEach((value, index) => {{
        if (value <= 0) {{
          return;
        }}
        const slice = (value / total) * Math.PI * 2;
        ctx.beginPath();
        ctx.strokeStyle = colors[index];
        ctx.lineWidth = ring;
        ctx.arc(cx, cy, radius, start, start + slice);
        ctx.stroke();
        start += slice;
      }});
      ctx.fillStyle = palette.ink;
      ctx.textAlign = "center";
      ctx.font = "700 26px Space Grotesk";
      ctx.fillText(String(total), cx, cy + 2);
      ctx.font = "12px IBM Plex Sans";
      ctx.fillStyle = "#64748b";
      ctx.fillText("Total medals", cx, cy + 22);
    }}

    function renderCharts() {{
      drawProgressChart();
      drawStreakChart();
      drawMedalMix();
    }}

    window.addEventListener("resize", renderCharts);
    renderCharts();
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
