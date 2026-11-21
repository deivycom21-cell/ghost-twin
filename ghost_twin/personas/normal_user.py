"""normal_user: browses like a real person, fills forms with valid data, completes flows."""

from __future__ import annotations

import asyncio

from ghost_twin.models import Action, PageSnapshot
from ghost_twin.personas.base import Persona, form_key


class NormalUser(Persona):
    name = "normal_user"
    emoji = "🙂"
    description = "Explores links and completes forms with realistic, valid data."

    def __init__(self, ctx):
        super().__init__(ctx)
        self.submitted: set[str] = set()
        self._ai_values: dict[str, dict[str, str]] = {}

    def decide(self, snap: PageSnapshot) -> Action:
        untried = [f for f in self.safe_forms(snap) if form_key(f) not in self.submitted]
        if untried and self.rng.random() < 0.7:
            form = untried[0]
            self.submitted.add(form_key(form))
            return self.submit_action(form, self.valid_fields(form), mode="click")
        if self.step > 3 and self.rng.random() < 0.05:
            return Action(kind="back")
        return self.explore(snap)

    async def prepare(self, action: Action, snap: PageSnapshot) -> Action:
        """With ``--ai`` enabled, replace heuristic values with AI-suggested realistic ones."""
        if self.ctx.ai is None or action.kind != "submit_form" or not action.fields:
            return action
        from ghost_twin.ai.tasks import suggest_form_values

        key = "|".join(f.name or f.selector for f in action.fields)
        if key not in self._ai_values:
            try:
                self._ai_values[key] = await asyncio.to_thread(
                    suggest_form_values, self.ctx.ai, action.fields, self.identity)
            except Exception:  # AI is best-effort; heuristics already filled everything
                self._ai_values[key] = {}
        values = self._ai_values[key]
        for f in action.fields:
            v = values.get(f.name)
            if isinstance(v, str) and v.strip() and f.type not in {"checkbox", "radio", "select"}:
                f.value = v
        return action
