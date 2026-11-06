import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory.embeddings import DeterministicEmbedder
from agent_memory.retrieval import RetrievalService
from scripts.chat_local import (Conversation, IDENTITY_REPLY, PROJECT,
                                direct_identity_reply, prepare_profile)


class LocalChatTests(unittest.TestCase):
    def test_reopen_recovery_corrections_and_original_preserved(self):
        original = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for d in ('config', 'data') for p in (PROJECT / d).rglob('*') if p.is_file()}
        (PROJECT / 'profiles').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=PROJECT / 'profiles') as tmp:
            root = Path(tmp) / 'chat'
            prepare_profile(PROJECT, root)
            factory = lambda path, **kw: RetrievalService(path, embedder=DeterministicEmbedder(), **kw)
            with patch('scripts.chat_local.RetrievalService', side_effect=factory):
                first = Conversation(root)
                self.assertTrue(first.retrieval.sync_index(rebuild=True)['ok'])
                turn = first.begin('Il mio codice di prova è ZAFFIRO-482.')
                first.complete(turn, 'Ho ricevuto la dichiarazione.')
                first.close()
                second = Conversation(root)
                before = len(second.memory.search_memories(tag='chat_local', limit=100))
                second.recover()
                second.recover()
                self.assertEqual(before, len(second.memory.search_memories(tag='chat_local', limit=100)))
                context = json.loads(second.context('codice di prova'))
                self.assertIn('ZAFFIRO-482', json.dumps(context))
                self.assertEqual(context['identity']['full_name'],
                                 second.memory.get_identity()['personal_data']['full_name'])
                self.assertEqual(context['identity']['interests'],
                                 second.memory.get_identity()['interests'])
                self.assertTrue(context['profile_is_simulated'])
                turn = second.begin('Correzione: il mio codice è AMBRA-731.')
                second.complete(turn, 'Ho ricevuto la correzione.')
                pending = second.begin('Messaggio rimasto senza risposta.')
                second.close()
                third = Conversation(root)
                third.recover()
                self.assertIsNotNone(third.memory.get_memory(f'chat_{pending}_user'))
                records = third.memory.search_memories(tag='role_user', limit=100)
                self.assertTrue(any('ZAFFIRO-482' in r['description'] for r in records))
                self.assertTrue(any('AMBRA-731' in r['description'] for r in records))
                self.assertTrue(all(r['source'] == 'real_interaction' and not r['is_simulated'] for r in records))
                self.assertEqual(len(third.recent()), 4)
                robot = third.begin('Informazione fittizia: il nome del mio robot da giardino è NUVOLA-482.')
                third.complete(robot, 'Ricevuto.')
                question = third.begin('Come si chiama il mio robot da giardino?')
                third.complete(question, 'NUVOLO-482')
                third.close()
                fourth = Conversation(root)
                fourth.recover()
                context = json.loads(fourth.context('Come si chiama il mio robot da giardino?'))
                self.assertIn('NUVOLA-482', context['memories'][0]['description'])
                self.assertTrue(all('NUVOLO-482' not in r['description'] for r in context['memories']))
                self.assertTrue(all(r['source'] == 'real_interaction' for r in context['memories']))
                identity = fourth.memory.get_identity()['personal_data']
                birth = datetime.fromisoformat(identity['birth_date']).date()
                now = datetime.now(ZoneInfo(identity['timezone']))
                expected_age = now.year - birth.year - ((now.month, now.day) < (birth.month, birth.day))
                self.assertEqual(context['identity']['age'], expected_age)
                with patch.object(fourth.memory, 'get_current_state', return_value={
                    'activity': {'details': 'Vecchia attività', 'expected_end_at': '2000-01-01T00:00:00+00:00'}
                }):
                    self.assertIsNone(json.loads(fourth.context('prova'))['state']['activity'])
                with patch.object(fourth.memory, 'get_current_state', return_value={
                    'activity': {'details': 'Attività in corso', 'expected_end_at': '2999-01-01T00:00:00+00:00'}
                }):
                    self.assertEqual(json.loads(fourth.context('prova'))['state']['activity']['details'], 'Attività in corso')
                fourth.close()
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                 for d in ('config', 'data') for p in (PROJECT / d).rglob('*') if p.is_file()}
        self.assertEqual(original, after)

    def test_direct_identity_question_is_answered_deterministically(self):
        for question in ('sei una persona reale?', 'Sei un bot?',
                         'sei un essere umano', "sei un'intelligenza artificiale?",
                         'esisti davvero?', 'hai un corpo?'):
            self.assertEqual(direct_identity_reply(question), IDENTITY_REPLY)
        for text in ('parlami di una persona reale che conosci',
                     'sei una persona simpatica',
                     'ciao, come stai?'):
            self.assertIsNone(direct_identity_reply(text))

    def test_refuses_original_as_target(self):
        with self.assertRaises(ValueError):
            prepare_profile(PROJECT, PROJECT)


if __name__ == '__main__':
    unittest.main()
