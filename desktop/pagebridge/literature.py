"""Public metadata search and anonymous, bounded literature PDF downloads.

The bridge snapshots settings under its library lock, performs all network I/O
without that lock, then imports a validated download under a new lock. No web
scripts, cookies, HTTP credentials or automated challenge handling are used.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .core import AppError, atomic_json, checked_pdf
from .services import CredentialVault

DEFAULT_SCIHUB = 'https://sci-hub.ru'
OPENALEX = 'https://api.openalex.org/works'
KEY_PAGE = 'https://openalex.org/settings/api'
LOCAL_ACTIONS = frozenset({'literature_settings', 'literature_save_settings', 'literature_cleanup'})
NETWORK_ACTIONS = frozenset({'literature_search', 'literature_open'})
MAX_METADATA = 4 * 1024 * 1024
MAX_HTML = 2 * 1024 * 1024
MAX_PDF = 100 * 1024 * 1024
PAGE_SIZE = 20


class Cancelled(AppError):
    pass


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise Cancelled('已取消文献操作。')
    if time.monotonic() > deadline:
        raise AppError('文献请求超时，请稍后重试或打开来源网页。')


def _clean(value, limit):
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split())[:limit]
    return value or None


def normalized_doi(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower().startswith('doi:'):
        value = value[4:].strip()
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme.lower() in {'http', 'https'} and parts.hostname in {'doi.org', 'www.doi.org', 'dx.doi.org'} and not parts.username and not parts.password:
            value = urllib.parse.unquote(parts.path.lstrip('/'))
    except ValueError:
        return None
    if len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value.lower() if re.fullmatch(r'10\.[0-9]{4,9}/[^\s]+', value) else None


def public_url(value):
    """Validate scheme/host shape; DNS is checked immediately before each GET."""
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise AppError('文献地址无效，只支持公开 HTTPS 地址。')
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme.lower() != 'https' or parts.username is not None or parts.password is not None or parts.port not in (None, 443):
            raise ValueError()
        raw_host = parts.hostname
        if not raw_host or '%' in raw_host or '\\' in raw_host:
            raise ValueError()
        host = raw_host.rstrip('.').encode('idna').decode('ascii').lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split('.')
            reserved = {'localhost', 'local', 'internal', 'lan', 'home', 'invalid', 'test', 'onion'}
            if (len(labels) < 2 or labels[-1] in reserved or not any(c.isalpha() for c in labels[-1])
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
                raise ValueError()
        else:
            if not _public_address(address):
                raise ValueError()
        netloc = ('[' + host + ']') if ':' in host else host
        return urllib.parse.urlunsplit(('https', netloc, parts.path or '/', parts.query, ''))
    except (ValueError, UnicodeError):
        raise AppError('文献地址指向本机、私有网络或不受支持的主机，已停止访问。') from None


def _public_address(address):
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if address.version == 6:
        return (address in ipaddress.ip_network('2000::/3') and address not in ipaddress.ip_network('2002::/16')
                and address not in ipaddress.ip_network('2001::/32') and address not in ipaddress.ip_network('2001:10::/28')
                and address not in ipaddress.ip_network('2001:20::/28'))
    return True


def validate_addresses(url):
    host = urllib.parse.urlsplit(public_url(url)).hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    try:
        addresses = [literal] if literal else [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    except (OSError, ValueError):
        raise AppError('无法解析文献地址，请检查网络或打开来源网页。') from None
    # Match the Mac TUN-proxy policy: synthetic addresses are accepted only for
    # public DNS names. TLS still verifies that original name. This is preflight
    # filtering, not DNS pinning; the HTTP stack resolves again when connecting.
    if not addresses or not all(_public_address(a) or (literal is None and a.version == 4 and a in ipaddress.ip_network('198.18.0.0/15')) for a in addresses):
        raise AppError('文献地址解析到本机或私有网络，已停止访问。')


def _optional_url(value):
    try:
        return public_url(value)
    except AppError:
        return None


def scihub_base(value):
    try:
        fragment = urllib.parse.urlsplit(value.strip()).fragment if isinstance(value, str) else True
    except ValueError:
        raise AppError('Sci-Hub 地址无效，请填写 HTTPS 公共域名。') from None
    if fragment:
        raise AppError('Sci-Hub 地址不能包含片段或查询参数。')
    url = public_url(value.strip() if isinstance(value, str) else value)
    parts = urllib.parse.urlsplit(url)
    if parts.query or not any(c.isalpha() for c in parts.hostname) or ':' in parts.hostname:
        raise AppError('Sci-Hub 地址需要是不含查询参数的 HTTPS 公共域名。')
    return url.rstrip('/')


def article_url(doi, base):
    doi = normalized_doi(doi)
    if not doi:
        raise AppError('请输入有效 DOI，或选择含 DOI 的文献。')
    return scihub_base(base) + '/' + urllib.parse.quote(doi, safe='/():;@!$&\'=,+-._~')


class LiteratureSettings:
    def __init__(self, root, vault=None):
        self.root = Path(root).resolve()
        self.path = self.root / 'desktop-literature.json'
        self.vault = vault or CredentialVault()
        self.key_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'PageBridge/OpenAlex/' + str(self.root)))
        self.value = dict(version=1, source='sciHub', scihub_base_url=DEFAULT_SCIHUB, key_configured=False)
        if self.path.is_symlink() or self.path.resolve() != self.path:
            raise AppError('文献设置路径被重定向，未读取配置。')
        if self.path.exists():
            try:
                if self.path.stat().st_size > 16384:
                    raise ValueError()
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if value.get('version') != 1 or value.get('source') not in ('sciHub', 'openAccess'):
                    raise ValueError()
                if type(value.get('key_configured', False)) is not bool:
                    raise ValueError()
                self.value = dict(version=1, source=value['source'], scihub_base_url=scihub_base(value['scihub_base_url']), key_configured=value.get('key_configured', False))
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
                raise AppError('文献设置损坏，原文件已保留。') from None

    def key(self):
        # Anonymous searches do not touch the Windows credential API at all.
        return self.vault.get(self.key_id) if self.value['key_configured'] else ''

    def snapshot(self):
        return dict(source=self.value['source'], scihub_base_url=self.value['scihub_base_url'],
                    openalex_has_key=bool(self.key()), credential_storage=self.vault.storage_description,
                    openalex_key_url=KEY_PAGE)

    def save(self, request):
        source = request.get('source', self.value['source'])
        if source not in ('sciHub', 'openAccess'):
            raise AppError('请选择 Sci-Hub 或公开 PDF 来源。')
        value = dict(version=1, source=source, scihub_base_url=scihub_base(request.get('scihub_base_url', self.value['scihub_base_url'])), key_configured=self.value['key_configured'])
        key_changed = False
        old_key = self.key() if 'openalex_api_key' in request else None
        if 'openalex_api_key' in request:
            key = request['openalex_api_key']
            if not isinstance(key, str) or len(key.encode('utf-8')) > 2560 or any(c in key for c in '\r\n\0'):
                raise AppError('OpenAlex API Key 格式无效。')
            if key or self.value['key_configured']:
                self.vault.set(self.key_id, key)
                key_changed = True
            value['key_configured'] = bool(key)
        try:
            atomic_json(self.path, value)
        except Exception:
            if key_changed:
                self.vault.set(self.key_id, old_key)
            raise
        self.value = value
        return self.snapshot()


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, cancel, deadline, api_key):
        self.cancel, self.deadline, self.api_key, self.count = cancel, deadline, api_key, 0

    def redirect_request(self, request, fp, code, message, headers, new_url):
        self.count += 1
        if self.count > 5:
            raise AppError('文献地址重定向过多，请打开来源网页。')
        _check(self.cancel, self.deadline)
        clean = public_url(new_url)
        validate_addresses(clean)
        if self.api_key is not None and urllib.parse.urlsplit(clean).hostname != 'api.openalex.org':
            raise AppError('检索服务跳转到其他网站，已停止发送凭据。')
        return _request(clean, request.get_header('Accept'), self.api_key)


def _request(url, accept, api_key=None):
    headers = {'Accept': accept, 'Accept-Encoding': 'identity', 'User-Agent': 'PageBridge/Windows (document reader)'}
    if api_key:
        if not isinstance(api_key, str) or any(c in api_key for c in '\r\n\0'):
            raise AppError('OpenAlex API Key 格式无效。')
        if urllib.parse.urlsplit(url).hostname != 'api.openalex.org':
            raise AppError('OpenAlex 密钥仅能发送到官方检索服务。')
        headers['Authorization'] = 'Bearer ' + api_key
    return urllib.request.Request(url, headers=headers, method='GET')


@contextmanager
def response_for(url, *, accept, api_key=None, cancel=None, deadline=None):
    deadline = deadline or (time.monotonic() + 45)
    _check(cancel, deadline)
    clean = public_url(url)
    validate_addresses(clean)
    _check(cancel, deadline)
    opener = urllib.request.build_opener(_Redirects(cancel, deadline, api_key))
    try:
        with opener.open(_request(clean, accept, api_key), timeout=min(25, max(0.1, deadline-time.monotonic()))) as response:
            if response.status != 200:
                raise AppError(f'文献服务返回 HTTP {response.status}，请打开来源网页或稍后重试。')
            final = public_url(response.geturl())
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise AppError('文献服务器返回了不受支持的压缩格式，请打开来源网页。')
            _check(cancel, deadline)
            yield response, final
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            message = 'OpenAlex 拒绝访问，请检查或填写自己的 API Key。' if api_key is not None else '来源网站需要访问验证，请打开网页手动处理；应用不会自动解验证码。'
        elif error.code == 429:
            message = '检索请求过于频繁或额度已用完，请稍后重试；可在设置中填写自己的 OpenAlex API Key。' if api_key is not None else '来源网站请求过于频繁，请稍后重试。'
        elif error.code == 404:
            message = '来源未找到这篇文献，请尝试其他公开 PDF 来源或打开网页。'
        else:
            message = f'文献服务暂时不可用（HTTP {error.code}），请稍后重试或打开来源网页。'
        raise AppError(message) from None
    except (TimeoutError, socket.timeout):
        raise AppError('文献连接超时，请稍后重试或打开来源网页。') from None
    except (urllib.error.URLError, OSError):
        _check(cancel, deadline)
        raise AppError('无法连接文献来源，请检查网络和站点地址。') from None
    except http.client.HTTPException:
        raise AppError('文献响应不完整，请重新下载或打开来源网页。') from None


def _chunks(response, limit, cancel, deadline):
    declared = response.headers.get('Content-Length')
    if declared is not None:
        try:
            declared = int(declared)
        except ValueError:
            raise AppError('文献来源返回了无效的文件大小。') from None
        if declared < 0 or declared > limit:
            raise AppError('文献内容超过大小上限。PDF 最多支持 100 MB。')
    received = 0
    reader = getattr(response, 'read1', response.read)
    while True:
        _check(cancel, deadline)
        block = reader(min(64 * 1024, limit - received + 1))
        if not block:
            break
        received += len(block)
        if received > limit:
            raise AppError('文献内容超过大小上限。PDF 最多支持 100 MB。')
        yield block
    _check(cancel, deadline)
    if declared is not None and received != declared:
        raise AppError('文献下载不完整，请重新下载。')


def _work(raw):
    if not isinstance(raw, dict):
        return None
    title = _clean(raw.get('display_name'), 800)
    doi = normalized_doi(raw.get('doi'))
    identifier = _clean(raw.get('id'), 512) or doi
    if not title or not identifier:
        return None
    extras = raw.get('locations') if isinstance(raw.get('locations'), list) else []
    locations = [raw.get('best_oa_location'), raw.get('primary_location')] + extras[:100]
    locations = [item for item in locations if isinstance(item, dict)]
    pdf = next((_optional_url(item.get('pdf_url')) for item in locations if item.get('is_oa') is True and _optional_url(item.get('pdf_url'))), None)
    landing = next((_optional_url(item.get('landing_page_url')) for item in [raw.get('primary_location')] + locations if isinstance(item, dict) and _optional_url(item.get('landing_page_url'))), None)
    landing = landing or ('https://doi.org/' + urllib.parse.quote(doi, safe='/') if doi else None)
    authors = []
    for item in raw.get('authorships', []) if isinstance(raw.get('authorships', []), list) else []:
        author = item.get('author') if isinstance(item, dict) else None
        name = _clean(author.get('display_name'), 120) if isinstance(author, dict) else None
        if name and name not in authors:
            authors.append(name)
        if len(authors) == 8:
            break
    publication = next((_clean(item['source'].get('display_name'), 240) for item in locations if isinstance(item.get('source'), dict) and _clean(item['source'].get('display_name'), 240)), None)
    year = raw.get('publication_year')
    return dict(id=identifier, title=title, authors=authors, year=year if type(year) is int and 1 <= year <= 9999 else None,
                publication=publication, doi=doi, landing_url=landing, pdf_url=pdf)


def search(request, api_key='', cancel=None):
    query, page, oa = request.get('query'), request.get('page', 1), request.get('open_access_only', False)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1024:
        raise AppError('请输入不超过 1024 字符的标题、关键词或 DOI。')
    if type(page) is not int or not 1 <= page <= 500 or type(oa) is not bool:
        raise AppError('检索页码或公开获取筛选无效。')
    query = query.strip()
    params = {'per_page': PAGE_SIZE, 'page': page, 'select': 'id,doi,display_name,publication_year,authorships,primary_location,best_oa_location,locations,open_access'}
    filters, doi = [], normalized_doi(query)
    if doi:
        if ',' in doi or '|' in doi:
            raise AppError('这个 DOI 含检索服务不支持的字符，请改用标题搜索。')
        filters.append('doi:https://doi.org/' + doi)
    else:
        params['search'] = query
    if oa:
        filters.append('is_oa:true')
    if filters:
        params['filter'] = ','.join(filters)
    url = OPENALEX + '?' + urllib.parse.urlencode(params)
    deadline = time.monotonic() + 45
    with response_for(url, accept='application/json', api_key=api_key, cancel=cancel, deadline=deadline) as (response, _):
        data = b''.join(_chunks(response, MAX_METADATA, cancel, deadline))
    try:
        result = json.loads(data)
        rows = result['results']
        if not isinstance(rows, list):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AppError('无法读取文献检索结果，请稍后重试。') from None
    works, seen = [], set()
    for row in rows[:PAGE_SIZE]:
        work = _work(row)
        if work and (work['doi'] or work['id']) not in seen:
            works.append(work)
            seen.add(work['doi'] or work['id'])
    total = result.get('meta', {}).get('count') if isinstance(result.get('meta'), dict) else None
    has_more = page < 500 and bool(rows) and ((page * PAGE_SIZE < total) if type(total) is int else len(rows) >= PAGE_SIZE)
    return dict(works=works, has_more=has_more, page=page)


class _PDFHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.dois, self.preferred, self.embedded, self.visible = [], [], [], []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ('script', 'style'):
            self.skip += 1
            return
        if self.skip:
            return
        attrs = dict(attrs)
        self.visible.append(tag)
        if tag == 'meta':
            name = (attrs.get('name') or attrs.get('property') or '').lower()
            if name == 'citation_doi' and attrs.get('content'):
                self.dois.append(attrs['content'])
            elif name == 'citation_pdf_url' and attrs.get('content'):
                self.preferred.append(attrs['content'])
        elif tag == 'object' and (attrs.get('type') or '').lower() == 'application/pdf' and attrs.get('data'):
            self.embedded.append(attrs['data'])
        elif tag in ('iframe', 'embed') and attrs.get('src'):
            url = attrs['src']
            if (attrs.get('type') or '').lower() == 'application/pdf' or '.pdf' in url.lower() or (attrs.get('id') or '').lower() == 'pdf':
                self.embedded.append(url)
        self.visible.extend(str(value) for key, value in attrs.items() if key in ('id', 'class') and value)

    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.visible.append(data)


def pdf_url_from_html(html, page_url, expected_doi):
    parser = _PDFHTML()
    parser.feed(html)
    expected = normalized_doi(expected_doi)
    if any(normalized_doi(value) != expected for value in parser.dois):
        raise AppError('Sci-Hub 返回的 DOI 与所选文献不一致，已停止打开。')
    for candidate in parser.preferred + parser.embedded:
        if candidate.strip():
            # Fail on an unsafe advertised target rather than silently trying an
            # unrelated link later in the document.
            return public_url(urllib.parse.urljoin(page_url, candidate.strip()))
    if re.search(r'captcha|altcha-widget|challenge|验证码|verify you are human', ' '.join(parser.visible), re.I):
        raise AppError('Sci-Hub 页面需要验证，请打开网页手动处理；应用不会自动解验证码。')
    raise AppError('Sci-Hub 暂未提供可直接打开的 PDF，请打开网页查看或选择公开 PDF 来源。')


def resolve_scihub(doi, base, cancel=None):
    page_url = article_url(doi, base)
    deadline = time.monotonic() + 45
    with response_for(page_url, accept='text/html, application/pdf;q=0.9', cancel=cancel, deadline=deadline) as (response, final):
        if response.headers.get('Content-Type', '').split(';')[0].strip().lower() == 'application/pdf':
            return final
        raw = b''.join(_chunks(response, MAX_HTML, cancel, deadline))
    try:
        html = raw.decode('utf-8')
    except UnicodeDecodeError:
        html = raw.decode('latin-1')
    return pdf_url_from_html(html, final, doi)


def filename(title):
    value = re.sub(r'[\x00-\x1f\x7f/\\:?%*|"<>]', ' ', title)
    value = ' '.join(value.split()).strip('. ')
    if value.lower().endswith('.pdf'):
        value = value[:-4]
    value = value[:100].strip('. ')
    while len(value.encode('utf-8')) > 220:
        value = value[:-1]
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '_' + value
    return (value or '文献') + '.pdf'


def _operation_id(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise AppError('文献下载任务标识无效。') from None


def _cache(root):
    path = Path(root).resolve() / 'literature-downloads'
    if path.is_symlink() or path.resolve() != path:
        raise AppError('文献临时目录被重定向，已停止下载。')
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup(root, operation_id):
    directory = _cache(root) / _operation_id(operation_id)
    if directory.is_symlink() or directory.resolve() != directory:
        raise AppError('文献临时目录被重定向，未删除任何文件。')
    if directory.exists():
        shutil.rmtree(directory)
        return {'cleaned': True}
    return {'cleaned': False}


def _prune(root):
    for path in _cache(root).iterdir():
        try:
            if path.is_dir() and path.name == _operation_id(path.name) and time.time() - path.stat().st_mtime > 3600:
                cleanup(root, path.name)
        except (AppError, OSError):
            pass


def download_pdf(url, destination, cancel=None):
    deadline = time.monotonic() + 180
    prefix = bytearray()
    with response_for(url, accept='application/pdf, application/octet-stream;q=0.8', cancel=cancel, deadline=deadline) as (response, final):
        mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if mime in ('text/html', 'application/xhtml+xml', 'application/json', 'text/plain'):
            raise AppError('来源返回了网页而非 PDF；可能需要验证，请打开来源网页。')
        with destination.open('xb') as stream:
            for block in _chunks(response, MAX_PDF, cancel, deadline):
                if len(prefix) < 1024:
                    prefix.extend(block[:1024-len(prefix)])
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
    if b'%PDF-' not in prefix:
        raise AppError('下载内容不是可识别的 PDF，请打开来源网页。')
    checked_pdf(destination)
    _check(cancel, deadline)
    return final


@dataclass
class DownloadedPDF:
    path: Path
    source: str
    url: str


@contextmanager
def open_document(root, request, settings, cancel=None):
    work = request.get('work')
    if not isinstance(work, dict) or not _clean(work.get('title'), 800):
        raise AppError('文献条目无效，请重新搜索。')
    source = request.get('source', settings['source'])
    if source not in ('sciHub', 'openAccess'):
        raise AppError('请选择 Sci-Hub 或公开 PDF 来源。')
    doi = normalized_doi(work.get('doi'))
    open_access = _optional_url(work.get('pdf_url'))
    landing = _optional_url(work.get('landing_url'))
    fallback = landing
    job = _operation_id(request.get('operation_id', str(uuid.uuid4())))
    directory = None
    try:
        if source == 'sciHub':
            fallback = article_url(doi, request.get('scihub_base_url', settings['scihub_base_url']))
        if request.get('resolved_url') is not None:
            target = public_url(request['resolved_url'])
        elif source == 'sciHub':
            target = resolve_scihub(doi, request.get('scihub_base_url', settings['scihub_base_url']), cancel)
        elif open_access:
            target = open_access
        else:
            raise AppError('这条文献没有公开 PDF 链接，请选择其他来源或打开文献网页。')
        _prune(root)
        candidate = _cache(root) / job
        try:
            candidate.mkdir(mode=0o777, exist_ok=False)
        except FileExistsError:
            raise AppError('这项下载任务已存在，请等待完成后重试。') from None
        directory = candidate
        path = directory / filename(work['title'])
        final = download_pdf(target, path, cancel)
    except Cancelled:
        raise
    except AppError as error:
        yield dict(document=None, requires_browser=bool(fallback), page_url=fallback, message=str(error),
                   can_try_open_access=source != 'openAccess' and open_access is not None)
    else:
        # Import exceptions belong to the caller, not a network fallback. This
        # yield stays outside the try/except to avoid yielding twice on failure.
        yield DownloadedPDF(path, source, final)
    finally:
        if directory is not None:
            try:
                cleanup(root, job)
            except (OSError, AppError):
                # A cleanup failure must not turn an already committed import
                # into an apparent failure. Old owned jobs are pruned later.
                pass
