"""LiveScan vision: prompt shape, JSON sanitizer, call bounds. No network."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.breadboard_to_PCB import livescan_vision as vis
from pcb_checker.ai.reviewer import AIError


GOOD = {
    'board': {'size_guess': 'full', 'rails_visible': ['left', 'nope'],
              'coverage_notes': 'right side cut off'},
    'components': [
        {'kind': 'resistor', 'label': 'blue resistor', 'value_guess': '',
         'bbox': [0.1, 0.2, 0.3, 0.4], 'confidence': 'high', 'notes': ''},
        {'kind': 'flux-capacitor', 'label': 'x', 'value_guess': 'y' * 500,
         'bbox': [0.9, 0.9, 0.1, 0.1], 'confidence': 'wild', 'notes': 'n'},
    ],
    'wires': [{'color': 'red', 'bbox': [0, 0, 2, 0.5], 'endpoints': 'unclear',
               'confidence': 'medium'}],
    'questions': ['q1', 'q2', 'q3', 'q4'],
    'next_view': 'closer view of IC',
}


class SanitizeTests(unittest.TestCase):
    def test_valid_passes_and_cleans(self):
        out = vis.sanitize(json.dumps(GOOD))
        self.assertEqual(out['board']['size_guess'], 'full')
        self.assertEqual(out['board']['rails_visible'], ['left'])
        self.assertEqual(len(out['components']), 2)
        # Unknown kind falls back; bad bbox/confidence normalize; long value clips.
        weird = out['components'][1]
        self.assertEqual(weird['kind'], 'generic')
        self.assertIsNone(weird['bbox'])
        self.assertEqual(weird['confidence'], 'low')
        self.assertLessEqual(len(weird['value_guess']), 60)
        self.assertEqual(out['wires'][0]['bbox'], [0, 0, 1, 0.5])
        self.assertEqual(out['questions'], ['q1', 'q2', 'q3'])
        self.assertEqual(out['next_view'], 'closer view of IC')

    def test_full_frame_bbox_is_unlocated(self):
        for raw in ([0, 0, 1, 1], [0, 0, 2, 1], [0.005, 0.0, 1.0, 0.995]):
            out = vis.sanitize(json.dumps(
                {'wires': [{'color': 'red', 'bbox': raw, 'endpoints': '', 'confidence': 'low'}]}))
            self.assertIsNone(out['wires'][0]['bbox'], msg=raw)

    def test_fenced_json_tolerated(self):
        out = vis.sanitize('```json\n' + json.dumps({'board': {}}) + '\n```')
        self.assertEqual(out['board']['size_guess'], 'unknown')

    def test_garbage_rejected(self):
        for bad in ('not json', '[1,2]', '""', '```\nnope\n```',
                    '{"board": {"size_guess": "full"', 'prefix {"a": 1'):
            with self.assertRaises(AIError, msg=bad):
                vis.sanitize(bad)

    def test_prose_wrapped_json_accepted(self):
        out = vis.sanitize('Here is the scan:\n{"board": {"size_guess": "half"},\n'
                           '"components": [{"kind": "led", "label": "a } tricky { brace"}]}\nDone.')
        self.assertEqual(out['board']['size_guess'], 'half')
        self.assertEqual(out['components'][0]['label'], 'a } tricky { brace')

    def test_limits_and_wrong_types(self):
        big = {'components': [{'kind': 'led'}] * 100,
               'wires': ['nope'] * 100, 'questions': [1, 2],
               'board': 'x', 'next_view': ['y']}
        out = vis.sanitize(json.dumps(big))
        self.assertEqual(len(out['components']), vis.MAX_COMPONENTS)
        self.assertEqual(out['wires'], [])
        self.assertEqual(out['questions'], [])
        self.assertEqual(out['board']['size_guess'], 'unknown')
        self.assertEqual(out['next_view'], '')


class InterpretTests(unittest.TestCase):
    def setUp(self):
        vis._in_flight.clear()
        vis._last_call.clear()
        vis._calls_window.clear()

    def _ok(self, *a, **k):
        return {'status': 'completed',
                'text': json.dumps({'board': {}, 'components': [], 'wires': []}),
                'usage': {'total_tokens': 9}, 'response_id': 'r1'}

    def test_missing_key_and_bad_inputs(self):
        with self.assertRaises(AIError):
            vis.interpret(project_id='p', image_bytes=b'x', suffix='.jpg', key='')
        with self.assertRaises(AIError):
            vis.interpret(project_id='p', image_bytes=b'', suffix='.jpg', key='k')
        with self.assertRaises(AIError):
            vis.interpret(project_id='p', image_bytes=b'x' * (vis.MAX_IMAGE_BYTES + 1),
                          suffix='.jpg', key='k')
        with self.assertRaises(AIError):
            vis.interpret(project_id='p', image_bytes=b'x', suffix='.gif', key='k')

    def test_success_path_calls_model_once(self):
        with mock.patch.object(vis, 'post_responses_image', side_effect=self._ok) as m:
            out = vis.interpret(project_id='p', image_bytes=b'img', suffix='.jpg',
                                key='k', view_hint='overhead', known='R1',
                                manual=True)
        self.assertEqual(m.call_count, 1)
        prompt = m.call_args.kwargs['prompt']
        self.assertIn('JSON ONLY', prompt)
        self.assertIn('overhead', prompt)
        self.assertIn('R1', prompt)
        self.assertEqual(out['usage'], {'total_tokens': 9})
        self.assertNotIn('p', vis._in_flight)

    def test_auto_cooldown_manual_bypass_and_hourly_cap(self):
        with mock.patch.object(vis, 'post_responses_image', side_effect=self._ok):
            vis.interpret(project_id='p', image_bytes=b'i', suffix='.jpg', key='k')
            with self.assertRaises(AIError):
                vis.interpret(project_id='p', image_bytes=b'i', suffix='.jpg', key='k')
            vis.interpret(project_id='p', image_bytes=b'i', suffix='.jpg', key='k',
                          manual=True)
        vis._calls_window['p'] = (vis.time.monotonic(), vis.WINDOW_MAX)
        with mock.patch.object(vis, 'post_responses_image', side_effect=self._ok) as m:
            with self.assertRaises(AIError):
                vis.interpret(project_id='p', image_bytes=b'i', suffix='.jpg',
                              key='k', manual=True)
        self.assertEqual(m.call_count, 0)

    def test_busy_and_unparseable(self):
        vis._in_flight.add('p')
        with mock.patch.object(vis, 'post_responses_image', side_effect=self._ok) as m:
            with self.assertRaises(AIError):
                vis.interpret(project_id='p', image_bytes=b'i', suffix='.jpg',
                              key='k', manual=True)
        self.assertEqual(m.call_count, 0)
        vis._in_flight.clear()

        def _garbage(*a, **k):
            return {'status': 'completed', 'text': 'nope', 'usage': {},
                    'response_id': ''}
        with mock.patch.object(vis, 'post_responses_image', side_effect=_garbage):
            with self.assertRaises(AIError):
                vis.interpret(project_id='p', image_bytes=b'i', suffix='.jpg',
                              key='k', manual=True)


class TransportShapeTests(unittest.TestCase):
    def test_request_shape_and_usage(self):
        from pcb_checker.ai import meta as meta_mod
        seen = {}

        def fake_request(url, payload, auth, provider, **kw):
            seen['url'] = url
            seen['payload'] = payload
            self.assertNotIn('key', json.dumps(auth).lower().replace('bearer', ''))
            return {'id': 'resp_1', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': '{"board": {}}'}]}],
                'usage': {'input_tokens': 524,
                          'input_tokens_details': {'cached_tokens': 3},
                          'output_tokens': 100, 'total_tokens': 624}}

        # Patch where post_responses_image looks it up (local import).
        import pcb_checker.ai.reviewer as reviewer_mod
        with mock.patch.object(reviewer_mod, '_request', side_effect=fake_request):
            out = meta_mod.post_responses_image(
                image_b64='QUJD', mime='image/jpeg', prompt='p', key='secret')
        self.assertEqual(seen['url'], meta_mod.RESPONSES_ENDPOINT)
        payload = seen['payload']
        self.assertEqual(payload['model'], meta_mod.MODEL_ID)
        block = payload['input'][0]['content'][1]
        self.assertEqual(block['type'], 'input_image')
        self.assertTrue(block['image_url'].startswith('data:image/jpeg;base64,QUJD'))
        self.assertEqual(payload['reasoning'], {'effort': 'minimal'})
        self.assertEqual(out['text'], '{"board": {}}')
        self.assertEqual(out['usage']['cached_input_tokens'], 3)
        self.assertEqual(out['response_id'], 'resp_1')

    def test_empty_output_rejected_and_recorded(self):
        from pcb_checker.ai import meta as meta_mod
        import pcb_checker.ai.reviewer as reviewer_mod
        records = []
        meta_mod.set_usage_recorder(records.append)
        try:
            with mock.patch.object(reviewer_mod, '_request', return_value={'output': []}):
                with self.assertRaises(AIError):
                    meta_mod.post_responses_image(
                        image_b64='QUJD', mime='image/jpeg', prompt='p', key='k')
        finally:
            meta_mod.clear_usage_recorder(records.append)
        self.assertEqual(records[-1]['status'], 'incomplete')
        self.assertEqual(records[-1]['model'], 'meta/' + meta_mod.MODEL_ID)


class FitForVisionTests(unittest.TestCase):
    def _noise_jpeg(self, w=3000, h=2000):
        import io
        from PIL import Image
        img = Image.effect_noise((w, h), 128).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=95)
        return buf.getvalue()

    def test_small_passthrough(self):
        blob = b'x' * 100
        self.assertEqual(vis._fit_for_vision(blob, '.png'), (blob, '.png'))

    def test_oversize_downscaled_under_cap(self):
        raw = self._noise_jpeg()
        self.assertGreater(len(raw), vis.MAX_IMAGE_BYTES)
        out, suffix = vis._fit_for_vision(raw, '.JPG')
        self.assertLessEqual(len(out), vis.MAX_IMAGE_BYTES)
        self.assertEqual(suffix, '.jpg')
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(out))
        im.load()
        self.assertLessEqual(max(im.size), 1600)

    def test_garbage_rejected(self):
        with self.assertRaises(AIError):
            vis._fit_for_vision(b'\x00\x01broken' * 500000, '.jpg')


class BatchTests(unittest.TestCase):
    def setUp(self):
        vis._in_flight.clear()
        vis._last_call.clear()
        vis._calls_window.clear()

    def _img(self, size=50000):
        import io
        from PIL import Image
        img = Image.effect_noise((400, 300), 128).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=90)
        blob = buf.getvalue()
        return blob if len(blob) >= size else blob + b'\x00' * (size - len(blob))

    def _resp(self, text):
        return {'text': text, 'usage': {'input_tokens': 10, 'output_tokens': 5,
                                        'cached_input_tokens': 0}, 'response_id': 'r1'}

    def test_photo_source_kept_and_validated(self):
        import json
        obs = {'board': {}, 'components': [
            {'kind': 'resistor', 'label': 'r', 'bbox': [0, 0, 1, 1],
             'photo': 'a.jpg', 'seen_in': ['a.jpg', 'evil.jpg']}],
            'wires': [], 'questions': []}
        out = vis.sanitize(json.dumps(obs), known_photos={'a.jpg'})
        self.assertEqual(out['components'][0]['photo'], 'a.jpg')
        self.assertEqual(out['components'][0]['seen_in'], ['a.jpg'])
        out2 = vis.sanitize(json.dumps(obs), known_photos={'b.jpg'})
        self.assertEqual(out2['components'][0]['photo'], '')
        self.assertEqual(out2['components'][0]['seen_in'], [])

    def test_batch_single_request_no_cooldown(self):
        import json
        from unittest import mock
        body = self._img()
        text = json.dumps({'board': {}, 'components': [], 'wires': [], 'questions': []})
        with mock.patch.object(vis, 'post_responses_image',
                               return_value=self._resp(text)) as post:
            photos = [{'name': f'p{i}.jpg', 'bytes': body, 'suffix': '.jpg'} for i in range(3)]
            out = vis.interpret_batch(project_id='b1', photos=photos, key='k')
            out2 = vis.interpret_batch(project_id='b1', photos=photos[:1], key='k')
        self.assertEqual(post.call_count, 2)  # two batches, no cooldown between
        self.assertEqual(out['usage']['requests'], 1)
        self.assertEqual(len(out['batches'][0]['photos']), 3)
        self.assertEqual(out['errors'], [])
        # One multimodal request carried all three images.
        sent = post.call_args_list[0].kwargs['images']
        self.assertEqual(len(sent), 3)
        self.assertIn('SAME physical', post.call_args_list[0].kwargs['prompt'])

    def test_batch_splits_by_bytes(self):
        import json
        from unittest import mock
        big = self._img()
        text = json.dumps({'board': {}, 'components': [], 'wires': [], 'questions': []})
        with mock.patch.object(vis, 'post_responses_image',
                               return_value=self._resp(text)) as post, \
                mock.patch.object(vis, 'BATCH_MAX_BYTES', len(big) + 1):
            photos = [{'name': f'p{i}.jpg', 'bytes': big, 'suffix': '.jpg'} for i in range(3)]
            out = vis.interpret_batch(project_id='b2', photos=photos, key='k')
        self.assertEqual(out['usage']['requests'], 3)
        self.assertEqual(post.call_count, 3)
        self.assertEqual([len(b['photos']) for b in out['batches']], [1, 1, 1])

    def test_batch_one_bad_photo_keeps_rest(self):
        import json
        from unittest import mock
        text = json.dumps({'board': {}, 'components': [], 'wires': [], 'questions': []})
        with mock.patch.object(vis, 'post_responses_image',
                               return_value=self._resp(text)) as post:
            photos = [{'name': 'good.jpg', 'bytes': self._img(), 'suffix': '.jpg'},
                      {'name': 'bad.gif', 'bytes': self._img(), 'suffix': '.gif'}]
            out = vis.interpret_batch(project_id='b3', photos=photos, key='k')
        self.assertEqual(post.call_count, 1)
        self.assertEqual(out['batches'][0]['photos'], ['good.jpg'])
        self.assertEqual(len(out['errors']), 1)
        self.assertEqual(out['errors'][0]['photo'], 'bad.gif')


class HoleEndsTests(unittest.TestCase):
    def test_grammar_and_range(self):
        self.assertEqual(vis._clean_hole_end('t12C'), 't12C')
        self.assertEqual(vis._clean_hole_end('T12c'), 't12C')
        self.assertEqual(vis._clean_hole_end('t1a'), 't1A')
        self.assertEqual(vis._clean_hole_end('rL+5'), 'rL+5')
        self.assertEqual(vis._clean_hole_end('RR-20'), 'rR-20')
        self.assertEqual(vis._clean_hole_end(' rl - 3 '), 'rL-3')
        for bad in ('', 't12', 't12CC', 'A5', 'hello', None, 42,
                    't0A', 't64A', 't99Z', 'zzz', 'rX+5', 'rLL+5',
                    'rL+0', 'rL+64', 't5K'):
            self.assertEqual(vis._clean_hole_end(bad), '', msg=repr(bad))

    def test_sanitize_carries_hole_ends(self):
        obs = {'wires': [
            {'color': 'red', 'bbox': [0.1, 0.1, 0.5, 0.5], 'endpoints': 'rail to terminal',
             'hole_ends': ['t12C', 'rL+5'], 'confidence': 'high'},
            {'color': 'blue', 'bbox': [0.1, 0.1, 0.5, 0.5], 'endpoints': 'x',
             'hole_ends': ['bogus', 't99Z'], 'confidence': 'low'},
            {'color': 'black', 'bbox': [0.1, 0.1, 0.5, 0.5], 'endpoints': 'y',
             'confidence': 'low'}]}
        out = vis.sanitize(json.dumps(obs))
        self.assertEqual(out['wires'][0]['hole_ends'], ['t12C', 'rL+5'])
        self.assertEqual(out['wires'][1]['hole_ends'], ['', ''])
        self.assertEqual(out['wires'][2]['hole_ends'], ['', ''])

    def test_prompts_scope_hole_ids_to_closeups(self):
        for prompt in (vis.SYSTEM_PROMPT, vis.BATCH_SYSTEM_PROMPT):
            self.assertIn('hole_ends', prompt)
            self.assertIn('CLOSE-UP or REGISTERED', prompt)
            # IMG_6259 proves continuous printed numbering: never assert
            # repeated numbers as fact, only conditional disambiguation.
            self.assertIn('read them as printed', prompt)
            self.assertNotIn('numbers repeat', prompt.lower())


if __name__ == '__main__':
    unittest.main()
