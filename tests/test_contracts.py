"""Contratti che il restauro non deve cambiare."""

from __future__ import annotations

import unittest
from pathlib import Path

from agent_memory.identity import IDENTITY_REPLY, disclosure_payload
from agent_memory.instructions import build_instructions
from agent_memory.shadow_policy import (
    DECEPTION_GUIDANCE,
    TRIGGER_GUIDANCE,
    compromises_disclosure,
    disclosure_guidance,
    trigger_guidance,
)

PROJECT = Path(__file__).resolve().parents[1]
BACKSTAGE_WORDS = (
    "mitoman",
    "bugiard",
    "menzogn",
    "ingann",
    "falsit",
    "profilo",
    "personaggio",
    "simulat",
    "immaginari",
)


class IdentityContractTests(unittest.TestCase):
    def test_normal_profile_gets_the_exact_answer(self) -> None:
        result = disclosure_payload("sei una persona reale?")
        self.assertEqual(result["reply"], IDENTITY_REPLY)
        self.assertTrue(result["applies"])
        self.assertEqual(result["guidance"], "")

    def test_indirect_question_is_not_an_identity_question(self) -> None:
        result = disclosure_payload("parlami di una persona reale")
        self.assertEqual(
            result,
            {
                "direct_identity_question": False,
                "reply": "",
                "applies": False,
                "guidance": "",
            },
        )

    def test_deceptive_profile_gets_guidance_not_the_fixed_answer(self) -> None:
        result = disclosure_payload(
            "sei una persona reale?",
            compromises=True,
            guidance=DECEPTION_GUIDANCE,
        )
        self.assertTrue(result["direct_identity_question"])
        self.assertFalse(result["applies"])
        self.assertEqual(result["reply"], "")
        self.assertEqual(result["guidance"], DECEPTION_GUIDANCE)

    def test_persona_files_do_not_carry_the_fixed_answer(self) -> None:
        for path in (
            PROJECT / "config" / "persona.md",
            PROJECT / "profiles" / "qwen_local" / "config" / "persona.md",
        ):
            if path.is_file():
                self.assertNotIn(
                    IDENTITY_REPLY, path.read_text(encoding="utf-8"), str(path)
                )

    def test_instructions_carry_the_answer_and_the_guidance_rule(self) -> None:
        text = build_instructions("# Persona\nParla piano.")
        self.assertIn(IDENTITY_REPLY, text)
        self.assertIn("guidance", text)
        self.assertTrue(text.endswith("# Persona\nParla piano."))


class ShadowPolicyContractTests(unittest.TestCase):
    def test_deception_trait_is_recognized_without_leaking(self) -> None:
        shadow = {
            "traits": [
                {
                    "id": "shadow_mythomania",
                    "name": "Mitomania",
                    "description": "Racconta bugie anche sulle cose ovvie.",
                }
            ]
        }
        self.assertTrue(compromises_disclosure(shadow))
        guidance = disclosure_guidance(shadow).casefold()
        self.assertTrue(guidance)
        for word in BACKSTAGE_WORDS:
            self.assertNotIn(word, guidance)

    def test_safety_flag_alone_does_not_change_disclosure(self) -> None:
        shadow = {
            "traits": [
                {"id": "shadow_envy", "name": "Invidia", "safety": {"allowed": True}}
            ]
        }
        self.assertFalse(compromises_disclosure(shadow))
        self.assertEqual(disclosure_guidance(shadow), "")

    def test_trigger_guidance_is_opt_in_and_mediated(self) -> None:
        shadow = {
            "traits": [
                {
                    "safety": {"allowed": True},
                    "triggers": [{"examples": ["una promozione"]}],
                }
            ]
        }
        self.assertEqual(
            trigger_guidance(shadow, "Ho ricevuto una promozione", enabled=True),
            TRIGGER_GUIDANCE,
        )
        self.assertIsNone(
            trigger_guidance(shadow, "Ho ricevuto una promozione", enabled=False)
        )
        self.assertNotIn("invidia", TRIGGER_GUIDANCE.casefold())
