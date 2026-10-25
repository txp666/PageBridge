import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import pymupdf
from pagebridge.core import AppError, DocumentStore, sha256
from pagebridge import literature as lit


class Vault:
    storage_description = 'isolated test vault'
    def __init__(self): self.values = {}; self.reads = 0
    def get(self, identifier): self.reads += 1; return self.values.get(identifier, '')
    def set(self, identifier, value): self.values[identifier] = value


class Response(io.BytesIO):
    status = 200
    def __init__(self, data, url='https://example.org/article.pdf', mime='application/pdf', declared=None, encoding=None, on_read=None):
        super().__init__(data)
        self.url, self.on_read = url, on_read
        self.headers = {'Content-Type': mime, 'Content-Length': str(len(data) if declared is None else declared)}
        if encoding: self.headers['Content-Encoding'] = encoding
    def geturl(self): return self.url
    def read1(self, count):
        value = super().read(count)
        if self.on_read: self.on_read()
        return value


class LiteratureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'library'
        self.store = DocumentStore(self.root)
        self.settings = dict(source='sciHub', scihub_base_url=lit.DEFAULT_SCIHUB)
        self.work = dict(id='https://openalex.org/W1', title='Public reading fixture', doi='10.1371/journal.pcbi.1003833',
                         pdf_url='https://example.org/article.pdf', landing_url='https://example.org/article')
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((30, 50), 'Public PDF fixture for literature import')
            self.pdf_bytes = pdf.tobytes()

    def tearDown(self): self.temporary.cleanup()

    @contextmanager
    def network(self, response, captured=None):
        class Opener:
            def open(self, request, timeout):
                if captured is not None: captured.append(request)
                if isinstance(response, Exception): raise response
                return response
        with patch.object(lit, 'validate_addresses'), patch.object(lit.urllib.request, 'build_opener', return_value=Opener()):
            yield

    def request(self, **values):
        return dict(work=copy.deepcopy(self.work), source='openAccess', operation_id=str(uuid.uuid4()), **values)

    def test_settings_store_only_metadata_and_optional_key_in_vault(self):
        vault = Vault(); settings = lit.LiteratureSettings(self.root, vault)
        self.assertFalse(settings.snapshot()['openalex_has_key']); self.assertEqual(vault.reads, 0)
        saved = settings.save(dict(source='openAccess', scihub_base_url='https://example.org/sci/', openalex_api_key='fixture-private-key'))
        self.assertTrue(saved['openalex_has_key']); self.assertNotIn('fixture-private-key', json.dumps(saved))
        self.assertNotIn('fixture-private-key', settings.path.read_text())
        reopened = lit.LiteratureSettings(self.root, vault)
        self.assertEqual(reopened.key(), 'fixture-private-key')
        reopened.save(dict(source='sciHub'))
        self.assertEqual(reopened.key(), 'fixture-private-key')
        self.assertFalse(reopened.save(dict(openalex_api_key=''))['openalex_has_key'])
        self.assertEqual(reopened.key(), '')

    def test_settings_atomic_failure_rolls_back_private_key(self):
        vault = Vault(); settings = lit.LiteratureSettings(self.root, vault)
        settings.save(dict(openalex_api_key='old-key'))
        before = settings.path.read_bytes()
        with patch.object(lit, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): settings.save(dict(openalex_api_key='new-key'))
        self.assertEqual(settings.key(), 'old-key'); self.assertEqual(settings.path.read_bytes(), before)

    def test_settings_validate_base_and_secret_without_network(self):
        settings = lit.LiteratureSettings(self.root, Vault())
        for base in ('http://example.org', 'https://user:secret@example.org', 'https://localhost', 'https://example.org?a=1', 'https://example.org#fragment'):
            with self.assertRaises(AppError): settings.save(dict(scihub_base_url=base))
        for key in ('bad\nkey', ['secret'], 'x' * 2561):
            with self.assertRaises(AppError): settings.save(dict(openalex_api_key=key))
        self.assertFalse(settings.path.exists())

    def test_corrupt_settings_are_not_overwritten(self):
        path = self.root / 'desktop-literature.json'; path.write_text('{"version":99}')
        with self.assertRaisesRegex(AppError, '保留'): lit.LiteratureSettings(self.root, Vault())
        self.assertEqual(path.read_text(), '{"version":99}')

    def test_doi_normalization_plus_and_exact_filter(self):
        doi='10.1234/abc+def'
        self.assertEqual(lit.normalized_doi(' DOI: 10.1234/ABC+DEF '), doi)
        self.assertEqual(lit.normalized_doi('https://doi.org/10.1234/abc%2Bdef'), doi)
        self.assertIsNone(lit.normalized_doi('title only'))
        captured=[]
        with self.network(Response(b'{"results":[],"meta":{"count":0}}',mime='application/json'),captured):
            self.assertEqual(lit.search(dict(query=doi,open_access_only=True))['works'], [])
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(captured[0].full_url).query)
        self.assertEqual(query['filter'], ['doi:https://doi.org/10.1234/abc+def,is_oa:true'])
        self.assertNotIn('search',query)
        for invalid in ('10.1234/a|b','10.1234/a,b'):
            with self.assertRaises(AppError): lit.search(dict(query=invalid))

    def test_search_maps_only_oa_pdf_and_keeps_pagination_after_bad_rows(self):
        work=dict(id='https://openalex.org/W1',display_name=' A\n title ',doi='https://doi.org/10.1234/Abc',publication_year=2026,
                  authorships=[dict(author=dict(display_name='Jane'))]*2+[dict(author=dict(display_name='Joe'))],
                  primary_location=dict(is_oa=False,pdf_url='https://example.org/closed.pdf',landing_page_url='https://example.org/article',source=dict(display_name='Journal')),
                  best_oa_location=dict(is_oa=True,pdf_url='https://example.org/open.pdf'))
        data=json.dumps(dict(results=[None,work,work,dict(id='bad')],meta=dict(count=60))).encode()
        with self.network(Response(data,mime='application/json')):
            result=lit.search(dict(query='title',page=1))
        self.assertTrue(result['has_more']); self.assertEqual(len(result['works']),1)
        parsed=result['works'][0]
        self.assertEqual(parsed['authors'],['Jane','Joe']); self.assertEqual(parsed['pdf_url'],'https://example.org/open.pdf')
        self.assertEqual(parsed['title'],'A title'); self.assertEqual(parsed['doi'],'10.1234/abc')
        work.pop('best_oa_location')
        self.assertIsNone(lit._work(work)['pdf_url'])

    def test_search_key_is_header_only_and_not_echoed_on_http_error(self):
        captured=[]
        error=urllib.error.HTTPError('https://api.openalex.org/works',403,'fixture-private-key',{},None)
        with self.network(error,captured):
            with self.assertRaises(AppError) as raised: lit.search(dict(query='title'),api_key='fixture-private-key')
        self.assertNotIn('fixture-private-key',str(raised.exception))
        self.assertNotIn('fixture-private-key',captured[0].full_url)
        self.assertEqual(captured[0].get_header('Authorization'),'Bearer fixture-private-key')
        self.assertIsNone(captured[0].get_header('Cookie'))

    def test_search_limits_and_malformed_json(self):
        for request in (dict(query=''),dict(query='x'*1025),dict(query='a',page=0),dict(query='a',page=True),dict(query='a',page=501),dict(query='a',open_access_only=1)):
            with self.assertRaises(AppError): lit.search(request)
        with self.network(Response(b'not json',mime='application/json')):
            with self.assertRaisesRegex(AppError,'检索结果'): lit.search(dict(query='title'))
        with self.network(Response(b'{}',mime='application/json',declared=lit.MAX_METADATA+1)):
            with self.assertRaisesRegex(AppError,'大小上限'): lit.search(dict(query='title'))

    def test_public_url_and_dns_reject_private_and_reserved_targets(self):
        for url in ('file:///tmp/a.pdf','http://example.org/a.pdf','https://127.0.0.1/a','https://[::1]/a','https://10.2.3.4/a',
                    'https://user:pass@example.org/a','https://example.org:444/a','https://host.local/a','https://198.18.0.1/a','https://127.1/a'):
            with self.assertRaises(AppError): lit.public_url(url)
        public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        with patch.object(lit.socket,'getaddrinfo',return_value=public): lit.validate_addresses('https://example.org/a')
        with patch.object(lit.socket,'getaddrinfo',return_value=public+[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
            with self.assertRaises(AppError): lit.validate_addresses('https://example.org/a')
        with patch.object(lit.socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.1.1',443))]):
            lit.validate_addresses('https://example.org/a')

    def test_redirects_validate_each_hop_and_never_forward_pdf_credentials(self):
        old=urllib.request.Request('https://example.org/a',headers={'Authorization':'private','Cookie':'session=private','Accept':'application/pdf'})
        redirect=lit._Redirects(None,time.monotonic()+10,None)
        with patch.object(lit,'validate_addresses') as validate:
            target=redirect.redirect_request(old,None,302,'',{},'https://example.org/b')
            self.assertIsNone(target.get_header('Authorization')); self.assertIsNone(target.get_header('Cookie'))
            validate.assert_called_once()
            with self.assertRaises(AppError): redirect.redirect_request(old,None,302,'',{},'https://127.0.0.1/b')
        api=lit._Redirects(None,time.monotonic()+10,'key')
        with patch.object(lit,'validate_addresses'):
            with self.assertRaises(AppError): api.redirect_request(old,None,302,'',{},'https://example.org/b')
            same=api.redirect_request(old,None,302,'',{},'https://api.openalex.org/works?page=2')
            self.assertEqual(same.get_header('Authorization'),'Bearer key')
        redirect.count=5
        with self.assertRaisesRegex(AppError,'重定向'): redirect.redirect_request(old,None,302,'',{},'https://example.org/b')

    def test_scihub_html_resolves_metadata_and_inert_relative_embeds(self):
        html='''<!-- <iframe src="https://evil.org/fake.pdf"> --><script><iframe src="https://evil.org/fake.pdf"></script>
        <meta name="citation_doi" content="10.1371/journal.pcbi.1003833"><iframe id=pdf src="/full.pdf?a=1&amp;b=2"></iframe>'''
        self.assertEqual(lit.pdf_url_from_html(html,'https://example.org/a',self.work['doi']), 'https://example.org/full.pdf?a=1&b=2')
        html='<object type="application/pdf" data="//example.org/full.pdf"></object>'
        self.assertEqual(lit.pdf_url_from_html(html,'https://example.org/a',self.work['doi']),'https://example.org/full.pdf')

    def test_scihub_challenge_wrong_doi_and_unsafe_target_are_not_imported(self):
        for html,phrase in (('<altcha-widget></altcha-widget>','验证'),('<meta name="citation_doi" content="10.1234/wrong"><iframe id=pdf src="/a.pdf">','不一致'),('<iframe id=pdf src="https://127.0.0.1/a.pdf">','私有')):
            with self.assertRaisesRegex(AppError,phrase): lit.pdf_url_from_html(html,'https://example.org/a',self.work['doi'])
        response=Response(b'<altcha-widget></altcha-widget>',url=lit.article_url(self.work['doi'],lit.DEFAULT_SCIHUB),mime='text/html')
        with self.network(response):
            with lit.open_document(self.root,dict(work=self.work,source='sciHub'),self.settings) as result:
                self.assertTrue(result['requires_browser']); self.assertTrue(result['can_try_open_access']); self.assertIn('验证',result['message'])
                self.assertEqual(result['page_url'],lit.article_url(self.work['doi'],lit.DEFAULT_SCIHUB))
        self.assertEqual(self.store.documents(),[])

    def test_scihub_direct_pdf_and_manual_resolved_url(self):
        url=lit.article_url(self.work['doi'],lit.DEFAULT_SCIHUB)
        with self.network(Response(b'',url=url,mime='application/pdf')):
            self.assertEqual(lit.resolve_scihub(self.work['doi'],lit.DEFAULT_SCIHUB),url)
        with self.network(Response(self.pdf_bytes)):
            with patch.object(lit,'resolve_scihub',side_effect=AssertionError('Must use explicit resolved URL')):
                with lit.open_document(self.root,dict(work=self.work,source='sciHub',resolved_url='https://example.org/article.pdf'),self.settings) as result:
                    self.assertIsInstance(result,lit.DownloadedPDF)

    def test_download_validates_then_imports_and_removes_temporary_file(self):
        request=self.request(); request['work']['title']='CON.pdf'
        with self.network(Response(self.pdf_bytes)):
            with lit.open_document(self.root,request,self.settings) as downloaded:
                self.assertEqual(downloaded.path.name,'_CON.pdf')
                source_hash=sha256(downloaded.path)
                document=self.store.import_pdf(downloaded.path)
                self.assertEqual(document['input_sha256'],source_hash)
                self.assertEqual(document['page_count'],1)
        self.assertFalse((self.root/'literature-downloads'/request['operation_id']).exists())
        self.assertEqual(sha256(document['input_path']),source_hash)

    def test_html_truncated_oversize_compressed_and_invalid_pdf_clean_up(self):
        responses=[Response(b'<html>captcha</html>',mime='text/html'),Response(self.pdf_bytes,declared=len(self.pdf_bytes)+1),
                   Response(b'',declared=lit.MAX_PDF+1),Response(b'not a PDF'),Response(self.pdf_bytes,encoding='gzip')]
        for response in responses:
            request=self.request()
            with self.network(response):
                with lit.open_document(self.root,request,self.settings) as result:
                    self.assertIsNone(result['document']); self.assertTrue(result['requires_browser'])
            self.assertFalse((self.root/'literature-downloads'/request['operation_id']).exists())
        self.assertEqual(self.store.documents(),[])

    def test_encrypted_pdf_is_not_imported(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            encrypted=pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='private')
        with self.network(Response(encrypted)):
            with lit.open_document(self.root,self.request(),self.settings) as result:
                self.assertIn('加密',result['message'])
        self.assertEqual(self.store.documents(),[])

    def test_cancelled_download_cleans_job_without_import(self):
        cancelled=threading.Event(); request=self.request()
        with self.network(Response(self.pdf_bytes,on_read=cancelled.set)):
            with self.assertRaises(lit.Cancelled):
                with lit.open_document(self.root,request,self.settings,cancelled): self.fail('Cancelled import reached caller')
        self.assertFalse((self.root/'literature-downloads'/request['operation_id']).exists())
        self.assertEqual(self.store.documents(),[])

    def test_timeout_returns_safe_fallback_without_provider_message(self):
        with self.network(TimeoutError('private internal URL')):
            with lit.open_document(self.root,self.request(),self.settings) as result:
                self.assertIn('超时',result['message']); self.assertNotIn('private',result['message'])
                self.assertEqual(result['page_url'],self.work['landing_url'])

    def test_cleanup_cannot_delete_another_path_or_existing_live_job(self):
        request=self.request(); job=lit._cache(self.root)/request['operation_id']; job.mkdir(); (job/'existing').write_text('keep')
        with self.network(Response(self.pdf_bytes)):
            with lit.open_document(self.root,request,self.settings) as result: self.assertIn('已存在',result['message'])
        self.assertEqual((job/'existing').read_text(),'keep')
        for value in ('../documents',None,'invalid'):
            with self.assertRaises(AppError): lit.cleanup(self.root,value)
        self.assertTrue(lit.cleanup(self.root,request['operation_id'])['cleaned'])
        self.assertFalse(lit.cleanup(self.root,request['operation_id'])['cleaned'])

    @unittest.skipIf(os.name=='nt','Symlink privilege differs')
    def test_redirected_download_directory_is_rejected(self):
        outside=Path(self.temporary.name)/'outside'; outside.mkdir()
        (self.root/'literature-downloads').symlink_to(outside,target_is_directory=True)
        with self.network(Response(self.pdf_bytes)):
            with lit.open_document(self.root,self.request(),self.settings) as result: self.assertIn('重定向',result['message'])
        self.assertEqual(list(outside.iterdir()),[])

    def test_bridge_network_is_outside_lock_and_import_is_inside_lock(self):
        held=[]; original_import=DocumentStore.import_pdf
        @contextmanager
        def lock(_):
            held.append(True)
            try: yield
            finally: held.pop()
        @contextmanager
        def response(*args,**kwargs):
            self.assertEqual(held,[])
            yield Response(self.pdf_bytes),'https://example.org/article.pdf'
        def import_pdf(store,path):
            self.assertEqual(held,[True]); return original_import(store,path)
        with patch.object(bridge,'library_lock',lock),patch.object(lit,'response_for',response),patch.object(DocumentStore,'import_pdf',import_pdf):
            result=bridge.dispatch(dict(action='literature_open',**self.request()),self.root)
        self.assertEqual(result['document']['page_count'],1)
        self.assertEqual(held,[])
        self.assertFalse(lit.NETWORK_ACTIONS & bridge.LOCAL_ACTIONS)
        self.assertTrue(lit.LOCAL_ACTIONS <= bridge.LOCAL_ACTIONS)


if __name__=='__main__': unittest.main()
