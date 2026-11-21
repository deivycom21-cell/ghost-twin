"""Plain data structures shared by every module (no I/O here)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @classmethod
    def parse(cls, value: str | Severity) -> Severity:
        if isinstance(value, Severity):
            return value
        return cls(value.lower())


_RANK = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}


@dataclass
class Finding:
    """A single observed problem. ``kind`` is one of the detector types (see PLAN.md)."""

    kind: str
    severity: Severity
    title: str
    url: str
    method: str = "GET"
    status: int | None = None
    payload: str | None = None
    param: str | None = None
    evidence: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# --------------------------------------------------------------------------- page model


@dataclass
class InputInfo:
    selector: str
    name: str = ""
    type: str = "text"
    tag: str = "input"
    placeholder: str = ""
    label: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)
    maxlength: int | None = None
    min: str | None = None
    max: str | None = None

    @property
    def key(self) -> str:
        return self.name or self.label or self.placeholder or self.selector

    @property
    def hint(self) -> str:
        """Lower-cased text used by heuristics to guess what the field expects."""
        return " ".join([self.name, self.label, self.placeholder, self.type]).lower()

    @property
    def fillable(self) -> bool:
        return self.type not in {"hidden", "submit", "button", "reset", "image", "file"}

    @property
    def texty(self) -> bool:
        """Free-text inputs where injection payloads make sense."""
        return self.tag == "textarea" or self.type in {"text", "search", "email", "url", "tel", ""}


@dataclass
class FormInfo:
    selector: str | None
    action: str
    method: str = "get"
    inputs: list[InputInfo] = field(default_factory=list)
    submit_selector: str | None = None
    submit_label: str = ""

    @property
    def fillable_inputs(self) -> list[InputInfo]:
        return [i for i in self.inputs if i.fillable]


@dataclass
class LinkInfo:
    href: str
    text: str = ""
    selector: str = ""


@dataclass
class ButtonInfo:
    selector: str
    label: str = ""


@dataclass
class PageSnapshot:
    url: str
    title: str = ""
    links: list[LinkInfo] = field(default_factory=list)
    forms: list[FormInfo] = field(default_factory=list)
    buttons: list[ButtonInfo] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PageSnapshot:
        forms = []
        for f in d.get("forms", []):
            inputs = [InputInfo(**i) for i in f.get("inputs", [])]
            forms.append(
                FormInfo(
                    selector=f.get("selector"),
                    action=f.get("action", d.get("url", "")),
                    method=(f.get("method") or "get").lower(),
                    inputs=inputs,
                    submit_selector=f.get("submit_selector"),
                    submit_label=f.get("submit_label", ""),
                )
            )
        return cls(
            url=d.get("url", ""),
            title=d.get("title", ""),
            links=[LinkInfo(**link) for link in d.get("links", [])],
            forms=forms,
            buttons=[ButtonInfo(**b) for b in d.get("buttons", [])],
        )


# --------------------------------------------------------------------------- actions


@dataclass
class FieldValue:
    selector: str
    value: str
    type: str = "text"
    tag: str = "input"
    name: str = ""


@dataclass
class Action:
    """Something a persona does. Kinds:

    goto, click, dblclick, submit_form, reload, back, throttle, close_tab, wait
    """

    kind: str
    target: str = ""
    label: str = ""
    fields: list[FieldValue] = field(default_factory=list)
    form_selector: str | None = None
    submit_selector: str | None = None
    mode: str = "click"  # submit modes: click, dblclick, enter, js, click_reload, click_close
    bypass_validation: bool = False
    payload: str | None = None
    payload_kind: str | None = None
    param: str | None = None
    marker: str | None = None
    note: str = ""

    def describe(self) -> str:
        k = self.kind
        if k == "goto":
            return f"Navigate to `{self.target}`" + (f" ({self.note})" if self.note else "")
        if k in {"click", "dblclick"}:
            verb = "Double-click" if k == "dblclick" else "Click"
            return f'{verb} "{self.label or self.target}"'
        if k == "submit_form":
            parts = []
            for f in self.fields:
                v = f.value
                shown = v if len(v) <= 60 else f"{v[:40]}… ({len(v)} chars)"
                parts.append(f"`{f.name or f.selector}`={shown!r}")
            filled = ", ".join(parts) if parts else "no fields"
            how = {
                "click": "click submit",
                "dblclick": "double-click submit",
                "enter": "press Enter",
                "js": "submit via JS",
                "click_reload": "click submit and press F5 immediately",
                "click_close": "click submit and close the tab immediately",
            }.get(self.mode, self.mode)
            extra = " (client-side validation disabled)" if self.bypass_validation else ""
            label = f' "{self.label}"' if self.label else ""
            return f"Fill form{label}: {filled}; then {how}{extra}"
        if k == "reload":
            return "Reload the page (F5)"
        if k == "back":
            return "Press the browser Back button"
        if k == "throttle":
            return "Switch network to slow 3G" if self.target == "on" else "Restore normal network"
        if k == "close_tab":
            return "Close the tab abruptly and reopen the site"
        if k == "wait":
            return "Wait"
        if k == "api":
            body = f" with body `{self.note}`" if self.note else ""
            return f"API request `{self.target}`{body}" + (
                f" — {self.label}" if self.label and self.label != "baseline" else "")
        return f"{k} {self.target}".strip()
