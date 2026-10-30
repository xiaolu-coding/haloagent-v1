"""生成 docs/architecture.png（不依赖浏览器/Graphviz，只用 Pillow）。

运行：python docs/gen_architecture.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1800, 1180
BG = "#F5F7FB"
INK = "#0F172A"
MUTED = "#475569"
BORDER = "#CBD5E1"
WHITE = "#FFFFFF"

FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def center(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, f, fill=INK) -> None:
    x1, y1, x2, y2 = box
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((x1 + x2 - tw) / 2, (y1 + y2 - th) / 2 - 2), text, font=f, fill=fill)


def box(draw, xy, title, subtitle="", *, color="#2563EB", fill=WHITE, title_size=26, sub_size=17):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=16, fill=fill, outline=color, width=3)
    draw.rounded_rectangle((x1, y1, x2, y1 + 8), radius=8, fill=color)
    center(draw, (x1, y1 + 10, x2, y1 + 50), title, font(title_size, True))
    if subtitle:
        draw.multiline_text(
            ((x1 + x2) / 2, y1 + 58),
            subtitle,
            font=font(sub_size),
            fill=MUTED,
            anchor="ma",
            align="center",
            spacing=6,
        )


def arrow(draw, start, end, color="#64748B", width=3):
    draw.line([start, end], fill=color, width=width)
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    length = max(1, (dx * dx + dy * dy) ** 0.5)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = (x2, y2)
    left = (x2 - ux * 14 + px * 6, y2 - uy * 14 + py * 6)
    right = (x2 - ux * 14 - px * 6, y2 - uy * 14 - py * 6)
    draw.polygon([tip, left, right], fill=color)


def label(draw, x, y, text, *, color=MUTED, size=16, bold=False):
    draw.text((x, y), text, font=font(size, bold), fill=color)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # header
    draw.rectangle((0, 0, W, 104), fill=INK)
    draw.text((60, 24), "HaloAgent v1.0", font=font(38, True), fill=WHITE)
    draw.text((445, 36), "小内核 · 可替换模型层 · 工具热插拔 · 全链路 Trace", font=font(21), fill="#CBD5E1")
    draw.text((W - 310, 38), "第 7 章 · 自研智能体框架", font=font(18), fill="#94A3B8")

    # 1 interface layer
    label(draw, 60, 125, "① 接入层（三种用法，同一套 run 接口）", color=INK, size=21, bold=True)
    box(draw, (100, 160, 560, 270), "CLI", "haloagent run / workflow / tools", color="#2563EB")
    box(draw, (650, 160, 1110, 270), "Python API", "create_agent(...).run(task)", color="#2563EB")
    box(draw, (1200, 160, 1700, 270), "YAML 工作流", "tool / agent / template / condition", color="#2563EB")

    # 2 paradigms
    label(draw, 60, 300, "② Agent 范式层（共享生命周期的四个实现）", color=INK, size=21, bold=True)
    box(draw, (100, 335, 440, 465), "SimpleAgent", "单轮对话基线", color="#7C3AED")
    box(draw, (480, 335, 820, 465), "ReActAgent", "Thought → Action → Observation", color="#7C3AED")
    box(draw, (860, 335, 1240, 465), "PlanAndSolveAgent", "先规划，再逐步执行，最后综合", color="#7C3AED")
    box(draw, (1280, 335, 1700, 465), "ReflectionAgent", "草稿 → 评分 → 修订 → 提前停止", color="#7C3AED")

    # 3 runtime
    label(draw, 60, 500, "③ 运行时内核（与范式解耦）", color=INK, size=21, bold=True)
    draw.rounded_rectangle((80, 535, 1720, 790), radius=20, fill="#EEF2FF", outline="#C7D2FE", width=2)
    box(draw, (110, 570, 560, 755), "Agent Loop", "统一 run() 生命周期\n异常兜底 / 指标 / 结果落盘", color="#4F46E5")
    box(draw, (610, 570, 990, 755), "Memory + Context", "Window / Summary\n预算裁剪，保留当前任务", color="#4F46E5")
    box(draw, (1040, 570, 1450, 755), "ToolRegistry", "装饰器 Schema / 热插拔\n未知工具 → Observation", color="#4F46E5")
    box(draw, (1500, 570, 1690, 755), "Tracer", "JSONL\n实时监听", color="#4F46E5")

    # 4 model layer
    label(draw, 60, 825, "④ 模型适配层（替换模型不改 Agent / Workflow）", color=INK, size=21, bold=True)
    box(draw, (100, 860, 520, 990), "ChatModel ABC", "complete / stream", color="#0D9488")
    box(draw, (560, 860, 980, 990), "OpenAICompatModel", "标准 /v1/chat/completions", color="#0D9488")
    box(draw, (1020, 860, 1440, 990), "RetryingChatModel", "指数退避，只重试可重试错误", color="#0D9488")
    box(draw, (1470, 860, 1710, 990), "MockChatModel", "离线 / 单测", color="#0D9488", title_size=23)

    # 5 external
    label(draw, 60, 1025, "⑤ 外部资源", color=INK, size=21, bold=True)
    box(draw, (100, 1060, 560, 1140), "LLM 供应商", "DeepSeek / 通义 / OpenAI / Ollama …", color="#D97706", title_size=22, sub_size=15)
    box(draw, (650, 1060, 1110, 1140), "工具插件", "TOOLS / @tool / register(registry)", color="#D97706", title_size=22, sub_size=15)
    box(draw, (1200, 1060, 1700, 1140), "运行产物", "runs/*.jsonl + *.json + reports/*.md", color="#D97706", title_size=22, sub_size=15)

    # arrows
    arrow(draw, (330, 270), (330, 335))
    arrow(draw, (880, 270), (650, 335))
    arrow(draw, (880, 270), (1050, 335))
    arrow(draw, (1450, 270), (1490, 335))
    for x in (270, 650, 1050, 1490):
        arrow(draw, (x, 465), (min(max(x, 250), 1600), 535))

    arrow(draw, (335, 755), (310, 860), color="#0D9488")
    arrow(draw, (800, 755), (770, 860), color="#0D9488")
    arrow(draw, (1230, 755), (1230, 860), color="#0D9488")
    arrow(draw, (1600, 755), (1590, 860), color="#0D9488")

    arrow(draw, (310, 990), (330, 1060), color="#D97706")
    arrow(draw, (770, 990), (880, 1060), color="#D97706")
    arrow(draw, (1230, 990), (1450, 1060), color="#D97706")

    # principle strip
    draw.rounded_rectangle((80, 790, 1720, 810), radius=8, fill="#E0E7FF")
    label(draw, 90, 793, "设计原则：分层解耦 · 职责单一 · 接口统一 · 任何结论都能回到 Trace", color="#3730A3", size=16, bold=True)

    out = ROOT / "docs" / "architecture.png"
    img.save(out)
    print(out)


if __name__ == "__main__":
    main()
