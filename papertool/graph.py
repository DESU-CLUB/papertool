from __future__ import annotations

import json
from pathlib import Path

from papertool.ingest import build_graph_payload
from papertool.db import PaperDB


def export_graph_json(db: PaperDB, output_path: Path) -> Path:
    payload = build_graph_payload(db)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def export_graph_mermaid(db: PaperDB, output_path: Path) -> Path:
    payload = build_graph_payload(db)
    lines = ["graph LR"]
    for node in payload["nodes"]:
        node_id = node["id"][:8]
        title = str(node["title"]).replace('"', "'")
        lines.append(f'    {node_id}["{title}"]')
    for link in payload["links"]:
        source = str(link["source"])[:8]
        target = str(link["target"])[:8]
        lines.append(f"    {source} --> {target}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def export_graph_html(db: PaperDB, output_path: Path) -> Path:
    payload = build_graph_payload(db)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_json = json.dumps(payload)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>Paper Graph</title>
  <style>
    html, body {{ margin: 0; height: 100%; font-family: ui-sans-serif, system-ui; }}
    #root {{ width: 100%; height: 100%; background: #f6f8ff; }}
    .tooltip {{ position: absolute; background: #111827; color: #fff; padding: 8px; border-radius: 6px; pointer-events: none; font-size: 12px; }}
  </style>
  <script src=\"https://cdn.jsdelivr.net/npm/d3@7\"></script>
</head>
<body>
  <svg id=\"root\"></svg>
  <script>
    const data = {data_json};
    const svg = d3.select('#root');
    let width = window.innerWidth;
    let height = window.innerHeight;
    svg.attr('viewBox', [0, 0, width, height]);
    const viewport = svg.append('g').attr('class', 'viewport');

    const link = viewport.append('g')
      .attr('stroke', '#93a4d2')
      .selectAll('line')
      .data(data.links)
      .join('line')
      .attr('stroke-opacity', 0.8)
      .attr('stroke-width', 1.5);

    const simulation = d3.forceSimulation(data.nodes)
      .force('link', d3.forceLink(data.links).id(d => d.id).distance(90))
      .force('charge', d3.forceManyBody().strength(-260))
      .force('center', d3.forceCenter(width / 2, height / 2));

    const node = viewport.append('g')
      .attr('stroke', '#1f2937')
      .attr('stroke-width', 1)
      .selectAll('circle')
      .data(data.nodes)
      .join('circle')
      .attr('r', 8)
      .attr('fill', '#1d4ed8')
      .call(drag(simulation));

    const label = viewport.append('g')
      .selectAll('text')
      .data(data.nodes)
      .join('text')
      .text(d => d.title.length > 40 ? d.title.slice(0, 40) + '...' : d.title)
      .attr('font-size', 10)
      .attr('fill', '#111827');

    const tooltip = d3.select('body').append('div').attr('class', 'tooltip').style('opacity', 0);
    const zoom = d3.zoom()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => {{
        viewport.attr('transform', event.transform);
      }});
    svg.call(zoom);
    svg.on('dblclick.zoom', null);

    function fitToScreen(animate = true) {{
      const positioned = data.nodes.filter(d => Number.isFinite(d.x) && Number.isFinite(d.y));
      if (!positioned.length) return;

      const minX = d3.min(positioned, d => d.x);
      const maxX = d3.max(positioned, d => d.x);
      const minY = d3.min(positioned, d => d.y);
      const maxY = d3.max(positioned, d => d.y);
      if (![minX, maxX, minY, maxY].every(Number.isFinite)) return;

      const boundsWidth = Math.max(1, maxX - minX);
      const boundsHeight = Math.max(1, maxY - minY);
      const margin = 36;
      const scale = Math.max(0.2, Math.min(3, Math.min(
        (width - margin * 2) / boundsWidth,
        (height - margin * 2) / boundsHeight
      )));
      const tx = width / 2 - ((minX + maxX) / 2) * scale;
      const ty = height / 2 - ((minY + maxY) / 2) * scale;
      const transform = d3.zoomIdentity.translate(tx, ty).scale(scale);

      const target = animate ? svg.transition().duration(300) : svg;
      target.call(zoom.transform, transform);
    }}

    node
      .on('mouseover', (event, d) => {{
        tooltip.style('opacity', 1).html(`<b>${{d.title}}</b><br/>${{d.path}}`);
      }})
      .on('mousemove', (event) => {{
        tooltip.style('left', (event.pageX + 12) + 'px').style('top', (event.pageY + 12) + 'px');
      }})
      .on('mouseout', () => tooltip.style('opacity', 0));

    simulation.on('tick', () => {{
      link
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);

      node
        .attr('cx', d => d.x)
        .attr('cy', d => d.y);

      label
        .attr('x', d => d.x + 10)
        .attr('y', d => d.y + 4);
    }});
    simulation.on('end', () => {{
      fitToScreen(true);
    }});

    window.addEventListener('resize', () => {{
      width = window.innerWidth;
      height = window.innerHeight;
      svg.attr('viewBox', [0, 0, width, height]);
      fitToScreen(false);
    }});
    svg.on('dblclick', () => fitToScreen(true));

    function drag(simulation) {{
      function dragstarted(event, d) {{
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      }}
      function dragged(event, d) {{
        d.fx = event.x;
        d.fy = event.y;
      }}
      function dragended(event, d) {{
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      }}
      return d3.drag().on('start', dragstarted).on('drag', dragged).on('end', dragended);
    }}
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    return output_path
