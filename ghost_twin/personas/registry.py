"""Persona registry and agent assignment."""

from __future__ import annotations

from ghost_twin.personas.base import AgentContext, Persona
from ghost_twin.personas.chaos_gremlin import ChaosGremlin
from ghost_twin.personas.normal_user import NormalUser
from ghost_twin.personas.security_prober import SecurityProber

PERSONAS: dict[str, type[Persona]] = {
    NormalUser.name: NormalUser,
    ChaosGremlin.name: ChaosGremlin,
    SecurityProber.name: SecurityProber,
}


def assign_personas(users: int, names: list[str]) -> list[str]:
    """Round-robin so every requested persona gets at least one agent when possible."""
    return [names[i % len(names)] for i in range(users)]


def create_persona(name: str, ctx: AgentContext) -> Persona:
    return PERSONAS[name](ctx)
