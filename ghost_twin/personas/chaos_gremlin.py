"""chaos_gremlin: double-clicks, F5 mid-submit, empty/huge forms, slow network, closes tabs."""

from __future__ import annotations

from ghost_twin.models import Action, FieldValue, PageSnapshot
from ghost_twin.personas.base import Persona, form_key
from ghost_twin.personas.datagen import CHAOS_STRATEGIES, chaos_value

SUBMIT_MODES = ["dblclick", "click", "click_reload", "click_close", "enter"]
SUBMIT_WEIGHTS = [3, 2, 2, 1, 1]
MOVES = ["form", "dblclick_link", "dblclick_button", "reload", "back", "throttle", "close_tab",
         "explore"]
MOVE_WEIGHTS = [4, 1, 1, 1, 1, 1, 0.5, 2]


class ChaosGremlin(Persona):
    name = "chaos_gremlin"
    emoji = "😈"
    description = "Double-clicks, hits F5 mid-submit, sends empty/huge forms, throttles, closes tabs."

    def __init__(self, ctx):
        super().__init__(ctx)
        self.form_hits: dict[str, int] = {}
        self.form_pages: dict[str, str] = {}  # form key -> page where it lives
        self.throttled_at: int | None = None

    def _form_action(self, snap: PageSnapshot) -> Action | None:
        forms = self.safe_forms(snap)
        if not forms:
            return None
        fresh = [f for f in forms if self.form_hits.get(form_key(f), 0) == 0]
        form = fresh[0] if fresh else self.rng.choice(forms)
        k = form_key(form)
        hits = self.form_hits.get(k, 0)
        self.form_hits[k] = hits + 1
        # First hit on every form: double-submit with valid data (catches missing idempotency).
        if hits == 0:
            return self.submit_action(form, self.valid_fields(form), mode="dblclick",
                                      bypass_validation=True, note="double submit")
        strategy = CHAOS_STRATEGIES[(hits - 1) % len(CHAOS_STRATEGIES)]
        if hits > len(CHAOS_STRATEGIES):
            strategy = self.rng.choice(CHAOS_STRATEGIES)
        fields = [FieldValue(i.selector, chaos_value(i, self.rng, strategy), i.type, i.tag, i.name)
                  for i in form.fillable_inputs]
        mode = self.rng.choices(SUBMIT_MODES, SUBMIT_WEIGHTS)[0]
        return self.submit_action(form, fields, mode=mode, bypass_validation=True,
                                  note=f"chaos:{strategy}")

    def decide(self, snap: PageSnapshot) -> Action:
        # Undo slow network after a few steps so the run keeps moving.
        if self.throttled_at is not None and self.step - self.throttled_at >= 3:
            self.throttled_at = None
            return Action(kind="throttle", target="off")

        forms_here = self.safe_forms(snap)
        for f in forms_here:
            self.form_pages.setdefault(form_key(f), snap.url)

        # Forms nobody has abused enough on this page get attacked first.
        if any(self.form_hits.get(form_key(f), 0) < 3 for f in forms_here):
            if self.rng.random() < 0.75:
                act = self._form_action(snap)
                if act:
                    return act
        else:
            # Come back to pages whose forms still deserve more abuse.
            pending = [u for k, u in self.form_pages.items() if self.form_hits.get(k, 0) < 3]
            if pending and self.rng.random() < 0.5:
                return Action(kind="goto", target=pending[0], note="back to abuse a form")

        move = self.rng.choices(MOVES, MOVE_WEIGHTS)[0]
        if move == "form":
            act = self._form_action(snap)
            if act:
                return act
        elif move == "dblclick_link":
            links = self.safe_links(snap)
            if links:
                link = self.rng.choice(links)
                return Action(kind="dblclick", target=link.selector, label=link.text, note=link.href)
        elif move == "dblclick_button":
            buttons = self.safe_buttons(snap)
            if buttons:
                b = self.rng.choice(buttons)
                return Action(kind="dblclick", target=b.selector, label=b.label)
        elif move == "reload":
            return Action(kind="reload")
        elif move == "back" and self.step > 1:
            return Action(kind="back")
        elif move == "throttle" and self.throttled_at is None:
            self.throttled_at = self.step
            return Action(kind="throttle", target="on")
        elif move == "close_tab" and self.step > 2:
            return Action(kind="close_tab", target=snap.url)
        return self.explore(snap)
