#!/usr/bin/env python3
"""Generate Excalidraw diagram source files for the copilot-advanced course.

Creates .excalidraw JSON source files for course diagrams. These can be opened
in Excalidraw for editing and exported to PNG/SVG for slide embeds.

Usage:
    python scripts/generate-excalidraw-diagrams.py
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SCRIPT_DIR, "..")
DIAGRAMS_DIR = "/Users/kangs/code/github/copilot-advanced/slides/slides-2-day-am-pm/day2-session2/diagrams"

# ── Colors (Excalidraw palette) ──────────────────────────────
BLUE = "#a5d8ff"
GREEN = "#b2f2bb"
RED = "#ffc9c9"
YELLOW = "#ffec99"
ORANGE = "#ffd8a8"
GRAY = "#dee2e6"
PURPLE = "#d0bfff"
WHITE = "#ffffff"
TRANSPARENT = "transparent"


# ── Scene builder ────────────────────────────────────────────
class Scene:
    """Builds an Excalidraw document from high-level primitives."""

    def __init__(self):
        self.elements = []
        self._n = 0

    def _id(self):
        self._n += 1
        return f"id{self._n:03d}"

    # ── primitives ──

    def _base(self, etype, eid, x, y, w, h, bg=TRANSPARENT,
              stroke="#1e1e1e", rnd=3, fill="hachure"):
        return {
            "id": eid, "type": etype,
            "x": x, "y": y, "width": w, "height": h, "angle": 0,
            "strokeColor": stroke, "backgroundColor": bg,
            "fillStyle": fill, "strokeWidth": 2, "strokeStyle": "solid",
            "roughness": 1, "opacity": 100,
            "groupIds": [], "frameId": None,
            "roundness": {"type": rnd} if rnd else None,
            "seed": abs(hash(eid)) % (2**31),
            "version": 1, "versionNonce": abs(hash(eid + "v")) % (2**31),
            "isDeleted": False, "boundElements": [],
            "updated": 1700000000000, "link": None, "locked": False,
        }

    def _txt_el(self, tid, x, y, w, h, text, fs=18, cid=None,
                align="center", valign="middle", color="#1e1e1e"):
        t = self._base("text", tid, x, y, w, h, rnd=None, stroke=color)
        t.update({"text": text, "fontSize": fs, "fontFamily": 1,
                  "textAlign": align, "verticalAlign": valign,
                  "containerId": cid, "originalText": text,
                  "lineHeight": 1.25, "fillStyle": "solid",
                  "backgroundColor": TRANSPARENT})
        return t

    # ── public API ──

    def box(self, x, y, w=160, h=60, label=None, bg=BLUE, fs=18):
        rid = self._id()
        r = self._base("rectangle", rid, x, y, w, h, bg=bg)
        self.elements.append(r)
        if label:
            tid = self._id()
            r["boundElements"] = [{"id": tid, "type": "text"}]
            self.elements.append(
                self._txt_el(tid, x + 10, y + 10, w - 20, h - 20, label, fs, cid=rid))
        return rid

    def diamond(self, x, y, w=160, h=80, label=None, bg=YELLOW, fs=16):
        did = self._id()
        d = self._base("diamond", did, x, y, w, h, bg=bg, rnd=2)
        self.elements.append(d)
        if label:
            tid = self._id()
            d["boundElements"] = [{"id": tid, "type": "text"}]
            self.elements.append(
                self._txt_el(tid, x + 10, y + 10, w - 20, h - 20, label, fs, cid=did))
        return did

    def ellipse(self, x, y, w=140, h=70, label=None, bg=BLUE, fs=16):
        eid = self._id()
        e = self._base("ellipse", eid, x, y, w, h, bg=bg, rnd=2)
        self.elements.append(e)
        if label:
            tid = self._id()
            e["boundElements"] = [{"id": tid, "type": "text"}]
            self.elements.append(
                self._txt_el(tid, x + 10, y + 10, w - 20, h - 20, label, fs, cid=eid))
        return eid

    def text(self, x, y, content, fs=20, color="#1e1e1e", align="left"):
        tid = self._id()
        lines = content.split("\n")
        w = max(len(l) for l in lines) * fs * 0.55
        h = len(lines) * fs * 1.25
        self.elements.append(
            self._txt_el(tid, x, y, w, h, content, fs, align=align, color=color))
        return tid

    def arrow(self, x, y, dx, dy, start=None, end=None, label=None):
        aid = self._id()
        a = self._base("arrow", aid, x, y, abs(dx), abs(dy), rnd=2)
        a.update({
            "points": [[0, 0], [dx, dy]], "lastCommittedPoint": None,
            "startBinding": {"elementId": start, "focus": 0, "gap": 5} if start else None,
            "endBinding": {"elementId": end, "focus": 0, "gap": 5} if end else None,
            "startArrowhead": None, "endArrowhead": "arrow",
        })
        self.elements.append(a)
        if label:
            self.text(x + dx / 2 - 20, y + dy / 2 - 22, label, fs=14)
        return aid

    def line(self, x, y, dx, dy, style="solid", color="#1e1e1e"):
        lid = self._id()
        el = self._base("line", lid, x, y, abs(dx), abs(dy), rnd=2, stroke=color)
        el.update({
            "points": [[0, 0], [dx, dy]], "lastCommittedPoint": None,
            "startBinding": None, "endBinding": None,
            "startArrowhead": None, "endArrowhead": None,
            "strokeStyle": style,
        })
        self.elements.append(el)
        return lid

    def save(self, filename):
        path = os.path.join(DIAGRAMS_DIR, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "type": "excalidraw", "version": 2,
            "source": "https://excalidraw.com",
            "elements": self.elements,
            "appState": {"gridSize": None, "viewBackgroundColor": WHITE},
            "files": {},
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  {filename} ({len(self.elements)} elements)")


# ── Diagram definitions ──────────────────────────────────────

def ch01_token_pipeline():
    """Token-to-Response Pipeline: Text → Tokens → Embeddings → Attention → Next Token"""
    s = Scene()
    y = 50
    b1 = s.box(50, y, 140, 60, "Text", bg=GRAY)
    b2 = s.box(240, y, 140, 60, "Tokens", bg=BLUE)
    b3 = s.box(430, y, 160, 60, "Embeddings", bg=GREEN)
    b4 = s.box(640, y, 150, 60, "Attention", bg=ORANGE)
    b5 = s.box(840, y, 160, 60, "Next Token", bg=PURPLE)
    s.arrow(190, y + 30, 50, 0, start=b1, end=b2)
    s.arrow(380, y + 30, 50, 0, start=b2, end=b3)
    s.arrow(590, y + 30, 50, 0, start=b3, end=b4)
    s.arrow(790, y + 30, 50, 0, start=b4, end=b5)
    s.text(350, 140, "The mental model: how your text becomes an LLM response", fs=14, color="#868e96")
    s.save("ch01-token-pipeline.excalidraw")


def ch01_ai_eras():
    """AI Eras at a Glance: 4-era horizontal timeline with growing bubbles."""
    s = Scene()
    s.ellipse(50, 60, 100, 60, "Rules\n(1950s)", bg=GRAY, fs=14)
    s.ellipse(200, 45, 120, 75, "ML\n(1990s)", bg=BLUE, fs=14)
    s.ellipse(370, 30, 150, 90, "Deep Learning\n(2010s)", bg=GREEN, fs=14)
    s.ellipse(570, 15, 180, 110, "GenAI\n(2017+)", bg=ORANGE, fs=14)
    s.arrow(150, 90, 50, 0)
    s.arrow(320, 82, 50, 0)
    s.arrow(520, 75, 50, 0)
    s.text(250, 150, "Increasing capability →", fs=14, color="#868e96")
    s.save("ch01-ai-eras.excalidraw")


def ch01_transformer_full():
    """Full Transformer Architecture (~12 elements)."""
    s = Scene()
    cx = 200
    b1 = s.box(cx, 50, 200, 50, "Input Embedding", bg=GRAY)
    b2 = s.box(cx, 130, 200, 50, "Positional Encoding", bg=GRAY)
    b3 = s.box(cx, 210, 200, 50, "Multi-Head Attention\n(Q / K / V)", bg=ORANGE, fs=14)
    b4 = s.box(cx, 290, 200, 50, "Add & Norm", bg=YELLOW)
    b5 = s.box(cx, 370, 200, 50, "Feed-Forward", bg=GREEN)
    b6 = s.box(cx, 450, 200, 50, "Add & Norm", bg=YELLOW)
    b7 = s.box(cx, 530, 200, 50, "Output Probabilities", bg=PURPLE)
    for i, (a, b) in enumerate([(b1, b2), (b2, b3), (b3, b4), (b4, b5), (b5, b6), (b6, b7)]):
        s.arrow(cx + 100, 100 + i * 80, 0, 30, start=a, end=b)
    s.text(430, 210, "← Self-attention:\nWhich words matter?", fs=14, color="#868e96")
    s.text(430, 370, "← Refines each\ntoken representation", fs=14, color="#868e96")
    s.text(cx + 20, 600, "Full Transformer block (simplified)", fs=14, color="#868e96")
    s.save("ch01-transformer-full.excalidraw")


def ch02_model_selection():
    """Model Selection Flowchart: decision tree with diamonds and boxes."""
    s = Scene()
    d1 = s.diamond(250, 30, 180, 90, "Budget\ntight?", bg=YELLOW)
    b1 = s.box(50, 180, 160, 50, "Mini / Flash", bg=GREEN, fs=16)
    d2 = s.diamond(300, 180, 180, 90, "Privacy\nrequired?", bg=YELLOW)
    b2 = s.box(100, 330, 180, 50, "Llama / Mistral", bg=GREEN, fs=16)
    d3 = s.diamond(350, 330, 180, 90, "Complex\nreasoning?", bg=YELLOW)
    b3 = s.box(560, 330, 180, 50, "GPT-4o / Sonnet", bg=BLUE, fs=16)
    b4 = s.box(350, 470, 180, 50, "GPT-4o-mini", bg=BLUE, fs=16)
    s.text(155, 130, "yes", fs=14)
    s.text(420, 130, "no", fs=14)
    s.text(200, 280, "yes", fs=14)
    s.text(470, 280, "no", fs=14)
    s.text(540, 400, "yes", fs=14)
    s.text(400, 440, "no", fs=14)
    s.arrow(260, 120, -80, 60, start=d1, end=b1)
    s.arrow(390, 120, 0, 60, start=d1, end=d2)
    s.arrow(300, 270, -110, 60, start=d2, end=b2)
    s.arrow(440, 270, 0, 60, start=d2, end=d3)
    s.arrow(530, 375, 30, 0, start=d3, end=b3)
    s.arrow(440, 420, 0, 50, start=d3, end=b4)
    s.save("ch02-model-selection.excalidraw")


def ch02_open_closed():
    """Open vs Closed Spectrum: horizontal scale with model dots."""
    s = Scene()
    s.line(50, 100, 700, 0, color="#495057")
    s.text(50, 115, "Fully Open", fs=14, color="#2f9e44")
    s.text(670, 115, "Fully Closed", fs=14, color="#1971c2")
    models = [
        (80, "Llama", GREEN), (200, "Mistral", GREEN),
        (400, "Gemini", YELLOW), (550, "Claude", BLUE), (680, "GPT-4", BLUE),
    ]
    for mx, name, bg in models:
        s.ellipse(mx - 20, 70, 40, 40, bg=bg)
        s.text(mx - 20, 40, name, fs=14)
    s.text(50, 150, "your infra / full control", fs=12, color="#868e96")
    s.text(580, 150, "their API / managed", fs=12, color="#868e96")
    s.save("ch02-open-closed.excalidraw")


def ch03_api_anatomy():
    """Anatomy of an API Call: Your Code → Request → LLM API → Response."""
    s = Scene()
    y = 50
    b1 = s.box(50, y, 140, 60, "Your Code", bg=GRAY)
    b2 = s.box(260, y, 160, 60, "Request\nmodel + messages", bg=BLUE, fs=14)
    b3 = s.box(490, y, 140, 60, "LLM API", bg=ORANGE)
    b4 = s.box(700, y, 170, 60, "Response\ncontent + usage", bg=GREEN, fs=14)
    s.arrow(190, y + 30, 70, 0, start=b1, end=b2)
    s.arrow(420, y + 30, 70, 0, start=b2, end=b3)
    s.arrow(630, y + 30, 70, 0, start=b3, end=b4)
    s.save("ch03-api-anatomy.excalidraw")


def ch03_three_roles():
    """The Three Roles: stacked speech bubbles for system, user, assistant."""
    s = Scene()
    x = 150
    s.box(x, 40, 350, 60, "System: \"You are a Python expert...\"", bg=GRAY, fs=14)
    s.box(x, 130, 350, 60, "User: \"Explain this function...\"", bg=BLUE, fs=14)
    s.box(x, 220, 350, 60, "Assistant: \"This function calculates...\"", bg=GREEN, fs=14)
    s.text(50, 100, "messages[]", fs=16, color="#495057")
    s.line(130, 50, 0, 220, color="#495057")
    s.save("ch03-three-roles.excalidraw")


def ch04_strengths_weaknesses():
    """Strengths vs Weaknesses T-chart."""
    s = Scene()
    s.text(100, 20, "Strengths", fs=22, color="#2f9e44")
    s.text(350, 20, "Weaknesses", fs=22, color="#e03131")
    s.line(300, 20, 0, 250, color="#495057")
    strengths = ["✓ Summarize", "✓ Classify", "✓ Generate Code"]
    weaknesses = ["✗ Math", "✗ Recent Facts", "✗ Counting"]
    for i, t in enumerate(strengths):
        s.text(80, 70 + i * 55, t, fs=18, color="#2f9e44")
    for i, t in enumerate(weaknesses):
        s.text(330, 70 + i * 55, t, fs=18, color="#e03131")
    s.save("ch04-strengths-weaknesses.excalidraw")


def ch04_preprocessing():
    """Input Preprocessing Pipeline: Raw Text → Clean → Strip PII → Chunk → Ready."""
    s = Scene()
    y = 50
    b1 = s.box(30, y, 130, 60, "Raw Text", bg=GRAY)
    b2 = s.box(210, y, 110, 60, "Clean", bg=BLUE)
    b3 = s.box(370, y, 130, 60, "Strip PII", bg=ORANGE)
    b4 = s.box(550, y, 110, 60, "Chunk", bg=YELLOW)
    b5 = s.box(710, y, 150, 60, "Ready for API", bg=GREEN)
    s.arrow(160, y + 30, 50, 0, start=b1, end=b2)
    s.arrow(320, y + 30, 50, 0, start=b2, end=b3)
    s.arrow(500, y + 30, 50, 0, start=b3, end=b4)
    s.arrow(660, y + 30, 50, 0, start=b4, end=b5)
    s.save("ch04-preprocessing.excalidraw")


def ch04_capability_matrix():
    """Full Capability Matrix (~15 elements): tasks x reliability x model x cost."""
    s = Scene()
    s.text(50, 20, "LLM Capability Matrix", fs=22, color="#1e1e1e")
    headers = ["Task", "Reliability", "Best Model", "Cost Tier"]
    for i, h in enumerate(headers):
        s.box(50 + i * 180, 60, 170, 40, h, bg=GRAY, fs=14)
    rows = [
        ("Summarization", "High", "Mini", "$", GREEN),
        ("Code Generation", "High", "Standard", "$$", GREEN),
        ("Classification", "High", "Mini", "$", GREEN),
        ("Translation", "High", "Standard", "$$", GREEN),
        ("Rewriting", "High", "Mini", "$", GREEN),
        ("Multi-step Reason", "Medium", "Premium", "$$$", YELLOW),
        ("Extraction (JSON)", "Medium", "Standard", "$$", YELLOW),
        ("Math / Counting", "Low", "Premium+Tools", "$$$", RED),
        ("Recent Events", "Low", "RAG Required", "$$", RED),
        ("Exact String Ops", "Low", "Code Instead", "-", RED),
    ]
    for i, (task, rel, model, cost, color) in enumerate(rows):
        ry = 110 + i * 45
        s.box(50, ry, 170, 40, task, bg=WHITE, fs=13)
        s.box(230, ry, 170, 40, rel, bg=color, fs=13)
        s.box(410, ry, 170, 40, model, bg=WHITE, fs=13)
        s.box(590, ry, 170, 40, cost, bg=WHITE, fs=13)
    s.save("ch04-capability-matrix.excalidraw")


def ch04_where_llms_excel():
    """The Sweet Spot: reliable when the answer is in the prompt, risky when it
    must be recalled from training memory."""
    s = Scene()
    s.text(40, 15, "The Sweet Spot: When the Answer Is Already There", fs=20,
           color="#1e1e1e")

    # Left panel: reliable zone
    s.box(40, 70, 380, 400, bg=GREEN)
    s.text(70, 88, "Answer is in the prompt", fs=16, color="#2f9e44")
    s.text(70, 112, "or follows a known template  →  Reliable", fs=13,
           color="#2f9e44")
    reliable = ["Summarize", "Classify", "Rewrite / edit", "Q&A over given text",
                "Format conversion", "Translate", "Generate code"]
    for i, t in enumerate(reliable):
        s.box(70, 150 + i * 44, 320, 34, t, bg=WHITE, fs=14)

    # Right panel: risky zone
    s.box(460, 70, 340, 400, bg=RED)
    s.text(490, 88, "Needs facts recalled", fs=16, color="#e03131")
    s.text(490, 112, "from training memory  →  Risky", fs=13, color="#e03131")
    risky = ["Recent events", "Exact counting", "Specific citations",
             "Precise arithmetic", "Niche trivia"]
    for i, t in enumerate(risky):
        s.box(490, 150 + i * 60, 280, 40, t, bg=WHITE, fs=14)

    s.text(40, 488,
           "Reliability drops the more the model has to rely on memory instead of the prompt.",
           fs=13, color="#868e96")
    s.save("ch04-where-llms-excel.excalidraw")


def ch05_building_blocks():
    """The 4 Building Blocks: stacked LEGO-style bricks."""
    s = Scene()
    x = 150
    s.box(x, 40, 280, 55, "Instruction", bg=BLUE, fs=18)
    s.box(x, 105, 280, 55, "Context", bg=GREEN, fs=18)
    s.box(x, 170, 280, 55, "Input Data", bg=YELLOW, fs=18)
    s.box(x, 235, 280, 55, "Output Format", bg=ORANGE, fs=18)
    s.text(460, 140, "Your\nPrompt", fs=20, color="#495057")
    s.line(445, 50, 0, 230, color="#495057")
    s.save("ch05-building-blocks.excalidraw")


def ch05_bad_vs_good():
    """Bad Prompt → Good Prompt: two-panel side-by-side comparison."""
    s = Scene()
    s.box(30, 30, 250, 180, bg=RED)
    s.text(80, 50, "Bad Prompt", fs=18, color="#e03131")
    s.text(50, 90, "\"Help me with\nthis code\"", fs=16)
    s.text(70, 160, "→ Rambling essay", fs=14, color="#868e96")

    s.box(360, 30, 280, 180, bg=GREEN)
    s.text(400, 50, "Good Prompt", fs=18, color="#2f9e44")
    s.text(380, 90, "Instruction + Context\n+ Input + Output Format", fs=14)
    s.text(400, 160, "→ Clean JSON output", fs=14, color="#868e96")

    s.arrow(290, 120, 60, 0, label="Prompt\nEngineering")
    s.save("ch05-bad-vs-good.excalidraw")


def ch06_technique_ladder():
    """Technique Ladder: ascending staircase with 4 labeled steps."""
    s = Scene()
    steps = [
        (50, 280, "Zero-Shot", "Direct", GRAY),
        (200, 210, "Few-Shot", "Examples", BLUE),
        (350, 140, "Chain-of-Thought", "Reasoning", GREEN),
        (500, 70, "Role + Structured", "Persona", ORANGE),
    ]
    for x, y, label, note, bg in steps:
        s.box(x, y, 160, 55, label, bg=bg, fs=14)
        s.text(x + 30, y + 60, note, fs=12, color="#868e96")
    s.text(650, 60, "↑ Increasing\nreliability", fs=14, color="#495057")
    s.save("ch06-technique-ladder.excalidraw")


def ch06_few_shot_layout():
    """Few-Shot Prompt Layout: highlighted zones of a well-built prompt."""
    s = Scene()
    x = 80
    s.box(x, 30, 350, 50, "System Message", bg=GRAY, fs=14)
    s.box(x, 90, 350, 50, "Example 1: input → output", bg=BLUE, fs=14)
    s.box(x, 150, 350, 50, "Example 2: input → output", bg=BLUE, fs=14)
    s.box(x, 210, 350, 50, "Example 3: edge case → output", bg=ORANGE, fs=14)
    s.box(x, 270, 350, 50, "Actual Input", bg=GREEN, fs=14)
    s.text(450, 150, "← Cover edge cases!", fs=13, color="#e8590c")
    s.save("ch06-few-shot-layout.excalidraw")


def ch06_technique_matrix():
    """Technique Comparison Matrix (~10 elements)."""
    s = Scene()
    s.text(50, 20, "Technique Comparison Matrix", fs=20)
    headers = ["Technique", "When to Use", "Pros", "Cons"]
    for i, h in enumerate(headers):
        s.box(50 + i * 190, 60, 180, 35, h, bg=GRAY, fs=13)
    rows = [
        ("Zero-Shot", "Simple tasks", "No examples needed", "Inconsistent format"),
        ("Few-Shot", "Classification", "High accuracy", "Uses more tokens"),
        ("Chain-of-Thought", "Multi-step reasoning", "Better logic", "Longer output"),
        ("Role Prompting", "Domain expertise", "Focused style", "May hallucinate role"),
        ("Structured Output", "JSON / tables", "Parseable", "May truncate"),
        ("Combined", "Production systems", "Most reliable", "Complex prompts"),
    ]
    for i, (tech, when, pros, cons) in enumerate(rows):
        ry = 105 + i * 40
        s.box(50, ry, 180, 35, tech, bg=BLUE, fs=12)
        s.box(240, ry, 180, 35, when, bg=WHITE, fs=12)
        s.box(430, ry, 180, 35, pros, bg=GREEN, fs=12)
        s.box(620, ry, 180, 35, cons, bg=RED, fs=12)
    s.save("ch06-technique-matrix.excalidraw")


def ch07_temperature_dial():
    """The Temperature Dial: circular gauge with three labeled zones."""
    s = Scene()
    s.ellipse(150, 30, 250, 250, bg=WHITE)
    s.text(200, 60, "0.0–0.3", fs=16, color="#1971c2")
    s.text(170, 90, "Deterministic", fs=13, color="#1971c2")
    s.text(170, 110, "code, classification", fs=11, color="#868e96")
    s.text(200, 150, "0.3–0.7", fs=16, color="#2f9e44")
    s.text(185, 180, "Balanced", fs=13, color="#2f9e44")
    s.text(180, 200, "chat, general", fs=11, color="#868e96")
    s.text(200, 230, "0.8–1.5", fs=16, color="#e8590c")
    s.text(180, 255, "Creative", fs=13, color="#e8590c")
    s.text(165, 275, "brainstorming, writing", fs=11, color="#868e96")
    s.text(200, 310, "Temperature", fs=20, color="#495057")
    s.save("ch07-temperature-dial.excalidraw")


def ch07_control_panel():
    """Parameter Control Panel: dashboard with 6 sliders/knobs."""
    s = Scene()
    s.text(150, 15, "LLM Parameter Control Panel", fs=20)
    params = [
        ("temperature", "0.0 – 2.0", BLUE),
        ("top_p", "0.0 – 1.0", BLUE),
        ("max_tokens", "1 – 128000", GREEN),
        ("frequency_penalty", "-2.0 – 2.0", ORANGE),
        ("presence_penalty", "-2.0 – 2.0", ORANGE),
        ("seed", "integer", GRAY),
    ]
    for i, (name, rng, bg) in enumerate(params):
        col = i % 2
        row = i // 2
        x = 50 + col * 280
        y = 60 + row * 85
        s.box(x, y, 250, 35, name, bg=bg, fs=15)
        s.box(x, y + 40, 250, 30, rng, bg=WHITE, fs=13)
    s.save("ch07-control-panel.excalidraw")


def ch08_iteration_spiral():
    """The Iteration Spiral: circular loop with 4 stations."""
    s = Scene()
    b1 = s.box(200, 30, 130, 50, "Write", bg=BLUE)
    b2 = s.box(380, 130, 110, 50, "Test", bg=GREEN)
    b3 = s.box(200, 230, 130, 50, "Analyze", bg=ORANGE)
    b4 = s.box(40, 130, 120, 50, "Refine", bg=YELLOW)
    s.arrow(330, 55, 50, 50, start=b1, end=b2)
    s.arrow(435, 180, -50, 50, start=b2, end=b3)
    s.arrow(200, 255, -50, -50, start=b3, end=b4)
    s.arrow(40, 155, 50, -50, start=b4, end=b1)
    s.text(190, 140, "v1 → v2 → v3", fs=14, color="#868e96")
    s.save("ch08-iteration-spiral.excalidraw")


def ch08_golden_dataset():
    """Golden Dataset Concept: mini-table with Input, Expected, Actual, Pass/Fail."""
    s = Scene()
    headers = ["Input", "Expected", "Actual", "Pass/Fail"]
    for i, h in enumerate(headers):
        s.box(50 + i * 150, 40, 140, 40, h, bg=GRAY, fs=14)
    rows = [
        ("Great product!", "POSITIVE", "POSITIVE", "✓", GREEN),
        ("Terrible service", "NEGATIVE", "NEGATIVE", "✓", GREEN),
        ("It was okay", "NEUTRAL", "POSITIVE", "✗", RED),
    ]
    for j, (inp, exp, act, pf, bg) in enumerate(rows):
        y = 90 + j * 45
        s.box(50, y, 140, 40, inp, bg=WHITE, fs=12)
        s.box(200, y, 140, 40, exp, bg=WHITE, fs=12)
        s.box(350, y, 140, 40, act, bg=WHITE, fs=12)
        s.box(500, y, 140, 40, pf, bg=bg, fs=16)
    s.save("ch08-golden-dataset.excalidraw")


def ch08_eval_pipeline():
    """Complete Evaluation Pipeline (~10 elements)."""
    s = Scene()
    y = 40
    b1 = s.box(30, y, 140, 50, "Golden Dataset", bg=GRAY)
    b2 = s.box(210, y, 140, 50, "Prompt Runner", bg=BLUE)
    b3 = s.box(390, y, 150, 50, "Output Collector", bg=BLUE)
    s.arrow(170, y + 25, 40, 0, start=b1, end=b2)
    s.arrow(350, y + 25, 40, 0, start=b2, end=b3)
    y2 = 130
    b4 = s.box(100, y2, 140, 50, "Exact Match", bg=GREEN)
    b5 = s.box(270, y2, 130, 50, "Fuzzy Match", bg=YELLOW)
    b6 = s.box(430, y2, 140, 50, "LLM-as-Judge", bg=ORANGE)
    s.arrow(460, y + 50, 0, 30, start=b3, end=b6)
    s.arrow(350, y + 50, -80, 30, start=b3, end=b5)
    s.arrow(270, y + 50, -100, 30, start=b3, end=b4)
    y3 = 220
    b7 = s.box(200, y3, 170, 50, "Report Generator", bg=PURPLE)
    b8 = s.box(420, y3, 180, 50, "Regression Tracker", bg=RED)
    s.arrow(170, y2 + 50, 100, 40, start=b4, end=b7)
    s.arrow(335, y2 + 50, 0, 40, start=b5, end=b7)
    s.arrow(500, y2 + 50, 0, 40, start=b6, end=b8)
    s.save("ch08-eval-pipeline.excalidraw")


def ch09_stateless_stateful():
    """Stateless vs Stateful: two-panel sketch."""
    s = Scene()
    s.text(80, 20, "Stateless", fs=18, color="#e03131")
    s.box(30, 50, 200, 50, "Turn 1: request → response", bg=RED, fs=12)
    s.box(30, 120, 200, 50, "Turn 2: request → response", bg=RED, fs=12)
    s.text(70, 180, "(no connection)", fs=13, color="#868e96")

    s.line(270, 20, 0, 180, style="dashed")

    s.text(340, 20, "Your Code Adds State", fs=18, color="#2f9e44")
    s.box(300, 50, 250, 50, "Turn 1: request → response", bg=GREEN, fs=12)
    s.box(300, 120, 250, 50, "Turn 2: [history] + request → response", bg=GREEN, fs=11)
    s.box(570, 70, 100, 80, "messages[]", bg=YELLOW, fs=13)
    s.arrow(550, 95, 20, 0)
    s.save("ch09-stateless-stateful.excalidraw")


def ch09_context_filling():
    """Context Window Filling Up: vertical stack growing toward token limit."""
    s = Scene()
    x = 100
    s.box(x, 300, 250, 40, "System Message", bg=GRAY, fs=14)
    s.box(x, 255, 250, 40, "User 1", bg=BLUE, fs=14)
    s.box(x, 210, 250, 40, "Assistant 1", bg=GREEN, fs=14)
    s.box(x, 165, 250, 40, "User 2", bg=BLUE, fs=14)
    s.box(x, 120, 250, 40, "Assistant 2", bg=GREEN, fs=14)
    s.box(x, 75, 250, 40, "User 3 ...", bg=BLUE, fs=14)
    s.line(60, 50, 330, 0, style="dashed", color="#e03131")
    s.text(400, 35, "128K token limit", fs=14, color="#e03131")
    s.text(400, 200, "← Truncate (scissors)", fs=13, color="#868e96")
    s.text(400, 240, "← Or summarize", fs=13, color="#868e96")
    s.save("ch09-context-filling.excalidraw")


def ch10_text_to_vector():
    """Text to Vector Space: sentence → embed → dot in 2D scatter."""
    s = Scene()
    s.box(30, 80, 200, 60, "\"The cat sat\non the mat\"", bg=GRAY, fs=14)
    s.arrow(230, 110, 70, 0, label="Embed")
    s.box(320, 50, 350, 200, bg=WHITE)
    s.text(330, 55, "Dimension 2 ↑", fs=12, color="#868e96")
    s.text(550, 230, "Dimension 1 →", fs=12, color="#868e96")
    s.ellipse(400, 120, 16, 16, bg=BLUE)
    s.text(420, 115, "cat sat", fs=12, color="#1971c2")
    s.ellipse(430, 140, 16, 16, bg=GREEN)
    s.text(450, 135, "kitten rested", fs=12, color="#2f9e44")
    s.ellipse(560, 200, 16, 16, bg=RED)
    s.text(580, 195, "database schema", fs=12, color="#e03131")
    s.save("ch10-text-to-vector.excalidraw")


def ch10_similarity_search():
    """Similarity Search: scatter plot with query Q and top-3 highlighted."""
    s = Scene()
    s.box(50, 30, 450, 300, bg=WHITE)
    s.text(60, 335, "Similarity Search: Top-K Retrieval", fs=14, color="#868e96")
    dots = [
        (150, 100, GRAY), (200, 180, GREEN), (250, 80, GREEN),
        (280, 200, GREEN), (350, 150, GRAY), (400, 100, GRAY),
        (120, 250, GRAY), (380, 250, GRAY),
    ]
    for dx, dy, bg in dots:
        s.ellipse(dx, dy, 14, 14, bg=bg)
    s.ellipse(260, 150, 20, 20, bg=ORANGE)
    s.text(285, 148, "Q", fs=16, color="#e8590c")
    s.text(460, 80, "● Stored chunks", fs=12, color="#868e96")
    s.text(460, 100, "● Top-3 results", fs=12, color="#2f9e44")
    s.text(460, 120, "★ Query", fs=12, color="#e8590c")
    s.save("ch10-similarity-search.excalidraw")


def ch10_chunking_indexing():
    """Full Chunking + Indexing Pipeline (~9 elements)."""
    s = Scene()
    y = 50
    b1 = s.box(20, y, 120, 50, "Document", bg=GRAY)
    b2 = s.box(170, y, 100, 50, "Loader", bg=BLUE)
    b3 = s.box(300, y, 100, 50, "Splitter", bg=BLUE)
    s.arrow(140, y + 25, 30, 0, start=b1, end=b2)
    s.arrow(270, y + 25, 30, 0, start=b2, end=b3)
    y2 = 140
    b4 = s.box(50, y2, 120, 50, "Fixed", bg=GREEN, fs=14)
    b5 = s.box(190, y2, 120, 50, "Semantic", bg=GREEN, fs=14)
    b6 = s.box(330, y2, 120, 50, "Overlap", bg=GREEN, fs=14)
    s.arrow(350, y + 50, -50, 40, start=b3)
    s.arrow(350, y + 50, -100, 40, start=b3)
    s.arrow(350, y + 50, -150, 40, start=b3)
    y3 = 230
    b7 = s.box(130, y3, 170, 50, "Embedding Model", bg=ORANGE)
    b8 = s.box(340, y3, 130, 50, "Vector DB", bg=PURPLE)
    b9 = s.box(510, y3, 100, 50, "Index", bg=GREEN)
    s.arrow(110, y2 + 50, 90, 40, start=b4, end=b7)
    s.arrow(250, y2 + 50, 0, 40, start=b5, end=b7)
    s.arrow(390, y2 + 50, -50, 40, start=b6, end=b7)
    s.arrow(300, y3 + 25, 40, 0, start=b7, end=b8)
    s.arrow(470, y3 + 25, 40, 0, start=b8, end=b9)
    s.save("ch10-chunking-indexing.excalidraw")


def ch11_rag_5_steps():
    """RAG in 5 Steps: horizontal pipeline with user query entry."""
    s = Scene()
    y = 60
    b1 = s.box(30, y, 120, 50, "Documents", bg=GRAY)
    b2 = s.box(180, y, 100, 50, "Chunk", bg=BLUE)
    b3 = s.box(310, y, 100, 50, "Embed", bg=GREEN)
    b4 = s.box(440, y, 100, 50, "Store", bg=PURPLE)
    b5 = s.box(580, y, 170, 50, "Retrieve +\nGenerate", bg=ORANGE, fs=14)
    s.arrow(150, y + 25, 30, 0, start=b1, end=b2)
    s.arrow(280, y + 25, 30, 0, start=b2, end=b3)
    s.arrow(410, y + 25, 30, 0, start=b3, end=b4)
    s.arrow(540, y + 25, 40, 0, start=b4, end=b5)
    uq = s.box(550, 160, 140, 40, "User Query", bg=YELLOW, fs=14)
    s.arrow(620, 155, 0, -45, start=uq, end=b5)
    s.save("ch11-rag-5-steps.excalidraw")


def ch11_rag_sandwich():
    """RAG Prompt Sandwich: layered prompt structure like a burger."""
    s = Scene()
    x = 100
    s.box(x, 40, 320, 55, "System Message\n\"Answer ONLY from context\"", bg=ORANGE, fs=14)
    s.box(x, 110, 320, 80, "Retrieved Context\n[chunk 1: source.md]\n[chunk 2: api.md]", bg=GREEN, fs=13)
    s.box(x, 205, 320, 55, "User Query\n\"How do I authenticate?\"", bg=BLUE, fs=14)
    s.text(440, 80, "← Top bun", fs=13, color="#868e96")
    s.text(440, 140, "← Filling", fs=13, color="#868e96")
    s.text(440, 220, "← Bottom bun", fs=13, color="#868e96")
    s.save("ch11-rag-sandwich.excalidraw")


def ch11_rag_full():
    """Complete RAG Architecture with Evaluation (~12 elements)."""
    s = Scene()
    s.text(200, 10, "RAG Architecture with Evaluation", fs=18)
    y = 50
    b1 = s.box(20, y, 110, 40, "Load", bg=GRAY, fs=14)
    b2 = s.box(150, y, 110, 40, "Chunk", bg=BLUE, fs=14)
    b3 = s.box(280, y, 110, 40, "Embed", bg=GREEN, fs=14)
    b4 = s.box(410, y, 110, 40, "Store", bg=PURPLE, fs=14)
    s.arrow(130, y + 20, 20, 0, start=b1, end=b2)
    s.arrow(260, y + 20, 20, 0, start=b2, end=b3)
    s.arrow(390, y + 20, 20, 0, start=b3, end=b4)
    s.text(20, 100, "Ingestion Pipeline ↑", fs=12, color="#868e96")

    y2 = 140
    b5 = s.box(20, y2, 110, 40, "Query", bg=YELLOW, fs=14)
    b6 = s.box(150, y2, 110, 40, "Embed Q", bg=GREEN, fs=14)
    b7 = s.box(280, y2, 110, 40, "Search", bg=BLUE, fs=14)
    b8 = s.box(410, y2, 110, 40, "Augment", bg=ORANGE, fs=14)
    b9 = s.box(540, y2, 110, 40, "Generate", bg=ORANGE, fs=14)
    s.arrow(130, y2 + 20, 20, 0, start=b5, end=b6)
    s.arrow(260, y2 + 20, 20, 0, start=b6, end=b7)
    s.arrow(390, y2 + 20, 20, 0, start=b7, end=b8)
    s.arrow(520, y2 + 20, 20, 0, start=b8, end=b9)
    s.text(20, 190, "Query Pipeline ↑", fs=12, color="#868e96")

    y3 = 230
    b10 = s.box(100, y3, 140, 40, "Retrieval Recall", bg=GREEN, fs=13)
    b11 = s.box(260, y3, 160, 40, "Answer Accuracy", bg=YELLOW, fs=13)
    b12 = s.box(440, y3, 140, 40, "Faithfulness", bg=RED, fs=13)
    s.arrow(650, y2 + 20, 0, 50)
    s.text(20, 280, "Evaluation Loop ↑ — feedback to chunking and prompt tuning", fs=12, color="#868e96")
    s.save("ch11-rag-full.excalidraw")


def ch12_defense_rings():
    """Defense-in-Depth Rings: concentric circles from outside in."""
    s = Scene()
    cx, cy = 250, 200
    s.ellipse(cx - 200, cy - 200, 400, 400, bg=RED, fs=14)
    s.text(cx - 50, cy - 185, "Input Sanitization", fs=13, color="#1e1e1e")
    s.ellipse(cx - 140, cy - 140, 280, 280, bg=ORANGE, fs=14)
    s.text(cx - 70, cy - 120, "System Message\nAuthority", fs=12, color="#1e1e1e")
    s.ellipse(cx - 80, cy - 80, 160, 160, bg=YELLOW, fs=14)
    s.text(cx - 55, cy - 60, "Output\nValidation", fs=12, color="#1e1e1e")
    s.ellipse(cx - 35, cy - 35, 70, 70, bg=GREEN)
    s.text(cx - 30, cy - 12, "Your\nApp", fs=12, color="#1e1e1e")
    s.save("ch12-defense-rings.excalidraw")


def ch12_injection_flow():
    """Prompt Injection Attack Flow: before/after with defense."""
    s = Scene()
    s.text(50, 10, "Without Defense:", fs=14, color="#e03131")
    a1 = s.box(30, 40, 130, 50, "Attacker", bg=RED, fs=14)
    a2 = s.box(210, 40, 160, 50, "\"Ignore all\ninstructions...\"", bg=RED, fs=12)
    a3 = s.box(420, 40, 100, 50, "LLM", bg=ORANGE, fs=14)
    a4 = s.box(570, 40, 130, 50, "Leaked Data!", bg=RED, fs=14)
    s.arrow(160, 65, 50, 0, start=a1, end=a2)
    s.arrow(370, 65, 50, 0, start=a2, end=a3)
    s.arrow(520, 65, 50, 0, start=a3, end=a4)

    s.text(50, 130, "With Defense:", fs=14, color="#2f9e44")
    b1 = s.box(30, 160, 130, 50, "Attacker", bg=RED, fs=14)
    b2 = s.box(210, 160, 120, 50, "Input Filter", bg=GREEN, fs=14)
    b3 = s.box(380, 160, 100, 50, "LLM", bg=ORANGE, fs=14)
    b4 = s.box(530, 160, 130, 50, "Safe Response", bg=GREEN, fs=14)
    s.arrow(160, 185, 50, 0, start=b1, end=b2)
    s.arrow(330, 185, 50, 0, start=b2, end=b3)
    s.arrow(480, 185, 50, 0, start=b3, end=b4)
    s.text(220, 220, "Attack blocked ↑", fs=12, color="#2f9e44")
    s.save("ch12-injection-flow.excalidraw")


def ch13_token_cost():
    """Token Cost Breakdown: side-by-side bar chart."""
    s = Scene()
    s.box(80, 180, 100, 60, "Input\n$2.50/1M", bg=GREEN, fs=14)
    s.box(250, 40, 100, 200, "Output\n$10/1M", bg=RED, fs=14)
    s.text(130, 260, "Output costs 4x more!", fs=16, color="#e03131")
    s.line(60, 240, 320, 0, color="#495057")
    s.save("ch13-token-cost.excalidraw")


def ch13_retry_backoff():
    """Retry with Exponential Backoff: horizontal timeline."""
    s = Scene()
    y = 80
    s.box(20, y, 90, 45, "Call 1", bg=RED, fs=14)
    s.text(120, y + 10, "wait\n1s", fs=12, color="#868e96")
    s.box(160, y, 100, 45, "Retry 1", bg=RED, fs=14)
    s.text(275, y + 10, "wait\n2s", fs=12, color="#868e96")
    s.box(320, y, 100, 45, "Retry 2", bg=RED, fs=14)
    s.text(440, y + 10, "wait\n4s", fs=12, color="#868e96")
    s.box(500, y, 110, 45, "Retry 3 ✓", bg=GREEN, fs=14)
    s.arrow(110, y + 22, 50, 0)
    s.arrow(260, y + 22, 60, 0)
    s.arrow(420, y + 22, 80, 0)
    s.text(150, 150, "Exponential backoff: 1s → 2s → 4s → success", fs=14, color="#868e96")
    s.save("ch13-retry-backoff.excalidraw")


def ch13_cost_decision_tree():
    """Cost Optimization Decision Tree (~10 elements)."""
    s = Scene()
    d1 = s.diamond(250, 20, 170, 80, "High\ncost?", bg=YELLOW)
    d2 = s.diamond(100, 140, 180, 80, "Repeated\nqueries?", bg=YELLOW, fs=14)
    b1 = s.box(20, 260, 120, 45, "Add Cache\n(−40%)", bg=GREEN, fs=13)
    d3 = s.diamond(250, 260, 170, 80, "Simple\ntask?", bg=YELLOW, fs=14)
    b2 = s.box(150, 380, 150, 45, "Downgrade Model\n(−60%)", bg=GREEN, fs=13)
    d4 = s.diamond(380, 380, 180, 80, "Long\nresponses?", bg=YELLOW, fs=14)
    b3 = s.box(300, 500, 160, 45, "Reduce max_tokens\n(−30%)", bg=GREEN, fs=13)
    b4 = s.box(520, 500, 130, 45, "Batch\n(−20%)", bg=GREEN, fs=13)
    s.arrow(250, 100, -60, 40, start=d1, end=d2, label="yes")
    s.arrow(100, 220, -20, 40, start=d2, end=b1, label="yes")
    s.arrow(190, 220, 100, 40, start=d2, end=d3, label="no")
    s.arrow(250, 340, -40, 40, start=d3, end=b2, label="yes")
    s.arrow(335, 340, 100, 40, start=d3, end=d4, label="no")
    s.arrow(380, 460, -20, 40, start=d4, end=b3, label="yes")
    s.arrow(470, 460, 100, 40, start=d4, end=b4, label="no")
    s.save("ch13-cost-decision-tree.excalidraw")


def ch14_human_in_loop():
    """Human-in-the-Loop Modes: three horizontal lanes."""
    s = Scene()
    modes = [
        ("Recommend", "AI suggests → Human decides", GREEN, 40),
        ("Augment", "AI drafts → Human edits", YELLOW, 120),
        ("Automate", "AI acts → Human audits", RED, 200),
    ]
    for name, desc, bg, y in modes:
        s.box(50, y, 130, 50, name, bg=bg, fs=16)
        s.arrow(180, y + 25, 50, 0)
        s.box(240, y, 280, 50, desc, bg=WHITE, fs=14)
    s.text(540, 40, "Low risk", fs=13, color="#2f9e44")
    s.text(540, 200, "High risk", fs=13, color="#e03131")
    s.arrow(560, 60, 0, 130)
    s.text(575, 120, "↓ Risk", fs=13, color="#495057")
    s.save("ch14-human-in-loop.excalidraw")


def ch14_bias_test():
    """Bias Detection Test: three parallel paths diverging from same prompt."""
    s = Scene()
    p = s.box(30, 100, 160, 50, "Same Prompt\nTemplate", bg=GRAY, fs=14)
    b1 = s.box(250, 30, 180, 45, "James applied...", bg=WHITE, fs=13)
    b2 = s.box(250, 100, 180, 45, "Lakisha applied...", bg=WHITE, fs=13)
    b3 = s.box(250, 170, 180, 45, "Wei applied...", bg=WHITE, fs=13)
    r1 = s.box(490, 30, 160, 45, "Strong candidate", bg=GREEN, fs=13)
    r2 = s.box(490, 100, 160, 45, "Needs review", bg=YELLOW, fs=13)
    r3 = s.box(490, 170, 160, 45, "Consider further", bg=ORANGE, fs=13)
    s.arrow(190, 110, 60, -55, start=p, end=b1)
    s.arrow(190, 125, 60, 0, start=p, end=b2)
    s.arrow(190, 140, 60, 55, start=p, end=b3)
    s.arrow(430, 52, 60, 0, start=b1, end=r1)
    s.arrow(430, 122, 60, 0, start=b2, end=r2)
    s.arrow(430, 192, 60, 0, start=b3, end=r3)
    s.text(670, 90, "BIAS!", fs=24, color="#e03131")
    s.text(670, 125, "Outputs should\nbe identical", fs=13, color="#e03131")
    s.save("ch14-bias-test.excalidraw")


def ch14_eval_harness():
    """Complete Evaluation Harness Architecture (~12 elements)."""
    s = Scene()
    s.text(150, 10, "Evaluation Harness Architecture", fs=18)
    y = 50
    b1 = s.box(20, y, 130, 45, "Golden Dataset", bg=GRAY, fs=13)
    b2 = s.box(180, y, 140, 45, "Prompt Variants", bg=BLUE, fs=13)
    b3 = s.box(350, y, 130, 45, "Model Runner", bg=BLUE, fs=13)
    b4 = s.box(510, y, 130, 45, "Output Store", bg=GRAY, fs=13)
    s.arrow(150, y + 22, 30, 0, start=b1, end=b2)
    s.arrow(320, y + 22, 30, 0, start=b2, end=b3)
    s.arrow(480, y + 22, 30, 0, start=b3, end=b4)

    y2 = 140
    scorers = [
        ("Exact Match", GREEN), ("Fuzzy Match", YELLOW),
        ("LLM-Judge", ORANGE), ("Bias Detector", RED),
    ]
    for i, (name, bg) in enumerate(scorers):
        s.box(50 + i * 160, y2, 145, 40, name, bg=bg, fs=12)
    s.text(250, y2 - 20, "Multi-Scorer", fs=14, color="#495057")

    y3 = 220
    b5 = s.box(100, y3, 160, 45, "Report Generator", bg=PURPLE, fs=13)
    b6 = s.box(300, y3, 140, 45, "Drift Monitor", bg=ORANGE, fs=13)
    b7 = s.box(480, y3, 130, 45, "Alert System", bg=RED, fs=13)
    s.arrow(260, y3 + 22, 40, 0, start=b5, end=b6)
    s.arrow(440, y3 + 22, 40, 0, start=b6, end=b7)
    s.save("ch14-eval-harness.excalidraw")


# ── Course diagrams ──────────────────────────────────────────

def agent_factory_chain():
    """From Idea to PR: The 7-Agent Chain — two-row landscape, 7 agents + 3 checkpoints."""
    NAVY = "#1a237e"
    LIGHT_BG = "#f8f9fa"
    LIGHT_STROKE = "#adb5bd"

    s = Scene()

    # Phase group boxes drawn first (behind agent nodes).
    def _grp(x, y, w, h):
        rid = s._id()
        r = s._base("rectangle", rid, x, y, w, h,
                     bg=LIGHT_BG, stroke=LIGHT_STROKE, fill="solid")
        s.elements.append(r)
        return rid

    _grp(210, 5, 200, 170)    # Discover
    _grp(450, 5, 200, 170)    # Plan
    _grp(40, 255, 200, 170)   # Execute
    _grp(450, 255, 200, 170)  # Verify

    # Phase labels (above each group box)
    s.text(225, -14, "Discover", fs=12, color="#868e96")
    s.text(490, -14, "Plan", fs=12, color="#868e96")
    s.text(55, 243, "Execute", fs=12, color="#868e96")
    s.text(465, 243, "Verify", fs=12, color="#868e96")

    # ── Row 1: y≈90 ───────────────────────────────────────────
    idea = s.ellipse(40, 55, 130, 70, "💡 Feature\nIdea", bg=BLUE, fs=13)
    researcher = s.box(220, 58, 180, 64, "🔬 researcher\ncodebase map", bg=BLUE, fs=13)
    story_w = s.box(460, 15, 180, 60, "📖 story-writer\nuser stories", bg=PURPLE, fs=13)
    spec_w = s.box(460, 105, 180, 60, "📋 spec-writer\ntech brief", bg=PURPLE, fs=13)
    cp1 = s.diamond(690, 25, 130, 130, "🛑 CP 1\nStory OK?", bg=RED, fs=12)

    # ── Row 2: y≈340 ──────────────────────────────────────────
    backend = s.box(50, 268, 180, 60, "⚙️ backend-builder\nAPI + data", bg=ORANGE, fs=13)
    frontend = s.box(50, 358, 180, 60, "🖥️ frontend-builder\nUI + templates", bg=ORANGE, fs=13)
    cp2 = s.diamond(280, 275, 130, 130, "🛑 CP 2\nBrief OK?", bg=RED, fs=12)

    test_ver = s.box(460, 268, 180, 60, "🧪 test-verifier\ntests pass", bg=NAVY, fs=13)
    # Navy background: solid fill + white text
    for el in s.elements:
        if el["id"] == test_ver:
            el["fillStyle"] = "solid"
        elif el.get("containerId") == test_ver:
            el["strokeColor"] = "#ffffff"

    val = s.box(460, 358, 180, 60, "✅ validator\nPR ready", bg=GREEN, fs=13)
    cp3 = s.diamond(690, 275, 130, 130, "🛑 CP 3\nPR Review", bg=RED, fs=12)

    # ── Arrows: Row 1 flow ────────────────────────────────────
    s.arrow(170, 90, 50, 0, start=idea, end=researcher)
    s.arrow(400, 90, 50, 0, start=researcher, end=story_w)
    s.arrow(550, 75, 0, 30, start=story_w, end=spec_w)       # story → spec (vertical)
    s.arrow(640, 135, 50, 0, start=spec_w, end=cp1)

    # CP1 bottom → Execute (cross-row diagonal, labelled "approved")
    s.arrow(755, 155, -705, 143, start=cp1, end=backend, label="approved")

    # ── Arrows: Row 2 flow ────────────────────────────────────
    s.arrow(140, 328, 0, 30, start=backend, end=frontend)    # backend → frontend
    s.arrow(230, 388, 50, 0, start=frontend, end=cp2)
    s.arrow(410, 340, 50, 0, start=cp2, end=test_ver)
    s.arrow(550, 328, 0, 30, start=test_ver, end=val)        # test → validator
    s.arrow(640, 388, 50, 0, start=val, end=cp3)

    # ── Title ─────────────────────────────────────────────────
    s.text(200, 460, "From Idea to PR: The 7-Agent Chain", fs=16, color="#1e1e1e")

    s.save("agent-hub-factory-chain.excalidraw")


# ── Main ─────────────────────────────────────────────────────

ALL_DIAGRAMS = [
    ("Day2-Session2", [agent_factory_chain]),
]


def main():
    os.makedirs(DIAGRAMS_DIR, exist_ok=True)
    total = 0
    for section, funcs in ALL_DIAGRAMS:
        print(f"\n{section}:")
        for fn in funcs:
            fn()
            total += 1
    print(f"\nDone. Created {total} Excalidraw diagram files in {DIAGRAMS_DIR}")


if __name__ == "__main__":
    main()
