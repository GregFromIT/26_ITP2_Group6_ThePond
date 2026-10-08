import sys
from xml.sax.saxutils import escape

W, H = 32, 20          # node half-width / half-height
LANE_W, LANE_GAP, X0 = 180, 70, 60
TOP = 70
GREEN, MAG = "#2f6f5e", "#9c2a5c"


def lane_cx(i):
    return X0 + i * (LANE_W + LANE_GAP) + LANE_W / 2


def clip(ax, ay, bx, by):
    """Point on node A's border along the line A->B."""
    dx, dy = bx - ax, by - ay
    t = min(W / abs(dx) if dx else 1e9, H / abs(dy) if dy else 1e9)
    return ax + dx * t, ay + dy * t


def loop(cx, cy, side):
    if side == "right":
        return f"M{cx+W},{cy-8} C{cx+W+58},{cy-40} {cx+W+58},{cy+34} {cx+W+2},{cy+10}"
    if side == "left":
        return f"M{cx-W},{cy-8} C{cx-W-58},{cy-40} {cx-W-58},{cy+34} {cx-W-2},{cy+10}"
    return f"M{cx-14},{cy+H} C{cx-46},{cy+H+56} {cx+40},{cy+H+56} {cx+12},{cy+H+2}"


def build(title, lanes, nodes, edges, loops, legend_cols):
    pos = {n: (lane_cx(l), y) for n, (l, y) in nodes.items()}
    lane_bottom = max(y for _, y in pos.values()) + 80
    width = X0 * 2 + len(lanes) * LANE_W + (len(lanes) - 1) * LANE_GAP
    width = max(width, 760)
    off = (width - (X0 * 2 + len(lanes) * LANE_W + (len(lanes) - 1) * LANE_GAP)) / 2

    leg_top = lane_bottom + 50
    leg_rows = max(sum(len(lines) for _, lines in col) + len(col) * 0.6 for col in legend_cols)
    height = int(leg_top + leg_rows * 21 + 40)

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="Helvetica, Arial, sans-serif">',
         '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{GREEN}"/></marker></defs>',
         f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
         f'<g transform="translate({off},0)">',
         f'<text x="{(width - 2*off)/2}" y="44" text-anchor="middle" font-size="26" font-weight="bold" fill="#222">{escape(title)}</text>']

    for i, name in enumerate(lanes):
        x = X0 + i * (LANE_W + LANE_GAP)
        o.append(f'<rect x="{x}" y="{TOP}" width="{LANE_W}" height="{lane_bottom - TOP}" rx="24" fill="#f4f7f6" stroke="{GREEN}" stroke-width="2"/>')
        o.append(f'<text x="{x + LANE_W/2}" y="{TOP + 34}" text-anchor="middle" font-size="20" font-weight="bold" fill="#222">{escape(name)}</text>')

    o.append(f'<g stroke="{GREEN}" stroke-width="2" fill="none" marker-end="url(#arrow)">')
    for a, b in edges:
        (ax, ay), (bx, by) = pos[a], pos[b]
        x1, y1 = clip(ax, ay, bx, by)
        x2, y2 = clip(bx, by, ax, ay)
        o.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    for n, side in loops.items():
        o.append(f'<path d="{loop(*pos[n], side)}"/>')
    o.append('</g>')

    for n, (cx, cy) in pos.items():
        o.append(f'<rect x="{cx-W}" y="{cy-H}" width="{2*W}" height="{2*H}" rx="10" fill="#fff" stroke="{MAG}" stroke-width="2"/>')
        o.append(f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central" font-size="18" font-weight="bold" fill="{MAG}">{n}</text>')

    col_w = (width - 2 * off - 2 * X0) / len(legend_cols)
    for ci, col in enumerate(legend_cols):
        x, y = X0 + ci * col_w, leg_top
        for num, lines in col:
            o.append(f'<text x="{x}" y="{y}" font-size="15" font-weight="bold" fill="{MAG}">{num}.</text>')
            for j, line in enumerate(lines):
                o.append(f'<text x="{x + 30}" y="{y + j*21}" font-size="15" fill="#222">{escape(line)}</text>')
            y += len(lines) * 21 + 12

    o.append('</g></svg>')
    return "\n".join(o)


startup = build(
    "Challenge Startup",
    ["user", "db (+ code)", "prox"],
    {1: (0, 150), 2: (1, 150), 3: (1, 250), 4: (0, 250), 5: (2, 340),
     6: (2, 440), 7: (0, 440), 8: (1, 530), 9: (1, 620), 10: (2, 710), 11: (0, 710)},
    [(1, 2), (2, 3), (3, 4), (3, 5), (5, 6), (6, 7), (6, 8), (8, 9), (9, 10), (10, 11)],
    {3: "bottom", 6: "right"},
    [[(1, ["user selects challenge"]),
      (2, ["db initiates cha.py, vm.py,", "chainst.py, vminst.py"]),
      (3, ["db verifies & checks for duplicates"]),
      (4, ["tells user db initiated"]),
      (5, ["prox creates everything", "based on db info"])],
     [(6, ["prox verifies & checks for duplicates,", "updates as needed, sends to db"]),
      (7, ["sends update to user"]),
      (8, ["verifies & checks for updates"]),
      (9, ["initiates"]),
      (10, ["sends back to prox"]),
      (11, ["serves to user"])]],
)

scoring = build(
    "Scoring",
    ["user", "db (+ code)"],
    {1: (0, 150), 2: (1, 150), 3: (1, 260), 4: (0, 260), 5: (1, 360), 6: (0, 440)},
    [(1, 2), (2, 3), (3, 4), (3, 5), (5, 6)],
    {3: "right"},
    [[(1, ["user inputs flag"]),
      (2, ["flag is checked against flags table"]),
      (3, ["flag is checked against user-flag table"]),
      (4, ["user is updated w/ success or fail"]),
      (5, ["if success, update user table += points"]),
      (6, ["user is updated"])]],
)

out = sys.argv[1]
open(f"{out}/challenge_startup.svg", "w").write(startup)
open(f"{out}/scoring.svg", "w").write(scoring)
