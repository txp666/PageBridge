"""Compatibility of the optional reading path with the existing worker protocol."""
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


def envelope(**fields):
    return dict(protocolVersion=1, jobId='reading-job', attemptId='reading-attempt', **fields)


def fixture_path(*parts):
    return str(Path(tempfile.gettempdir()).resolve().joinpath('pagebridge-reading-protocol', *parts))


def reading_request(**fields):
    data=envelope(action='translate', inputPath=fixture_path('source.pdf'), outputDirectory=fixture_path('reading-output'),
                  service=dict(kind='openai',baseURL='https://example.com/v1',model='model',apiKey='fixture-key',timeoutSeconds=30,concurrency=2),
                  outputMode='reading')
    data.update(fields)
    return worker.parse_request(json.dumps(data).encode())


class ReadingProtocolTests(unittest.TestCase):
    def test_preparation_needs_no_service_or_credentials(self):
        result=worker.parse_request(json.dumps(envelope(action='prepare_reading')).encode())
        self.assertEqual(result['action'],'prepare_reading')
        self.assertNotIn('service',result)

    def test_legacy_translation_keeps_pdf_default(self):
        original=reading_request();original.pop('outputMode')
        result=worker.parse_request(json.dumps(original).encode())
        self.assertEqual(result['outputMode'],'pdf')

    def test_retry_is_bound_to_reading_artifact_and_explicit_blocks(self):
        result=reading_request(readingPath=fixture_path('previous','reading.json'),retryBlockIds=['p0001-b0002'])
        self.assertEqual(result['retryBlockIds'],['p0001-b0002'])
        for changes in (dict(readingPath='relative.json',retryBlockIds=['p1-b2']),
                        dict(readingPath=fixture_path('reading.json'),retryBlockIds=[]),
                        dict(readingPath=fixture_path('reading.json'),retryBlockIds=['p1-b2','p1-b2']),
                        dict(readingPath=fixture_path('reading.json'),retryBlockIds=['../bad']),
                        dict(outputMode='pdf',readingPath=fixture_path('reading.json'),retryBlockIds=['p1-b2']),
                        dict(retryBlockIds=['p1-b2'])):
            with self.subTest(changes=changes),self.assertRaises(worker.WorkerError):
                reading_request(**changes)

    def test_reading_dispatch_bypasses_pdf_text_and_quality_gates(self):
        translate=AsyncMock();prepare=AsyncMock()
        module=SimpleNamespace(translate=translate,prepare=prepare)
        request=reading_request();events=worker.Events(io.StringIO(),request)
        with patch.dict(sys.modules,structured_reading=module),patch.object(worker,'translate',side_effect=AssertionError('legacy PDF path called')):
            asyncio.run(worker.run(request,events))
        translate.assert_awaited_once_with(request,events)
        prepare.assert_not_called()

    def test_prepare_dispatch_does_not_start_translation(self):
        translate=AsyncMock();prepare=AsyncMock()
        module=SimpleNamespace(translate=translate,prepare=prepare)
        request=envelope(action='prepare_reading');events=worker.Events(io.StringIO(),request)
        with patch.dict(sys.modules,structured_reading=module):
            asyncio.run(worker.run(request,events))
        prepare.assert_awaited_once_with(events);translate.assert_not_called()


if __name__=='__main__': unittest.main()
