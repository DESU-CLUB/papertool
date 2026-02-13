from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from papertool.db import PaperDB


def _bool_badge(value: bool, label: str) -> str:
    color = "#0f766e" if value else "#94a3b8"
    return f'<span style="padding:2px 8px;border-radius:999px;background:{color};color:#fff;font-size:12px;">{label}</span>'


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
        "medals": medals,
        "resources": resources,
        "resource_topics": resource_topics,
    }
    data_json = json.dumps(payload, ensure_ascii=True)

    rows_html: list[str] = []
    for row in medals:
        bronze = _bool_badge(bool(row.get("bronze_awarded_at")), "Bronze")
        silver = _bool_badge(bool(int(row.get("silver_active") or 0)), "Silver")
        gold = _bool_badge(bool(row.get("gold_awarded_at")), "Gold")
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
    :root {{
      --bg: #f7fafc;
      --text: #111827;
      --card: #ffffff;
      --muted: #64748b;
      --accent: #0ea5e9;
    }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
      background: radial-gradient(circle at 10% 20%, #e0f2fe, transparent 35%), var(--bg);
      color: var(--text);
    }}
    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--card);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 1px 10px rgba(2, 6, 23, 0.08);
    }}
    .label {{ color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .value {{ font-size: 26px; font-weight: 700; }}
    #chart {{
      width: 100%;
      height: 240px;
      background: var(--card);
      border-radius: 12px;
      padding: 12px;
      box-sizing: border-box;
      margin-bottom: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 1px 10px rgba(2, 6, 23, 0.08);
    }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
    th {{ background: #e2e8f0; font-size: 12px; letter-spacing: 0.03em; text-transform: uppercase; }}
    tr:last-child td {{ border-bottom: none; }}
    .section-title {{ margin: 20px 0 10px 0; }}
  </style>
</head>
<body>
  <main>
    <h1>PaperTool Streaks & Medals</h1>
    <p>Daily goal: {int(goal["daily_goal"])} paper(s), timezone: {goal["timezone"]}</p>
    <div class="cards">
      <div class="card"><div class="label">Current Streak</div><div class="value">{int(current_streak)}</div></div>
      <div class="card"><div class="label">Longest Streak</div><div class="value">{int(longest_streak)}</div></div>
      <div class="card"><div class="label">Today Progress</div><div class="value">{int(today["qualified_count"])} / {int(today["goal_target"])}</div></div>
      <div class="card"><div class="label">Bronze / Silver / Gold</div><div class="value">{bronze_count} / {silver_count} / {gold_count}</div></div>
    </div>
    <canvas id="chart"></canvas>
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
  </main>
  <script>
    const payload = {data_json};
    const rows = payload.progress.slice(-60);
    const canvas = document.getElementById("chart");
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);
    if (rows.length > 0) {{
      const max = Math.max(...rows.map(r => Number(r.goal_target || 1)), 1);
      const barW = Math.max(3, Math.floor((width - 20) / rows.length) - 1);
      rows.forEach((row, idx) => {{
        const x = 10 + idx * (barW + 1);
        const h = Math.round((Number(row.qualified_count || 0) / max) * (height - 30));
        const y = height - 20 - h;
        const met = Number(row.goal_met || 0) === 1;
        ctx.fillStyle = met ? "#0ea5e9" : "#cbd5e1";
        ctx.fillRect(x, y, barW, h);
      }});
    }}
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
