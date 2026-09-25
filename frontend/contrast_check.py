"""WCAG contrast of the D-019 state colors against both theme backgrounds.
Rails are non-text UI (WCAG 1.4.11: >= 3:1); dashed/hatched rails show the
background between marks, so every mark must pass on its own. Chip text: >= 4.5:1.
    python frontend/contrast_check.py"""
BG = {"light": "#ffffff", "dark": "#0d1117"}
CHIP_BG = {"light": "#f6f8fa", "dark": "#161b22"}  # chip background (text sits on this)
COLORS = {  # state: (light, dark, rail pattern)
    "muted":       ("#57606a", "#8b949e", "none (idle is silent; muted chips, e.g. background build)"),
    "queued":      ("#57606a", "#8b949e", "solid"),
    "stale":       ("#57606a", "#8b949e", "hatched"),
    "compiling":   ("#8250df", "#a371f7", "solid"),
    "running":     ("#0969da", "#58a6ff", "solid"),
    "ok":          ("#1a7f37", "#3fb950", "none (silent)"),
    "modified":    ("#9a6700", "#d29922", "dashed"),
    "error":       ("#cf222e", "#f85149", "solid"),
    "blocked":     ("#bc4c00", "#f0883e", "solid"),
    "crashed":     ("#bf3989", "#db61a2", "solid, 6 px"),
    "interrupted": ("#7d4e00", "#bb8009", "solid"),
}


def lum(hex_):
    c = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


if __name__ == "__main__":
    bad = []
    for state, (light, dark, rail) in COLORS.items():
        row = []
        for theme, color in (("light", light), ("dark", dark)):
            r, rc = ratio(color, BG[theme]), ratio(color, CHIP_BG[theme])
            row.append(f"{theme} rail {r:4.1f}:1 chip-text {rc:4.1f}:1")
            if min(r, rc) < 4.5:  # the same color is chip text: hold it to the text bar
                bad.append((state, theme, round(min(r, rc), 2)))
        print(f"{state:12s} {rail:14s} " + "  ".join(row))
    print("below 4.5:1:", bad or "none")
    assert all(r >= 3.0 for _, _, r in bad), "a rail color is below 3:1"
