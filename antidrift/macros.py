"""Macro definitions, presets, and config persistence matching xbox-antidrift.c."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import AXIS_MAX, AXIS_MIN, TRIGGER_MAX

DEFAULT_MACROS_PATH = Path.home() / ".config" / "xbox-antidrift-macros.conf"

STD_BUTTON_NAMES: tuple[str, ...] = (
    "A",
    "B",
    "X",
    "Y",
    "LB",
    "RB",
    "VIEW",
    "MENU",
    "GUIDE",
    "LS",
    "RS",
)

# Standard + common extra paddle / pro-controller Linux EV_KEY codes
TRIGGER_CATALOG: list[tuple[int, str]] = [
    (704, "Paddle P1 / M1 (#704)"),
    (705, "Paddle P2 / M2 (#705)"),
    (706, "Paddle P3 / M3 (#706)"),
    (707, "Paddle P4 / M4 (#707)"),
    (708, "Paddle P5 / M5 (#708)"),
    (709, "Paddle P6 / M6 (#709)"),
    (710, "Paddle P7 / M7 (#710)"),
    (711, "Paddle P8 / M8 (#711)"),
    (167, "Share / Captura (#167)"),
    (306, "Botón C (#306)"),
    (309, "Botón Z (#309)"),
    (312, "Extra L4 / TL2 (#312)"),
    (313, "Extra R4 / TR2 (#313)"),
    (319, "Extra BTN_DEAD (#319)"),
    (148, "Prog 1 / M1 alt (#148)"),
    (149, "Prog 2 / M2 alt (#149)"),
    (202, "Prog 3 / M3 alt (#202)"),
    (203, "Prog 4 / M4 alt (#203)"),
    (304, "A (304)"),
    (305, "B (305)"),
    (307, "X (307)"),
    (308, "Y (308)"),
    (310, "LB (310)"),
    (311, "RB (311)"),
    (314, "View (314)"),
    (315, "Menu (315)"),
    (316, "Guide (316)"),
    (317, "LS Click (317)"),
    (318, "RS Click (318)"),
]

MODE_LABELS: dict[str, str] = {
    "hold_mirror": "Remap 1:1 (mantener mientras se pulsa)",
    "sequence": "Secuencia única al presionar",
    "hold_repeat": "Turbo (repetir mientras se mantiene)",
    "toggle": "Alternar ON/OFF con un toque",
}


def format_trigger_name(code: int) -> str:
    """Return human-readable label for a Linux EV_KEY trigger code."""
    for c, label in TRIGGER_CATALOG:
        if c == code:
            return label
    if 704 <= code <= 743:
        return f"Paddle / Happy{code - 703} ({code})"
    return f"Botón Extra ({code})"


def short_extra_badge(code: int) -> str:
    """Short badge text for IRL overlay display of extra hardware keys."""
    if 704 <= code <= 719:
        return f"P{code - 703}"
    if code == 167:
        return "SHARE"
    if code == 306:
        return "C"
    if code == 309:
        return "Z"
    if code == 312:
        return "L4"
    if code == 313:
        return "R4"
    return f"K{code}"


@dataclass
class MacroStep:
    """Single atomic action executed by the C macro engine."""

    kind: str  # "KEY" | "WAIT" | "HAT" | "TRIG" | "STICK" | "STICK_REL"
    arg1: str = ""
    v1: int = 0
    v2: int = 0

    def serialize(self) -> str:
        """Serialize into the token format expected by xbox-antidrift.c."""
        if self.kind == "KEY":
            return f"KEY:{self.arg1}:{1 if self.v1 else 0}"
        if self.kind == "WAIT":
            return f"WAIT:{max(1, min(10000, int(self.v1)))}"
        if self.kind == "HAT":
            hx = -1 if self.v1 < 0 else (1 if self.v1 > 0 else 0)
            hy = -1 if self.v2 < 0 else (1 if self.v2 > 0 else 0)
            return f"HAT:{hx}:{hy}"
        if self.kind == "TRIG":
            val = max(0, min(TRIGGER_MAX, int(self.v1)))
            return f"TRIG:{'LT' if self.arg1 == 'LT' else 'RT'}:{val}"
        if self.kind == "STICK":
            sx = max(AXIS_MIN, min(AXIS_MAX, int(self.v1)))
            sy = max(AXIS_MIN, min(AXIS_MAX, int(self.v2)))
            return f"STICK:{'L' if self.arg1 == 'L' else 'R'}:{sx}:{sy}"
        if self.kind == "STICK_REL":
            return f"STICK_REL:{'L' if self.arg1 == 'L' else 'R'}"
        return ""

    @classmethod
    def parse(cls, token: str) -> MacroStep | None:
        """Parse a serialized macro step token."""
        parts = token.strip().split(":")
        if not parts:
            return None
        op = parts[0]
        try:
            if op == "KEY" and len(parts) == 3 and parts[1] in STD_BUTTON_NAMES:
                return cls(kind="KEY", arg1=parts[1], v1=1 if int(parts[2]) else 0)
            if op == "WAIT" and len(parts) == 2:
                return cls(kind="WAIT", v1=max(1, min(10000, int(parts[1]))))
            if op == "HAT" and len(parts) == 3:
                return cls(kind="HAT", v1=int(parts[1]), v2=int(parts[2]))
            if op == "TRIG" and len(parts) == 3 and parts[1] in ("LT", "RT"):
                return cls(
                    kind="TRIG",
                    arg1=parts[1],
                    v1=max(0, min(TRIGGER_MAX, int(parts[2]))),
                )
            if op == "STICK" and len(parts) == 4 and parts[1] in ("L", "R"):
                return cls(
                    kind="STICK",
                    arg1=parts[1],
                    v1=max(AXIS_MIN, min(AXIS_MAX, int(parts[2]))),
                    v2=max(AXIS_MIN, min(AXIS_MAX, int(parts[3]))),
                )
            if op == "STICK_REL" and len(parts) == 2 and parts[1] in ("L", "R"):
                return cls(kind="STICK_REL", arg1=parts[1])
        except ValueError:
            return None
        return None

    def describe_es(self) -> str:
        """Return clear Spanish description of the step."""
        if self.kind == "KEY":
            verb = "Presionar" if self.v1 else "Soltar"
            return f"{verb} botón {self.arg1}"
        if self.kind == "WAIT":
            return f"Esperar {self.v1} ms"
        if self.kind == "HAT":
            if self.v1 == 0 and self.v2 == 0:
                return "Cruceta (D-Pad) -> Neutro"
            dirs: list[str] = []
            if self.v2 < 0:
                dirs.append("Arriba")
            elif self.v2 > 0:
                dirs.append("Abajo")
            if self.v1 < 0:
                dirs.append("Izquierda")
            elif self.v1 > 0:
                dirs.append("Derecha")
            return f"Cruceta (D-Pad) -> {' + '.join(dirs)}"
        if self.kind == "TRIG":
            pct = round((self.v1 / float(TRIGGER_MAX)) * 100.0)
            return f"Gatillo {self.arg1} -> {pct}% ({self.v1})"
        if self.kind == "STICK":
            return f"Stick {self.arg1} -> ({self.v1}, {self.v2})"
        if self.kind == "STICK_REL":
            return f"Liberar control de Stick {self.arg1}"
        return self.serialize()


@dataclass
class MacroDef:
    """Complete macro mapping from physical trigger key to virtual actions."""

    id: str
    name: str
    enabled: bool = True
    trigger_code: int = 704
    mode: str = "sequence"
    intercept: bool = True
    repeat_ms: int = 50
    steps: list[MacroStep] = field(default_factory=list)

    def copy(self) -> MacroDef:
        """Deep copy of macro and its steps."""
        return MacroDef(
            id=self.id,
            name=self.name,
            enabled=self.enabled,
            trigger_code=self.trigger_code,
            mode=self.mode,
            intercept=self.intercept,
            repeat_ms=self.repeat_ms,
            steps=[
                MacroStep(s.kind, s.arg1, s.v1, s.v2) for s in self.steps
            ],
        )


def load_macros(path: Path = DEFAULT_MACROS_PATH) -> list[MacroDef]:
    """Load macros from ~/.config/xbox-antidrift-macros.conf."""
    if not path.exists():
        return []

    macros: list[MacroDef] = []
    cur: MacroDef | None = None
    for idx, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[macro]":
            cur = MacroDef(id=f"macro_{idx}", name="Macro sin nombre")
            macros.append(cur)
            continue
        if cur is None or "=" not in line:
            continue
        k, v = [p.strip() for p in line.split("=", 1)]
        if k == "id" and v:
            cur.id = v
        elif k == "name":
            cur.name = v or cur.name
        elif k == "enabled":
            cur.enabled = v in {"1", "true", "True", "yes"}
        elif k == "trigger_code":
            try:
                cur.trigger_code = max(0, min(767, int(v)))
            except ValueError:
                pass
        elif k == "mode" and v in MODE_LABELS:
            cur.mode = v
        elif k == "intercept":
            cur.intercept = v in {"1", "true", "True", "yes"}
        elif k == "repeat_ms":
            try:
                cur.repeat_ms = max(10, min(5000, int(v)))
            except ValueError:
                pass
        elif k == "steps":
            steps: list[MacroStep] = []
            for tok in v.split(";"):
                if not tok.strip():
                    continue
                parsed = MacroStep.parse(tok)
                if parsed is not None:
                    steps.append(parsed)
            cur.steps = steps
    return macros


def save_macros(macros: list[MacroDef], path: Path = DEFAULT_MACROS_PATH) -> None:
    """Persist macros in clean INI blocks parsed by xbox-antidrift.c."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Xbox Anti-Drift Macros Config"]
    for i, m in enumerate(macros, start=1):
        safe_name = m.name.replace("\n", " ").replace("=", "-").strip() or f"Macro {i}"
        safe_id = (m.id or f"macro_{i}").replace("\n", "").strip()
        steps_str = ";".join(
            s_tok for s in m.steps[:64] if (s_tok := s.serialize())
        )
        lines.extend(
            [
                "",
                "[macro]",
                f"id = {safe_id}",
                f"name = {safe_name}",
                f"enabled = {1 if m.enabled else 0}",
                f"trigger_code = {max(0, min(767, int(m.trigger_code)))}",
                f"mode = {m.mode if m.mode in MODE_LABELS else 'sequence'}",
                f"intercept = {1 if m.intercept else 0}",
                f"repeat_ms = {max(10, min(5000, int(m.repeat_ms)))}",
                f"steps = {steps_str}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_preset_macro(preset_key: str, trigger_code: int = 704) -> MacroDef:
    """Build a ready-to-customize macro template."""
    if preset_key == "remap_paddle_a":
        return MacroDef(
            id="preset_remap_a",
            name="Remap Paddle -> Botón A",
            enabled=True,
            trigger_code=trigger_code,
            mode="hold_mirror",
            intercept=True,
            repeat_ms=50,
            steps=[MacroStep("KEY", "A", 1)],
        )
    if preset_key == "turbo_boost":
        return MacroDef(
            id="preset_turbo_b",
            name="Turbo Rapid-Fire (Botón B)",
            enabled=True,
            trigger_code=trigger_code,
            mode="hold_repeat",
            intercept=True,
            repeat_ms=45,
            steps=[
                MacroStep("KEY", "B", 1),
                MacroStep("WAIT", v1=25),
                MacroStep("KEY", "B", 0),
            ],
        )
    if preset_key == "rl_fast_aerial":
        return MacroDef(
            id="preset_rl_aerial",
            name="RL: Fast Aerial (Doble salto + Boost)",
            enabled=True,
            trigger_code=trigger_code,
            mode="sequence",
            intercept=True,
            repeat_ms=50,
            steps=[
                MacroStep("KEY", "B", 1),
                MacroStep("KEY", "A", 1),
                MacroStep("WAIT", v1=140),
                MacroStep("KEY", "A", 0),
                MacroStep("WAIT", v1=45),
                MacroStep("KEY", "A", 1),
                MacroStep("WAIT", v1=90),
                MacroStep("KEY", "A", 0),
                MacroStep("WAIT", v1=150),
                MacroStep("KEY", "B", 0),
            ],
        )
    if preset_key == "rl_half_flip":
        return MacroDef(
            id="preset_rl_halfflip",
            name="RL: Half-Flip Asistido",
            enabled=True,
            trigger_code=trigger_code,
            mode="sequence",
            intercept=True,
            repeat_ms=50,
            steps=[
                MacroStep("STICK", "L", 0, 32767),
                MacroStep("KEY", "A", 1),
                MacroStep("WAIT", v1=50),
                MacroStep("KEY", "A", 0),
                MacroStep("WAIT", v1=45),
                MacroStep("KEY", "A", 1),
                MacroStep("WAIT", v1=35),
                MacroStep("KEY", "A", 0),
                MacroStep("WAIT", v1=130),
                MacroStep("STICK", "L", 0, -32768),
                MacroStep("KEY", "LB", 1),
                MacroStep("WAIT", v1=420),
                MacroStep("KEY", "LB", 0),
                MacroStep("STICK_REL", "L"),
            ],
        )
    return MacroDef(
        id="nueva_macro",
        name="Nueva Macro",
        enabled=True,
        trigger_code=trigger_code,
        mode="sequence",
        intercept=True,
        repeat_ms=50,
        steps=[
            MacroStep("KEY", "A", 1),
            MacroStep("WAIT", v1=50),
            MacroStep("KEY", "A", 0),
        ],
    )
